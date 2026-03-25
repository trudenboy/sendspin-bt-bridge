from __future__ import annotations

from types import SimpleNamespace

from services.sendspin_compat import (
    analyze_audio_api_compatibility,
    analyze_daemon_args_compatibility,
    filter_supported_call_kwargs,
    resolve_preferred_audio_format,
)


def test_filter_supported_call_kwargs_drops_unknown_fields():
    class FakeDaemonArgs:
        def __init__(self, audio_device, client_id, use_mpris=False):
            pass

    filtered = filter_supported_call_kwargs(
        FakeDaemonArgs,
        {
            "audio_device": "default",
            "client_id": "player-1",
            "use_mpris": False,
            "volume_controller": None,
            "use_hardware_volume": False,
        },
    )

    assert filtered == {
        "audio_device": "default",
        "client_id": "player-1",
        "use_mpris": False,
    }


def test_analyze_daemon_args_compatibility_flags_missing_required_fields():
    class FakeDaemonArgs:
        def __init__(self, audio_device, client_id, new_required):
            pass

    result = analyze_daemon_args_compatibility(
        FakeDaemonArgs,
        {
            "audio_device": "default",
            "client_id": "player-1",
            "volume_controller": None,
        },
    )

    assert result["compatible"] is False
    assert result["missing_required"] == ["new_required"]


def test_resolve_preferred_audio_format_uses_parser_when_available():
    calls = []

    def _parse(spec):
        calls.append(spec)
        return {"parsed": spec}

    audio_module = SimpleNamespace(parse_audio_format=_parse)

    assert resolve_preferred_audio_format(audio_module, "flac:44100:16:2", 0) == {"parsed": "flac:44100:16:2"}
    assert calls == ["flac:44100:16:2"]


def test_resolve_preferred_audio_format_falls_back_to_supported_formats():
    class _Fmt:
        def __init__(self, text):
            self._text = text

        def __str__(self):
            return self._text

    fmt_a = _Fmt("aac:44100:16:2")
    fmt_b = _Fmt("flac:44100:16:2")

    audio_module = SimpleNamespace(detect_supported_audio_formats=lambda _index: [fmt_a, fmt_b])

    assert resolve_preferred_audio_format(audio_module, "flac:44100:16:2", 7) is fmt_b


def test_resolve_preferred_audio_format_matches_structured_fields_when_str_is_generic():
    class _GenericFmt:
        codec = "flac"
        sample_rate = 44100
        bit_depth = 16
        channels = 2

        def __str__(self):
            return "<AudioFormat>"

    generic_fmt = _GenericFmt()
    audio_module = SimpleNamespace(detect_supported_audio_formats=lambda _index: [generic_fmt])

    assert resolve_preferred_audio_format(audio_module, "flac:44100:16:2", 1) is generic_fmt


def test_analyze_audio_api_compatibility_allows_missing_parser_when_detection_exists():
    audio_module = SimpleNamespace(
        query_devices=lambda: [],
        detect_supported_audio_formats=lambda _index: [],
    )

    result = analyze_audio_api_compatibility(audio_module)

    assert result["compatible"] is True
    assert result["has_query_devices"] is True
    assert result["has_parse_audio_format"] is False
    assert result["has_detect_supported_audio_formats"] is True
    assert result["warnings"] == []


def test_analyze_audio_api_compatibility_warns_when_preferred_format_cannot_be_parsed():
    audio_module = SimpleNamespace(query_devices=lambda: [])

    result = analyze_audio_api_compatibility(audio_module)

    assert result["compatible"] is True
    assert any("preferred_format will be ignored" in warning for warning in result["warnings"])
