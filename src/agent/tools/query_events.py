from pydantic import Field

from agent.config import config
from agent.db.repository import events
from agent.tools.base import DateWindow, tool


class QueryEventsArgs(DateWindow):
    district: str | None = Field(
        default=None, description="Berlin district/borough, e.g. 'Mitte', 'Kreuzberg'."
    )
    category: str | None = Field(
        default=None, description="Event category substring, e.g. 'Demonstration'."
    )
    keyword: str | None = Field(
        default=None, description="Free-text term matched in title and description."
    )
    limit: int | None = Field(
        default=None, ge=1, le=100, description="Max events to return."
    )


@tool(
    "query_events",
    "Find registered Berlin events by date range, district, category, or keyword. "
    "Read-only. Returns a sample of matching events with id, title, date, time, "
    "location and district.",
    QueryEventsArgs,
)
def query_events(args: QueryEventsArgs):
    found = events.search(
        date_from=args.date_from,
        date_to=args.date_to,
        district=args.district,
        category=args.category,
        keyword=args.keyword,
        limit=args.limit,
    )
    return {
        "count": len(found),
        "truncated": len(found) >= config.AGENT_RESULT_LIMIT,
        "events": found,
    }
