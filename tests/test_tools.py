import pytest

from agent import tools
from agent.db.repository import events
from agent.tools.base import ToolError


@pytest.fixture
def seed(monkeypatch):
    rows = [
        {"id": 1, "title": "Climate march", "date": "2026-06-27", "time": "14:00:00",
         "category": "Demonstration", "district": "Mitte", "location": "Alexanderplatz",
         "lat": 52.52, "lon": 13.40},
        {"id": 2, "title": "Jazz night", "date": "2026-06-27", "time": "20:00:00",
         "category": "Kultur", "district": "Neukölln", "location": "Club",
         "lat": 52.48, "lon": 13.43},
    ]
    by_id = {r["id"]: r for r in rows}
    monkeypatch.setattr(events, "search", lambda **k: rows)
    monkeypatch.setattr(events, "by_ids", lambda ids: [by_id[i] for i in ids if i in by_id])
    monkeypatch.setattr(events, "on_date", lambda date, limit=2000: rows)
    return rows


def test_query_events_shape(seed):
    out = tools.dispatch("query_events", {"keyword": "climate"})
    assert out["count"] == 2
    assert out["events"][0]["title"] == "Climate march"


def test_query_events_rejects_bad_limit(seed):
    with pytest.raises(ToolError):
        tools.dispatch("query_events", {"limit": 999})


def test_day_analysis_aggregates(seed):
    out = tools.dispatch("day_analysis", {"date": "2026-06-27"})
    assert out["total_events"] == 2
    assert out["by_category"] == {"Demonstration": 1, "Kultur": 1}
    assert out["time_density"]["afternoon"] == 1
    assert out["time_density"]["evening"] == 1


def test_propose_validates_missing(seed):
    with pytest.raises(ToolError):
        tools.dispatch(
            "propose_editors_choice",
            {"picks": [{"event_id": 999, "reason": "Worth checking"}]},
        )


def test_propose_returns_reasons_without_writing(seed):
    out = tools.dispatch(
        "propose_editors_choice",
        {"picks": [{"event_id": 1, "reason": "Unusual civic angle"}]},
    )
    assert out["picks"] == [{"event_id": 1, "reason": "Unusual civic angle"}]
    assert out["events"][0]["id"] == 1


def test_commit_not_exposed_to_llm():
    assert "commit_editors_choice" not in tools.LLM_TOOL_NAMES
    assert tools.get("commit_editors_choice") is None


def test_nearby_requires_center():
    with pytest.raises(ToolError):
        tools.dispatch("nearby_events", {"radius_km": 1})
