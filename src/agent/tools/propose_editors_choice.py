from pydantic import BaseModel, Field

from agent.config import config
from agent.db.repository import events
from agent.tools.base import ToolError, tool


class EditorsChoicePick(BaseModel):
    event_id: int = Field(description="ID of the event to recommend.")
    reason: str = Field(
        min_length=1,
        max_length=240,
        description="One-line editorial reason for recommending this event.",
    )


class ProposeArgs(BaseModel):
    picks: list[EditorsChoicePick] = Field(
        min_length=1,
        max_length=10,
        description="Events to recommend, each with a short editorial reason.",
    )


@tool(
    "propose_editors_choice",
    "Recommend one or more events as Editor's Choice candidates, with a one-line "
    "editorial reason for each pick. This does not save anything; the human editor "
    "selects the final set and applies it.",
    ProposeArgs,
)
def propose_editors_choice(args: ProposeArgs):
    requested_ids = [pick.event_id for pick in args.picks]
    resolved = events.by_ids(requested_ids)
    found = {e["id"] for e in resolved}
    missing = [i for i in requested_ids if i not in found]
    if missing:
        raise ToolError(f"no such event id(s): {missing}")

    by_id = {event["id"]: event for event in resolved}
    picks = [
        {
            "event_id": pick.event_id,
            "reason": pick.reason.strip()[: config.AGENT_FIELD_MAX_CHARS],
        }
        for pick in args.picks
    ]
    return {
        "picks": picks,
        "events": [by_id[pick["event_id"]] for pick in picks],
    }
