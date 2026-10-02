import asyncio
import base64
import contextlib
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit, urlunsplit

import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError, model_validator

from .browser import BrowserManager
from .ai import AISettings
from .classifier import CATEGORIES, classify_records
from .store import Store, normalize_url

os.umask(0o077)
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
COOKIE = "organizer_session"
SESSION_SECONDS = 12 * 60 * 60
LOGIN_PAGE = """<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>모아분류 로그인</title><script src='/login-import.js' defer></script>
<link rel='stylesheet' href='/style.css'><body><main style='max-width:440px;margin:12vh auto;padding:24px'><h1>모아분류</h1><p>서버 관리자 암호로 접속하세요.</p><form method='post' action='/login'><label for='password'>관리자 암호</label><input id='password' name='password' type='password' autocomplete='current-password' required style='display:block;width:100%;margin:12px 0;padding:14px;border:1px solid #ccd4e0;border-radius:10px'><button type='submit' style='padding:12px 20px;background:#245eea;color:white;border:0;border-radius:10px'>로그인</button></form><p style='font-size:14px;color:#52617b'>인스타그램은 본인 PC의 Chrome·Edge에서 로그인한 뒤 확장 프로그램으로 가져옵니다.</p>ERROR</main></body></html>"""


def signed_session(password, expires=None):
    payload = str(expires or int(time.time()) + SESSION_SECONDS) + ":" + secrets.token_hex(16)
    signature = hmac.new(("organizer-session:" + password).encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload.encode()).decode() + "." + signature


def valid_session(value, password):
    if not value or not password:
        return False
    try:
        encoded, signature = value.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded).decode()
        expected = hmac.new(("organizer-session:" + password).encode(), payload.encode(), hashlib.sha256).hexdigest()
        expires = int(payload.split(":", 1)[0])
        return hmac.compare_digest(signature, expected) and int(time.time()) < expires <= int(time.time()) + SESSION_SECONDS + 60
    except (ValueError, UnicodeError, TypeError):
        return False


def origin_matches(origin, host, scheme):
    expected = os.getenv("PUBLIC_ORIGIN", "").rstrip("/") or f"{scheme}://{host}"
    return bool(origin) and hmac.compare_digest(origin.rstrip("/"), expected)


class CollectInput(BaseModel):
    mode: str = "current"
    use_ai: bool = False


class EditInput(BaseModel):
    category: str = Field(max_length=80)
    note: str = Field(default="", max_length=1000)


class AIInput(BaseModel):
    api_key: str = Field(min_length=1, max_length=1024)
    model: str | None = Field(default=None, max_length=80)
    provider: Literal["openai", "gemini"] = "openai"


class ClassifyInput(BaseModel):
    only_pending: bool = False
    enrich_remote: bool = False


IMPORT_BODY_LIMIT = 2 * 1024 * 1024
EXTENSION_FILES = (
    "manifest.json", "popup.html", "popup.js", "popup.css", "content.js", "README.md",
)


def client_url(raw, source, context=False):
    """Validate browser-supplied links without fetching their contents."""
    if not raw or any(ord(char) <= 32 for char in raw) or "\\" in raw:
        raise ValueError("링크 형식을 확인하세요.")
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
        if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        instagram = host == "instagram.com" or host.endswith(".instagram.com")
        if context:
            if not instagram or port not in (None, 80, 443):
                raise ValueError
            # DM thread and saved-page paths are context only; query data is discarded.
            return urlunsplit(("https", "www.instagram.com", parsed.path or "/", "", ""))
        if instagram:
            match = re.fullmatch(r"/(p|reel|tv)/([A-Za-z0-9_-]+)/?", parsed.path)
            if not match or port not in (None, 80, 443):
                raise ValueError
            return f"https://www.instagram.com/{match[1]}/{match[2]}/"
        if source != "dm":
            raise ValueError
        normalized = normalize_url(raw)
        if not normalized:
            raise ValueError
        return normalized
    except (ValueError, TypeError):
        raise ValueError("Instagram 게시물 링크 또는 DM으로 공유한 http·https 링크를 확인하세요.") from None


class ImportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2048)
    title: StrictStr = Field(default="", max_length=250)
    text: StrictStr = Field(default="", max_length=2500)
    source: Literal["saved", "dm", "post"]
    source_url: StrictStr | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_links(self):
        self.url = client_url(self.url, self.source)
        if self.source_url is not None:
            self.source_url = client_url(self.source_url, self.source, context=True)
        return self


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[ImportRecord] = Field(min_length=1, max_length=500)
    use_ai: StrictBool = False


async def checkpoint_browser():
    if app.state.cloud is None or not app.state.browser.ready:
        return
    async with app.state.checkpoint_lock:
        try:
            snapshot = await app.state.browser.snapshot()
            if snapshot is not None:
                await asyncio.to_thread(app.state.cloud.save_browser_state, snapshot)
            app.state.session_warning = ""
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Snapshot contents can contain authentication cookies: never log them.
            logging.error("Cloud browser snapshot failed (%s)", type(error).__name__)
            app.state.session_warning = "로그인 상태를 저장하지 못했습니다. 서버가 재시작되면 다시 로그인해야 할 수 있습니다."


async def periodic_checkpoints():
    while True:
        await asyncio.sleep(30)
        await checkpoint_browser()


@asynccontextmanager
async def lifespan(app):
    password = os.getenv("ADMIN_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters. Run ./setup.sh first.")
    directory = Path(os.getenv("DATA_DIR", "/data"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    app.state.password = password
    backend = os.getenv("STORE_BACKEND", "sqlite").lower()
    bucket = os.getenv("ORGANIZER_STATE_BUCKET", "").strip()
    if backend not in ("sqlite", "firestore"):
        raise RuntimeError("STORE_BACKEND must be sqlite or firestore.")
    if backend == "firestore" and not bucket:
        raise RuntimeError("ORGANIZER_STATE_BUCKET is required with Firestore to persist login and AI settings.")
    app.state.cloud = None
    restored_state = None
    if bucket:
        from .cloud_state import CloudState
        app.state.cloud = CloudState(bucket, directory)
        await asyncio.to_thread(app.state.cloud.restore_ai_settings)
        restored_state = await asyncio.to_thread(app.state.cloud.load_browser_state)
    if backend == "firestore":
        from .firestore_store import FirestoreStore
        app.state.store = await asyncio.to_thread(
            FirestoreStore, project_id=os.getenv("GOOGLE_CLOUD_PROJECT") or None,
            database=os.getenv("FIRESTORE_DATABASE", "(default)"))
    else:
        app.state.store = Store(directory)
    app.state.ai = AISettings(directory)
    app.state.browser = BrowserManager(restored_state=restored_state)
    app.state.checkpoint_lock = asyncio.Lock()
    app.state.session_warning = ""
    app.state.busy = False
    app.state.job = {"status": "idle", "message": "PC의 Instagram에서 확장 프로그램으로 가져온 뒤 분류하세요.", "added": 0, "updated": 0}
    app.state.task = None
    app.state.login_failures = []
    checkpoint_task = asyncio.create_task(periodic_checkpoints()) if app.state.cloud else None
    try:
        yield
    finally:
        for task in (app.state.task, checkpoint_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if app.state.cloud:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(checkpoint_browser(), timeout=8)
        await app.state.browser.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Pydantic's default errors echo input, which could expose submitted API keys.
    return JSONResponse({"detail": [
        {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
        for item in error.errors()
    ]}, status_code=422)


@app.middleware("http")
async def session_protection(request, call_next):
    path = request.url.path
    public = path in ("/login", "/health", "/style.css", "/login-import.js")
    password = getattr(app.state, "password", "")
    if not public and not valid_session(request.cookies.get(COOKIE), password):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not origin_matches(request.headers.get("origin"), request.headers.get("host", ""), request.url.scheme):
            return JSONResponse({"detail": "요청 출처를 확인할 수 없습니다."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Chrome gives form POSTs an opaque Origin under no-referrer, which
    # prevents a legitimate administrator login from passing the origin check.
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss:; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
    return response


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_PAGE.replace("ERROR", "")


@app.post("/login")
async def login(request: Request):
    now = time.monotonic()
    app.state.login_failures = [value for value in app.state.login_failures if now - value < 60]
    if len(app.state.login_failures) >= 10:
        return HTMLResponse(LOGIN_PAGE.replace("ERROR", "<p>잠시 후 다시 시도하세요.</p>"), status_code=429)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(413, "요청이 너무 큽니다.")
    password = parse_qs(body.decode("utf-8", errors="replace")).get("password", [""])[0]
    if not hmac.compare_digest(password.encode(), app.state.password.encode()):
        app.state.login_failures.append(now)
        return HTMLResponse(LOGIN_PAGE.replace("ERROR", "<p>관리자 암호를 확인하세요.</p>"), status_code=401)
    response = RedirectResponse("/", status_code=303)
    secure = os.getenv("COOKIE_SECURE", "0") == "1" or request.url.scheme == "https"
    response.set_cookie(COOKIE, signed_session(app.state.password), max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict")
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, samesite="strict")
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/style.css")
@app.get("/static/style.css")
async def styles():
    return FileResponse(STATIC / "style.css")


@app.get("/app.js")
@app.get("/static/app.js")
async def script():
    return FileResponse(STATIC / "app.js", media_type="application/javascript")


@app.get("/login-import.js")
async def import_login_script():
    return FileResponse(STATIC / "login-import.js", media_type="application/javascript")


@app.get("/extension.zip")
async def extension_archive():
    directory = ROOT / "extension"
    if directory.is_symlink():
        raise HTTPException(503, "확장 프로그램 파일을 확인하지 못했습니다.")
    root = directory.resolve()
    entries = []
    for name in EXTENSION_FILES:
        target = root / name
        if not target.is_file():
            raise HTTPException(503, "확장 프로그램 파일을 준비하지 못했습니다.")
        if target.is_symlink() or not target.resolve().is_relative_to(root) or target.stat().st_size > 512 * 1024:
            raise HTTPException(503, "확장 프로그램 파일을 확인하지 못했습니다.")
        entries.append((name, target.read_bytes()))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, contents in entries:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, contents)
    return Response(archive.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="wherewego-extension.zip"'})


async def status():
    return {"browser_ready": app.state.browser.ready, "busy": app.state.busy, "job": app.state.job,
            "total": await asyncio.to_thread(app.state.store.total), "categories": CATEGORIES,
            "ai_available": bool(app.state.ai.key()), "session_warning": app.state.session_warning}


@app.get("/api/ai/config")
async def ai_config():
    return app.state.ai.public()


@app.put("/api/ai/config")
async def configure_ai(values: AIInput):
    if app.state.busy:
        raise HTTPException(409, "진행 중인 작업이 끝난 뒤 AI 설정을 변경하세요.")
    app.state.busy = True
    try:
        previous = app.state.ai.path.read_bytes() if app.state.ai.path.exists() else None
        result = await app.state.ai.configure(values.api_key, values.model, provider=values.provider)
        if app.state.cloud:
            try:
                await asyncio.to_thread(app.state.cloud.save_ai_settings)
            except Exception as error:
                logging.error("Cloud AI settings save failed (%s)", type(error).__name__)
                if previous is None:
                    app.state.ai.path.unlink(missing_ok=True)
                else:
                    app.state.ai.path.write_bytes(previous)
                    app.state.ai.path.chmod(0o600)
                app.state.ai = AISettings(app.state.ai.directory)
                raise HTTPException(503, "AI 설정을 저장하지 못했습니다. 잠시 후 다시 연결해주세요.") from None
        return result
    except ValueError as error:
        raise HTTPException(422, str(error))
    finally:
        app.state.busy = False


@app.get("/api/status")
async def get_status():
    return await status()


@app.post("/api/browser/start")
async def start_browser():
    if app.state.busy:
        raise HTTPException(409, "수집을 마친 뒤 브라우저를 여세요.")
    app.state.busy = True
    try:
        await app.state.browser.start()
    except Exception:
        logging.exception("Browser startup failed")
        raise HTTPException(503, "브라우저를 열지 못했습니다. 서버 실행 상태를 확인하세요.")
    finally:
        app.state.busy = False
    return await status()


async def run_collection(options):
    app.state.job = {"status": "running", "message": "열려 있는 화면에서 링크를 확인하고 있습니다.", "added": 0, "updated": 0}
    try:
        async def progress(message):
            app.state.job["message"] = message
        records = await app.state.browser.collect(mode=options.mode, progress=progress)
        if options.use_ai and records:
            records = await app.state.browser.enrich(records, progress=progress)
        app.state.job["message"] = f"{len(records)}개 링크를 분류하고 있습니다."
        classified, warning = await classify_records(records, options.use_ai, api_key=app.state.ai.key(), model=app.state.ai.model(), provider=app.state.ai.provider())
        added, updated = await asyncio.to_thread(app.state.store.upsert, classified)
        message = f"새 항목 {added}개, 보완한 항목 {updated}개."
        if not records:
            message = "가져올 링크가 없습니다. 저장함 또는 DM 대화를 열고 게시물이 보이게 해주세요."
        if warning:
            message += " " + warning
        app.state.job = {"status": "done", "message": message, "added": added, "updated": updated}
    except asyncio.CancelledError:
        raise
    except ValueError as error:
        app.state.job = {"status": "error", "message": str(error), "added": 0, "updated": 0}
    except Exception:
        logging.exception("Collection failed")
        app.state.job = {"status": "error", "message": "수집 중 화면을 읽지 못했습니다. 브라우저에서 로그인·인증 상태와 열린 화면을 확인하고 다시 시도하세요.", "added": 0, "updated": 0}
    finally:
        app.state.busy = False


@app.post("/api/collect")
async def collect(options: CollectInput):
    if options.mode not in ("current", "scroll"):
        raise HTTPException(422, "수집 방식이 올바르지 않습니다.")
    if options.use_ai and not app.state.ai.key():
        raise HTTPException(422, "AI API 키가 설정되어 있지 않습니다.")
    if app.state.busy:
        raise HTTPException(409, "이미 수집 중입니다.")
    if not app.state.browser.ready:
        raise HTTPException(409, "먼저 로그인 브라우저를 여세요.")
    app.state.busy = True
    app.state.task = asyncio.create_task(run_collection(options))
    return await status()


async def run_client_import(options):
    app.state.job = {"status": "running", "message": "PC에서 가져온 링크를 분류하고 있습니다.", "added": 0, "updated": 0}
    try:
        records = [record.model_dump(exclude_none=True) for record in options.records]
        classified, warning = await classify_records(
            records, options.use_ai, api_key=app.state.ai.key(),
            model=app.state.ai.model(), provider=app.state.ai.provider())
        added, updated = await asyncio.to_thread(app.state.store.upsert, classified)
        message = f"가져온 링크 {len(records)}개: 새 항목 {added}개, 보완한 항목 {updated}개."
        if warning:
            message += " " + warning
        app.state.job = {"status": "done", "message": message, "added": added, "updated": updated}
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Imported post/DM text is private; neither submitted data nor exception text is logged.
        logging.error("Client import failed (%s)", type(error).__name__)
        app.state.job = {"status": "error", "message": "가져온 링크를 저장하지 못했습니다. 잠시 후 다시 시도하세요.", "added": 0, "updated": 0}
    finally:
        app.state.busy = False


@app.post("/api/import")
async def import_records(request: Request):
    if app.state.busy:
        raise HTTPException(409, "이미 작업 중입니다.")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON 형식으로 가져오세요.")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) < 0 or int(length) > IMPORT_BODY_LIMIT:
                raise HTTPException(413, "한 번에 가져오는 자료는 2MB 이내로 제한됩니다.")
        except ValueError:
            raise HTTPException(400, "요청 크기를 확인할 수 없습니다.") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > IMPORT_BODY_LIMIT:
            raise HTTPException(413, "한 번에 가져오는 자료는 2MB 이내로 제한됩니다.")
        body.extend(chunk)
    try:
        values = ImportInput.model_validate_json(bytes(body))
    except ValidationError:
        # Avoid returning submitted credentials or private DM text in validation errors.
        raise HTTPException(422, "수집 데이터 형식이나 링크를 확인하세요. 한 번에 1~500개를 가져올 수 있습니다.") from None
    if values.use_ai and not app.state.ai.key():
        raise HTTPException(422, "먼저 AI 설정에서 API 키를 연결하세요.")
    # A second request may have started a job while this request was streaming.
    if app.state.busy:
        raise HTTPException(409, "이미 작업 중입니다.")
    app.state.busy = True
    app.state.job = {"status": "running", "message": "PC에서 가져온 링크를 분류하고 있습니다.", "added": 0, "updated": 0}
    app.state.task = asyncio.create_task(run_client_import(values))
    return await status()


async def run_reclassification(options):
    app.state.job = {"status": "running", "message": "기존 항목을 AI로 다시 분류하고 있습니다.", "added": 0, "updated": 0}
    try:
        stored = await asyncio.to_thread(app.state.store.all)
        records = [item for item in stored
                   if item["classification"] != "manual" and (not options.only_pending or item["category"] == "분류 보류")]
        if options.enrich_remote and app.state.browser.ready:
            async def progress(message):
                app.state.job["message"] = message
            pending = sorted((item for item in records if item["category"] == "분류 보류"), key=lambda item: item.get("detail_checked", False))
            enriched = await app.state.browser.enrich(pending, progress=progress)
            updates = {item["id"]: item for item in enriched}
            records = [updates.get(item["id"], item) for item in records]
        classified, warning = await classify_records(records, use_ai=True, api_key=app.state.ai.key(), model=app.state.ai.model(), provider=app.state.ai.provider())
        updated = await asyncio.to_thread(app.state.store.reclassify, classified)
        message = f"{updated}개 항목을 AI로 다시 분류했습니다. 직접 수정한 카테고리는 유지했습니다."
        if warning:
            message += " " + warning
        app.state.job = {"status": "done", "message": message, "added": 0, "updated": updated}
    except asyncio.CancelledError:
        raise
    except ValueError as error:
        app.state.job = {"status": "error", "message": str(error), "added": 0, "updated": 0}
    except Exception:
        logging.exception("Reclassification failed")
        app.state.job = {"status": "error", "message": "다시 분류하지 못했습니다. AI 설정과 로그인 상태를 확인하세요.", "added": 0, "updated": 0}
    finally:
        app.state.busy = False


@app.post("/api/classify")
async def reclassify(options: ClassifyInput):
    if app.state.busy:
        raise HTTPException(409, "이미 작업 중입니다.")
    if not app.state.ai.key():
        raise HTTPException(422, "먼저 AI 설정에서 API 키를 연결하세요.")
    app.state.busy = True
    app.state.task = asyncio.create_task(run_reclassification(options))
    return await status()


@app.get("/api/items")
async def items(q: str = "", category: str = "", source: str = ""):
    return {"items": await asyncio.to_thread(app.state.store.all, q[:300], category, source)}


@app.patch("/api/items/{item_id}")
async def edit(item_id: str, values: EditInput):
    if values.category not in CATEGORIES:
        raise HTTPException(422, "카테고리를 확인하세요.")
    result = await asyncio.to_thread(app.state.store.update, item_id, values.category, values.note)
    if not result:
        raise HTTPException(404, "항목을 찾지 못했습니다.")
    return result


@app.get("/api/export")
async def export():
    data = {"format": "instagram-organizer-v1", "items": await asyncio.to_thread(app.state.store.all)}
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="instagram-library.json"'})


@app.get("/browser/{path:path}")
async def browser_asset(path: str):
    root = Path(os.getenv("NOVNC_DIR", "/usr/share/novnc")).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "브라우저 화면 파일을 찾지 못했습니다.")
    return FileResponse(target)


@app.websocket("/browser/websockify")
async def browser_socket(socket: WebSocket):
    scheme = "https" if socket.url.scheme == "wss" else "http"
    if not valid_session(socket.cookies.get(COOKIE), app.state.password) or not origin_matches(socket.headers.get("origin"), socket.headers.get("host", ""), scheme):
        await socket.close(code=1008)
        return
    offered = socket.headers.get("sec-websocket-protocol", "").split(",")
    await socket.accept(subprotocol="binary" if "binary" in [part.strip() for part in offered] else None)
    tasks = []
    try:
        async with websockets.connect("ws://127.0.0.1:6080/websockify", subprotocols=["binary"], proxy=None, max_size=8 * 1024 * 1024) as upstream:
            async def to_vnc():
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    await upstream.send(message.get("bytes") if message.get("bytes") is not None else message["text"])
            async def to_browser():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await socket.send_bytes(message)
                    else:
                        await socket.send_text(message)
            tasks = [asyncio.create_task(to_vnc()), asyncio.create_task(to_browser())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (OSError, WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
        pass
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError, websockets.exceptions.ConnectionClosed):
                await task
        with contextlib.suppress(RuntimeError):
            await socket.close()
