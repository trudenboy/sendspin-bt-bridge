"""Tests for ``services.bt_class_of_device`` — sets a per-adapter
Class of Device via BlueZ kernel mgmt opcode 0x002C.

Two seams:

- ``parse_class_hex`` is pure string→tuple decoding; tests target the
  bit layout (Major bits 12-8, Minor bits 7-2) end-to-end.
- ``set_device_class`` opens an ``AF_BLUETOOTH`` raw socket; we
  monkey-patch ``_open_mgmt_socket`` with a fake that returns a
  pre-baked ``CommandComplete`` blob, exercising the wire-format parser
  and the event-skipping loop in the same style as
  ``test_bt_rssi_mgmt``.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock

from sendspin_bridge.services.bluetooth import bt_class_of_device

# ── parse_class_hex ────────────────────────────────────────────────────


def test_parse_class_hex_decodes_samsung_compat_value():
    """``0x00010c`` is the Samsung Q-series compat value from
    bluez/bluez#1025.  Major bits (12-8) of ``0x000100`` = ``1``
    (Computer); Minor bits (7-2) of ``0x0c`` = ``0b000011`` = ``3``
    (Laptop)."""
    assert bt_class_of_device.parse_class_hex("0x00010c") == (1, 3)


def test_parse_class_hex_uppercase_accepted():
    """Operators may type the hex with uppercase letters; both forms
    must produce the same wire octets so we don't gate the workaround
    on capitalisation."""
    assert bt_class_of_device.parse_class_hex("0x00010C") == (1, 3)


def test_parse_class_hex_with_zero_minor_returns_zero_minor():
    """``0x000100`` decodes to Major=Computer with Minor=0 (i.e. the
    ``Uncategorized`` minor slot).  Sanity-checks that we don't accidentally
    treat a 0-minor as a parse failure."""
    assert bt_class_of_device.parse_class_hex("0x000100") == (1, 0)


def test_parse_class_hex_rejects_missing_prefix():
    """Hex prefix is required — without it the user could pass an
    integer-looking string and get unexpected octets.  Reject and
    return ``None`` so the caller logs and skips."""
    assert bt_class_of_device.parse_class_hex("00010c") is None


def test_parse_class_hex_rejects_wrong_length():
    """Only the canonical 6-hex-digit form decodes; anything shorter
    or longer is a typo and must be rejected.  The mgmt opcode takes
    exactly two octets, so partial values would silently address the
    wrong fields."""
    assert bt_class_of_device.parse_class_hex("0x10010") is None
    assert bt_class_of_device.parse_class_hex("0x1000010c") is None


def test_parse_class_hex_rejects_garbage():
    assert bt_class_of_device.parse_class_hex("not-hex") is None
    assert bt_class_of_device.parse_class_hex("") is None
    assert bt_class_of_device.parse_class_hex(None) is None  # type: ignore[arg-type]


# ── set_device_class — wire-level via fake socket ──────────────────────


def _make_fake_socket(reply_bytes: bytes) -> MagicMock:
    """Return a MagicMock that mimics a btsocket-style mgmt socket.

    ``send`` accepts any payload; ``recv`` returns the canned bytes;
    ``settimeout`` is a no-op; ``close`` is observed via the close hook
    in the helper, which tolerates failures."""
    sock = MagicMock()
    sock.send = MagicMock(return_value=None)
    sock.recv = MagicMock(return_value=reply_bytes)
    sock.settimeout = MagicMock(return_value=None)
    return sock


def _build_cmd_complete_packet(
    *,
    adapter_index: int,
    opcode: int,
    status: int,
) -> bytes:
    """Synthesise the kernel's reply blob for a successful (or failed)
    ``CommandComplete`` event.  Layout: 6-byte mgmt event header
    (``ev_code``, ``ev_idx``, ``len``) + ``cmd_op`` + ``status`` + 3
    bytes of CoD echo to mirror the real reply size."""
    payload = struct.pack("<HB", opcode, status) + b"\x00\x00\x00"  # opcode, status, echoed CoD
    header = struct.pack("<HHH", 0x0001, adapter_index, len(payload))  # 0x0001 = CMD_COMPLETE
    return header + payload


def test_set_device_class_returns_true_on_kernel_success(monkeypatch):
    """A status-0 ``CommandComplete`` for the matching opcode and
    adapter index is the success path — operator gets the Samsung-Q
    workaround applied without touching the host."""
    sock = _make_fake_socket(
        _build_cmd_complete_packet(
            adapter_index=0,
            opcode=bt_class_of_device._MGMT_OP_SET_DEV_CLASS,
            status=0,
        )
    )
    monkeypatch.setattr(bt_class_of_device, "_open_mgmt_socket", lambda: sock)
    monkeypatch.setattr(bt_class_of_device, "_close_mgmt_socket", lambda _s: None)

    assert bt_class_of_device.set_device_class(0, 1, 3) is True
    sock.send.assert_called_once()
    sent = sock.send.call_args[0][0]
    # Wire format: 6-byte mgmt header (opcode 0x002C, idx 0, plen 2)
    # then the two CoD octets in order — no shifts, no padding.
    assert sent == struct.pack("<HHH", bt_class_of_device._MGMT_OP_SET_DEV_CLASS, 0, 2) + bytes([1, 3])


def test_set_device_class_returns_false_on_mgmt_status_error(monkeypatch):
    """A non-zero status in the kernel reply means the controller
    rejected the change (e.g. unsupported on this adapter); we must
    log-and-continue, never silently claim success."""
    sock = _make_fake_socket(
        _build_cmd_complete_packet(
            adapter_index=0,
            opcode=bt_class_of_device._MGMT_OP_SET_DEV_CLASS,
            status=0x07,  # Invalid Parameters / Not Supported
        )
    )
    monkeypatch.setattr(bt_class_of_device, "_open_mgmt_socket", lambda: sock)
    monkeypatch.setattr(bt_class_of_device, "_close_mgmt_socket", lambda _s: None)

    assert bt_class_of_device.set_device_class(0, 1, 3) is False


def test_set_device_class_skips_unrelated_events(monkeypatch):
    """bluetoothd shares the mgmt control channel.  An ``Index Added``
    or another command's reply must be skipped, not folded into the
    success/failure decision for our request — otherwise multi-adapter
    hosts would race their startup events."""
    unrelated = struct.pack("<HHH", 0x0004, 7, 0)  # Index Added on hci7
    success = _build_cmd_complete_packet(
        adapter_index=0,
        opcode=bt_class_of_device._MGMT_OP_SET_DEV_CLASS,
        status=0,
    )
    sock = MagicMock()
    sock.send = MagicMock(return_value=None)
    sock.settimeout = MagicMock(return_value=None)
    sock.recv = MagicMock(side_effect=[unrelated, success])
    monkeypatch.setattr(bt_class_of_device, "_open_mgmt_socket", lambda: sock)
    monkeypatch.setattr(bt_class_of_device, "_close_mgmt_socket", lambda _s: None)

    assert bt_class_of_device.set_device_class(0, 1, 3) is True
    assert sock.recv.call_count == 2


def test_set_device_class_returns_false_when_btsocket_missing(monkeypatch):
    """Non-Linux dev box / container without ``CAP_NET_ADMIN`` /
    btsocket not installed: collapse to False without raising so
    bridge_orchestrator's startup keeps booting."""

    def _raise_import_error():
        raise ImportError("btsocket not available")

    monkeypatch.setattr(bt_class_of_device, "_open_mgmt_socket", _raise_import_error)
    assert bt_class_of_device.set_device_class(0, 1, 3) is False


