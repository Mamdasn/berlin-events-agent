from functools import lru_cache

import numpy as np

from agent.config import config


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(config.EMBED_MODEL, cache_dir="/app/data/fastembed")


def embed_documents_iter(texts):
    for vec in _model().embed(list(texts), batch_size=64):
        yield np.asarray(vec, dtype=np.float32)


def embed_documents(texts):
    return np.asarray(list(embed_documents_iter(texts)), dtype=np.float32)


def embed_query(text):
    vec = next(iter(_model().query_embed([text or ""])))
    return np.asarray(vec, dtype=np.float32)
