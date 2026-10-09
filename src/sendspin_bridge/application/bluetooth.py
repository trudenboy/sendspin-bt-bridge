"""Bluetooth: adapters, discovery, pairing, the BlueZ device cache.

Scans, pairings and resets run as jobs (see ``application.jobs``) and hold the
adapter lease for their whole run, so two Bluetooth operations never overlap.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import time
from typing import Any

from sendspin_bridge.application.errors import UseCaseError, normalize_adapter, validate_mac
from sendspin_bridge.application.jobs import Job, JobContext, jobs
from sendspin_bridge.bluetooth.adapter_map import hci_for
from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
from sendspin_bridge.bluetooth.bluez import Adapter, Outcome, get_bluez
from sendspin_bridge.bluetooth.controller import get_controller
from sendspin_bridge.bluetooth.pairing import PairOptions, PairSession, PairTimings
from sendspin_bridge.config import load_config
from sendspin_bridge.services.bluetooth import (
    COMMON_BT_PAIR_PINS,
    build_hci_map,
    classify_audio_capability,
    get_adapter_alias,
    list_bt_adapters,
)
from sendspin_bridge.services.bluetooth import bt_remove_device as _bt_remove_device
from sendspin_bridge.services.bluetooth.bt_class_of_device import read_device_class as _read_device_class
from sendspin_bridge.services.bluetooth.pairing_quiesce import quiesce_adapter_peers

logger = logging.getLogger(__name__)

_scan_lock = threading.Lock()
_PAIR_SCAN_DURATION = 12  # seconds to scan before pairing
_PAIR_WAIT_DURATION = 15  # seconds to wait for pairing to complete


def _canonicalise_mac_input(mac: str) -> str | None:
    """Normalise a user-supplied MAC into the canonical XX:XX:XX:XX:XX:XX form.

    Accepts colon, dash, or no-separator forms (mirroring the
    ``MprisRegistry`` tolerance) and returns ``None`` if the input does
    not contain exactly 12 hex digits.  The caller still runs
    ``validate_mac`` on the result so any out-of-band malformed payload
    (non-string, oversized) is rejected with a clean 400.
    """
    if not isinstance(mac, str):
        return None
    hex_only = "".join(ch for ch in mac if ch.isalnum())
    if len(hex_only) != 12 or not all(c in "0123456789abcdefABCDEF" for c in hex_only):
        return None
    return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2)).upper()


def _device_info_payload(info, mac: str) -> dict:
    """The public ``/api/bt/info`` JSON shape — ``mac`` + ``raw`` stdout
    lines + the INFO_FIELDS keys, reproduced by ``DeviceInfo`` exactly
    (``static/app.js`` renders ``raw`` verbatim in the info modal)."""
    payload = {"mac": mac, "raw": list(info.raw)}
    payload.update(info.fields)
    return payload


def _get_bt_device_info(mac: str, adapter: str = "") -> dict:
    """Return ``bluetoothctl info`` for ``mac``, adapter-aware.

    With ``adapter`` explicit, the query is scoped by ``select`` (``hciN``
    is resolved to the controller MAC by the BluezControl chain — HAOS/LXC
    reject ``select hciN``). Without ``adapter``, each known controller is
    probed in turn and the first response that actually contains device
    fields (``Name:``/``Paired:``/…) wins; this is what lets the info
    modal work for bonds on the non-default radio when older UI call
    sites haven't been updated to pass the adapter yet.
    """
    bluez = get_bluez()
    if adapter:
        return _device_info_payload(bluez.device_info(mac, Adapter.select(adapter)), mac)

    try:
        adapter_macs = [m.upper() for m in list_bt_adapters() if m]
    except Exception:  # pragma: no cover - defensive
        adapter_macs = []

    last_info = None
    for adapter_mac in adapter_macs:
        info = bluez.device_info(mac, Adapter.select(adapter_mac))
        if info.fields:
            return _device_info_payload(info, mac)
        last_info = info

    if last_info is not None:
        return _device_info_payload(last_info, mac)
    return _device_info_payload(bluez.device_info(mac), mac)


def _resolve_adapter_to_mac(adapter: str) -> str:
    """Translate ``hciN`` → controller MAC for ``bluetoothctl select``.

    ``bluetoothctl select hci0`` fails with ``Controller hci0 not
    available`` on HAOS and LXC containers where the D-Bus objects are
    keyed by MAC, not by interface name.  When the bridge's fleet-row
    ``<select>`` emits ``hci0``/``hci1`` we must resolve it against
    ``bluetoothctl list`` (ordered) before issuing any ``select``. If
    resolution fails (adapters all down, etc.) the original ``hciN`` is
    returned so the caller can still attempt it — a failed ``select``
    at least surfaces as a visible paring failure, while silently
    dropping the prefix would run the flow against the default
    controller.  MAC inputs pass through unchanged.
    """
    if not adapter or not adapter.startswith("hci"):
        return adapter
    try:
        idx = int(adapter[3:])
    except ValueError:
        return adapter
    try:
        macs = [m.upper() for m in list_bt_adapters() if m]
    except Exception:  # pragma: no cover - defensive
        return adapter
    # Resolve through the sysfs-backed kernel map first: ``bluetoothctl
    # list`` order is BlueZ registration order, not kernel hciN numbering —
    # positional indexing paired/scanned the wrong physical adapter on
    # hosts where hci1 registers before hci0 (issue #340, hit live on the
    # two-adapter stand where a pair for hci1 silently ran against hci0).
    kernel_hci = adapter.lower()
    hci_map = build_hci_map()
    if hci_map:
        for mac in macs:
            if hci_for(hci_map, mac) == kernel_hci:
                return mac
        return adapter  # mapped nowhere — let the failed select surface loudly
    # Sysfs gave nothing (Docker without /sys, or kernels whose
    # /sys/class/bluetooth/hciN lacks the ``address`` file — seen live on the
    # rc.1 stand).  The D-Bus object path /org/bluez/hciN is keyed by the
    # kernel index unambiguously — prefer it over list position.
    dbus_addr = get_controller().adapter_address(kernel_hci)
    if dbus_addr:
        return dbus_addr.upper()
    # No sysfs/hciconfig/D-Bus visibility: the adapters endpoint fell back
    # to synthetic ``hci{i}`` labels in list order, so mirror that here.
    if 0 <= idx < len(macs):
        return macs[idx]
    return adapter


_MAX_SCAN_RESULTS = 50

_last_scan_completed: float = 0.0
_SCAN_COOLDOWN = 10.0  # seconds between scans
_SCAN_BASE_DURATION = 15
_SCAN_ADAPTER_OVERHEAD = 2


def _coerce_scan_audio_only(value) -> bool:
    """Return a normalized audio-only flag from request JSON."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("Invalid audio_only flag")


