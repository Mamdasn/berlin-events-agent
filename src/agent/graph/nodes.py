import json

from agent import tools
from agent.llm import deepseek
from agent.tools.base import ToolError


def reason(messages, allow_tools=True):
    spec = tools.specs(tools.LLM_TOOL_NAMES) if allow_tools else None
    choice = "auto" if allow_tools else "none"
    return deepseek.chat(messages, tools=spec, tool_choice=choice)


def parse_tool_call(tool_call):
    fn = tool_call.get("function", {})
    name = fn.get("name", "")
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        return name, None, "arguments were not valid JSON"
    return name, args, None


def run_tool(name, args):
    try:
        result = tools.dispatch(name, args)
        return result, None
    except ToolError as e:
        return None, str(e)


def tool_message(tool_call_id, payload):
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(payload, ensure_ascii=False, default=str),
    }
