from pydantic import Field

from agent.config import config
from agent.db.repository import events
from agent.retrieval import embedder, vector_store
from agent.tools.base import DateWindow, tool


class SemanticSearchArgs(DateWindow):
    query: str = Field(
        description="Natural-language description of the kind of event to find, "
        "e.g. 'unusual cultural happening' or 'climate protest'."
    )
    top_k: int | None = Field(
        default=None, ge=1, le=50, description="How many best matches to return."
    )


def _within_window(event, date_from, date_to):
    d = event.get("date")
    if date_from and (d is None or d < date_from):
        return False
    if date_to and (d is None or d > date_to):
        return False
    return True


@tool(
    "semantic_search",
    "Semantically search events by meaning rather than exact keywords, using local "
    "embeddings. Best for vague/conceptual asks like 'most unusual event'. Falls back "
    "to keyword search if the embedding index is unavailable. Read-only.",
    SemanticSearchArgs,
)
def semantic_search(args: SemanticSearchArgs):
    top_k = int(args.top_k or config.SEMANTIC_TOP_K)

    if not vector_store.available():
        found = events.search(
            keyword=args.query,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=top_k,
        )
        return {"mode": "keyword_fallback", "count": len(found), "events": found}

    hits = vector_store.search(embedder.embed_query(args.query), top_k * 4)
    scores = {eid: score for eid, score in hits}
    found = events.by_ids(list(scores))
    for event in found:
        event["score"] = round(scores.get(event["id"], 0.0), 4)
    found = [e for e in found if _within_window(e, args.date_from, args.date_to)]
    found.sort(key=lambda e: e["score"], reverse=True)
    return {"mode": "semantic", "count": len(found[:top_k]), "events": found[:top_k]}
