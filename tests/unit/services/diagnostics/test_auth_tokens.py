"""Tests for ``services/auth_tokens.py``."""

from __future__ import annotations

import json

import pytest

from sendspin_bridge.services.diagnostics import auth_tokens as M


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Each test gets its own config.json so token state doesn't leak."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(json.dumps({"AUTH_TOKENS": []}))

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(M, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: json.loads(cfg_file.read_text()),
    )
    monkeypatch.setattr(M, "load_config", lambda: json.loads(cfg_file.read_text()))
    return cfg_file


# ---------------------------------------------------------------------------
# Mint / hash / verify
# ---------------------------------------------------------------------------


def test_mint_token_returns_distinct_values():
    a, _ = M.mint_token()
    b, _ = M.mint_token()
    assert a != b
    assert len(a) > 30  # url-safe 32 bytes ≈ 43 chars


def test_mint_token_id_is_short_handle():
    _, token_id = M.mint_token()
    assert len(token_id) == 16  # 8 hex bytes


def test_hash_and_verify_roundtrip():
    plain = "test-token-abc"
    stored = M.hash_token(plain)
    assert stored.startswith("v1:")
    assert M.verify_token(plain, stored) is True
    assert M.verify_token("wrong", stored) is False


def test_verify_rejects_malformed_hash():
    assert M.verify_token("x", "garbage") is False
    assert M.verify_token("x", "") is False


# ---------------------------------------------------------------------------
# Issue / list / revoke / lookup
# ---------------------------------------------------------------------------


def test_issue_token_persists_hash_not_plaintext(isolated_config):
    plain, _record = M.issue_token("ha-cc")
    saved = json.loads(isolated_config.read_text())
    assert saved["AUTH_TOKENS"], "token should have been persisted"
    persisted = saved["AUTH_TOKENS"][0]
    assert "token_hash" in persisted
    assert plain not in persisted["token_hash"]
    assert "plaintext" not in str(persisted)
    assert plain not in str(persisted)


def test_list_tokens_redacts_hash():
    M.issue_token("a")
    M.issue_token("b")
    listed = M.list_tokens()
    assert len(listed) == 2
    for tok in listed:
        assert tok.token_hash == ""  # never exposed
        public = tok.to_public_dict()
        assert "token_hash" not in public
        assert public["label"] in ("a", "b")


def test_find_matching_token_returns_record(isolated_config):
    plain, record = M.issue_token("ha-cc")
    found = M.find_matching_token(plain)
    assert found is not None
    assert found.id == record.id


def test_find_matching_token_unknown_returns_none(isolated_config):
    M.issue_token("a")
    assert M.find_matching_token("not-a-real-token") is None


def test_find_matching_token_updates_last_used(isolated_config):
    plain, _record = M.issue_token("ha-cc")
    saved_before = json.loads(isolated_config.read_text())
    assert saved_before["AUTH_TOKENS"][0]["last_used"] is None

    M.find_matching_token(plain)

    saved_after = json.loads(isolated_config.read_text())
    assert saved_after["AUTH_TOKENS"][0]["last_used"] is not None


def test_find_matching_token_blank_returns_none():
    assert M.find_matching_token("") is None
    assert M.find_matching_token("   ") is None
    assert M.find_matching_token(None) is None  # type: ignore[arg-type]


def test_revoke_token_removes_record(isolated_config):
    _, rec_a = M.issue_token("a")
    _, rec_b = M.issue_token("b")
    assert M.revoke_token(rec_a.id) is True
    listed = M.list_tokens()
    assert len(listed) == 1
    assert listed[0].id == rec_b.id


def test_revoke_unknown_id_returns_false():
    M.issue_token("a")
    assert M.revoke_token("nonexistent") is False
    assert M.revoke_token("") is False


def test_issue_truncates_overlong_label():
    long_label = "x" * 200
    _, record = M.issue_token(long_label)
    assert len(record.label) <= 64


def test_issue_handles_empty_label_gracefully():
    _, record = M.issue_token("")
    assert record.label == "unnamed"


# ---------------------------------------------------------------------------
# Bearer extraction
# ---------------------------------------------------------------------------


def test_extract_bearer_normal():
    headers = {"Authorization": "Bearer abc123"}
    assert M.extract_bearer(headers) == "abc123"


def test_extract_bearer_case_insensitive_scheme():
    headers = {"Authorization": "bearer abc123"}
    assert M.extract_bearer(headers) == "abc123"


def test_extract_bearer_missing_returns_none():
    assert M.extract_bearer({}) is None


def test_extract_bearer_wrong_scheme_returns_none():
    headers = {"Authorization": "Basic dXNlcjpwYXNz"}
    assert M.extract_bearer(headers) is None


def test_extract_bearer_no_token_returns_none():
    headers = {"Authorization": "Bearer "}
    assert M.extract_bearer(headers) is None


def test_extract_bearer_strips_whitespace():
    headers = {"Authorization": "Bearer  abc123  "}
    assert M.extract_bearer(headers) == "abc123"


# ---------------------------------------------------------------------------
# Multi-token isolation
# ---------------------------------------------------------------------------


def test_two_tokens_match_independently(isolated_config):
    plain_a, rec_a = M.issue_token("a")
    plain_b, rec_b = M.issue_token("b")
    assert M.find_matching_token(plain_a).id == rec_a.id
    assert M.find_matching_token(plain_b).id == rec_b.id


def test_revoking_one_doesnt_affect_other(isolated_config):
    plain_a, rec_a = M.issue_token("a")
    plain_b, _rec_b = M.issue_token("b")
    M.revoke_token(rec_a.id)
    assert M.find_matching_token(plain_a) is None
    assert M.find_matching_token(plain_b) is not None
