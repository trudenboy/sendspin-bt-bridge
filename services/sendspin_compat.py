"""Helpers for runtime sendspin compatibility checks and dependency fingerprints."""

from __future__ import annotations

import inspect
from importlib.metadata import PackageNotFoundError, version

_RUNTIME_DEPENDENCIES = (
    "sendspin",
    "aiosendspin",
    "av",
    "music-assistant-client",
)


def filter_supported_call_kwargs(callable_obj, kwargs: dict[str, object]) -> dict[str, object]:
    """Keep only kwargs supported by the inspected callable signature."""
    try:
        supported = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in supported}


def analyze_daemon_args_compatibility(daemon_args_cls, candidate_kwargs: dict[str, object]) -> dict[str, object]:
    """Inspect whether our runtime DaemonArgs kwargs still bind cleanly."""
    try:
        signature = inspect.signature(daemon_args_cls)
    except (TypeError, ValueError) as exc:
        return {
            "compatible": False,
            "signature": None,
            "filtered_keys": [],
            "dropped_keys": [],
            "missing_required": [],
            "bind_error": f"Could not inspect DaemonArgs signature: {exc}",
        }

    filtered = filter_supported_call_kwargs(daemon_args_cls, candidate_kwargs)
    missing_required: list[str] = []
    for name, param in signature.parameters.items():
        if name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        if name not in filtered:
            missing_required.append(name)

    bind_error = None
    try:
        signature.bind(**filtered)
    except TypeError as exc:
        bind_error = str(exc)

    return {
        "compatible": not missing_required and bind_error is None,
        "signature": str(signature),
        "filtered_keys": list(filtered.keys()),
        "dropped_keys": [key for key in candidate_kwargs if key not in filtered],
        "missing_required": missing_required,
        "bind_error": bind_error,
    }


def get_runtime_dependency_versions(names: tuple[str, ...] = _RUNTIME_DEPENDENCIES) -> dict[str, str]:
    """Return resolved versions for the critical runtime Python dependencies."""
    resolved: dict[str, str] = {}
    for name in names:
        try:
            resolved[name] = version(name)
        except PackageNotFoundError:
            resolved[name] = "not installed"
        except Exception:
            resolved[name] = "unknown"
    return resolved


def format_dependency_versions(versions_by_name: dict[str, str]) -> str:
    """Format dependency versions for concise logging."""
    return ", ".join(f"{name}={value}" for name, value in versions_by_name.items())
