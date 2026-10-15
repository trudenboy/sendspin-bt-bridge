"""A thin synchronous face over Music Assistant's WebSocket API for the sync adapters.

Callers are API worker threads: every command runs on the bridge loop through
``run_on_loop`` (waited, bounded) or ``submit_on_loop`` (fired, never waited),
so the event loop itself is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.runtime import run_on_loop, submit_on_loop
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials

if TYPE_CHECKING:
    from music_assistant_client import MusicAssistantClient

logger = logging.getLogger(__name__)

_FORBIDDEN_ERRORS = ("InsufficientPermissions", "AuthenticationRequired", "AuthenticationFailed", "InvalidToken")
_FORBIDDEN_WORDS = ("forbidden", "permission", "scope", "unauthorized", "403", "401")
_UNAVAILABLE_ERRORS = ("TransportError", "NotConnected", "InvalidState")


class MaApiError(Exception):
    """``code`` is one of ``ma_unavailable`` | ``ma_forbidden`` | ``ma_error``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class MaApi(Protocol):
    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any: ...
    def fire(self, command: str, args: dict[str, Any]) -> None: ...


def _classify(exc: BaseException) -> MaApiError:
    """The one place that turns a Music Assistant / transport failure into a stable code."""
    if isinstance(exc, MaApiError):
        return exc
    names = {cls.__name__ for cls in type(exc).__mro__}
    text = str(exc).lower()
    if names & set(_FORBIDDEN_ERRORS) or any(w in text for w in _FORBIDDEN_WORDS):
        return MaApiError("ma_forbidden", str(exc))
    if (
        names & set(_UNAVAILABLE_ERRORS)
        or isinstance(exc, (ConnectionError, OSError, TimeoutError, asyncio.CancelledError))
        or (isinstance(exc, UseCaseError) and exc.code in ("not_ready", "timeout"))
        or "not connected" in text
    ):
        return MaApiError("ma_unavailable", str(exc) or type(exc).__name__)
    return MaApiError("ma_error", str(exc) or type(exc).__name__)


class MaWsApi:
    """One ``MusicAssistantClient`` per instance, connected lazily on the bridge loop.

    The client is not started in listening mode, so ``send_command`` reads its own
    reply off the socket; the lock keeps two such reads from interleaving.  A reply
    to a fired command (``play_announcement`` answers only when the clip ends) is
    skipped by the next reader because its message id does not match.
    """

    def __init__(self, url: str, token: str) -> None:
        url = url.strip()
        self._url = url if "://" in url else f"http://{url}"
        self._token = token
        self._client: MusicAssistantClient | None = None
        self._lock = asyncio.Lock()

    async def _connected(self) -> MusicAssistantClient:
        if self._client is None:
            from music_assistant_client import MusicAssistantClient

            client = MusicAssistantClient(self._url, None, token=self._token)
            await client.__aenter__()  # connects
            self._client = client
        return self._client

    async def _drop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("Closing the Music Assistant client failed: %s", exc)

    async def _call(self, command: str, args: dict[str, Any]) -> Any:
        async with self._lock:
            try:
                client = await self._connected()
                return await client.send_command(command, **args)
            except BaseException as exc:
                if _classify(exc).code == "ma_unavailable":
                    await asyncio.shield(self._drop())
                raise

    async def _fire(self, command: str, args: dict[str, Any]) -> None:
        async with self._lock:
            try:
                client = await self._connected()
                await client.send_command_no_wait(command, **args)
            except Exception as exc:
                logger.warning("Music Assistant %s failed: %s", command, exc)
                if _classify(exc).code == "ma_unavailable":
                    await self._drop()
                raise

    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any:
        from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

        if get_main_loop() is None:  # checked first so no coroutine is created and left unawaited
            raise MaApiError("ma_unavailable", "bridge loop not running")
        try:
            return run_on_loop(self._call(command, args), timeout=timeout, description=f"MA {command}")
        except Exception as exc:
            raise _classify(exc) from exc

    def fire(self, command: str, args: dict[str, Any]) -> None:
        if not submit_on_loop(self._fire(command, args), description=f"MA {command}"):
            raise MaApiError("ma_unavailable", "bridge loop not running")

    def close(self) -> None:
        async def _close() -> None:
            async with self._lock:
                await self._drop()

        submit_on_loop(_close(), description="MA close")


def ma_api_from_config() -> MaWsApi:
    url, token = get_ma_api_credentials()
    if not url or not token:
        raise MaApiError("ma_unavailable", "Music Assistant is not connected")
    return MaWsApi(url, token)
