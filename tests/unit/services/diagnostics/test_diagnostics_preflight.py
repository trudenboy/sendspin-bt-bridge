"""The diagnostics bundle must not invent a broken runtime.

`/api/diagnostics` built its state model with `diag.get("preflight")` — a key
the bundle does not have.  The state model therefore always saw an empty
preflight, decided D-Bus was unavailable, and the recovery card in the
bundle claimed "the bridge runtime cannot reach the host D-Bus services
required for Bluetooth control" on a host that was paired, connected and
playing.  `/api/status`, which collects a real preflight, said nothing of the
kind — so the bug report contradicted the screen it was taken from.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask


@pytest.fixture
def app(monkeypatch, tmp_path):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))

    import sendspin_bridge.web.routes.api_status as api_status

    monkeypatch.setattr(
        api_status,
        "_collect_preflight_status",
        lambda: {"dbus": {"available": True}, "bluetooth": {"status": "ok"}, "audio": {"status": "ok"}},
    )

    flask_app = Flask(__name__)
    flask_app.register_blueprint(api_status.status_bp)
    return flask_app, api_status


def test_the_bundle_state_model_keeps_the_runtime_it_measured(app, monkeypatch):
    flask_app, api_status = app

    seen: list[object] = []

    def _spy(**kwargs):
        seen.append(kwargs.get("bridge_state"))
        return {}

    monkeypatch.setattr(api_status, "_build_recovery_assistant_payload", _spy)
    monkeypatch.setattr(api_status, "_build_onboarding_assistant_payload", lambda **kw: {})
    monkeypatch.setattr(api_status, "_build_operator_guidance_payload", lambda **kw: {})

    flask_app.test_client().get("/api/diagnostics")

    assert seen and seen[0] is not None, "the bundle was built without a state model"
    substrate = seen[0].to_dict()["runtime_substrate"]
    assert substrate["dbus_available"] is True, "the bundle reported a D-Bus it never measured"
    assert substrate["bluetooth"] == {"status": "ok"}
