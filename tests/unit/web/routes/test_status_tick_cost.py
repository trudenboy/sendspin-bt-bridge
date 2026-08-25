"""A status tick must not probe the host every time.

`/api/status` and every SSE tick rebuilt the whole payload, and the host probe
inside it shells out to `bluetoothctl` twice plus the audio and D-Bus checks.
Measured on a live bridge: `/api/preflight` alone is ~56 ms of the ~62 ms a
status build costs, and one idle speaker produces a tick about every six
seconds — per connected client, so per open browser tab.

The guidance built on top of the probe stays per-tick fresh; only the probe
itself is rate-limited.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def api_status(monkeypatch, tmp_path):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))

    import sendspin_bridge.web.routes.api_status as module

    probes: list[int] = []

    def _probe(**_kwargs):
        probes.append(len(probes) + 1)
        return {"dbus": {"available": True}, "bluetooth": {"status": "ok"}, "audio": {"status": "ok"}}

    monkeypatch.setattr(module, "_shared_collect_preflight_status", _probe)
    module.reset_preflight_probe()
    return module, probes


def test_repeated_status_builds_share_one_probe(api_status):
    module, probes = api_status

    module._build_status_payload()
    module._build_status_payload()
    module._build_status_payload()

    assert len(probes) == 1, "each status build probed the host again"


def test_the_probe_is_taken_again_once_it_is_stale(api_status, monkeypatch):
    module, probes = api_status
    clock = {"now": 0.0}
    monkeypatch.setattr(module._preflight_probe, "_clock", lambda: clock["now"])

    module._build_status_payload()
    clock["now"] = 60.0
    module._build_status_payload()

    assert len(probes) == 2


def test_the_guidance_is_still_derived_on_every_tick(api_status, monkeypatch):
    """Only the probe is rate-limited; what is built on it must stay fresh."""
    module, _probes = api_status
    builds: list[int] = []

    def _spy(**kwargs):
        builds.append(len(builds) + 1)
        return {}

    monkeypatch.setattr(module, "_build_recovery_assistant_payload", _spy)
    monkeypatch.setattr(module, "_build_onboarding_assistant_payload", lambda **kw: {})
    monkeypatch.setattr(module, "_build_operator_guidance_payload", lambda **kw: {})

    module._build_status_payload()
    module._build_status_payload()

    assert len(builds) == 2


def test_a_bug_report_measures_the_host_rather_than_reading_the_sample(api_status):
    """Diagnostics is where someone looks when something is wrong."""
    module, probes = api_status

    module._build_status_payload()
    before = len(probes)
    module._collect_preflight_status()

    assert len(probes) == before + 1


def test_an_invalidation_makes_the_next_tick_probe_again(api_status):
    """After the operator changes an adapter, the next screen must be true."""
    module, probes = api_status

    module._build_status_payload()
    module.invalidate_preflight_probe()
    module._build_status_payload()

    assert len(probes) == 2


# ── the moments that cannot wait for the window ──────────────────────────


def test_powering_an_adapter_makes_the_next_status_measure_again(api_status, installed_bluez):
    """The operator flips a controller and looks straight at the screen."""
    module, probes = api_status
    module._build_status_payload()

    from flask import Flask

    import sendspin_bridge.web.routes.api_bt as api_bt

    installed_bluez.on("power on", stdout="Changing power on succeeded\n")
    app = Flask(__name__)
    app.register_blueprint(api_bt.bt_bp)
    response = app.test_client().post("/api/bt/adapter/power", json={"adapter": "hci0", "power": True})
    assert response.status_code == 200

    module._build_status_payload()

    assert len(probes) == 2, "the screen after an adapter change still showed the old probe"


def test_saving_the_config_makes_the_next_status_measure_again(api_status, monkeypatch, tmp_path):
    """A saved setting can change what the host looks like to the bridge."""
    module, probes = api_status
    module._build_status_payload()

    from flask import Flask

    import sendspin_bridge.web.routes.api_config as api_config

    monkeypatch.setattr(api_config, "CONFIG_FILE", tmp_path / "config.json")
    app = Flask(__name__)
    app.secret_key = "testing"
    app.register_blueprint(api_config.config_bp)
    response = app.test_client().post("/api/config", json={"BRIDGE_NAME": "Renamed", "BLUETOOTH_DEVICES": []})
    assert response.status_code == 200

    module._build_status_payload()

    assert len(probes) == 2, "the screen after a settings save still showed the old probe"
