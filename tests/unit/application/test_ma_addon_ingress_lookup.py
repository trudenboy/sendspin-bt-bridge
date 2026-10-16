"""Reaching a Music Assistant add-on with any slug through Home Assistant's ingress.

On a real install the MA add-on came from a local repository
(``local_music_assistant_dev_local``); the bridge only tried the official
repository's slugs, found nothing, and the sign-in failed with "MA ingress URL
unavailable via Home Assistant proxy".  A non-official add-on is used only once
it proved to be the Music Assistant the bridge talks to.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.music_assistant import auth as ma_auth
from sendspin_bridge.services.ha import ha_addon

LOCAL_SLUG = "local_music_assistant_dev_local"
MA_URL = "http://192.168.10.10:8095"


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
    """Home Assistant's ``supervisor/api`` WebSocket command.

    An admin may list add-ons (``/supervisor/info``); anyone may read one
    add-on's info.
    """

    def __init__(self, addons: list[dict], *, admin: bool = True):
        self.addons = {a["slug"]: a for a in addons}
        self.admin = admin
        self.endpoints: list[str] = []

    def __call__(self, ha_token: str, endpoint: str, ha_url: str | None = None):
        self.endpoints.append(endpoint)
        if endpoint == "/supervisor/info":
            if not self.admin:
                return None
            return {"addons": [{k: a[k] for k in ("slug", "name", "state")} for a in self.addons.values()]}
        if endpoint.startswith("/addons/") and endpoint.endswith("/info"):
            return self.addons.get(endpoint[len("/addons/") : -len("/info")])
        return None


def _ma_addon(slug: str = LOCAL_SLUG, name: str = "Music Assistant (dev)", state: str = "started") -> dict:
    return {
        "slug": slug,
        "name": name,
        "state": state,
        "hostname": slug.replace("_", "-"),
        "ingress": True,
        "ingress_port": 8094,
        "ingress_url": f"/api/hassio_ingress/{slug}-token/",
    }


def _bridge_is_an_add_on(monkeypatch, addons: list[dict], server_ids: dict[str, str]) -> None:
    """The bridge's own Supervisor view, plus what answers ``/info`` where."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "bridge-token")
    by_slug = {a["slug"]: a for a in addons}

    def supervisor(path: str, timeout: float = 5.0):
        if path == "/supervisor/info":
            return {"data": {"addons": [{k: a[k] for k in ("slug", "name", "state")} for a in addons]}}
        if path.startswith("/addons/") and path.endswith("/info"):
            info = by_slug.get(path[len("/addons/") : -len("/info")])
            return {"data": dict(info)} if info else None
        return None

    monkeypatch.setattr(ha_addon, "_get_supervisor_payload", supervisor)
    monkeypatch.setattr(ha_addon, "ma_server_id", lambda url, timeout=5.0: server_ids.get(url.rstrip("/")))


def _token_response(token: str = "ma_long_lived_token"):
    resp = MagicMock()
    resp.read.return_value = json.dumps({"result": token}).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@patch.object(ma_auth, "_create_ha_ingress_session_via_ws", return_value="ingress-session")
@patch.object(ma_auth, "safe_urlopen")
def test_token_is_made_through_a_verified_local_ma_addon(mock_urlopen, _session, monkeypatch):
    """A non-admin HA user may not list add-ons, but may read one add-on's info:
    the bridge's own Supervisor lookup supplies (and verifies) the slug."""
    _bridge_is_an_add_on(
        monkeypatch,
        [_ma_addon()],
        {MA_URL: "srv-1", "http://local-music-assistant-dev-local:8095": "srv-1"},
    )
    ha = FakeHaSupervisorApi([_ma_addon()], admin=False)
    mock_urlopen.return_value = _token_response()

    with patch.object(ma_auth, "_ha_ws_supervisor_get", ha):
        token = ma_auth._create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-token", MA_URL)

    assert token == "ma_long_lived_token"
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == f"http://ha.local:8123/api/hassio_ingress/{LOCAL_SLUG}-token/api"
    assert req.get_header("Cookie") == "ingress_session=ingress-session"


@patch.object(ma_auth, "_create_ha_ingress_session_via_ws")
@patch.object(ma_auth, "safe_urlopen")
def test_no_ingress_session_goes_to_a_look_alike(mock_urlopen, mock_session, monkeypatch):
    fake = _ma_addon("abcd1234_music_assistant_fake", "Music Assistant")
    _bridge_is_an_add_on(
        monkeypatch,
        [fake],
        {MA_URL: "srv-real", "http://abcd1234-music-assistant-fake:8095": "srv-fake"},
    )

    with patch.object(ma_auth, "_ha_ws_supervisor_get", FakeHaSupervisorApi([fake])):
        token = ma_auth._create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-token", MA_URL)

    assert token is None
    mock_session.assert_not_called()
    mock_urlopen.assert_not_called()
    assert "abcd1234_music_assistant_fake" in ma_auth._ha_proxy_failure.get()


