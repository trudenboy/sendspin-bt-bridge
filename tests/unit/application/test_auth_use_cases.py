"""Sign-in use cases: which methods exist, MA credentials, secret-safe logging, lockout buckets."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import sendspin_bridge.application.auth as auth_uc

# -- available methods ------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "env", "expected"),
    [
        ({}, {}, ["password"]),
        ({"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "tok"}, {}, ["ma", "password"]),
        ({"MA_API_URL": "http://ma:8095"}, {}, ["password"]),
        (
            {"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "t", "MA_AUTH_PROVIDER": "ha"},
            {},
            ["ha_via_ma", "password"],
        ),
        ({"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "t"}, {"SUPERVISOR_TOKEN": "x"}, ["ha"]),
    ],
)
def test_available_methods(config, env, expected):
    with patch.object(auth_uc, "load_config", return_value=config), patch.dict("os.environ", env, clear=True):
        assert auth_uc.available_methods() == expected


# -- Music Assistant credentials ------------------------------------------------------


def test_ma_credentials_without_ma_is_unavailable():
    with patch.object(auth_uc, "load_config", return_value={}):
        outcome = auth_uc.ma_credentials("user", "pass")
    assert outcome.kind == "unavailable"
    assert "not connected" in outcome.message.lower()


@pytest.mark.parametrize(
    ("effect", "kind", "counts", "fragment"),
    [
        (None, "success", False, ""),
        (RuntimeError("Invalid username or password"), "invalid", True, "invalid"),
        (ConnectionError("refused"), "unavailable", False, "unreachable"),
    ],
)
def test_ma_credentials(effect, kind, counts, fragment):
    with (
        patch.object(auth_uc, "load_config", return_value={"MA_API_URL": "http://ma:8095"}),
        patch("sendspin_bridge.application.music_assistant.auth.ma_http_login", side_effect=effect, return_value="t"),
    ):
        outcome = auth_uc.ma_credentials("user", "pass")
    assert outcome.kind == kind
    assert outcome.counts_as_failure is counts
    assert fragment in outcome.message.lower()


# -- secret-safe logging of HA login_flow results ---------------------------------------


def test_redact_flow_result_drops_auth_code():
    """``result`` of a ``create_entry`` step is the authorization code — never logged."""
    summary = auth_uc._redact_flow_result(
        {"type": "create_entry", "flow_id": "abcd1234", "result": "SUPER_SECRET_AUTH_CODE", "title": "HA"}
    )
    assert summary["type"] == "create_entry"
    assert summary["flow_id"] == "abcd1234"
    assert summary["has_result"] is True
    assert "SUPER_SECRET_AUTH_CODE" not in repr(summary)
    assert "result" not in summary


def test_redact_flow_result_keeps_error_keys_only():
    summary = auth_uc._redact_flow_result(
        {"type": "form", "flow_id": "f1", "step_id": "mfa", "errors": {"base": "invalid_code"}}
    )
    assert summary["step_id"] == "mfa"
    assert summary["errors"] == ["base"]
    assert "invalid_code" not in repr(summary)


def test_redact_flow_result_handles_non_dict():
    assert auth_uc._redact_flow_result("a-bare-token-string")["type"] == "str"
    assert "a-bare-token-string" not in repr(auth_uc._redact_flow_result("a-bare-token-string"))


# -- lockout buckets ------------------------------------------------------------------------


def _bucket_of(tmp_config, *, trusted, peer, username="alice", headers=None, method="password"):
    """The rate-limit bucket a failed sign-in from *peer* lands in."""
    import json

    from tests.support.api_client import make_client

    tmp_config.write_text(json.dumps({"TRUSTED_PROXIES": trusted}))
    buckets: list[str] = []
    with (
        patch.object(auth_uc.rate_limiter, "record_failure", side_effect=buckets.append),
        patch.object(auth_uc, "sign_in", return_value=auth_uc.LoginOutcome("invalid", counts_as_failure=True)),
    ):
        make_client({"AUTH_ENABLED": True}, peer=peer).post(
            "/api/v1/auth/session",
            json={"method": method, "username": username, "password": "x"},
            headers=headers or {},
        )
    assert len(buckets) == 1
    return buckets[0]


def test_bucket_uses_forwarded_for_from_a_trusted_proxy(tmp_config):
    bucket = _bucket_of(
        tmp_config, trusted=["10.0.0.10"], peer="10.0.0.10", headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.10"}
    )
    assert bucket == "198.51.100.7"


def test_bucket_ignores_forwarded_for_from_an_untrusted_peer(tmp_config):
    bucket = _bucket_of(tmp_config, trusted=[], peer="10.0.0.10", headers={"X-Forwarded-For": "198.51.100.7"})
    assert bucket == "10.0.0.10"


def test_bucket_honours_a_cidr_trusted_proxy(tmp_config):
    """Under ingress the peer is somewhere in 172.30.32.0/23: bucketing by it would
    lock out the whole household after five failures by anyone."""
    bucket = _bucket_of(
        tmp_config, trusted=["172.30.32.0/23"], peer="172.30.33.7", headers={"X-Forwarded-For": "192.168.10.55"}
    )
    assert bucket == "192.168.10.55"


@pytest.mark.parametrize(("trusted", "peer"), [(["172.30.32.0/23"], "172.30.33.7"), (["10.0.0.10"], "10.0.0.10")])
def test_bucket_falls_back_to_the_account_behind_a_proxy_that_names_no_client(tmp_config, trusted, peer):
    bucket = _bucket_of(tmp_config, trusted=trusted, peer=peer, username="Alice", method="ma")
    assert bucket == "proxy-login:alice"


def test_the_local_password_cannot_dodge_the_lockout_by_varying_the_username(tmp_config):
    """The password method ignores the username, so it must not pick the bucket."""
    buckets = {
        _bucket_of(tmp_config, trusted=["10.0.0.10"], peer="10.0.0.10", username=name)
        for name in ("alice", "bob", "mallory", "")
    }
    assert buckets == {"proxy:10.0.0.10"}
