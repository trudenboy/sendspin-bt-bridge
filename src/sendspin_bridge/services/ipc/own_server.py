"""Which Music Assistant a player may belong to.

Two Music Assistant servers on one network both discover the bridge's
``_sendspin._tcp`` adverts, and the first one to dial a player keeps it.  The
bridge then shows the player as connected while the Music Assistant the
operator uses never sees it.  This module holds the decision, free of any
transport, so the daemon and the parent share one definition of "ours".
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_AUTO_HOSTS = frozenset({"", "auto", "discover"})
#: A refusal for the same server is logged again only after this long.
REFUSAL_LOG_PERIOD_S = 600.0
_MAX_RECENT_REFUSALS = 5


def normalize_host(host: str | None) -> str:
    """Lower-case a host and unwrap an IPv4-mapped IPv6 address."""
    value = str(host or "").strip().strip("[]").lower()
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return value
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(addr)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class OwnServer:
    """What the bridge knows about its own Music Assistant."""

    hosts: frozenset[str] = frozenset()
    server_ids: frozenset[str] = frozenset()
    name: str | None = None

    @property
    def known(self) -> bool:
        return bool(self.hosts or self.server_ids)

    def to_params(self) -> dict[str, Any]:
        return {"hosts": sorted(self.hosts), "server_ids": sorted(self.server_ids), "name": self.name}

    @classmethod
    def from_params(cls, params: Any) -> OwnServer | None:
        if not isinstance(params, dict):
            return None
        own = cls(
            hosts=frozenset(normalize_host(h) for h in params.get("hosts") or () if str(h).strip()),
            server_ids=frozenset(str(i) for i in params.get("server_ids") or () if str(i).strip()),
            name=str(params["name"]) if params.get("name") else None,
        )
        return own if own.known else None


@dataclass(frozen=True)
class Verdict:
    accept: bool
    #: ``True``/``False`` when the bridge knows its server, ``None`` when it does not.
    own: bool | None
    reason: str


def decide(
    own: OwnServer | None,
    *,
    server_id: str | None,
    peer_host: str | None,
    trusted_ids: Iterable[str] = (),
    enforce: bool = True,
) -> Verdict:
    """Whether a server that dialled in may take the player."""
    if not enforce or own is None or not own.known:
        return Verdict(True, None, "own server unknown" if enforce else "check disabled")
    if server_id and (server_id in own.server_ids or server_id in set(trusted_ids)):
        return Verdict(True, True, "server id matches")
    if normalize_host(peer_host) in own.hosts:
        return Verdict(True, True, "host matches")
    return Verdict(False, False, "not the bridge's Music Assistant")


Resolver = Callable[[str], Awaitable[list[str]]]


async def _system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout=3.0)
    except (OSError, TimeoutError):
        return []
    return sorted({str(info[4][0]) for info in infos})


async def resolve_own_server(
    *,
    sendspin_server: str | None,
    ma_api_urls: Iterable[str | None],
    resolver: Resolver | None = None,
) -> OwnServer | None:
    """Work out the bridge's Music Assistant from its configuration.

    A loopback address says nothing about which machine Music Assistant is on,
    so it never makes the bridge strict.
    """
    resolve = resolver or _system_resolver
    names: set[str] = set()
    server = str(sendspin_server or "").strip()
    if server.lower() not in _AUTO_HOSTS:
        names.add(normalize_host(server))
    for url in ma_api_urls:
        text = str(url or "").strip()
        if not text:
            continue
        try:
            host = urlsplit(text if "://" in text else f"http://{text}").hostname
        except ValueError:
            continue
        if host:
            names.add(normalize_host(host))
    names = {n for n in names if n and not _is_loopback(n)}
    if not names:
        return None
    hosts = set(names)
    for name in sorted(names):
        try:
            ipaddress.ip_address(name)
        except ValueError:
            hosts.update(normalize_host(a) for a in await resolve(name))
    return OwnServer(hosts=frozenset(hosts))


@dataclass
class RefusalLog:
    """Remembers refused servers and says when one is worth logging again."""

    period_s: float = REFUSAL_LOG_PERIOD_S
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    _logged_at: dict[str, float] = field(default_factory=dict)

    def note(self, *, server_id: str | None, host: str | None, name: str | None) -> bool:
        """Record a refusal; return whether it should be logged now."""
        key = server_id or normalize_host(host) or "?"
        now = self.clock()
        entry = self._entries.setdefault(key, {"count": 0})
        entry.update(
            {"server_id": server_id, "host": normalize_host(host) or None, "name": name, "at": _iso_now(), "mono": now}
        )
        entry["count"] += 1
        last = self._logged_at.get(key)
        if last is not None and now - last < self.period_s:
            return False
        self._logged_at[key] = now
        return True

    def recent(self) -> list[dict[str, Any]]:
        """The latest refusals, newest first, in a shape the status carries."""
        rows = sorted(self._entries.values(), key=lambda e: e["mono"], reverse=True)
        return [{k: v for k, v in row.items() if k != "mono"} for row in rows[:_MAX_RECENT_REFUSALS]]


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()


#: The address the connection being served was dialled from.  The aiosendspin
#: client decides admission without seeing the socket, so the listener's
#: handler records it for the task that runs that decision.
peer_host_var: ContextVar[str | None] = ContextVar("sendspin_peer_host", default=None)


class ServerGate:
    """The admission decision for one player, with its side effects.

    ``admits`` answers whether a freshly handshaken connection may take the
    player; a refusal is remembered so the later reject step can tell it from
    an ordinary lost arbitration, and so the diagnostics can list it.
    """

    def __init__(
        self,
        own: OwnServer | None,
        *,
        enforce: bool = True,
        on_refused: Callable[[list[dict[str, Any]]], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.own = own
        self.enforce = enforce
        self.trusted_ids: set[str] = set()
        self.refusals = RefusalLog()
        self._refused: set[int] = set()
        self._on_refused = on_refused
        self._logger = logger or logging.getLogger(__name__)

    def verdict_for(self, server_id: str | None, peer_host: str | None) -> Verdict:
        return decide(
            self.own, server_id=server_id, peer_host=peer_host, trusted_ids=self.trusted_ids, enforce=self.enforce
        )

    def admits(self, connection: Any) -> bool:
        server_id = getattr(connection, "server_id", None)
        verdict = self.verdict_for(server_id, peer_host_var.get())
        if verdict.accept:
            if verdict.own and server_id:
                self.trusted_ids.add(server_id)
            return True
        info = getattr(connection, "server_info", None)
        name = getattr(info, "name", None)
        host = peer_host_var.get()
        self._refused.add(id(connection))
        if self.refusals.note(server_id=server_id, host=host, name=name):
            self._logger.warning(
                "Refused Music Assistant at %s (%s): this bridge belongs to %s",
                host or "unknown host",
                name or server_id or "unnamed",
                self._own_label(),
            )
        if self._on_refused is not None:
            self._on_refused(self.refusals.recent())
        return False

    def _own_label(self) -> str:
        if self.own is None:
            return "another server"
        return self.own.name or ", ".join(sorted(self.own.hosts)) or "another server"

    def take_refused(self, connection: Any) -> bool:
        """Whether ``connection`` was refused here; forgets it."""
        if id(connection) in self._refused:
            self._refused.discard(id(connection))
            return True
        return False
