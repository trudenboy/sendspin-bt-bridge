"""GitHub App proxy for creating issues without requiring user authentication."""

from __future__ import annotations

import base64
import json
import logging
import os
import ssl
import threading
import time
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_REPO_OWNER = "trudenboy"
_REPO_NAME = "sendspin-bt-bridge"


def _compat_ssl_ctx() -> ssl.SSLContext:
    """SSL context compatible with middleboxes that drop post-quantum TLS."""
    ctx = ssl.create_default_context()
    try:
        ctx.set_ecdh_curve("prime256v1")
    except (ValueError, ssl.SSLError):
        pass
    return ctx


# App credentials — App ID and Installation ID are not secret
_APP_ID = "3219015"
_INSTALLATION_ID = 119948226

# Rate limits
_PER_IP_MAX = 3
_PER_IP_WINDOW_SECS = 3600  # 1 hour
_GLOBAL_MAX = 20
_GLOBAL_WINDOW_SECS = 86400  # 24 hours


class GitHubIssueProxy:
    """Creates GitHub issues using a GitHub App installation token."""

    def __init__(self, private_key_pem: str | None = None):
        self._private_key = private_key_pem
        self._token: str | None = None
        self._token_expires: float = 0
        self._lock = threading.Lock()
        # Rate limiting
        self._ip_hits: dict[str, list[float]] = {}
        self._global_hits: list[float] = []
        self._rate_lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Return True if the proxy is configured with a valid private key."""
        if not self._private_key:
            return False
        try:
            import jwt  # noqa: F401

            return True
        except ImportError:
            return False

    def check_rate_limit(self, client_ip: str) -> str | None:
        """Return error message if rate-limited, None if OK."""
        now = time.monotonic()
        with self._rate_lock:
            # Clean expired entries
            self._global_hits = [t for t in self._global_hits if now - t < _GLOBAL_WINDOW_SECS]
            if len(self._global_hits) >= _GLOBAL_MAX:
                return "Global rate limit reached. Please try again later."

            hits = self._ip_hits.get(client_ip, [])
            hits = [t for t in hits if now - t < _PER_IP_WINDOW_SECS]
            self._ip_hits[client_ip] = hits
            if len(hits) >= _PER_IP_MAX:
                return f"Rate limit reached ({_PER_IP_MAX} reports per hour). Please try again later."

            # Record hit
            hits.append(now)
            self._ip_hits[client_ip] = hits
            self._global_hits.append(now)

            # Periodic sweep of stale IPs
            if len(self._ip_hits) > 200:
                stale = [ip for ip, h in self._ip_hits.items() if not h or now - h[-1] > _PER_IP_WINDOW_SECS]
                for ip in stale:
                    del self._ip_hits[ip]

        return None

    def _get_installation_token(self) -> str:
        """Return a cached or fresh installation access token."""
        import jwt as pyjwt

        with self._lock:
            if self._token and time.time() < self._token_expires - 60:
                return self._token

            now = int(time.time())
            payload = {"iat": now - 60, "exp": now + 600, "iss": _APP_ID}
            jwt_token = pyjwt.encode(payload, self._private_key, algorithm="RS256")  # type: ignore[arg-type]

            req = Request(
                f"{_GITHUB_API}/app/installations/{_INSTALLATION_ID}/access_tokens",
                method="POST",
            )
            req.add_header("Authorization", f"Bearer {jwt_token}")
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("User-Agent", "sendspin-bug-reporter")

            resp = urlopen(req, timeout=15, context=_compat_ssl_ctx())
            data = json.loads(resp.read())
            self._token = data["token"]
            # Installation tokens expire in 1 hour
            self._token_expires = time.time() + 3500
            return self._token

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a GitHub issue. Returns dict with 'number', 'html_url', 'id'."""
        token = self._get_installation_token()

        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels

        req = Request(
            f"{_GITHUB_API}/repos/{_REPO_OWNER}/{_REPO_NAME}/issues",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "sendspin-bug-reporter")
        req.add_header("Content-Type", "application/json")

        resp = urlopen(req, timeout=20, context=_compat_ssl_ctx())
        issue = json.loads(resp.read())
        logger.info("Created GitHub issue #%s: %s", issue["number"], issue["html_url"])
        return {
            "number": issue["number"],
            "html_url": issue["html_url"],
            "id": issue["id"],
        }


def _load_private_key() -> str | None:
    """Load the GitHub App private key from environment or config."""
    # 1. Environment variable (base64-encoded PEM)
    key_b64 = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
    if key_b64:
        try:
            return base64.b64decode(key_b64).decode("utf-8")
        except Exception:
            logger.warning("GITHUB_APP_PRIVATE_KEY env var is not valid base64")

    # 2. Environment variable (raw PEM)
    key_raw = os.environ.get("GITHUB_APP_PRIVATE_KEY_PEM", "")
    if key_raw and key_raw.startswith("-----BEGIN"):
        return key_raw

    # 3. File path from env
    key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_FILE", "")
    if key_path:
        try:
            with open(key_path) as f:
                return f.read()
        except OSError:
            logger.warning("Cannot read GITHUB_APP_PRIVATE_KEY_FILE: %s", key_path)

    return None


# Module-level singleton
_proxy: GitHubIssueProxy | None = None
_proxy_lock = threading.Lock()


def get_issue_proxy() -> GitHubIssueProxy:
    """Return the module-level GitHubIssueProxy singleton."""
    global _proxy
    if _proxy is None:
        with _proxy_lock:
            if _proxy is None:
                key = _load_private_key()
                _proxy = GitHubIssueProxy(key)
                if key:
                    logger.info("GitHub issue proxy initialized (App ID: %s)", _APP_ID)
                else:
                    logger.debug("GitHub issue proxy: no private key configured")
    return _proxy
