"""Issue keys, whichever way the caller spells the separator.

The registry grew two naming styles — `runtime_access` and `runtime-access`,
`ma_auth` and `ma-auth` — and answered each only for the exact spelling.  Two
entries were registered twice with identical bodies to paper over it; the
other five hyphenated keys have no twin, so a caller reaching for the snake
form of those silently fell through to "unclassified, priority 999" and
sorted below everything real.

Silence is the problem: an issue the bridge knows about is meant to sort by
its layer, and a typo in a key is indistinguishable from an issue with no
opinion about where it belongs.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.diagnostics.guidance_issue_registry import (
    ISSUE_REGISTRY,
    build_issue_context,
    issue_definition,
    issue_sort_priority,
)

# ── one entry per issue ──────────────────────────────────────────────────


def test_no_issue_is_registered_twice_under_two_spellings():
    canonical: dict[str, list[str]] = {}
    for key in ISSUE_REGISTRY:
        canonical.setdefault(key.replace("-", "_"), []).append(key)

    duplicated = {name: keys for name, keys in canonical.items() if len(keys) > 1}
    assert duplicated == {}, f"the same issue is registered under several spellings: {duplicated}"


# ── either spelling resolves ─────────────────────────────────────────────


@pytest.mark.parametrize(("spelling", "twin"), [("runtime_access", "runtime-access"), ("ma_auth", "ma-auth")])
def test_both_spellings_resolve_to_the_same_definition(spelling, twin):
    assert issue_definition(spelling) is issue_definition(twin)


def test_a_hyphenated_key_is_reachable_by_its_snake_spelling():
    """Five keys only ever existed hyphenated; the snake form used to miss."""
    assert issue_definition("device_disconnected") is not None
    assert issue_sort_priority("device_disconnected") == issue_sort_priority("device-disconnected")


def test_every_registered_key_resolves_by_either_spelling():
    for key in ISSUE_REGISTRY:
        assert issue_definition(key.replace("-", "_")) is not None, key
        assert issue_definition(key.replace("_", "-")) is not None, key


# ── an unknown key is not silently ordinary ──────────────────────────────


def test_an_unknown_key_is_reported():
    assert issue_definition("no_such_issue") is None


def test_an_unknown_key_still_sorts_last_rather_than_raising():
    """Production keeps rendering; only the tests below insist on knowing."""
    assert issue_sort_priority("no_such_issue") > max(d.priority for d in ISSUE_REGISTRY.values())


def test_an_unknown_key_is_marked_unclassified_in_its_context():
    context = build_issue_context("no_such_issue")

    assert context["layer"] == "unclassified"


# ── the context a card renders from ──────────────────────────────────────


def test_a_known_key_carries_its_layer_and_priority():
    context = build_issue_context("runtime-access")

    assert context["layer"] == "runtime_access"
    assert context["priority"] == issue_sort_priority("runtime_access")
    assert context["severity"] == "error"


def test_the_caller_may_override_the_severity():
    context = build_issue_context("runtime_access", severity="warning")

    assert context["severity"] == "warning"


def test_device_names_drive_the_affected_count():
    context = build_issue_context("runtime_access", device_names=["Kitchen", "Study"])

    assert context["device_names"] == ["Kitchen", "Study"]
    assert context["affected_count"] == 2
