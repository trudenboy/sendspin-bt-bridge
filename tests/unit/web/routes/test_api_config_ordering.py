"""Removing a speaker is only safe once the removal is on disk.

`POST /api/config` unpaired a deleted speaker from its controller *before*
writing the new config.  If the write then failed — a full disk, a read-only
mount, the wrong ownership after a container update — the endpoint answered
500 and the operator's config still listed the speaker, but the speaker had
already been unpaired from the adapter.  Recovering means pairing it again by
hand, for a change that was reported as failed.
"""

from __future__ import annotations

import json
import sys

import pytest
from flask import Flask

MAC = "AA:BB:CC:DD:EE:FF"
DEVICE = {"mac": MAC, "player_name": "Kitchen", "enabled": True}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "BRIDGE_NAME": "TestBridge",
                "BLUETOOTH_DEVICES": [DEVICE],
                "BLUETOOTH_ADAPTERS": [],
            }
        )
    )

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)

    for name in ("sendspin_bridge.web.routes.api_config",):
        if name in sys.modules and getattr(sys.modules[name], "__file__", None) is None:
            sys.modules.pop(name)

    import sendspin_bridge.web.routes.api_config as api_config_module

    monkeypatch.setattr(api_config_module, "CONFIG_FILE", cfg_file)

    removed: list[str] = []
    monkeypatch.setattr(api_config_module, "_bt_remove_device", lambda mac, adapter: removed.append(mac))

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_config_module.config_bp)
    return app.test_client(), cfg_file, api_config_module, removed


def test_a_deleted_speaker_is_unpaired_once_the_change_is_saved(client):
    cl, cfg_file, _mod, removed = client

    response = cl.post("/api/config", json={"BRIDGE_NAME": "TestBridge", "BLUETOOTH_DEVICES": []})

    assert response.status_code == 200
    assert removed == [MAC]
    assert json.loads(cfg_file.read_text())["BLUETOOTH_DEVICES"] == []


def test_a_failed_save_leaves_the_speaker_paired(client, monkeypatch):
    """The ordering bug: the speaker used to be unpaired anyway."""
    cl, cfg_file, mod, removed = client

    def _cannot_write(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod, "write_config_file", _cannot_write)

    response = cl.post("/api/config", json={"BRIDGE_NAME": "TestBridge", "BLUETOOTH_DEVICES": []})

    assert response.status_code == 500
    assert removed == [], "the speaker was unpaired for a change that failed to save"
    # The deletion never reached disk, so the speaker is still configured —
    # which is the state the operator will retry from.
    stored = json.loads(cfg_file.read_text())["BLUETOOTH_DEVICES"]
    assert [dev["mac"] for dev in stored] == [MAC]


def test_an_unchanged_device_list_unpairs_nothing(client):
    cl, _cfg_file, _mod, removed = client

    response = cl.post("/api/config", json={"BRIDGE_NAME": "Renamed", "BLUETOOTH_DEVICES": [DEVICE]})

    assert response.status_code == 200
    assert removed == []
