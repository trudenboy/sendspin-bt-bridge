"""Tests for ``services.bt_rssi_mgmt`` — the kernel mgmt-socket wrapper
that asks BlueZ for the live RSSI of a connected BR/EDR peer.

The real ``btsocket`` library opens an ``AF_BLUETOOTH`` mgmt socket and
issues opcode 0x0031 (``MGMT_OP_GET_CONN_INFO``).  We can't open that
socket in CI, so every test here monkey-patches the indirection seam
``_get_btmgmt_sync`` to return a fake module whose ``send`` we control.

Each test exercises one branch of the wrapper's failure-mode contract
so that ``None`` truly means "no fresh value, keep last known" — never
a stale or made-up integer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest  # noqa: TC002 — runtime-used (monkeypatch fixture type-hint)

from services import bt_rssi_mgmt


def _fake_response(*, status: str = "Success", rssi: int | None = -62) -> SimpleNamespace:
    """Mimic the shape of ``btmgmt_sync.send()``'s ``Response`` dataclass.

    Real shape per btsocket:
        Response(header=..., event_frame=<status=...>, cmd_response_frame=<rssi=...>)
    """
    return SimpleNamespace(
        header=SimpleNamespace(),
        event_frame=SimpleNamespace(status=status),
        cmd_response_frame=SimpleNamespace(rssi=rssi),
    )


def _install_fake_btmgmt(monkeypatch: pytest.MonkeyPatch, send: MagicMock) -> None:
    fake_module = SimpleNamespace(send=send)
    monkeypatch.setattr(bt_rssi_mgmt, "_get_btmgmt_sync", lambda: fake_module)


def test_returns_signed_dbm_on_success(monkeypatch):
    send = MagicMock(return_value=_fake_response(rssi=-62))
    _install_fake_btmgmt(monkeypatch, send)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") == -62


def test_converts_unsigned_byte_to_signed_int8(monkeypatch):
    """btsocket reports the wire byte unsigned (0–255); mgmt-api.txt §
    GetConnectionInformation says rssi is **signed int8**.  The wrapper
    must do the >127 → -256 fold so ``200`` becomes ``-56`` dBm
    (typical for a speaker on the next floor)."""
    send = MagicMock(return_value=_fake_response(rssi=200))
    _install_fake_btmgmt(monkeypatch, send)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") == -56


def test_treats_sentinel_127_as_unavailable(monkeypatch):
    """mgmt-api.txt: ``If the RSSI is not available a value of 127
    will be returned``.  Surfacing 127 dBm in the UI would be wildly
    wrong (Bluetooth tops out around +20 dBm); collapse to ``None``."""
    send = MagicMock(return_value=_fake_response(rssi=127))
    _install_fake_btmgmt(monkeypatch, send)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") is None


def test_does_not_drop_minus_128_as_unavailable(monkeypatch):
    """mgmt-api.txt names ONLY 127 as the "RSSI not available"
    sentinel — never -128.  The unsigned wire byte 0x80 (decimal 128)
    folds to a signed -128 dBm reading: a real, very weak signal
    (e.g. a speaker at the edge of range under heavy interference).
    Conflating it with the unavailable sentinel would silently grey
    out the UI chip for legitimate weak-link conditions."""
    send = MagicMock(return_value=_fake_response(rssi=128))
    _install_fake_btmgmt(monkeypatch, send)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") == -128


def test_returns_none_when_status_not_success(monkeypatch):
    """A Failed/InvalidParameters response carries no useful rssi; the
    cmd_response_frame is unspecified and must not be trusted."""
    send = MagicMock(return_value=_fake_response(status="Failed", rssi=-50))
    _install_fake_btmgmt(monkeypatch, send)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") is None


def test_returns_none_when_send_raises(monkeypatch):
    """Mgmt socket can EPERM (no CAP_NET_ADMIN), ENODEV (adapter
    detached), or btsocket can throw a parse error on a malformed
    response — none of these should propagate up into the asyncio loop
    that drives the periodic refresh."""
    send = MagicMock(side_effect=PermissionError("CAP_NET_ADMIN required"))
    _install_fake_btmgmt(monkeypatch, send)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") is None


def test_returns_none_when_btsocket_unavailable(monkeypatch):
    """On non-Linux dev machines (and any container without the dep
    installed) btsocket raises ImportError at module load.  The
    wrapper degrades silently so the rest of the bridge still runs."""

    def _raise():
        raise ImportError("No module named 'btsocket'")

    monkeypatch.setattr(bt_rssi_mgmt, "_get_btmgmt_sync", _raise)

    assert bt_rssi_mgmt.read_conn_info(0, "AA:BB:CC:DD:EE:FF") is None


def test_returns_none_for_negative_adapter_index(monkeypatch):
    """``BluetoothManager._resolve_adapter_index`` returns -1 when
    sysfs lookup failed at startup; the wrapper short-circuits so
    btsocket isn't asked to address controller 0xFFFF and emit a
    confusing error in the logs."""
    send = MagicMock()
    _install_fake_btmgmt(monkeypatch, send)

    assert bt_rssi_mgmt.read_conn_info(-1, "AA:BB:CC:DD:EE:FF") is None
    send.assert_not_called()


def test_passes_adapter_index_and_mac_through(monkeypatch):
    """The wrapper must forward the integer adapter index and the
    MAC string positionally to ``btmgmt_sync.send`` — the mgmt
    response addresses the correct controller and peer.  BR/EDR
    address type (0) is hard-coded; we don't currently support LE."""
    send = MagicMock(return_value=_fake_response(rssi=-40))
    _install_fake_btmgmt(monkeypatch, send)

    bt_rssi_mgmt.read_conn_info(1, "11:22:33:44:55:66")

    send.assert_called_once()
    args = send.call_args.args
    assert args[0] == "GetConnectionInformation"
    assert args[1] == 1
    assert args[2] == "11:22:33:44:55:66"
    assert args[3] == 0  # BR/EDR address type
