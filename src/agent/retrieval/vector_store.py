import logging
import os
from threading import Lock

import numpy as np

from agent.config import config

log = logging.getLogger(__name__)

_lock = Lock()
_index = None


def _load():
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is not None:
            return _index
        path = config.EMBED_INDEX_PATH
        if not os.path.exists(path):
            log.warning("Embedding index missing at %s; semantic search disabled", path)
            _index = (np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32))
            return _index
        data = np.load(path)
        _index = (data["ids"].astype(np.int64), data["vectors"].astype(np.float32))
        log.info("Loaded %d embeddings from %s", len(_index[0]), path)
    return _index


def available():
    ids, _ = _load()
    return len(ids) > 0


def save_index(ids, vectors, path=None):
    path = path or config.EMBED_INDEX_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    np.savez(
        path,
        ids=np.asarray(ids, dtype=np.int64),
        vectors=np.asarray(vectors, dtype=np.float32),
    )
    global _index
    _index = None  # force reload next query


def search(query_vector, top_k):
    ids, matrix = _load()
    if len(ids) == 0:
        return []
    scores = matrix @ np.asarray(query_vector, dtype=np.float32)
    k = min(int(top_k), len(ids))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return [(int(ids[i]), float(scores[i])) for i in top]
