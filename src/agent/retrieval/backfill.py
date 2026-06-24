import logging
import os

import numpy as np

from agent.config import config
from agent.db.repository import events
from agent.retrieval import embedder, vector_store

LOG_PATH = os.path.join(os.path.dirname(config.EMBED_INDEX_PATH) or ".", "backfill.log")

log = logging.getLogger("backfill")


def _setup_logging():
    logging.basicConfig(level=logging.INFO)
    try:
        handler = logging.FileHandler(LOG_PATH, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError:
        log.warning("Could not open %s for logging", LOG_PATH)


def main():
    _setup_logging()
    ids, texts = [], []
    for event_id, text in events.embedding_corpus():
        ids.append(event_id)
        texts.append(text)
    total = len(ids)
    log.info("Embedding %d unique events with %s", total, config.EMBED_MODEL)
    if not ids:
        log.warning("No events to embed")
        return
    step = max(1, total // 20)
    vectors = []
    for done, vec in enumerate(embedder.embed_documents_iter(texts), start=1):
        vectors.append(vec)
        if done % step == 0 or done == total:
            log.info("Embedded %d/%d (%d%%)", done, total, done * 100 // total)
    vector_store.save_index(np.asarray(ids), np.asarray(vectors, dtype=np.float32))
    log.info("Wrote %d vectors to %s", total, config.EMBED_INDEX_PATH)


if __name__ == "__main__":
    main()
