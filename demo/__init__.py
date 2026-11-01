"""Demo mode — run the web UI against a canonical nine-player demo stand.

Usage:
    DEMO_MODE=true python -m sendspin_bridge

All hardware-dependent layers (BlueZ, D-Bus, PulseAudio, Music Assistant,
and per-device subprocesses) are replaced with deterministic mocks so the
local demo is repeatable enough for documentation screenshots.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Demo audio state (the Hardware page's audio half and the safe commands)
# ---------------------------------------------------------------------------

_DEMO_BLUEZ_NAME_RE = re.compile(r"(?P<address>[0-9A-F]{2}(?:_[0-9A-F]{2}){5})")

_demo_audio_lock = threading.RLock()
_demo_sink_volume: dict[str, int] = {}
_demo_sink_muted: dict[str, bool] = {}
_demo_sink_suspended: dict[str, bool] = {}
_demo_default_sink: str | None = None
_demo_audio_ready = False


def _demo_name_mac(name: str) -> str:
    """The MAC embedded in a demo ``bluez_card.*`` / ``bluez_output.*`` name."""
    match = _DEMO_BLUEZ_NAME_RE.search(str(name).upper())
    return match.group("address").replace("_", ":") if match else ""


def _demo_audio_state() -> None:
    """Seed the in-memory volume/mute state once, from the fixture stand."""
    global _demo_audio_ready, _demo_default_sink
    with _demo_audio_lock:
        if _demo_audio_ready:
            return
        from demo.fixtures import DEMO_CARDS, DEMO_DEVICE_STATUS

        for card in DEMO_CARDS:
            for endpoint in card.get("endpoints", []):
                if endpoint.get("kind") != "sink":
                    continue
                name = str(endpoint["name"])
                status = DEMO_DEVICE_STATUS.get(_demo_name_mac(name), {})
                _demo_sink_volume[name] = int(status.get("volume", 50))
                _demo_sink_muted[name] = bool(status.get("muted", False))
                _demo_sink_suspended[name] = False
        _demo_default_sink = next(iter(_demo_sink_volume), None)
        _demo_audio_ready = True


def _demo_local_sinks() -> list[Any]:
    """The fixture sinks as ``LocalSink`` objects, with the live demo state."""
    from sendspin_bridge.services.audio.sink_inventory import LocalSink

    _demo_audio_state()
    from demo.fixtures import DEMO_CARDS

    result: list[LocalSink] = []
    with _demo_audio_lock:
        for card in DEMO_CARDS:
            ports = card.get("ports") or []
            port = next((item for item in ports if item.get("active")), ports[0] if ports else {})
            for endpoint in card.get("endpoints", []):
                if endpoint.get("kind") != "sink":
                    continue
                name = str(endpoint["name"])
                result.append(
                    LocalSink(
                        name=name,
                        description=str(port.get("description") or name),
                        state="suspended" if _demo_sink_suspended.get(name) else "idle",
                        sample_spec="s16le 2ch 48000Hz",
                        muted=bool(_demo_sink_muted.get(name, False)),
                        volume=int(_demo_sink_volume.get(name, 50)),
                        active_port=str(port.get("name") or "") or None,
                        active_port_available="available" if port.get("available", True) else "not available",
                        identifiers={},
                    )
                )
    return result


def _demo_local_sources() -> list[Any]:
    """The fixture sources as ``LocalSource`` objects."""
    from demo.fixtures import DEMO_SOURCES
    from sendspin_bridge.services.audio.sink_inventory import LocalSource

    result: list[LocalSource] = []
    for index, source in enumerate(DEMO_SOURCES):
        raw_state = str(source.get("state") or "").strip().lower()
        result.append(
            LocalSource(
                name=str(source["name"]),
                description=str(source.get("description") or ""),
                state=raw_state if raw_state in {"running", "idle", "suspended"} else "unknown",
                sample_spec="s16le 2ch 44100Hz",
                muted=False,
                volume=100,
                index=index,
            )
        )
    return result


def _demo_local_cards() -> list[Any]:
    """The fixture cards as ``LocalCard`` objects, with their endpoints attached."""
    from demo.fixtures import DEMO_CARDS
    from sendspin_bridge.services.audio.sink_inventory import CardPort, CardProfile, LocalCard

    sink_names = [sink.name for sink in _demo_local_sinks()]
    source_names = [source.name for source in _demo_local_sources()]
    result: list[LocalCard] = []
    for index, card in enumerate(DEMO_CARDS):
        card_name = str(card["name"])
        profiles = tuple(
            CardProfile(
                name=str(profile.get("name", "")),
                description=str(profile.get("description", "")),
                available=bool(profile.get("available", True)),
            )
            for profile in card.get("profiles", [])
            if profile.get("name")
        )
        ports = tuple(
            CardPort(
                name=str(port.get("name", "")),
                description=str(port.get("description", "")),
                available=bool(port.get("available", True)),
                active=bool(port.get("active", False)),
            )
            for port in card.get("ports", [])
            if port.get("name")
        )
        endpoints = {str(endpoint.get("name") or "") for endpoint in card.get("endpoints", [])}
        card_mac = _demo_name_mac(card_name)
        result.append(
            LocalCard(
                name=card_name,
                driver=str(card.get("driver") or ""),
                active_profile=str(card.get("active_profile") or "") or None,
                profiles=profiles,
                ports=ports,
                sink_names=tuple(name for name in sink_names if name in endpoints),
                source_names=tuple(name for name in source_names if _demo_name_mac(name) == card_mac),
                index=index,
            )
        )
    return result


def _demo_audio_facts() -> Any:
    """One ``AudioFacts`` assembled from the fixture cards, sinks and sources."""
    from sendspin_bridge.services.hardware.inventory import CaptureFacts, build_audio_facts

    sources = _demo_local_sources()
    capture = (
        CaptureFacts(kind="pulse_source", node=sources[0].name)
        if sources
        else CaptureFacts(kind="unavailable", reason="The demo stand has no capture source.")
    )
    return build_audio_facts(
        _demo_local_cards(),
        _demo_local_sinks(),
        sources,
        capture,
        default_sink=_demo_default_sink,
    )


def _demo_invalidate_hardware() -> None:
    from sendspin_bridge.services.hardware import inventory as _inventory

    _inventory.clear_cache()


def _demo_pulse_list_sinks() -> list[dict[str, Any]]:
    """The audio server's sink list: connected demo speakers, with live volume/mute."""
    from demo.fixtures import DEMO_DEVICE_STATUS

    listed: list[dict[str, Any]] = []
    for sink in _demo_local_sinks():
        status = DEMO_DEVICE_STATUS.get(_demo_name_mac(sink.name), {})
        if not status.get("bluetooth_connected"):
            continue
        if not status.get("bt_management_enabled", True):
            continue
        listed.append(
            {
                "name": sink.name,
                "description": sink.description,
                "volume": sink.volume,
                "muted": sink.muted,
            }
        )
    return listed


def _demo_set_sink_volume(sink_name: str, volume_pct: int) -> bool:
    _demo_audio_state()
    with _demo_audio_lock:
        _demo_sink_volume[sink_name] = int(volume_pct)
    _demo_invalidate_hardware()
    return True


