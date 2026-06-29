import asyncio
from types import SimpleNamespace

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
    service._mem_login_failures.clear()
    service._mem_login_locks.clear()


class FakeRequest:
    def __init__(self, body=None, headers=None, cookies=None, json_error=None):
        self._body = body
        self._json_error = json_error
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.url = SimpleNamespace(scheme="https", netloc="maps.farhadlabs.com")
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        if self._json_error:
            raise self._json_error
        return self._body


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


def test_fastapi_docs_are_disabled():
    paths = {getattr(route, "path", None) for route in service.app.routes}
    assert "/docs" not in paths
    assert "/redoc" not in paths
    assert "/openapi.json" not in paths


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


def test_same_origin_rejects_wrong_origin():
    request = FakeRequest(
        headers={
            "origin": "https://evil.example",
            "host": "maps.farhadlabs.com",
            "x-forwarded-proto": "https",
        }
    )
    with pytest.raises(HTTPException) as exc:
        service.require_same_origin(request)
    assert exc.value.status_code == 403


def test_same_origin_accepts_matching_origin():
    request = FakeRequest(
        headers={
            "origin": "https://maps.farhadlabs.com",
            "host": "maps.farhadlabs.com",
            "x-forwarded-proto": "https",
        }
    )
    service.require_same_origin(request)


def test_editors_choice_rejects_bad_json(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)
    request = FakeRequest(json_error=ValueError("bad json"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.editors_choice_set(request, sid="sid"))
    assert exc.value.status_code == 400


def test_editors_choice_rejects_invalid_items(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)
    invalid_bodies = [
        [],
        {},
        {"items": "bad"},
        {"items": ["bad"]},
        {"items": [{}]},
        {"items": [{"event_id": "1.5"}]},
        {"items": [{"event_id": 1, "note": 3}]},
    ]
    for body in invalid_bodies:
        request = FakeRequest(body=body)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(service.editors_choice_set(request, sid="sid"))
        assert exc.value.status_code == 400


def test_parse_editor_choice_accepts_empty_and_valid_items():
    assert service.parse_editor_choice_items({"items": []}) == []
    assert service.parse_editor_choice_items(
        {"items": [{"event_id": "12", "note": "Worth a look", "extra": "ignored"}]}
    ) == [{"event_id": 12, "note": "Worth a look"}]


def test_login_lockout_in_memory(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "LOGIN_MAX_FAILURES", 2)
    monkeypatch.setattr(config, "LOGIN_LOCKOUT_SECONDS", 900)
    monkeypatch.setattr(config, "LOGIN_FAILURE_WINDOW_SECONDS", 900)

    ip = "203.0.113.10"
    assert not service.login_is_locked(ip)
    service.record_login_failure(ip)
    assert not service.login_is_locked(ip)
    service.record_login_failure(ip)
    assert service.login_is_locked(ip)
    service.clear_login_failures(ip)
    assert not service.login_is_locked(ip)


def test_login_rejects_wrong_origin_before_credentials(tmp_path, monkeypatch):
    _init_serializer(monkeypatch, tmp_path)
    request = FakeRequest(
        body={"password": "anything"},
        headers={
            "origin": "https://evil.example",
            "host": "maps.farhadlabs.com",
            "x-forwarded-proto": "https",
        },
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.login(request))
    assert exc.value.status_code == 403