def _resolve_scan_adapter_macs(adapter: str) -> list[str]:
    """Resolve a selected adapter identifier into bluetoothctl adapter MACs."""
    adapter_macs = list_bt_adapters()
    normalized = adapter.strip()
    if not normalized or normalized.lower() == "all":
        raise ValueError("A specific Bluetooth adapter is required")
    if normalized.lower().startswith("hci"):
        kernel_hci = normalized.lower()
        try:
            idx = int(kernel_hci[3:])
        except ValueError as exc:
            raise ValueError("Invalid adapter identifier") from exc
        # The UI's adapter ids are kernel hciN labels resolved via sysfs
        # (/api/bt/adapters, issue #193).  Resolve them back through the
        # same map — ``bluetoothctl list`` order is BlueZ registration
        # order, not kernel numbering, so indexing the list positionally
        # scanned the wrong physical adapter (issue #340).
        hci_map = build_hci_map()
        if hci_map:
            for mac in adapter_macs:
                if hci_for(hci_map, mac) == kernel_hci:
                    return [mac.upper()]
            raise ValueError("Selected adapter is not available")
        # No sysfs/hciconfig visibility: the adapters endpoint fell back
        # to synthetic ``hci{i}`` labels in list order, so mirror that.
        if idx < 0 or idx >= len(adapter_macs):
            raise ValueError("Selected adapter is not available")
        return [adapter_macs[idx].upper()]
    normalized = normalized.upper()
    if normalized not in {mac.upper() for mac in adapter_macs}:
        raise ValueError("Selected adapter is not available")
    return [normalized]


