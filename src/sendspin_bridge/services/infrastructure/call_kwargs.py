from __future__ import annotations

import inspect


def filter_supported_call_kwargs(callable_obj, kwargs: dict[str, object]) -> dict[str, object]:
    """Keep only kwargs supported by the inspected callable signature."""
    try:
        supported = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return dict(kwargs)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in supported.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in supported}
