#!/usr/bin/env bash
set -euo pipefail
if (($# > 1)); then
  printf 'Usage: %s [--check|--help]\n' "$0" >&2
  exit 1
fi
case "${1:-}" in
  ''|--check|--help|-h) ;;
  *) printf 'Usage: %s [--check|--help]\n' "$0" >&2; exit 1 ;;
esac
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$SCRIPT_DIR/deploy.sh" --project ml-cherry \
  --site ml-cherry-wherewego --database instagram-organizer "$@"
