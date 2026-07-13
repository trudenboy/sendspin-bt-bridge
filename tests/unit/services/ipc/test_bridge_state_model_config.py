"""Regression: bridge state model must read the real config keys.

``/api/status`` reported MA as unconfigured and the update channel as
``stable`` regardless of settings because it read lowercase keys
(``ma_base_url`` / ``ma_token`` / ``update_channel``) that never exist —
the canonical keys are ``MA_API_URL`` / ``MA_API_TOKEN`` / ``UPDATE_CHANNEL``.
"""

from __future__ import annotations

from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model


def _model(config):
    return build_bridge_state_model(
        config=config,
        preflight=None,
        devices=[],
        ma_connected=False,
        runtime_mode="running",
    )


def test_ma_configured_true_for_real_config_keys():
    model = _model({"MA_API_URL": "http://ma.local:8095", "MA_API_TOKEN": "tok"})
    assert model.configuration.ma_configured is True


def test_ma_configured_true_with_only_url():
    model = _model({"MA_API_URL": "http://ma.local:8095"})
    assert model.configuration.ma_configured is True


def test_ma_configured_false_when_absent():
    assert _model({}).configuration.ma_configured is False


def test_update_channel_reads_uppercase_key():
    assert _model({"UPDATE_CHANNEL": "beta"}).configuration.update_channel == "beta"


def test_update_channel_defaults_to_stable():
    assert _model({}).configuration.update_channel == "stable"