def test_set_device_class_rejects_negative_adapter_index():
    """``-1`` is the sysfs-lookup-failed sentinel; short-circuit before
    any syscall to keep noise low when an adapter is hot-unplugged."""
    assert bt_class_of_device.set_device_class(-1, 1, 3) is False


def test_set_device_class_rejects_out_of_range_class_octets():
    """Major Class is 5 bits (0..31); Minor Class is 6 bits (0..63).
    Out-of-range values would silently overflow the bitfield — better
    to refuse them up front than to ship a malformed mgmt command."""
    assert bt_class_of_device.set_device_class(0, 32, 0) is False
    assert bt_class_of_device.set_device_class(0, 0, 64) is False


# ── apply_device_class_for_hex — the convenience wrapper ───────────────


def test_apply_device_class_for_hex_no_op_on_empty(monkeypatch):
    """Empty string means "leave kernel default in place" — the
    orchestrator iterates over every adapter and lets this wrapper
    decide whether to do anything; an empty value must not produce
    a mgmt round-trip (and must not log a warning)."""
    called = []
    monkeypatch.setattr(bt_class_of_device, "set_device_class", lambda *a: called.append(a) or True)
    assert bt_class_of_device.apply_device_class_for_hex(0, "") is False
    assert called == []


def test_apply_device_class_for_hex_invalid_returns_false(monkeypatch):
    """Bad hex string in config: log-and-skip, don't crash startup."""
    called = []
    monkeypatch.setattr(bt_class_of_device, "set_device_class", lambda *a: called.append(a) or True)
    assert bt_class_of_device.apply_device_class_for_hex(0, "bogus") is False
    assert called == []


def test_apply_device_class_for_hex_calls_set_with_decoded_octets(monkeypatch):
    """Round-trip from hex form to mgmt octets: the wrapper must
    decode, dispatch, and surface the result — anything else means
    the fix didn't actually land on the controller."""
    captured = []

    def _fake_set(idx, major, minor):
        captured.append((idx, major, minor))
        return True

    monkeypatch.setattr(bt_class_of_device, "set_device_class", _fake_set)
    assert bt_class_of_device.apply_device_class_for_hex(0, "0x00010c") is True
    assert captured == [(0, 1, 3)]
