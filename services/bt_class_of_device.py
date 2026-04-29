"""Apply a per-adapter Class of Device override via BlueZ kernel mgmt API.

Workaround for the Samsung Q-series quirk documented in
`bluez/bluez#1025 <https://github.com/bluez/bluez/issues/1025>`_: the
soundbar's BR/EDR firmware filters incoming connection attempts by the
initiator's Class of Device.  When the local adapter's CoD doesn't match
its allowlist (e.g. the bare ``0x0c0000`` that HAOS / RPi default to), the
soundbar replies ``LMP_not_accepted_ext: Limited Resources`` and BlueZ
surfaces the failure as ``HCI Connect Complete status=0x0d`` →
``MGMT Connect Failed: No Resources (0x07)`` →
``org.bluez.Error.AuthenticationCanceled``.  Setting CoD to ``0x00010c``
(Computer / Laptop) is the documented fix from the BlueZ thread.

This module sends ``MGMT_OP_SET_DEV_CLASS`` (opcode ``0x002C``) on a raw
``AF_BLUETOOTH`` mgmt socket — the same channel the existing
``bt_rssi_mgmt`` helper uses for ``GET_CONN_INFO``.  Errors collapse to
``False`` so a missing capability or non-Linux dev box never blocks
startup; the warning is logged once and the bridge moves on.

Per `mgmt-api.txt § Set Device Class
<https://git.kernel.org/pub/scm/bluetooth/bluez.git/tree/doc/mgmt-api.txt>`_
the command can be issued whether or not the controller is currently
powered: a powered controller applies the new CoD immediately, an
unpowered one applies it on the next power-on.
"""

from __future__ import annotations

import logging
import re
import struct
import time

logger = logging.getLogger(__name__)

# Mgmt opcodes / event codes (mgmt-api.txt § Packet Structures + Commands).
_MGMT_OP_SET_DEV_CLASS = 0x002C
_MGMT_EV_CMD_COMPLETE = 0x0001
_MGMT_EV_CMD_STATUS = 0x0002

# Wall-clock budget for the round-trip.  bluetoothd answers Set Device
# Class in a few ms in normal operation; 2 s is generous and bounds how
# long a stuck controller can stall the startup sequence.
_MGMT_DEADLINE_S = 2.0

# Six-hex-digit form: ``0x00010c``.  Anchored both ends so trailing
# whitespace or a stray ``L`` suffix is rejected at parse time, not
# silently ignored by ``int(..., 16)``.
_DEVICE_CLASS_RE = re.compile(r"^0x([0-9a-fA-F]{6})$")


def parse_class_hex(value: str) -> tuple[int, int] | None:
    """Decode a 24-bit Class of Device hex string into ``(major, minor)`` octets.

    The mgmt ``Set Device Class`` command takes two octets; the bluetoothd
    side then derives Service Class bits (23-13) from advertised UUIDs and
    the Format Type bits (1-0 of CoD) are fixed at ``0b00``.

    The hex form encodes the full 24 bits ``Service|Major|Minor|Format``;
    we extract:

    - Major octet: bits 12-8 (low nibble of the middle byte) — e.g. for
      ``0x00010c``, the ``01`` byte yields major ``1`` (Computer).
    - Minor octet: bits 7-2 of the low byte, shifted right by 2 — e.g.
      ``0x0c >> 2 = 3`` (Laptop).

    Returns ``None`` for malformed input so the caller can warn-and-skip
    instead of crashing the startup sequence.

    >>> parse_class_hex("0x00010c")
    (1, 3)
    >>> parse_class_hex("0x000100")
    (1, 0)
    >>> parse_class_hex("not-hex") is None
    True
    """
    if not isinstance(value, str):
        return None
    match = _DEVICE_CLASS_RE.match(value.strip())
    if not match:
        return None
    full = int(match.group(1), 16)
    major = (full >> 8) & 0x1F
    minor = (full >> 2) & 0x3F
    return major, minor


def _open_mgmt_socket():
    """Open a bound HCI control-channel socket.

    Indirection seam so the syscall stays mockable without touching the
    real kernel interface in tests.  ``btsocket`` is imported lazily so
    non-Linux dev installs (which can't bind the socket anyway) don't
    fail at module load — this matches the convention in
    ``services.bt_rssi_mgmt``.
    """
    from btsocket import btmgmt_socket  # type: ignore[import-untyped]

    return btmgmt_socket.open()