def _demo_set_sink_mute(sink_name: str, muted: object) -> bool:
    _demo_audio_state()
    with _demo_audio_lock:
        _demo_sink_muted[sink_name] = bool(muted)
    _demo_invalidate_hardware()
    return True


def _demo_suspend_sink(sink_name: str, suspend: bool) -> bool:
    _demo_audio_state()
    with _demo_audio_lock:
        _demo_sink_suspended[sink_name] = bool(suspend)
    _demo_invalidate_hardware()
    return True


def _demo_set_default_sink(sink_name: str) -> bool:
    global _demo_default_sink
    _demo_audio_state()
    with _demo_audio_lock:
        _demo_default_sink = sink_name
    _demo_invalidate_hardware()
    return True


def _demo_install_audio() -> None:
    """Point the audio helpers, the command state and the inventory at the demo stand.

    Idempotent: ``install()`` calls it before the legacy routes bind their
    imports, and ``install_inventory()`` calls it again so a test that only
    installs the inventory still gets working commands.
    """
    from sendspin_bridge.services.audio import pulse as _sp

    _demo_audio_state()
    _sp.list_sinks = _demo_pulse_list_sinks  # type: ignore[assignment]
    _sp.get_sink_description = lambda _name: "Demo audio output"  # type: ignore[assignment]
    _sp.get_server_name = lambda: "pulseaudio (demo)"  # type: ignore[assignment]
    _sp.set_sink_volume = _demo_set_sink_volume  # type: ignore[assignment]
    _sp.get_sink_volume = lambda name: _demo_sink_volume.get(name, 50)  # type: ignore[assignment]
    _sp.set_sink_mute = _demo_set_sink_mute  # type: ignore[assignment]
    _sp.get_sink_mute = lambda name: _demo_sink_muted.get(name, False)  # type: ignore[assignment]
    _sp.suspend_sink = _demo_suspend_sink  # type: ignore[assignment]
    _sp.set_default_sink = _demo_set_default_sink  # type: ignore[assignment]

    # The Default badge on the audio page comes from the server's own answer, so
    # the demo has to answer it too — otherwise the stand shows no default at all.
    async def _demo_aget_default_sink_name() -> str | None:
        return _demo_default_sink

    _sp.aget_default_sink_name = _demo_aget_default_sink_name  # type: ignore[assignment]
    _sp.get_default_sink_name = lambda: _demo_default_sink  # type: ignore[assignment]


def build_demo_config() -> dict[str, Any]:
    """The canonical config demo mode seeds into its isolated config directory.

    The fixtures are the legacy ``BLUETOOTH_DEVICES`` shape (they are the
    screenshot stand's source of truth); the real migration turns them into
    ``players`` exactly as it does for an operator's file, so demo mode never
    carries a second config schema of its own.
    """
    from demo.fixtures import (
        DEMO_ADAPTERS,
        DEMO_DEVICES,
        DEMO_MA_TOKEN,
        DEMO_MA_URL,
    )
    from sendspin_bridge.config import CONFIG_SCHEMA_VERSION, migrate_config_payload

    base: dict[str, Any] = {
        "CONFIG_SCHEMA_VERSION": CONFIG_SCHEMA_VERSION,
        "BLUETOOTH_DEVICES": deepcopy(DEMO_DEVICES),
        "BLUETOOTH_ADAPTERS": deepcopy(DEMO_ADAPTERS),
        "AUTH_ENABLED": False,
        "CHECK_UPDATES": True,
        "UPDATE_CHANNEL": "stable",
        "AUTO_UPDATE": False,
        "BRIDGE_NAME": os.getenv("BRIDGE_NAME") or "DEMO",
        # Always inject canonical MA credentials in demo mode.
        "MA_API_URL": DEMO_MA_URL,
        "MA_API_TOKEN": DEMO_MA_TOKEN,
        # Demo MA state and commands are fully simulated in-process.  Disable
        # the production websocket monitor so bootstrap can never leak a real
        # connection attempt to the intentionally fake demo-ma.local host.
        "MA_WEBSOCKET_MONITOR": False,
        "DUPLICATE_DEVICE_CHECK": False,
        # The phone source is part of the stand: the Hardware page's source
        # strip must be present, and the orchestrator's start call is served
        # by the demo manager below.
        "SENDSPIN_SOURCE": True,
    }
    return migrate_config_payload(base).normalized_config


def _demo_source_node() -> str | None:
    """The capture node the demo phone streams into, or ``None``."""
    from demo.fixtures import DEMO_SOURCES

    return str(DEMO_SOURCES[0]["name"]) if DEMO_SOURCES else None


def _demo_publish_source(enabled: bool = True) -> None:
    """Publish the demo phone source's runtime snapshot."""
    from demo.fixtures import DEMO_BT_DEVICE_INFO
    from sendspin_bridge.services.source import state as _source_state

    node = _demo_source_node()
    phone_mac = str(DEMO_BT_DEVICE_INFO[0]["mac"]) if DEMO_BT_DEVICE_INFO else None
    _source_state.publish(
        enabled=enabled,
        state="streaming" if enabled else "off",
        phone=node if enabled else None,
        phone_mac=phone_mac if enabled else None,
        paired_phone_mac=phone_mac if enabled else None,
        streaming=enabled,
        server="ws://demo-ma.local:8927/sendspin" if enabled else None,
        ma_connected=enabled,
        ma_paired=enabled,
        ma_pairing_code=None,
        ma_pairing_until=0.0,
        ma_error=None,
        error=None,
    )


