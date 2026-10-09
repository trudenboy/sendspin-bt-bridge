"""Signing in: which methods exist, and checking credentials against each.

HTTP-free. The router decides what a successful sign-in does to the session;
this module only answers *whether* the credentials are good, or what the next
step of a multi-factor flow is.

Methods (auto-detected):

* ``ha`` — Home Assistant Core ``login_flow`` (add-on mode only; supports MFA);
* ``ha_via_ma`` — the same flow against the HA instance next to Music Assistant;
* ``ma`` — Music Assistant credentials;
* ``password`` — the local PBKDF2 password (always offered outside add-on mode).
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request as _ur
from dataclasses import dataclass, field
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from sendspin_bridge.config import check_password, load_config
from sendspin_bridge.security.login_rate_limiter import LockoutSettings, LoginRateLimiter

logger = logging.getLogger(__name__)

AuthMethod = Literal["ha", "ha_via_ma", "ma", "password"]

_HA_CORE_URL = os.environ.get("HA_CORE_URL", "http://homeassistant:8123").rstrip("/")
_FLOW_ID_RE = re.compile(r"[0-9a-f-]{32,36}", re.IGNORECASE)

_LOCKOUT_MAX_ATTEMPTS = 5
_LOCKOUT_WINDOW_SECS = 60
_LOCKOUT_DURATION_SECS = 300


def is_ha_addon() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def available_methods() -> list[AuthMethod]:
    """Sign-in methods this bridge offers right now."""
    if is_ha_addon():
        return ["ha"]
    methods: list[AuthMethod] = []
    config = load_config()
    if config.get("MA_API_URL") and config.get("MA_API_TOKEN"):
        methods.append("ha_via_ma" if config.get("MA_AUTH_PROVIDER") == "ha" else "ma")
    methods.append("password")
    return methods


# ---------------------------------------------------------------------------
# Brute-force protection
# ---------------------------------------------------------------------------


def _coerce_int(value, default: int, low: int, high: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, coerced))


def lockout_settings() -> LockoutSettings:
    config = load_config()
    return LockoutSettings(
        enabled=bool(config.get("BRUTE_FORCE_PROTECTION", True)),
        max_attempts=_coerce_int(config.get("BRUTE_FORCE_MAX_ATTEMPTS", _LOCKOUT_MAX_ATTEMPTS), 5, 1, 50),
        window_s=_coerce_int(config.get("BRUTE_FORCE_WINDOW_MINUTES", 1), 1, 1, 1440) * 60,
        lockout_s=_coerce_int(config.get("BRUTE_FORCE_LOCKOUT_MINUTES", 5), 5, 1, 1440) * 60,
    )


#: One limiter for the process; it reads its settings live.
rate_limiter = LoginRateLimiter(settings_provider=lockout_settings)


def format_duration(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} seconds"


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

OutcomeKind = Literal["success", "mfa_required", "invalid", "unavailable", "expired"]


@dataclass
class LoginOutcome:
    kind: OutcomeKind
    user: str | None = None
    message: str = ""
    flow_id: str | None = None
    mfa_module_id: str | None = None
    mfa_module_name: str | None = None
    #: Counts as a failed attempt for brute-force protection.
    counts_as_failure: bool = False
    #: Supervisor fallback used — MFA was not verified.
    fallback_used: bool = False
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Home Assistant login_flow
# ---------------------------------------------------------------------------


def _redact_flow_result(result: object) -> dict:
    """Routing fields of a login_flow step, never the authorization code."""
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    summary: dict[str, object] = {
        k: result[k] for k in ("type", "flow_id", "step_id", "handler", "reason") if k in result
    }
    errors = result.get("errors")
    if isinstance(errors, dict):
        summary["errors"] = sorted(errors.keys())
    summary["has_result"] = bool(result.get("result"))
    return summary


def _post_json(url: str, payload: dict, timeout: float = 10) -> dict:
    req = _ur.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with _ur.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def flow_start(ha_url: str) -> dict | None:
    """Start a login_flow.

    ``{"_ha_error": True}`` when HA answered with an error — the caller must not
    fall back to Supervisor auth (that would bypass MFA). ``None`` when HA is
    unreachable.
    """
    client_id = f"{ha_url}/"
    try:
        return _post_json(
            f"{ha_url}/auth/login_flow",
            {"client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id},
        )
    except HTTPError as exc:
        logger.warning("HA login_flow HTTP %s — service error, MFA bypass prevented", exc.code)
        return {"_ha_error": True}
    except (URLError, OSError, ValueError) as exc:
        logger.warning("HA login_flow unreachable: %s", exc)
        return None


def flow_step(ha_url: str, flow_id: str, data: dict) -> dict | None:
    if not _FLOW_ID_RE.fullmatch(flow_id or ""):
        logger.warning("Invalid flow_id rejected")
        return None
    try:
        result = _post_json(f"{ha_url}/auth/login_flow/{flow_id}", {"client_id": f"{ha_url}/", **data})
        logger.debug("HA flow step result: %s", _redact_flow_result(result))
        return result
    except HTTPError as exc:
        try:
            result = json.loads(exc.read())
            logger.warning("HA flow step HTTP %s: %s", exc.code, _redact_flow_result(result))
            return result
        except (json.JSONDecodeError, ValueError):
            logger.warning("HA flow step HTTP %s (unparseable body)", exc.code)
            return None
    except (URLError, OSError, ValueError) as exc:
        logger.warning("HA login flow step error: %s", exc)
        return None


def _supervisor_auth(username: str, password: str) -> bool:
    """Supervisor ``/auth`` — bypasses MFA; used only with ALLOW_SUPERVISOR_FALLBACK=1."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    req = _ur.Request(
        "http://supervisor/auth",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        _ur.urlopen(req, timeout=10)
        return True
    except HTTPError:
        return False
    except (URLError, OSError) as exc:
        logger.warning("HA supervisor auth fallback error: %s", exc)
        return False


