"""Helpers for runtime sendspin compatibility checks and dependency fingerprints."""

from __future__ import annotations

import inspect
import logging
from importlib.metadata import PackageNotFoundError, version

logger = logging.getLogger(__name__)

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
        except Exception as exc:
            resolved[name] = "unknown"
            logger.debug("Failed to get version for %s: %s", name, exc)
    return resolved


def format_dependency_versions(versions_by_name: dict[str, str]) -> str:
    """Format dependency versions for concise logging."""
    return ", ".join(f"{name}={value}" for name, value in versions_by_name.items())


def _normalize_audio_format_spec(spec: str) -> str:
    """Normalize an audio format string for tolerant comparisons."""
    return "".join(spec.strip().lower().split())


def _audio_format_identifier(audio_format: object) -> str:
    """Best-effort stable identifier for a sendspin audio format object."""
    rendered = str(audio_format).strip()
    normalized_rendered = _normalize_audio_format_spec(rendered)
    if ":" in normalized_rendered and "<" not in normalized_rendered:
        return normalized_rendered

    codec = None
    for attr_name in ("codec", "encoding", "format", "format_name", "name"):
        value = getattr(audio_format, attr_name, None)
        if value is not None:
            codec = getattr(value, "value", value)
            break

    sample_rate = None
    for attr_name in ("sample_rate", "sample_rate_hz", "samplerate", "rate"):
        value = getattr(audio_format, attr_name, None)
        if value is not None:
            sample_rate = getattr(value, "value", value)
            break

    bit_depth = None
    for attr_name in ("bit_depth", "bits_per_sample", "bits", "sample_size"):
        value = getattr(audio_format, attr_name, None)
        if value is not None:
            bit_depth = getattr(value, "value", value)
            break

    channels = None
    for attr_name in ("channels", "channel_count", "num_channels", "nchannels"):
        value = getattr(audio_format, attr_name, None)
        if value is None:
            continue
        channels = len(value) if isinstance(value, (list, tuple, set)) else getattr(value, "value", value)
        break

    if codec is not None and sample_rate is not None and bit_depth is not None and channels is not None:
        return _normalize_audio_format_spec(f"{codec}:{sample_rate}:{bit_depth}:{channels}")

    return normalized_rendered


def resolve_preferred_audio_format(audio_module, preferred_format: str, audio_device_index: int) -> object:
    """Resolve a preferred format string across old/new sendspin.audio APIs."""
    parser = getattr(audio_module, "parse_audio_format", None)
    if callable(parser):
        return parser(preferred_format)

    detect_supported = getattr(audio_module, "detect_supported_audio_formats", None)
    if not callable(detect_supported):
        raise ValueError("sendspin.audio has no parse_audio_format or detect_supported_audio_formats")

    wanted = _normalize_audio_format_spec(preferred_format)
    supported_formats = detect_supported(audio_device_index)
    for candidate in supported_formats:
        if _audio_format_identifier(candidate) == wanted:
            return candidate

    raise ValueError(f"Preferred format {preferred_format!r} is not supported by audio device {audio_device_index}")


def analyze_audio_api_compatibility(audio_module) -> dict[str, object]:
    """Inspect whether the installed sendspin.audio module exposes usable APIs."""
    has_query_devices = callable(getattr(audio_module, "query_devices", None))
    has_parse_audio_format = callable(getattr(audio_module, "parse_audio_format", None))
    has_detect_supported_audio_formats = callable(getattr(audio_module, "detect_supported_audio_formats", None))
    warnings: list[str] = []

    if not has_query_devices:
        warnings.append("sendspin.audio.query_devices is missing")
    if not has_parse_audio_format and not has_detect_supported_audio_formats:
        warnings.append(
            "sendspin.audio exposes neither parse_audio_format nor detect_supported_audio_formats; "
            "preferred_format will be ignored"
        )

    return {
        "compatible": has_query_devices,
        "has_query_devices": has_query_devices,
        "has_parse_audio_format": has_parse_audio_format,
        "has_detect_supported_audio_formats": has_detect_supported_audio_formats,
        "warnings": warnings,
    }
