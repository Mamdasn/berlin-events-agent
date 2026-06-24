from functools import lru_cache

import numpy as np

from agent.config import config

_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(config.EMBED_MODEL, device="cpu")


def embed_documents(texts):
    vecs = _model().encode(
        list(texts),
        normalize_embeddings=True,
        batch_size=64,
        show_progress_bar=False,
    )
    return np.asarray(vecs, dtype=np.float32)


def embed_query(text):
    vec = _model().encode(
        _QUERY_PREFIX + (text or ""),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vec, dtype=np.float32)
