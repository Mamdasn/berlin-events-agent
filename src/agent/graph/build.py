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


def _new_events(events, seen_event_ids):
    surfaced = []
    for event in events or []:
        event_id = event.get("id") or event.get("event_id")
        if event_id is None:
            continue
        event_id = int(event_id)
        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        surfaced.append(_event_payload({**event, "id": event_id}))
    return surfaced


async def _drive(state: AgentState):
    nudged = False
    seen_event_ids = set()
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
            for chunk in _chunks(assistant.get("content")):
                yield "token", {"text": chunk}
            memory.save_history(state.thread_id, _history_view(state.messages))
            return

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
                surfaced = _new_events(result.get("events"), seen_event_ids)
                if surfaced:
                    yield "events", {"events": surfaced}
                if name == "propose_editors_choice":
                    yield "propose", {"picks": result["picks"]}

            yield "status", {"tools_used": state.tools_used}


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
