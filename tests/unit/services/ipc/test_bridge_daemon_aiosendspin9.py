from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosendspin.audio.codecs import create_decoder
from aiosendspin.models.types import AudioCodec

from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

if TYPE_CHECKING:
    from pathlib import Path


def _daemon(tmp_path: Path) -> BridgeDaemon:
    args = DaemonArgs(
        client_id="legacy-id",
        client_name="Speaker",
        identity_path=str(tmp_path / "identity.key"),
    )
    daemon = BridgeDaemon(args, {}, None)
    daemon._player = MagicMock()
    return daemon


@pytest.mark.asyncio
async def test_inbound_daemon_creates_one_client_after_identity_load(tmp_path: Path):
    daemon = _daemon(tmp_path)
    order: list[str] = []
    client = SimpleNamespace(disconnect=AsyncMock())

    async def load():
        order.append("load")
        daemon._identity = SimpleNamespace(peer_id="identity-peer-id")

    async def inbound():
        order.append("listen")
        raise asyncio.CancelledError

    daemon._load_identity_and_pairing_store = load  # type: ignore[method-assign]
    daemon._create_client = MagicMock(side_effect=lambda *_: order.append("client") or client)  # type: ignore[method-assign]
    daemon._run_server_initiated = inbound  # type: ignore[method-assign]

    await daemon.run()

    assert order[:3] == ["load", "client", "listen"]
    daemon._create_client.assert_called_once()


@pytest.mark.asyncio
async def test_attach_websocket_owns_lifecycle_and_client_is_reused(tmp_path: Path):
    daemon = _daemon(tmp_path)
    attached: list[object] = []

    class Client:
        connected = False

        async def attach_websocket(self, ws):
            attached.append(ws)

        async def disconnect(self):
            return None

    daemon._client = Client()
    daemon._connection_lock = asyncio.Lock()
    first, second = object(), object()

    await asyncio.gather(daemon._handle_server_connection(first), daemon._handle_server_connection(second))

    assert attached == [first, second]
    assert daemon._client is not None


def test_listener_advertises_identity_peer_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    daemon = _daemon(tmp_path)
    daemon._identity = SimpleNamespace(peer_id="identity-peer-id")
    daemon._client = SimpleNamespace()
    captured: dict[str, object] = {}
    started = asyncio.Event()

    class Listener:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def start(self):
            started.set()

    import aiosendspin.client

    monkeypatch.setattr(aiosendspin.client, "ClientListener", Listener)

    async def exercise():
        task = asyncio.create_task(daemon._run_server_initiated())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert captured["client_id"] == "identity-peer-id"


def _audio_format(codec: AudioCodec, sample_rate: int = 48000):
    return SimpleNamespace(
        codec=codec,
        codec_header=None,
        pcm_format=SimpleNamespace(sample_rate=sample_rate, bit_depth=16, channels=2),
    )


def test_pcm_chunk_is_submitted_unchanged(tmp_path: Path):
    daemon = _daemon(tmp_path)
    payload = b"\x01\x02\x03\x04"

    daemon._on_audio_chunk(100, payload, _audio_format(AudioCodec.PCM))

    daemon._player.submit.assert_called_once_with(100, payload)


def test_format_change_rebuilds_decoder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    daemon = _daemon(tmp_path)
    decoders = [
        MagicMock(decode=MagicMock(return_value=b"pcm-48")),
        MagicMock(decode=MagicMock(return_value=b"pcm-44")),
    ]
    created: list[int] = []

    def factory(codec, **kwargs):
        assert codec == "flac"
        created.append(kwargs["sample_rate"])
        return decoders[len(created) - 1]

    monkeypatch.setattr("aiosendspin.audio.codecs.create_decoder", factory)

    daemon._on_audio_chunk(100, b"flac-48", _audio_format(AudioCodec.FLAC, 48000))
    daemon._on_audio_chunk(200, b"flac-44", _audio_format(AudioCodec.FLAC, 44100))

    assert created == [48000, 44100]
    assert [call.args[1] for call in daemon._player.submit.call_args_list] == [b"pcm-48", b"pcm-44"]