@patch.object(ma_auth, "safe_urlopen")
def test_no_identity_headers_go_to_a_look_alike(mock_urlopen, monkeypatch):
    fake = _ma_addon("abcd1234_music_assistant_fake", "Music Assistant")
    _bridge_is_an_add_on(
        monkeypatch,
        [fake],
        {MA_URL: "srv-real", "http://abcd1234-music-assistant-fake:8095": "srv-fake"},
    )

    assert ma_auth._create_ma_token_via_ingress("u1", "admin", "Admin", ma_url=MA_URL) is None
    mock_urlopen.assert_not_called()


@patch.object(ma_auth, "_create_ha_ingress_session_via_ws")
def test_outside_home_assistant_a_non_official_add_on_is_reported_not_used(mock_session):
    ha = FakeHaSupervisorApi([_ma_addon()])

    with patch.object(ma_auth, "_ha_ws_supervisor_get", ha):
        url, why = ma_auth._resolve_ma_ingress_url_via_ha("http://ha.local:8123", "ha-token", MA_URL)

    assert url == ""
    assert LOCAL_SLUG in why
    mock_session.assert_not_called()


def test_non_official_add_on_needs_a_real_ingress_path():
    data = {**_ma_addon(), "ingress_url": "", "webui": "http://evil.example/"}

    assert ma_auth._ingress_url_from_addon_info("http://ha.local:8123", data, official=False) == ""


@patch.object(ma_auth, "_get_ha_user_via_ws", return_value={"id": "u1", "name": "admin"})
def test_sign_in_error_names_what_was_found(_user, monkeypatch):
    _bridge_is_an_add_on(monkeypatch, [_ma_addon(state="stopped")], {})

    with patch.object(ma_auth, "_ha_ws_supervisor_get", FakeHaSupervisorApi([])):
        result, error = ma_auth._complete_ma_login_via_ha_token("http://ha.local:8123", "ha-token", MA_URL)

    assert result is None
    assert error is not None
    assert "Music Assistant add-on" in error
    assert LOCAL_SLUG in error and "stopped" in error


@patch.object(ma_auth, "get_ma_api_credentials", return_value=("", ""))
@patch.object(ma_auth, "_get_ha_user_via_ws", return_value={"id": "u1", "name": "admin"})
def test_automatic_sign_in_error_says_the_add_on_was_not_found(_user, _creds, monkeypatch):
    _bridge_is_an_add_on(monkeypatch, [{"slug": "core_mosquitto", "name": "Mosquitto broker", "state": "started"}], {})

    with patch.object(ma_auth, "_ha_ws_supervisor_get", FakeHaSupervisorApi([])), pytest.raises(UseCaseError) as err:
        ma_auth.ha_silent_auth("ha-token", MA_URL)

    assert err.value.status == 502
    assert "No running Music Assistant add-on" in (err.value.detail or "")


def test_older_tokens_of_this_bridge_are_revoked(monkeypatch):
    calls: list[tuple[str, dict]] = []
    name = ma_auth._ma_token_name()
    tokens = [
        {"token_id": "new", "name": name, "created_at": "2026-10-16T10:00:00"},
        {"token_id": "other", "name": "Phone", "created_at": "2026-10-15T10:00:00"},
        {"token_id": "old", "name": name, "created_at": "2026-10-14T10:00:00"},
    ]

    def fake_call(ma_url, token, command, args=None):
        calls.append((command, args or {}))
        return tokens if command == "auth/tokens" else None

    monkeypatch.setattr(ma_auth, "_ma_api_call", fake_call)
    ma_auth._retire_older_bridge_tokens(MA_URL, "new-token")

    assert ("auth/token/revoke", {"token_id": "old"}) in calls
    assert all(args.get("token_id") not in ("new", "other") for _c, args in calls)


_NO_OAUTH = (
    "Music Assistant Home Assistant auth is unavailable: Provider does not support OAuth or is not configured. "
    "If Home Assistant login is not configured in Music Assistant, switch to Music Assistant authentication."
)


@patch.object(ma_auth, "_get_ma_oauth_bootstrap", return_value=(None, _NO_OAUTH))
@patch.object(ma_auth, "_ma_reports_homeassistant_addon", return_value=False)
def test_without_oauth_the_error_points_to_a_token(_addon, _oauth):
    with pytest.raises(UseCaseError) as err:
        ma_auth.ha_login_start("http://ma:8095", "user", "pass")

    assert err.value.code == "oauth_unavailable"
    assert "Provider does not support OAuth" in (err.value.detail or "")
    assert "token" in (err.value.detail or "").lower()


@patch.object(ma_auth, "_get_ma_oauth_bootstrap", return_value=(None, _NO_OAUTH))
@patch.object(ma_auth, "_ma_reports_homeassistant_addon", return_value=False)
def test_in_the_add_on_the_error_points_to_the_automatic_sign_in(_addon, _oauth, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "bridge-token")
    monkeypatch.setattr(ha_addon, "_get_supervisor_payload", lambda *_a, **_k: None)
    with pytest.raises(UseCaseError) as err:
        ma_auth.ha_login_start("http://ma:8095", "user", "pass")

    assert "Home Assistant sidebar" in (err.value.detail or "")