def _build_scan_options(adapter: str, audio_only: bool, adapter_macs: list[str]) -> dict:
    """Build the public scan-options payload returned to the UI."""
    return {
        "adapter": adapter,
        "audio_only": audio_only,
        "adapter_scope": "selected",
        "adapter_count": len(adapter_macs),
    }


def _estimate_scan_duration(adapter_macs: list[str]) -> int:
    """Return a client-facing timed-scan duration hint in seconds."""
    return _SCAN_BASE_DURATION + max(len(adapter_macs) - 1, 0) * _SCAN_ADAPTER_OVERHEAD


def _describe_discovery_refusal(errors: tuple[str, ...]) -> str:
    """Turn ``Failed to start discovery: …`` into operator-facing guidance.

    A controller whose firmware has wedged answers every discovery request
    with an error and then goes quiet, which used to be reported as "no
    devices found" — the one outcome that hides the actual fault.
    """
    reason = errors[0]
    lowered = reason.lower()
    if "inprogress" in lowered:
        detail = "the adapter reports a discovery already in progress"
    elif "notready" in lowered:
        detail = "the adapter is not ready — it may be powered off or blocked by rfkill"
    else:
        detail = f"the adapter refused it ({reason})"
    return (
        f"Bluetooth discovery could not be started: {detail}. "
        "Power-cycle the adapter (Reboot adapter), or unplug and replug the USB dongle, then scan again."
    )


def _resolve_unnamed_devices(all_macs: set[str], names: dict[str, str]) -> None:
    """Look up names for unnamed devices from the bluetoothctl device cache."""
    unnamed = {mac for mac in all_macs if mac not in names}
    if not unnamed:
        return
    for entry in get_bluez().list_devices(filter=""):
        if entry.mac in unnamed and entry.name:
            names[entry.mac] = entry.name


def _enrich_scan_device(mac: str, names: dict[str, str], audio_only: bool = True) -> tuple[dict | None, str | None]:
    """Return ``(device_info_or_None, drop_reason_or_None)``.

    ``device_info`` is ``None`` when the device was filtered out by
    ``audio_only``; ``drop_reason`` is populated in that case so the caller
    can aggregate scan reject stats for support diagnostics.
    """
    if not validate_mac(mac):
        return {"mac": mac, "name": mac, "audio_capable": True}, None
    info = get_bluez().device_info(mac, timeout=4.0)
    if info.outcome is not Outcome.OK:
        # Legacy contract: never drop a scannable speaker on the strength of
        # an info block the command itself reported as failed — a non-zero
        # exit can still leave a partial block behind.
        return {"mac": mac, "name": names.get(mac, mac), "audio_capable": True}, None
    if mac not in names and info.name and not re.match(r"^[0-9A-Fa-f]{2}[-:]", info.name):
        names[mac] = info.name
    audio_capable, reason = classify_audio_capability(info)
    if audio_only and not audio_capable:
        logger.info(
            "BT scan filter dropped %s (name=%s, reason=%s)",
            mac,
            names.get(mac, ""),
            reason,
        )
        return None, reason
    info_rssi = info.rssi
    device_info: dict = {"mac": mac, "name": names.get(mac, mac), "audio_capable": audio_capable}
    if info_rssi is not None:
        device_info["rssi_dbm"] = info_rssi
    return device_info, None


def _annotate_scan_conflicts(devices: list[dict]) -> None:
    """Add ``warning`` field to devices that are already registered on another bridge."""
    try:
        cfg = load_config()
        if not cfg.get("DUPLICATE_DEVICE_CHECK", True):
            return
        ma_url = str(cfg.get("MA_API_URL") or "").strip()
        ma_token = str(cfg.get("MA_API_TOKEN") or "").strip()
        bridge_name = str(cfg.get("BRIDGE_NAME") or "").strip()
        if not ma_url or not ma_token:
            return
        macs = [d["mac"] for d in devices if d.get("mac")]
        if not macs:
            return
        from sendspin_bridge.services.bluetooth.duplicate_device_check import find_scan_device_conflicts

        conflicts = find_scan_device_conflicts(macs, ma_url, ma_token, bridge_name)
        for d in devices:
            warning = conflicts.get(str(d.get("mac") or "").strip().upper())
            if warning:
                d["warning"] = warning
    except Exception:
        logger.debug("Scan conflict annotation failed", exc_info=True)


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


