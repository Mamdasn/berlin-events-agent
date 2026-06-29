import json
import re
import secrets

from starlette.concurrency import run_in_threadpool

from agent import memory
from agent.graph import guardrails, nodes
from agent.graph.state import AgentState

_CHUNK = 80

_DSML_RE = re.compile(r"<[｜|]+\s*DSML.*", re.DOTALL)

_BUDGET_DONE = (
    "You have used all available tool calls. Write your final answer for the editor "
    "now, in plain text, using only what you already found. Do not call tools."
)

_TOOL_RESPONSE_NUDGE = (
    "You have just used event tools. Continue the conversation in natural "
    "language, with event handles inside the prose, for example: "
    "'This {{event:id}} is about ... and {{event:id}} is useful because ...'. "
    "If you want to recommend Editor's Choice candidates, call "
    "propose_editors_choice with reasons first. When you are ready to answer, "
    "mention each relevant event with {{event:id}} using the event's actual id, "
    "and give a short reason for each. Do not say you will check more unless you "
    "are actually calling a tool. Do not end the turn with only cards or tool "
    "output."
)

_EVENT_HANDLE_RE = re.compile(r"\{\{\s*event\s*:\s*(\d+)\s*\}\}")
_EVENT_MARKER_RE = re.compile(r"\[\[(\d+)\|([^\]]*)\]\]")
_SAFE_MARKER_DUP_SEP_RE = re.compile(r"^[\s\"'“”‘’()\[\],.:;–—-]{0,32}$")
_PROTECTED_RE = re.compile(r"(\[\[\d+\|[^\]]*\]\]|`[^`]*`|https?://\S+)")
_NO_MATCH_RE = re.compile(
    r"\b(?:no|not)\b.{0,50}\b(?:relevant|matching|matches|events?)\b|"
    r"\b(?:did not|could not|can't|cannot)\b.{0,50}\b(?:find|validate)\b",
    re.IGNORECASE,
)
_WEAK_FINAL_RE = re.compile(
    r"\b(?:let me|I'll|I will)\b.{0,60}\b(?:check|search|look)\b|"
    r"\bfound something\b|"
    r"\bfound (?:several|a few|some) (?:possible )?(?:matches|events)\b|"
    r"\btagged\b.{0,80}\btied to\b",
    re.IGNORECASE,
)
_DESCRIPTION_LIMIT = 360
_EVIDENCE_LIMIT = 8
_FACETS = {
    "middle_east": {
        "label": "Middle East",
        "query_terms": (
            "middle east",
            "nahost",
            "gaza",
            "palestine",
            "palästina",
            "israel",
        ),
        "terms": (
            "middle east",
            "nahost",
            "gaza",
            "palestine",
            "palästina",
            "israel",
        ),
    },
    "colonial": {
        "label": "colonial history",
        "query_terms": (
            "colonized",
            "colonised",
            "colonial",
            "kolonial",
            "decolonial",
            "dekolonial",
        ),
        "terms": (
            "colonized",
            "colonised",
            "colonial",
            "kolonial",
            "decolonial",
            "dekolonial",
            "kolonialbiograf",
        ),
    },
    "exhibition": {
        "label": "exhibition",
        "query_terms": (
            "exhibition",
            "exhibitions",
            "ausstellung",
            "ausstellungen",
            "gallery",
            "galerie",
            "museum",
            "art",
        ),
        "terms": (
            "exhibition",
            "ausstellung",
            "gallery",
            "galerie",
            "museum",
            "kunst",
        ),
    },
}


def _clean(text):
    return _DSML_RE.sub("", text or "").strip()


_DSML_INVOKE = re.compile(
    r"DSML[｜|]+\s*invoke\s+name=\"([^\"]+)\"\s*>(.*?)(?:</[｜|]+\s*DSML[｜|]+\s*invoke>|\Z)",
    re.DOTALL,
)
_DSML_PARAM = re.compile(
    r"DSML[｜|]+\s*parameter\s+name=\"([^\"]+)\"(?:\s+string=\"([^\"]*)\")?\s*>"
    r"(.*?)(?:</[｜|]+\s*DSML[｜|]+\s*parameter>|\Z)",
    re.DOTALL,
)


