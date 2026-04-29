"""Tests for ``services.bluetooth.classify_pair_failure`` — the
fingerprinter that picks recognised pair-failure shapes out of the
captured ``bluetoothctl pair`` output.

The Samsung Q-series Class-of-Device filter quirk (bluez/bluez#1025)
is the only fingerprint we currently surface; these tests cover its
positive matches across bluetoothctl wording variants and the
negative cases that must NOT trigger it (because the operator action
is different — they need to set ``device_class`` on the adapter,
not retry / re-pair / press a passkey)."""

from __future__ import annotations

from sendspin_bridge.services.bluetooth import classify_pair_failure

# ── positive matches: every wording shape we've seen on the wire ───────


_BLUETOOTHCTL_NO_RESOURCES_OUTPUT = """\
Attempting to pair with 1C:86:9A:71:E0:F5
[CHG] Device 1C:86:9A:71:E0:F5 RSSI: 0xffffffc7 (-57)
hci0 1C:86:9A:71:E0:F5 type BR/EDR connect failed (status 0x07, No Resources)
Failed to pair: org.bluez.Error.AuthenticationCanceled
"""


def test_matches_samsung_cod_filter_via_no_resources_marker():
    """The exact bluetoothctl output captured from issue #210 for the
    UGREEN Actions-chipset adapter — both ``status 0x07, No Resources``
    and ``AuthenticationCanceled`` are present, agent path is unused
    (CLI bluetoothctl doesn't go through our PairingAgent)."""
    assert classify_pair_failure(_BLUETOOTHCTL_NO_RESOURCES_OUTPUT) == "samsung_cod_filter"


def test_matches_via_status_0x0d_marker():
    """Some bluez builds surface the HCI status code (0x0d) verbatim
    instead of the human label.  The fingerprint must still trigger so
    the operator-guidance card fires regardless of bluez version."""
    output = (
        "Attempting to pair with 1C:86:9A:71:E0:F5\n"
        "Connect Complete: status 0x0d (Connection Rejected due to Limited Resources)\n"
        "Failed to pair: org.bluez.Error.AuthenticationCanceled\n"
    )
    assert classify_pair_failure(output) == "samsung_cod_filter"


def test_treats_methods_empty_telemetry_as_signal_corroboration():
    """When the native PairingAgent telemetry shows zero method calls,
    that confirms BlueZ never reached IO-cap negotiation — exactly the
    on-the-wire signature of an LMP-layer rejection.  The classifier
    should still return the kind, not be paranoid about it."""
    telemetry = {"method_calls": [], "peer_cancelled": False}
    assert classify_pair_failure(_BLUETOOTHCTL_NO_RESOURCES_OUTPUT, agent_telemetry=telemetry) == "samsung_cod_filter"


# ── negative matches: things that LOOK similar but mean something else ─


def test_does_not_match_when_agent_did_negotiate():
    """If the PairingAgent actually got called (e.g. RequestConfirmation,
    AuthorizeService), this is NOT the LMP-layer rejection — it's a
    later-stage failure (PIN reject, IO-cap mismatch, user cancelled
    on speaker).  Wrong remediation: don't surface the CoD card."""
    output = (
        "Attempting to pair with 1C:86:9A:71:E0:F5\n"
        "hci0 1C:86:9A:71:E0:F5 type BR/EDR connect failed (status 0x07, No Resources)\n"
        "Failed to pair: org.bluez.Error.AuthenticationCanceled\n"
    )
    telemetry = {"method_calls": ["AuthorizeService", "RequestConfirmation"]}
    assert classify_pair_failure(output, agent_telemetry=telemetry) is None


def test_does_not_match_authcancelled_alone_without_no_resources_marker():
    """``AuthenticationCanceled`` on its own happens in many failure
    shapes (timeout, peer cancelled, bond cleared mid-flow).  Without
    the ``No Resources`` co-marker we can't claim the CoD-filter
    fingerprint — leave it to the generic pair_failure path."""
    output = "Attempting to pair with 30:21:0E:0A:AE:5A\nFailed to pair: org.bluez.Error.AuthenticationCanceled\n"
    assert classify_pair_failure(output) is None


def test_does_not_match_no_resources_without_authcancelled():
    """Conversely, ``No Resources`` can appear during legitimate
    out-of-RAM / busy-controller transients that don't terminate as
    AuthenticationCanceled.  Both halves of the fingerprint must be
    present before we surface the Samsung-specific card."""
    output = (
        "Attempting to pair with 30:21:0E:0A:AE:5A\n"
        "hci0 30:21:0E:0A:AE:5A type BR/EDR connect failed (status 0x07, No Resources)\n"
        "Failed to pair: org.bluez.Error.ConnectionAttemptFailed\n"
    )
    assert classify_pair_failure(output) is None


def test_returns_none_for_empty_input():
    """Defensive: empty / None inputs must not raise and must produce
    no fingerprint, since the call site treats ``None`` as "no
    actionable diagnosis available"."""
    assert classify_pair_failure("") is None
    assert classify_pair_failure(None) is None  # type: ignore[arg-type]
