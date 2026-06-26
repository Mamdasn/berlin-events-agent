import asyncio

import pytest

from agent.db.repository import events
from agent import memory
from agent.graph import build, nodes


def _assistant_tool(name, arguments="{}", call_id="c1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": arguments}},
        ],
    }


def _assistant_text(text):
    return {"role": "assistant", "content": text}


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages, tools=None, tool_choice="auto"):
        reply = self.replies[self.calls]
        self.calls += 1
        return reply


def _drain(agen):
    async def run():
        return [item async for item in agen]

    return asyncio.run(run())


def _token_text(out):
    return "".join(d["text"] for n, d in out if n == "token")


@pytest.fixture
def repo(monkeypatch):
    event = {"id": 1, "title": "Odd ritual", "date": "2026-06-27", "time": "21:00:00",
             "category": "Kultur", "district": "Mitte", "location": "Square",
             "lat": 52.5, "lon": 13.4}
    monkeypatch.setattr(events, "search", lambda **k: [event])
    monkeypatch.setattr(events, "by_ids", lambda ids: [event] if 1 in ids else [])
    monkeypatch.setattr(memory, "load_history", lambda thread_id: [])
    monkeypatch.setattr(memory, "save_history", lambda thread_id, messages: None)

    async def inline_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(build, "run_in_threadpool", inline_threadpool)
    return event


def test_discovery_surfaces_events_then_propose_adds_reason(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_tool(
            "propose_editors_choice",
            '{"picks": [{"event_id": 1, "reason": "odd public ritual"}]}',
            "c2",
        ),
        _assistant_text("I would start with {{event:1}}."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t1", "find something weird", budget=5))
    names = [n for n, _ in out]
    assert names.count("events") == 1
    assert "propose" in names
    surfaced = [data for name, data in out if name == "events"][0]
    assert surfaced["events"][0]["event_id"] == 1
    proposal = dict(out)["propose"]
    assert proposal["picks"] == [{"event_id": 1, "reason": "odd public ritual"}]
    text = _token_text(out)
    assert text == "I would start with [[1|Odd ritual]]."


def test_direct_propose_surfaces_event_for_action(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool(
            "propose_editors_choice",
            '{"picks": [{"event_id": 1, "reason": "strong local relevance"}]}',
        ),
        _assistant_text("Recommended {{event:1}}."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t2", "recommend event 1", budget=5))
    assert [name for name, _ in out if name in ("events", "propose")] == [
        "events",
        "propose",
    ]
    assert dict(out)["events"]["events"][0]["title"] == "Odd ritual"
    assert _token_text(out) == "Recommended [[1|Odd ritual]]."


def test_empty_final_answer_gets_contextual_fallback(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text(""),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t3", "find ritual events", budget=5))
    text = _token_text(out)
    assert "I found 1 matching event card" in text
    assert "[[1|Odd ritual]]" in text
    assert "{{event:1}}" not in text
    assert "fits because" in text


def test_empty_final_answer_uses_proposal_reason(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool(
            "propose_editors_choice",
            '{"picks": [{"event_id": 1, "reason": "strong local relevance"}]}',
        ),
        _assistant_text(""),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t4", "recommend event 1", budget=5))
    text = _token_text(out)
    assert "[[1|Odd ritual]]" in text
    assert "strong local relevance" in text


def test_event_handle_uses_registry_title(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text("Fake title is not trusted: {{event:1}}."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t5", "find ritual events", budget=5))
    text = _token_text(out)
    assert text == "Fake title is not trusted: [[1|Odd ritual]]."


def test_unknown_event_handle_does_not_create_wildcard(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text("I cannot validate {{event:999}} from the current tools."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t6", "find ritual events", budget=5))
    text = _token_text(out)
    assert "[[999|" not in text
    assert "event 999" in text


def test_event_handle_without_registry_does_not_leak(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_text("I cannot validate {{event:999}} without tool context."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t10", "hello", budget=5))
    text = _token_text(out)
    assert "{{event:999}}" not in text
    assert "[[999|" not in text
    assert "event 999" in text


def test_raw_known_title_gets_wildcard_fallback(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text("Odd ritual fits because it is tagged Kultur."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t7", "find ritual events", budget=5))
    assert _token_text(out) == "[[1|Odd ritual]] fits because it is tagged Kultur."


def test_known_id_gets_wildcard_fallback(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text("id 1 fits because it is tagged Kultur."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t8", "find ritual events", budget=5))
    assert _token_text(out) == "[[1|Odd ritual]] fits because it is tagged Kultur."


def test_existing_marker_title_is_normalized(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text("[[1|Wrong title]] fits because it is tagged Kultur."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t9", "find ritual events", budget=5))
    assert _token_text(out) == "[[1|Odd ritual]] fits because it is tagged Kultur."


def test_title_fallback_does_not_rewrite_inside_generated_marker():
    surfaced = {1: {"event_id": 1, "title": "id 1 gathering"}}
    text = build._finalize_event_mentions("id 1 gathering fits.", surfaced)
    assert text == "[[1|id 1 gathering]] fits."


def test_resume_flow_removed():
    assert not hasattr(build, "stream_resume")
