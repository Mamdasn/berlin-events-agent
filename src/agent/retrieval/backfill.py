import hashlib
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


def _text_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _load_existing():
    path = config.EMBED_INDEX_PATH
    if not os.path.exists(path):
        return {}
    data = np.load(path)
    ids = data["ids"]
    vectors = data["vectors"]
    hashes = data["hashes"] if "hashes" in data.files else [""] * len(ids)
    return {int(i): (v, str(h)) for i, v, h in zip(ids, vectors, hashes)}


def main():
    _setup_logging()
    corpus = list(events.embedding_corpus())
    total = len(corpus)
    log.info("Corpus has %d unique events", total)
    if not corpus:
        log.warning("No events to embed")
        return

    existing = _load_existing()

    plan = []  # (event_id, hash, reused_vector_or_None)
    pending_texts = []
    for event_id, text in corpus:
        h = _text_hash(text)
        prev = existing.get(event_id)
        if prev is not None and prev[1] == h:
            plan.append((event_id, h, prev[0]))
        else:
            plan.append((event_id, h, None))
            pending_texts.append(text)

    new_count = len(pending_texts)
    ids_changed = {eid for eid, _, _ in plan} != set(existing)
    if new_count == 0 and not ids_changed:
        log.info("Index already current for %d events; nothing to embed", total)
        return

    log.info("Reusing %d, embedding %d new/changed", total - new_count, new_count)

    fresh = []
    if new_count:
        step = max(1, new_count // 20)
        for done, vec in enumerate(embedder.embed_documents_iter(pending_texts), start=1):
            fresh.append(vec)
            if done % step == 0 or done == new_count:
                log.info("Embedded %d/%d new (%d%%)", done, new_count, done * 100 // new_count)

    fresh_iter = iter(fresh)
    ids, vectors, hashes = [], [], []
    for event_id, h, reused in plan:
        ids.append(event_id)
        vectors.append(reused if reused is not None else next(fresh_iter))
        hashes.append(h)

    vector_store.save_index(
        np.asarray(ids, dtype=np.int64),
        np.asarray(vectors, dtype=np.float32),
        hashes=hashes,
    )
    log.info("Wrote %d vectors to %s (%d newly embedded)", total, config.EMBED_INDEX_PATH, new_count)


if __name__ == "__main__":
    main()
