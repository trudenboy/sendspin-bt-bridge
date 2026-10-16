"""A thin synchronous face over Music Assistant's WebSocket API for the sync adapters.

Callers are API worker threads: every command runs on the bridge loop through
``run_on_loop`` (bounded wait), so the event loop itself is never blocked; a call
made from the bridge loop thread is refused at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
from typing import TYPE_CHECKING, Any, Protocol

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.runtime import run_on_loop, submit_on_loop
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials

if TYPE_CHECKING:
    from collections.abc import Callable

    from music_assistant_client import MusicAssistantClient

logger = logging.getLogger(__name__)

_FORBIDDEN_ERRORS = frozenset(
    {"InsufficientPermissions", "AuthenticationRequired", "AuthenticationFailed", "InvalidToken"}
)
# whole words only: a hex player id such as "5f4013ab" must not read as HTTP 401
_FORBIDDEN_WORDS = re.compile(r"\b(forbidden|permission|permissions|scope|unauthorized|401|403)\b")
_UNAVAILABLE_ERRORS = frozenset({"TransportError", "NotConnected", "InvalidState"})
_FIRE_WINDOW_S = 2.0  # a fired command that fails fails fast; after this the clip is playing
_CONNECT_TIMEOUT_S = 10.0


class MaApiError(Exception):
    """``code`` is one of ``ma_unavailable`` | ``ma_forbidden`` | ``ma_error``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class MaApi(Protocol):
    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any:
        """Send *command* and wait up to *timeout* seconds for its reply."""
        ...

    def fire(self, command: str, args: dict[str, Any]) -> None:
        """Send a command whose reply comes late (``play_announcement`` answers when the clip ends).

        Raises only for a rejection that arrives within a short window; the eventual reply is dropped.
        """
        ...


def _classify(exc: BaseException) -> MaApiError:
    """The one place that turns a Music Assistant / transport failure into a stable code."""
    if isinstance(exc, MaApiError):
        return exc
    names = {cls.__name__ for cls in type(exc).__mro__}
    text = str(exc).lower()
    if names & _FORBIDDEN_ERRORS or _FORBIDDEN_WORDS.search(text):
        return MaApiError("ma_forbidden", str(exc))
    if (
        names & _UNAVAILABLE_ERRORS
        or isinstance(exc, (ConnectionError, OSError, TimeoutError, asyncio.CancelledError))
        or (isinstance(exc, UseCaseError) and exc.code in ("not_ready", "timeout"))
        or "not connected" in text
    ):
        return MaApiError("ma_unavailable", str(exc) or type(exc).__name__)
    return MaApiError("ma_error", str(exc) or type(exc).__name__)


def _bridge_loop_or_raise() -> None:
    from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

    loop = get_main_loop()
    if loop is None:  # checked before any coroutine exists, so none is left unawaited
        raise MaApiError("ma_unavailable", "bridge loop not running")
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        raise RuntimeError("MaWsApi blocks; call it from a worker thread, never from the bridge loop")


def _log_late_reply(command: str) -> Callable[[asyncio.Future[Any]], None]:
    def _done(fut: asyncio.Future[Any]) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            logger.info("Music Assistant %s ended with an error: %s", command, exc)

    return _done


