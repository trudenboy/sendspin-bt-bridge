from __future__ import annotations

from services.sendspin_compat import analyze_daemon_args_compatibility, filter_supported_call_kwargs


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
            "use_hardware_volume": False,
        },
    )

    assert result["compatible"] is False
    assert result["missing_required"] == ["new_required"]