def _lease(reason: str, *, adapter: str = "", bt_manager=None):
    """The exclusive adapter lease, or 409 when another Bluetooth operation holds it."""
    handle = getattr(bt_manager, "adapter_handle", None) if bt_manager is not None else None
    if handle is None:
        handle = AdapterHandle(adapter=adapter)
    lease = handle.try_lease(reason)
    if lease is None:
        raise UseCaseError(409, "bluetooth_busy", "Another Bluetooth operation is already in progress")
    return lease


def start_leased_job(kind: str, work, *, lease, subject: str | None = None, progress: dict | None = None) -> Job:
    """A job that releases *lease* when it ends — and here, if it never starts."""

    def _run(ctx: JobContext):
        try:
            return work(ctx)
        finally:
            lease.release()

    try:
        return jobs.start(kind, _run, subject=subject, progress=progress)
    except Exception:
        lease.release()
        raise


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def list_adapters() -> list[dict[str, Any]]:
    """Controllers with their kernel hciN name (from sysfs, issue #193), alias, power and live CoD."""
    macs = list_bt_adapters()
    hci_map = build_hci_map()
    adapters = []
    for i, mac in enumerate(macs):
        kernel_hci_sysfs = hci_for(hci_map, mac) or None
        kernel_hci = kernel_hci_sysfs or f"hci{i}"
        alias, powered = get_adapter_alias(mac)
        live_cod = None
        if kernel_hci_sysfs and kernel_hci_sysfs.startswith("hci") and kernel_hci_sysfs[3:].isdigit():
            live_cod = _read_device_class(int(kernel_hci_sysfs[3:]))
        adapters.append(
            {
                "id": kernel_hci,
                "mac": mac,
                "name": alias or kernel_hci,
                "powered": powered,
                "live_class": f"0x{live_cod:06x}" if live_cod is not None else None,
            }
        )
    return adapters


def set_adapter_power(adapter: str, on: bool) -> bool:
    adapter = normalize_adapter(adapter)
    result = get_controller().power(bool(on), Adapter.of(adapter))
    if result.outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE):
        logger.error("Failed to toggle adapter power: %s", result.detail or result.outcome.value)
        raise UseCaseError(502, "adapter_power_failed", "Failed to toggle adapter power")
    from sendspin_bridge.application.diagnostics import invalidate_preflight_probe

    # The host just changed; the next status must measure it, not reuse the sample.
    invalidate_preflight_probe()
    return bool(result.applied)


# ---------------------------------------------------------------------------
# The BlueZ device cache
# ---------------------------------------------------------------------------


def known_devices(*, named_only: bool = True) -> list[dict[str, Any]]:
    """Devices BlueZ knows (paired or cached) across every adapter; bridge speakers first."""
    adapter_macs = [str(mac).upper() for mac in list_bt_adapters() if mac]
    merged: dict[str, dict] = {}

    def _ingest(pairs, adapter_mac: str = "") -> None:
        for mac, name in pairs:
            entry = merged.setdefault(mac, {"name": "", "adapters": set()})
            if name and not entry["name"]:
                entry["name"] = name
            if adapter_mac:
                entry["adapters"].add(adapter_mac)

    bluez = get_bluez()
    if adapter_macs:
        for adapter in adapter_macs:
            _ingest(list(bluez.list_devices(Adapter.select(adapter))), adapter)
    else:
        _ingest(list(bluez.list_devices(filter="")))
    devices: list[dict[str, Any]] = [
        {"mac": mac, "name": entry["name"] or mac, "adapters": sorted(entry["adapters"])}
        for mac, entry in merged.items()
        if entry["name"] or not named_only
    ]
    bridge_macs = {d.get("mac", "").upper() for d in load_config().get("BLUETOOTH_DEVICES", []) if d.get("mac")}
    devices.sort(key=lambda d: (0 if d["mac"] in bridge_macs else 1, d["name"].lower()))
    return devices