def _close_mgmt_socket(sock) -> None:
    try:
        from btsocket import btmgmt_socket  # type: ignore[import-untyped]

        btmgmt_socket.close(sock)
    except Exception:
        pass


def set_device_class(adapter_index: int, major: int, minor: int) -> bool:
    """Apply ``MGMT_OP_SET_DEV_CLASS`` to the given controller.

    ``adapter_index`` is the kernel hci number (``hci0`` → ``0``).
    Returns ``True`` on success, ``False`` on any failure path; logs the
    reason at WARNING.  Never raises — the caller can keep iterating
    over the rest of the controllers.
    """
    if adapter_index < 0:
        logger.warning("CoD: adapter_index=%d is negative; skipping", adapter_index)
        return False
    if not (0 <= major <= 0x1F):
        logger.warning("CoD: major=%d out of range 0..31; skipping", major)
        return False
    if not (0 <= minor <= 0x3F):
        logger.warning("CoD: minor=%d out of range 0..63; skipping", minor)
        return False

    try:
        sock = _open_mgmt_socket()
    except ImportError:
        logger.warning("CoD: btsocket unavailable — cannot set device class on hci%d", adapter_index)
        return False
    except Exception as exc:
        logger.warning("CoD: mgmt socket open failed for hci%d: %s", adapter_index, exc)
        return False

    try:
        payload = bytes([major & 0x1F, minor & 0x3F])
        header = struct.pack("<HHH", _MGMT_OP_SET_DEV_CLASS, adapter_index, len(payload))
        try:
            sock.send(header + payload)
        except Exception as exc:
            logger.warning("CoD: mgmt send failed for hci%d: %s", adapter_index, exc)
            return False

        deadline = time.monotonic() + _MGMT_DEADLINE_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("CoD: mgmt read timeout for hci%d", adapter_index)
                return False
            try:
                sock.settimeout(remaining)
                data = sock.recv(512)
            except TimeoutError:
                logger.warning("CoD: mgmt read timeout for hci%d", adapter_index)
                return False
            except Exception as exc:
                logger.warning("CoD: mgmt recv failed for hci%d: %s", adapter_index, exc)
                return False
            if len(data) < 6:
                continue
            ev_code, ev_idx = struct.unpack_from("<HH", data, 0)
            # bluetoothd shares the control channel; skip events for
            # other controllers or unrelated commands so we don't fold a
            # neighbour's reply into our success/failure decision.
            if ev_idx != adapter_index:
                continue
            if len(data) < 9:
                continue
            cmd_op, status = struct.unpack_from("<HB", data, 6)
            if ev_code == _MGMT_EV_CMD_STATUS:
                if cmd_op == _MGMT_OP_SET_DEV_CLASS:
                    logger.warning(
                        "CoD: hci%d Set Device Class failed early with mgmt status=%d",
                        adapter_index,
                        status,
                    )
                    return False
                continue
            if ev_code != _MGMT_EV_CMD_COMPLETE or cmd_op != _MGMT_OP_SET_DEV_CLASS:
                continue
            if status != 0:
                logger.warning(
                    "CoD: hci%d Set Device Class returned mgmt status=%d",
                    adapter_index,
                    status,
                )
                return False
            return True
    finally:
        _close_mgmt_socket(sock)


def apply_device_class_for_hex(adapter_index: int, hex_value: str) -> bool:
    """Convenience wrapper: parse hex form then call :func:`set_device_class`.

    Returns ``False`` (without logging extra noise) when ``hex_value``
    is empty so the orchestrator can call this unconditionally on every
    adapter and let the helper decide whether to act.
    """
    if not hex_value:
        return False
    parsed = parse_class_hex(hex_value)
    if parsed is None:
        logger.warning(
            "CoD: hci%d device_class %r is not a valid 6-hex-digit string; ignoring",
            adapter_index,
            hex_value,
        )
        return False
    major, minor = parsed
    ok = set_device_class(adapter_index, major, minor)
    if ok:
        logger.info(
            "CoD: hci%d device class set to %s (major=%d, minor=%d)",
            adapter_index,
            hex_value.lower(),
            major,
            minor,
        )
    return ok
