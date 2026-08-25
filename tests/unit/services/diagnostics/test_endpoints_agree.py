"""The two places that report on a device must report the same thing.

`build_recovery_assistant_snapshot` has two modes.  Given a `bridge_state` it
rebuilds its own device wrappers from the normalised state; without one it
reads the snapshot objects it was handed.  `/api/status` passed the state and
`/api/diagnostics` did not, so the same speaker produced different issues,
different traces and a different timeline depending on which page the
operator opened — and the diagnostics bundle they attach to a bug report is
the one that took the second path.
"""

from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot
from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model

CONFIG = {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen"}]}
ONBOARDING = {"checklist": {"steps": []}, "checks": []}


def _device(**bluetooth) -> SimpleNamespace:
    facts = {"reconnect_attempt": 0, "max_reconnect_fails": 5, "connected": False}
    facts.update(bluetooth)
    return SimpleNamespace(
        player_name="Kitchen",
        enabled=True,
        extra={"bluetooth_connected": facts["connected"], **facts},
        state_model={
            "bluetooth": facts,
            "management": {},
            "audio": {},
            "transport": {},
            "async_ops": {},
            "music_assistant": {},
            "health": {},
        },
        recent_events=[],
        health_summary=None,
    )


def _both_ways(device) -> tuple[dict, dict]:
    """The snapshot as each endpoint builds it."""
    state = build_bridge_state_model(
        config=CONFIG,
        devices=[device],
        runtime_mode="docker",
        ma_connected=False,
        preflight={},
    )
    diagnostics_view = build_recovery_assistant_snapshot(
        config=CONFIG,
        devices=[device],
        onboarding_assistant=ONBOARDING,
        startup_progress={},
    ).to_dict()
    status_view = build_recovery_assistant_snapshot(
        config=CONFIG,
        devices=[device],
        onboarding_assistant=ONBOARDING,
        startup_progress={},
        bridge_state=state,
    ).to_dict()
    return diagnostics_view, status_view


def test_the_two_modes_do_diverge():
    """Pinning why every caller must pass the state, not just most of them.

    If this ever stops being true the dual mode is dead weight and can go;
    while it is true, a caller that forgets the state gets a different
    answer, silently.
    """
    diagnostics_view, status_view = _both_ways(_device(reconnect_attempt=4))

    assert (
        diagnostics_view["timeline"]["summary"]["entry_count"] != (status_view["timeline"]["summary"]["entry_count"])
    ), "the two modes now agree — the dual branch in recovery_assistant can be removed"


def test_the_diagnostics_endpoint_passes_the_state_model(monkeypatch, tmp_path):
    """The bug: it did not, so its bundle described devices differently."""
    import json

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))

    import sendspin_bridge.web.routes.api_status as api_status

    seen: list[object] = []

    def _spy(**kwargs):
        seen.append(kwargs.get("bridge_state"))
        return {}

    monkeypatch.setattr(api_status, "_build_recovery_assistant_payload", _spy)
    monkeypatch.setattr(api_status, "_build_onboarding_assistant_payload", lambda **kw: {})
    monkeypatch.setattr(api_status, "_build_operator_guidance_payload", lambda **kw: {})

    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api_status.status_bp)
    # The response itself is not the subject: on a host without PulseAudio
    # some collectors fail and the bundle still renders their errors.  What
    # matters is that the recovery assistant was handed the state model.
    app.test_client().get("/api/diagnostics")

    assert seen, "the recovery assistant was never built"
    assert seen[0] is not None, "the diagnostics bundle was built without the state model"


def test_the_status_endpoint_passes_the_state_model(monkeypatch, tmp_path):
    import json

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))

    import sendspin_bridge.web.routes.api_status as api_status

    seen: list[object] = []

    def _spy(**kwargs):
        seen.append(kwargs.get("bridge_state"))
        return {}

    monkeypatch.setattr(api_status, "_build_recovery_assistant_payload", _spy)
    monkeypatch.setattr(api_status, "_build_onboarding_assistant_payload", lambda **kw: {})
    monkeypatch.setattr(api_status, "_build_operator_guidance_payload", lambda **kw: {})

    api_status._build_status_payload()

    assert seen and seen[0] is not None
