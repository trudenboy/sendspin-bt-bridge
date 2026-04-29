from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.services.diagnostics.sendspin_compat import (
    SendspinAudioApi,
    analyze_audio_api_compatibility,
    analyze_daemon_args_compatibility,
    detect_supported_audio_formats_for_device,
    filter_supported_call_kwargs,
    load_sendspin_audio_api,
    query_audio_devices,
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

    audio_api = SendspinAudioApi(
        query_devices=None,
        detect_supported_audio_formats=None,
        parse_audio_format=_parse,
        sources={"parse_audio_format": "sendspin.audio_devices"},
    )

    assert resolve_preferred_audio_format("flac:44100:16:2", object(), audio_api) == {"parsed": "flac:44100:16:2"}
    assert calls == ["flac:44100:16:2"]


def test_resolve_preferred_audio_format_falls_back_to_supported_formats():
    class _Fmt:
        def __init__(self, text):
            self._text = text

        def __str__(self):
            return self._text

    fmt_a = _Fmt("aac:44100:16:2")
    fmt_b = _Fmt("flac:44100:16:2")

    audio_api = SendspinAudioApi(
        query_devices=None,
        detect_supported_audio_formats=lambda _device: [fmt_a, fmt_b],
        parse_audio_format=None,
        sources={"detect_supported_audio_formats": "sendspin.audio_devices"},
    )

    assert resolve_preferred_audio_format("flac:44100:16:2", object(), audio_api) is fmt_b


def test_resolve_preferred_audio_format_matches_structured_fields_when_str_is_generic():
    class _GenericFmt:
        codec = "flac"
        sample_rate = 44100
        bit_depth = 16
        channels = 2

        def __str__(self):
            return "<AudioFormat>"

    generic_fmt = _GenericFmt()
    audio_api = SendspinAudioApi(
        query_devices=None,
        detect_supported_audio_formats=lambda _device: [generic_fmt],
        parse_audio_format=None,
        sources={"detect_supported_audio_formats": "sendspin.audio_devices"},
    )

    assert resolve_preferred_audio_format("flac:44100:16:2", object(), audio_api) is generic_fmt


def test_detect_supported_audio_formats_uses_audio_device_object_for_new_layout():
    seen = []

    def _detect(device):
        seen.append(device)
        return ["ok"]

    audio_device = object()
    audio_api = SendspinAudioApi(
        query_devices=None,
        detect_supported_audio_formats=_detect,
        parse_audio_format=None,
        sources={"detect_supported_audio_formats": "sendspin.audio_devices"},
    )

    assert detect_supported_audio_formats_for_device(audio_device, audio_api) == ["ok"]
    assert seen == [audio_device]


def test_detect_supported_audio_formats_uses_index_for_legacy_layout():
    seen = []

    def _detect(device_index):
        seen.append(device_index)
        return ["ok"]

    audio_device = SimpleNamespace(index=42)
    audio_api = SendspinAudioApi(
        query_devices=None,
        detect_supported_audio_formats=_detect,
        parse_audio_format=None,
        sources={"detect_supported_audio_formats": "sendspin.audio"},
    )

    assert detect_supported_audio_formats_for_device(audio_device, audio_api) == ["ok"]
    assert seen == [42]


def test_query_audio_devices_uses_resolved_callable():
    devices = [object()]
    audio_api = SendspinAudioApi(
        query_devices=lambda: devices,
        detect_supported_audio_formats=None,
        parse_audio_format=None,
        sources={"query_devices": "sendspin.audio_devices"},
    )

    assert query_audio_devices(audio_api) == devices


def test_load_sendspin_audio_api_prefers_audio_devices_module(monkeypatch):
    audio_devices_mod = SimpleNamespace(
        query_devices=lambda: ["new"],
        detect_supported_audio_formats=lambda _device: ["fmt"],
        parse_audio_format=lambda spec: {"spec": spec},
    )
    legacy_audio_mod = SimpleNamespace(query_devices=lambda: ["legacy"])

    def _fake_import(name):
        if name == "sendspin.audio_devices":
            return audio_devices_mod
        if name == "sendspin.audio":
            return legacy_audio_mod
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("sendspin_bridge.services.diagnostics.sendspin_compat.importlib.import_module", _fake_import)

    audio_api = load_sendspin_audio_api()

    assert audio_api.query_devices is audio_devices_mod.query_devices
    assert audio_api.sources["query_devices"] == "sendspin.audio_devices"
    assert audio_api.sources["detect_supported_audio_formats"] == "sendspin.audio_devices"
    assert audio_api.sources["parse_audio_format"] == "sendspin.audio_devices"


def test_analyze_audio_api_compatibility_allows_missing_parser_when_detection_exists():
    audio_api = SendspinAudioApi(
        query_devices=lambda: [],
        detect_supported_audio_formats=lambda _index: [],
        parse_audio_format=None,
        sources={
            "query_devices": "sendspin.audio_devices",
            "detect_supported_audio_formats": "sendspin.audio_devices",
        },
    )

    result = analyze_audio_api_compatibility(audio_api)

    assert result["compatible"] is True
    assert result["has_query_devices"] is True
    assert result["has_parse_audio_format"] is False
    assert result["has_detect_supported_audio_formats"] is True
    assert result["warnings"] == []
    assert result["sources"]["query_devices"] == "sendspin.audio_devices"


def test_analyze_audio_api_compatibility_warns_when_preferred_format_cannot_be_parsed():
    audio_api = SendspinAudioApi(
        query_devices=lambda: [],
        detect_supported_audio_formats=None,
        parse_audio_format=None,
        sources={"query_devices": "sendspin.audio"},
    )

    result = analyze_audio_api_compatibility(audio_api)

    assert result["compatible"] is False
    assert any("missing detect_supported_audio_formats" in warning for warning in result["warnings"])
    assert any("preferred_format will be ignored" in warning for warning in result["warnings"])