def install() -> None:
    """Monkey-patch all hardware-dependent modules for demo mode.

    Must be called before the bridge's config is loaded and before any
    BluetoothManager or SendspinClient instances are created.
    """
    # ------------------------------------------------------------------
    # 0. Isolate the config directory and seed it with the fixture stand
    # ------------------------------------------------------------------
    # The rest of the app keeps using the real config layer; only the path it
    # reads and writes is redirected, so API saves, migrations and runtime
    # state all behave exactly as they do in production.
    import sendspin_bridge.config as _config_mod

    _demo_config_dir = Path(os.getenv("SENDSPIN_DEMO_CONFIG_DIR") or "/tmp/sendspin-demo-config")
    _demo_config_file = _demo_config_dir / "config.json"
    os.environ["CONFIG_DIR"] = str(_demo_config_dir)
    _config_mod.CONFIG_DIR = _demo_config_dir
    _config_mod.CONFIG_FILE = _demo_config_file

    def _seed_demo_config() -> None:
        _config_mod.write_config_file(
            build_demo_config(),
            config_file=_demo_config_file,
            config_dir=_demo_config_dir,
        )

    _seed_demo_config()

    # ------------------------------------------------------------------
    # 1. Patch BluetoothManager class
    # ------------------------------------------------------------------
    import sendspin_bridge.bluetooth.manager as bluetooth_manager
    import sendspin_bridge.bridge.state as _st
    from demo.bt_manager import DemoBluetoothManager
    from demo.fixtures import (
        DEMO_ADAPTER_NAMES,
        DEMO_ADAPTERS,
        DEMO_BT_DEVICE_INFO,
        DEMO_DEVICE_STATUS,
        DEMO_DEVICES,
        DEMO_LOG_LINES,
        DEMO_MA_ALL_GROUPS,
        DEMO_MA_NAME_MAP,
        DEMO_MA_NOW_PLAYING,
        DEMO_MA_SERVER_INFO,
        DEMO_MA_TOKEN,
        DEMO_MA_URL,
        DEMO_PAIRED_DEVICES,
        DEMO_PORTAUDIO_DEVICES,
        DEMO_SCAN_RESULTS,
        DEMO_TRACKS,
        DEMO_UPDATE_INFO,
        _ma_now_playing_entry,
        demo_player_id_for_name,
        get_demo_adapter,
    )

    _adapter_names_by_mac = {str(adapter["mac"]).upper(): str(adapter["name"]) for adapter in DEMO_ADAPTERS}
    _demo_state_lock = threading.RLock()
    _demo_runtime_scan_results = deepcopy(DEMO_SCAN_RESULTS)
    _demo_runtime_paired_devices = deepcopy(DEMO_PAIRED_DEVICES)
    _canonical_bt_info_macs = {str(item["mac"]).upper() for item in DEMO_BT_DEVICE_INFO}

    def _demo_enabled() -> bool:
        return os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")

    def _build_demo_bt_device_info(devices: list[dict[str, Any]]) -> list[dict[str, str]]:
        info: list[dict[str, str]] = []
        for device in devices:
            mac = str(device.get("mac") or "").upper()
            if not mac:
                continue
            if mac not in _canonical_bt_info_macs and any(
                str(item.get("mac") or "").upper() == mac for item in DEMO_PAIRED_DEVICES
            ):
                continue
            name = str(device.get("name") or mac)
            info.append(
                {
                    "mac": mac,
                    "name": name,
                    "paired": "yes",
                    "trusted": "yes",
                    "connected": "yes" if bool(device.get("connected")) else "no",
                    "bonded": "yes",
                    "blocked": "no",
                    "icon": "audio-card",
                }
            )
        return info

    _demo_runtime_bt_device_info = _build_demo_bt_device_info(_demo_runtime_paired_devices)

    def _current_demo_paired_devices() -> list[dict[str, Any]]:
        with _demo_state_lock:
            return deepcopy(_demo_runtime_paired_devices)

    def _current_demo_bt_device_info() -> list[dict[str, str]]:
        with _demo_state_lock:
            return deepcopy(_demo_runtime_bt_device_info)

    def _current_demo_excluded_scan_macs() -> set[str]:
        from sendspin_bridge.config import load_config
        from sendspin_bridge.config.players import device_entries

        with _demo_state_lock:
            paired_macs = {
                str(device.get("mac") or "").upper()
                for device in _demo_runtime_paired_devices
                if str(device.get("mac") or "").strip()
            }
        configured_macs = {
            str(device.get("mac") or "").upper() for device in device_entries(load_config()) if device.get("mac")
        }
        return configured_macs | paired_macs

    def _find_demo_device(mac: str) -> dict[str, Any] | None:
        normalized = str(mac or "").strip().upper()
        with _demo_state_lock:
            for collection in (_demo_runtime_scan_results, _demo_runtime_paired_devices):
                for item in collection:
                    if str(item.get("mac") or "").upper() == normalized:
                        return deepcopy(item)
        return None

    _st.set_runtime_mode_info(
        {
            "mode": "demo",
            "is_mocked": True,
            "simulator_active": True,
            "fixture_devices": len(DEMO_DEVICES),
            "fixture_groups": len(DEMO_MA_ALL_GROUPS),
            "disclaimer": "Demo mode simulates Bluetooth, PulseAudio, Music Assistant, and subprocess runtime layers.",
            "details": {
                "bridge_name": "DEMO",
                "adapter_count": len(DEMO_ADAPTERS),
                "adapter_names": list(DEMO_ADAPTER_NAMES),
            },
            "mocked_layers": [
                {
                    "layer": "BluetoothManager",
                    "summary": "Bluetooth transport is simulated with DemoBluetoothManager.",
                    "details": {
                        "adapters": [
                            {"id": adapter["id"], "mac": adapter["mac"], "name": adapter["name"]}
                            for adapter in DEMO_ADAPTERS
                        ],
                        "scan_results": len(DEMO_SCAN_RESULTS),
                    },
                },
                {
                    "layer": "PulseAudio",
                    "summary": "Pulse sink control is served from in-memory demo state.",
                    "details": {"sinks": len(DEMO_DEVICES)},
                },
                {
                    "layer": "Sendspin subprocess",
                    "summary": "Per-device subprocess lifecycle is short-circuited to local status updates.",
                    "details": {"simulated_devices": len(DEMO_DEVICES)},
                },
                {
                    "layer": "Music Assistant",
                    "summary": "MA discovery, groups, monitor, and commands use demo fixtures.",
                    "details": {"syncgroups": len(DEMO_MA_ALL_GROUPS)},
                },
            ],
        }
    )
    _st.set_ma_api_credentials(DEMO_MA_URL, DEMO_MA_TOKEN)
    _st.set_ma_server_version(str(DEMO_MA_SERVER_INFO["version"]))
    _st.set_ma_groups(deepcopy(DEMO_MA_NAME_MAP), deepcopy(DEMO_MA_ALL_GROUPS))
    _st.set_ma_connected(True)
    _st.replace_ma_now_playing({queue_id: deepcopy(data) for queue_id, data in DEMO_MA_NOW_PLAYING.items()})
    _st.set_update_available(deepcopy(DEMO_UPDATE_INFO))

    demo_dbus_path = "/tmp/sendspin-demo-dbus.sock"
    try:
        with open(demo_dbus_path, "a", encoding="utf-8"):
            pass
    except OSError:
        logger.debug("Could not create demo D-Bus marker at %s", demo_dbus_path)
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = f"unix:path={demo_dbus_path}"

    bluetooth_manager.BluetoothManager = DemoBluetoothManager  # type: ignore[misc,assignment]
    # Patch the local binding in bridge.client (imported via `from X import Y`).
    import sendspin_bridge.bridge.client as _bridge_client_mod

    _bridge_client_mod.BluetoothManager = DemoBluetoothManager  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 2. Patch services.bluetooth
    # ------------------------------------------------------------------
    import sendspin_bridge.services.bluetooth as _sbt

    _original_bt_remove_device = _sbt.bt_remove_device
    _original_list_bt_adapters = _sbt.list_bt_adapters
    _original_is_audio_device = _sbt.is_audio_device
    _audio_macs = {d["mac"] for d in DEMO_SCAN_RESULTS}
    _sbt.list_bt_adapters = (  # type: ignore[assignment]
        lambda timeout=5: (
            [str(adapter["mac"]) for adapter in DEMO_ADAPTERS]
            if _demo_enabled()
            else _original_list_bt_adapters(timeout=timeout)
        )
    )
    _sbt.is_audio_device = (  # type: ignore[assignment]
        lambda mac: mac.upper() in _audio_macs if _demo_enabled() else _original_is_audio_device(mac)
    )

    def _demo_bt_remove_device(mac: str, adapter_mac: str = "") -> None:
        if not _demo_enabled():
            _original_bt_remove_device(mac, adapter_mac)
            return
        del adapter_mac
        nonlocal _demo_runtime_paired_devices, _demo_runtime_bt_device_info
        normalized = str(mac or "").strip().upper()
        with _demo_state_lock:
            _demo_runtime_paired_devices = [
                item for item in _demo_runtime_paired_devices if str(item.get("mac") or "").upper() != normalized
            ]
            _demo_runtime_bt_device_info = _build_demo_bt_device_info(_demo_runtime_paired_devices)

    _sbt.bt_remove_device = _demo_bt_remove_device

    # ------------------------------------------------------------------
    # 3. Patch services.pulse (the audio commands behind the Hardware page)
    # ------------------------------------------------------------------
    # Volume/mute/suspend/default-sink state is shared with the Hardware page's
    # audio half, so a command and a read describe the same sink.
    _demo_install_audio()

    # ------------------------------------------------------------------
    # 4. Patch SendspinClient methods
    # ------------------------------------------------------------------
    import sendspin_bridge.bridge.client as _client_mod

    _SendspinClient = _client_mod.SendspinClient

    async def _demo_start_sendspin_inner(self: Any) -> None:
        """Set status as if connected — no real subprocess spawned."""
        await self.stop_sendspin()

        mac = self.bt_manager.mac_address if self.bt_manager else None
        initial = DEMO_DEVICE_STATUS.get(mac or "", {})

        self.player_id = demo_player_id_for_name(self.player_name)
        self.bt_management_enabled = bool(initial.get("bt_management_enabled", True))
        # Sentinel so is_running() returns True (checks returncode is None)
        fake_pid = 9000 + next(
            (
                idx
                for idx, device in enumerate(DEMO_DEVICES, start=1)
                if device.get("player_name") == self.player_name.split(" @ ", 1)[0]
            ),
            0,
        )
        daemon_should_run = bool(
            initial.get("connected", True) or initial.get("server_connected", False) or initial.get("stopping", False)
        )
        self._daemon_proc = (
            type("_FakeProc", (), {"returncode": None, "pid": fake_pid})() if daemon_should_run else None
        )
        self._daemon_task = None
        self._stderr_task = None

        bt_connected = bool(initial.get("bluetooth_connected", False))
        server_connected = bool(initial.get("server_connected", bt_connected))
        runtime_connected = bool(initial.get("connected", server_connected))

        self.bluetooth_sink_name = None
        if mac and bt_connected:
            self.bluetooth_sink_name = f"bluez_output.{mac.replace(':', '_')}.1"

        is_playing = bool(initial.get("playing", False))
        self._update_status(
            {
                "server_connected": server_connected,
                "connected": runtime_connected,
                "playing": is_playing,
                "audio_streaming": is_playing and server_connected,
                "volume": initial.get("volume", 100),
                "muted": initial.get("muted", False),
                "audio_format": initial.get("audio_format"),
                "current_track": initial.get("current_track"),
                "current_artist": initial.get("current_artist"),
                "track_duration_ms": initial.get("track_duration_ms"),
                "track_progress_ms": initial.get("track_progress_ms"),
                "battery_level": initial.get("battery_level"),
                "bluetooth_connected": bt_connected,
                "bluetooth_available": True,
                "buffering": initial.get("buffering", False),
                "reanchor_count": initial.get("reanchor_count", 0),
                "reanchor_count_5m": initial.get("reanchor_count_5m", 0),
                "reanchor_count_30m": initial.get("reanchor_count_30m", 0),
                "last_sync_error_ms": initial.get("last_sync_error_ms"),
                "last_reanchor_at": initial.get("last_reanchor_at"),
                "reanchoring": initial.get("reanchoring", False),
                "reconnecting": initial.get("reconnecting", False),
                "reconnect_attempt": initial.get("reconnect_attempt", 0),
                "stopping": initial.get("stopping", False),
                "bt_management_enabled": self.bt_management_enabled,
                "bt_released_by": initial.get("bt_released_by"),
                "group_id": initial.get("group_id"),
                "group_name": initial.get("group_name"),
                "rssi_dbm": initial.get("rssi_dbm"),
                "rssi_at_ts": time.time() if initial.get("rssi_dbm") is not None else None,
                "bt_codec_name": initial.get("bt_codec_name"),
                "bt_reported_delay_ms": initial.get("bt_reported_delay_ms"),
                "bt_delay_reporting_supported": initial.get("bt_delay_reporting_supported", False),
                "suggested_static_delay_ms": initial.get("suggested_static_delay_ms"),
                "latency_suggestion_source": initial.get("latency_suggestion_source", "unavailable"),
                "latency_suggestion_confidence": initial.get("latency_suggestion_confidence", "none"),
                "latency_suggestion_explanation": initial.get("latency_suggestion_explanation", ""),
                "latency_suggestion_revision": initial.get("latency_suggestion_revision"),
                "latency_double_count_risk": initial.get("latency_double_count_risk", False),
                "timing_metrics_available": initial.get("timing_metrics_available", False),
                "backend_output_latency_ms": initial.get("backend_output_latency_ms"),
                "buffered_audio_ms": initial.get("buffered_audio_ms"),
                "playback_sync_error_ms": initial.get("playback_sync_error_ms"),
                "clock_synchronized": initial.get("clock_synchronized", False),
                "clock_offset_ms": initial.get("clock_offset_ms"),
                "clock_uncertainty_ms": initial.get("clock_uncertainty_ms"),
                "timing_sampled_at": initial.get("timing_sampled_at"),
            }
        )
        logger.info("[demo] Player '%s' started (no subprocess)", self.player_name)

    async def _demo_send_command(self: Any, cmd: dict) -> None:
        """Handle subprocess commands locally."""
        action = cmd.get("cmd")
        if action == "set_volume":
            self._update_status({"volume": cmd.get("value", 100)})
        elif action == "set_mute":
            self._update_status({"muted": cmd.get("muted", cmd.get("value", False))})
        elif action in ("pause", "play"):
            is_play = action == "play"
            self._update_status({"playing": is_play, "audio_streaming": is_play})
            # Propagate to other group members (in real system MA does this via WS)
            gid = self.status.get("group_id")
            if gid:
                with _st.clients_lock:
                    peers = list(_st.clients)
                for peer in peers:
                    if peer is not self and peer.status.get("group_id") == gid:
                        peer._update_status({"playing": is_play, "audio_streaming": is_play})
        elif action == "stop":
            self._update_status({"server_connected": False, "playing": False, "audio_streaming": False})

    async def _demo_stop_sendspin(self: Any) -> None:
        """Update status without touching a real subprocess."""
        self._daemon_proc = None
        self._daemon_task = None
        self._stderr_task = None
        self._update_status(
            {
                "server_connected": False,
                "connected": False,
                "playing": False,
                "audio_streaming": False,
                "current_track": None,
                "current_artist": None,
                "audio_format": None,
                "reanchoring": False,
                "group_name": None,
                "group_id": None,
            }
        )

    _SendspinClient._start_sendspin_inner = _demo_start_sendspin_inner
    _SendspinClient._send_subprocess_command = _demo_send_command

    async def _demo_play_calibration_tone(self: Any) -> bool:
        logger.info("[demo] Calibration clicks played on %s", self.player_name)
        return bool(self.status.get("bluetooth_connected"))

    _SendspinClient.play_calibration_tone = _demo_play_calibration_tone
    _SendspinClient.stop_sendspin = _demo_stop_sendspin

    # ------------------------------------------------------------------
    # 5. Patch the Bluetooth use cases (discovery, pairing, adapters)
    # ------------------------------------------------------------------
    # The v3 API reads BlueZ through application.bluetooth, not through the
    # deleted Flask route module.  Patching the use cases keeps the job
    # machinery, the adapter lease and the response models intact while the
    # answers come from the fixture stand.
    import sendspin_bridge.application.bluetooth as _app_bt

    _original_scan = _app_bt._scan
    _original_run_standalone_pair = _app_bt._run_standalone_pair
    _demo_adapter_macs = [str(adapter["mac"]) for adapter in DEMO_ADAPTERS]

    def _demo_list_bt_adapters(timeout: int = 5) -> list[str]:
        return list(_demo_adapter_macs) if _demo_enabled() else _original_list_bt_adapters(timeout=timeout)

    _sbt.list_bt_adapters = _demo_list_bt_adapters  # type: ignore[assignment]
    _app_bt.list_bt_adapters = _demo_list_bt_adapters  # type: ignore[assignment]
    # The adapter ids are synthetic hciN labels in fixture order; sysfs on a
    # dev host would otherwise re-map them to whatever radio is installed.
    _app_bt.build_hci_map = lambda: {}  # type: ignore[assignment]
    _app_bt._last_scan_completed = 0.0

    def _demo_scan(adapter: str, audio_only: bool) -> dict[str, Any]:
        if not _demo_enabled():
            return _original_scan(adapter, audio_only)
        time.sleep(1.5)
        selected_adapter = str(get_demo_adapter(adapter)["id"]) if adapter else ""
        excluded_macs = _current_demo_excluded_scan_macs()
        with _demo_state_lock:
            devices: list[dict[str, Any]] = []
            for item in _demo_runtime_scan_results:
                if selected_adapter and str(item.get("adapter") or "") != selected_adapter:
                    continue
                entry: dict[str, Any] = deepcopy(item)
                mac = str(entry.get("mac") or "").upper()
                if mac in excluded_macs:
                    continue
                entry.setdefault("audio_capable", True)
                if audio_only and entry.get("audio_capable") is False:
                    continue
                entry.setdefault("supports_import", entry.get("audio_capable", True))
                entry.setdefault("kind", "audio" if entry.get("audio_capable", True) else "other")
                devices.append(entry)
        return {
            "devices": devices,
            "stats": {
                "total_candidates": len(devices),
                "returned_candidates": len(devices),
                "audio_candidates": sum(1 for item in devices if item.get("audio_capable", True)),
                "audio_only": audio_only,
            },
        }

    def _demo_run_standalone_pair(
        mac: str,
        adapter: str,
        *,
        quiesce_adapter: bool = False,
        no_input_no_output_agent: bool = False,
        allow_hfp_profile: bool = False,
    ) -> dict[str, Any]:
        if not _demo_enabled():
            return _original_run_standalone_pair(
                mac,
                adapter,
                quiesce_adapter=quiesce_adapter,
                no_input_no_output_agent=no_input_no_output_agent,
                allow_hfp_profile=allow_hfp_profile,
            )
        del quiesce_adapter, no_input_no_output_agent, allow_hfp_profile
        nonlocal _demo_runtime_paired_devices, _demo_runtime_bt_device_info
        normalized = str(mac or "").strip().upper()
        del adapter  # the demo stand pairs on whichever controller owns the device
        time.sleep(2.0)
        device = _find_demo_device(normalized)
        if not device:
            from sendspin_bridge.application.errors import UseCaseError

            raise UseCaseError(422, "pairing_failed", "Demo device not found")
        with _demo_state_lock:
            existing = next(
                (item for item in _demo_runtime_paired_devices if str(item.get("mac") or "").upper() == normalized),
                None,
            )
            if existing is None:
                _demo_runtime_paired_devices.append(
                    {
                        "mac": normalized,
                        "name": str(device.get("name") or normalized),
                        "connected": False,
                    }
                )
                _demo_runtime_paired_devices.sort(
                    key=lambda item: str(item.get("name") or item.get("mac") or "").lower()
                )
            _demo_runtime_bt_device_info = _build_demo_bt_device_info(_demo_runtime_paired_devices)
        logger.info("[demo] Standalone pair %s: OK", normalized)
        return {"mac": normalized, "paired": True, "agent_telemetry": {}}

    _app_bt._scan = _demo_scan
    _app_bt._run_standalone_pair = _demo_run_standalone_pair

    def _demo_list_adapters() -> list[dict[str, Any]]:
        return [
            {
                "id": str(adapter["id"]),
                "mac": str(adapter["mac"]),
                "name": str(adapter["name"]),
                "powered": bool(adapter.get("powered", True)),
                "live_class": None,
            }
            for adapter in DEMO_ADAPTERS
        ]

    _app_bt.list_adapters = _demo_list_adapters

    def _demo_known_devices(*, named_only: bool = True) -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = []
        for item in _current_demo_paired_devices():
            mac = str(item.get("mac") or "").upper()
            if not mac:
                continue
            if named_only and not item.get("name"):
                continue
            devices.append({"mac": mac, "name": str(item.get("name") or mac), "adapters": []})
        return devices

    def _demo_device_info(mac: str, adapter: str = "") -> dict[str, Any]:
        del adapter
        normalized = str(mac or "").strip().upper()
        info = next((item for item in _current_demo_bt_device_info() if item["mac"] == normalized), None)
        if info is None:
            return {"mac": normalized, "raw": []}
        fields = {key: value for key, value in info.items() if key != "mac"}
        raw = [f"Device {info['mac']} {info['name']}"] + [f"\t{key}: {value}" for key, value in fields.items()]
        return {"mac": info["mac"], "raw": raw, **fields}

    _app_bt.known_devices = _demo_known_devices
    _app_bt.device_info = _demo_device_info

    # ------------------------------------------------------------------
    # 6. Patch state module
    # ------------------------------------------------------------------
    _st.get_adapter_name = lambda mac: _adapter_names_by_mac.get(str(mac).upper())  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 7. Config, runtime label, updates and the emulated restart
    # ------------------------------------------------------------------
    # The config layer resolves CONFIG_FILE at call time, so redirecting the
    # module global is enough for load_config/update_config/write_config_file
    # everywhere.  ``application.config`` and the HA command helpers bound the
    # value at import time and are redirected explicitly.
    import sendspin_bridge.application.bridge_control as _bridge_control
    import sendspin_bridge.application.config as _app_config
    import sendspin_bridge.services.bluetooth.bt_commands as _bt_commands

    _app_config.CONFIG_FILE = _demo_config_file  # type: ignore[assignment]
    _bt_commands.CONFIG_FILE = _demo_config_file  # type: ignore[assignment]
    _original_detect_runtime = _app_config._detect_runtime
    _app_config._detect_runtime = lambda: "systemd" if _demo_enabled() else _original_detect_runtime()  # type: ignore[assignment]
    _original_bridge_detect_runtime = _bridge_control.detect_runtime
    _bridge_control.detect_runtime = (  # type: ignore[assignment]
        lambda: "systemd" if _demo_enabled() else _original_bridge_detect_runtime()
    )
    _original_detect_installation = _st.detect_installation_type
    _st.detect_installation_type = (  # type: ignore[assignment]
        lambda: "systemd" if _demo_enabled() else _original_detect_installation()
    )

    _original_sync_ha_options = _app_config._sync_ha_options
    _app_config._sync_ha_options = (  # type: ignore[assignment]
        lambda config: None if _demo_enabled() else _original_sync_ha_options(config)
    )
    _original_read_log_lines = _app_config._read_log_lines
    _app_config._read_log_lines = (  # type: ignore[assignment]
        lambda runtime, lines: (
            list(DEMO_LOG_LINES)[-lines:] if _demo_enabled() else _original_read_log_lines(runtime, lines)
        )
    )

    def _reset_demo_runtime_state() -> None:
        nonlocal _demo_runtime_scan_results, _demo_runtime_paired_devices, _demo_runtime_bt_device_info
        with _demo_state_lock:
            _demo_runtime_scan_results = deepcopy(DEMO_SCAN_RESULTS)
            _demo_runtime_paired_devices = deepcopy(DEMO_PAIRED_DEVICES)
            _demo_runtime_bt_device_info = _build_demo_bt_device_info(_demo_runtime_paired_devices)
        _seed_demo_config()

    def _simulate_demo_restart() -> None:
        if not _demo_enabled():
            return
        from sendspin_bridge.bridge.state import (
            complete_startup_progress,
            reset_startup_progress,
            update_startup_progress,
        )

        reset_startup_progress(6, message="Demo restart initiated")
        update_startup_progress(
            "shutdown",
            "Restart in progress…",
            current_step=6,
            total_steps=6,
            status="stopping",
            details={"demo_mode": True, "emulated_restart": True},
        )
        _reset_demo_runtime_state()
        time.sleep(0.35)
        reset_startup_progress(6, message="Demo startup initiated")
        update_startup_progress(
            "sendspin_bridge.config",
            "Loading demo configuration",
            current_step=1,
            details={"demo_mode": True, "emulated_restart": True},
        )
        time.sleep(0.2)
        update_startup_progress(
            "devices",
            "Refreshing demo device registry",
            current_step=3,
            details={"configured_devices": len(DEMO_DEVICES), "emulated_restart": True},
        )
        time.sleep(0.2)
        update_startup_progress(
            "integrations",
            "Restoring demo integrations",
            current_step=5,
            details={"ma_configured": True, "ma_monitor_enabled": True, "emulated_restart": True},
        )
        time.sleep(0.15)
        complete_startup_progress(
            "Demo restart complete",
            details={
                "active_clients": len(_st.clients),
                "ma_monitor_enabled": True,
                "demo_mode": True,
                "emulated_restart": True,
            },
        )

    def _demo_restart_override() -> None:
        if not _demo_enabled():
            return

        def _run_demo_restart() -> None:
            time.sleep(0.1)
            _simulate_demo_restart()

        threading.Thread(target=_run_demo_restart, daemon=True, name="demo-restart").start()

    _bridge_control.restart_override = _demo_restart_override

    # ------------------------------------------------------------------
    # 8. Patch diagnostics / bugreport helpers
    # ------------------------------------------------------------------
    import sendspin_bridge.application.diagnostics as _diag

    def _demo_collect_subprocess_info() -> list[dict]:
        info = []
        for idx, client in enumerate(_st.clients, start=1):
            status = getattr(client, "status", {}) or {}
            is_running = (
                client.is_running() if hasattr(client, "is_running") else bool(getattr(client, "_daemon_proc", None))
            )
            info.append(
                {
                    "name": getattr(client, "player_name", "?"),
                    "pid": getattr(getattr(client, "_daemon_proc", None), "pid", 9000 + idx),
                    "alive": bool(is_running),
                    "running": bool(is_running),
                    "restart_delay": getattr(client, "restart_delay", 1.0),
                    "zombie_restarts": getattr(client, "_zombie_restart_count", 0),
                    "reconnecting": status.get("reconnecting", False),
                    "reconnect_attempt": status.get("reconnect_attempt", 0),
                    "last_error": status.get("last_error"),
                    "last_error_at": status.get("last_error_at"),
                    "process_rss_mb": None,
                }
            )
        return info

    def _demo_collect_preflight_status() -> dict:
        """The host diagnostics probes against, answered by the fixture stand."""
        paired = len(_current_demo_paired_devices())
        return {
            "status": "ok",
            "failed_collections": [],
            "collections_status": {
                "audio": {"status": "ok", "count": len(DEMO_DEVICES)},
                "bluetooth": {"status": "ok", "count": paired},
                "memory": {"status": "ok"},
                "config_writable": {"status": "ok"},
            },
            "platform": "demo",
            "audio": {
                "system": "pulseaudio",
                "socket": "unix:/tmp/sendspin-demo-pulse.sock",
                "socket_exists": True,
                "socket_reachable": True,
                "sinks": len(DEMO_DEVICES),
                "last_error": None,
            },
            "bluetooth": {
                "controller": True,
                "adapter": DEMO_ADAPTERS[0]["mac"],
                "paired_devices": paired,
                "daemon": "active",
            },
            "dbus": True,
            "memory_mb": 512,
            "config_writable": {
                "status": "ok",
                "writable": True,
                "remediation": None,
                "config_dir": str(_demo_config_dir),
                "uid": getattr(os, "getuid", lambda: 0)(),
            },
            "version": _diag.VERSION,
        }

    _original_collect_preflight_status = _diag._collect_preflight_status
    _diag._collect_preflight_status = (  # type: ignore[assignment]
        lambda: _demo_collect_preflight_status() if _demo_enabled() else _original_collect_preflight_status()
    )
    # The sampler may have measured the real host before demo mode installed.
    _diag.reset_preflight_probe()
    _diag._collect_recent_logs = lambda n=100: list(DEMO_LOG_LINES)[-n:]  # type: ignore[assignment]
    _original_collect_bt_device_info = _diag._collect_bt_device_info
    _diag._collect_bt_device_info = (  # type: ignore[assignment]
        lambda: _current_demo_bt_device_info() if _demo_enabled() else _original_collect_bt_device_info()
    )
    _diag._collect_subprocess_info = _demo_collect_subprocess_info  # type: ignore[assignment]

    # Never import the real audio modules in demo mode.  PortAudio probing can
    # block indefinitely on a headless host before we get a chance to patch
    # ``query_devices``.  Lightweight modules are sufficient for diagnostics.
    sendspin_pkg = sys.modules.get("sendspin")
    if sendspin_pkg is None:
        sendspin_pkg = ModuleType("sendspin")
        sendspin_pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["sendspin"] = sendspin_pkg
    _sendspin_audio = sys.modules.get("sendspin.audio") or ModuleType("sendspin.audio")
    sys.modules["sendspin.audio"] = _sendspin_audio
    cast("Any", sendspin_pkg).audio = _sendspin_audio
    _sendspin_audio.query_devices = lambda: [SimpleNamespace(**device) for device in DEMO_PORTAUDIO_DEVICES]

    _sendspin_audio_devices = sys.modules.get("sendspin.audio_devices") or ModuleType("sendspin.audio_devices")
    sys.modules["sendspin.audio_devices"] = _sendspin_audio_devices
    cast("Any", sendspin_pkg).audio_devices = _sendspin_audio_devices
    _sendspin_audio_devices.query_devices = lambda: [SimpleNamespace(**device) for device in DEMO_PORTAUDIO_DEVICES]

    # ------------------------------------------------------------------
    # 9. Patch MA client (discover_ma_groups)
    # ------------------------------------------------------------------
    import sendspin_bridge.services.music_assistant.ma_client as _ma_client

    async def _demo_discover_ma_groups(
        ma_url: str,
        ma_token: str,
        bridge_players: list[dict | str],
    ) -> tuple[dict, list]:
        # Build id_map matching actual bridge players to demo groups
        id_map: dict[str, dict] = {}
        for item in bridge_players:
            if isinstance(item, str):
                pid, name = "", item
            else:
                pid, name = item.get("player_id", ""), item.get("player_name", "")
            base = name.split(" @ ")[0].lower() if " @ " in name else name.lower()
            if base in DEMO_MA_NAME_MAP:
                key = pid if pid else base
                id_map[key] = DEMO_MA_NAME_MAP[base]
        logger.info("[demo] MA group discovery: %d groups, %d mapped", len(DEMO_MA_ALL_GROUPS), len(id_map))
        return id_map, list(DEMO_MA_ALL_GROUPS)

    _ma_client.discover_ma_groups = _demo_discover_ma_groups  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 10. Patch MA monitor (start_monitor, send_queue_cmd)
    # ------------------------------------------------------------------
    import sendspin_bridge.services.music_assistant.ma_monitor as _ma_monitor

    def _seconds_value_to_ms(value: object, default: int | None) -> int | None:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return int(float(value) * 1000)
        if isinstance(value, str):
            try:
                return int(float(value) * 1000)
            except ValueError:
                return default
        return default

    def _sync_queue_state_to_clients(queue_id: str, now_playing: dict[str, object]) -> None:
        """Mirror demo queue changes back onto runtime device status fields."""
        with _st.clients_lock:
            peers = list(_st.clients)
        track_duration_ms = _seconds_value_to_ms(now_playing.get("duration"), None)
        track_progress_ms = _seconds_value_to_ms(now_playing.get("elapsed"), 0) or 0
        for peer in peers:
            if peer.status.get("group_id") == queue_id or getattr(peer, "player_id", None) == queue_id:
                peer._update_status(
                    {
                        "current_track": now_playing.get("track"),
                        "current_artist": now_playing.get("artist"),
                        "track_duration_ms": track_duration_ms,
                        "track_progress_ms": track_progress_ms,
                        "playing": now_playing.get("state") == "playing",
                        "audio_streaming": now_playing.get("state") == "playing"
                        and peer.status.get("server_connected"),
                    }
                )

    class _DemoMaMonitor:
        """Fake MA monitor that sets state as connected and populates now-playing."""

        async def run(self) -> None:
            import time as _time

            _st.set_ma_connected(True)
            # Populate initial now-playing
            for sg_id, np_data in DEMO_MA_NOW_PLAYING.items():
                data = dict(np_data)
                data["elapsed_updated_at"] = _time.time()
                _st.set_ma_now_playing_for_group(sg_id, data)
            _st.set_ma_server_version(str(DEMO_MA_SERVER_INFO["version"]))
            logger.info("[demo] MA monitor connected (simulated)")
            # Keep alive
            while True:
                await asyncio.sleep(30)

    def _demo_start_monitor(ma_url: str, ma_token: str) -> _DemoMaMonitor:
        return _DemoMaMonitor()

    _ma_monitor.start_monitor = _demo_start_monitor  # type: ignore[assignment]

    async def _demo_send_queue_cmd(
        action: str,
        value: Any = None,
        syncgroup_id: str | None = None,
        player_id: str | None = None,
    ) -> dict[str, object]:
        """Handle queue commands locally — update now-playing state."""
        del player_id
        sg_id = syncgroup_id or next(iter(DEMO_MA_NOW_PLAYING), None)
        if not sg_id:
            return {"accepted": False, "queue_id": "", "error": "no queue available"}

        np = _st.get_ma_now_playing_for_group(sg_id) or {}
        queue_name = str(np.get("syncgroup_name") or sg_id)
        current_index = int(np.get("queue_index", 0) or 0)
        connected = bool(np.get("connected", True))
        shuffle = bool(np.get("shuffle", False))
        repeat = str(np.get("repeat", "off") or "off")

        if action == "next":
            idx = (current_index + 1) % len(DEMO_TRACKS)
            np.update(
                _ma_now_playing_entry(
                    sg_id,
                    queue_name,
                    idx,
                    state="playing",
                    connected=connected,
                    elapsed_seconds=0,
                    shuffle=shuffle,
                    repeat=repeat,
                )
            )
        elif action == "previous":
            idx = max(0, current_index - 1)
            np.update(
                _ma_now_playing_entry(
                    sg_id,
                    queue_name,
                    idx,
                    state="playing",
                    connected=connected,
                    elapsed_seconds=0,
                    shuffle=shuffle,
                    repeat=repeat,
                )
            )
        elif action == "shuffle":
            np["shuffle"] = bool(value)
        elif action == "repeat":
            np["repeat"] = value or "off"
        elif action == "seek":
            np["elapsed"] = int(value or 0)

        import time as _time

        accepted_at = _time.time()
        np["elapsed_updated_at"] = accepted_at
        _st.set_ma_now_playing_for_group(sg_id, np)
        _sync_queue_state_to_clients(sg_id, np)
        logger.debug("[demo] queue cmd: %s value=%s → %s", action, value, sg_id)
        return {
            "accepted": True,
            "queue_id": sg_id,
            "syncgroup_id": sg_id,
            "accepted_at": accepted_at,
            "ack_latency_ms": 0,
        }

    _ma_monitor.send_queue_cmd = _demo_send_queue_cmd

    async def _demo_send_player_cmd(command: str, args: dict) -> bool:
        """Accept any player command in demo mode (volume_set, volume_mute, etc.)."""
        logger.debug("[demo] send_player_cmd: %s %s", command, args)
        return True

    _ma_monitor.send_player_cmd = _demo_send_player_cmd

    # Also patch ma_group_play (used by group/pause play action)
    async def _demo_ma_group_play(ma_url: str, ma_token: str, syncgroup_id: str) -> bool:
        logger.debug("[demo] ma_group_play: %s", syncgroup_id)
        return True

    _ma_client.ma_group_play = _demo_ma_group_play

    # ------------------------------------------------------------------
    # 11. Patch MA discovery (validate_ma_url, discover_ma_servers)
    # ------------------------------------------------------------------
    import sendspin_bridge.services.diagnostics.update_checker as _update_checker
    import sendspin_bridge.services.music_assistant.ma_discovery as _ma_disc

    async def _demo_validate_ma_url(url: str) -> dict | None:
        return dict(DEMO_MA_SERVER_INFO)

    async def _demo_discover_ma_servers(timeout: float = 5.0) -> list[dict]:
        await asyncio.sleep(1.0)  # simulate mDNS delay
        return [dict(DEMO_MA_SERVER_INFO)]

    _ma_disc.validate_ma_url = _demo_validate_ma_url
    if hasattr(_ma_disc, "discover_ma_servers"):
        _ma_disc.discover_ma_servers = _demo_discover_ma_servers

    async def _demo_check_latest_version(channel: str | None = None) -> dict[str, object]:
        payload = dict(DEMO_UPDATE_INFO)
        payload["channel"] = channel or str(DEMO_UPDATE_INFO.get("channel", "stable"))
        return payload

    _update_checker.check_latest_version = _demo_check_latest_version
    _app_config.check_latest_version = _demo_check_latest_version  # type: ignore[assignment]

    install_inventory()
    logger.info("🎭 Demo mode installed — all hardware interactions are simulated")


