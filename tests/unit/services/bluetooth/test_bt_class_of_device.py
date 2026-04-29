"""Tests for ``services.bt_class_of_device`` — applies a per-adapter
Class of Device via raw HCI ``Write_Class_Of_Device``
(opcode ``0x0C24``) and reads it back via ``Read_Class_Of_Device``
(``0x0C23``) on a ``BTPROTO_HCI`` socket.

Three seams:

- ``parse_class_hex`` is pure string → int decoding; tests target the
  6-hex-digit shape end-to-end.
- ``set_device_class`` and ``read_device_class`` open a raw HCI
  socket. We monkey-patch ``_open_hci_socket`` with a ``FakeHciSocket``
  that records sent packets and returns pre-baked Command Complete
  events, exercising the wire-format parser and the event-skipping
  loop without touching the kernel.
"""

from __future__ import annotations

from sendspin_bridge.services.bluetooth import bt_class_of_device

# ── parse_class_hex ────────────────────────────────────────────────────


def test_parse_class_hex_decodes_samsung_compat_value():
    """``0x00010c`` is the Samsung Q-series compat value from
    bluez/bluez#1025 (Computer/Laptop)."""
    assert bt_class_of_device.parse_class_hex("0x00010c") == 0x00010C


def test_parse_class_hex_uppercase_accepted():
    assert bt_class_of_device.parse_class_hex("0x00010C") == 0x00010C


def test_parse_class_hex_with_zero_minor_returns_int():
    assert bt_class_of_device.parse_class_hex("0x000100") == 0x000100


def test_parse_class_hex_rejects_missing_prefix():
    assert bt_class_of_device.parse_class_hex("00010c") is None


def test_parse_class_hex_rejects_wrong_length():
    assert bt_class_of_device.parse_class_hex("0x0010c") is None
    assert bt_class_of_device.parse_class_hex("0x000010c") is None


def test_parse_class_hex_rejects_garbage():
    assert bt_class_of_device.parse_class_hex("garbage") is None
    assert bt_class_of_device.parse_class_hex("") is None
    assert bt_class_of_device.parse_class_hex(None) is None  # type: ignore[arg-type]


# ── HCI socket fake ────────────────────────────────────────────────────


class FakeHciSocket:
    """Minimal stand-in for the BTPROTO_HCI socket used by the applier.

    Records every packet passed to ``send`` and yields canned Command
    Complete events from ``recv`` in order.
    """

    def __init__(self, recv_payloads: list[bytes]):
        self.sent: list[bytes] = []
        self._recv_queue = list(recv_payloads)
        self.closed = False
        self._timeout: float | None = None

    def send(self, data: bytes) -> int:
        self.sent.append(bytes(data))
        return len(data)

    def settimeout(self, t: float | None) -> None:
        self._timeout = t

    def recv(self, _bufsize: int) -> bytes:
        if not self._recv_queue:
            raise TimeoutError("FakeHciSocket: no more pre-baked recv payloads")
        return self._recv_queue.pop(0)

    def close(self) -> None:
        self.closed = True


def _command_complete_event(opcode: int, status: int, return_params: bytes = b"") -> bytes:
    """Build a canonical HCI Command Complete event packet."""
    body = bytes([1]) + opcode.to_bytes(2, "little") + bytes([status]) + return_params
    # [04][0e][plen][body...]
    return bytes([0x04, 0x0E, len(body)]) + body


# ── set_device_class wire-format ───────────────────────────────────────


def test_set_device_class_writes_correct_hci_packet(monkeypatch):
    """Verify we send a well-formed HCI Write_Class_Of_Device packet."""
    fake = FakeHciSocket([_command_complete_event(0x0C24, 0x00)])
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.set_device_class(0, 0x00010C) is True
    # Expected packet: [01][24 0c][03][0c 01 00] for CoD 0x00010c
    assert len(fake.sent) == 1
    assert fake.sent[0] == bytes([0x01, 0x24, 0x0C, 0x03, 0x0C, 0x01, 0x00])
    assert fake.closed is True


def test_set_device_class_returns_true_on_command_complete_status_zero(monkeypatch):
    fake = FakeHciSocket([_command_complete_event(0x0C24, 0x00)])
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.set_device_class(0, 0x00010C) is True


