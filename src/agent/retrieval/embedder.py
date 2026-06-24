from functools import lru_cache

import numpy as np

from agent.config import config


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(config.EMBED_MODEL, cache_dir="/app/data/fastembed")


def embed_documents(texts):
    vecs = list(_model().embed(list(texts), batch_size=64))
    return np.asarray(vecs, dtype=np.float32)


def embed_query(text):
    vec = next(iter(_model().query_embed([text or ""])))
    return np.asarray(vec, dtype=np.float32)
