import pyotp
import pytest

from agent import service
from agent.config import config
from scripts.admin_enroll import password_hash


def test_password_hash_roundtrip():
    stored = password_hash("hunter2", iterations=1000)
    assert service.verify_password("hunter2", stored)
    assert not service.verify_password("wrong", stored)


def test_password_rejects_garbage_hash():
    assert not service.verify_password("x", "not-a-valid-hash")


def test_totp_accepts_valid_code(monkeypatch):
    secret = pyotp.random_base32()
    monkeypatch.setattr(config, "ADMIN_TOTP_SECRET", secret)
    assert service.verify_totp(pyotp.TOTP(secret).now())
    assert not service.verify_totp("000000")


def test_totp_rejected_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOTP_SECRET", "")
    assert not service.verify_totp("123456")


def test_session_roundtrip():
    cookie = service.create_session()
    assert service.read_session(cookie) is not None
    service.destroy_session(cookie)
    assert service.read_session(cookie) is None


def test_session_rejects_tampered_cookie():
    assert service.read_session("garbage") is None
    assert service.read_session(None) is None
