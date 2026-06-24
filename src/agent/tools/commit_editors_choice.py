from agent.config import config
from agent.db.repository import events


def commit_editors_choice(event_ids, note=None, selected_by=None):
    written = events.feature(
        event_ids=event_ids,
        note=(note or None),
        selected_by=selected_by or config.EDITOR_NAME,
    )
    return {"committed": written, "event_ids": [int(i) for i in event_ids]}
