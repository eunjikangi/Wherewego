#!/usr/bin/env bash
# Deploy ml-cherry using its existing application image and Cloud Shell account.
set -euo pipefail
umask 077

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

CHECK_ONLY=0
BUILD_NEW=0
case $# in
  0) ;;
  1)
    case "$1" in
      --check) CHECK_ONLY=1 ;;
      --build) BUILD_NEW=1 ;;
      *) die 'Usage: bash deploy/firebase/cloudshell.sh [--check|--build]' ;;
    esac
    ;;
  *) die 'Usage: bash deploy/firebase/cloudshell.sh [--check|--build]' ;;
esac

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v gcloud >/dev/null || die 'Run this script in Google Cloud Shell, where gcloud is installed.'
[[ -x "$SCRIPT_DIR/firebase_gcloud.py" ]] || die 'The Firebase gcloud adapter is missing or not executable.'
gcloud auth print-access-token >/dev/null || die 'Authorize Google Cloud Shell with your Google account, then retry.'

export GCLOUD_BIN=gcloud
export FIREBASE_BIN="$SCRIPT_DIR/firebase_gcloud.py"
export WHEREWEGO_PROJECT_ID=ml-cherry

ARGS=(
  --project ml-cherry
  --region asia-northeast3
  --site ml-cherry-wherewego
  --database instagram-organizer
)
if ((!BUILD_NEW)); then
  ARGS+=(--image asia-northeast3-docker.pkg.dev/ml-cherry/cloud-run-source-deploy/instagram-organizer:latest)
fi
if ((CHECK_ONLY)); then
  ARGS+=(--check)
fi
exec bash "$SCRIPT_DIR/deploy.sh" "${ARGS[@]}"
