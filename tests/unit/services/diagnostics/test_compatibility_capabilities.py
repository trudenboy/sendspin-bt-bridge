from __future__ import annotations

from sendspin_bridge.services.diagnostics.compatibility_capabilities import detect_compatibility_capabilities


def test_pipewire_disables_pulseaudio_module_reload() -> None:
    result = detect_compatibility_capabilities(
        server_name="PulseAudio (on PipeWire 1.0.5)",
        loaded_modules={"libpipewire-module-protocol-native"},
        is_linux=True,
        adapter_library_available=True,
        has_bt_capabilities=True,
        usb_access=True,
        rfkill_access=True,
    )

    assert result["audio_backend"] == "pipewire"
    assert result["pa_module_reload"]["available"] is False
    assert "PipeWire" in result["pa_module_reload"]["reason"]


def test_classic_pulseaudio_reload_requires_discover_module() -> None:
    available = detect_compatibility_capabilities(
        server_name="pulseaudio",
        loaded_modules={"module-bluez5-discover"},
        is_linux=True,
        adapter_library_available=True,
        has_bt_capabilities=True,
        usb_access=False,
        rfkill_access=False,
    )
    missing = detect_compatibility_capabilities(
        server_name="pulseaudio",
        loaded_modules=set(),
        is_linux=True,
        adapter_library_available=True,
        has_bt_capabilities=True,
        usb_access=False,
        rfkill_access=False,
    )

    assert available["pa_module_reload"]["available"] is True
    assert missing["pa_module_reload"]["available"] is False


def test_adapter_recovery_reports_partial_usb_and_rfkill_support() -> None:
    result = detect_compatibility_capabilities(
        server_name="pulseaudio",
        loaded_modules=set(),
        is_linux=True,
        adapter_library_available=True,
        has_bt_capabilities=True,
        usb_access=False,
        rfkill_access=False,
    )

    recovery = result["adapter_auto_recovery"]
    assert recovery["available"] is True
    assert recovery["level"] == "power_cycle_only"
    assert recovery["usb_reset_available"] is False
    assert recovery["rfkill_available"] is False


def test_adapter_recovery_unavailable_without_bt_capabilities() -> None:
    result = detect_compatibility_capabilities(
        server_name="pulseaudio",
        loaded_modules=set(),
        is_linux=True,
        adapter_library_available=True,
        has_bt_capabilities=False,
        usb_access=True,
        rfkill_access=True,
    )

    recovery = result["adapter_auto_recovery"]
    assert recovery["available"] is False
    assert recovery["level"] == "unavailable"
    assert "capabilities" in recovery["reason"].lower()
