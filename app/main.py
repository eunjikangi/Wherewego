import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .browser import BrowserManager
from .ai import AISettings
from .classifier import CATEGORIES, classify_records
from .store import Store

os.umask(0o077)
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
COOKIE = "organizer_session"
SESSION_SECONDS = 12 * 60 * 60
LOGIN_PAGE = """<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>모아분류 로그인</title>
<link rel='stylesheet' href='/style.css'><body><main style='max-width:440px;margin:12vh auto;padding:24px'><h1>모아분류</h1><p>서버 관리자 암호로 접속하세요.</p><form method='post' action='/login'><label for='password'>관리자 암호</label><input id='password' name='password' type='password' autocomplete='current-password' required style='display:block;width:100%;margin:12px 0;padding:14px;border:1px solid #ccd4e0;border-radius:10px'><button type='submit' style='padding:12px 20px;background:#245eea;color:white;border:0;border-radius:10px'>로그인</button></form><p style='font-size:14px;color:#52617b'>인스타그램 비밀번호는 접속 후 브라우저의 Instagram 화면에서 직접 입력합니다.</p>ERROR</main></body></html>"""


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
    model: str = Field(default="gpt-4.1-mini", max_length=80)


class ClassifyInput(BaseModel):
    only_pending: bool = False


@asynccontextmanager
async def lifespan(app):
    password = os.getenv("ADMIN_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters. Run ./setup.sh first.")
    directory = Path(os.getenv("DATA_DIR", "/data"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    app.state.password = password
    app.state.store = Store(directory)
    app.state.ai = AISettings(directory)
    app.state.browser = BrowserManager()
    app.state.busy = False
    app.state.job = {"status": "idle", "message": "브라우저를 열고 인스타그램에 로그인하세요.", "added": 0, "updated": 0}
    app.state.task = None
    app.state.login_failures = []
    yield
    if app.state.task and not app.state.task.done():
        app.state.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.task
    await app.state.browser.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def session_protection(request, call_next):
    path = request.url.path
    public = path in ("/login", "/health", "/style.css")
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


def status():
    return {"browser_ready": app.state.browser.ready, "busy": app.state.busy, "job": app.state.job,
            "total": app.state.store.total(), "categories": CATEGORIES, "ai_available": bool(app.state.ai.key())}


@app.get("/api/ai/config")
async def ai_config():
    return app.state.ai.public()


@app.put("/api/ai/config")
async def configure_ai(values: AIInput):
    if app.state.busy:
        raise HTTPException(409, "진행 중인 작업이 끝난 뒤 AI 설정을 변경하세요.")
    app.state.busy = True
    try:
        return await app.state.ai.configure(values.api_key, values.model)
    except ValueError as error:
        raise HTTPException(422, str(error))
    finally:
        app.state.busy = False


@app.get("/api/status")
async def get_status():
    return status()


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
    return status()


async def run_collection(options):
    app.state.job = {"status": "running", "message": "열려 있는 화면에서 링크를 확인하고 있습니다.", "added": 0, "updated": 0}
    try:
        async def progress(message):
            app.state.job["message"] = message
        records = await app.state.browser.collect(mode=options.mode, progress=progress)
        if options.use_ai and records:
            records = await app.state.browser.enrich(records, progress=progress)
        app.state.job["message"] = f"{len(records)}개 링크를 분류하고 있습니다."
        classified, warning = await classify_records(records, options.use_ai, api_key=app.state.ai.key(), model=app.state.ai.model())
        added, updated = app.state.store.upsert(classified)
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
    return status()


async def run_reclassification(options):
    app.state.job = {"status": "running", "message": "기존 항목을 AI로 다시 분류하고 있습니다.", "added": 0, "updated": 0}
    try:
        records = [item for item in app.state.store.all()
                   if item["classification"] != "manual" and (not options.only_pending or item["category"] == "분류 보류")]
        if app.state.browser.ready:
            async def progress(message):
                app.state.job["message"] = message
            pending = sorted((item for item in records if item["category"] == "분류 보류"), key=lambda item: item.get("detail_checked", False))
            enriched = await app.state.browser.enrich(pending, progress=progress)
            updates = {item["id"]: item for item in enriched}
            records = [updates.get(item["id"], item) for item in records]
        classified, warning = await classify_records(records, use_ai=True, api_key=app.state.ai.key(), model=app.state.ai.model())
        updated = app.state.store.reclassify(classified)
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
    return status()


@app.get("/api/items")
async def items(q: str = "", category: str = "", source: str = ""):
    return {"items": app.state.store.all(q[:300], category, source)}


@app.patch("/api/items/{item_id}")
async def edit(item_id: str, values: EditInput):
    if values.category not in CATEGORIES:
        raise HTTPException(422, "카테고리를 확인하세요.")
    result = app.state.store.update(item_id, values.category, values.note)
    if not result:
        raise HTTPException(404, "항목을 찾지 못했습니다.")
    return result


@app.get("/api/export")
async def export():
    data = {"format": "instagram-organizer-v1", "items": app.state.store.all()}
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
