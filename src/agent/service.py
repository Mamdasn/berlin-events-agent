import json
import logging
import re
import secrets
import time
from datetime import date as _date
from pathlib import Path

import pyotp
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.concurrency import run_in_threadpool

from agent import secret_store
from agent._password import verify_password
from agent.config import config
from agent.db.client import db
from agent.db.repository import events

CURATOR_DIR = Path(__file__).resolve().parents[2] / "curator"
SESSION_PREFIX = "ec:session:"
LOGIN_FAIL_PREFIX = "ec:login-fail:ip:"
LOGIN_LOCK_PREFIX = "ec:login-lock:ip:"
_EVENT_ID_RE = re.compile(r"\d+")

app = FastAPI(
    title="Editor's Choice curation agent",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

log = logging.getLogger(__name__)

_serializer = None
_redis = None
_mem_sessions = {}
_mem_login_failures = {}
_mem_login_locks = {}


@app.on_event("startup")
async def _ensure_schema():
    try:
        await run_in_threadpool(events.ensure_schema)
    except Exception:
        log.exception("Could not ensure editors_choice schema")


def _get_serializer():
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(
            secret_store.get_session_secret(), salt="ec-session"
        )
    return _serializer


def _store():
    global _redis
    if not config.REDIS_URL:
        return None
    if _redis is None:
        import redis

        url = config.REDIS_URL.rstrip("/") + f"/{config.AGENT_REDIS_DB_MEMORY}"
        _redis = redis.from_url(url, decode_responses=True)
    return _redis


def create_session():
    sid = secrets.token_urlsafe(32)
    store = _store()
    if store is not None:
        store.setex(SESSION_PREFIX + sid, config.SESSION_TTL_SECONDS, "1")
    else:
        _mem_sessions[sid] = time.time() + config.SESSION_TTL_SECONDS
    return _get_serializer().dumps(sid)


def read_session(cookie):
    if not cookie:
        return None
    try:
        sid = _get_serializer().loads(cookie, max_age=config.SESSION_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    store = _store()
    if store is not None:
        return sid if store.exists(SESSION_PREFIX + sid) else None
    expires = _mem_sessions.get(sid)
    if expires and expires > time.time():
        return sid
    _mem_sessions.pop(sid, None)
    return None


def destroy_session(cookie):
    if not cookie:
        return
    try:
        sid = _get_serializer().loads(cookie, max_age=config.SESSION_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return
    store = _store()
    if store is not None:
        store.delete(SESSION_PREFIX + sid)
    else:
        _mem_sessions.pop(sid, None)


def verify_totp(code, secret=None):
    if not code:
        return False
    if secret is None:
        secret = secret_store.get_active_totp_secret()
    if not secret:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


async def require_session(request: Request):
    sid = read_session(request.cookies.get(config.SESSION_COOKIE))
    if not sid:
        raise HTTPException(status_code=401, detail="authentication required")
    return sid


def request_origin(request: Request):
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def require_same_origin(request: Request):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != request_origin(request):
        raise HTTPException(status_code=403, detail="bad origin")


def client_ip(request: Request):
    return (
        request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        or (request.client.host if request.client else "")
        or "unknown"
    )


def login_is_locked(ip):
    store = _store()
    if store is not None:
        return bool(store.exists(LOGIN_LOCK_PREFIX + ip))
    now = time.time()
    expires = _mem_login_locks.get(ip)
    if expires and expires > now:
        return True
    _mem_login_locks.pop(ip, None)
    return False


def record_login_failure(ip):
    store = _store()
    if store is not None:
        key = LOGIN_FAIL_PREFIX + ip
        count = int(store.incr(key))
        if count == 1:
            store.expire(key, config.LOGIN_FAILURE_WINDOW_SECONDS)
        if count >= config.LOGIN_MAX_FAILURES:
            store.setex(LOGIN_LOCK_PREFIX + ip, config.LOGIN_LOCKOUT_SECONDS, "1")
        return count
    now = time.time()
    count, expires = _mem_login_failures.get(ip, (0, 0))
    if expires <= now:
        count = 0
    count += 1
    _mem_login_failures[ip] = (count, now + config.LOGIN_FAILURE_WINDOW_SECONDS)
    if count >= config.LOGIN_MAX_FAILURES:
        _mem_login_locks[ip] = now + config.LOGIN_LOCKOUT_SECONDS
    return count


def clear_login_failures(ip):
    store = _store()
    if store is not None:
        store.delete(LOGIN_FAIL_PREFIX + ip)
        store.delete(LOGIN_LOCK_PREFIX + ip)
        return
    _mem_login_failures.pop(ip, None)
    _mem_login_locks.pop(ip, None)


async def read_json_object(request: Request):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return body


def parse_editor_choice_items(body):
    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="each item must be an object")
        if "event_id" not in raw:
            raise HTTPException(status_code=400, detail="event_id is required")
        raw_event_id = raw["event_id"]
        if isinstance(raw_event_id, bool):
            raise HTTPException(status_code=400, detail="event_id must be an integer")
        if isinstance(raw_event_id, int):
            event_id = raw_event_id
        elif isinstance(raw_event_id, str) and _EVENT_ID_RE.fullmatch(raw_event_id.strip()):
            event_id = int(raw_event_id)
        else:
            raise HTTPException(status_code=400, detail="event_id must be an integer")
        note = raw.get("note")
        if note is not None and not isinstance(note, str):
            raise HTTPException(status_code=400, detail="note must be a string")
        items.append({"event_id": event_id, "note": note})
    return items


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def agent_events(thread_id, message, date=None):
    try:
        from agent.graph.build import stream_answer
    except Exception:
        yield "error", {"message": "The curation agent is not wired up yet."}
        return
    async for name, data in stream_answer(
        thread_id=thread_id,
        message=message,
        budget=config.AGENT_MAX_TOOL_CALLS,
        date=date,
    ):
        yield name, data


def _set_session():
    response = JSONResponse({"ok": True})
    response.set_cookie(
        config.SESSION_COOKIE,
        create_session(),
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root(request: Request):
    if not read_session(request.cookies.get(config.SESSION_COOKIE)):
        return RedirectResponse("login.html", status_code=302)
    return FileResponse(CURATOR_DIR / "index.html")


@app.get("/login.html")
async def login_page():
    return FileResponse(CURATOR_DIR / "login.html")


@app.get("/login.css")
async def login_css():
    return FileResponse(CURATOR_DIR / "login.css")


@app.get("/style.css")
async def style(sid: str = Depends(require_session)):
    return FileResponse(
        CURATOR_DIR / "style.css",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/chat.js")
async def chat_js(sid: str = Depends(require_session)):
    return FileResponse(
        CURATOR_DIR / "chat.js",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/qrcode.js")
async def qrcode_js():
    return FileResponse(CURATOR_DIR / "qrcode.min.js")


@app.get("/material-symbols.woff2")
async def material_symbols_font():
    return FileResponse(
        CURATOR_DIR / "material-symbols.woff2",
        media_type="font/woff2",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.post("/login")
async def login(request: Request):
    require_same_origin(request)
    ip = client_ip(request)
    if login_is_locked(ip):
        return JSONResponse({"error": "Too many failed attempts."}, status_code=429)
    try:
        body = await read_json_object(request)
    except HTTPException:
        return JSONResponse({"error": "Bad request."}, status_code=400)

    password = body.get("password") or ""
    code = (body.get("code") or "").strip()

    if not verify_password(password, config.ADMIN_PW_HASH):
        record_login_failure(ip)
        return JSONResponse({"error": "Wrong password."}, status_code=401)

    active_secret = secret_store.get_active_totp_secret()
    if not active_secret:
        pending = secret_store.get_pending_totp_secret()
        if not code:
            if not pending:
                pending = pyotp.random_base32()
                secret_store.set_pending_totp_secret(pending)
            uri = pyotp.TOTP(pending).provisioning_uri(
                name=config.EDITOR_NAME, issuer_name="Berlin Events Curator"
            )
            return JSONResponse({"enroll": True, "otpauth_uri": uri, "secret": pending})

        if pending and verify_totp(code, pending):
            secret_store.promote_pending_totp()
            clear_login_failures(ip)
            return _set_session()

        secret_store.clear_pending_totp()
        record_login_failure(ip)
        return JSONResponse({"error": "Wrong code. Try again."}, status_code=401)

    if not verify_totp(code, active_secret):
        record_login_failure(ip)
        return JSONResponse({"error": "Wrong code."}, status_code=401)
    clear_login_failures(ip)
    return _set_session()


@app.post("/logout")
async def logout(request: Request):
    require_same_origin(request)
    destroy_session(request.cookies.get(config.SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(config.SESSION_COOKIE, path="/")
    return response


def _valid_date(value):
    if not value:
        return None
    try:
        return _date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        return None


@app.post("/ask/stream")
async def ask_stream(request: Request, sid: str = Depends(require_session)):
    require_same_origin(request)
    body = await read_json_object(request)
    message = (body.get("message") or "").strip()
    thread_id = body.get("thread_id") or sid
    date = _valid_date(body.get("date"))

    async def generate():
        if not message:
            yield sse("error", {"message": "Empty message."})
            yield sse("done", {"thread_id": thread_id})
            return
        try:
            async for name, data in agent_events(thread_id, message, date):
                yield sse(name, data)
        except Exception:
            yield sse("error", {"message": "The agent failed mid-response."})
        yield sse("done", {"thread_id": thread_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _invalidate_map_cache():
    r = db.redis(config.WEBAPP_PG_CACHE_DB)
    if r is None:
        return
    try:
        for pattern in ("locations::*", "page::*", "api::*"):
            for key in r.scan_iter(match=pattern, count=200):
                r.delete(key)
    except Exception:
        log.warning("map cache invalidation failed", exc_info=True)


@app.get("/editors-choice")
async def editors_choice_list(sid: str = Depends(require_session)):
    items = await run_in_threadpool(events.featured)
    return {"items": items}


@app.post("/editors-choice")
async def editors_choice_set(request: Request, sid: str = Depends(require_session)):
    require_same_origin(request)
    body = await read_json_object(request)
    items = parse_editor_choice_items(body)
    await run_in_threadpool(events.set_featured, items)
    await run_in_threadpool(_invalidate_map_cache)
    return {"ok": True, "count": len(items)}