def _demo_install_source() -> None:
    """Point the phone source's runtime snapshot and lifecycle at the stand.

    The read models (``/hardware``'s source strip and ``/status``) read the
    source's runtime snapshot, and the orchestrator starts the real capture
    server from config.  Publishing the fixture node and serving start/stop
    locally keeps both surfaces truthful without opening a capture pipeline.
    """
    from sendspin_bridge.services.source import manager as _source_manager
    from sendspin_bridge.services.source import state as _source_state

    _demo_publish_source(True)

    async def _demo_source_start(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        _demo_publish_source(True)
        return {"started": True, "server": "ws://demo-ma.local:8927/sendspin"}

    async def _demo_source_stop() -> dict[str, Any]:
        _demo_publish_source(False)
        return {"started": False}

    class _DemoSourceClient:
        """The pairing surface the source panel uses, served by the stand."""

        async def open_ma_pairing(self) -> dict[str, Any]:
            return {"code": "12345678", "seconds_left": 300, "client_id": "demo"}

        async def cancel_ma_pairing(self, *, forget: bool = False) -> None:
            del forget
            _demo_publish_source(True)

    _source_manager.start = _demo_source_start  # type: ignore[assignment]
    _source_manager.stop = _demo_source_stop  # type: ignore[assignment]
    _source_manager.running = lambda: _source_state.snapshot().enabled  # type: ignore[assignment]
    _source_manager.client = lambda: _DemoSourceClient()  # type: ignore[assignment]

    # The pairing window normally opens discoverable/pairable mode through the
    # real BlueZ agent.  The demo has no adapter to open, so it publishes the
    # window on the same typed snapshot the status read uses: the strip counts
    # down, the countdown survives a page reload, and "Stop pairing" closes it.
    from demo.fixtures import DEMO_ADAPTERS
    from sendspin_bridge.services.source import pairing as _source_pairing

    async def _demo_open_window(seconds: int = _source_pairing.PAIRING_WINDOW_S) -> dict[str, Any]:
        _source_state.publish(
            pairing_until=time.time() + max(30, int(seconds)),
            pairing_adapters=(str(DEMO_ADAPTERS[0]["mac"]),),
            pairing_error=None,
        )
        return _source_pairing.pairing_status()

    async def _demo_close_window() -> dict[str, Any]:
        _source_state.publish(pairing_until=0.0, pairing_adapters=(), pairing_error=None)
        return _source_pairing.pairing_status()

    _source_pairing.open_window = _demo_open_window  # type: ignore[assignment]
    _source_pairing.close_window = _demo_close_window  # type: ignore[assignment]


def install_inventory() -> None:
    """Point the hardware reader, the audio commands and the source at the demo fixtures.

    The orchestrator installs this *after* the D-Bus resolver, so a later
    ``set_bluez()`` cannot put the real host back in front of the page.  The
    reader's refresh is replaced rather than its transport, so no bus and no
    ``bluetoothctl`` is ever asked — the demo stand is the only answer.  The
    audio helpers and the source runtime are installed here too: a test that
    only wants the inventory still gets working volume, mute, suspend and
    source reads.
    """
    from sendspin_bridge.services.hardware import inventory as _inventory

    _demo_install_audio()
    _demo_install_source()
    _inventory._refresh = _demo_inventory_refresh
    _inventory.clear_cache()


def _demo_inventory_refresh():
    """Build one :class:`InventoryRead` from the demo fixture stand."""
    from datetime import UTC, datetime

    from demo.fixtures import (
        DEMO_ADAPTERS,
        DEMO_BT_DEVICE_INFO,
        DEMO_DEVICE_STATUS,
        DEMO_DEVICES,
    )
    from sendspin_bridge.bluetooth.address import DeviceAddress
    from sendspin_bridge.services.hardware import inventory as _inventory
    from sendspin_bridge.services.hardware.capabilities import build_capabilities
    from sendspin_bridge.services.hardware.inventory import (
        ControllerFacts,
        DeviceFacts,
        InventoryRead,
    )
    from sendspin_bridge.services.hardware.roles import DeviceLink, infer_roles

    def _yes(value: object) -> bool:
        return str(value or "").strip().lower() in ("yes", "1", "true")

    adapter_by_id = {str(adapter["id"]): adapter for adapter in DEMO_ADAPTERS}
    adapter_for_device: dict[str, dict] = {}
    for device in DEMO_DEVICES:
        address = DeviceAddress.parse(device.get("mac"))
        if address is None:
            continue
        adapter_for_device[address.colons] = adapter_by_id.get(str(device.get("adapter")), DEMO_ADAPTERS[0])

    devices_by_adapter: dict[str, list[str]] = {}
    devices: list[DeviceFacts] = []
    for entry in DEMO_BT_DEVICE_INFO:
        address = DeviceAddress.parse(entry.get("mac"))
        if address is None:
            continue
        mac = address.colons
        adapter = adapter_for_device.get(mac, DEMO_ADAPTERS[0])
        devices_by_adapter.setdefault(str(adapter["id"]), []).append(mac)
        status = DEMO_DEVICE_STATUS.get(mac, {})
        connected = _yes(entry.get("connected"))
        transport_state = "active" if connected and bool(status.get("audio_streaming")) else None
        battery = status.get("battery_level")
        rssi = status.get("rssi_dbm")
        devices.append(
            DeviceFacts(
                mac=mac,
                name=str(entry.get("name") or mac),
                alias=str(entry.get("name") or mac),
                paired=_yes(entry.get("paired")),
                bonded=_yes(entry.get("bonded")),
                trusted=_yes(entry.get("trusted")),
                blocked=_yes(entry.get("blocked")),
                connected=connected,
                controller_macs=(str(adapter["mac"]).upper(),),
                uuids=(),
                modalias=None,
                services_resolved=None,
                battery_pct=int(battery) if isinstance(battery, int) else None,
                rssi_delta_db=int(rssi) if isinstance(rssi, int) else None,
                rssi_at=None,
                transport_state=transport_state,
                roles=tuple(infer_roles(DeviceLink(mac, transport_state=transport_state), (), None)),
            )
        )

    controllers = [
        ControllerFacts(
            id=str(adapter["id"]),
            id_synthetic=False,
            mac=str(adapter["mac"]).upper(),
            name=str(adapter["name"]),
            alias=str(adapter["name"]),
            powered=bool(adapter.get("powered", True)),
            present=True,
            class_of_device=None,
            device_macs=tuple(devices_by_adapter.get(str(adapter["id"]), ())),
        )
        for adapter in DEMO_ADAPTERS
    ]
    return InventoryRead(
        as_of=datetime.now(tz=UTC).isoformat(),
        stale=False,
        stale_after_s=int(_inventory._TTL_S),
        capabilities=build_capabilities(
            bluetooth=True,
            dbus=True,
            server_name="pulseaudio (demo)",
            capture=True,
            default_sink=True,
        ),
        controllers=tuple(controllers),
        devices=tuple(devices),
        degraded=(),
        audio=_demo_audio_facts(),
    )
