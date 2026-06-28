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
        self.call_args = []

    def __call__(self, messages, tools=None, tool_choice="auto"):
        self.call_args.append(
            {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        )
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
             "description": "A late public ritual with unusual local context.",
             "lat": 52.5, "lon": 13.4}
    monkeypatch.setattr(events, "search", lambda **k: [event])
    monkeypatch.setattr(events, "by_ids", lambda ids: [event] if 1 in ids else [])
    monkeypatch.setattr(memory, "load_history", lambda thread_id: [])
    monkeypatch.setattr(memory, "save_history", lambda thread_id, messages: None)

    async def inline_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(build, "run_in_threadpool", inline_threadpool)
    return event


def test_discovery_registers_event_refs_then_propose_adds_reason(repo, monkeypatch):
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
    assert names.count("event_refs") == 1
    assert "events" not in names
    assert "propose" in names
    surfaced = [data for name, data in out if name == "event_refs"][0]
    assert surfaced["events"][0]["event_id"] == 1
    assert surfaced["events"][0]["description"] == "A late public ritual with unusual local context."
    proposal = dict(out)["propose"]
    assert proposal["picks"] == [{"event_id": 1, "reason": "odd public ritual"}]
    assert proposal["events"][0]["event_id"] == 1
    assert proposal["events"][0]["reason"] == "odd public ritual"
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
    assert [name for name, _ in out if name in ("event_refs", "events", "propose")] == [
        "event_refs",
        "propose",
    ]
    assert dict(out)["event_refs"]["events"][0]["title"] == "Odd ritual"
    assert _token_text(out) == "Recommended [[1|Odd ritual]]."


def test_empty_final_answer_gets_contextual_fallback(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text(""),
        _assistant_text("I would treat {{event:1}} as the useful match because its description gives unusual local context."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t3", "find ritual events", budget=5))
    text = _token_text(out)
    assert "I would treat" in text
    assert "[[1|Odd ritual]]" in text
    assert "{{event:1}}" not in text
    assert llm.call_args[-1]["tool_choice"] == "none"
    assert llm.call_args[-1]["tools"] is None


def test_non_event_final_answer_gets_contextual_fallback(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text("Great, I found something. Let me also check for more."),
        _assistant_text("{{event:1}} is the best match because the record describes an unusual local ritual."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t11", "find ritual events", budget=5))
    text = _token_text(out)
    assert "Let me also check" not in text
    assert "[[1|Odd ritual]]" in text
    assert "unusual local ritual" in text


def test_no_match_final_answer_is_not_forced_to_event_fallback(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "ritual"}'),
        _assistant_text("I did not find a relevant match for that request."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t12", "find ritual events", budget=5))
    text = _token_text(out)
    assert text == "I did not find a relevant match for that request."
    assert "[[1|Odd ritual]]" not in text


def test_contextual_fallback_uses_facet_signal(monkeypatch):
    event = {
        "id": 2,
        "title": "Gaza solidarity gathering",
        "date": "2026-06-27",
        "time": "13:00:00",
        "category": "action/protest/camp",
        "district": "Friedrichshain",
        "location": "Square",
        "description": "Information about Gaza and Nahost solidarity.",
        "lat": 52.5,
        "lon": 13.4,
    }
    monkeypatch.setattr(events, "search", lambda **k: [event])
    monkeypatch.setattr(events, "by_ids", lambda ids: [event] if 2 in ids else [])
    monkeypatch.setattr(memory, "load_history", lambda thread_id: [])
    monkeypatch.setattr(memory, "save_history", lambda thread_id, messages: None)

    async def inline_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(build, "run_in_threadpool", inline_threadpool)
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"keyword": "Gaza"}'),
        _assistant_text(""),
        _assistant_text(""),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    out = _drain(build.stream_answer("t13", "middle east events", budget=5))
    text = _token_text(out)
    assert "[[2|Gaza solidarity gathering]]" in text
    assert "strong Middle East match" in text
    assert "tagged action/protest/camp" not in text


def test_empty_final_answer_uses_proposal_reason(repo, monkeypatch):
    llm = ScriptedLLM([
        _assistant_tool(
            "propose_editors_choice",
            '{"picks": [{"event_id": 1, "reason": "strong local relevance"}]}',
        ),
        _assistant_text(""),
        _assistant_text("Recommended {{event:1}} because it has strong local relevance."),
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


def test_selected_day_clamps_tool_date_window(repo, monkeypatch):
    seen = {}

    def recording_search(**kwargs):
        seen.update(kwargs)
        return [repo]

    monkeypatch.setattr(events, "search", recording_search)
    llm = ScriptedLLM([
        _assistant_tool("query_events", '{"date_from": "2026-01-01", "date_to": "2026-12-31"}'),
        _assistant_text("Here is {{event:1}}."),
    ])
    monkeypatch.setattr(nodes.deepseek, "chat", llm)

    _drain(build.stream_answer("t10", "what's on", budget=5, date="2026-06-27"))
    assert seen["date_from"] == "2026-06-27"
    assert seen["date_to"] == "2026-06-27"


def test_clamp_helper_forces_day_analysis_date():
    assert build._clamp_to_day("day_analysis", {"date": "2026-01-01"}, "2026-06-27") == {
        "date": "2026-06-27"
    }
    assert build._clamp_to_day("query_events", {}, None) == {}