def device_info(mac: str, adapter: str = "") -> dict[str, Any]:
    """``bluetoothctl info``-shaped fields for one device (``raw`` lines included)."""
    return _get_bt_device_info(canonical_mac(mac), normalize_adapter(adapter))


def remove_device(mac: str, adapter_mac: str = "") -> None:
    """Drop the bond: on *adapter_mac*, or on every adapter when omitted."""
    mac = canonical_mac(mac)
    adapter_mac = (adapter_mac or "").strip().upper()
    if adapter_mac and not validate_mac(adapter_mac):
        raise UseCaseError(400, "invalid_adapter", "Invalid adapter MAC")
    adapters = [str(a).upper() for a in list_bt_adapters() if a]
    if adapter_mac:
        # An unknown controller makes ``select`` fail silently and the remove
        # run against the default one — a misleading success.
        if adapters and adapter_mac not in adapters:
            raise UseCaseError(400, "unknown_adapter", f"Unknown adapter MAC: {adapter_mac}")
        _bt_remove_device(mac, adapter_mac)
    elif adapters:
        for adapter in adapters:
            _bt_remove_device(mac, adapter)
    else:
        _bt_remove_device(mac, "")


def disconnect_device(mac: str) -> None:
    """Disconnect on the adapter that holds the bond (bonds are per-controller)."""
    mac = canonical_mac(mac)
    bluez = get_bluez()
    owner = ""
    for ref in bluez.list_adapters():
        if any(entry.mac.upper() == mac for entry in bluez.list_devices(Adapter.select(ref.mac))):
            owner = ref.mac
            break
    result = get_controller().disconnect(mac, Adapter.select(owner) if owner else Adapter.DEFAULT)
    if result.outcome is Outcome.FAILED:
        raise UseCaseError(409, "disconnect_refused", result.detail or "Bluetooth disconnect refused")
    if not result.ok:
        logger.error("Failed to disconnect device %s: %s", mac, result.detail or result.outcome.value)
        raise UseCaseError(502, "disconnect_failed", "Bluetooth disconnect failed")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _scan(adapter: str, audio_only: bool) -> dict[str, Any]:
    adapter_macs = _resolve_scan_adapter_macs(adapter)
    # The discovery window equals the duration the client was told to expect.
    transcript = get_bluez().scan(adapter_macs, window_s=float(_SCAN_BASE_DURATION))
    names = dict(transcript.names)
    discovery_errors = transcript.discovery_errors
    if discovery_errors:
        logger.warning("BT scan: adapter refused to start discovery (%s)", "; ".join(discovery_errors))
    all_macs = set(transcript.seen_macs) | set(transcript.active_macs)
    if len(all_macs) > _MAX_SCAN_RESULTS:
        logger.warning("BT scan found %d devices, capping to %d", len(all_macs), _MAX_SCAN_RESULTS)
        all_macs = set(list(all_macs)[:_MAX_SCAN_RESULTS])
    _resolve_unnamed_devices(all_macs, names)

    devices: list[dict] = []
    dropped: dict[str, int] = {}
    if all_macs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_enrich_scan_device, mac, names, audio_only): mac for mac in all_macs}
            for fut in concurrent.futures.as_completed(futures):
                device, drop_reason = fut.result()
                if device is not None:
                    devices.append(device)
                elif drop_reason:
                    dropped[drop_reason] = dropped.get(drop_reason, 0) + 1
    for d in devices:
        d["adapter"] = transcript.device_adapter.get(d["mac"], "")
        d["supports_import"] = bool(d.get("audio_capable", True))
        d["kind"] = "audio" if d.get("audio_capable", True) else "other"
        scan_rssi = transcript.rssi_by_mac.get(d["mac"])
        d["rssi_dbm"] = scan_rssi if scan_rssi is not None else d.get("rssi_dbm")
    _annotate_scan_conflicts(devices)
    devices.sort(key=lambda d: (d["name"] == d["mac"], d["name"]))
    if discovery_errors and not devices:
        # Nothing found *and* discovery never started: say why, instead of "no devices".
        raise UseCaseError(502, "discovery_refused", _describe_discovery_refusal(discovery_errors))
    stats: dict[str, Any] = {
        "total_candidates": len(all_macs),
        "returned_candidates": len(devices),
        "audio_candidates": sum(1 for d in devices if d.get("audio_capable", True)),
        "audio_only": audio_only,
        "dropped_reasons": dropped,
    }
    if discovery_errors:
        stats["discovery_error"] = discovery_errors[0]
    return {"devices": devices, "stats": stats}


