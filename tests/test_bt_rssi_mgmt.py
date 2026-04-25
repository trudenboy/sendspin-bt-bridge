"""Tests for ``services.bt_rssi_mgmt`` — the kernel mgmt-socket reader
that asks BlueZ for the live RSSI of a connected BR/EDR peer.

The real implementation opens an ``AF_BLUETOOTH`` raw socket, sends a
mgmt opcode 0x0031 packet, and parses the ``CommandComplete`` event.
We can't open that socket in CI, so the tests target two seams:

- ``read_conn_info`` is the public API — sentinel/sign handling.  We
  monkey-patch ``_query_rssi_byte`` to feed it specific raw bytes.
- ``_query_rssi_byte`` is the syscall layer — we monkey-patch
  ``_open_mgmt_socket`` with a fake socket that returns a
  pre-baked CommandComplete blob, exercising the wire-format parser
  and the event-skipping loop.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock

from services import bt_rssi_mgmt

# ── public API: read_conn_info → int | None ─────────────────────────


def test_returns_signed_dbm_on_success(monkeypatch):
    """Wire byte 194 (= 0xC2) folds to -62 dBm — a typical
    in-the-same-room RSSI."""
    monkeypatch.setattr(bt_rssi_mgmt, "_query_rssi_byte", lambda *_: 194)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") == -62


def test_converts_unsigned_byte_to_signed_int8(monkeypatch):
    """The mgmt API field is declared signed int8 but socket bytes
    arrive unsigned; ``200`` (= 0xC8) must fold to ``-56``."""
    monkeypatch.setattr(bt_rssi_mgmt, "_query_rssi_byte", lambda *_: 200)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") == -56


def test_treats_sentinel_127_as_unavailable(monkeypatch):
    """mgmt-api.txt: ``If the RSSI is not available a value of 127
    will be returned``.  Surfacing 127 dBm in the UI would be wildly
    wrong (Bluetooth tops out around +20 dBm); collapse to ``None``."""
    monkeypatch.setattr(bt_rssi_mgmt, "_query_rssi_byte", lambda *_: 127)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") is None


def test_does_not_drop_minus_128_as_unavailable(monkeypatch):
    """mgmt-api.txt names ONLY 127 as the "unavailable" sentinel —
    never -128.  Wire byte 128 (0x80) folds to a signed -128 dBm: a
    real, very weak signal.  Conflating it with the sentinel would
    silently grey out the UI chip for legitimate edge-of-range links."""
    monkeypatch.setattr(bt_rssi_mgmt, "_query_rssi_byte", lambda *_: 128)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") == -128


def test_returns_none_when_query_returns_none(monkeypatch):
    """``_query_rssi_byte`` collapses every syscall failure (EPERM,
    timeout, parse error, peer not connected, btsocket import error)
    into ``None``.  ``read_conn_info`` must propagate that ``None``
    upward, not raise and not synthesise a number."""
    monkeypatch.setattr(bt_rssi_mgmt, "_query_rssi_byte", lambda *_: None)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") is None


def test_returns_none_for_negative_adapter_index(monkeypatch):
    """``BluetoothManager._resolve_adapter_index`` returns -1 when
    sysfs lookup failed at startup; short-circuit before any syscall."""
    query = MagicMock()
    monkeypatch.setattr(bt_rssi_mgmt, "_query_rssi_byte", query)

    assert bt_rssi_mgmt.read_conn_info(-1, "AA:BB:CC:DD:EE:FF") is None
    query.assert_not_called()


# ── syscall layer: _query_rssi_byte → unsigned wire byte | None ─────


def _build_cmd_complete(*, status: int = 0, mac: str = "AA:BB:CC:DD:EE:FF", rssi_byte: int = 200) -> bytes:
    """Build the on-the-wire bytes BlueZ would emit for a successful
    GetConnectionInformation.  Used to feed the parser through a
    fake socket without going near a real kernel mgmt channel.

    Layout: header (event_code u16, idx u16, paramlen u16) + payload
    (cmd_opcode u16, status u8, addr 6 bytes LE, addr_type u8,
    rssi u8, tx_power u8, max_tx_power u8).
    """
    addr = bytes(int(b, 16) for b in reversed(mac.split(":")))
    payload = (
        struct.pack("<HB", bt_rssi_mgmt._MGMT_OP_GET_CONN_INFO, status)
        + addr
        + bytes([bt_rssi_mgmt._ADDR_TYPE_BREDR, rssi_byte, 0, 0])
    )
    header = struct.pack("<HHH", bt_rssi_mgmt._MGMT_EV_CMD_COMPLETE, 0, len(payload))
    return header + payload


class _FakeMgmtSocket:
    """Records ``send`` payloads and replays a queue of ``recv`` blobs."""

    def __init__(self, recv_queue: list[bytes]):
        self._recv_queue = list(recv_queue)
        self.sent: list[bytes] = []
        self.timeouts: list[float] = []
        self.closed = False

    def settimeout(self, t):
        self.timeouts.append(t)

    def send(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def recv(self, _bufsize):
        if not self._recv_queue:
            raise TimeoutError("no more pre-baked recv blobs")
        return self._recv_queue.pop(0)


def test_query_parses_rssi_byte_from_command_complete(monkeypatch):
    """End-to-end through the real parser: a well-formed
    CommandComplete with rssi byte 200 yields 200 (the unsigned wire
    value); the public ``read_conn_info`` is what folds to signed dBm."""
    fake = _FakeMgmtSocket([_build_cmd_complete(rssi_byte=200)])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") == 200


def test_query_sends_correct_opcode_index_addr_type(monkeypatch):
    """The crash that prompted this rewrite was a wire-format bug —
    pin the exact bytes emitted so we'd notice if a future refactor
    flipped endianness, swapped addr type, or used the bitmask
    encoding btsocket got wrong."""
    fake = _FakeMgmtSocket([_build_cmd_complete()])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    bt_rssi_mgmt._query_rssi_byte(2, "11:22:33:44:55:66")

    assert len(fake.sent) == 1
    pkt = fake.sent[0]
    op, idx, plen = struct.unpack_from("<HHH", pkt, 0)
    assert op == bt_rssi_mgmt._MGMT_OP_GET_CONN_INFO
    assert idx == 2
    assert plen == 7  # 6 addr + 1 addr_type
    # MAC bytes follow header in *little-endian* order
    assert pkt[6:12] == bytes([0x66, 0x55, 0x44, 0x33, 0x22, 0x11])
    # And the discriminator byte for BR/EDR — the bug we're guarding
    # against was btsocket emitting 0x01 (bitmask LSB-set) here.
    assert pkt[12] == 0x00


def test_query_skips_unrelated_events_until_match(monkeypatch):
    """Bluetoothd shares the control channel; a stray
    ``IndexAdded`` or ``CommandComplete`` for someone else's command
    will land in our ``recv`` first.  The reader must skip them and
    keep going until our matching opcode echoes back."""
    # Fake 1: CmdComplete for a different opcode (0x0006 = ReadInfo)
    other = struct.pack("<HHH", bt_rssi_mgmt._MGMT_EV_CMD_COMPLETE, 0, 3) + struct.pack("<HB", 0x0006, 0)
    # Fake 2: IndexAdded event (0x0004) — no payload
    index_added = struct.pack("<HHH", 0x0004, 0xFFFF, 0)
    # Fake 3: our actual reply
    ours = _build_cmd_complete(rssi_byte=180)

    fake = _FakeMgmtSocket([other, index_added, ours])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") == 180


def test_query_returns_none_on_command_status_failure(monkeypatch):
    """``CommandStatus`` (event 0x0002) is BlueZ's "couldn't even
    start" response — most commonly emitted when the peer isn't
    actually connected, which will happen every refresh tick during
    the few seconds between an ACL drop and our state catching up."""
    cmd_status = struct.pack("<HHH", bt_rssi_mgmt._MGMT_EV_CMD_STATUS, 0, 3) + struct.pack(
        "<HB",
        bt_rssi_mgmt._MGMT_OP_GET_CONN_INFO,
        0x0E,  # NotConnected
    )
    fake = _FakeMgmtSocket([cmd_status])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") is None


def test_query_returns_none_on_command_complete_nonzero_status(monkeypatch):
    """If the kernel responds with CommandComplete but a non-zero
    status (e.g. InvalidParameters because we asked for the wrong
    address type), the rssi field is unspecified and must be dropped."""
    fake = _FakeMgmtSocket([_build_cmd_complete(status=0x05)])  # AuthenticationFailed
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") is None


def test_query_returns_none_when_open_raises_import(monkeypatch):
    """On non-Linux dev machines (and any container without btsocket
    installed) the open helper raises ``ImportError`` — degrade
    silently so the rest of the bridge still runs and the refresh
    loop just emits no values."""

    def _raise():
        raise ImportError("No module named 'btsocket'")

    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", _raise)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") is None


def test_query_returns_none_when_open_raises_other(monkeypatch):
    """Mgmt socket open can EPERM (no CAP_NET_ADMIN), ENODEV
    (kernel module not loaded), or fail with various ctypes errors —
    none of these should propagate up into the asyncio refresh loop."""

    def _raise():
        raise PermissionError("CAP_NET_ADMIN required")

    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", _raise)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") is None


def test_query_returns_none_on_malformed_mac(monkeypatch):
    """Defensive: a MAC string from a wonky config (extra colon, hex
    typo) must not crash the refresh loop with ValueError."""
    fake = _FakeMgmtSocket([])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "not-a-mac") is None
    # And we never sent anything to the kernel.
    assert fake.sent == []


def test_query_returns_none_for_short_mac(monkeypatch):
    """A 5-octet MAC ('AA:BB:CC:DD:EE') would silently produce a
    5-byte BD_ADDR; the kernel would either return InvalidParameters
    or — worse — interpret a payload byte as an addr_type byte and
    pick a random peer.  Validate the octet count before encoding."""
    fake = _FakeMgmtSocket([])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE") is None
    assert fake.sent == []


def test_query_returns_none_for_long_mac(monkeypatch):
    """Symmetric guard for a 7-octet input."""
    fake = _FakeMgmtSocket([])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF:00") is None
    assert fake.sent == []


def _build_cmd_complete_with_idx(
    *,
    idx: int = 0,
    mac: str = "AA:BB:CC:DD:EE:FF",
    echoed_mac: str | None = None,
    rssi_byte: int = 200,
) -> bytes:
    """Variant of ``_build_cmd_complete`` that lets a test set the
    header ``controller_idx`` and an arbitrary echoed BD_ADDR — used
    to simulate replies from a different controller or for a different
    peer that bluetoothd may forward on the shared control channel."""
    addr = bytes(int(b, 16) for b in reversed((echoed_mac or mac).split(":")))
    payload = (
        struct.pack("<HB", bt_rssi_mgmt._MGMT_OP_GET_CONN_INFO, 0)
        + addr
        + bytes([bt_rssi_mgmt._ADDR_TYPE_BREDR, rssi_byte, 0, 0])
    )
    header = struct.pack("<HHH", bt_rssi_mgmt._MGMT_EV_CMD_COMPLETE, idx, len(payload))
    return header + payload


def test_query_skips_event_for_different_controller_index(monkeypatch):
    """On a host with hci0 + hci1, both bound to the same control
    channel by other clients (bluetoothd, btmgmt), our recv may pick
    up a sibling adapter's CommandComplete first.  The header's
    controller_idx must gate the match — without it we'd return RSSI
    measured on the wrong adapter for a coincidentally-same MAC, or
    falsely return None when our actual reply still arrives later."""
    foreign = _build_cmd_complete_with_idx(idx=1, rssi_byte=42)
    ours = _build_cmd_complete_with_idx(idx=0, rssi_byte=180)
    fake = _FakeMgmtSocket([foreign, ours])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") == 180


def test_query_skips_reply_for_wrong_echoed_mac(monkeypatch):
    """``GetConnectionInformation`` echoes the queried BD_ADDR back in
    the CommandComplete payload.  If another mgmt client on the
    control channel issued the same opcode for a different peer just
    before us, we'd otherwise return that peer's RSSI as if it were
    ours — wrong reading on the wrong device card."""
    other_peer = _build_cmd_complete_with_idx(echoed_mac="11:22:33:44:55:66", rssi_byte=42)
    ours = _build_cmd_complete_with_idx(echoed_mac="AA:BB:CC:DD:EE:FF", rssi_byte=180)
    fake = _FakeMgmtSocket([other_peer, ours])
    monkeypatch.setattr(bt_rssi_mgmt, "_open_mgmt_socket", lambda: fake)
    monkeypatch.setattr(bt_rssi_mgmt, "_close_mgmt_socket", lambda _s: None)

    assert bt_rssi_mgmt._query_rssi_byte(0, "AA:BB:CC:DD:EE:FF") == 180
