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
        _assistant_text("I would start with Odd ritual."),
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
    text = "".join(d["text"] for n, d in out if n == "token")
    assert "Odd ritual" in text


def test_direct_propose_surfaces_event_for_action(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool(
            "propose_editors_choice",
            '{"picks": [{"event_id": 1, "reason": "strong local relevance"}]}',
        ),
        _assistant_text("Recommended it."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t2", "recommend event 1", budget=5))
    assert [name for name, _ in out if name in ("events", "propose")] == [
        "events",
        "propose",
    ]
    assert dict(out)["events"]["events"][0]["title"] == "Odd ritual"


def test_empty_final_answer_gets_contextual_fallback(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text(""),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t3", "find ritual events", budget=5))
    text = "".join(d["text"] for n, d in out if n == "token")
    assert "I found 1 matching event card" in text
    assert "Odd ritual" in text
    assert "id 1" in text
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
    text = "".join(d["text"] for n, d in out if n == "token")
    assert "Odd ritual" in text
    assert "strong local relevance" in text


def test_resume_flow_removed():
    assert not hasattr(build, "stream_resume")
