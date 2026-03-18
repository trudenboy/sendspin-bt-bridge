"""Demo mode — run the full web UI with simulated Bluetooth devices.

Usage:
    DEMO_MODE=true python sendspin_client.py

All hardware-dependent layers (BlueZ, D-Bus, PulseAudio) are replaced
with intelligent mocks.  The web UI and all API endpoints work normally
with fake devices that respond to user actions.
"""

from __future__ import annotations

import asyncio
import logging
import random
import subprocess as _real_subprocess
import sys
import time
from typing import Any

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
        DEMO_ADAPTER_INFO,
        DEMO_ADAPTER_MAC,
        DEMO_DEVICE_STATUS,
        DEMO_DEVICES,
        DEMO_MA_ALL_GROUPS,
        DEMO_MA_NAME_MAP,
        DEMO_MA_NOW_PLAYING,
        DEMO_MA_SERVER_INFO,
        DEMO_MA_TOKEN,
        DEMO_MA_URL,
        DEMO_PAIRED_DEVICES,
        DEMO_SCAN_RESULTS,
    )

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
                "adapter_name": DEMO_ADAPTER_INFO["name"],
                "adapter_mac": DEMO_ADAPTER_MAC,
            },
            "mocked_layers": [
                {
                    "layer": "BluetoothManager",
                    "summary": "Bluetooth transport is simulated with DemoBluetoothManager.",
                    "details": {"adapter_mac": DEMO_ADAPTER_MAC, "scan_results": len(DEMO_SCAN_RESULTS)},
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
    _sbt.list_bt_adapters = lambda timeout=5: [DEMO_ADAPTER_MAC]
    _sbt.is_audio_device = lambda mac: mac.upper() in _audio_macs
    _sbt.bt_remove_device = lambda mac, adapter_mac="": None

    # ------------------------------------------------------------------
    # 3. Patch services.pulse (sync wrappers used by routes/api.py)
    # ------------------------------------------------------------------
    import services.pulse as _sp

    _sp.list_sinks = lambda: [
        {
            "name": f"bluez_output.{str(d['mac']).replace(':', '_')}.1",
            "description": d.get("player_name", d["mac"]),
        }
        for d in DEMO_DEVICES
    ]
    # Stateful sink volume/mute so API reads back correct values
    _demo_sink_vol: dict[str, int] = {}
    _demo_sink_mute: dict[str, bool] = {}

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

    # ------------------------------------------------------------------
    # 4. Patch SendspinClient methods
    # ------------------------------------------------------------------
    _SendspinClient = _sc_mod.SendspinClient

    async def _demo_start_sendspin_inner(self: Any) -> None:
        """Set status as if connected — no real subprocess spawned."""
        await self.stop_sendspin()

        mac = self.bt_manager.mac_address if self.bt_manager else None
        initial = DEMO_DEVICE_STATUS.get(mac or "", {})

        safe_id = "".join(c if c.isalnum() or c == "-" else "-" for c in self.player_name.lower()).strip("-")
        self.player_id = f"sendspin-demo-{safe_id}"
        # Sentinel so is_running() returns True (checks returncode is None)
        self._daemon_proc = type("_FakeProc", (), {"returncode": None})()
        self._daemon_task = None
        self._stderr_task = None

        # Set fake sink name so has_sink=True in the API
        if mac:
            self.bluetooth_sink_name = f"bluez_output.{mac.replace(':', '_')}.1"

        is_playing = initial.get("playing", False)
        self._update_status(
            {
                "server_connected": True,
                "connected": True,
                "playing": is_playing,
                "audio_streaming": is_playing,
                "volume": initial.get("volume", 100),
                "muted": initial.get("muted", False),
                "audio_format": initial.get("audio_format"),
                "current_track": initial.get("current_track"),
                "current_artist": initial.get("current_artist"),
                "track_duration_ms": initial.get("track_duration_ms"),
                "track_progress_ms": initial.get("track_progress_ms"),
                "battery_level": initial.get("battery_level"),
                "bluetooth_connected": initial.get("bluetooth_connected", False),
                "bluetooth_available": True,
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

        time.sleep(random.uniform(3.0, 5.0))
        devices = list(DEMO_SCAN_RESULTS)
        random.shuffle(devices)
        finish_scan_job(job_id, {"devices": devices})

    _abt._run_bt_scan = _demo_run_bt_scan
    _abt.list_bt_adapters = lambda timeout=5: [DEMO_ADAPTER_MAC]

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
                    return self._adapter_output()
            return _real_subprocess.run(args, *a, **kw)

        @staticmethod
        def _paired_output() -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            lines = [f"Device {d['mac']} {d['name']}" for d in DEMO_PAIRED_DEVICES]
            return _real_subprocess.CompletedProcess(["bluetoothctl"], 0, stdout="\n".join(lines), stderr="")

        @staticmethod
        def _adapter_output() -> _real_subprocess.CompletedProcess:  # type: ignore[type-arg]
            mac = DEMO_ADAPTER_INFO["address"]
            name = DEMO_ADAPTER_INFO["name"]
            stdout = f"Controller {mac} {name}\n\tPowered: yes\n\tAlias: {name}\n"
            return _real_subprocess.CompletedProcess(["bluetoothctl"], 0, stdout=stdout, stderr="")

    _abt.subprocess = _DemoSubprocess()  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 6. Patch state module
    # ------------------------------------------------------------------
    _st.get_adapter_name = lambda mac: "Demo Adapter"  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 7. Inject demo devices + MA credentials into config
    # ------------------------------------------------------------------
    from config import load_config

    _cfg = load_config()
    import config as _config_mod

    _original_load = _config_mod.load_config

    def _demo_load_config() -> dict:
        result = _original_load()
        if not result.get("BLUETOOTH_DEVICES"):
            result["BLUETOOTH_DEVICES"] = list(DEMO_DEVICES)
        if not result.get("BRIDGE_NAME"):
            result["BRIDGE_NAME"] = "DEMO"
        # Always inject MA credentials in demo mode
        if not result.get("MA_API_URL"):
            result["MA_API_URL"] = DEMO_MA_URL
        if not result.get("MA_API_TOKEN"):
            result["MA_API_TOKEN"] = DEMO_MA_TOKEN
        return result

    _config_mod.load_config = _demo_load_config
    _sc_mod.load_config = _demo_load_config  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 8. Patch MA client (discover_ma_groups)
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
    # 9. Patch MA monitor (start_monitor, send_queue_cmd)
    # ------------------------------------------------------------------
    import services.ma_monitor as _ma_monitor

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
        from demo.fixtures import DEMO_TRACKS

        sg_id = syncgroup_id or next(iter(DEMO_MA_NOW_PLAYING), None)
        if not sg_id:
            return {"accepted": False, "queue_id": "", "error": "no queue available"}

        np = _st.get_ma_now_playing_for_group(sg_id) or {}

        if action == "next":
            idx = (np.get("queue_index", 0) + 1) % len(DEMO_TRACKS)
            title, artist, dur = DEMO_TRACKS[idx]
            np.update(
                {
                    "track": title,
                    "artist": artist,
                    "duration": dur / 1000,
                    "elapsed": 0,
                    "queue_index": idx,
                    "state": "playing",
                }
            )
        elif action == "previous":
            idx = max(0, np.get("queue_index", 0) - 1)
            title, artist, dur = DEMO_TRACKS[idx]
            np.update(
                {
                    "track": title,
                    "artist": artist,
                    "duration": dur / 1000,
                    "elapsed": 0,
                    "queue_index": idx,
                    "state": "playing",
                }
            )
        elif action == "shuffle":
            np["shuffle"] = bool(value)
        elif action == "repeat":
            np["repeat"] = value or "off"
        elif action == "seek":
            np["elapsed"] = int(value or 0)

        import time as _time

        np["elapsed_updated_at"] = _time.time()
        _st.set_ma_now_playing_for_group(sg_id, np)
        logger.debug("[demo] queue cmd: %s value=%s → %s", action, value, sg_id)
        return {"accepted": True, "queue_id": sg_id, "syncgroup_id": sg_id}

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
    # 10. Patch MA discovery (validate_ma_url, discover_ma_servers)
    # ------------------------------------------------------------------
    import services.ma_discovery as _ma_disc

    async def _demo_validate_ma_url(url: str) -> dict | None:
        return dict(DEMO_MA_SERVER_INFO)

    async def _demo_discover_ma_servers(timeout: float = 5.0) -> list[dict]:
        await asyncio.sleep(1.0)  # simulate mDNS delay
        return [dict(DEMO_MA_SERVER_INFO)]

    _ma_disc.validate_ma_url = _demo_validate_ma_url
    if hasattr(_ma_disc, "discover_ma_servers"):
        _ma_disc.discover_ma_servers = _demo_discover_ma_servers

    logger.info("🎭 Demo mode installed — all hardware interactions are simulated")