def test_failed_flac_decoder_never_submits_encoded_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    daemon = _daemon(tmp_path)
    attempts = 0

    def fail(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("decoder unavailable")

    monkeypatch.setattr("aiosendspin.audio.codecs.create_decoder", fail)
    audio_format = _audio_format(AudioCodec.FLAC)

    daemon._on_audio_chunk(100, b"encoded-one", audio_format)
    daemon._on_audio_chunk(200, b"encoded-two", audio_format)

    assert attempts == 1
    daemon._player.submit.assert_not_called()


def test_stream_end_stops_output_and_discards_buffered_audio(tmp_path: Path):
    """#464: the player role defines no completion behaviour, so on stream/end
    the client MUST stop output and clear its buffers (Sendspin spec,
    ``stream/end``). Playing out the queue made Pause/Stop lag 5-30 s. The
    pipeline is torn down rather than flushed: flush events deadlocked the
    daemon on some A2DP sinks."""
    daemon = _daemon(tmp_path)
    daemon._decoder = MagicMock(flush=MagicMock(return_value=b"trailing-pcm"))
    daemon._decoder_format = ("flac", 48000, 16, 2, None)
    daemon._next_play_time_us = 1234

    daemon._on_stream_end(None)

    daemon._player.stop.assert_called_once_with()
    daemon._player.submit.assert_not_called()
    daemon._player.close_stream.assert_not_called()
    daemon._player.clear.assert_not_called()
    assert daemon._decoder is None


def test_buffered_decoder_output_keeps_first_packet_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    daemon = _daemon(tmp_path)
    decoder = MagicMock()
    decoder.decode.side_effect = [b"", b"decoded-pcm"]
    monkeypatch.setattr("aiosendspin.audio.codecs.create_decoder", lambda *_args, **_kwargs: decoder)
    audio_format = _audio_format(AudioCodec.FLAC)

    daemon._on_audio_chunk(100, b"first-frame", audio_format)
    daemon._on_audio_chunk(200, b"second-frame", audio_format)

    daemon._player.submit.assert_called_once_with(100, b"decoded-pcm")


def test_real_aiosendspin_pcm_decoder_is_passthrough():
    decoder = create_decoder("pcm", sample_rate=48000, bit_depth=16, channels=2, codec_header=None)
    payload = b"\x00\x01\x02\x03"
    assert decoder.decode(payload) == payload


def test_real_aiosendspin_flac_decoder_returns_pcm():
    av = pytest.importorskip("av")
    encoder = av.CodecContext.create("flac", "w")
    encoder.sample_rate = 48000
    encoder.layout = "stereo"
    encoder.format = "s16"
    encoder.open()
    frame = av.AudioFrame(format="s16", layout="stereo", samples=96)
    frame.sample_rate = 48000
    frame.planes[0].update(bytes(96 * 2 * 2))
    packets = encoder.encode(frame) + encoder.encode(None)
    decoder = create_decoder(
        "flac",
        sample_rate=48000,
        bit_depth=16,
        channels=2,
        codec_header=encoder.extradata,
    )

    decoded = b"".join(decoder.decode(bytes(packet)) for packet in packets)

    assert decoded == bytes(96 * 2 * 2)


@pytest.mark.asyncio
async def test_pairing_window_status_follows_sdk_window(tmp_path: Path):
    daemon = _daemon(tmp_path)

    class PairingClient:
        pairing_window_open = False

        def open_pairing_window(self):
            self.pairing_window_open = True

    client = PairingClient()
    daemon._client = client

    daemon.open_pairing_window()
    assert daemon._bridge_status["pairing_window_open"] is True

    client.pairing_window_open = False
    await asyncio.wait_for(daemon._pairing_window_task, timeout=1)

    assert daemon._bridge_status["pairing_window_open"] is False


@pytest.mark.asyncio
async def test_consumed_pairing_window_does_not_erase_displayed_pin(tmp_path: Path):
    daemon = _daemon(tmp_path)
    daemon._bridge_status.update({"pairing_pin": "123456", "pairing_window_open": True})
    client = SimpleNamespace(pairing_window_open=False)

    await daemon._watch_pairing_window(client)

    assert daemon._bridge_status["pairing_window_open"] is False
    assert daemon._bridge_status["pairing_pin"] == "123456"


@pytest.mark.asyncio
async def test_pairing_pin_is_not_written_to_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    daemon = _daemon(tmp_path)
    daemon._args = replace(daemon._args, require_pairing=True)
    support = daemon._pairing_support()
    assert support is not None

    with caplog.at_level(logging.DEBUG):
        await support.pairing_code_display("123456", grouped="123 456")

    assert daemon._bridge_status["pairing_pin"] == "123 456"
    assert daemon._bridge_status["pairing_state"] == "pin_displayed"
    assert "123456" not in caplog.text
    assert "123 456" not in caplog.text


@pytest.mark.asyncio
async def test_pairing_state_reflects_persisted_server_record(tmp_path: Path):
    daemon = _daemon(tmp_path)
    daemon._args = replace(daemon._args, require_pairing=True)
    daemon._pairing_store = SimpleNamespace(
        list_records=AsyncMock(return_value=[SimpleNamespace(server_id="music-assistant")])
    )

    await daemon._refresh_pairing_state()

    assert daemon._bridge_status["pairing_state"] == "paired"


@pytest.mark.asyncio
async def test_daemon_stop_says_goodbye_with_restart(tmp_path: Path):
    """The parent stops the daemon on link drops, standby, config changes and
    bridge restarts, all of which bring it back. ``shutdown`` tells the server
    not to auto-reconnect (Sendspin spec, ``client/goodbye``), so Music
    Assistant never dialled the respawned daemon again; ``restart`` is the
    reason that matches."""
    from aiosendspin.models.types import GoodbyeReason

    daemon = _daemon(tmp_path)
    client = SimpleNamespace(disconnect=AsyncMock())

    async def load():
        daemon._identity = SimpleNamespace(peer_id="identity-peer-id")

    async def inbound():
        raise asyncio.CancelledError

    daemon._load_identity_and_pairing_store = load  # type: ignore[method-assign]
    daemon._create_client = MagicMock(return_value=client)  # type: ignore[method-assign]
    daemon._run_server_initiated = inbound  # type: ignore[method-assign]

    await daemon.run()

    client.disconnect.assert_awaited_once_with(GoodbyeReason.RESTART)


@pytest.mark.asyncio
async def test_daemon_publishes_the_client_id_it_says_hello_with(tmp_path: Path):
    """Music Assistant keys the player by the identity key in the hello, not
    the bridge's player id; the parent needs it to address that player."""
    daemon = _daemon(tmp_path)
    client = SimpleNamespace(disconnect=AsyncMock())

    async def inbound():
        raise asyncio.CancelledError

    daemon._create_client = MagicMock(return_value=client)  # type: ignore[method-assign]
    daemon._run_server_initiated = inbound  # type: ignore[method-assign]

    await daemon.run()

    assert daemon._identity is not None
    assert daemon._bridge_status["sendspin_client_id"] == daemon._identity.peer_id


# ---------------------------------------------------------------------------
# aiosendspin 10 / Sendspin 1.0.0-rc1 wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_carries_the_output_delay_and_declares_commands_in_state(tmp_path: Path):
    """The spec renamed static delay to output delay and moved player
    commands from client/hello to client/state; MA flags the old wire as
    non-compliant. A constructor keyword the library no longer takes is
    dropped silently, so the delay itself must be checked on the client."""
    from aiosendspin.models.types import PlayerCommand

    daemon = _daemon(tmp_path)
    await daemon._load_identity_and_pairing_store()

    client = daemon._create_client(300.0)

    assert client.output_delay_ms == 300.0
    assert set(client.state_supported_commands) == {
        PlayerCommand.VOLUME,
        PlayerCommand.MUTE,
        PlayerCommand.SET_OUTPUT_DELAY,
    }
    assert client.player_support.supported_commands is None


@pytest.mark.asyncio
async def test_require_pairing_policy_applies_to_the_current_pairing_config(tmp_path: Path):
    """The pairing config fields were renamed (PIN -> pairing code) and the
    pairing-PSK switch removed; setting the old names fails the daemon."""
    daemon = _daemon(tmp_path)
    daemon._args = replace(daemon._args, require_pairing=True, pairing_store_path=str(tmp_path / "pairing.json"))
    await daemon._load_identity_and_pairing_store()

    config = await daemon._pairing_store.get_pairing_config()

    assert config.unpaired_access_enabled is False
    assert config.dynamic_pairing_code_enabled is True
    assert config.static_pairing_code_enabled is False


@pytest.mark.asyncio
async def test_pairing_code_is_shown_and_cleared(tmp_path: Path):
    daemon = _daemon(tmp_path)
    daemon._args = replace(daemon._args, require_pairing=True)
    support = daemon._pairing_support()

    await support.pairing_code_display("123456", grouped="123 456")
    assert daemon._bridge_status["pairing_pin"] == "123 456"
    assert daemon._bridge_status["pairing_state"] == "pin_displayed"

    await support.pairing_code_display(None, grouped=None)
    assert daemon._bridge_status["pairing_pin"] is None


def test_server_output_delay_command_is_mirrored_into_status(tmp_path: Path):
    from aiosendspin.models.player import PlayerCommandPayload
    from aiosendspin.models.types import PlayerCommand

    daemon = _daemon(tmp_path)
    daemon._client = SimpleNamespace(output_delay_ms=750.0)

    daemon._handle_server_command(
        SimpleNamespace(player=PlayerCommandPayload(command=PlayerCommand.SET_OUTPUT_DELAY, output_delay_ms=750))
    )

    assert daemon._bridge_status["static_delay_ms"] == 750


def test_audio_chunks_and_cleared_state_are_accepted(tmp_path: Path):
    """Chunks now carry the server's send-ahead, and metadata/controller
    listeners receive None when a role's state is cleared."""
    daemon = _daemon(tmp_path)

    daemon._on_audio_chunk(100, b"\x00\x00\x00\x00", _audio_format(AudioCodec.PCM), 250_000)
    daemon._on_metadata_update(None)
    daemon._on_controller_state(None)
