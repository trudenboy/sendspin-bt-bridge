"""Demo mode — run the web UI against a canonical nine-player demo stand.

Usage:
    DEMO_MODE=true python sendspin_client.py

All hardware-dependent layers (BlueZ, D-Bus, PulseAudio, Music Assistant,
and per-device subprocesses) are replaced with deterministic mocks so the
local demo is repeatable enough for documentation screenshots.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess as _real_subprocess
import sys
import time
from copy import deepcopy
from types import ModuleType, SimpleNamespace
from typing import Any, cast

logger = logging.getLogger(__name__)


def install() -> None:
    """Monkey-patch all hardware-dependent modules for demo mode.

    Must be called at the very beginning of ``main()`` before any
    BluetoothManager or SendspinClient instances are created and before
    ``web_interface`` is imported (so that route modules pick up the
    patched functions).
    """
    # ------------------------------------------------------------------
    # 1. Patch BluetoothManager class
    # ------------------------------------------------------------------
    import bluetooth_manager
    import state as _st
    from demo.bt_manager import DemoBluetoothManager
    from demo.fixtures import (
        DEMO_ADAPTER_NAMES,
        DEMO_ADAPTERS,
        DEMO_BT_DEVICE_INFO,
        DEMO_DEVICE_STATUS,
        DEMO_DEVICES,
        DEMO_DISPLAY_VERSION,
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
    # Also patch the already-imported name in __main__ (sendspin_client.py)
    _sc_mod = sys.modules.get("__main__")
    if not (_sc_mod and hasattr(_sc_mod, "SendspinClient")):
        raise RuntimeError("demo.install() must be called from sendspin_client.py main()")
    if hasattr(_sc_mod, "BluetoothManager"):
        _sc_mod.BluetoothManager = DemoBluetoothManager  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 2. Patch services.bluetooth
    # ------------------------------------------------------------------
    import services.bluetooth as _sbt

    _audio_macs = {d["mac"] for d in DEMO_SCAN_RESULTS}
    _sbt.list_bt_adapters = lambda timeout=5: [str(adapter["mac"]) for adapter in DEMO_ADAPTERS]
    _sbt.is_audio_device = lambda mac: mac.upper() in _audio_macs
    _sbt.bt_remove_device = lambda mac, adapter_mac="": None

    # ------------------------------------------------------------------
    # 3. Patch services.pulse (sync wrappers used by routes/api.py)
    # ------------------------------------------------------------------
    import services.pulse as _sp

    def _demo_status_for(mac: str) -> dict[str, Any]:
        status = DEMO_DEVICE_STATUS.get(mac)
        return status if isinstance(status, dict) else {}

    _sp.list_sinks = lambda: [
        {
            "name": f"bluez_output.{str(d['mac']).replace(':', '_')}.1",
            "description": d.get("player_name", d["mac"]),
        }
        for d in DEMO_DEVICES
        if _demo_status_for(str(d["mac"])).get("bluetooth_connected")
        and _demo_status_for(str(d["mac"])).get("bt_management_enabled", True)
    ]
    # Stateful sink volume/mute so API reads back correct values
    _demo_sink_vol: dict[str, int] = {
        f"bluez_output.{str(d['mac']).replace(':', '_')}.1": int(_demo_status_for(str(d["mac"])).get("volume", 100))
        for d in DEMO_DEVICES
    }
    _demo_sink_mute: dict[str, bool] = {
        f"bluez_output.{str(d['mac']).replace(':', '_')}.1": bool(_demo_status_for(str(d["mac"])).get("muted", False))
        for d in DEMO_DEVICES
    }

    def _sp_set_volume(sink_name: str, volume_pct: int) -> bool:
        _demo_sink_vol[sink_name] = volume_pct
        return True

    def _sp_get_volume(sink_name: str) -> int:
        return _demo_sink_vol.get(sink_name, 100)

    def _sp_set_mute(sink_name: str, muted: object) -> bool:
        _demo_sink_mute[sink_name] = bool(muted)
        return True

    def _sp_get_mute(sink_name: str) -> bool:
        return _demo_sink_mute.get(sink_name, False)

    _sp.set_sink_volume = _sp_set_volume
    _sp.get_sink_volume = _sp_get_volume
    _sp.set_sink_mute = _sp_set_mute
    _sp.get_sink_mute = _sp_get_mute
    _sp.get_sink_description = lambda sink_name: "Demo BT Speaker"
    _sp.get_server_name = lambda: "pulseaudio (demo)"

    # Also patch names already imported into routes.api (from X import Y binds locally)
    import routes.api as _api_mod

    _api_mod.set_sink_volume = _sp_set_volume
    _api_mod.set_sink_mute = _sp_set_mute
    _api_mod.get_sink_mute = _sp_get_mute

    import routes.api_status as _api_status_mod

    _api_status_mod.get_server_name = _sp.get_server_name
    _api_status_mod.list_sinks = _sp.list_sinks

    # ------------------------------------------------------------------
    # 4. Patch SendspinClient methods
    # ------------------------------------------------------------------
    _SendspinClient = _sc_mod.SendspinClient

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
                "reconnecting": initial.get("reconnecting", False),
                "reconnect_attempt": initial.get("reconnect_attempt", 0),
                "stopping": initial.get("stopping", False),
                "bt_management_enabled": self.bt_management_enabled,
                "bt_released_by": initial.get("bt_released_by"),
                "group_id": initial.get("group_id"),
                "group_name": initial.get("group_name"),
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
    _SendspinClient.stop_sendspin = _demo_stop_sendspin

    # ------------------------------------------------------------------
    # 5. Patch routes.api_bt (scan, paired, adapters)
    # ------------------------------------------------------------------
    import routes.api_bt as _abt

    def _demo_run_bt_scan(job_id: str) -> None:
        from state import finish_scan_job

        time.sleep(3.0)
        finish_scan_job(job_id, {"devices": list(DEMO_SCAN_RESULTS)})

    _abt._run_bt_scan = _demo_run_bt_scan
    _abt.list_bt_adapters = lambda timeout=5: [str(adapter["mac"]) for adapter in DEMO_ADAPTERS]

    # Replace subprocess module reference in api_bt so that handlers
    # calling subprocess.run(["bluetoothctl"], ...) get fake output.
    class _DemoSubprocess:
        """Intercept bluetoothctl subprocess calls in routes.api_bt."""

        def __getattr__(self, name: str) -> Any:
            return getattr(_real_subprocess, name)

        def run(self, args: Any, *a: Any, **kw: Any) -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            if args == ["bluetoothctl"]:
                input_text = kw.get("input", "")
                if "devices" in input_text:
                    return self._paired_output()
                if "show" in input_text:
                    return self._adapter_output(input_text)
                if "info " in input_text:
                    return self._info_output(input_text)
            if args == ["bluetoothctl", "list"]:
                return self._adapter_list_output()
            if args == ["bluetoothctl", "devices", "Paired"]:
                return self._paired_devices_output()
            if args == ["bluetoothctl", "--version"]:
                return _real_subprocess.CompletedProcess(args, 0, stdout="bluetoothctl: 5.72-demo\n", stderr="")
            if args == ["systemctl", "is-active", "bluetooth"]:
                return _real_subprocess.CompletedProcess(args, 0, stdout="active\n", stderr="")
            if args == ["pactl", "info"]:
                return _real_subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="Server Name: pulseaudio (demo)\nDefault Sink: bluez_output.AA_BB_CC_DD_EE_01.1\n",
                    stderr="",
                )
            if args == ["pactl", "list", "sink-inputs"]:
                return self._sink_inputs_output()
            if args == ["pulseaudio", "--version"]:
                return _real_subprocess.CompletedProcess(args, 0, stdout="pulseaudio 17.0-demo\n", stderr="")
            if args == ["pipewire", "--version"]:
                return _real_subprocess.CompletedProcess(args, 127, stdout="", stderr="pipewire not installed\n")
            if args[:3] == ["git", "describe", "--tags"]:
                return _real_subprocess.CompletedProcess(args, 0, stdout=f"{DEMO_DISPLAY_VERSION}\n", stderr="")
            return _real_subprocess.run(args, *a, **kw)

        @staticmethod
        def _paired_output() -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            lines = [f"Device {d['mac']} {d['name']}" for d in DEMO_PAIRED_DEVICES]
            return _real_subprocess.CompletedProcess(["bluetoothctl"], 0, stdout="\n".join(lines), stderr="")

        @staticmethod
        def _paired_devices_output() -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            lines = [f"Device {d['mac']} {d['name']}" for d in DEMO_PAIRED_DEVICES]
            return _real_subprocess.CompletedProcess(
                ["bluetoothctl", "devices", "Paired"], 0, stdout="\n".join(lines), stderr=""
            )

        @staticmethod
        def _adapter_list_output() -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            lines = []
            for idx, adapter in enumerate(DEMO_ADAPTERS):
                suffix = " [default]" if idx == 0 else ""
                lines.append(f"Controller {adapter['mac']} {adapter['name']}{suffix}")
            return _real_subprocess.CompletedProcess(["bluetoothctl", "list"], 0, stdout="\n".join(lines), stderr="")

        @staticmethod
        def _adapter_output(input_text: str) -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            selected = ""
            for line in str(input_text).splitlines():
                if line.startswith("select "):
                    selected = line.split(" ", 1)[1].strip()
                    break
            adapter_info = get_demo_adapter(selected)
            mac = str(adapter_info["mac"])
            name = str(adapter_info["name"])
            powered = "yes" if adapter_info.get("powered", True) else "no"
            pairable = "yes" if adapter_info.get("pairable", True) else "no"
            discoverable = "yes" if adapter_info.get("discoverable", False) else "no"
            stdout = (
                f"Controller {mac} {name}\n"
                f"\tPowered: {powered}\n"
                f"\tAlias: {name}\n"
                f"\tDiscoverable: {discoverable}\n"
                f"\tPairable: {pairable}\n"
            )
            return _real_subprocess.CompletedProcess(["bluetoothctl"], 0, stdout=stdout, stderr="")

        @staticmethod
        def _info_output(input_text: str) -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            selected_mac = ""
            for line in str(input_text).splitlines():
                if line.startswith("info "):
                    selected_mac = line.split(" ", 1)[1].strip().upper()
                    break
            device = next(
                (item for item in DEMO_BT_DEVICE_INFO if str(item.get("mac", "")).upper() == selected_mac),
                None,
            )
            if not device:
                return _real_subprocess.CompletedProcess(["bluetoothctl"], 0, stdout="", stderr="")
            stdout = (
                f"Device {device['mac']} {device['name']}\n"
                f"\tPaired: {device.get('paired', 'yes')}\n"
                f"\tBonded: {device.get('bonded', 'yes')}\n"
                f"\tTrusted: {device.get('trusted', 'yes')}\n"
                f"\tBlocked: {device.get('blocked', 'no')}\n"
                f"\tConnected: {device.get('connected', 'no')}\n"
                f"\tIcon: {device.get('icon', 'audio-card')}\n"
            )
            return _real_subprocess.CompletedProcess(["bluetoothctl"], 0, stdout=stdout, stderr="")

        @staticmethod
        def _sink_inputs_output() -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            blocks = []
            sink_input_id = 40
            for device in DEMO_DEVICES:
                mac = str(device["mac"])
                status = DEMO_DEVICE_STATUS.get(mac, {})
                if not status.get("playing"):
                    continue
                sink_input_id += 1
                sink_name = f"bluez_output.{mac.replace(':', '_')}.1"
                blocks.extend(
                    [
                        f"Sink Input #{sink_input_id}",
                        f"Sink: {sink_name}",
                        "State: RUNNING",
                        "application.name = Sendspin Bridge",
                        "application.process.binary = python3",
                        f"media.name = {status.get('current_track', 'Demo Track')}",
                        f"media.title = {status.get('current_artist', 'Demo Artist')}",
                        "",
                    ]
                )
            return _real_subprocess.CompletedProcess(
                ["pactl", "list", "sink-inputs"], 0, stdout="\n".join(blocks).strip(), stderr=""
            )

    _demo_subprocess = _DemoSubprocess()
    _abt.subprocess = _demo_subprocess  # type: ignore[assignment]
    _api_status_mod.subprocess = _demo_subprocess  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 6. Patch state module
    # ------------------------------------------------------------------
    _st.get_adapter_name = lambda mac: _adapter_names_by_mac.get(str(mac).upper())  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 7. Inject demo devices + MA credentials into config
    # ------------------------------------------------------------------
    from config import load_config

    _cfg = load_config()
    import config as _config_mod

    _original_load = _config_mod.load_config

    def _demo_load_config() -> dict:
        result = _original_load()
        result["BLUETOOTH_DEVICES"] = deepcopy(DEMO_DEVICES)
        result["BLUETOOTH_ADAPTERS"] = deepcopy(DEMO_ADAPTERS)
        result["AUTH_ENABLED"] = False
        result["CHECK_UPDATES"] = True
        result["UPDATE_CHANNEL"] = "stable"
        result["AUTO_UPDATE"] = False
        if not result.get("BRIDGE_NAME"):
            result["BRIDGE_NAME"] = "DEMO"
        # Always inject canonical MA credentials in demo mode.
        result["MA_API_URL"] = DEMO_MA_URL
        result["MA_API_TOKEN"] = DEMO_MA_TOKEN
        return result

    _config_mod.load_config = _demo_load_config
    _sc_mod.load_config = _demo_load_config  # type: ignore[attr-defined]
    import bridge_orchestrator as _bridge_orchestrator
    import routes.api_config as _api_config_mod

    _bridge_orchestrator.load_config = _demo_load_config
    _api_config_mod.load_config = _demo_load_config
    _api_config_mod._read_log_lines = lambda runtime, lines: list(DEMO_LOG_LINES)[-lines:]
    _api_config_mod.subprocess = _demo_subprocess  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 8. Patch diagnostics / bugreport helpers
    # ------------------------------------------------------------------
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
                    "restart_delay": getattr(client, "_restart_delay", 1.0),
                    "zombie_restarts": getattr(client, "_zombie_restart_count", 0),
                    "reconnecting": status.get("reconnecting", False),
                    "reconnect_attempt": status.get("reconnect_attempt", 0),
                    "last_error": status.get("last_error"),
                    "last_error_at": status.get("last_error_at"),
                }
            )
        return info

    def _demo_collect_preflight_status() -> dict:
        return {
            "platform": "demo",
            "audio": {
                "system": "pulseaudio",
                "socket": "unix:/tmp/sendspin-demo-pulse.sock",
                "sinks": len(DEMO_DEVICES),
            },
            "bluetooth": {
                "controller": True,
                "adapter": DEMO_ADAPTERS[0]["mac"],
                "paired_devices": len(DEMO_PAIRED_DEVICES),
            },
            "dbus": True,
            "memory_mb": 512,
            "version": _api_status_mod.VERSION,
        }

    _api_status_mod._collect_preflight_status = _demo_collect_preflight_status
    _api_status_mod._collect_recent_logs = lambda n=100: list(DEMO_LOG_LINES)[-n:]
    _api_status_mod._collect_bt_device_info = lambda: deepcopy(DEMO_BT_DEVICE_INFO)
    _api_status_mod._collect_subprocess_info = _demo_collect_subprocess_info

    try:
        import sendspin.audio as _sendspin_audio  # type: ignore[import-not-found]
    except Exception:
        sendspin_pkg = sys.modules.get("sendspin")
        if sendspin_pkg is None:
            sendspin_pkg = ModuleType("sendspin")
            sys.modules["sendspin"] = sendspin_pkg
        _sendspin_audio = ModuleType("sendspin.audio")
        sys.modules["sendspin.audio"] = _sendspin_audio
        cast("Any", sendspin_pkg).audio = _sendspin_audio
    _sendspin_audio.query_devices = lambda: [SimpleNamespace(**device) for device in DEMO_PORTAUDIO_DEVICES]

    # ------------------------------------------------------------------
    # 9. Patch MA client (discover_ma_groups)
    # ------------------------------------------------------------------
    import services.ma_client as _ma_client

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
    import services.ma_monitor as _ma_monitor

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
        action: str, value: Any = None, syncgroup_id: str | None = None
    ) -> dict[str, object]:
        """Handle queue commands locally — update now-playing state."""
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
    import services.ma_discovery as _ma_disc
    import services.update_checker as _update_checker

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
    _api_config_mod.check_latest_version = _demo_check_latest_version

    logger.info("🎭 Demo mode installed — all hardware interactions are simulated")