def _recover_dsml_tool_calls(content):
    calls = []
    for name, body in _DSML_INVOKE.findall(content or ""):
        args = {}
        for pname, is_string, pval in _DSML_PARAM.findall(body):
            pval = pval.strip()
            if is_string == "false":
                try:
                    pval = json.loads(pval)
                except ValueError:
                    pass
            args[pname] = pval
        calls.append(
            {
                "id": "dsml_" + secrets.token_hex(4),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        )
    return calls


def _chunks(text):
    text = text or ""
    for i in range(0, len(text), _CHUNK):
        yield text[i : i + _CHUNK]


def _compact(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text


def _snippet(text, limit=_DESCRIPTION_LIMIT):
    text = _compact(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _history_view(messages):
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
        and m.get("content")
        and not m.get("tool_calls")
    ]


def _extract_facets(text):
    lowered = _compact(text).lower()
    facets = []
    for key, meta in _FACETS.items():
        if any(term in lowered for term in meta["query_terms"]):
            facets.append(key)
    return facets


def _event_facet_hits(event, intent_facets):
    facets = intent_facets or tuple(_FACETS)
    title = _compact(event.get("title")).lower()
    description = _compact(event.get("description")).lower()
    category = _compact(event.get("category")).lower()
    hits = {}
    for facet in facets:
        terms = _FACETS.get(facet, {}).get("terms", ())
        title_hits = [term for term in terms if term in title]
        description_hits = [term for term in terms if term in description]
        category_hits = [term for term in terms if term in category]
        if facet == "exhibition" and "exhibition" in category:
            category_hits.append("exhibition")
        all_hits = sorted(set(title_hits + description_hits + category_hits))
        if all_hits:
            hits[facet] = {
                "terms": all_hits[:5],
                "title": bool(title_hits),
                "description": bool(description_hits),
                "category": bool(category_hits),
            }
    return hits


def _source_query(name, args):
    args = args or {}
    if name == "query_events":
        parts = [args.get(k) for k in ("keyword", "category", "district") if args.get(k)]
        return ", ".join(str(part) for part in parts)
    if name == "semantic_search":
        return args.get("query")
    if name == "resolve_event_reference":
        return args.get("text")
    if name == "nearby_events":
        return "nearby search"
    if name == "day_analysis":
        return args.get("date")
    return None


def _event_score(event, intent_facets):
    hits = _event_facet_hits(event, intent_facets)
    score = 0
    for hit in hits.values():
        if hit["title"]:
            score += 4
        if hit["description"]:
            score += 3
        if hit["category"]:
            score += 2
    try:
        semantic_score = float(event.get("score"))
    except (TypeError, ValueError):
        semantic_score = 0
    if semantic_score:
        score += min(2, max(0, semantic_score))
    if event.get("reason"):
        score += 3
    return score


def _annotate_event(event, intent_facets):
    hits = _event_facet_hits(event, intent_facets)
    if hits:
        event["matched_facets"] = [_FACETS[k]["label"] for k in hits]
        event["match_terms"] = {
            _FACETS[k]["label"]: v["terms"] for k, v in hits.items()
        }
    event["rank_score"] = _event_score(event, intent_facets)
    return event


def _event_payload(event, source_tool=None, source_args=None, intent_facets=None):
    event_id = event.get("id") or event.get("event_id")
    payload = {
        "id": event_id,
        "event_id": event_id,
        "title": event.get("title") or event.get("thema") or "Untitled",
        "date": event.get("date"),
        "time": event.get("time") or event.get("von"),
        "location": event.get("location") or event.get("versammlungsort"),
        "district": event.get("district"),
        "category": event.get("category"),
    }
    description = _snippet(event.get("description") or event.get("body"))
    if description:
        payload["description"] = description
    for key in ("score", "distance_km", "match_score", "match_reason"):
        if event.get(key) is not None:
            payload[key] = event.get(key)
    if source_tool:
        payload["source_tool"] = source_tool
    query = _source_query(source_tool, source_args)
    if query:
        payload["source_query"] = query
    return _annotate_event(payload, intent_facets or ())


def _new_events(events, seen_event_ids, surfaced_events, source_tool=None,
                source_args=None, intent_facets=None):
    surfaced = []
    for event in events or []:
        event_id = event.get("id") or event.get("event_id")
        if event_id is None:
            continue
        event_id = int(event_id)
        payload = _event_payload(
            {**event, "id": event_id},
            source_tool,
            source_args,
            intent_facets,
        )
        surfaced_events[event_id] = {**surfaced_events.get(event_id, {}), **payload}
        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        surfaced.append(payload)
    return surfaced


def _marker_title(title, event_id):
    clean = re.sub(r"[\|\[\]\r\n]+", " ", title or "").strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean or f"Event {event_id}"


def _marker(event):
    event_id = int(event["event_id"])
    return f"[[{event_id}|{_marker_title(event.get('title'), event_id)}]]"


def _protect_segments(text):
    parts = []
    pos = 0
    for match in _PROTECTED_RE.finditer(text):
        if match.start() > pos:
            parts.append((False, text[pos : match.start()]))
        parts.append((True, match.group(0)))
        pos = match.end()
    if pos < len(text):
        parts.append((False, text[pos:]))
    return parts


def _replace_event_handles(text, surfaced_events):
    def replace(match):
        event_id = int(match.group(1))
        event = surfaced_events.get(event_id)
        return _marker(event) if event else f"event {event_id}"

    return _EVENT_HANDLE_RE.sub(replace, text)


def _normalize_markers(text, surfaced_events):
    def replace(match):
        event_id = int(match.group(1))
        event = surfaced_events.get(event_id)
        return _marker(event) if event else f"event {event_id}"

    return _EVENT_MARKER_RE.sub(replace, text)


def _replace_known_ids(text, surfaced_events):
    for event_id in sorted(surfaced_events):
        pattern = re.compile(
            rf"(?<!\w)(?:event\s+)?id\s*#?\s*{event_id}(?!\w)",
            re.IGNORECASE,
        )
        text = pattern.sub(_marker(surfaced_events[event_id]), text)
    return text


def _replace_known_titles(text, surfaced_events):
    by_title = {}
    for event in surfaced_events.values():
        title = _marker_title(event.get("title"), event["event_id"])
        by_title.setdefault(title, []).append(event)

    for title in sorted(by_title, key=len, reverse=True):
        events = by_title[title]
        if len(events) != 1:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(title)}(?!\w)")
        text = pattern.sub(_marker(events[0]), text)
    return text


def _skip_duplicate_closer(text, pos, between):
    if pos >= len(text):
        return pos
    closer = text[pos]
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’", "(": ")", "[": "]"}
    if any(pairs.get(ch) == closer for ch in between):
        return pos + 1
    return pos


def _dedupe_adjacent_event_markers(text):
    matches = list(_EVENT_MARKER_RE.finditer(text or ""))
    if len(matches) < 2:
        return text

    out = []
    pos = 0
    last_id = None
    for match in matches:
        event_id = int(match.group(1))
        between = text[pos : match.start()]
        if (
            last_id == event_id
            and _SAFE_MARKER_DUP_SEP_RE.fullmatch(between)
        ):
            pos = _skip_duplicate_closer(text, match.end(), between)
            continue
        out.append(text[pos : match.end()])
        pos = match.end()
        last_id = event_id
    out.append(text[pos:])
    return "".join(out)


def _finalize_event_mentions(text, surfaced_events):
    if not text:
        return text

    surfaced_events = surfaced_events or {}
    text = _normalize_markers(
        _replace_event_handles(text, surfaced_events), surfaced_events
    )
    finalized = []
    for protected, segment in _protect_segments(text):
        if protected:
            finalized.append(segment)
            continue
        segment = _replace_known_titles(segment, surfaced_events)
        id_finalized = []
        for id_protected, id_segment in _protect_segments(segment):
            if id_protected:
                id_finalized.append(id_segment)
            else:
                id_finalized.append(_replace_known_ids(id_segment, surfaced_events))
        finalized.append("".join(id_finalized))
    return _dedupe_adjacent_event_markers("".join(finalized))


def _has_event_marker(text):
    return bool(_EVENT_MARKER_RE.search(text or ""))


def _says_no_match(text):
    return bool(_NO_MATCH_RE.search(text or ""))


def _weak_final_answer(text):
    return bool(_WEAK_FINAL_RE.search(text or ""))


def _rank_events(surfaced_events, intent_facets, proposed_reasons=None):
    proposed_reasons = proposed_reasons or {}
    ranked = []
    for event in surfaced_events.values():
        event = _annotate_event(dict(event), intent_facets)
        if int(event["event_id"]) in proposed_reasons:
            event["reason"] = proposed_reasons[int(event["event_id"])]
            event["rank_score"] += 3
        ranked.append(event)
    ranked.sort(
        key=lambda event: (
            event.get("rank_score") or 0,
            bool(event.get("description")),
            str(event.get("date") or ""),
            str(event.get("time") or ""),
        ),
        reverse=True,
    )
    return ranked


def _term_phrase(terms):
    terms = [str(term) for term in terms or []]
    if not terms:
        return "that theme"
    if len(terms) == 1:
        return terms[0]
    return ", ".join(terms[:-1]) + " or " + terms[-1]


def _fallback_reason(event, intent_facets=None):
    hits = _event_facet_hits(event, intent_facets)
    if "middle_east" in hits:
        terms = _term_phrase(hits["middle_east"]["terms"])
        return (
            "This is a strong Middle East match because the available event text "
            f"explicitly refers to {terms}."
        )
    if "colonial" in hits:
        terms = _term_phrase(hits["colonial"]["terms"])
        return (
            "This is a strong colonial-history match because the available event "
            f"text explicitly refers to {terms}."
        )
    if "exhibition" in hits:
        has_other_intent = any(facet in hits for facet in ("middle_east", "colonial"))
        if has_other_intent:
            return "This is relevant as an exhibition with a topical signal in the event text."
        return (
            "This is a looser exhibition match: the record identifies it as an "
            "exhibition, but the available text does not show a Middle East or "
            "colonial angle."
        )
    description = event.get("description")
    if description:
        return f"The available description gives some context: {_snippet(description, 180)}"
    category = event.get("category")
    location = event.get("location")
    date = event.get("date")
    if category and location:
        return (
            f"This is a lower-confidence match based on its {category} category "
            f"and location at {location}."
        )
    if category:
        return f"This is a lower-confidence match based on its {category} category."
    if location or date:
        bits = " on ".join(part for part in (location, date) if part)
        return f"This matched the search, but the record only gives limited context ({bits})."
    return "This matched the search, but the record has limited context."


def _fallback_answer(surfaced_events, proposed_reasons, intent_facets=None):
    events = _rank_events(surfaced_events, intent_facets or (), proposed_reasons)
    if not events:
        return (
            "I checked the event tools, but I do not have enough event context to give "
            "a useful answer yet."
        )

    count = len(events)
    header = "I found some possible matches, with different confidence levels."
    lines = [header]
    for event in events[:5]:
        event_id = int(event["event_id"])
        meta = ", ".join(
            str(part)
            for part in (event.get("date"), event.get("time"), event.get("location"))
            if part
        )
        reason = proposed_reasons.get(event_id) or _fallback_reason(event, intent_facets)
        prefix = f"{{{{event:{event_id}}}}}"
        context = f" ({meta})" if meta else ""
        lines.append(f"{prefix}{context}. {reason}")
    if count > 5:
        lines.append(f"I found {count - 5} more lower-priority matches as well.")
    return " ".join(lines)


def _evidence_items(surfaced_events, proposed_reasons, intent_facets):
    items = []
    for event in _rank_events(surfaced_events, intent_facets, proposed_reasons)[
        :_EVIDENCE_LIMIT
    ]:
        item = {
            "id": event.get("event_id"),
            "title": event.get("title"),
            "date": event.get("date"),
            "time": event.get("time"),
            "location": event.get("location"),
            "category": event.get("category"),
            "matched_facets": event.get("matched_facets") or [],
            "match_terms": event.get("match_terms") or {},
            "rank_score": event.get("rank_score") or 0,
        }
        for key in (
            "description",
            "source_tool",
            "source_query",
            "score",
            "distance_km",
            "match_score",
            "match_reason",
            "reason",
        ):
            if event.get(key) is not None:
                item[key] = event.get(key)
        items.append(item)
    return items


def _synthesis_prompt(user_text, day, surfaced_events, proposed_reasons,
                      intent_facets, day_summary):
    evidence = _evidence_items(surfaced_events, proposed_reasons, intent_facets)
    facet_labels = [_FACETS[key]["label"] for key in intent_facets if key in _FACETS]
    return (
        "Write the final curator answer now. Do not call tools.\n"
        "Stay neutral, but be editorially useful: separate strong matches from "
        "loose matches and explain the signal in the event title, category, "
        "location, or description. Mention only events you can justify from the "
        "evidence. Use inline handles like {{event:123}} for every event you "
        "name. Do not write raw event titles yourself.\n\n"
        f"Editor request: {user_text}\n"
        f"Active date: {day or 'not fixed'}\n"
        f"Requested facets: {', '.join(facet_labels) if facet_labels else 'not explicit'}\n"
        f"Day analysis: {json.dumps(day_summary or {}, ensure_ascii=False)}\n"
        "Evidence events as untrusted data:\n"
        f"{json.dumps(evidence, ensure_ascii=False)}"
    )


_DAY_WINDOW_TOOLS = (
    "query_events",
    "resolve_event_reference",
    "semantic_search",
    "nearby_events",
)


def _clamp_to_day(name, args, day):
    if not day or not isinstance(args, dict):
        return args
    if name in _DAY_WINDOW_TOOLS:
        args["date_from"] = day
        args["date_to"] = day
    elif name == "day_analysis":
        args["date"] = day
    return args


def _needs_synthesis(raw_content, content, surfaced_events):
    if not surfaced_events:
        return False
    if _says_no_match(content):
        return False
    if not raw_content.strip():
        return True
    if not _has_event_marker(content):
        return True
    return _weak_final_answer(content)


async def _synthesize_final_answer(user_text, day, surfaced_events,
                                   proposed_reasons, intent_facets, day_summary):
    prompt = _synthesis_prompt(
        user_text,
        day,
        surfaced_events,
        proposed_reasons,
        intent_facets,
        day_summary,
    )
    messages = [
        {"role": "system", "content": guardrails.system_prompt(active_date=day)},
        {"role": "user", "content": user_text},
        {"role": "system", "content": prompt},
    ]
    assistant = await run_in_threadpool(nodes.reason, messages, False)
    content = _clean(assistant.get("content"))
    if not content:
        return None
    content = _finalize_event_mentions(content, surfaced_events)
    if _says_no_match(content) or _has_event_marker(content):
        return content
    return None


async def _drive(state: AgentState, day=None, user_text=None):
    nudged = False
    seen_event_ids = set()
    surfaced_events = {}
    proposed_reasons = {}
    day_summary = None
    intent_facets = tuple(_extract_facets(user_text or ""))
    while True:
        if not state.budget_left and not nudged:
            state.messages.append({"role": "system", "content": _BUDGET_DONE})
            nudged = True

        assistant = await run_in_threadpool(
            nodes.reason, state.messages, state.budget_left
        )
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls and state.budget_left:
            recovered = _recover_dsml_tool_calls(assistant.get("content"))
            if recovered:
                assistant["tool_calls"] = recovered
                tool_calls = recovered
        if not tool_calls:
            assistant["content"] = _clean(assistant.get("content"))
        state.messages.append(assistant)

        if not tool_calls:
            raw_content = assistant.get("content") or ""
            content = raw_content
            if not content:
                content = _fallback_answer(
                    surfaced_events, proposed_reasons, intent_facets
                )
            content = _finalize_event_mentions(content, surfaced_events)
            if _needs_synthesis(raw_content, content, surfaced_events):
                synthesized = await _synthesize_final_answer(
                    user_text or "",
                    day,
                    surfaced_events,
                    proposed_reasons,
                    intent_facets,
                    day_summary,
                )
                if synthesized:
                    content = synthesized
            attempted_handle = bool(
                _EVENT_HANDLE_RE.search(raw_content)
                or _EVENT_MARKER_RE.search(raw_content)
            )
            if (
                surfaced_events
                and not _has_event_marker(content)
                and not attempted_handle
                and not _says_no_match(content)
            ):
                content = _finalize_event_mentions(
                    _fallback_answer(surfaced_events, proposed_reasons, intent_facets),
                    surfaced_events,
                )
            assistant["content"] = content
            for chunk in _chunks(content):
                yield "token", {"text": chunk}
            memory.save_history(state.thread_id, _history_view(state.messages))
            return

        had_tool_result = False
        for tc in tool_calls:
            name, args, parse_err = nodes.parse_tool_call(tc)
            if parse_err:
                state.messages.append(nodes.tool_message(tc["id"], {"error": parse_err}))
                continue

            args = _clamp_to_day(name, args, day)
            result, tool_err = await run_in_threadpool(nodes.run_tool, name, args)
            state.used_tool(name)

            payload = {"error": tool_err} if tool_err else result
            state.messages.append(nodes.tool_message(tc["id"], payload))

            if not tool_err:
                had_tool_result = True
                surfaced = _new_events(
                    result.get("events"),
                    seen_event_ids,
                    surfaced_events,
                    name,
                    args,
                    intent_facets,
                )
                if surfaced:
                    yield "event_refs", {"events": surfaced}
                if name == "day_analysis":
                    day_summary = result
                if name == "propose_editors_choice":
                    for pick in result["picks"]:
                        proposed_reasons[int(pick["event_id"])] = pick["reason"]
                    proposed = []
                    for event in result.get("events") or []:
                        payload = _event_payload(event, name, args, intent_facets)
                        event_id = int(payload["event_id"])
                        reason = proposed_reasons.get(event_id)
                        if reason:
                            payload["reason"] = reason
                            surfaced_events[event_id] = {
                                **surfaced_events.get(event_id, {}),
                                **payload,
                            }
                        proposed.append(payload)
                    yield "propose", {"picks": result["picks"], "events": proposed}

            yield "status", {"tools_used": state.tools_used}

        if had_tool_result and state.budget_left:
            state.messages.append({"role": "system", "content": _TOOL_RESPONSE_NUDGE})


async def stream_answer(thread_id, message, budget, date=None):
    text = guardrails.sanitize_user_message(message)
    if not text:
        yield "error", {"message": "Empty message."}
        return
    history = await run_in_threadpool(memory.load_history, thread_id)
    messages = (
        [{"role": "system", "content": guardrails.system_prompt(active_date=date)}]
        + history
        + [{"role": "user", "content": text}]
    )
    state = AgentState(thread_id, messages, [], budget)
    async for event in _drive(state, date, text):
        yield event
