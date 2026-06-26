import asyncio

import pyotp
import pytest
from fastapi import HTTPException

from agent import secret_store
from agent import service
from agent._password import hash_password, verify_password
from agent.config import config


def test_password_hash_roundtrip():
    stored = hash_password("hunter2", iterations=1000)
    assert verify_password("hunter2", stored)
    assert not verify_password("wrong", stored)


def test_password_hash_format():
    h = hash_password("test", iterations=1000)
    parts = h.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert parts[1] == "1000"
    assert len(parts[2]) == 32
    assert len(parts[3]) == 64
    assert verify_password("test", h)


def test_password_rejects_garbage_hash():
    assert not verify_password("x", "not-a-valid-hash")


def test_totp_accepts_valid_code():
    secret = pyotp.random_base32()
    assert service.verify_totp(pyotp.TOTP(secret).now(), secret)
    assert not service.verify_totp("000000", secret)


def test_totp_rejected_when_unconfigured():
    assert not service.verify_totp("123456", None)
    assert not service.verify_totp("123456", "")


def _init_serializer(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SECRET_STORE_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.setattr(config, "REDIS_URL", "")
    monkeypatch.delenv("COOKIE_SESSION_SECRET", raising=False)
    service._serializer = None
    service._redis = None


def test_session_roundtrip(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)
    cookie = service.create_session()
    assert service.read_session(cookie) is not None
    service.destroy_session(cookie)
    assert service.read_session(cookie) is None


def test_session_rejects_tampered_cookie(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)
    assert service.read_session("garbage") is None
    assert service.read_session(None) is None


def test_secret_store_session_secret_generates_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRET_STORE_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.delenv("COOKIE_SESSION_SECRET", raising=False)
    s1 = secret_store.get_session_secret()
    assert s1
    s2 = secret_store.get_session_secret()
    assert s2 == s1


def test_totp_pending_promote_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRET_STORE_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)
    assert secret_store.get_active_totp_secret() is None

    secret = pyotp.random_base32()
    secret_store.set_pending_totp_secret(secret)
    assert secret_store.get_pending_totp_secret() == secret

    promoted = secret_store.promote_pending_totp()
    assert promoted == secret
    assert secret_store.get_pending_totp_secret() is None
    assert secret_store.get_active_totp_secret() == secret


def test_totp_clear_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRET_STORE_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)

    secret_store.set_pending_totp_secret(pyotp.random_base32())
    secret_store.clear_pending_totp()
    assert secret_store.get_pending_totp_secret() is None
    assert secret_store.get_active_totp_secret() is None


def test_totp_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRET_STORE_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.setenv("ADMIN_TOTP_SECRET", "env-override-secret")
    assert secret_store.get_active_totp_secret() == "env-override-secret"


def _route(path):
    return next(route for route in service.app.routes if getattr(route, "path", None) == path)


def test_curator_app_assets_require_session(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)

    for path in ("/style.css", "/chat.js"):
        deps = _route(path).dependant.dependencies
        assert any(dep.call is service.require_session for dep in deps)

    assert not _route("/login.css").dependant.dependencies

    class Request:
        cookies = {}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.require_session(Request()))
    assert exc.value.status_code == 401


def test_curator_app_assets_are_private_when_logged_in(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)

    for handler in (service.style, service.chat_js):
        response = asyncio.run(handler(sid="sid"))
        assert response.headers["cache-control"] == "private, no-store"

    login_response = asyncio.run(service.login_css())
    assert "login.css" in str(login_response.path)
