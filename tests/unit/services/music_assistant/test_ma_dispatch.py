"""One connection, many conversations, one place that sorts them out.

Every caller that wanted an answer from Music Assistant read the socket
itself, hunting for its own `message_id` and stashing whatever else turned up
along the way. Three such hunts exist, with three different bounds — ten
messages, twenty, thirty — and past the bound the answer is simply dropped:
the caller returns an empty payload and reports failure for a command the
server carried out.

A dispatcher makes that a routing question instead of a search: one reader
takes each message and hands it to whoever is waiting for it, or to the event
handler if nobody is.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.services.music_assistant.ma_dispatch import MessageDispatcher


class _Socket:
    """A socket that yields a scripted sequence of messages."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.sent: list[dict] = []

    async def recv(self) -> dict:
        if not self._messages:
            await asyncio.sleep(3600)  # nothing more will arrive
        return self._messages.pop(0)

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)


def _dispatcher(messages: list[dict], events: list[str] | None = None) -> tuple[MessageDispatcher, _Socket]:
    socket = _Socket(messages)
    collected = events if events is not None else []
    return MessageDispatcher(socket.recv, on_event=collected.append), socket


# ── routing ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_answer_reaches_the_caller_that_asked_for_it():
    dispatcher, _socket = _dispatcher([{"message_id": "7", "result": "mine"}])

    waiting = dispatcher.expect("7")
    await dispatcher.pump_once()

    assert (await waiting)["result"] == "mine"


@pytest.mark.asyncio
async def test_two_callers_do_not_take_each_other_s_answers():
    dispatcher, _socket = _dispatcher([{"message_id": "2", "result": "second"}, {"message_id": "1", "result": "first"}])

    first = dispatcher.expect("1")
    second = dispatcher.expect("2")
    await dispatcher.pump_once()
    await dispatcher.pump_once()

    assert (await first)["result"] == "first"
    assert (await second)["result"] == "second"


@pytest.mark.asyncio
async def test_a_message_nobody_waits_for_goes_to_the_event_handler():
    seen: list[str] = []
    dispatcher, _socket = _dispatcher([{"event": "player_queue_updated"}], events=seen)

    await dispatcher.pump_once()

    assert seen == ["player_queue_updated"]


@pytest.mark.asyncio
async def test_events_do_not_consume_a_caller_s_patience():
    """The defect: past a fixed number of interleaved events, the answer was lost."""
    noise = [{"event": "player_updated"} for _ in range(50)]
    dispatcher, socket = _dispatcher([*noise, {"message_id": "1", "result": "arrived late"}])

    answer = await dispatcher.request(
        lambda mid: socket.send({"command": "player_queues/shuffle", "message_id": mid}),
        message_id="1",
        timeout=5.0,
        pump=True,
    )

    assert answer["result"] == "arrived late"


# ── when the answer does not come ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_silent_server_times_out_rather_than_waiting_forever():
    dispatcher, socket = _dispatcher([])

    with pytest.raises(asyncio.TimeoutError):
        await dispatcher.request(
            lambda mid: socket.send({"message_id": mid}),
            message_id="1",
            timeout=0.05,
            pump=True,
        )


@pytest.mark.asyncio
async def test_a_timed_out_caller_stops_being_waited_on():
    """Otherwise a late answer resolves a future nobody holds, and leaks."""
    dispatcher, socket = _dispatcher([])

    with pytest.raises(asyncio.TimeoutError):
        await dispatcher.request(lambda mid: socket.send({"message_id": mid}), message_id="1", timeout=0.05, pump=True)

    assert dispatcher.pending == 0


@pytest.mark.asyncio
async def test_a_dropped_connection_fails_everyone_waiting():
    """A caller must not hang on a socket that is gone."""
    dispatcher, _socket = _dispatcher([])
    waiting = dispatcher.expect("1")

    dispatcher.fail_all(ConnectionError("socket closed"))

    with pytest.raises(ConnectionError):
        await waiting
    assert dispatcher.pending == 0
