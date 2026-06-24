from collections import Counter

from pydantic import BaseModel, Field

from agent.db.repository import events
from agent.tools.base import tool


class DayAnalysisArgs(BaseModel):
    date: str = Field(description="The day to analyze, ISO format YYYY-MM-DD.")


_DAY_PARTS = (
    ("morning", 6, 12),
    ("afternoon", 12, 17),
    ("evening", 17, 22),
    ("night", 22, 30),
)


def _hour(event):
    t = event.get("time")
    if not t:
        return None
    try:
        return int(str(t).split(":", 1)[0])
    except (ValueError, IndexError):
        return None


def _part(hour):
    if hour is None:
        return "unknown"
    h = hour if hour >= 6 else hour + 24
    for name, lo, hi in _DAY_PARTS:
        if lo <= h < hi:
            return name
    return "night"


@tool(
    "day_analysis",
    "Summarize a single day: total events, breakdown by category and district, and "
    "how event starts spread across the day. Use this for 'how busy is X' or "
    "'what dominates day X' questions. Read-only.",
    DayAnalysisArgs,
)
def day_analysis(args: DayAnalysisArgs):
    day = events.on_date(args.date)
    by_category = Counter(e["category"] for e in day if e.get("category"))
    by_district = Counter(e["district"] for e in day if e.get("district"))
    by_part = Counter(_part(_hour(e)) for e in day)
    peak = by_part.most_common(1)[0][0] if by_part else None
    return {
        "date": args.date,
        "total_events": len(day),
        "by_category": dict(by_category.most_common(10)),
        "by_district": dict(by_district.most_common(10)),
        "time_density": dict(by_part),
        "peak_part": peak,
        "distinct_venues": len({e["location"] for e in day if e.get("location")}),
    }
