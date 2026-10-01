#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ ! -f .env ]; then
    umask 077
    python3 - <<'PY'
import pathlib, secrets
pathlib.Path('.env').write_text('ADMIN_PASSWORD=' + secrets.token_urlsafe(24) + '\nCOOKIE_SECURE=0\nOPENAI_MODEL=gpt-4.1-mini\n')
PY
    echo '관리자 암호를 .env에 만들었습니다. 이 파일의 ADMIN_PASSWORD 값으로 앱에 접속하세요.'
fi
docker compose up -d --build
echo '서버 준비가 끝나면 http://127.0.0.1:8000 에 접속하세요.'
echo '원격 서버라면 SSH 터널을 이용하거나 HTTPS 역방향 프록시를 연결하세요. README.md에 안내가 있습니다.'
