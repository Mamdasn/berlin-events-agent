import asyncio

import pytest

from agent.db.repository import events
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
    return event


def test_discovery_then_proposal_interrupt(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_tool("propose_editors_choice", '{"event_ids": [1], "note": "odd"}', "c2"),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t1", "find something weird", budget=5))
    names = [n for n, _ in out]
    assert "proposal" in names
    proposal = dict(out)["proposal"]
    assert proposal["events"][0]["id"] == 1
    assert proposal["proposal_id"]


def test_approve_commits(repo, monkeypatch):
    committed = {}
    monkeypatch.setattr(events, "feature",
                        lambda event_ids, note=None, selected_by=None: committed.update(
                            ids=list(event_ids), note=note) or len(event_ids))

    llm = ScriptedLLM([
        _assistant_tool("propose_editors_choice", '{"event_ids": [1]}'),
        _assistant_text("Featured it."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t2", "feature event 1", budget=5))
    proposal_id = dict(out)["proposal"]["proposal_id"]

    resumed = _drain(build.stream_resume("t2", proposal_id, "approve", "looks good"))
    assert committed["ids"] == [1]
    text = "".join(d["text"] for n, d in resumed if n == "token")
    assert "Featured" in text


def test_reject_does_not_commit(repo, monkeypatch):
    monkeypatch.setattr(events, "feature",
                        lambda *a, **k: pytest.fail("commit must not run on reject"))

    llm = ScriptedLLM([
        _assistant_tool("propose_editors_choice", '{"event_ids": [1]}'),
        _assistant_text("Okay, leaving it out."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t3", "feature event 1", budget=5))
    proposal_id = dict(out)["proposal"]["proposal_id"]

    resumed = _drain(build.stream_resume("t3", proposal_id, "reject", None))
    text = "".join(d["text"] for n, d in resumed if n == "token")
    assert "leaving it out" in text


def test_expired_proposal_errors():
    out = _drain(build.stream_resume("t4", "missing", "approve", None))
    assert out[0][0] == "error"
