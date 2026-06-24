import json
import os
import secrets
from pathlib import Path

from agent.config import config


def _store_path():
    return Path(config.SECRET_STORE_PATH)


def _read():
    p = _store_path()
    try:
        return json.loads(p.read_bytes())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data):
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o600)
    os.replace(tmp, p)


def get_session_secret():
    env = os.environ.get("COOKIE_SESSION_SECRET")
    if env:
        return env
    data = _read()
    secret = data.get("session_secret")
    if secret:
        return secret
    secret = secrets.token_urlsafe(48)
    data = _read()
    data["session_secret"] = secret
    _write(data)
    return secret


def get_active_totp_secret():
    env = os.environ.get("ADMIN_TOTP_SECRET")
    if env:
        return env
    return _read().get("totp_secret")


def set_pending_totp_secret(secret):
    data = _read()
    data["pending_totp_secret"] = secret
    _write(data)


def get_pending_totp_secret():
    return _read().get("pending_totp_secret")


def promote_pending_totp():
    data = _read()
    pending = data.pop("pending_totp_secret", None)
    if pending:
        data["totp_secret"] = pending
        _write(data)
    return pending


def clear_pending_totp():
    data = _read()
    data.pop("pending_totp_secret", None)
    _write(data)
