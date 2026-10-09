"""Errors a use case raises, and the shared checks that raise them.

``UseCaseError`` carries an HTTP-style status as a *category* (400 bad input,
404 unknown, 409 conflict, 503 not ready…) so every adapter — REST, MCP, the
HA compat router — can map it without knowing the use case.
"""

from __future__ import annotations

import errno
import logging
import os
import os.path as _os_path
import re
from typing import Any

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_ADAPTER_ID_RE = re.compile(r"^(hci\d+|[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5})$")


class UseCaseError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        title: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title or code.replace("_", " ").capitalize()
        self.errors = errors
        self.headers = headers or {}
        #: Additional structured fields for the problem body (e.g. ``remediation``).
        self.extra = extra or {}


def bad_request(detail: str, code: str = "bad_request") -> UseCaseError:
    return UseCaseError(400, code, detail)


def not_found(detail: str, code: str = "not_found") -> UseCaseError:
    return UseCaseError(404, code, detail)


def conflict(detail: str, code: str = "conflict") -> UseCaseError:
    return UseCaseError(409, code, detail)


def unavailable(detail: str, code: str = "unavailable") -> UseCaseError:
    return UseCaseError(503, code, detail)


def validate_mac(mac: str) -> bool:
    return bool(_MAC_RE.fullmatch(mac or ""))


def require_mac(mac: str | None, *, field: str = "mac") -> str:
    value = (mac or "").strip().upper()
    if not validate_mac(value):
        raise UseCaseError(400, "invalid_mac", f"{field} must be a Bluetooth address like AA:BB:CC:DD:EE:FF")
    return value


def normalize_adapter(adapter: str | None) -> str:
    """An adapter id (``hciN`` or a controller MAC), or ``""`` for the default."""
    if not adapter:
        return ""
    adapter = adapter.strip()
    if adapter and not _ADAPTER_ID_RE.fullmatch(adapter):
        raise UseCaseError(400, "invalid_adapter", f"Invalid adapter identifier: {adapter!r}")
    return adapter


def config_write_error(exc: OSError, context: str | None = None) -> UseCaseError:
    """The config directory could not be written; say how to fix it (issue #190)."""
    logger.exception("Config write failed: %s", exc)
    error_no = getattr(exc, "errno", None)
    filename = getattr(exc, "filename", None)
    if filename:
        config_dir = str(_os_path.dirname(filename) or filename)
    else:
        from sendspin_bridge.config import CONFIG_DIR as _live_config_dir

        config_dir = str(_live_config_dir)
    uid = os.getuid()
    prefix = f"{context}: " if context else ""
    if error_no == errno.EACCES or isinstance(exc, PermissionError):
        return UseCaseError(
            500,
            "config_not_writable",
            f"{prefix}{config_dir} is not writable by UID {uid} (permission denied).",
            extra={
                "remediation": {
                    "summary": "Bind-mount target needs to be owned by the bridge UID",
                    "fix": f"On the host: chown -R {uid}:{os.getgid()} <bind-mount target for {config_dir}>",
                    "details_url": "https://github.com/trudenboy/sendspin-bt-bridge/issues/190",
                }
            },
        )
    if error_no == errno.EROFS:
        return UseCaseError(
            500,
            "config_read_only",
            f"{prefix}{config_dir} is on a read-only file system.",
            extra={
                "remediation": {
                    "summary": "Bind-mount must be writable for runtime config persistence",
                    "fix": f"Remount {config_dir} read-write (rw), or move CONFIG_DIR to a writable path",
                    "details_url": None,
                }
            },
        )
    return UseCaseError(
        500,
        "config_write_failed",
        f"{prefix}config write failed: {exc}",
        extra={
            "remediation": {
                "summary": "Inspect the bridge logs for the underlying error",
                "fix": "Check container logs (docker logs / journalctl) for the full traceback",
                "details_url": None,
            }
        },
    )
