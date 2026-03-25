"""Helpers for runtime sendspin compatibility checks and dependency fingerprints."""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Optional, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_RUNTIME_DEPENDENCIES = (
    "sendspin",
    "aiosendspin",
    "av",
    "music-assistant-client",
)

_AUDIO_API_MODULE_CANDIDATES = ("sendspin.audio_devices", "sendspin.audio")


@dataclass(frozen=True)
class SendspinAudioApi:
    """Resolved sendspin audio helpers across legacy and current package layouts."""

    query_devices: Callable[[], list[AudioDeviceLike]] | None
    detect_supported_audio_formats: Callable[[object], list[object]] | None
    parse_audio_format: Callable[[str], object] | None
    sources: dict[str, str]


class AudioDeviceLike(Protocol):
    """Minimal audio-device surface used by the bridge."""

    index: int | None
    name: str
    output_channels: int
    is_default: bool


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


def load_sendspin_audio_api() -> SendspinAudioApi:
    """Resolve audio helpers from whichever sendspin module exports them."""
    modules: list[tuple[str, object]] = []
    for module_name in _AUDIO_API_MODULE_CANDIDATES:
        try:
            modules.append((module_name, importlib.import_module(module_name)))
        except ModuleNotFoundError:
            continue

    resolved: dict[str, object | None] = {
        "query_devices": None,
        "detect_supported_audio_formats": None,
        "parse_audio_format": None,
    }
    sources: dict[str, str] = {}
    for func_name in resolved:
        for module_name, module in modules:
            candidate = getattr(module, func_name, None)
            if callable(candidate):
                resolved[func_name] = candidate
                sources[func_name] = module_name
                break

    return SendspinAudioApi(
        query_devices=cast("Optional[Callable[[], list[AudioDeviceLike]]]", resolved["query_devices"]),
        detect_supported_audio_formats=cast(
            "Optional[Callable[[object], list[object]]]", resolved["detect_supported_audio_formats"]
        ),
        parse_audio_format=cast("Optional[Callable[[str], object]]", resolved["parse_audio_format"]),
        sources=sources,
    )


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


def query_audio_devices(audio_api: SendspinAudioApi | None = None) -> list[AudioDeviceLike]:
    """Query audio devices through the resolved sendspin audio API."""
    api = audio_api or load_sendspin_audio_api()
    if not callable(api.query_devices):
        raise RuntimeError("sendspin audio API has no query_devices")
    return cast("list[AudioDeviceLike]", api.query_devices())


def detect_supported_audio_formats_for_device(
    audio_device: AudioDeviceLike, audio_api: SendspinAudioApi | None = None
) -> list[object]:
    """Resolve supported formats across legacy/new detect_supported_audio_formats signatures."""
    api = audio_api or load_sendspin_audio_api()
    detector = api.detect_supported_audio_formats
    if not callable(detector):
        raise RuntimeError("sendspin audio API has no detect_supported_audio_formats")

    if api.sources.get("detect_supported_audio_formats") == "sendspin.audio_devices":
        return detector(audio_device)

    legacy_device = getattr(audio_device, "index", audio_device)
    return detector(legacy_device)


def resolve_preferred_audio_format(
    preferred_format: str, audio_device: AudioDeviceLike, audio_api: SendspinAudioApi | None = None
) -> object:
    """Resolve a preferred format string across legacy and current sendspin layouts."""
    api = audio_api or load_sendspin_audio_api()
    parser = api.parse_audio_format
    if callable(parser):
        return parser(preferred_format)

    if not callable(api.detect_supported_audio_formats):
        raise ValueError("sendspin.audio has no parse_audio_format or detect_supported_audio_formats")

    wanted = _normalize_audio_format_spec(preferred_format)
    supported_formats = detect_supported_audio_formats_for_device(audio_device, api)
    for candidate in supported_formats:
        if _audio_format_identifier(candidate) == wanted:
            return candidate

    raise ValueError(f"Preferred format {preferred_format!r} is not supported by the selected audio device")


def analyze_audio_api_compatibility(audio_api: SendspinAudioApi | None = None) -> dict[str, object]:
    """Inspect whether the installed sendspin audio helpers meet runtime needs."""
    api = audio_api or load_sendspin_audio_api()
    has_query_devices = callable(api.query_devices)
    has_parse_audio_format = callable(api.parse_audio_format)
    has_detect_supported_audio_formats = callable(api.detect_supported_audio_formats)
    warnings: list[str] = []

    if not has_query_devices:
        warnings.append("sendspin audio API is missing query_devices")
    if not has_parse_audio_format and not has_detect_supported_audio_formats:
        warnings.append(
            "sendspin audio API exposes neither parse_audio_format nor detect_supported_audio_formats; "
            "preferred_format will be ignored"
        )
    if has_query_devices and not has_detect_supported_audio_formats:
        warnings.append("sendspin audio API is missing detect_supported_audio_formats")

    return {
        "compatible": has_query_devices and has_detect_supported_audio_formats,
        "has_query_devices": has_query_devices,
        "has_parse_audio_format": has_parse_audio_format,
        "has_detect_supported_audio_formats": has_detect_supported_audio_formats,
        "sources": dict(api.sources),
        "warnings": warnings,
    }
