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
    "You have just used event tools and may have surfaced event cards in the UI. "
    "Continue the conversation in natural language. If you want to recommend "
    "Editor's Choice candidates, call propose_editors_choice with reasons first. "
    "When you are ready to answer, mention each relevant event with {{event:id}} "
    "using the event's actual id, and give a short reason for each. Do not end "
    "the turn with only cards or tool output."
)

_EVENT_HANDLE_RE = re.compile(r"\{\{\s*event\s*:\s*(\d+)\s*\}\}")
_EVENT_MARKER_RE = re.compile(r"\[\[(\d+)\|([^\]]*)\]\]")
_PROTECTED_RE = re.compile(r"(\[\[\d+\|[^\]]*\]\]|`[^`]*`|https?://\S+)")


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


def _history_view(messages):
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
        and m.get("content")
        and not m.get("tool_calls")
    ]


def _event_payload(event):
    event_id = event.get("id") or event.get("event_id")
    return {
        "id": event_id,
        "event_id": event_id,
        "title": event.get("title") or event.get("thema") or "Untitled",
        "date": event.get("date"),
        "time": event.get("time") or event.get("von"),
        "location": event.get("location") or event.get("versammlungsort"),
        "district": event.get("district"),
        "category": event.get("category"),
    }


def _new_events(events, seen_event_ids, surfaced_events):
    surfaced = []
    for event in events or []:
        event_id = event.get("id") or event.get("event_id")
        if event_id is None:
            continue
        event_id = int(event_id)
        payload = _event_payload({**event, "id": event_id})
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
    return "".join(finalized)


def _fallback_reason(event):
    category = event.get("category")
    location = event.get("location")
    date = event.get("date")
    if category and location:
        return f"It fits because it is tagged {category} and is tied to {location}."
    if category:
        return f"It fits because it is tagged {category} in the event data."
    if location or date:
        bits = " on ".join(part for part in (location, date) if part)
        return f"It matched the tool search and has concrete event context ({bits})."
    return "It matched the event search, but the record has limited context."


def _fallback_answer(surfaced_events, proposed_reasons):
    events = list(surfaced_events.values())
    if not events:
        return (
            "I checked the event tools, but I do not have enough event context to give "
            "a useful answer yet."
        )

    count = len(events)
    header = (
        f"I found {count} matching event card{'s' if count != 1 else ''}. "
        "Here is the context I can give from the event data:"
    )
    lines = [header]
    for index, event in enumerate(events[:5], start=1):
        event_id = int(event["event_id"])
        meta = ", ".join(
            str(part)
            for part in (event.get("date"), event.get("time"), event.get("location"))
            if part
        )
        reason = proposed_reasons.get(event_id) or _fallback_reason(event)
        suffix = f" — {meta}" if meta else ""
        lines.append(f"{index}. {{{{event:{event_id}}}}}{suffix}. {reason}")
    if count > 5:
        lines.append(f"There are {count - 5} more matching cards in the workspace.")
    return "\n".join(lines)


_DAY_WINDOW_TOOLS = ("query_events", "semantic_search", "nearby_events")


def _clamp_to_day(name, args, day):
    if not day or not isinstance(args, dict):
        return args
    if name in _DAY_WINDOW_TOOLS:
        args["date_from"] = day
        args["date_to"] = day
    elif name == "day_analysis":
        args["date"] = day
    return args


async def _drive(state: AgentState, day=None):
    nudged = False
    seen_event_ids = set()
    surfaced_events = {}
    proposed_reasons = {}
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
            content = assistant.get("content") or ""
            if not content:
                content = _fallback_answer(surfaced_events, proposed_reasons)
            content = _finalize_event_mentions(content, surfaced_events)
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
                    result.get("events"), seen_event_ids, surfaced_events
                )
                if surfaced:
                    yield "events", {"events": surfaced}
                if name == "propose_editors_choice":
                    for pick in result["picks"]:
                        proposed_reasons[int(pick["event_id"])] = pick["reason"]
                    yield "propose", {"picks": result["picks"]}

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
    async for event in _drive(state, date):
        yield event