def test_set_device_class_returns_false_on_command_complete_status_nonzero(monkeypatch):
    fake = FakeHciSocket([_command_complete_event(0x0C24, 0x12)])
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.set_device_class(0, 0x00010C) is False


def test_set_device_class_returns_false_on_timeout(monkeypatch):
    fake = FakeHciSocket([])  # no events → recv raises TimeoutError immediately
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.set_device_class(0, 0x00010C) is False


def test_set_device_class_skips_unrelated_event_codes(monkeypatch):
    """An interleaved non-CommandComplete event must not mislead the parser."""
    unrelated = bytes([0x04, 0x05, 0x01, 0x00])  # event code 0x05
    fake = FakeHciSocket([unrelated, _command_complete_event(0x0C24, 0x00)])
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.set_device_class(0, 0x00010C) is True


def test_set_device_class_skips_unrelated_opcode_in_complete(monkeypatch):
    """A Command Complete for some other command must not be claimed."""
    other = _command_complete_event(0x1234, 0x00)
    fake = FakeHciSocket([other, _command_complete_event(0x0C24, 0x00)])
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.set_device_class(0, 0x00010C) is True


def test_set_device_class_returns_false_when_socket_open_fails(monkeypatch):
    def _boom(_idx):
        raise OSError("permission denied")

    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", _boom)
    assert bt_class_of_device.set_device_class(0, 0x00010C) is False


def test_set_device_class_returns_false_on_import_error(monkeypatch):
    def _boom(_idx):
        raise ImportError("ctypes unavailable on this platform")

    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", _boom)
    assert bt_class_of_device.set_device_class(0, 0x00010C) is False


def test_set_device_class_rejects_negative_adapter_index():
    assert bt_class_of_device.set_device_class(-1, 0x00010C) is False


def test_set_device_class_rejects_out_of_range_cod():
    assert bt_class_of_device.set_device_class(0, -1) is False
    assert bt_class_of_device.set_device_class(0, 0x1000000) is False


# ── read_device_class ──────────────────────────────────────────────────


def test_read_device_class_returns_int_on_success(monkeypatch):
    """Read_Class_Of_Device returns 3 bytes of CoD after the status byte."""
    return_params = bytes([0x0C, 0x01, 0x00])  # 0x00010c LE
    event = _command_complete_event(0x0C23, 0x00, return_params)
    fake = FakeHciSocket([event])
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.read_device_class(0) == 0x00010C
    # And we sent a zero-payload Read command.
    assert fake.sent == [bytes([0x01, 0x23, 0x0C, 0x00])]


def test_read_device_class_returns_none_on_failure(monkeypatch):
    fake = FakeHciSocket([_command_complete_event(0x0C23, 0x12)])
    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", lambda _idx: fake)

    assert bt_class_of_device.read_device_class(0) is None


def test_read_device_class_returns_none_on_socket_error(monkeypatch):
    def _boom(_idx):
        raise OSError("not on linux")

    monkeypatch.setattr(bt_class_of_device, "_open_hci_socket", _boom)
    assert bt_class_of_device.read_device_class(0) is None


def test_read_device_class_returns_none_for_negative_index():
    assert bt_class_of_device.read_device_class(-1) is None


# ── apply_device_class_for_hex ─────────────────────────────────────────


def test_apply_device_class_for_hex_no_op_on_empty():
    # Must NOT touch the syscall layer — we don't even want to attempt
    # opening a socket when the operator hasn't configured an override.
    assert bt_class_of_device.apply_device_class_for_hex(0, "") is False


def test_apply_device_class_for_hex_invalid_returns_false(monkeypatch):
    called = {"set": False}

    def _set(_idx, _cod):
        called["set"] = True
        return True

    monkeypatch.setattr(bt_class_of_device, "set_device_class", _set)
    assert bt_class_of_device.apply_device_class_for_hex(0, "garbage") is False
    assert called["set"] is False


def test_apply_device_class_for_hex_calls_set_with_decoded_int(monkeypatch):
    captured: dict[str, object] = {}

    def _set(idx, cod):
        captured["idx"] = idx
        captured["cod"] = cod
        return True

    monkeypatch.setattr(bt_class_of_device, "set_device_class", _set)
    assert bt_class_of_device.apply_device_class_for_hex(2, "0x00010c") is True
    assert captured == {"idx": 2, "cod": 0x00010C}