def ha_url_from_ma() -> str | None:
    """HA Core next to Music Assistant (MA as an HA add-on): same host, port 8123."""
    ma_url = load_config().get("MA_API_URL", "")
    if not ma_url:
        return None
    parsed = urlparse(ma_url)
    return f"{parsed.scheme}://{parsed.hostname}:8123"


def _flow_result_outcome(result: dict | None, *, flow_id: str, user: str) -> LoginOutcome:
    if result is None:
        return LoginOutcome("unavailable", message="Authentication service unavailable")
    if result.get("type") == "create_entry":
        return LoginOutcome("success", user=user)
    if result.get("type") == "form" and result.get("step_id") == "mfa":
        placeholders = result.get("description_placeholders") or {}
        return LoginOutcome(
            "mfa_required",
            user=user,
            flow_id=flow_id,
            mfa_module_id=placeholders.get("mfa_module_id", "totp"),
            mfa_module_name=placeholders.get("mfa_module_name", "Authenticator app"),
        )
    errors = result.get("errors") or {}
    message = "Invalid credentials" if errors.get("base") == "invalid_auth" else "Authentication failed"
    return LoginOutcome("invalid", message=message, counts_as_failure=True)


def ha_credentials(ha_url: str, username: str, password: str, *, allow_supervisor_fallback: bool) -> LoginOutcome:
    """Step 1 of an HA sign-in: username and password."""
    flow = flow_start(ha_url)
    if flow is None:
        if allow_supervisor_fallback and os.environ.get("ALLOW_SUPERVISOR_FALLBACK") == "1":
            logger.warning(
                "HA login_flow unreachable — falling back to Supervisor auth "
                "(ALLOW_SUPERVISOR_FALLBACK=1). This path does NOT verify MFA."
            )
            if _supervisor_auth(username, password):
                return LoginOutcome("success", user=username, fallback_used=True)
            return LoginOutcome("invalid", message="Invalid credentials", counts_as_failure=True)
        logger.error("HA login_flow unreachable; refusing Supervisor fallback (MFA cannot be verified)")
        return LoginOutcome("unavailable", message="Authentication service unavailable")
    if flow.get("_ha_error") or not flow.get("flow_id"):
        return LoginOutcome("unavailable", message="Home Assistant authentication service unavailable")
    flow_id = flow["flow_id"]
    return _flow_result_outcome(
        flow_step(ha_url, flow_id, {"username": username, "password": password}), flow_id=flow_id, user=username
    )


