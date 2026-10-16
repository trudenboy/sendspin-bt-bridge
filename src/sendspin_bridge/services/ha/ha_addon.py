"""Helpers for Home Assistant add-on Supervisor integration."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import urllib.request as _ur
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

HA_ADDON_BASE_SLUG = "sendspin_bt_bridge"

KNOWN_MA_ADDON_SLUGS = (
    "d5369777_music_assistant",
    "d5369777_music_assistant_beta",
    "d5369777_music_assistant_dev",
)

# Official Mosquitto broker add-on slug.  Stable since the add-on was
# introduced — used to query install/start state and to build the
# my.home-assistant.io deep-link the UI surfaces when the broker is
# missing.
MOSQUITTO_ADDON_SLUG = "core_mosquitto"
MOSQUITTO_ADDON_DEEP_LINK = "https://my.home-assistant.io/redirect/supervisor_addon/?addon=core_mosquitto"

# HACS deep-link to add the bridge's custom-component repository.  Works
# only when HACS itself is already installed on the operator's HA Core
# instance — but that's also the only setup where the link makes sense.
CUSTOM_COMPONENT_HACS_DEEP_LINK = (
    "https://my.home-assistant.io/redirect/hacs_repository/"
    "?owner=trudenboy&repository=sendspin-bt-bridge&category=integration"
)
# How recent does a token's ``last_used`` have to be for us to call the
# custom_component "currently connected"?  6 hours is generous — covers
# overnight inactivity on a household setup but flags genuinely stale
# pair records.
_CUSTOM_COMPONENT_ACTIVE_WINDOW_S = 6 * 3600
# How recently must a token have been *issued* (``created``) for us to
# treat it as "just paired, HA may still be initialising the integration
# entry".  Kept short so a stale token doesn't masquerade as a fresh
# pair forever.
_CUSTOM_COMPONENT_RECENT_PAIR_S = 15 * 60


def _get_supervisor_payload(path: str, timeout: float = 5.0) -> dict[str, Any] | None:
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        return None

    try:
        req = _ur.Request(
            f"http://supervisor{path}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with _ur.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except (OSError, ValueError) as exc:
        logger.debug("Supervisor request failed for %s: %s", path, exc)
        return None

    return payload if isinstance(payload, dict) else None


def get_supervisor_addon_info(slug: str, timeout: float = 5.0) -> dict[str, Any] | None:
    payload = _get_supervisor_payload(f"/addons/{slug}/info", timeout=timeout)
    if not payload:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def get_self_addon_info(timeout: float = 5.0) -> dict[str, Any] | None:
    payload = _get_supervisor_payload("/addons/self/info", timeout=timeout)
    if not payload:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def get_mqtt_addon_credentials(timeout: float = 5.0) -> dict[str, Any] | None:
    """Auto-detect a Mosquitto add-on broker on HAOS via Supervisor API.

    Supervisor exposes installed services at ``/services/<service_type>``;
    when the user has the official Mosquitto add-on installed and started,
    ``GET /services/mqtt`` returns broker credentials in ``data.<…>``.

    Returns a dict ``{"host", "port", "username", "password", "ssl"}`` on
    success, ``None`` when the service is unavailable (no Supervisor token,
    no MQTT add-on installed, or running outside HAOS).  Callers — the
    publisher and the web UI auto-fill button — must surface a
    user-actionable hint ("install Mosquitto add-on") when this returns
    ``None`` and ``HA_INTEGRATION.mqtt.broker == "auto"``.
    """
    payload = _get_supervisor_payload("/services/mqtt", timeout=timeout)
    if not payload:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    # Normalise the keys we care about; Supervisor sometimes wraps service
    # data under nested objects depending on which integration registered
    # the service.  Be lenient and look for both shapes.
    inner = data.get("services", {})
    if isinstance(inner, dict) and "mqtt" in inner and isinstance(inner["mqtt"], dict):
        data = inner["mqtt"]
    host = data.get("host") or data.get("addon_address")
    port = data.get("port") or 1883
    username = data.get("username") or ""
    password = data.get("password") or ""
    ssl = bool(data.get("ssl", False))
    if not host:
        return None
    return {
        "host": str(host),
        "port": int(port),
        "username": str(username),
        "password": str(password),
        "ssl": ssl,
    }


_TLS_SCHEMES = frozenset({"mqtts", "ssl", "tls", "https", "wss"})
_PLAIN_SCHEMES = frozenset({"mqtt", "tcp", "http", "ws"})


def normalise_broker_host(raw: str) -> dict[str, Any]:
    """Strip URL noise from a manually-entered broker host string.

    Operators often paste ``mqtt://192.168.10.10:1883`` or
    ``mqtts://broker.example.com`` from environment variables, MA config,
    or docs.  Rather than rejecting those, we accept liberally and store
    the bare host — Postel's law.

    Returns a dict with these keys (always present):

    - ``host`` (str): bare hostname / IP without scheme, port, or path.
    - ``port`` (int | None): port extracted from the input, or ``None``
      when the input had no port.  Callers can treat ``None`` as "keep
      whatever was in the dedicated port field."
    - ``tls`` (bool | None): ``True`` when the scheme indicated TLS
      (``mqtts``/``ssl``/``tls``/``https``/``wss``); ``False`` for an
      explicitly plain scheme; ``None`` when no scheme was given (so
      callers don't override the operator's TLS toggle).
    - ``stripped`` (bool): ``True`` when a scheme or port was removed
      from the input — UI uses this to flash a "scheme stripped" hint.

    Always returns a dict — passes ``"auto"`` and bare hostnames through
    unchanged.  Empty input returns ``host=""``.
    """
    text = (raw or "").strip()
    if not text:
        return {"host": "", "port": None, "tls": None, "stripped": False}

    original = text
    tls: bool | None = None
    port: int | None = None
    scheme_consumed = False

    # Scheme prefix.  Match case-insensitively and only when followed by
    # ``://`` to avoid false positives on hostnames that happen to start
    # with ``http`` etc.
    scheme_idx = text.find("://")
    if scheme_idx > 0:
        scheme = text[:scheme_idx].lower()
        if scheme in _TLS_SCHEMES:
            tls = True
            text = text[scheme_idx + 3 :]
            scheme_consumed = True
        elif scheme in _PLAIN_SCHEMES:
            tls = False
            text = text[scheme_idx + 3 :]
            scheme_consumed = True
        # Unknown scheme → leave string alone, let the user see their typo.

    # Strip a path / query / fragment if present — broker URIs sometimes
    # carry one.  Only do this when we've consumed a recognised scheme;
    # otherwise a bare hostname with weird characters could lose part
    # of itself silently.
    if scheme_consumed:
        for sep in ("/", "?", "#"):
            cut = text.find(sep)
            if cut >= 0:
                text = text[:cut]

    # Extract port — only for IPv4 / hostname.  IPv6 needs brackets so
    # ``[::1]:1883`` is unambiguous; bare IPv6 like ``::1`` is left to
    # the dedicated port field.
    if text.startswith("["):
        # IPv6 in brackets, optional ``]:port``.
        close = text.find("]")
        if close > 0:
            host_part = text[1:close]
            tail = text[close + 1 :]
            if tail.startswith(":"):
                try:
                    port = int(tail[1:])
                except ValueError:
                    port = None
            text = host_part
    elif text.count(":") == 1:
        host_part, _, port_part = text.partition(":")
        try:
            port = int(port_part)
            text = host_part
        except ValueError:
            # Not a port — could be a hostname with a colon (rare).
            # Leave the string intact.
            pass

    return {
        "host": text,
        "port": port,
        "tls": tls,
        "stripped": text != original,
    }


def derive_mqtt_broker_from_ma_url(ma_api_url: str) -> dict[str, Any] | None:
    """Derive a *suggested* MQTT broker host from a configured MA URL.

    Standalone bridges can't query Supervisor for the Mosquitto add-on's
    broker info, but a user who already configured Music Assistant has
    its URL on file (e.g. ``http://192.168.10.10:8095``).  When MA runs
    as an HA add-on, the Mosquitto broker is on the same host — so the
    MA host is a strong heuristic for the broker host.

    Returns a dict containing the same broker connection fields as
    :func:`get_mqtt_addon_credentials` (``host``, ``port``, ``username``,
    ``password``, and ``ssl``), but with empty ``username`` /
    ``password`` because the operator must enter Mosquitto credentials
    manually (or leave them blank for an anonymous broker).  The
    returned dict also includes an extra ``source="ma_url"`` key so
    callers can flag the value as a suggestion rather than
    authoritative.

    Returns ``None`` when ``ma_api_url`` is empty / unparseable.
    """
    if not ma_api_url:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(ma_api_url.strip())
    except Exception:
        return None
    host = (parsed.hostname or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": 1883,
        "username": "",
        "password": "",
        "ssl": False,
        "source": "ma_url",
    }


def get_mosquitto_addon_state(timeout: float = 5.0) -> dict[str, Any]:
    """Report the install/start state of the official Mosquitto add-on.

    Used by the web UI's HA panel to decide whether to show the install
    banner (with a deep-link to the HA add-on store), the "start the
    add-on" hint, or the "auto-configure" CTA.

    Returns a dict with stable shape (always populated):

      ``available``: ``True`` when running in HA addon mode (Supervisor
        token present); ``False`` otherwise.  When ``False`` the other
        fields are best-effort defaults — callers should hide the banner.
      ``installed``: ``True`` when Supervisor reports the add-on as
        present (any state).
      ``started``: ``True`` when ``state == "started"``.
      ``slug``: the add-on slug (``core_mosquitto``).
      ``install_url``: my.home-assistant.io deep-link to the add-on page,
        suitable for an "Install" / "Open Mosquitto add-on" button.
      ``error``: optional string when the Supervisor query failed for a
        reason other than "add-on not installed" (e.g. permission denied).

    Outside HA addon mode this returns ``{"available": False, ...}``
    rather than ``None`` so the UI doesn't have to special-case the
    response shape.
    """
    base: dict[str, Any] = {
        "available": False,
        "installed": False,
        "started": False,
        "slug": MOSQUITTO_ADDON_SLUG,
        "install_url": MOSQUITTO_ADDON_DEEP_LINK,
        "error": None,
    }
    if not os.environ.get("SUPERVISOR_TOKEN", "").strip():
        return base
    base["available"] = True
    info = get_supervisor_addon_info(MOSQUITTO_ADDON_SLUG, timeout=timeout)
    if info is None:
        # Supervisor returns 400 for unknown / not-installed add-ons.  We
        # can't tell a permission-denied response from "not installed"
        # at this layer (``_get_supervisor_payload`` swallows both into
        # ``None``), but treating "no info" as "not installed" matches
        # what the operator would see in the HA add-on list and keeps
        # the install button useful in either case.
        return base
    base["installed"] = True
    base["started"] = str(info.get("state") or "").lower() == "started"
    return base


def get_custom_component_state() -> dict[str, Any]:
    """Heuristic detection of the HACS custom_component install/active state.

    The custom_component is a Python package installed via HACS into
    ``/config/custom_components/sendspin_bridge/`` on the operator's
    Home Assistant Core instance.  The bridge runs in its own
    container / process and can't see HA Core's filesystem, so direct
    "is the file there" detection isn't possible.

    What we *can* observe is the side effect of the integration being
    set up: it talks to the bridge over REST + bearer auth, and every
    issued bearer token has an ``id`` / ``label`` / ``last_used`` row
    in ``AUTH_TOKENS``.  So:

      ``installed`` = at least one bearer token has ever been issued
        (the integration successfully paired at least once).
      ``started`` = at least one bearer token has been used within the
        last few hours (the integration is currently talking to us).
      ``last_seen`` = the most recent ``last_used`` ISO timestamp across
        all tokens (or ``None`` when no token has been used yet).

    Returns a dict shaped like ``get_mosquitto_addon_state`` so the UI
    can render the two transports' install indicators with the same
    code path.

    Outside HA addon mode this still returns useful data — bearer
    tokens are a standalone concept too, so the heuristic works
    everywhere.
    """
    base: dict[str, Any] = {
        "available": True,
        "installed": False,
        "started": False,
        "recently_paired": False,
        "last_seen": None,
        "install_url": CUSTOM_COMPONENT_HACS_DEEP_LINK,
        "error": None,
    }
    try:
        from sendspin_bridge.config import load_config

        cfg = load_config()
    except Exception as exc:
        base["error"] = f"config unavailable: {exc}"
        return base
    tokens = cfg.get("AUTH_TOKENS") or []
    if not tokens:
        return base
    base["installed"] = True

    # ``recently_paired`` — token was *issued* in the last 15 min, even if
    # HA hasn't called the bridge yet.  Distinguishes "just paired,
    # waiting on HA to load the integration entry" from "paired weeks
    # ago and went dark".  Avoids "paired before but isn't currently
    # active" alarmism right after the operator clicks Generate.
    created_strs = [str(tok.get("created") or "") for tok in tokens if isinstance(tok, dict)]
    created_strs = [s for s in created_strs if s]
    if created_strs:
        try:
            most_recent_created = max(created_strs)
            as_utc = datetime.fromisoformat(most_recent_created.replace("Z", "+00:00"))
            if as_utc.tzinfo is None:
                as_utc = as_utc.replace(tzinfo=UTC)
            delta = (datetime.now(tz=UTC) - as_utc).total_seconds()
            if 0 <= delta < _CUSTOM_COMPONENT_RECENT_PAIR_S:
                base["recently_paired"] = True
        except (TypeError, ValueError):
            pass

    last_used_strs = [str(tok.get("last_used") or "") for tok in tokens if isinstance(tok, dict)]
    last_used_strs = [s for s in last_used_strs if s]
    if not last_used_strs:
        return base
    most_recent = max(last_used_strs)
    base["last_seen"] = most_recent
    try:
        # Tokens persist ``last_used`` as an ISO-8601 timestamp; tolerate
        # both ``Z`` suffix (UTC) and explicit offsets.
        as_utc = datetime.fromisoformat(most_recent.replace("Z", "+00:00"))
        if as_utc.tzinfo is None:
            as_utc = as_utc.replace(tzinfo=UTC)
        delta = (datetime.now(tz=UTC) - as_utc).total_seconds()
        if 0 <= delta < _CUSTOM_COMPONENT_ACTIVE_WINDOW_S:
            base["started"] = True
    except (TypeError, ValueError):
        # Malformed timestamp — leave ``started`` false; ``last_seen`` is
        # still surfaced so the operator can investigate manually.
        pass
    return base


def detect_delivery_channel_from_slug(slug: str) -> str | None:
    normalized = str(slug or "").strip().lower()
    if not normalized:
        return None
    if normalized.endswith(f"{HA_ADDON_BASE_SLUG}_beta"):
        return "beta"
    if normalized.endswith(f"{HA_ADDON_BASE_SLUG}_rc"):
        return "rc"
    if normalized.endswith(HA_ADDON_BASE_SLUG):
        return "stable"
    return None


def get_self_delivery_channel() -> str:
    data = get_self_addon_info()
    if data:
        detected = detect_delivery_channel_from_slug(str(data.get("slug") or ""))
        if detected:
            return detected

    hostname = str(os.environ.get("HOSTNAME") or "").strip().lower()
    if hostname.endswith("-beta"):
        return "beta"
    if hostname.endswith("-rc"):
        return "rc"
    return "stable"


# Music Assistant's own web server port (``ma_url`` normally points here).
MA_DEFAULT_PORT = 8095
# MA's internal ingress site port.
MA_INGRESS_PORT = 8094
# How long "no usable Music Assistant add-on" is remembered, so retries from
# the web UI don't re-list the add-ons and repeat the warning every time.
_NOT_FOUND_TTL_S = 60.0

# Slug of the Music Assistant add-on verified for an MA URL ("" = no URL).
_ma_addon_slug_cache: dict[str, str] = {}
# MA URL -> (monotonic time, reason) of the last failed lookup.
_ma_addon_not_found: dict[str, tuple[float, str]] = {}


def reset_ma_addon_cache() -> None:
    """Forget the remembered Music Assistant add-on lookups."""
    _ma_addon_slug_cache.clear()
    _ma_addon_not_found.clear()


def is_ma_addon(entry: dict[str, Any]) -> bool:
    """Whether an add-on entry (list row or info) *looks like* Music Assistant.

    The slug is ``<repository>_<add-on slug>``: the repository part is a hash for
    a custom repository and ``local`` for one on the host, and the add-on slug
    itself varies between the official, beta, dev and self-built variants.  So
    any slug containing ``music_assistant`` counts, and so does any add-on whose
    name says Music Assistant.  Both are chosen by the add-on's author, so this
    only makes a *candidate*: see :func:`resolve_ma_addon` for how one is trusted.
    """
    slug = str(entry.get("slug") or "").lower()
    name = " ".join(str(entry.get("name") or "").lower().replace("_", " ").replace("-", " ").split())
    return "music_assistant" in slug or "music assistant" in name


def _is_started(info: dict[str, Any] | None) -> bool:
    return bool(info) and str((info or {}).get("state") or "").lower() == "started"


def _url_key(ma_url: str) -> str:
    return str(ma_url or "").strip().rstrip("/").lower()


def list_supervisor_addons(timeout: float = 5.0) -> list[dict[str, Any]]:
    """The installed add-ons (slug, name, state) as the bridge's Supervisor role sees them.

    ``hassio_role: default`` may read ``/supervisor/info`` (whose ``addons`` lists
    every installed add-on) but not ``GET /addons``; the latter is tried too for
    a bridge running with a higher role.
    """
    for path in ("/supervisor/info", "/addons"):
        payload = _get_supervisor_payload(path, timeout=timeout)
        data = (payload or {}).get("data")
        rows = data.get("addons") if isinstance(data, dict) else None
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict) and row.get("slug")]
    return []


def describe_addons(rows: list[dict[str, Any]], *, with_state: bool = True) -> str:
    """``slug (name[, state])`` for each row, for logs and error messages."""
    if not rows:
        return "none"
    if with_state:
        return ", ".join(f"{row.get('slug')} ({row.get('name') or '?'}, {row.get('state') or '?'})" for row in rows)
    return ", ".join(f"{row.get('slug')} ({row.get('name') or '?'})" for row in rows)


def ma_server_id(base_url: str, timeout: float = 5.0) -> str | None:
    """``server_id`` from Music Assistant's unauthenticated ``/info``, or None."""
    from sendspin_bridge.services.infrastructure.url_safety import safe_urlopen

    try:
        with safe_urlopen(f"{base_url.rstrip('/')}/info", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except Exception as exc:
        logger.debug("MA /info failed for %s: %s", base_url, exc)
        return None
    server_id = payload.get("server_id") if isinstance(payload, dict) else None
    return str(server_id) if server_id else None


def addon_internal_urls(info: dict[str, Any]) -> list[str]:
    """Where the add-on answers on the Supervisor network: MA's own port, then its ingress port."""
    hostname = str(info.get("hostname") or "").strip()
    if not hostname:
        return []
    ports = [MA_DEFAULT_PORT]
    with contextlib.suppress(TypeError, ValueError):
        ports.append(int(info.get("ingress_port") or MA_INGRESS_PORT))
    return [f"http://{hostname}:{port}" for port in dict.fromkeys(ports)]


def _addon_server_id(info: dict[str, Any]) -> str | None:
    for url in addon_internal_urls(info):
        if server_id := ma_server_id(url):
            return server_id
    return None


class MaAddonLookup(NamedTuple):
    """The trusted Music Assistant add-on's Supervisor info, or why there is none."""

    info: dict[str, Any] | None
    reason: str = ""
    #: Which rule accepted the add-on, for the log.
    rule: str = ""


def _started_ma_candidates() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(official started infos, other started MA-like infos, all listed rows)."""
    official: list[dict[str, Any]] = []
    for slug in KNOWN_MA_ADDON_SLUGS:
        info = get_supervisor_addon_info(slug)
        if info is not None and _is_started(info):
            official.append({**info, "slug": slug})
    rows = list_supervisor_addons()
    others: list[dict[str, Any]] = []
    for row in rows:
        slug = str(row["slug"])
        if slug in KNOWN_MA_ADDON_SLUGS or not is_ma_addon(row) or str(row.get("state") or "").lower() != "started":
            continue
        info = get_supervisor_addon_info(slug)
        if info is not None and _is_started(info):
            others.append({**info, "slug": slug})
    return official, others, rows


def _opted_in_slug() -> str:
    """``MA_ADDON_SLUG``: the admin's explicit choice of a Music Assistant add-on."""
    try:
        from sendspin_bridge.config import load_config

        return str(load_config().get("MA_ADDON_SLUG") or "").strip()
    except Exception as exc:
        logger.debug("MA_ADDON_SLUG unavailable: %s", exc)
        return ""


def _trust_rule(info: dict[str, Any], opted_in: str) -> str:
    """Why a non-official add-on may be trusted, from facts the Supervisor sets ("" = none).

    Slug and name text are the author's choice and prove nothing.  The
    Supervisor's ``repository`` is ``local`` only for an add-on an admin placed
    in the host's ``/addons`` folder; any other repository needs the admin to
    name the slug in ``MA_ADDON_SLUG``.
    """
    if str(info.get("repository") or "").lower() == "local":
        return "local repository"
    if opted_in and str(info.get("slug") or "") == opted_in:
        return "MA_ADDON_SLUG"
    return ""


def _resolve_host(host: str) -> set[str]:
    """The IP addresses *host* resolves to (itself when it is one)."""
    import ipaddress
    import socket

    try:
        return {str(ipaddress.ip_address(host))}
    except ValueError:
        pass
    try:
        return {str(row[4][0]) for row in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    except OSError as exc:
        logger.debug("Could not resolve %s: %s", host, exc)
        return set()


def host_addresses() -> set[str]:
    """The HA host's own addresses: the Supervisor network gateway and every interface address."""
    payload = _get_supervisor_payload("/network/info")
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return set()
    found: set[str] = set()
    docker = data.get("docker")
    if isinstance(docker, dict) and docker.get("gateway"):
        found.add(str(docker["gateway"]))
    for interface in data.get("interfaces") or []:
        if not isinstance(interface, dict):
            continue
        for family in ("ipv4", "ipv6"):
            config = interface.get(family)
            if not isinstance(config, dict):
                continue
            for key in ("address", "addresses"):
                for address in config.get(key) or []:
                    found.add(str(address).split("/", 1)[0])
    return found


def _exposed_ports(info: dict[str, Any]) -> tuple[set[int], set[int]]:
    """(container ports, host-published ports) from the add-on's ``network`` map."""
    container: set[int] = set()
    published: set[int] = set()
    network = info.get("network")
    if isinstance(network, dict):
        for key, value in network.items():
            with contextlib.suppress(TypeError, ValueError):
                container.add(int(str(key).split("/", 1)[0]))
            with contextlib.suppress(TypeError, ValueError):
                if value is not None:
                    published.add(int(value))
    return container, published


def _address_matches(info: dict[str, Any], ma_url: str, host_ips: Callable[[], set[str]]) -> bool:
    """Whether *ma_url* is an address of this add-on, judged by Supervisor facts only.

    A host-network add-on: the URL's host is the HA host (Supervisor gateway or
    a host interface address) and its port is Music Assistant's.  A bridged
    add-on: the URL's host is the add-on's hostname or IP with a port it
    exposes, or the HA host with a port it publishes there.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(ma_url.strip())
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    if not host:
        return False
    resolved = _resolve_host(host)
    if info.get("host_network"):
        return port == MA_DEFAULT_PORT and bool(resolved & host_ips())
    container, published = _exposed_ports(info)
    own = {str(info.get("hostname") or "").lower(), str(info.get("ip_address") or "")} - {""}
    if (host in own or resolved & own) and port in container:
        return True
    return port in published and bool(resolved & host_ips())


def _choose(official: list[dict], others: list[dict], ma_url: str) -> MaAddonLookup:
    """Pick the add-on that is the MA behind *ma_url*, or refuse.

    The official repository's add-ons are trusted outright.  Any other add-on
    is used only when all of this holds, so it can't be handed a user's
    ingress session or identity by naming itself "Music Assistant" or by
    echoing the real server's public ``/info``:

    - a Supervisor fact vouches for it (``repository: local``, or the admin's
      ``MA_ADDON_SLUG``);
    - *ma_url* is one of its addresses (``_address_matches``);
    - it is the only such add-on;
    - its ``server_id`` does not contradict *ma_url*'s (checked, never sufficient).
    """
    target = ma_server_id(ma_url) if ma_url else None

    for candidate in official:
        if not target or _addon_server_id(candidate) in (None, target):
            return MaAddonLookup(candidate, rule="official repository")

    if not others:
        return MaAddonLookup(
            None,
            f"The Music Assistant add-on is not the server at {ma_url} "
            f"({describe_addons(official, with_state=False)}); set the Music Assistant URL to the add-on you use",
        )
    if not ma_url:
        return MaAddonLookup(
            None,
            "Can't confirm which add-on is Music Assistant without its address: "
            f"{describe_addons(others, with_state=False)}; set the Music Assistant URL",
        )

    opted_in = _opted_in_slug()
    trusted = [(c, rule) for c in others if (rule := _trust_rule(c, opted_in))]
    untrusted = [c for c in others if not _trust_rule(c, opted_in)]
    cached_ips: list[set[str]] = []

    def host_ips() -> set[str]:
        if not cached_ips:
            cached_ips.append(host_addresses())
        return cached_ips[0]

    matching = [(c, rule) for c, rule in trusted if _address_matches(c, ma_url, host_ips)]
    if target:
        matching = [(c, rule) for c, rule in matching if _addon_server_id(c) in (None, target)]
    if len(matching) == 1:
        candidate, rule = matching[0]
        return MaAddonLookup(candidate, rule=f"{rule}, at {ma_url}")
    if len(matching) > 1:
        return MaAddonLookup(
            None,
            f"Several Music Assistant add-ons are at {ma_url}: "
            f"{describe_addons([c for c, _r in matching], with_state=False)}; refusing to pick one",
        )
    parts = []
    if trusted:
        parts.append(f"not at {ma_url}: {describe_addons([c for c, _r in trusted], with_state=False)}")
    if untrusted:
        parts.append(
            "not from a local repository and not named in MA_ADDON_SLUG: "
            f"{describe_addons(untrusted, with_state=False)}"
        )
    return MaAddonLookup(None, "No Music Assistant add-on can be trusted (" + "; ".join(parts) + ")")


def resolve_ma_addon(ma_url: str = "") -> MaAddonLookup:
    """The running Music Assistant add-on the bridge may trust for *ma_url*.

    The result is remembered per MA URL (re-checked to still be running); a
    failure is remembered for a minute, logging its warning once.
    """
    if not os.environ.get("SUPERVISOR_TOKEN", "").strip():
        return MaAddonLookup(None, "The bridge is not running as a Home Assistant add-on")
    key = _url_key(ma_url)

    if slug := _ma_addon_slug_cache.get(key):
        cached = get_supervisor_addon_info(slug)
        if cached is not None and _is_started(cached):
            return MaAddonLookup({**cached, "slug": slug})
        _ma_addon_slug_cache.pop(key, None)

    failed = _ma_addon_not_found.get(key)
    if failed and time.monotonic() - failed[0] < _NOT_FOUND_TTL_S:
        return MaAddonLookup(None, failed[1])

    official, others, rows = _started_ma_candidates()
    if not official and not others:
        ma_rows = [row for row in rows if is_ma_addon(row)]
        reason = (
            f"No running Music Assistant add-on found (Music Assistant add-ons: {describe_addons(ma_rows)}; "
            f"{len(rows)} add-ons installed)"
        )
        result = MaAddonLookup(None, reason)
    else:
        result = _choose(official, others, ma_url)

    if result.info is None:
        logger.warning("%s", result.reason)
        _ma_addon_not_found[key] = (time.monotonic(), result.reason)
        return result
    slug = str(result.info["slug"])
    logger.info(
        "Using Music Assistant add-on '%s' (%s): %s", slug, result.info.get("name") or "?", result.rule or "remembered"
    )
    _ma_addon_slug_cache[key] = slug
    _ma_addon_not_found.pop(key, None)
    return result


def find_ma_addon_info(ma_url: str = "") -> dict[str, Any] | None:
    """The trusted running Music Assistant add-on's Supervisor info for *ma_url*, or None."""
    return resolve_ma_addon(ma_url).info


def find_started_ma_addon_info(ma_url: str = "") -> dict[str, Any] | None:
    return find_ma_addon_info(ma_url)


def get_ma_addon_ui_url(ma_url: str = "") -> str:
    data = find_ma_addon_info(ma_url)
    if not data or not data.get("ingress"):
        return ""
    return str(data.get("ingress_url") or data.get("ingress_entry") or "").rstrip("/")


def get_ma_addon_internal_ingress_url(ma_url: str = "") -> str:
    """The trusted MA add-on's internal ingress site, or "" when there is none."""
    data = find_ma_addon_info(ma_url)
    if not data:
        return ""
    hostname = str(data.get("hostname") or "").strip()
    if not hostname:
        return ""
    try:
        port = int(data.get("ingress_port") or MA_INGRESS_PORT)
    except (ValueError, TypeError):
        port = MA_INGRESS_PORT
    return f"http://{hostname}:{port}"


def get_ma_addon_discovery_candidates() -> list[dict[str, str]]:
    """Return prioritized MA API discovery candidates for HA-aware runtimes.

    Only suggests addresses (nothing is sent to them but ``/info``).  A
    non-official add-on is suggested only when it is the only Music Assistant
    candidate, so a look-alike can't become the configured server by sorting
    first.
    """
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def _append(url: str, source: str, summary: str) -> None:
        normalized = str(url or "").strip().rstrip("/")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append({"url": normalized, "source": source, "summary": summary})

    if os.environ.get("SUPERVISOR_TOKEN", "").strip():
        official, others, _rows = _started_ma_candidates()
        opted_in = _opted_in_slug()
        addons = official or (others if len(others) == 1 and _trust_rule(others[0], opted_in) else [])
        for addon in addons:
            hostname = str(addon.get("hostname") or "").strip()
            if hostname:
                _append(
                    f"http://{hostname}:{MA_DEFAULT_PORT}",
                    "ha_addon_hostname",
                    "Home Assistant Supervisor reported a running Music Assistant add-on.",
                )

    _append(
        "http://homeassistant.local:8095",
        "ha_supervisor_dns",
        "Home Assistant local hostname is the next MA discovery fallback.",
    )
    _append(
        "http://localhost:8095",
        "local_fallback",
        "Localhost is the final HA add-on Music Assistant fallback.",
    )
    return candidates
