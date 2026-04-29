"""Runtime-scoped webhook delivery for internal bridge events."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

UTC = timezone.utc

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sendspin_bridge.services.diagnostics.internal_events import InternalEvent

logger = logging.getLogger(__name__)


@dataclass
class EventHook:
    hook_id: str
    url: str
    categories: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    timeout_sec: float = 5.0
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_http_status: int | None = None
    last_error: str | None = None
    success_count: int = 0
    failure_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.hook_id,
            "url": self.url,
            "categories": list(self.categories),
            "event_types": list(self.event_types),
            "timeout_sec": self.timeout_sec,
            "created_at": self.created_at,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_http_status": self.last_http_status,
            "last_error": self.last_error,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


class EventHookRegistry:
    """Thread-safe runtime-scoped webhook registry with delivery history."""

    def __init__(self, *, delivery_history_limit: int = 50, max_workers: int = 4):
        self._hooks: dict[str, EventHook] = {}
        self._recent_deliveries: deque[dict[str, Any]] = deque(maxlen=delivery_history_limit)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="event-hooks")

    def shutdown(self) -> None:
        """Shut down the background delivery thread pool."""
        self._executor.shutdown(wait=False)

    def clear(self) -> None:
        self.shutdown()
        with self._lock:
            self._hooks.clear()
            self._recent_deliveries.clear()

    def register(
        self,
        *,
        url: str,
        categories: Iterable[str] | None = None,
        event_types: Iterable[str] | None = None,
        timeout_sec: float = 5.0,
    ) -> dict[str, Any]:
        normalized_url = self._normalize_url(url)
        normalized_categories = self._normalize_filter_values(categories)
        normalized_event_types = self._normalize_filter_values(event_types)
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be greater than 0")
        hook = EventHook(
            hook_id=str(uuid.uuid4()),
            url=normalized_url,
            categories=normalized_categories,
            event_types=normalized_event_types,
            timeout_sec=float(timeout_sec),
        )
        with self._lock:
            self._hooks[hook.hook_id] = hook
        return hook.to_dict()

    def unregister(self, hook_id: str) -> bool:
        with self._lock:
            return self._hooks.pop(hook_id, None) is not None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            hooks = [hook.to_dict() for hook in self._hooks.values()]
            deliveries = list(self._recent_deliveries)
        return {
            "delivery_mode": "runtime",
            "summary": {
                "registered_hooks": len(hooks),
                "successful_deliveries": sum(int(hook["success_count"]) for hook in hooks),
                "failed_deliveries": sum(int(hook["failure_count"]) for hook in hooks),
                "recent_deliveries": len(deliveries),
            },
            "hooks": hooks,
            "recent_deliveries": deliveries,
        }

    def dispatch(self, event: InternalEvent, *, background: bool = True) -> int:
        matched_hooks = self._matching_hooks(event)
        for hook in matched_hooks:
            if background:
                self._executor.submit(self._deliver_hook, hook.hook_id, event)
            else:
                self._deliver_hook(hook.hook_id, event)
        return len(matched_hooks)

    @staticmethod
    def _normalize_filter_values(values: Iterable[str] | None) -> tuple[str, ...]:
        normalized = []
        for value in values or ():
            cleaned = str(value or "").strip()
            if cleaned:
                normalized.append(cleaned)
        return tuple(dict.fromkeys(normalized))

    @staticmethod
    def _normalize_url(url: str) -> str:
        normalized = str(url or "").strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute http:// or https:// URL")
        hostname = (parsed.hostname or "").strip()
        if not hostname:
            raise ValueError("url must include a hostname")
        EventHookRegistry._validate_hook_host(hostname, parsed.port, parsed.scheme)
        return normalized

    @staticmethod
    def _validate_hook_host(hostname: str, port: int | None, scheme: str) -> None:
        normalized_host = hostname.strip().lower()
        if normalized_host in {"localhost", "localhost.localdomain"} or normalized_host.endswith(".local"):
            raise ValueError("url must not target loopback, local, or private network hosts")
        try:
            addresses = EventHookRegistry._resolve_host_addresses(normalized_host, port, scheme)
        except OSError as exc:
            raise ValueError(f"could not resolve hook host: {normalized_host}") from exc
        if not addresses:
            raise ValueError(f"could not resolve hook host: {normalized_host}")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError("url must not target loopback, local, or private network hosts")

    @staticmethod
    def _resolve_host_addresses(hostname: str, port: int | None, scheme: str) -> set[str]:
        try:
            return {str(ipaddress.ip_address(hostname))}
        except ValueError:
            resolved_port = port or (443 if scheme == "https" else 80)
            addresses: set[str] = set()
            for item in socket.getaddrinfo(hostname, resolved_port, type=socket.SOCK_STREAM):
                sockaddr = item[4]
                if not sockaddr:
                    continue
                address = sockaddr[0]
                if isinstance(address, str) and address:
                    addresses.add(address)
            return addresses

    def _matching_hooks(self, event: InternalEvent) -> list[EventHook]:
        with self._lock:
            hooks = list(self._hooks.values())
        return [
            hook
            for hook in hooks
            if (not hook.categories or event.category in hook.categories)
            and (not hook.event_types or event.event_type in hook.event_types)
        ]

    def _deliver_hook(self, hook_id: str, event: InternalEvent) -> None:
        started_at = datetime.now(tz=UTC).isoformat()
        started_monotonic = time.monotonic()
        with self._lock:
            hook = self._hooks.get(hook_id)
            if hook is None:
                return
            hook.last_attempt_at = started_at

        status = "success"
        http_status: int | None = None
        error: str | None = None
        payload = {
            "event_type": event.event_type,
            "category": event.category,
            "subject_id": event.subject_id,
            "payload": event.payload,
            "at": event.at,
        }
        request = urllib.request.Request(
            hook.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Sendspin-Event": event.event_type,
                "X-Sendspin-Category": event.category,
                "X-Sendspin-Hook-Id": hook.hook_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=hook.timeout_sec) as response:
                status_value = getattr(response, "status", None)
                if status_value is None:
                    status_value = response.getcode()
                http_status = int(status_value)
        except urllib.error.HTTPError as exc:
            status = "failed"
            http_status = exc.code
            error = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            status = "failed"
            error = str(exc.reason)
        except OSError as exc:
            status = "failed"
            error = str(exc)

        finished_at = datetime.now(tz=UTC).isoformat()
        duration_ms = round((time.monotonic() - started_monotonic) * 1000, 1)
        with self._lock:
            hook = self._hooks.get(hook_id)
            if hook is None:
                return
            hook.last_http_status = http_status
            hook.last_error = error
            if status == "success":
                hook.success_count += 1
                hook.last_success_at = finished_at
            else:
                hook.failure_count += 1
                hook.last_failure_at = finished_at
                logger.warning("Event hook delivery failed for %s: %s", hook.url, error or "unknown error")
            self._recent_deliveries.appendleft(
                {
                    "hook_id": hook.hook_id,
                    "url": hook.url,
                    "event_type": event.event_type,
                    "category": event.category,
                    "subject_id": event.subject_id,
                    "status": status,
                    "http_status": http_status,
                    "error": error,
                    "duration_ms": duration_ms,
                    "attempted_at": started_at,
                    "completed_at": finished_at,
                }
            )


_event_hook_registry = EventHookRegistry()


def get_event_hook_registry() -> EventHookRegistry:
    return _event_hook_registry


def dispatch_internal_event_to_hooks(event: InternalEvent) -> None:
    _event_hook_registry.dispatch(event, background=True)