def start_scan(adapter: str, *, audio_only: bool = True) -> Job:
    """Discover nearby devices on one adapter (about 15 s)."""
    raw = (adapter or "").strip()
    if not raw or raw.lower() == "all":
        raise UseCaseError(400, "adapter_required", "A specific Bluetooth adapter is required")
    adapter = normalize_adapter(raw)
    try:
        adapter_macs = _resolve_scan_adapter_macs(adapter)
    except ValueError as exc:
        raise UseCaseError(400, "unknown_adapter", str(exc)) from exc
    with _scan_lock:
        if jobs.running("bluetooth.scan"):
            raise UseCaseError(409, "scan_in_progress", "A scan is already in progress")
        since = time.monotonic() - _last_scan_completed
        if since < _SCAN_COOLDOWN:
            raise UseCaseError(
                429,
                "scan_cooldown",
                "Scan cooldown active",
                headers={"Retry-After": str(int(_SCAN_COOLDOWN - since) + 1)},
            )
        lease = _lease(f"scan {adapter}", adapter=adapter)

        def _work(_ctx: JobContext):
            global _last_scan_completed
            try:
                return _scan(adapter, audio_only)
            finally:
                # The cooldown is the adapter's rest after the discovery window.
                _last_scan_completed = time.monotonic()

        return start_leased_job(
            "bluetooth.scan",
            _work,
            lease=lease,
            subject=adapter,
            progress={
                "scan_options": _build_scan_options(adapter, audio_only, adapter_macs),
                "expected_duration": _estimate_scan_duration(adapter_macs),
                "started_at": time.time(),
            },
        )


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def _pair_options(
    *, no_input_no_output_agent: bool, allow_hfp_profile: bool, connect: bool, settle: float = 0.0
) -> PairOptions:
    return PairOptions(
        pins=COMMON_BT_PAIR_PINS,
        # NoInputNoOutput forces Just-Works SSP for speakers that cancel a
        # passkey exchange; opt-in per request (issue #168).
        capability="NoInputNoOutput" if no_input_no_output_agent else "DisplayYesNo",
        allow_hfp=bool(allow_hfp_profile),
        connect_after_trust=connect,
        timings=PairTimings(
            scan_window_s=_PAIR_SCAN_DURATION, pair_wait_s=_PAIR_WAIT_DURATION, post_trust_settle_s=settle
        ),
    )


def _run_standalone_pair(
    mac: str,
    adapter: str,
    *,
    quiesce_adapter: bool = False,
    no_input_no_output_agent: bool = False,
    allow_hfp_profile: bool = False,
) -> dict[str, Any]:
    """Pair and trust *mac* on *adapter*; raises 422 with the failure fingerprint."""
    resolved = _resolve_adapter_to_mac(adapter)
    attempt_context = None
    if quiesce_adapter and resolved:

        def attempt_context():
            # Single-adapter hosts cannot pair while another A2DP link is up.
            return quiesce_adapter_peers(resolved, exclude_mac=mac)

    outcome = PairSession(
        get_bluez(),
        adapter=Adapter.of(resolved),
        mac=mac,
        options=_pair_options(
            no_input_no_output_agent=no_input_no_output_agent, allow_hfp_profile=allow_hfp_profile, connect=False
        ),
        attempt_context=attempt_context,
        label=mac,
    ).run()
    if not outcome.success:
        raise UseCaseError(
            422,
            outcome.failure_kind or "pairing_failed",
            outcome.reason or "Pairing failed — is the device in pairing mode?",
        )
    return {"mac": mac, "paired": True, "agent_telemetry": outcome.agent_telemetry}


