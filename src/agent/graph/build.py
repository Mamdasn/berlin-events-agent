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
    "When you are ready to answer, mention each relevant event by wrapping its title "
    "in a [[id|Title]] marker and give a short reason for each. Do not end the turn "
    "with only cards or tool output."
)


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
        title = event.get("title") or "Untitled"
        meta = ", ".join(
            str(part)
            for part in (event.get("date"), event.get("time"), event.get("location"))
            if part
        )
        reason = proposed_reasons.get(event_id) or _fallback_reason(event)
        suffix = f" — {meta}" if meta else ""
        lines.append(f"{index}. [[{event_id}|{title}]]{suffix}. {reason}")
    if count > 5:
        lines.append(f"There are {count - 5} more matching cards in the workspace.")
    return "\n".join(lines)


async def _drive(state: AgentState):
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


async def stream_answer(thread_id, message, budget):
    text = guardrails.sanitize_user_message(message)
    if not text:
        yield "error", {"message": "Empty message."}
        return
    history = await run_in_threadpool(memory.load_history, thread_id)
    messages = (
        [{"role": "system", "content": guardrails.system_prompt()}]
        + history
        + [{"role": "user", "content": text}]
    )
    state = AgentState(thread_id, messages, [], budget)
    async for event in _drive(state):
        yield event
