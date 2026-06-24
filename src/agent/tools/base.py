from pydantic import BaseModel, ValidationError

_REGISTRY = {}


class ToolError(Exception):
    pass


class Tool:
    def __init__(self, name, description, args_model, func):
        self.name = name
        self.description = description
        self.args_model = args_model
        self.func = func

    def spec(self):
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def run(self, arguments):
        try:
            args = self.args_model(**(arguments or {}))
        except ValidationError as e:
            raise ToolError(_format_validation_error(e)) from e
        return self.func(args)


def _format_validation_error(error):
    parts = []
    for err in error.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(args)"
        parts.append(f"{loc}: {err['msg']}")
    return "invalid arguments — " + "; ".join(parts)


def tool(name, description, args_model):
    def decorator(func):
        _REGISTRY[name] = Tool(name, description, args_model, func)
        return func

    return decorator


def get(name):
    return _REGISTRY.get(name)


def specs(names=None):
    items = _REGISTRY.values() if names is None else [_REGISTRY[n] for n in names]
    return [t.spec() for t in items]


def dispatch(name, arguments):
    t = get(name)
    if t is None:
        raise ToolError(f"unknown tool '{name}'")
    return t.run(arguments)


class DateWindow(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
