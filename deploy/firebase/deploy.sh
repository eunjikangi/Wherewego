#!/usr/bin/env bash
# Deploy only after explicit execution with an existing, billed Firebase project.
set -euo pipefail
umask 077

usage() {
  cat <<'USAGE'
Usage: deploy/firebase/deploy.sh --project PROJECT_ID [--region REGION]
       [--site SITE_ID] [--database DATABASE_ID] [--check]

  --project PROJECT_ID  Existing Firebase / Google Cloud project ID (required)
  --region REGION      Deployment region (default: asia-northeast3)
  --site SITE_ID       Hosting site (default: PROJECT_ID)
  --database DATABASE_ID  Firestore database (default: (default))
  --check              Read-only checks; do not create or deploy resources
  --help               Show this help

GCLOUD_BIN and FIREBASE_BIN can point to authenticated CLI wrappers.
No global gcloud project setting is changed. This script does not create projects
or enable billing. Actual deployment provisions billable cloud resources.
USAGE
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
PROJECT_ID=''
REGION='asia-northeast3'
SITE_ID=''
DATABASE_ID='(default)'
CHECK_ONLY=0
while (($#)); do
  case "$1" in
    --project) (($# >= 2)) || die '--project requires a value'; PROJECT_ID=$2; shift 2 ;;
    --region) (($# >= 2)) || die '--region requires a value'; REGION=$2; shift 2 ;;
    --site) (($# >= 2)) && [[ -n "$2" ]] || die '--site requires a value'; SITE_ID=$2; shift 2 ;;
    --database) (($# >= 2)) || die '--database requires a value'; DATABASE_ID=$2; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown argument: $1 (use --help)" ;;
  esac
done
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die 'A valid --project ID is required (6–30 lowercase letters, digits or hyphens).'
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || die 'Invalid region.'
SITE_ID=${SITE_ID:-$PROJECT_ID}
[[ "$SITE_ID" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || die 'Invalid Hosting site ID; use 1–63 lowercase letters, digits or hyphens, without leading/trailing hyphens.'
if [[ "$DATABASE_ID" != '(default)' ]]; then
  [[ "$DATABASE_ID" =~ ^[a-z][a-z0-9-]{2,61}[a-z0-9]$ ]] || die 'Invalid Firestore database ID; use 4–63 lowercase letters, digits or hyphens, starting with a letter and ending with a letter or digit.'
  [[ ! "$DATABASE_ID" =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || die 'Firestore database IDs must not be UUID-like.'
fi

GCLOUD_BIN=${GCLOUD_BIN:-gcloud}
FIREBASE_BIN=${FIREBASE_BIN:-firebase}
command -v "$GCLOUD_BIN" >/dev/null || die 'Install gcloud or provide GCLOUD_BIN.'
command -v "$FIREBASE_BIN" >/dev/null || die 'Install the Firebase CLI or provide FIREBASE_BIN.'
command -v python3 >/dev/null || die 'python3 is required.'
command -v curl >/dev/null || die 'curl is required.'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
[[ -f "$REPO_DIR/Dockerfile" ]] || die 'Dockerfile is missing from the repository root.'
WORK_DIR=$(mktemp -d)
trap 'rm -rf -- "$WORK_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
gc() { "$GCLOUD_BIN" "$@" --project="$PROJECT_ID" --quiet; }

printf 'Checking existing project %s and billing before provisioning…\n' "$PROJECT_ID"
# This works with gcloud --access-token-file wrappers; auth list need not be populated.
gc projects describe "$PROJECT_ID" --format=json > "$WORK_DIR/project.json"
gc billing projects describe "$PROJECT_ID" --format=json > "$WORK_DIR/billing.json"
python3 - "$WORK_DIR/billing.json" <<'PY'
import json, sys
if not json.load(open(sys.argv[1], encoding='utf-8')).get('billingEnabled'):
    sys.exit('Billing is not enabled. Link a billing account and select Firebase Blaze before deploying.')
PY
"$FIREBASE_BIN" projects:list --json > "$WORK_DIR/firebase-projects.json"
python3 - "$WORK_DIR/firebase-projects.json" "$PROJECT_ID" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
projects = data.get('result', [])
if isinstance(projects, dict):
    projects = projects.get('projects', [])
if not any(p.get('projectId') == sys.argv[2] for p in projects):
    sys.exit('This project is not an accessible Firebase project. Register it in Firebase Console and sign in to the Firebase CLI first.')
PY

SERVICE='instagram-organizer'
RUNTIME_SA_NAME='wherewego-runtime'
BUILD_SA_NAME='wherewego-build'
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA="${BUILD_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="${PROJECT_ID}-${SERVICE}-state"
SECRET="${SERVICE}-admin-password"
printf 'Plan: Cloud Run %s (%s), Firestore %s, private bucket %s, Firebase Hosting %s redirect.\n' "$SERVICE" "$REGION" "$DATABASE_ID" "$BUCKET" "$SITE_ID"
if ((CHECK_ONLY)); then
  printf 'Read-only checks passed. No resources were created or deployed.\n'
  exit 0
fi

gc services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com firebase.googleapis.com firebaserules.googleapis.com firebasehosting.googleapis.com

ensure_service_account() {
  local name=$1 email=$2
  if ! gc iam service-accounts describe "$email" --format='value(email)' > /dev/null 2> "$WORK_DIR/sa-error"; then
    gc iam service-accounts create "$name" --display-name="Wherewego $name"
  fi
}
ensure_service_account "$RUNTIME_SA_NAME" "$RUNTIME_SA"
ensure_service_account "$BUILD_SA_NAME" "$BUILD_SA"
# Newly created service accounts can take a short time to become visible to
# project IAM. Retry that exact propagation error; preserve other failures.
bind_project_role() {
  local email=$1 role=$2 index=0 status
  local error_file="$WORK_DIR/project-iam-error"
  local -a waits=(2 4 8 16 30)
  while true; do
    if gc projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$email" \
      --role="$role" --condition=None > /dev/null 2> "$error_file"; then
      return 0
    else
      status=$?
    fi
    if ((index >= ${#waits[@]})) || ! python3 - "$error_file" "$email" <<'PY'
import pathlib, re, sys
message = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8', errors='replace')
pattern = r'INVALID_ARGUMENT:\s+Service account\s+' + re.escape(sys.argv[2]) + r'\s+does not exist(?:[.\s]|$)'
sys.exit(0 if re.search(pattern, message) else 1)
PY
    then
      cat "$error_file" >&2
      return "$status"
    fi
    printf 'Waiting %s seconds for new service-account visibility…\n' "${waits[index]}" >&2
    sleep "${waits[index]}"
    index=$((index + 1))
  done
}
bind_project_role "$RUNTIME_SA" roles/datastore.user
# Dedicated source-build identity avoids relying on an Editor-enabled default SA.
bind_project_role "$BUILD_SA" roles/run.builder

gc firestore databases list --format=json > "$WORK_DIR/databases.json"
DB_TYPE=$(python3 - "$WORK_DIR/databases.json" "$DATABASE_ID" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
if isinstance(data, dict):
    data = data.get('databases', [])
for database in data:
    if database.get('name', '').endswith('/databases/' + sys.argv[2]):
        print(database.get('type', 'UNKNOWN'))
        break
PY
)
if [[ -z "$DB_TYPE" ]]; then
  gc firestore databases create --database="$DATABASE_ID" --location="$REGION" --type=firestore-native
elif [[ "$DB_TYPE" != 'FIRESTORE_NATIVE' ]]; then
  die "The requested database $DATABASE_ID is not Firestore Native. It was left unchanged; use a compatible database."
fi
# Existing Firestore databases, their rules and other applications' collections are not replaced.

if ! gc storage buckets describe "gs://$BUCKET" --raw --format=json > "$WORK_DIR/bucket.json" 2> "$WORK_DIR/bucket-error"; then
  gc storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  gc storage buckets describe "gs://$BUCKET" --raw --format=json > "$WORK_DIR/bucket.json"
fi
python3 - "$WORK_DIR/bucket.json" "$WORK_DIR/project.json" <<'PY'
import json, sys
bucket = json.load(open(sys.argv[1], encoding='utf-8'))
project = json.load(open(sys.argv[2], encoding='utf-8'))
owner = bucket.get('project_number', bucket.get('projectNumber'))
if owner is None or str(owner) != str(project.get('projectNumber')):
    sys.exit('The state bucket does not belong to this project. No permissions were changed.')
PY
gc storage buckets update "gs://$BUCKET" --uniform-bucket-level-access --public-access-prevention
gc storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectAdmin > /dev/null

if ! gc secrets describe "$SECRET" --format='value(name)' > /dev/null 2> "$WORK_DIR/secret-error"; then
  gc secrets create "$SECRET" --replication-policy=automatic
fi
VERSION=$(gc secrets versions list "$SECRET" --filter='state=ENABLED' --sort-by='~createTime' --limit=1 --format='value(name)')
if [[ -z "$VERSION" ]]; then
  # Save a newly generated password outside the checkout; never put it in arguments or stdout.
  PASSWORD_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/wherewego/${PROJECT_ID}"
  python3 - "$PASSWORD_DIR" "$REPO_DIR" <<'PY'
import os, pathlib, secrets, stat, sys
directory, checkout = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve()
if directory == checkout or checkout in directory.parents:
    sys.exit('The password directory must be outside the repository.')
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(directory, 0o700)
(directory / '.gitignore').write_text('*\n', encoding='utf-8')
path = directory / 'admin-password'
if path.exists() or path.is_symlink():
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        sys.exit('An existing password file has unsafe ownership or permissions; it was left unchanged.')
    value = path.read_text(encoding='utf-8')
    if len(value) < 32 or not value.isascii() or any(c.isspace() or not c.isprintable() for c in value):
        sys.exit('The existing password file is invalid; it was left unchanged.')
else:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(secrets.token_urlsafe(32))
PY
  gc secrets versions add "$SECRET" --data-file="$PASSWORD_DIR/admin-password" > /dev/null
  printf 'A new admin password was saved to a private file: %s/admin-password\n' "$PASSWORD_DIR"
  VERSION=$(gc secrets versions list "$SECRET" --filter='state=ENABLED' --sort-by='~createTime' --limit=1 --format='value(name)')
fi
VERSION=${VERSION##*/}
[[ "$VERSION" =~ ^[0-9]+$ ]] || die 'No enabled Secret Manager password version could be found.'
gc secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$RUNTIME_SA" \
  --role=roles/secretmanager.secretAccessor --condition=None > /dev/null

printf 'Building Dockerfile and deploying Cloud Run…\n'
gc run deploy "$SERVICE" --source="$REPO_DIR" --region="$REGION" \
  --service-account="$RUNTIME_SA" \
  --build-service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
  --cpu=1 --memory=4Gi --min-instances=0 --max-instances=1 --concurrency=40 \
  --timeout=3600 --no-cpu-throttling --session-affinity --port=8000 --allow-unauthenticated \
  --set-env-vars="STORE_BACKEND=firestore,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,FIRESTORE_DATABASE=$DATABASE_ID,ORGANIZER_STATE_BUCKET=$BUCKET,DATA_DIR=/data,COOKIE_SECURE=1" \
  --set-secrets="ADMIN_PASSWORD=$SECRET:$VERSION"
RUN_URL=$(gc run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')
[[ "$RUN_URL" =~ ^https://[a-zA-Z0-9.-]+\.run\.app$ ]] || die 'Cloud Run did not return a valid HTTPS service URL.'
# Cloud Run terminates HTTPS before the container. Explicit origin also protects
# login POST and noVNC WebSocket checks when uvicorn observes an HTTP upstream.
gc run services update "$SERVICE" --region="$REGION" --update-env-vars="PUBLIC_ORIGIN=$RUN_URL,COOKIE_SECURE=1"
curl --fail --silent --show-error --max-time 60 --retry 3 --retry-delay 3 --retry-connrefused \
  --output "$WORK_DIR/health.json" "$RUN_URL/health"
python3 - "$WORK_DIR/health.json" <<'PY'
import json, sys
if json.load(open(sys.argv[1], encoding='utf-8')).get('ok') is not True:
    sys.exit('Cloud Run health check did not report a ready application.')
PY

"$FIREBASE_BIN" hosting:sites:list --project "$PROJECT_ID" --json > "$WORK_DIR/hosting-sites.json"
SITE_EXISTS=$(python3 - "$WORK_DIR/hosting-sites.json" "$SITE_ID" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8')).get('result', [])
if isinstance(data, dict):
    data = data.get('sites', [])
print('yes' if any(site.get('name', '').split('/')[-1] == sys.argv[2] or site.get('siteId') == sys.argv[2] for site in data) else 'no')
PY
)
if [[ "$SITE_EXISTS" != yes ]]; then
  "$FIREBASE_BIN" hosting:sites:create "$SITE_ID" --project "$PROJECT_ID" --non-interactive
fi

# Generate an actual destination only after Cloud Run reports its real URL.
mkdir -p "$WORK_DIR/hosting/public"
python3 - "$WORK_DIR" "$RUN_URL" "$SITE_ID" <<'PY'
import html, json, pathlib, sys
directory, destination = pathlib.Path(sys.argv[1]), sys.argv[2]
config = {'hosting': {'site': sys.argv[3], 'public': 'hosting/public', 'ignore': ['firebase.json', '**/.*', '**/node_modules/**'],
                      'redirects': [{'source': '/**', 'destination': destination, 'type': 302}]}}
(directory / 'firebase.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
url = html.escape(destination, quote=True)
(directory / 'hosting/public/index.html').write_text(
    '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>모아분류로 이동</title><meta http-equiv="refresh" content="0;url=' + url + '">'
    '<p><a href="' + url + '">모아분류 열기</a></p></html>', encoding='utf-8')
PY
(cd -- "$WORK_DIR" && "$FIREBASE_BIN" deploy --only hosting --project "$PROJECT_ID" --config "$WORK_DIR/firebase.json" --non-interactive)
curl --silent --show-error --max-time 60 --retry 3 --retry-delay 3 \
  --head --output "$WORK_DIR/hosting-headers.txt" "https://$SITE_ID.web.app/"
python3 - "$WORK_DIR/hosting-headers.txt" "$RUN_URL" <<'PY'
import pathlib, re, sys
headers = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
codes = re.findall(r'^HTTP/\S+\s+(\d+)', headers, re.MULTILINE)
destinations = re.findall(r'^location:\s*(.+)$', headers, re.MULTILINE | re.IGNORECASE)
if not codes or codes[-1] != '302' or not destinations or destinations[-1].strip().rstrip('/') != sys.argv[2].rstrip('/'):
    sys.exit('Firebase Hosting redirect verification failed. Review the Hosting deployment before sharing its URL.')
PY
printf '\nDeployment complete. App: %s\nFirebase entry: https://%s.web.app\n' "$RUN_URL" "$SITE_ID"
printf 'The admin password is stored in Secret Manager: %s (version %s).\n' "$SECRET" "$VERSION"
printf 'Sign in to the app, then log in to Instagram directly in its browser.\n'
