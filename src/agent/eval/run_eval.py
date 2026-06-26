#!/usr/bin/env python3
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import tools  # noqa: E402
from agent.db.repository import events  # noqa: E402
from agent.retrieval import vector_store  # noqa: E402

DATASET = Path(__file__).with_name("dataset.jsonl")

ROWS = [
    {
        "id": 1,
        "title": "Climate march",
        "date": "2026-06-27",
        "time": "14:00:00",
        "category": "Demonstration",
        "district": "Mitte",
        "location": "Alexanderplatz",
        "description": "Climate justice march through central Berlin.",
        "lat": 52.5208,
        "lon": 13.4095,
    },
    {
        "id": 2,
        "title": "Jazz night",
        "date": "2026-06-27",
        "time": "20:00:00",
        "category": "Kultur",
        "district": "Neukolln",
        "location": "Club",
        "description": "Late evening jazz concert.",
        "lat": 52.4808,
        "lon": 13.431,
    },
    {
        "id": 3,
        "title": "Repair cafe",
        "date": "2026-06-28",
        "time": "12:00:00",
        "category": "Community",
        "district": "Kreuzberg",
        "location": "Workshop",
        "description": "Community repair session for small electronics.",
        "lat": 52.499,
        "lon": 13.421,
    },
    {
        "id": 4,
        "title": "Tenant advice clinic",
        "date": "2026-06-27",
        "time": "16:00:00",
        "category": "Support",
        "district": "Mitte",
        "location": "Advice office",
        "description": "Tenant advice and housing rights support.",
        "lat": 52.521,
        "lon": 13.411,
    },
    {
        "id": 5,
        "title": "Kids print workshop",
        "date": "2026-06-28",
        "time": "15:00:00",
        "category": "Workshop",
        "district": "Pankow",
        "location": "Family center",
        "description": "Screen printing workshop for families.",
        "lat": 52.545,
        "lon": 13.415,
    },
]


def _contains(value, needle):
    return needle.casefold() in str(value or "").casefold()


def _distance_km(a, b):
    lat1, lon1 = math.radians(a["lat"]), math.radians(a["lon"])
    lat2, lon2 = math.radians(b["lat"]), math.radians(b["lon"])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def install_fixture():
    by_id = {row["id"]: row for row in ROWS}

    def search(date_from=None, date_to=None, district=None, category=None, keyword=None, limit=None):
        found = []
        for row in ROWS:
            if date_from and row["date"] < date_from:
                continue
            if date_to and row["date"] > date_to:
                continue
            if district and not _contains(row["district"], district):
                continue
            if category and not _contains(row["category"], category):
                continue
            if keyword and not (
                _contains(row["title"], keyword)
                or _contains(row["description"], keyword)
                or _contains(row["category"], keyword)
            ):
                continue
            found.append(dict(row))
        return found[: int(limit or len(found))]

    def by_ids(ids):
        return [dict(by_id[int(i)]) for i in ids if int(i) in by_id]

    def on_date(day, limit=2000):
        return [dict(row) for row in ROWS if row["date"] == day][: int(limit)]

    def nearby(lat, lon, radius_km=None, date_from=None, date_to=None, limit=None):
        center = {"lat": float(lat), "lon": float(lon)}
        found = []
        for row in search(date_from=date_from, date_to=date_to):
            distance = _distance_km(center, row)
            if distance <= float(radius_km):
                item = dict(row)
                item["distance_km"] = round(distance, 3)
                found.append(item)
        found.sort(key=lambda row: (row["distance_km"], row["date"], row["id"]))
        return found[: int(limit or len(found))]

    events.search = search
    events.by_ids = by_ids
    events.on_date = on_date
    events.nearby = nearby
    vector_store.available = lambda: False


def load_cases():
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def result_ids(result):
    if "events" in result:
        return [event["id"] for event in result["events"]]
    if "picks" in result:
        return [pick["event_id"] for pick in result["picks"]]
    return []


def check(case, result):
    expect = case["expect"]
    problems = []
    for key in ("count", "mode", "total_events", "peak_part"):
        if key in expect and result.get(key) != expect[key]:
            problems.append(f"{key}: expected {expect[key]!r}, got {result.get(key)!r}")
    if "ids" in expect and result_ids(result) != expect["ids"]:
        problems.append(f"ids: expected {expect['ids']!r}, got {result_ids(result)!r}")
    if "picks" in expect and result.get("picks") != expect["picks"]:
        problems.append(f"picks: expected {expect['picks']!r}, got {result.get('picks')!r}")
    if "by_category" in expect:
        actual = result.get("by_category") or {}
        for category, count in expect["by_category"].items():
            if actual.get(category) != count:
                problems.append(
                    f"by_category.{category}: expected {count!r}, got {actual.get(category)!r}"
                )
    return problems


def main():
    install_fixture()
    cases = load_cases()
    failures = []
    start = time.perf_counter()

    for case in cases:
        t0 = time.perf_counter()
        try:
            result = tools.dispatch(case["tool"], case.get("args") or {})
            problems = check(case, result)
        except Exception as exc:
            result = None
            problems = [f"raised {type(exc).__name__}: {exc}"]
        latency_ms = (time.perf_counter() - t0) * 1000
        if problems:
            failures.append({"id": case["id"], "problems": problems, "result": result})
            status = "FAIL"
        else:
            status = "ok"
        print(f"{status:4} {case['id']:<18} {case['tool']:<24} {latency_ms:7.1f} ms")

    elapsed_ms = (time.perf_counter() - start) * 1000
    passed = len(cases) - len(failures)
    print()
    print(f"task_success={passed}/{len(cases)} latency_ms={elapsed_ms:.1f} token_cost_usd=0.00")
    if failures:
        print(json.dumps({"failures": failures}, indent=2, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
