from agent.config import config
from agent.graph import guardrails


def test_sanitize_strips_control_chars():
    out = guardrails.sanitize_user_message("hi\x00\x07 there")
    assert "\x00" not in out and "\x07" not in out
    assert out.startswith("hi") and out.endswith("there")


def test_sanitize_truncates(monkeypatch):
    monkeypatch.setattr(config, "AGENT_MESSAGE_MAX_CHARS", 10)
    out = guardrails.sanitize_user_message("x" * 50)
    assert len(out) <= 11
    assert out.endswith("…")


def test_system_prompt_has_security_stance():
    prompt = guardrails.system_prompt()
    assert "untrusted data" in prompt
    assert "approve" in prompt.lower()
