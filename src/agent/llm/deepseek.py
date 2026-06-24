import logging
import time

import requests

from agent.config import config

log = logging.getLogger(__name__)


class DeepSeekError(RuntimeError):
    pass


def chat(messages, tools=None, tool_choice="auto", temperature=0.2, max_tokens=1200):
    if not config.DEEPSEEK_API_KEY:
        raise DeepSeekError("DEEPSEEK_API_KEY is not set")

    body = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice

    last_error = None
    for attempt in range(1, config.DEEPSEEK_MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                config.DEEPSEEK_API_URL,
                headers={
                    "Authorization": "Bearer %s" % config.DEEPSEEK_API_KEY,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=config.DEEPSEEK_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]
        except Exception as e:  # noqa: BLE001 - network/parse errors all retry the same
            last_error = e
            log.warning(
                "DeepSeek try %d/%d failed: %s",
                attempt,
                config.DEEPSEEK_MAX_ATTEMPTS,
                e,
            )
            if attempt < config.DEEPSEEK_MAX_ATTEMPTS:
                time.sleep(config.DEEPSEEK_RETRY_BACKOFF_SECONDS * attempt)
    raise DeepSeekError(str(last_error))
