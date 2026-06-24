from pydantic import BaseModel, Field

from agent.config import config
from agent.db.repository import events
from agent.tools.base import ToolError, tool


class ProposeArgs(BaseModel):
    event_ids: list[int] = Field(
        min_length=1,
        max_length=10,
        description="IDs of events to feature as Editor's Choice.",
    )
    note: str | None = Field(
        default=None,
        description="Short reason these events are worth featuring.",
    )


@tool(
    "propose_editors_choice",
    "Propose one or more events to be featured as Editor's Choice on the public map. "
    "This does NOT save anything — it asks the human editor to approve first. Call it "
    "once you have identified specific events the editor asked to feature.",
    ProposeArgs,
)
def propose_editors_choice(args: ProposeArgs):
    resolved = events.by_ids(args.event_ids)
    found = {e["id"] for e in resolved}
    missing = [i for i in args.event_ids if i not in found]
    if missing:
        raise ToolError(f"no such event id(s): {missing}")

    note = (args.note or "").strip()[: config.AGENT_FIELD_MAX_CHARS]
    return {
        "pending_approval": True,
        "event_ids": [e["id"] for e in resolved],
        "note": note,
        "events": resolved,
    }
