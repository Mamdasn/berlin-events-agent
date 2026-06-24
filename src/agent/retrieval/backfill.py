import logging

import numpy as np

from agent.config import config
from agent.db.repository import events
from agent.retrieval import embedder, vector_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill")


def main():
    ids, texts = [], []
    for event_id, text in events.embedding_corpus():
        ids.append(event_id)
        texts.append(text)
    log.info("Embedding %d unique events with %s", len(ids), config.EMBED_MODEL)
    if not ids:
        log.warning("No events to embed")
        return
    vectors = embedder.embed_documents(texts)
    vector_store.save_index(np.asarray(ids), vectors)
    log.info("Wrote %d vectors to %s", len(ids), config.EMBED_INDEX_PATH)


if __name__ == "__main__":
    main()