class MaWsApi:
    """One ``MusicAssistantClient`` per instance, connected lazily on the bridge loop.

    The client runs in listening mode (``start_listening`` as a background task), so
    server pings are answered, pushed events are drained and every reply is routed to
    its own command by message id — concurrent calls need no lock, and a reply nobody
    waits for any more is dropped.  When the connection closes, the listener ends and
    the next call builds a fresh client.
    """

    def __init__(self, url: str, token: str, *, fire_window: float = _FIRE_WINDOW_S) -> None:
        self.credentials = (url, token)  # as given, to tell whether the configured ones changed
        url = url.strip()
        self._url = url if "://" in url else f"http://{url}"
        self._token = token
        self._fire_window = fire_window
        self._client: MusicAssistantClient | None = None
        self._listener: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()  # guards building the client, not the commands

    def _alive(self) -> bool:
        return (
            self._client is not None
            and self._listener is not None
            and not self._listener.done()
            and self._client.connection.connected
        )

    async def _connected(self) -> MusicAssistantClient:
        async with self._lock:
            if self._alive():
                assert self._client is not None
                return self._client
            await self._drop()
            from music_assistant_client import MusicAssistantClient

            client = MusicAssistantClient(self._url, None, token=self._token)
            try:
                await client.connect()
            except BaseException:
                with contextlib.suppress(Exception):
                    await client.disconnect()
                raise
            listener = asyncio.get_running_loop().create_task(client.start_listening(), name="ma-sync-listener")
            # start_listening re-enters connect() (already connected: returns at once) and then
            # switches the client to routed replies; let that first step run before any command.
            for _ in range(100):
                await asyncio.sleep(0)
                if getattr(client, "_listening", True) or listener.done():
                    break
            if listener.done():
                listener.result()  # raises why it stopped
                raise MaApiError("ma_unavailable", "Music Assistant listener stopped")
            self._client, self._listener = client, listener
            return client

    async def _drop(self) -> None:
        client, listener = self._client, self._listener
        self._client = self._listener = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:
                logger.debug("Closing the Music Assistant client failed: %s", exc)
        if listener is not None and not listener.done():
            listener.cancel()
            with contextlib.suppress(BaseException):
                await listener

    async def _call(self, command: str, args: dict[str, Any]) -> Any:
        client = await self._connected()
        return await client.send_command(command, **args)

    async def _fire(self, command: str, args: dict[str, Any]) -> None:
        client = await self._connected()
        reply = asyncio.ensure_future(client.send_command(command, **args))
        done, _ = await asyncio.wait({reply}, timeout=self._fire_window)
        if reply in done:
            reply.result()  # a fast rejection raises here
            return
        reply.add_done_callback(_log_late_reply(command))

    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any:
        _bridge_loop_or_raise()
        try:
            return run_on_loop(self._call(command, args), timeout=timeout, description=f"MA {command}")
        except Exception as exc:
            raise _classify(exc) from exc

    def fire(self, command: str, args: dict[str, Any]) -> None:
        _bridge_loop_or_raise()
        timeout = self._fire_window + _CONNECT_TIMEOUT_S
        try:
            run_on_loop(self._fire(command, args), timeout=timeout, description=f"MA {command}")
        except Exception as exc:
            raise _classify(exc) from exc

    async def aclose(self) -> None:
        """Disconnect and stop the listener; run on the bridge loop."""
        async with self._lock:
            await self._drop()

    def close(self) -> None:
        """Disconnect and stop the listener (scheduled on the bridge loop; not waited for)."""
        submit_on_loop(self.aclose(), description="MA close")


def ma_api_from_config() -> MaWsApi:
    url, token = get_ma_api_credentials()
    if not url or not token:
        raise MaApiError("ma_unavailable", "Music Assistant is not connected")
    return MaWsApi(url, token)


# -- one long-lived connection for requests outside a session ------------------------------

_shared_lock = threading.Lock()
_shared: MaWsApi | None = None


def shared_ma_api() -> MaWsApi:
    """The connection session-less requests share (group lists, manual writes, profiles).

    Built on first use and kept open (it reconnects lazily when MA drops it); replaced, and
    the old one closed, when the configured credentials change. Sessions keep their own.
    """
    global _shared
    url, token = get_ma_api_credentials()
    if not url or not token:
        raise MaApiError("ma_unavailable", "Music Assistant is not connected")
    with _shared_lock:
        old = _shared
        if old is not None and old.credentials == (url, token):
            return old
        _shared = api = MaWsApi(url, token)
    if old is not None:
        old.close()
    return api


def _take_shared() -> MaWsApi | None:
    global _shared
    with _shared_lock:
        api, _shared = _shared, None
    return api


def close_shared_ma_api() -> None:
    """Close the shared connection (scheduled on the bridge loop); the next request opens a new one."""
    api = _take_shared()
    if api is not None:
        api.close()


async def aclose_shared_ma_api() -> None:
    """Close the shared connection and wait for it; the bridge's shutdown awaits this on its loop."""
    api = _take_shared()
    if api is not None:
        await api.aclose()
