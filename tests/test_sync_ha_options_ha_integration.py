"""``_sync_ha_options`` mirrors ``HA_INTEGRATION`` to Supervisor options.

Without this mirror, the HAOS Configuration tab silently shows stale
``ha_integration`` values after the operator saves changes via the
bridge web UI — and the next addon restart writes those stale values
back over the live config, undoing the change.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def captured_supervisor_post(monkeypatch):
    """Capture the JSON body the bridge would have POSTed to Supervisor.

    ``_sync_ha_options`` no-ops outside HA addon mode and when there's
    no ``SUPERVISOR_TOKEN`` env var; we set both up before importing the
    target so it takes the addon-mode branch.
    """
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")

    # Drop any cached stub for routes.api_config from prior tests.
    if "routes.api_config" in sys.modules and getattr(sys.modules["routes.api_config"], "__file__", None) is None:
        sys.modules.pop("routes.api_config")

    import routes.api_config as M

    monkeypatch.setattr(M, "_detect_runtime", lambda: "ha_addon")

    captured: dict = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = getattr(req, "full_url", None) or req.get_full_url()
        captured["body"] = json.loads(req.data.decode())
        captured["headers"] = dict(req.headers.items())

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"result":"ok"}'

        return _Resp()

    import urllib.request as ur

    monkeypatch.setattr(ur, "urlopen", fake_urlopen)
    return M, captured


# ---------------------------------------------------------------------------
# Mirror correctness
# ---------------------------------------------------------------------------


def test_ha_integration_block_mirrored_in_full(captured_supervisor_post):
    M, captured = captured_supervisor_post
    config = {
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
        "HA_INTEGRATION": {
            "enabled": True,
            "mode": "mqtt",
            "mqtt": {
                "broker": "core-mosquitto",
                "port": 1883,
                "username": "sendspin-bridge",
                "password": "supersecret",
                "discovery_prefix": "homeassistant",
                "tls": False,
                "client_id": "",
            },
            "rest": {"advertise_mdns": True, "supervisor_pair": True},
        },
    }
    M._sync_ha_options(config)

    posted = captured["body"]["options"]
    assert "ha_integration" in posted, "ha_integration block missing from Supervisor sync"
    flat = posted["ha_integration"]
    assert flat["enabled"] is True
    assert flat["mode"] == "mqtt"
    assert flat["mqtt_broker"] == "core-mosquitto"
    assert flat["mqtt_port"] == 1883
    assert flat["mqtt_username"] == "sendspin-bridge"
    assert flat["mqtt_password"] == "supersecret"
    assert flat["mqtt_discovery_prefix"] == "homeassistant"
    assert flat["mqtt_tls"] is False
    assert flat["advertise_mdns"] is True
    assert flat["supervisor_pair"] is True


def test_missing_block_yields_safe_defaults(captured_supervisor_post):
    M, captured = captured_supervisor_post
    config = {
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
        # No HA_INTEGRATION key at all — first-boot config.
    }
    M._sync_ha_options(config)
    flat = captured["body"]["options"]["ha_integration"]
    assert flat["enabled"] is False
    assert flat["mode"] == "off"
    assert flat["mqtt_broker"] == "auto"
    assert flat["mqtt_port"] == 1883
    assert flat["mqtt_username"] == ""
    assert flat["mqtt_password"] == ""
    assert flat["mqtt_discovery_prefix"] == "homeassistant"
    assert flat["mqtt_tls"] is False
    assert flat["advertise_mdns"] is True
    assert flat["supervisor_pair"] is True


def test_partial_block_falls_back_to_defaults(captured_supervisor_post):
    """Operator hand-edited config.json with a sparse HA_INTEGRATION
    sub-tree — every missing leaf should get the canonical default,
    NOT cause a KeyError."""
    M, captured = captured_supervisor_post
    config = {
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
        "HA_INTEGRATION": {"enabled": True, "mode": "rest"},  # no mqtt/rest sub-blocks
    }
    M._sync_ha_options(config)
    flat = captured["body"]["options"]["ha_integration"]
    assert flat["enabled"] is True
    assert flat["mode"] == "rest"
    assert flat["mqtt_broker"] == "auto"
    assert flat["advertise_mdns"] is True


def test_non_dict_block_does_not_crash(captured_supervisor_post):
    """Defensive: corrupt or accidentally-string HA_INTEGRATION must
    not raise — the rest of the sync should still go through."""
    M, captured = captured_supervisor_post
    config = {
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
        "HA_INTEGRATION": "garbage",
    }
    M._sync_ha_options(config)
    # No ha_integration block written, but the call still succeeded
    # and the rest of the options dict is intact.
    assert "sendspin_server" in captured["body"]["options"]


def test_no_op_outside_addon_mode(monkeypatch):
    """Outside HA addon mode the function returns silently — neither
    Supervisor nor stale options matter for Docker / standalone."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "")
    if "routes.api_config" in sys.modules and getattr(sys.modules["routes.api_config"], "__file__", None) is None:
        sys.modules.pop("routes.api_config")
    import routes.api_config as M

    monkeypatch.setattr(M, "_detect_runtime", lambda: "docker")

    called = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", called)
    M._sync_ha_options({"HA_INTEGRATION": {"enabled": True, "mode": "mqtt"}})
    called.assert_not_called()
