"""Reaching a Music Assistant add-on with any slug through Home Assistant's ingress.

On a real install the MA add-on came from a local repository
(``local_music_assistant_dev_local``); the bridge only tried the official
repository's slugs, found nothing, and the sign-in failed with "MA ingress URL
unavailable via Home Assistant proxy".
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sendspin_bridge.application.music_assistant import auth as ma_auth
from sendspin_bridge.services.ha import ha_addon

LOCAL_SLUG = "local_music_assistant_dev_local"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import sendspin_bridge.config as config
    from sendspin_bridge.services.infrastructure import url_safety

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    monkeypatch.setattr(ma_auth, "is_safe_external_url", lambda _u: True)
    monkeypatch.setattr(url_safety, "is_safe_external_url", lambda _u: True)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    ha_addon.reset_ma_addon_cache()
    yield
    ha_addon.reset_ma_addon_cache()


class FakeHaSupervisorApi:
    """Home Assistant's ``supervisor/api`` WebSocket command, for an admin user."""

    def __init__(self, addons: list[dict]):
        self.addons = {a["slug"]: a for a in addons}
        self.endpoints: list[str] = []

    def __call__(self, ha_token: str, endpoint: str, ha_url: str | None = None):
        self.endpoints.append(endpoint)
        if endpoint == "/supervisor/info":
            return {"addons": [{k: a[k] for k in ("slug", "name", "state")} for a in self.addons.values()]}
        if endpoint.startswith("/addons/") and endpoint.endswith("/info"):
            return self.addons.get(endpoint[len("/addons/") : -len("/info")])
        return None


def _local_ma(state: str = "started") -> dict:
    return {
        "slug": LOCAL_SLUG,
        "name": "Music Assistant (dev)",
        "state": state,
        "ingress": True,
        "ingress_url": "/api/hassio_ingress/local-ma-token/",
    }


def _token_response(token: str = "ma_long_lived_token"):
    resp = MagicMock()
    resp.read.return_value = json.dumps({"result": token}).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@patch.object(ma_auth, "_create_ha_ingress_session_via_ws", return_value="ingress-session")
@patch.object(ma_auth, "safe_urlopen")
def test_token_is_made_through_a_local_repository_ma_addon(mock_urlopen, _session):
    fake_ha = FakeHaSupervisorApi(
        [{"slug": "core_mosquitto", "name": "Mosquitto broker", "state": "started"}, _local_ma()]
    )
    mock_urlopen.return_value = _token_response()

    with patch.object(ma_auth, "_ha_ws_supervisor_get", fake_ha):
        token = ma_auth._create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-token")

    assert token == "ma_long_lived_token"
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "http://ha.local:8123/api/hassio_ingress/local-ma-token/api"
    assert req.get_header("Cookie") == "ingress_session=ingress-session"


@patch.object(ma_auth, "_create_ha_ingress_session_via_ws", return_value="ingress-session")
@patch.object(ma_auth, "safe_urlopen")
def test_the_bridge_add_on_finds_the_slug_itself(mock_urlopen, _session, monkeypatch):
    """A non-admin HA user may not list add-ons, but may read one add-on's info:
    the bridge's own Supervisor lookup supplies the slug."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "bridge-token")

    def supervisor(path: str, timeout: float = 5.0):
        if path == "/supervisor/info":
            return {"data": {"addons": [{"slug": LOCAL_SLUG, "name": "Music Assistant (dev)", "state": "started"}]}}
        if path == f"/addons/{LOCAL_SLUG}/info":
            return {"data": _local_ma()}
        return None

    monkeypatch.setattr(ha_addon, "_get_supervisor_payload", supervisor)
    ha_only_reads_one_addon = FakeHaSupervisorApi([_local_ma()])

    def non_admin(ha_token, endpoint, ha_url=None):
        if endpoint == "/supervisor/info":
            return None  # Unauthorized for a non-admin user
        return ha_only_reads_one_addon(ha_token, endpoint, ha_url)

    mock_urlopen.return_value = _token_response()
    with patch.object(ma_auth, "_ha_ws_supervisor_get", non_admin):
        token = ma_auth._create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-token")

    assert token == "ma_long_lived_token"
    assert f"/addons/{LOCAL_SLUG}/info" in ha_only_reads_one_addon.endpoints


@patch.object(ma_auth, "_get_ha_user_via_ws", return_value={"id": "u1", "name": "admin"})
def test_sign_in_error_names_what_home_assistant_has(_user):
    fake_ha = FakeHaSupervisorApi(
        [{"slug": "core_mosquitto", "name": "Mosquitto broker", "state": "started"}, _local_ma(state="stopped")]
    )
    with patch.object(ma_auth, "_ha_ws_supervisor_get", fake_ha):
        result, error = ma_auth._complete_ma_login_via_ha_token("http://ha.local:8123", "ha-token", "http://ma:8095")

    assert result is None
    assert error is not None
    assert "Music Assistant add-on" in error
    assert LOCAL_SLUG in error and "stopped" in error


@patch.object(ma_auth, "get_ma_api_credentials", return_value=("", ""))
@patch.object(ma_auth, "_create_ma_token_via_ingress", return_value=None)
@patch.object(ma_auth, "_get_ha_user_via_ws", return_value={"id": "u1", "name": "admin"})
def test_automatic_sign_in_error_says_the_add_on_was_not_found(_user, _direct, _creds):
    from sendspin_bridge.application.errors import UseCaseError

    fake_ha = FakeHaSupervisorApi([{"slug": "core_mosquitto", "name": "Mosquitto broker", "state": "started"}])
    with patch.object(ma_auth, "_ha_ws_supervisor_get", fake_ha), pytest.raises(UseCaseError) as err:
        ma_auth.ha_silent_auth("ha-token", "http://ma:8095")

    assert err.value.status == 502
    assert "Music Assistant add-on" in err.value.detail
    assert "core_mosquitto" in err.value.detail


_NO_OAUTH = (
    "Music Assistant Home Assistant auth is unavailable: Provider does not support OAuth or is not configured. "
    "If Home Assistant login is not configured in Music Assistant, switch to Music Assistant authentication."
)


@patch.object(ma_auth, "_get_ma_oauth_bootstrap", return_value=(None, _NO_OAUTH))
@patch.object(ma_auth, "_ma_reports_homeassistant_addon", return_value=False)
def test_without_oauth_the_error_points_to_a_token(_addon, _oauth):
    from sendspin_bridge.application.errors import UseCaseError

    with pytest.raises(UseCaseError) as err:
        ma_auth.ha_login_start("http://ma:8095", "user", "pass")

    assert err.value.code == "oauth_unavailable"
    assert "Provider does not support OAuth" in (err.value.detail or "")
    assert "token" in (err.value.detail or "").lower()


@patch.object(ma_auth, "_get_ma_oauth_bootstrap", return_value=(None, _NO_OAUTH))
@patch.object(ma_auth, "_ma_reports_homeassistant_addon", return_value=False)
def test_in_the_add_on_the_error_points_to_the_automatic_sign_in(_addon, _oauth, monkeypatch):
    from sendspin_bridge.application.errors import UseCaseError

    monkeypatch.setenv("SUPERVISOR_TOKEN", "bridge-token")
    monkeypatch.setattr(ha_addon, "_get_supervisor_payload", lambda *_a, **_k: None)
    with pytest.raises(UseCaseError) as err:
        ma_auth.ha_login_start("http://ma:8095", "user", "pass")

    assert "Home Assistant sidebar" in (err.value.detail or "")
