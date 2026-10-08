"""Saving the settings unchanged must not restart anything.

A config.json written before a key existed lacks it; the bridge runs on the
default, and GET /api/config shows the default. Saving that form back used to
be diffed against the raw file, so the default read as a change: seen live,
an untouched save reported BT_CHURN_THRESHOLD / BT_CHURN_WINDOW as changed and
restarted every speaker's player — Music Assistant then had to dial each one
again.
"""

from __future__ import annotations

import json
import sys

import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.json"
    # An older file: none of the keys added since are present.
    cfg_file.write_text(json.dumps({"BRIDGE_NAME": "TestBridge", "BLUETOOTH_DEVICES": [], "BLUETOOTH_ADAPTERS": []}))

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)
    if "sendspin_bridge.web.routes.api_config" in sys.modules and (
        getattr(sys.modules["sendspin_bridge.web.routes.api_config"], "__file__", None) is None
    ):
        sys.modules.pop("sendspin_bridge.web.routes.api_config")

    import sendspin_bridge.web.routes.api_config as api_config_module

    monkeypatch.setattr(api_config_module, "CONFIG_FILE", cfg_file)

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_config_module.config_bp)
    return app.test_client()


def test_saving_the_settings_unchanged_restarts_nothing(client):
    shown = client.get("/api/config").get_json()

    resp = client.post("/api/config", json=shown)

    assert resp.status_code == 200, resp.data
    reconfig = (resp.get_json() or {}).get("reconfig") or {}
    spurious = {kind: actions for kind, actions in reconfig.items() if isinstance(actions, list) and actions}
    assert spurious == {}, f"an unchanged save produced reconfig actions: {spurious}"


def test_a_real_change_is_still_seen(client):
    shown = client.get("/api/config").get_json()
    shown["BT_CHURN_THRESHOLD"] = 5

    resp = client.post("/api/config", json=shown)

    restart = (resp.get_json() or {}).get("reconfig", {}).get("global_restart") or []
    assert [a["fields"] for a in restart] == [["BT_CHURN_THRESHOLD"]]
