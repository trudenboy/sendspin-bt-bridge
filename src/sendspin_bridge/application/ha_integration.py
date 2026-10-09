"""Home Assistant integration: state projection, MQTT/REST/mDNS diagnostics, commands.

The HA custom component reads the projection and sends commands through the
compat router; the SPA's Home Assistant settings use the diagnostics here.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any

from sendspin_bridge.application.errors import UseCaseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /api/ha/state — dehydrated projection bootstrap
# ---------------------------------------------------------------------------


def _build_projection_for_request():
    """Build a fresh ``HAStateProjection`` from the live bridge snapshot."""
    from sendspin_bridge.bridge.state import get_clients_snapshot
    from sendspin_bridge.config import ensure_bridge_name, get_runtime_version, load_config
    from sendspin_bridge.services.ha.ha_state_projector import project_snapshot
    from sendspin_bridge.services.lifecycle.status_snapshot import build_bridge_snapshot

    config = load_config()
    bridge_name = ensure_bridge_name(config)
    bridge_id = hashlib.sha1(bridge_name.encode("utf-8")).hexdigest()[:12]
    snapshot = build_bridge_snapshot(get_clients_snapshot())
    return project_snapshot(
        snapshot,
        bridge_id=bridge_id,
        bridge_name=bridge_name,
        runtime_extras={"version": get_runtime_version()},
    )


def state_projection() -> dict[str, Any]:
    """The full HA entity-state projection (the custom component's first refresh)."""
    try:
        return _build_projection_for_request().to_json()
    except Exception as exc:
        logger.exception("Failed to build HA state projection")
        raise UseCaseError(500, "projection_failed", "Failed to build projection") from exc


# ---------------------------------------------------------------------------
# /api/status/events — typed event SSE channel
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/ha/mqtt/probe — Supervisor MQTT auto-detect
# ---------------------------------------------------------------------------


def mqtt_probe() -> dict[str, Any]:
    """Probe for an MQTT broker the bridge can talk to.

    Used by the web UI's "Auto-detect MQTT add-on" button.  Two paths:

    1. **HA add-on (Supervisor available)** — query Supervisor for the
       Mosquitto add-on's full credentials (host, port, user, pass).
       Returns ``source: "supervisor"`` and ``password_present`` so the
       UI knows the secret is already on the bridge side.

    2. **Standalone (no Supervisor)** — derive a *suggested* broker
       host from the configured Music Assistant URL.  When MA runs as
       an HA add-on (the common harryfine-style topology — bridge in
       Docker, MA on HAOS), Mosquitto sits on the same host, so the
       MA host is a strong default.  Returns ``source: "ma_url"`` and
       ``password_present: false`` so the UI prompts for credentials.

    Returns the credentials *without* the password (the password lives
    in ``config.json`` after a save) and a ``found`` flag.
    """
    try:
        from sendspin_bridge.services.ha.ha_addon import (
            derive_mqtt_broker_from_ma_url,
            get_mqtt_addon_credentials,
        )

        creds = get_mqtt_addon_credentials()
    except Exception as exc:  # pragma: no cover
        logger.exception("MQTT probe failed")
        raise UseCaseError(500, "probe_failed", str(exc)) from exc

    if creds is not None:
        # Supervisor path: full credentials including password.
        return {
            "found": True,
            "source": "supervisor",
            "host": creds.get("host"),
            "port": creds.get("port"),
            "username": creds.get("username"),
            "password_present": bool(creds.get("password")),
            "ssl": creds.get("ssl"),
        }

    # Fallback: derive from MA URL on standalone deployments.
    try:
        from sendspin_bridge.config import load_config

        ma_api_url = str(load_config().get("MA_API_URL", "")).strip()
    except Exception:  # pragma: no cover
        ma_api_url = ""

    suggested = derive_mqtt_broker_from_ma_url(ma_api_url) if ma_api_url else None
    if suggested is not None:
        return {
            "found": True,
            "source": "ma_url",
            "host": suggested["host"],
            "port": suggested["port"],
            "username": "",
            "password_present": False,
            "ssl": False,
            "hint": (
                f"Suggested host {suggested['host']!r} taken from your Music Assistant URL. "
                "Enter Mosquitto credentials below if your broker requires authentication "
                "(anonymous-access brokers can be left blank)."
            ),
        }

    # Nothing to suggest — neither Supervisor nor a configured MA URL.
    return {
        "found": False,
        "source": None,
        "hint": (
            "Auto-detect needs either HA add-on mode (Supervisor) or a "
            "configured Music Assistant URL.  Enter the broker host and "
            "Mosquitto credentials manually."
        ),
    }


# ---------------------------------------------------------------------------
# /api/ha/mosquitto/status — Mosquitto add-on install state
# ---------------------------------------------------------------------------


def mosquitto_status() -> dict[str, Any]:
    """Read-only snapshot of the Mosquitto add-on state on HAOS.

    Used by the web UI to decide whether to show the "Install Mosquitto"
    install banner, the "Start Mosquitto" hint, or the auto-configure
    CTA.  Outside HA addon mode the response has ``available=false`` so
    the UI hides the banner.
    """
    try:
        from sendspin_bridge.services.ha.ha_addon import get_mosquitto_addon_state

        return get_mosquitto_addon_state()
    except Exception as exc:  # pragma: no cover
        logger.exception("Mosquitto status query failed")
        # Reuse the canonical constants instead of duplicating them.  Set
        # ``available`` from SUPERVISOR_TOKEN even on the error path so
        # the UI keeps showing the banner (with the error context) when
        # we're inside HA addon mode — otherwise the operator loses the
        # actionable hint and just sees a silent missing banner.
        import os as _os

        from sendspin_bridge.services.ha.ha_addon import MOSQUITTO_ADDON_DEEP_LINK, MOSQUITTO_ADDON_SLUG

        return {
            "available": bool(_os.environ.get("SUPERVISOR_TOKEN", "").strip()),
            "installed": False,
            "started": False,
            "slug": MOSQUITTO_ADDON_SLUG,
            "install_url": MOSQUITTO_ADDON_DEEP_LINK,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# /api/ha/mqtt/status — publisher diagnostics
# ---------------------------------------------------------------------------


def custom_component_status() -> dict[str, Any]:
    """Heuristic install/active state for the HACS custom_component.

    Mirrors ``/api/ha/mosquitto/status`` so the UI can render the two
    transports' install indicators with the same code path.  See
    ``services.ha.ha_addon.get_custom_component_state`` for the
    detection logic (token-presence + last_used recency).
    """
    try:
        from sendspin_bridge.services.ha.ha_addon import get_custom_component_state

        return get_custom_component_state()
    except Exception as exc:
        logger.exception("custom_component status query failed")
        return {
            "available": True,
            "installed": False,
            "started": False,
            "last_seen": None,
            "install_url": "https://my.home-assistant.io/redirect/hacs_repository/?owner=trudenboy&repository=sendspin-bt-bridge&category=integration",
            "error": str(exc),
        }


def mqtt_status() -> dict[str, Any]:
    """Read-only snapshot of the MQTT publisher state for the UI."""
    try:
        from sendspin_bridge.services.ha.ha_integration_lifecycle import get_default_lifecycle
        from sendspin_bridge.services.ha.ha_mqtt_publisher import publisher_status

        lifecycle = get_default_lifecycle()
        publisher = lifecycle.publisher if lifecycle is not None else None
        return publisher_status(publisher)
    except Exception:
        logger.exception("MQTT status query failed")
        return {
            "running": False,
            "state": "error",
            "broker": None,
            "discovery_payload_count": 0,
            "published_messages": 0,
            "last_error": "status query failed",
            "last_event_at": None,
        }


# ---------------------------------------------------------------------------
# /api/ha/mqtt/test — pre-save broker reachability + auth check
# ---------------------------------------------------------------------------


_MQTT_TEST_TCP_TIMEOUT_S = 5.0
_MQTT_TEST_CONNACK_TIMEOUT_S = 10.0
# Wire-protocol marker for "password unchanged" — see ``routes/api_config.py``
# for the matching round-trip logic on save.
_REDACTED_PASSWORD_MARKER = "***REDACTED***"


async def _probe_mqtt_broker(
    host: str,
    port: int,
    username: str,
    password: str,
    tls: bool,
) -> dict[str, Any]:
    """Run a one-shot reachability + auth probe against an MQTT broker.

    Mirrors the pre-flight + bounded-CONNACK pattern in
    ``ha_mqtt_publisher._serve``: TCP open via ``asyncio.open_connection``
    (asyncio-native, cancellable on timeout), then a full ``aiomqtt`` CONNACK
    round-trip inside ``asyncio.timeout``.  No retained state, no
    publishes — purely "do these credentials reach this broker".
    """
    import asyncio

    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_MQTT_TEST_TCP_TIMEOUT_S,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        del reader  # only needed to satisfy the open_connection return shape
    except (TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "error_class": type(exc).__name__,
            "error": f"broker {host}:{port} unreachable: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        import aiomqtt
    except ImportError as exc:
        return {
            "ok": False,
            "error_class": "ImportError",
            "error": f"aiomqtt not installed: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    # Use a unique-per-call identifier so two simultaneous tests (rapid
    # double-click, two operator browsers) can't collide on the broker
    # side and trigger spurious disconnects.  ``secrets.token_hex`` gives
    # us 64 random bits — far below the MQTT v3.1.1 23-byte client_id
    # limit, plenty for collision-free fanout.
    client = aiomqtt.Client(
        hostname=host,
        port=port,
        username=username or None,
        password=password or None,
        identifier=f"sendspin-test-{secrets.token_hex(8)}",
        tls_params=aiomqtt.TLSParameters() if tls else None,
        timeout=10,
    )
    try:
        async with asyncio.timeout(_MQTT_TEST_CONNACK_TIMEOUT_S):
            await client.__aenter__()
        try:
            return {"ok": True, "elapsed_ms": int((time.monotonic() - started) * 1000)}
        finally:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
    except Exception as exc:
        return {
            "ok": False,
            "error_class": type(exc).__name__,
            "error": f"connect failed: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


def _saved_mqtt_block() -> dict[str, Any]:
    """Return the saved ``HA_INTEGRATION.mqtt`` block (or an empty dict)."""
    try:
        from sendspin_bridge.config import load_config

        cfg = load_config()
        block = cfg.get("HA_INTEGRATION") or {}
        return dict(block.get("mqtt") or {})
    except Exception:
        logger.debug("Could not load saved MQTT config", exc_info=True)
        return {}


def _split_broker_host_port(broker: str, default_port: int) -> tuple[str, int]:
    """Normalise a saved ``broker`` value to ``(host_lower, port)``.

    ``broker`` may be a bare host, ``host:port`` (IPv4 / hostname), or a
    bracketed IPv6 literal with an optional port.  A port embedded in the
    string wins over ``default_port``.
    """
    broker = (broker or "").strip()
    if not broker:
        return "", default_port
    if broker.startswith("[") and "]" in broker:  # [ipv6] or [ipv6]:port
        host, _, rest = broker.partition("]")
        host = host.lstrip("[").strip().lower()
        if rest.startswith(":"):
            try:
                return host, int(rest[1:])
            except ValueError:
                return host, default_port
        return host, default_port
    if ":" in broker:  # host:port — IPv6 handled above, so a lone colon is a port
        host, _, port_s = broker.rpartition(":")
        try:
            return host.strip().lower(), int(port_s)
        except ValueError:
            return broker.lower(), default_port
    return broker.lower(), default_port


def _resolve_redacted_password(submitted: str, host: str, port: int) -> str:
    """Resolve the ``***REDACTED***`` marker — but only for the saved broker.

    The marker lets an operator re-test after editing unrelated fields
    (username, TLS) without retyping the password.  It must never hand the
    saved broker credential to a *different* target: an authed request with
    ``host=attacker.tld, password=***REDACTED***`` would otherwise
    exfiltrate the real broker password.  On any host/port mismatch we
    return an empty password so the operator must supply one explicitly for
    the new target.
    """
    if submitted != _REDACTED_PASSWORD_MARKER:
        return submitted
    mqtt = _saved_mqtt_block()
    saved_password = str(mqtt.get("password") or "")
    if not saved_password:
        return ""
    saved_host, saved_port = _split_broker_host_port(str(mqtt.get("broker") or ""), int(mqtt.get("port") or 1883))
    if host.strip().lower() == saved_host and int(port) == saved_port:
        return saved_password
    logger.info(
        "MQTT test: redacted-password marker submitted for a broker that does "
        "not match the saved one; requiring an explicit password rather than "
        "sending the saved credential to a different host.",
    )
    return ""


async def mqtt_test(
    *, host: str, port: int = 1883, username: str = "", password: str = "", tls: bool = False
) -> dict[str, Any]:
    """Probe a broker with the form's values. Probe failures are a result, not an error."""
    host = (host or "").strip()
    if not host:
        raise UseCaseError(400, "host_required", "host required")
    if not 1 <= int(port) <= 65535:
        raise UseCaseError(400, "port_out_of_range", "port out of range")
    password = _resolve_redacted_password(password or "", host, int(port))
    try:
        return await _probe_mqtt_broker(host, int(port), (username or "").strip(), password, bool(tls))
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("MQTT test probe crashed unexpectedly")
        return {"ok": False, "error_class": type(exc).__name__, "error": f"probe crashed: {exc}"}


# ---------------------------------------------------------------------------
# /api/ha/rest/probe — auto-detected advertise host/port for the form
# ---------------------------------------------------------------------------


def rest_probe() -> dict[str, Any]:
    """Return the host + port the bridge would advertise via mDNS by default.

    Mirrors ``/api/ha/mqtt/probe`` so the REST card can fill its
    "Bridge host" / "Bridge port" override fields with sensible
    initial values.  Auto-detection mirrors the mDNS advertiser:
    ``socket.gethostbyname(socket.gethostname())`` for the host and
    ``resolve_web_port()`` for the port.

    Always returns 200 with ``{"ok": bool, "host": str, "port": int,
    "source": str, "hint": str}``.  ``source`` is ``"hostname"`` for
    a successful gethostbyname lookup or ``"fallback"`` when only a
    bind-any (``0.0.0.0``) address is available.
    """
    try:
        from sendspin_bridge.config import resolve_web_port
        from sendspin_bridge.services.ipc.bridge_mdns import _resolve_host_address

        host = _resolve_host_address()
        port = int(resolve_web_port() or 8080)
        source = "hostname" if host and host != "0.0.0.0" else "fallback"
        hint = (
            f"Auto-detected from this host's hostname ({host}:{port})."
            if source == "hostname"
            else (
                f"Could not resolve a specific LAN address — using bind-any ({host}:{port}).  "
                "Set Bridge host explicitly if Home Assistant cannot reach this bridge "
                "on its hostname."
            )
        )
        return {"ok": True, "host": host, "port": port, "source": source, "hint": hint}
    except Exception as exc:
        logger.exception("REST advertise probe failed")
        return {
            "ok": False,
            "host": "",
            "port": 0,
            "source": "error",
            "hint": f"Probe failed: {exc}",
        }


# ---------------------------------------------------------------------------
# /api/ha/mdns/status — mDNS advertiser diagnostics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/ha/command — unified command dispatch for the custom_component
# ---------------------------------------------------------------------------


def dispatch_device_command(player_id: str, command: str, value: Any = None):
    """A device-scoped command from the custom component; returns the dispatcher result."""
    from sendspin_bridge.services.ha.ha_command_dispatcher import get_default_dispatcher

    return get_default_dispatcher().dispatch_device(player_id, command, value)


def dispatch_bridge_command(command: str, value: Any = None):
    """A bridge-scoped command from the custom component."""
    from sendspin_bridge.services.ha.ha_command_dispatcher import get_default_dispatcher

    return get_default_dispatcher().dispatch_bridge(command, value)


def mdns_status() -> dict[str, Any]:
    try:
        from sendspin_bridge.services.ipc.bridge_mdns import get_default_advertiser

        adv = get_default_advertiser()
        if adv is None or adv.advertisement is None:
            return {"advertised": False}
        return {
            "advertised": True,
            "service_name": adv.advertisement.service_name,
            "host_id": adv.advertisement.host_id,
            "port": adv.advertisement.port,
            "txt_records": dict(adv.advertisement.txt_records),
        }
    except Exception:
        logger.exception("mDNS status query failed")
        return {"advertised": False, "error": "status query failed"}
