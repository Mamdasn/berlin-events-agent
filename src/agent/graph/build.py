import secrets

from starlette.concurrency import run_in_threadpool

from agent import memory
from agent.config import config
from agent.graph import guardrails, nodes
from agent.graph.state import AgentState
from agent.tools.commit_editors_choice import commit_editors_choice

_CHUNK = 80


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


async def _drive(state: AgentState):
    while True:
        assistant = await run_in_threadpool(
            nodes.reason, state.messages, state.budget_left
        )
        tool_calls = assistant.get("tool_calls") or []
        state.messages.append(assistant)

        if not tool_calls:
            for chunk in _chunks(assistant.get("content")):
                yield "token", {"text": chunk}
            memory.save_history(state.thread_id, _history_view(state.messages))
            return

        pending = None
        for tc in tool_calls:
            name, args, parse_err = nodes.parse_tool_call(tc)
            if parse_err:
                state.messages.append(nodes.tool_message(tc["id"], {"error": parse_err}))
                continue

            result, tool_err = await run_in_threadpool(nodes.run_tool, name, args)
            state.used_tool(name)

            if name == "propose_editors_choice" and not tool_err:
                state.messages.append(
                    nodes.tool_message(
                        tc["id"],
                        {
                            "pending_approval": True,
                            "event_ids": result["event_ids"],
                            "note": result["note"],
                        },
                    )
                )
                pending = result
            else:
                payload = {"error": tool_err} if tool_err else result
                state.messages.append(nodes.tool_message(tc["id"], payload))

            yield "status", {"tools_used": state.tools_used}

        if pending is not None:
            proposal_id = secrets.token_urlsafe(12)
            memory.stage_proposal(
                state.thread_id,
                proposal_id,
                {
                    "messages": state.messages,
                    "event_ids": pending["event_ids"],
                    "note": pending["note"],
                    "tools_used": state.tools_used,
                },
            )
            yield "proposal", {
                "proposal_id": proposal_id,
                "events": pending["events"],
                "note": pending["note"],
            }
            return


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


async def stream_resume(thread_id, proposal_id, decision, note):
    pending = await run_in_threadpool(memory.take_proposal, thread_id, proposal_id)
    if not pending:
        yield "error", {"message": "This proposal expired or was already handled."}
        return

    event_ids = pending["event_ids"]
    note = (note or "").strip() or pending.get("note")

    if decision == "approve":
        await run_in_threadpool(
            commit_editors_choice, event_ids, note, config.EDITOR_NAME
        )
        decision_text = (
            "EDITOR DECISION: APPROVED. The proposed events were saved as Editor's "
            "Choice and now show on the public map. Confirm this briefly to the editor."
        )
    else:
        decision_text = (
            "EDITOR DECISION: REJECTED. Do not feature these events. Acknowledge "
            "briefly and offer to keep looking."
        )
    if note:
        decision_text += f" Editor note: {note}"

    messages = pending["messages"] + [{"role": "user", "content": decision_text}]
    state = AgentState(
        thread_id, messages, pending.get("tools_used", []), config.AGENT_MAX_TOOL_CALLS
    )
    async for event in _drive(state):
        yield event
