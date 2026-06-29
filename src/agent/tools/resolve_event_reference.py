import re

from pydantic import Field

from agent.config import config
from agent.db.repository import events
from agent.tools.base import DateWindow, tool

_HANDLE_RE = re.compile(
    r"\[\[\s*(\d+)\s*\|[^\]]*\]\]|\{\{\s*event\s*:\s*(\d+)\s*\}\}|"
    r"(?<!\w)(?:event\s+)?id\s*#?\s*(\d+)(?!\w)",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[\wäöüÄÖÜß]+", re.UNICODE)


class ResolveEventReferenceArgs(DateWindow):
    text: str = Field(min_length=1, max_length=2000)
    limit: int | None = Field(default=None, ge=1, le=10)


def _norm(value):
    return _SPACE_RE.sub(" ", str(value or "").casefold()).strip()


def _time_variants(value):
    value = str(value or "").strip()
    if not value:
        return []
    out = [value]
    if len(value) >= 5:
        out.append(value[:5])
    return list(dict.fromkeys(out))


def _within_window(event, date_from, date_to):
    day = event.get("date")
    if date_from and (day is None or day < date_from):
        return False
    if date_to and (day is None or day > date_to):
        return False
    return True


def _events_for_text(text, date_from, date_to):
    if date_from and date_from == date_to:
        return events.on_date(date_from, limit=2000)
    words = [w for w in _WORD_RE.findall(text) if len(w) > 2]
    keyword = max(words, key=len) if words else None
    return events.search(
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        limit=config.AGENT_RESULT_LIMIT,
    )


def _id_matches(text, date_from, date_to):
    ids = []
    for match in _HANDLE_RE.finditer(text):
        raw = next((part for part in match.groups() if part), None)
        if raw is not None:
            ids.append(int(raw))
    if not ids:
        return []
    found = []
    seen = set()
    for event in events.by_ids(ids):
        event_id = int(event.get("id") or event.get("event_id"))
        if event_id in seen or not _within_window(event, date_from, date_to):
            continue
        seen.add(event_id)
        found.append({**event, "match_score": 100, "match_reason": "event id matched"})
    return found


def _score(event, text_norm):
    text_words = set(_WORD_RE.findall(text_norm))
    title = _norm(event.get("title"))
    location = _norm(event.get("location"))
    category = _norm(event.get("category"))
    date = _norm(event.get("date"))
    score = 0
    reasons = []
    if title:
        if text_norm == title:
            score += 120
            reasons.append("exact title")
        elif title in text_norm:
            score += 80
            reasons.append("title")
        else:
            words = [w for w in _WORD_RE.findall(title) if len(w) > 2]
            hits = [w for w in words if w in text_words]
            if words and hits:
                score += min(35, int(35 * len(hits) / len(words)))
                reasons.append("title words")
    if location and location in text_norm:
        score += 25
        reasons.append("location")
    if category and category in text_norm:
        score += 12
        reasons.append("category")
    if date and date in text_norm:
        score += 15
        reasons.append("date")
    for variant in _time_variants(event.get("time")):
        if _norm(variant) in text_norm:
            score += 15
            reasons.append("time")
            break
    return score, ", ".join(dict.fromkeys(reasons)) or "text match"


def _rank_text_matches(text, date_from, date_to):
    text_norm = _norm(text)
    ranked = []
    for event in _events_for_text(text, date_from, date_to):
        if not _within_window(event, date_from, date_to):
            continue
        score, reason = _score(event, text_norm)
        if score <= 0:
            continue
        ranked.append({**event, "match_score": score, "match_reason": reason})
    ranked.sort(
        key=lambda event: (
            event["match_score"],
            str(event.get("date") or ""),
            str(event.get("time") or ""),
            -int(event.get("id") or event.get("event_id") or 0),
        ),
        reverse=True,
    )
    return ranked


def _shape(event):
    event_id = int(event.get("id") or event.get("event_id"))
    return {
        **event,
        "id": event_id,
        "event_id": event_id,
        "match_score": event.get("match_score"),
        "match_reason": event.get("match_reason"),
    }


@tool(
    "resolve_event_reference",
    "Resolve an event id, wildcard, pasted event text, or title fragment to real "
    "Berlin event candidates. Use before nearby_events when the editor says "
    "'near this event' without a concrete id. Read-only.",
    ResolveEventReferenceArgs,
)
def resolve_event_reference(args: ResolveEventReferenceArgs):
    limit = int(args.limit or 5)
    found = _id_matches(args.text, args.date_from, args.date_to)
    if not found:
        found = _rank_text_matches(args.text, args.date_from, args.date_to)
    found = [_shape(event) for event in found[:limit]]
    ambiguous = (
        len(found) > 1
        and (found[0].get("match_score") or 0) - (found[1].get("match_score") or 0) < 20
    )
    return {"count": len(found), "ambiguous": ambiguous, "events": found}
