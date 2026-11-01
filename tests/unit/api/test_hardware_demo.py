"""DEMO_MODE feeds the hardware page from the demo fixtures, not the host."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from demo.fixtures import DEMO_ADAPTERS, DEMO_BT_DEVICE_INFO, DEMO_DEVICES
from sendspin_bridge.services.hardware import inventory
from tests.support.api_client import make_client


@pytest.fixture(autouse=True)
def _fresh_inventory(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    from sendspin_bridge.services.audio import pulse
    from sendspin_bridge.services.source import manager as source_manager
    from sendspin_bridge.services.source import pairing as source_pairing
    from sendspin_bridge.services.source import state as source_state

    originals = {
        (pulse, name): getattr(pulse, name)
        for name in (
            "list_sinks",
            "get_sink_description",
            "get_server_name",
            "set_sink_volume",
            "get_sink_volume",
            "set_sink_mute",
            "get_sink_mute",
            "suspend_sink",
            "set_default_sink",
        )
    }
    source_originals = {name: getattr(source_manager, name) for name in ("start", "stop", "running")}
    pairing_originals = {name: getattr(source_pairing, name) for name in ("open_window", "close_window")}
    original_source_state = source_state.snapshot()
    original_refresh = inventory._refresh
    inventory.clear_cache()
    yield
    inventory._refresh = original_refresh
    for (module, name), value in originals.items():
        setattr(module, name, value)
    for name, value in source_originals.items():
        setattr(source_manager, name, value)
    for name, value in pairing_originals.items():
        setattr(source_pairing, name, value)
    source_state.publish(**asdict(original_source_state))
    inventory.clear_cache()


def test_demo_mode_lists_the_controllers_and_paired_devices_the_page_needs():
    from demo import install_inventory

    install_inventory()

    response = make_client().get("/api/v1/hardware")

    assert response.status_code == 200
    body = response.json()
    assert {controller["mac"] for controller in body["controllers"]} == {adapter["mac"] for adapter in DEMO_ADAPTERS}
    assert {device["mac"] for device in body["bluetooth_devices"]} == {
        str(device["mac"]) for device in DEMO_BT_DEVICE_INFO
    }
    assert body["capabilities"]["bluetooth"] is True
    assert body["capabilities"]["dbus"] is True
    assert body["degraded"] == []


def test_demo_mode_serves_audio_and_the_phone_source():
    from demo import install_inventory

    install_inventory()

    body = make_client().get("/api/v1/hardware").json()

    assert len(body["audio"]["sinks"]) >= 2
    assert len(body["audio"]["sources"]) >= 1
    source = body["source"]
    assert source is not None
    assert source["enabled"] is True
    assert source["state"] == "waiting"
    assert source["capture"]["kind"] == "pulse_source"
    assert source["capture"]["node"] == body["audio"]["sources"][0]["name"]
    assert source["phone_mac"] == DEMO_BT_DEVICE_INFO[0]["mac"]
    assert [device["mac"] for device in body["bluetooth_devices"] if device["source_phone"]] == [
        DEMO_BT_DEVICE_INFO[0]["mac"]
    ]


def test_demo_mode_shows_audio_and_a_command_round_trips():
    from demo import install_inventory

    install_inventory()
    client = make_client()

    audio = client.get("/api/v1/hardware").json()["audio"]
    assert len(audio["sinks"]) >= 2
    assert len(audio["sources"]) >= 1
    card = next(item for item in audio["cards"] if item["profile_count"] and item["port_count"])
    detail = client.get(f"/api/v1/hardware/audio/cards/{card['name']}").json()
    assert detail["profiles"] and detail["ports"]

    sink = audio["sinks"][0]
    response = client.put(f"/api/v1/audio/sinks/{sink['name']}/volume", json={"volume": 23})

    assert response.status_code == 200
    assert response.json()["volume"] == 23

    flipped = not sink["muted"]
    muted = client.put(f"/api/v1/audio/sinks/{sink['name']}/mute", json={"muted": flipped})
    suspended = client.post(f"/api/v1/audio/sinks/{sink['name']}/suspend")

    assert muted.status_code == 200 and muted.json()["muted"] is flipped
    assert suspended.status_code == 200 and suspended.json()["state"] == "suspended"

    refreshed = client.get("/api/v1/hardware").json()["audio"]
    stored = next(item for item in refreshed["sinks"] if item["name"] == sink["name"])
    assert stored["volume"] == 23
    assert stored["muted"] is flipped
    assert stored["state"] == "suspended"


def test_demo_install_seeds_the_current_tree_and_its_source(tmp_path):
    """The canonical stand installs without the deleted Flask tree.

    Runs in a child interpreter so the install's process-wide monkey patches
    cannot leak into the rest of the suite; it is also the regression test for
    the legacy ``sendspin_bridge.web`` imports that used to crash startup.
    """
    repo_root = Path(__file__).resolve().parents[3]
    script = "\n".join(
        [
            "import json",
            "import demo",
            "demo.install()",
            "from sendspin_bridge.application import source as source_uc",
            "from sendspin_bridge.config import load_config",
            "from sendspin_bridge.services.source import manager",
            "config = load_config()",
            "print(json.dumps({",
            "    'players': [player['name'] for player in config.get('players') or []],",
            "    'source_configured': bool(config.get('SENDSPIN_SOURCE')),",
            "    'source_running': manager.running(),",
            "    'source_status': source_uc.status(config).as_status_dict(),",
            "}))",
        ]
    )
    env = {
        **os.environ,
        "DEMO_MODE": "true",
        "SENDSPIN_DEMO_CONFIG_DIR": str(tmp_path / "config"),
    }
    env.pop("CONFIG_DIR", None)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["players"] == [device["player_name"] for device in DEMO_DEVICES]
    assert payload["source_configured"] is True
    assert payload["source_running"] is True
    assert payload["source_status"]["enabled"] is True
    assert payload["source_status"]["phone"] == "bluez_source.AA_BB_CC_DD_EE_01"


def test_demo_mode_marks_one_output_as_the_default():
    from demo import install_inventory

    install_inventory()

    body = make_client().get("/api/v1/hardware").json()

    defaults = [sink for sink in body["audio"]["sinks"] if sink["is_default"]]
    assert len(defaults) == 1
    assert body["capabilities"]["default_sink"] is True


def test_demo_mode_opens_a_pairing_window_the_strip_can_count_down():
    from demo import install_inventory

    install_inventory()
    client = make_client(config={"SENDSPIN_SOURCE": True})

    opened = client.post("/api/v1/bridge/source/pairing", json={})

    assert opened.status_code == 200
    assert opened.json()["pairing_seconds_left"] > 0
    assert opened.json()["error"] is None

    # A second visit sees the window still open: the countdown lives on the
    # source's shared snapshot, which is what the strip adopts after a reload.
    again = client.post("/api/v1/bridge/source/pairing", json={})
    assert again.json()["pairing_seconds_left"] > 0
    assert again.json()["pairing_adapters"]

    assert client.delete("/api/v1/bridge/source/pairing").status_code == 204
