"""Secrets must not survive into the logs an operator uploads.

`_mask_text` masks MAC and IPv4 addresses, which is what a bug report needs
— but it knows nothing about credentials.  Three branches in `ma_auth.py` log
the whole Music Assistant response at WARNING, and they are precisely the
branches taken when the token arrives in a shape the code did not expect.
Those lines reach the log ring, `/api/logs`, `/api/logs/download` and the
bug-report bundle, all unredacted.

The adjacent code already refuses to log a raw body, so the intent was there;
three sites simply missed it.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.web.redaction import REDACTED, redact

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"


# ── the shapes a leak arrives in ─────────────────────────────────────────


def test_a_bearer_header_is_redacted():
    assert TOKEN not in redact(f"Authorization: Bearer {TOKEN}")


def test_a_json_token_field_is_redacted():
    text = f'{{"result": {{"token": "{TOKEN}"}}}}'

    assert TOKEN not in redact(text)


@pytest.mark.parametrize("key", ["token", "access_token", "refresh_token", "api_token", "password", "secret"])
def test_credential_shaped_keys_are_redacted(key):
    assert "hunter2horse" not in redact(f'{{"{key}": "hunter2horse"}}')


def test_a_python_repr_of_a_response_is_redacted():
    """The `%s` of a dict is the exact shape those WARNING lines produce."""
    text = str({"result": {"access_token": "abc123def456ghi", "expires": 1800}})

    assert "abc123def456ghi" not in redact(text)


def test_a_bare_jwt_in_running_text_is_redacted():
    assert TOKEN not in redact(f"unexpected result: {TOKEN}")


# ── what must survive ────────────────────────────────────────────────────


def test_ordinary_text_is_left_alone():
    text = "auth/token/create returned unexpected result"

    assert redact(text) == text


def test_the_surrounding_structure_stays_readable():
    """A redacted line still has to tell the operator what happened."""
    redacted = redact(f'{{"error": "invalid_grant", "access_token": "{TOKEN}"}}')

    assert "invalid_grant" in redacted
    assert TOKEN not in redacted


def test_a_short_non_secret_value_is_not_mangled():
    assert redact('{"expires_in": 1800}') == '{"expires_in": 1800}'


def test_redaction_is_idempotent():
    once = redact(f"Bearer {TOKEN}")

    assert redact(once) == once


@pytest.mark.parametrize("value", ["", None])
def test_nothing_to_redact_is_handled(value):
    assert redact(value) == (value if value else "")


# ── wired into the masking used by diagnostics ───────────────────────────


def test_the_diagnostics_masker_redacts_secrets_too():
    from sendspin_bridge.web.routes.api_status import _mask_text

    masked = _mask_text(f'{{"access_token": "{TOKEN}"}}')

    assert TOKEN not in masked


def test_the_diagnostics_masker_still_masks_addresses():
    from sendspin_bridge.web.routes.api_status import _mask_text

    masked = _mask_text("device AA:BB:CC:DD:EE:FF at 192.168.10.55")

    assert "AA:BB:CC:DD:EE:FF" not in masked
    assert "192.168.10.55" not in masked


# ── the three branches that leaked ───────────────────────────────────────


def test_the_unexpected_result_branches_redact_before_logging(caplog):
    import sendspin_bridge.web.routes.ma_auth as ma_auth

    with caplog.at_level("WARNING", logger="sendspin_bridge.web.routes.ma_auth"):
        ma_auth._log_unexpected_result("auth/token/create", {"result": {"token": TOKEN}})

    assert TOKEN not in caplog.text
    assert "auth/token/create" in caplog.text


# ── a value containing the other quote character ─────────────────────────


def test_a_json_value_may_contain_an_apostrophe():
    """The value pattern rejected both quotes, not just its own delimiter."""
    line = '{"access_token": "abc\'def"}'

    assert "abc'def" not in redact(line)
    assert REDACTED in redact(line)


def test_a_dict_repr_value_may_contain_a_double_quote():
    line = "{'access_token': 'ab\"cd'}"

    assert 'ab"cd' not in redact(line)
    assert REDACTED in redact(line)


def test_the_neighbouring_fields_survive_either_way():
    line = '{"user": "kim", "password": "p\'ss", "state": "ok"}'
    masked = redact(line)

    assert '"user": "kim"' in masked
    assert '"state": "ok"' in masked
    assert "p'ss" not in masked
