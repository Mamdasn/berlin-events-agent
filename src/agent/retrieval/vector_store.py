import logging
import os
import tempfile
from threading import Lock

import numpy as np

from agent.config import config

log = logging.getLogger(__name__)

_lock = Lock()
_index = None
_index_mtime = None


def _load():
    global _index, _index_mtime
    path = config.EMBED_INDEX_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        log.warning("Embedding index missing at %s; semantic search disabled", path)
        return (np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32))
    if _index is not None and mtime == _index_mtime:
        return _index
    with _lock:
        if _index is not None and mtime == _index_mtime:
            return _index
        data = np.load(path)
        _index = (data["ids"].astype(np.int64), data["vectors"].astype(np.float32))
        _index_mtime = mtime
        log.info("Loaded %d embeddings from %s", len(_index[0]), path)
    return _index


def available():
    ids, _ = _load()
    return len(ids) > 0


def save_index(ids, vectors, hashes=None, path=None):
    path = path or config.EMBED_INDEX_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    arrays = {
        "ids": np.asarray(ids, dtype=np.int64),
        "vectors": np.asarray(vectors, dtype=np.float32),
    }
    if hashes is not None:
        arrays["hashes"] = np.asarray(hashes, dtype="U40")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".npz")
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez(f, **arrays)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
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