def ha_mfa(ha_url: str, flow_id: str, code: str, *, user: str) -> LoginOutcome:
    """Step 2 of an HA sign-in: the one-time code."""
    code = (code or "").replace(" ", "").replace("-", "")
    if not flow_id or not _FLOW_ID_RE.fullmatch(flow_id):
        return LoginOutcome("expired", message="Session expired — please sign in again")
    if not code:
        return LoginOutcome("mfa_required", user=user, flow_id=flow_id, message="Authentication code is required")
    result = flow_step(ha_url, flow_id, {"code": code})
    if result and result.get("type") == "create_entry":
        return LoginOutcome("success", user=user)
    if result and result.get("type") == "abort":
        return LoginOutcome("expired", message="Session expired — please sign in again", counts_as_failure=True)
    return LoginOutcome(
        "mfa_required", user=user, flow_id=flow_id, message="Invalid authentication code", counts_as_failure=True
    )


# ---------------------------------------------------------------------------
# Music Assistant and local password
# ---------------------------------------------------------------------------


def ma_credentials(username: str, password: str) -> LoginOutcome:
    ma_url = load_config().get("MA_API_URL", "")
    if not ma_url:
        return LoginOutcome("unavailable", message="Music Assistant is not connected")
    from sendspin_bridge.application.music_assistant.auth import ma_http_login

    try:
        ma_http_login(ma_url, username, password)
        return LoginOutcome("success", user=username)
    except RuntimeError as exc:
        logger.warning("MA auth failed: %s", exc)
        return LoginOutcome("invalid", message="Invalid credentials", counts_as_failure=True)
    except (ConnectionError, OSError) as exc:
        logger.warning("MA auth failed (network): %s", exc)
        return LoginOutcome("unavailable", message="Music Assistant server is unreachable")
    except Exception as exc:
        logger.warning("MA auth unexpected error: %s", exc)
        return LoginOutcome("unavailable", message="Authentication service error")


def local_password(password: str) -> LoginOutcome:
    stored = load_config().get("AUTH_PASSWORD_HASH", "")
    if not stored:
        return LoginOutcome("unavailable", message="No password configured — set one in the settings")
    if check_password(password, stored):
        return LoginOutcome("success")
    return LoginOutcome("invalid", message="Invalid password", counts_as_failure=True)


def sign_in(
    method: AuthMethod,
    *,
    username: str = "",
    password: str = "",
    flow_id: str = "",
    code: str = "",
    pending_user: str = "",
) -> LoginOutcome:
    """Run one step of a sign-in with *method*."""
    if is_ha_addon() and method != "ha":
        method = "ha"
    if method == "ha":
        if flow_id:
            return ha_mfa(_HA_CORE_URL, flow_id, code, user=pending_user)
        return ha_credentials(_HA_CORE_URL, username.strip(), password, allow_supervisor_fallback=True)
    if method == "ha_via_ma":
        ha_url = ha_url_from_ma()
        if not ha_url:
            return LoginOutcome("unavailable", message="Music Assistant is not connected")
        if flow_id:
            return ha_mfa(ha_url, flow_id, code, user=pending_user)
        return ha_credentials(ha_url, username.strip(), password, allow_supervisor_fallback=False)
    if method == "ma":
        return ma_credentials(username.strip(), password)
    return local_password(password)
