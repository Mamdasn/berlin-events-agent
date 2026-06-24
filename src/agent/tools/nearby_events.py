from pydantic import Field, model_validator

from agent.config import config
from agent.db.repository import events
from agent.tools.base import DateWindow, ToolError, tool


class NearbyEventsArgs(DateWindow):
    event_id: int | None = Field(
        default=None, description="Center the search on this event's location."
    )
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(
        default=None, gt=0, le=25, description="Search radius in kilometers."
    )
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _need_center(self):
        if self.event_id is None and (self.lat is None or self.lon is None):
            raise ValueError("provide either event_id or both lat and lon")
        return self


@tool(
    "nearby_events",
    "Find events near a point or near another event, sorted by distance. Give either "
    "event_id, or lat+lon. Read-only.",
    NearbyEventsArgs,
)
def nearby_events(args: NearbyEventsArgs):
    lat, lon = args.lat, args.lon
    if args.event_id is not None:
        center = events.by_ids([args.event_id])
        if not center or center[0]["lat"] is None:
            raise ToolError(f"event {args.event_id} not found or has no location")
        lat, lon = center[0]["lat"], center[0]["lon"]

    found = events.nearby(
        lat=lat,
        lon=lon,
        radius_km=args.radius_km or config.NEARBY_RADIUS_KM,
        date_from=args.date_from,
        date_to=args.date_to,
        limit=args.limit,
    )
    found = [e for e in found if e["id"] != args.event_id]
    return {"center": {"lat": lat, "lon": lon}, "count": len(found), "events": found}
