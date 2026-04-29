"""Integration-style tests for demo-mode runtime helpers."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from demo.bt_manager import DemoBluetoothManager
from demo.fixtures import (
    DEMO_ADAPTERS,
    DEMO_BT_DEVICE_INFO,
    DEMO_DEVICE_STATUS,
    DEMO_DEVICES,
    DEMO_DISPLAY_VERSION,
    DEMO_LOG_LINES,
    DEMO_MA_SERVER_INFO,
    DEMO_MA_TOKEN,
    DEMO_MA_URL,
    DEMO_PORTAUDIO_DEVICES,
    DEMO_UPDATE_INFO,
    demo_player_id_for_name,
)
from demo.simulator import run_simulator
from sendspin_bridge.config import VERSION
from sendspin_bridge.web.routes.auth import auth_bp
from sendspin_bridge.web.routes.views import views_bp


def _reset_demo_shared_state() -> None:
    import sendspin_bridge.bridge.state as state

    sys.modules.pop("sendspin.audio", None)
    sys.modules.pop("sendspin.audio_devices", None)
    state.set_clients([])
    state.set_disabled_devices([])
    state.set_ma_groups({}, [])
    state.set_ma_api_credentials("", "")
    state.set_ma_connected(False)
    state.set_ma_server_version("")
    state.replace_ma_now_playing({})
    state.set_runtime_mode_info({})
    state.reset_startup_progress(0, message="")
    state.set_update_available(None)


def _install_demo_runtime(monkeypatch, request):
    import demo
    import sendspin_bridge.web.routes.api_bt as api_bt_module

    class StubSendspinClient:
        def __init__(self):
            self.player_name = "Office @ DEMO"
            self.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:04")
            self.status = {}
            self.bluetooth_sink_name = None
            self._daemon_proc = None
            self._daemon_task = None
            self._stderr_task = None

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)

        _update_status = update_status

    monkeypatch.setattr(sys.modules["__main__"], "SendspinClient", StubSendspinClient, raising=False)
    monkeypatch.setattr(sys.modules["__main__"], "BluetoothManager", object, raising=False)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("SENDSPIN_DEMO_CONFIG_DIR", str(Path("/tmp") / request.node.name))
    request.addfinalizer(_reset_demo_shared_state)

    demo.install()
    monkeypatch.setattr("demo.time.sleep", lambda _delay: None)
    monkeypatch.setattr("sendspin_bridge.web.routes.api.time.sleep", lambda _delay: None)
    monkeypatch.setattr(api_bt_module, "_last_scan_completed", 0.0, raising=False)
    monkeypatch.setattr(api_bt_module, "_SCAN_COOLDOWN", 0.0, raising=False)
    return demo


def test_demo_bluetooth_manager_connect_device_restores_sink_and_volume():
    seen: list[tuple[str, int | None]] = []
    manager = DemoBluetoothManager(
        "AA:BB:CC:DD:EE:02",
        device_name="Kitchen",
        on_sink_found=lambda sink, volume: seen.append((sink, volume)),
    )

    assert manager.connect_device() is True

    assert manager.connected is True
    assert manager.battery_level == 45
    assert seen == [("bluez_output.AA_BB_CC_DD_EE_02.1", 50)]


def test_demo_bluetooth_manager_disconnect_clears_connection_state():
    manager = DemoBluetoothManager("AA:BB:CC:DD:EE:01", device_name="Living Room")

    assert manager.disconnect_device() is True

    assert manager.connected is False
    assert manager.battery_level is None


def test_demo_bluetooth_manager_supports_reconnect_cancellation_api():
    class StubClient:
        def __init__(self):
            self.status = {"reconnecting": True, "reconnect_attempt": 3}

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)

    client = StubClient()
    manager = DemoBluetoothManager("AA:BB:CC:DD:EE:05", device_name="Patio", host=client)

    manager.cancel_reconnect()

    assert manager.management_enabled is False
    assert client.status["reconnecting"] is False
    assert client.status["reconnect_attempt"] == 0

    manager.allow_reconnect()

    assert manager.management_enabled is True


@pytest.mark.asyncio
async def test_demo_bluetooth_manager_monitor_loop_stops_after_shutdown(monkeypatch):
    manager = DemoBluetoothManager("AA:BB:CC:DD:EE:01", device_name="Living Room")
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("demo.bt_manager.asyncio.sleep", fast_sleep)

    task = asyncio.create_task(manager.monitor_and_reconnect())
    await real_sleep(0.01)
    manager.shutdown()
    await asyncio.wait_for(task, timeout=0.1)


def test_demo_bluetooth_manager_seeds_reanchor_buffering_and_group_reconnecting_states():
    class StubClient:
        def __init__(self):
            self.status = {}

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)

    reanchor_client = StubClient()
    DemoBluetoothManager("AA:BB:CC:DD:EE:02", device_name="Kitchen", host=reanchor_client)
    assert reanchor_client.status["reanchoring"] is True
    assert reanchor_client.status["reanchor_count"] == DEMO_DEVICE_STATUS["AA:BB:CC:DD:EE:02"]["reanchor_count"]
    assert reanchor_client.status["last_sync_error_ms"] == DEMO_DEVICE_STATUS["AA:BB:CC:DD:EE:02"]["last_sync_error_ms"]

    low_reanchor_client = StubClient()
    DemoBluetoothManager("AA:BB:CC:DD:EE:01", device_name="Living Room", host=low_reanchor_client)
    assert low_reanchor_client.status["reanchor_count"] == 5

    medium_reanchor_client = StubClient()
    DemoBluetoothManager("AA:BB:CC:DD:EE:03", device_name="Studio", host=medium_reanchor_client)
    assert medium_reanchor_client.status["reanchor_count"] == 15

    reconnecting_client = StubClient()
    reconnecting_manager = DemoBluetoothManager("AA:BB:CC:DD:EE:05", device_name="Patio", host=reconnecting_client)
    assert reconnecting_client.status["group_name"] == "Focus Zone"
    assert reconnecting_client.status["reconnecting"] is True
    assert reconnecting_manager.connect_device() is False

    buffering_client = StubClient()
    DemoBluetoothManager("AA:BB:CC:DD:EE:06", device_name="Bedroom", host=buffering_client)
    assert buffering_client.status["buffering"] is True
    assert buffering_client.status["muted"] is True


@pytest.mark.asyncio
async def test_demo_simulator_advances_track_progress(monkeypatch):
    progress_updated = asyncio.Event()

    class FakeClient:
        def __init__(self):
            self.player_name = "Living Room"
            self.status = {
                "group_id": None,
                "server_connected": True,
                "bluetooth_connected": True,
                "playing": True,
                "track_progress_ms": 0,
                "track_duration_ms": 10000,
                "battery_level": 80,
                "current_track": "Demo Track",
                "current_artist": "Demo Artist",
            }

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)
            if self.status.get("track_progress_ms", 0) > 0:
                progress_updated.set()

        _update_status = update_status

    client = FakeClient()
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("demo.simulator.asyncio.sleep", fast_sleep)

    task = asyncio.create_task(run_simulator([client]))
    await asyncio.wait_for(progress_updated.wait(), timeout=0.2)
    task.cancel()
    await task

    assert client.status["track_progress_ms"] > 0


@pytest.mark.asyncio
async def test_demo_simulator_keeps_group_members_on_same_track(monkeypatch):
    real_sleep = asyncio.sleep
    group_aligned = asyncio.Event()

    class FakeClient:
        def __init__(self, player_name, group_id=None, player_id=None, track=None, artist=None):
            self.player_name = player_name
            self.player_id = player_id
            self.status = {
                "group_id": group_id,
                "server_connected": True,
                "bluetooth_connected": True,
                "playing": True,
                "track_progress_ms": 0,
                "track_duration_ms": 10000,
                "battery_level": 80,
                "current_track": track,
                "current_artist": artist,
            }

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)
            if (
                living.status.get("current_track") == kitchen.status.get("current_track")
                and living.status.get("current_artist") == kitchen.status.get("current_artist")
                and living.status.get("track_progress_ms") == kitchen.status.get("track_progress_ms")
                and bedroom.status.get("current_track") != living.status.get("current_track")
                and living.status.get("track_progress_ms", 0) > 0
            ):
                group_aligned.set()

        _update_status = update_status

    living = FakeClient(
        "Living Room",
        group_id="syncgroup_main_floor",
        track="Midnight City",
        artist="M83",
    )
    kitchen = FakeClient(
        "Kitchen",
        group_id="syncgroup_main_floor",
        track="Texas Sun",
        artist="Khruangbin & Leon Bridges",
    )
    bedroom = FakeClient(
        "Bedroom",
        player_id="sendspin-demo-bedroom",
        track="Sunset Lover",
        artist="Petit Biscuit",
    )

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("demo.simulator.asyncio.sleep", fast_sleep)

    task = asyncio.create_task(run_simulator([living, kitchen, bedroom]))
    await asyncio.wait_for(group_aligned.wait(), timeout=0.2)
    task.cancel()
    await task

    assert living.status["current_track"] == kitchen.status["current_track"]
    assert living.status["current_artist"] == kitchen.status["current_artist"]
    assert living.status["track_progress_ms"] == kitchen.status["track_progress_ms"]
    assert bedroom.status["current_track"] != living.status["current_track"]


@pytest.mark.asyncio
async def test_demo_install_seeds_connected_ma_state_and_named_adapters(monkeypatch, request):
    import demo
    import sendspin_bridge.bridge.state as state
    import sendspin_bridge.config as config_module

    class StubSendspinClient:
        def __init__(self):
            self.player_name = "Office @ DEMO"
            self.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:04")
            self.status = {}
            self.bluetooth_sink_name = None
            self._daemon_proc = None
            self._daemon_task = None
            self._stderr_task = None

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)

        _update_status = update_status

    monkeypatch.setattr(sys.modules["__main__"], "SendspinClient", StubSendspinClient, raising=False)
    monkeypatch.setattr(sys.modules["__main__"], "BluetoothManager", object, raising=False)
    monkeypatch.setenv("DEMO_MODE", "true")
    request.addfinalizer(_reset_demo_shared_state)

    demo.install()

    config = config_module.load_config()
    assert config["MA_API_URL"] == DEMO_MA_URL
    assert config["MA_API_TOKEN"] == DEMO_MA_TOKEN
    assert [adapter["name"] for adapter in config["BLUETOOTH_ADAPTERS"]] == [
        adapter["name"] for adapter in DEMO_ADAPTERS
    ]

    assert state.get_ma_api_credentials() == (DEMO_MA_URL, DEMO_MA_TOKEN)
    assert state.is_ma_connected() is True
    assert DEMO_MA_SERVER_INFO["version"] == VERSION
    assert state.get_ma_server_version() == DEMO_MA_SERVER_INFO["version"]
    assert state.get_adapter_name(DEMO_ADAPTERS[1]["mac"]) == DEMO_ADAPTERS[1]["name"]
    main_floor = state.get_ma_now_playing_for_group("syncgroup_main_floor")
    assert main_floor["connected"] is True
    assert main_floor["image_url"].startswith("data:image/svg+xml;utf8,")
    assert main_floor["prev_track"]
    assert main_floor["next_track"]
    update_info = state.get_update_available()
    assert update_info is not None
    assert update_info["current_version"] == VERSION
    assert update_info["version"] == DEMO_UPDATE_INFO["version"]
    assert "What's Changed" in update_info["body"]
    assert "album artwork" in update_info["body"]

    client = StubSendspinClient()
    await client._start_sendspin_inner()
    assert client.player_id == demo_player_id_for_name("Office")
    assert client.status["reanchor_count"] == 0

    from sendspin_bridge.web.routes.api_bt import bt_bp
    from sendspin_bridge.web.routes.api_config import config_bp

    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    app.register_blueprint(config_bp)
    response = app.test_client().get("/api/bt/adapters")
    assert response.status_code == 200
    adapters = response.get_json()["adapters"]
    assert [adapter["name"] for adapter in adapters] == [adapter["name"] for adapter in DEMO_ADAPTERS]
    assert [adapter["mac"] for adapter in adapters] == [adapter["mac"] for adapter in DEMO_ADAPTERS]

    update_response = app.test_client().get("/api/update/info")
    assert update_response.status_code == 200
    update_payload = update_response.get_json()
    assert update_payload["update_available"] is True
    assert update_payload["version"] == DEMO_UPDATE_INFO["version"]
    assert update_payload["body"] == DEMO_UPDATE_INFO["body"]
    assert update_payload["channel"] == "stable"

    version_response = app.test_client().get("/api/version")
    assert version_response.status_code == 200
    version_payload = version_response.get_json()
    assert version_payload["version"] == DEMO_DISPLAY_VERSION


def test_demo_bt_manager_seeds_released_ready_and_stopping_states():
    class StubClient:
        def __init__(self):
            self.status = {}

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)

    released_client = StubClient()
    DemoBluetoothManager("AA:BB:CC:DD:EE:07", device_name="Guest Room", host=released_client)
    assert released_client.status["bt_management_enabled"] is False
    assert released_client.status["bt_released_by"] == "user"

    ready_client = StubClient()
    ready_manager = DemoBluetoothManager("AA:BB:CC:DD:EE:08", device_name="Bathroom", host=ready_client)
    assert ready_client.status["muted"] is True
    assert ready_client.status["battery_level"] is None
    assert ready_manager.connect_device() is True
    assert ready_client.status["reconnecting"] is False

    stopping_client = StubClient()
    DemoBluetoothManager("AA:BB:CC:DD:EE:09", device_name="Balcony", host=stopping_client)
    assert stopping_client.status["stopping"] is True


def test_demo_bt_scan_pair_and_paired_inventory_are_dynamic(monkeypatch, request):
    _install_demo_runtime(monkeypatch, request)

    from sendspin_bridge.web.routes.api_bt import bt_bp

    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    client = app.test_client()

    scan_resp = client.post("/api/bt/scan", json={"adapter": "hci1", "audio_only": True})
    assert scan_resp.status_code == 200
    scan_job_id = scan_resp.get_json()["job_id"]

    scan_result = None
    for _ in range(30):
        payload = client.get(f"/api/bt/scan/result/{scan_job_id}").get_json()
        if payload.get("status") == "done":
            scan_result = payload
            break
        time.sleep(0.01)

    assert scan_result is not None
    assert all(not str(device["mac"]).startswith("AA:BB:CC:DD:EE:") for device in scan_result["devices"])
    assert any(device["mac"] == "11:22:33:44:55:03" for device in scan_result["devices"])

    pair_resp = client.post("/api/bt/pair_new", json={"mac": "11:22:33:44:55:03", "adapter": "hci1"})
    assert pair_resp.status_code == 200
    pair_job_id = pair_resp.get_json()["job_id"]

    pair_result = None
    for _ in range(30):
        payload = client.get(f"/api/bt/pair_new/result/{pair_job_id}").get_json()
        if payload.get("status") == "done":
            pair_result = payload
            break
        time.sleep(0.01)

    assert pair_result is not None
    assert pair_result["success"] is True

    paired_resp = client.get("/api/bt/paired")
    assert paired_resp.status_code == 200
    assert any(device["mac"] == "11:22:33:44:55:03" for device in paired_resp.get_json()["devices"])

    info_resp = client.post("/api/bt/info", json={"mac": "11:22:33:44:55:03"})
    assert info_resp.status_code == 200
    info_payload = info_resp.get_json()
    assert info_payload["paired"] == "yes"
    assert info_payload["trusted"] == "yes"

    rescan_resp = client.post("/api/bt/scan", json={"adapter": "hci1", "audio_only": True})
    assert rescan_resp.status_code == 200
    rescan_job_id = rescan_resp.get_json()["job_id"]

    rescan_result = None
    for _ in range(30):
        payload = client.get(f"/api/bt/scan/result/{rescan_job_id}").get_json()
        if payload.get("status") == "done":
            rescan_result = payload
            break
        time.sleep(0.01)

    assert rescan_result is not None
    assert all(device["mac"] != "11:22:33:44:55:03" for device in rescan_result["devices"])


def test_demo_config_save_is_temporary_and_restart_resets_to_canonical(monkeypatch, request):
    _install_demo_runtime(monkeypatch, request)

    from sendspin_bridge.web.routes.api import api_bp
    from sendspin_bridge.web.routes.api_config import config_bp
    from sendspin_bridge.web.routes.api_status import status_bp

    app = Flask(__name__)
    app.register_blueprint(api_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(status_bp)
    client = app.test_client()

    original = client.get("/api/config").get_json()
    original_macs = [device["mac"] for device in original["BLUETOOTH_DEVICES"]]
    original_count = len(original_macs)

    payload = {key: value for key, value in original.items() if not str(key).startswith("_")}
    payload["BLUETOOTH_DEVICES"] = [
        *payload["BLUETOOTH_DEVICES"],
        {
            "mac": "11:22:33:44:55:03",
            "player_name": "Portable Boom",
            "adapter": "hci1",
            "enabled": True,
        },
    ]

    save_resp = client.post("/api/config", json=payload)
    assert save_resp.status_code == 200

    saved = client.get("/api/config").get_json()
    assert any(device["mac"] == "11:22:33:44:55:03" for device in saved["BLUETOOTH_DEVICES"])

    restart_resp = client.post("/api/restart")
    assert restart_resp.status_code == 200
    assert restart_resp.get_json()["success"] is True
    assert restart_resp.get_json()["emulated"] is True

    # monkeypatch patches demo.time.sleep (== global time.sleep), so use
    # threading.Event().wait() to yield CPU without being affected by the patch.
    _yield = threading.Event()
    current = None
    progress = None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        current = client.get("/api/config").get_json()
        progress = client.get("/api/startup-progress").get_json()
        if (
            not any(device["mac"] == "11:22:33:44:55:03" for device in current["BLUETOOTH_DEVICES"])
            and progress.get("status") == "ready"
            and progress.get("message") == "Demo restart complete"
        ):
            break
        _yield.wait(0.05)

    assert current is not None
    assert progress is not None
    assert len(current["BLUETOOTH_DEVICES"]) == original_count
    assert [device["mac"] for device in current["BLUETOOTH_DEVICES"]] == original_macs
    assert progress["status"] == "ready", f"expected 'ready', got {progress!r}"
    assert progress["message"] == "Demo restart complete"


@pytest.mark.asyncio
async def test_demo_install_patches_bridge_orchestrator_load_config(monkeypatch, request):
    import demo
    import sendspin_bridge.bridge.orchestrator as bridge_orchestrator

    class StubSendspinClient:
        def __init__(self):
            self.player_name = "Office @ DEMO"
            self.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:04")
            self.status = {}

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)

        _update_status = update_status

    monkeypatch.setattr(sys.modules["__main__"], "SendspinClient", StubSendspinClient, raising=False)
    monkeypatch.setattr(sys.modules["__main__"], "BluetoothManager", object, raising=False)
    monkeypatch.setenv("DEMO_MODE", "true")
    request.addfinalizer(_reset_demo_shared_state)

    demo.install()

    cfg = bridge_orchestrator.load_config()
    assert len(cfg["BLUETOOTH_DEVICES"]) == len(DEMO_DEVICES)
    assert [device["player_name"] for device in cfg["BLUETOOTH_DEVICES"]] == [
        device["player_name"] for device in DEMO_DEVICES
    ]


@pytest.mark.asyncio
async def test_demo_install_exposes_demo_logs_diagnostics_and_bugreport(monkeypatch, request):
    import demo
    import sendspin_bridge.bridge.state as state
    from sendspin_bridge.web.routes.api_config import config_bp
    from sendspin_bridge.web.routes.api_status import status_bp

    class StubSendspinClient:
        def __init__(self):
            self.player_name = "Living Room @ DEMO"
            self.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:01")
            self.status = {}
            self.bluetooth_sink_name = None
            self._daemon_proc = None
            self._daemon_task = None
            self._stderr_task = None
            self._restart_delay = 1.0
            self._zombie_restart_count = 0
            self.bt_management_enabled = True

        def update_status(self, updates: dict) -> None:
            self.status.update(updates)

        _update_status = update_status

        def is_running(self) -> bool:
            proc = getattr(self, "_daemon_proc", None)
            return bool(proc) and getattr(proc, "returncode", 1) is None

    monkeypatch.setattr(sys.modules["__main__"], "SendspinClient", StubSendspinClient, raising=False)
    monkeypatch.setattr(sys.modules["__main__"], "BluetoothManager", object, raising=False)
    monkeypatch.setenv("DEMO_MODE", "true")
    request.addfinalizer(_reset_demo_shared_state)

    demo.install()

    client = StubSendspinClient()
    await client._start_sendspin_inner()
    state.set_clients([client])
    assert client.status["reanchor_count"] == 5

    app = Flask(__name__)
    app.register_blueprint(config_bp)
    app.register_blueprint(status_bp)
    test_client = app.test_client()

    logs_resp = test_client.get("/api/logs")
    assert logs_resp.status_code == 200
    logs_data = logs_resp.get_json()
    assert logs_data["logs"] == DEMO_LOG_LINES
    assert logs_data["has_recent_issues"] is True
    assert logs_data["recent_issue_level"] == "error"

    diag_resp = test_client.get("/api/diagnostics")
    assert diag_resp.status_code == 200
    diag_data = diag_resp.get_json()
    assert diag_data["runtime_info"]["mode"] == "demo"
    assert diag_data["bluetooth_daemon"] == "active"
    assert len(diag_data["adapters"]) == len(DEMO_ADAPTERS)
    assert diag_data["dbus_available"] is True
    assert diag_data["sink_inputs"]
    assert [device["name"] for device in diag_data["portaudio_devices"]] == [
        device["name"] for device in DEMO_PORTAUDIO_DEVICES
    ]
    assert diag_data["onboarding_assistant"]["runtime_mode"] == "demo"

    bugreport_resp = test_client.get("/api/bugreport")
    assert bugreport_resp.status_code == 200
    bugreport_data = bugreport_resp.get_json()
    report = bugreport_data["report"]
    assert report["recent_issue_logs"]
    assert report["diagnostics"]["runtime_info"]["mode"] == "demo"
    assert [device["name"] for device in report["bt_device_info"]] == [device["name"] for device in DEMO_BT_DEVICE_INFO]
    assert "BUG REPORT — FULL DIAGNOSTICS" in bugreport_data["text_full"]
    assert "ONBOARDING ASSISTANT" in bugreport_data["text_full"]
    assert "### Diagnostics summary" in bugreport_data["suggested_description"]


def test_demo_index_shows_demo_user_and_ma_token_notice_by_default(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")

    template_root = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(template_root / "src" / "sendspin_bridge" / "web" / "templates"),
        static_folder=str(template_root / "src" / "sendspin_bridge" / "web" / "static"),
    )
    app.secret_key = "testing"
    app.config["AUTH_ENABLED"] = False
    app.config["IS_HA_ADDON"] = False

    @app.context_processor
    def inject_version():
        return {"VERSION": VERSION, "asset_version": lambda _filename: "test-asset-version"}

    @app.route("/static/v<version>/<path:filename>", endpoint="vstatic")
    def _vstatic(version, filename):
        return f"{version}:{filename}"

    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)

    response = app.test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Demo User" in html
    assert "header-btn-signout" in html
    assert "Music Assistant token needs attention" in html
    assert "Open Configuration \u2192 Music Assistant to get one." in html
    assert 'id="auth-warning-notice"' not in html


def test_standalone_index_shows_short_web_port_hint(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    template_root = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(template_root / "src" / "sendspin_bridge" / "web" / "templates"),
        static_folder=str(template_root / "src" / "sendspin_bridge" / "web" / "static"),
    )
    app.secret_key = "testing"
    app.config["AUTH_ENABLED"] = False
    app.config["IS_HA_ADDON"] = False

    @app.context_processor
    def inject_version():
        return {"VERSION": VERSION, "asset_version": lambda _filename: "test-asset-version"}

    @app.route("/static/v<version>/<path:filename>", endpoint="vstatic")
    def _vstatic(version, filename):
        return f"{version}:{filename}"

    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)

    response = app.test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Web UI port" in html
    assert "Direct web UI port. Leave empty for 8080." in html


def test_ha_addon_index_hides_logout_button(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(
        "sendspin_bridge.web.routes.views.get_ma_addon_ui_url",
        lambda: "/api/hassio_ingress/ma-token",
    )
    monkeypatch.setattr("sendspin_bridge.web.routes.views.resolve_web_port", lambda: 8081)
    monkeypatch.setattr("sendspin_bridge.web.routes.views.detect_ha_addon_channel", lambda: "rc")

    template_root = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(template_root / "src" / "sendspin_bridge" / "web" / "templates"),
        static_folder=str(template_root / "src" / "sendspin_bridge" / "web" / "static"),
    )
    app.secret_key = "testing"
    app.config["AUTH_ENABLED"] = True
    app.config["IS_HA_ADDON"] = True

    @app.context_processor
    def inject_version():
        return {"VERSION": VERSION, "asset_version": lambda _filename: "test-asset-version"}

    @app.route("/static/v<version>/<path:filename>", endpoint="vstatic")
    def _vstatic(version, filename):
        return f"{version}:{filename}"

    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["ha_user"] = "HA Admin"

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "HA Admin" in html
    assert 'data-ma-ui-url="/api/hassio_ingress/ma-token"' in html
    assert 'id="header-user-link"' in html
    assert 'href="/api/hassio_ingress/ma-token/#/settings/profile"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'data-action="follow-link-new-tab"' in html
    assert 'data-ma-profile-url="/api/hassio_ingress/ma-token/#/settings/profile"' in html
    assert 'id="ha-web-port-indicator"' in html
    assert 'class="config-readonly-input"' in html
    assert 'value="8081"' in html
    assert "Home Assistant ingress port" in html
    assert "Fixed by installed add-on track (RC)." in html
    assert "header-btn-signout" not in html