def start_pairing(
    mac: str,
    adapter: str = "",
    *,
    quiesce_adapter: bool = False,
    no_input_no_output_agent: bool = False,
    allow_hfp_profile: bool = False,
) -> Job:
    """Pair and trust a device that is in pairing mode. Options apply to this attempt only."""
    mac = canonical_mac(mac)
    adapter = normalize_adapter(adapter)
    lease = _lease(f"pair {mac}", adapter=adapter)

    def _work(_ctx: JobContext):
        return _run_standalone_pair(
            mac,
            adapter,
            quiesce_adapter=quiesce_adapter,
            no_input_no_output_agent=no_input_no_output_agent,
            allow_hfp_profile=allow_hfp_profile,
        )

    return start_leased_job("bluetooth.pairing", _work, lease=lease, subject=mac)


def _run_reset_reconnect(
    mac: str,
    adapter: str,
    *,
    no_input_no_output_agent: bool = False,
    allow_hfp_profile: bool = False,
    ctx: JobContext | None = None,
) -> dict[str, Any]:
    """Drop the bond, power-cycle the controller, then pair + trust + connect.

    The reset is this path's own contribution; the pairing that follows is the
    shared choreography (popular-PIN ladder, failure fingerprint).
    """

    def _step(name: str) -> None:
        if ctx is not None:
            ctx.progress(step=name)

    scope = Adapter.of(_resolve_adapter_to_mac(adapter))
    _step("removing")
    get_controller().remove(mac, scope)
    time.sleep(1)
    # A power cycle clears kernel link state that survives ``remove``.
    _step("power_cycle")
    get_controller().power(False, scope)
    time.sleep(2)
    _step("pairing")
    outcome = PairSession(
        get_bluez(),
        adapter=scope,
        mac=mac,
        options=_pair_options(
            no_input_no_output_agent=no_input_no_output_agent,
            allow_hfp_profile=allow_hfp_profile,
            connect=True,
            # The connect result and the closing ``info`` block land during
            # this window; without it a speaker that did connect reads false.
            settle=5.0,
        ),
        label=mac,
    ).run()
    logger.info("Reset & Reconnect %s: paired=%s connected=%s", mac, outcome.success, outcome.connected)
    if not outcome.success:
        raise UseCaseError(422, outcome.failure_kind or "pairing_failed", outcome.reason or "Pairing failed")
    return {"mac": mac, "paired": True, "connected": outcome.connected, "agent_telemetry": outcome.agent_telemetry}


def start_reset(
    mac: str, adapter: str = "", *, no_input_no_output_agent: bool = False, allow_hfp_profile: bool = False
) -> Job:
    """Drop the bond, power-cycle the controller, then pair, trust and connect again."""
    mac = canonical_mac(mac)
    adapter = normalize_adapter(adapter)
    lease = _lease(f"reset-and-reconnect {mac}", adapter=adapter)

    def _work(ctx: JobContext):
        return _run_reset_reconnect(
            mac,
            adapter,
            no_input_no_output_agent=no_input_no_output_agent,
            allow_hfp_profile=allow_hfp_profile,
            ctx=ctx,
        )

    return start_leased_job("bluetooth.reset", _work, lease=lease, subject=mac)


def canonical_mac(mac: str) -> str:
    """Accept colon, dash or bare forms of a MAC; 400 when it is not one."""
    canonical = _canonicalise_mac_input(mac)
    if canonical is None or not validate_mac(canonical):
        raise UseCaseError(400, "invalid_mac", "Invalid MAC address")
    return canonical


__all__ = [
    "canonical_mac",
    "device_info",
    "disconnect_device",
    "known_devices",
    "list_adapters",
    "remove_device",
    "set_adapter_power",
    "start_leased_job",
    "start_pairing",
    "start_reset",
    "start_scan",
]
