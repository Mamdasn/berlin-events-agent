import json
import time

from agent.config import config
from agent.db.client import db

_HISTORY_PREFIX = "ec:history:"

_mem = {}


def _redis():
    return db.redis(config.AGENT_REDIS_DB_MEMORY)


def _get(key):
    r = _redis()
    if r is not None:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    entry = _mem.get(key)
    if not entry:
        return None
    value, expires = entry
    if expires and expires < time.time():
        _mem.pop(key, None)
        return None
    return value


def _set(key, value, ttl):
    r = _redis()
    if r is not None:
        r.setex(key, ttl, json.dumps(value))
    else:
        _mem[key] = (value, time.time() + ttl)


def load_history(thread_id):
    return _get(_HISTORY_PREFIX + thread_id) or []


def save_history(thread_id, messages):
    trimmed = messages[-(config.MEMORY_MAX_TURNS * 2):]
    _set(_HISTORY_PREFIX + thread_id, trimmed, config.MEMORY_TTL_SECONDS)
