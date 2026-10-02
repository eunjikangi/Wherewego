#!/usr/bin/env python3
"""Use Cloud Shell's gcloud account for the deployment's Firebase operations."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

FIREBASE_API = "https://firebase.googleapis.com/v1beta1/"
HOSTING_API = "https://firebasehosting.googleapis.com/v1beta1/"
PROJECT_PATTERN = r"[a-z][a-z0-9-]{4,28}[a-z0-9]"
SITE_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


def validate_identifier(value, pattern, label):
    if not re.fullmatch(pattern, value or ""):
        raise ValueError("Invalid " + label)
    return value


def gcloud_token(project):
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token", "--project=" + project],
        capture_output=True, text=True, check=False,
    )
    token = result.stdout.strip()
    if result.returncode or not token or any(c.isspace() for c in token):
        raise RuntimeError("Cloud Shell Google authorization is unavailable. Authorize Cloud Shell for this project and retry.")
    return token


def redirect_config(config, expected_site):
    hosting = config.get("hosting")
    if not isinstance(hosting, dict) or hosting.get("site") != expected_site:
        raise ValueError("One explicit Hosting site is required.")
    if set(hosting) - {"site", "public", "ignore", "redirects"}:
        raise ValueError("Unsupported Hosting configuration.")
    redirects = hosting.get("redirects")
    if not isinstance(redirects, list) or len(redirects) != 1:
        raise ValueError("This adapter publishes one application redirect only.")
    redirect = redirects[0]
    if not isinstance(redirect, dict) or set(redirect) != {"source", "destination", "type"}:
        raise ValueError("Unsupported redirect configuration.")
    if redirect.get("source") != "/**" or redirect.get("type") != 302:
        raise ValueError("Only the application-wide 302 redirect is supported.")
    destination = redirect.get("destination", "")
    parsed = urllib.parse.urlsplit(destination)
    if not (
        parsed.scheme == "https" and parsed.hostname
        and parsed.hostname.endswith(".run.app")
        and parsed.username is None and parsed.password is None
        and parsed.port in (None, 443) and parsed.path in ("", "/")
        and not parsed.query and not parsed.fragment
    ):
        raise ValueError("An HTTPS Cloud Run application origin is required.")
    return {"redirects": [{
        "glob": "/**", "location": destination, "statusCode": 302,
    }]}


class Firebase:
    def __init__(self, project, token):
        self.project = validate_identifier(project, PROJECT_PATTERN, "project ID")
        self.token = token

    def request(self, api, method, path, body=None, query=None):
        if api not in (FIREBASE_API, HOSTING_API):
            raise ValueError("Unsupported API.")
        url = api + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=encoded, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read(16384)).get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                message = ""
            message = str(message).replace(self.token, "[redacted]")
            raise RuntimeError(f"Firebase API HTTP {error.code}: {message or error.reason}") from None

    def project_metadata(self):
        return self.request(FIREBASE_API, "GET", "projects/" + self.project)

    def sites(self):
        result = []
        page_token = None
        seen = set()
        while True:
            page = self.request(
                HOSTING_API, "GET", "projects/" + self.project + "/sites",
                query={"pageToken": page_token} if page_token else None,
            )
            result.extend(page.get("sites", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                return result
            if page_token in seen:
                raise RuntimeError("Hosting pagination returned a repeated page token.")
            seen.add(page_token)

    def create_site(self, site):
        validate_identifier(site, SITE_PATTERN, "site ID")
        return self.request(
            HOSTING_API, "POST", "projects/" + self.project + "/sites",
            body={"appId": ""}, query={"siteId": site},
        )

    def publish_redirect(self, config):
        hosting = config.get("hosting")
        if not isinstance(hosting, dict):
            raise ValueError("One explicit Hosting site is required.")
        site = validate_identifier(hosting.get("site"), SITE_PATTERN, "site ID")
        converted = redirect_config(config, site)
        if not any(entry.get("name", "").split("/")[-1] == site or entry.get("siteId") == site
                   for entry in self.sites()):
            raise RuntimeError("The selected Hosting site does not belong to this project.")
        resource = "projects/-/sites/" + site
        version = self.request(
            HOSTING_API, "POST", resource + "/versions",
            body={"status": "CREATED", "config": converted},
        )
        version_name = version.get("name", "")
        pattern = r"(?:projects/(?:-|[a-z0-9-]+)/)?sites/" + re.escape(site) + r"/versions/[A-Za-z0-9_-]+"
        if not re.fullmatch(pattern, version_name):
            raise RuntimeError("Hosting returned an invalid version resource.")
        self.request(
            HOSTING_API, "PATCH", version_name,
            body={"status": "FINALIZED"}, query={"updateMask": "status"},
        )
        return self.request(
            HOSTING_API, "POST", resource + "/channels/live/releases",
            body={"message": "Wherewego Cloud Shell application redirect"},
            query={"versionName": version_name},
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("projects:list", "hosting:sites:list", "hosting:sites:create", "deploy"))
    parser.add_argument("site", nargs="?")
    parser.add_argument("--project")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--config")
    options = parser.parse_args(argv)
    project = options.project or os.environ.get("WHEREWEGO_PROJECT_ID")
    validate_identifier(project, PROJECT_PATTERN, "project ID")
    if options.command != "projects:list" and not options.project:
        parser.error("--project is required for Hosting operations.")
    firebase = Firebase(project, gcloud_token(project))
    if options.command == "projects:list":
        result = [firebase.project_metadata()]
    elif options.command == "hosting:sites:list":
        result = {"sites": firebase.sites()}
    elif options.command == "hosting:sites:create":
        result = firebase.create_site(options.site)
    else:
        if options.only != "hosting" or not options.config:
            parser.error("Only --only hosting with an explicit --config is supported.")
        result = firebase.publish_redirect(json.loads(Path(options.config).read_text(encoding="utf-8")))
    if options.json:
        print(json.dumps({"status": "success", "result": result}, ensure_ascii=False))
    else:
        print("Firebase operation completed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, urllib.error.URLError) as error:
        print("Error: " + str(error), file=sys.stderr)
        sys.exit(1)
