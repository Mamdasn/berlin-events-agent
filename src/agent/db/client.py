from functools import lru_cache
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
import redis

from agent.config import config


class Database:
    def __init__(self, cfg=config):
        self.config = cfg

    def connect(self, write=False):
        url = self.config.AGENT_DATABASE_URL if write else self.config.DATABASE_URL
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        return psycopg2.connect(url, connect_timeout=5)

    def query(self, sql, params=None, write=False):
        with self.connect(write=write) as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(sql, params or [])
            return cur.fetchall()

    def execute(self, run, write=True):
        with self.connect(write=write) as conn, conn.cursor() as cur:
            result = run(cur)
            conn.commit()
            return result

    @lru_cache(maxsize=None)
    def redis(self, db):
        if not self.config.REDIS_URL:
            return None
        parsed = urlparse(self.config.REDIS_URL)
        return redis.Redis(
            host=parsed.hostname,
            port=parsed.port or 6379,
            db=db,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=2.0,
            health_check_interval=30,
            retry_on_timeout=True,
        )


db = Database()
