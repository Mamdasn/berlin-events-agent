import os


class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    AGENT_DATABASE_URL = os.environ.get("AGENT_DATABASE_URL", "") or DATABASE_URL

    REDIS_URL = os.environ.get("REDIS_URL", "")
    AGENT_REDIS_DB_MEMORY = int(os.environ.get("AGENT_REDIS_DB_MEMORY", "3"))
    AGENT_REDIS_DB_CACHE = int(os.environ.get("AGENT_REDIS_DB_CACHE", "4"))

    COOKIE_SESSION_SECRET = os.environ.get("COOKIE_SESSION_SECRET", "")
    SESSION_COOKIE = os.environ.get("SESSION_COOKIE", "ec_session")
    SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "43200"))
    COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

    SECRET_STORE_PATH = os.environ.get("SECRET_STORE_PATH", "/app/data/secrets.json")

    ADMIN_PW_HASH = os.environ.get("ADMIN_PW_HASH", "")
    ADMIN_TOTP_SECRET = os.environ.get("ADMIN_TOTP_SECRET", "")
    EDITOR_NAME = os.environ.get("EDITOR_NAME", "editor")

    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    DEEPSEEK_API_URL = os.environ.get(
        "DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions"
    )
    DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    DEEPSEEK_TIMEOUT_SECONDS = float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "30"))
    DEEPSEEK_MAX_ATTEMPTS = int(os.environ.get("DEEPSEEK_MAX_ATTEMPTS", "3"))
    DEEPSEEK_RETRY_BACKOFF_SECONDS = float(
        os.environ.get("DEEPSEEK_RETRY_BACKOFF_SECONDS", "1.5")
    )
    DEEPSEEK_DAILY_MAX_REQUESTS = int(
        os.environ.get("DEEPSEEK_DAILY_MAX_REQUESTS") or 200
    )

    AGENT_MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS") or 10)
    AGENT_RESULT_LIMIT = int(os.environ.get("AGENT_RESULT_LIMIT", "25"))
    AGENT_FIELD_MAX_CHARS = int(os.environ.get("AGENT_FIELD_MAX_CHARS", "600"))
    AGENT_MESSAGE_MAX_CHARS = int(os.environ.get("AGENT_MESSAGE_MAX_CHARS", "2000"))
    PROPOSAL_TTL_SECONDS = int(os.environ.get("PROPOSAL_TTL_SECONDS", "1800"))
    MEMORY_TTL_SECONDS = int(os.environ.get("MEMORY_TTL_SECONDS", "3600"))
    MEMORY_MAX_TURNS = int(os.environ.get("MEMORY_MAX_TURNS", "12"))

    EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    EMBED_INDEX_PATH = os.environ.get("EMBED_INDEX_PATH", "data/embeddings.npz")
    SEMANTIC_TOP_K = int(os.environ.get("SEMANTIC_TOP_K", "10"))
    NEARBY_RADIUS_KM = float(os.environ.get("NEARBY_RADIUS_KM", "2.0"))


config = Config()
