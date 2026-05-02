"""Tests for BridgeDaemon visualizer and metadata callback methods."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure aiosendspin/sendspin stubs are available for import
_MOCK_MODULES = [
    "aiosendspin",
    "aiosendspin.client",
    "aiosendspin.models",
    "aiosendspin.models.core",
    "aiosendspin.models.player",
    "aiosendspin.models.types",
    "aiosendspin.models.visualizer",
    "sendspin",
    "sendspin.audio",
    "sendspin.audio_devices",
    "sendspin.audio_connector",
    "sendspin.daemon",
    "sendspin.daemon.daemon",
    "sendspin.hooks",
    "sendspin.settings",
    "sendspin.utils",
    "sendspin.decoder",
    "sendspin.volume_controller",
    "aiosendspin_mpris",
    "pulsectl_asyncio",
]

_stubs: dict[str, MagicMock] = {}
for _mod in _MOCK_MODULES:
    if _mod not in sys.modules:
        _stubs[_mod] = MagicMock()
        sys.modules[_mod] = _stubs[_mod]

# Provide minimal type stubs so bridge_daemon.py can import
_types_mod = sys.modules["aiosendspin.models.types"]
_types_mod.PlayerCommand = type(  # type: ignore[attr-defined]
    "PlayerCommand", (), {"VOLUME": "volume", "MUTE": "mute", "SET_STATIC_DELAY": "set_static_delay"}
)
_types_mod.UndefinedField = type("UndefinedField", (), {})  # type: ignore[attr-defined]

_daemon_mod = sys.modules["sendspin.daemon.daemon"]
_daemon_mod.DaemonArgs = MagicMock  # type: ignore[attr-defined]
_daemon_mod.SendspinDaemon = type(  # type: ignore[attr-defined]
    "SendspinDaemon",
    (),
    {
        "__init__": lambda self, args: None,
        # No-op super for handlers BridgeDaemon overrides.
        "_handle_server_command": lambda self, payload: None,
    },
)


# Provide a real-ish ClientListener stub so BridgeDaemon._run_server_initiated can subclass it
class _StubClientListener:
    """Minimal stub matching aiosendspin.client.listener.ClientListener."""

    def __init__(self, *, client_id, on_connection, port=8928, client_name=None, **kw):
        self._client_id = client_id
        self._on_connection = on_connection
        self._port = port
        self._client_name = client_name

    async def start(self):
        pass

    async def stop(self):
        pass

    async def _handle_websocket(self, request):
        raise NotImplementedError


_client_mod = sys.modules["aiosendspin.client"]
_client_mod.ClientListener = _StubClientListener  # type: ignore[attr-defined]

sys.modules.pop("sendspin_bridge.services.ipc.bridge_daemon", None)

from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon  # noqa: E402


def _make_bridge_daemon(status: dict | None = None) -> BridgeDaemon:
    """Create a BridgeDaemon with minimal mocked DaemonArgs for testing callbacks."""
    mock_args = MagicMock()
    mock_args.client_id = "test-client"
    mock_args.client_name = "Test"
    mock_args.audio_device = MagicMock()
    mock_args.audio_device.index = 0
    mock_args.settings = MagicMock()
    mock_args.preferred_format = None
    mock_args.use_mpris = False
    mock_args.url = None
    mock_args.listen_port = 8928
    mock_args.static_delay_ms = None

    if status is None:
        status = {}

    notified = []

    daemon = object.__new__(BridgeDaemon)
    daemon._args = mock_args
    daemon._bridge_status = status
    daemon._bluetooth_sink_name = "bluez_sink.AA_BB.a2dp_sink"
    daemon._on_status_change = lambda: notified.append(True)
    daemon._client = None
    daemon._listener = None  # type: ignore[assignment]
    daemon._audio_handler = None
    daemon._settings = mock_args.settings
    daemon._mpris = None
    daemon._static_delay_ms = 0.0
    daemon._connection_lock = None  # type: ignore[assignment]
    daemon._server_url = None

    daemon._notified = notified
    return daemon


class TestVisualizerCallback:
    def test_visualizer_loudness_stored(self):
        daemon = _make_bridge_daemon()
        frames = [SimpleNamespace(loudness=4200, f_peak=None, spectrum=None)]
        daemon._on_visualizer_frames(frames)

        assert daemon._bridge_status["visualizer"]["loudness"] == 4200
        assert "f_peak" not in daemon._bridge_status["visualizer"]
        # Visualizer does NOT trigger notify (too frequent, would cause SSE storms)
        assert len(daemon._notified) == 0

    def test_visualizer_with_spectrum(self):
        daemon = _make_bridge_daemon()
        frames = [SimpleNamespace(loudness=100, f_peak=440, spectrum=[10, 20, 30])]
        daemon._on_visualizer_frames(frames)

        viz = daemon._bridge_status["visualizer"]
        assert viz["loudness"] == 100
        assert viz["f_peak"] == 440
        assert viz["spectrum"] == [10, 20, 30]

    def test_visualizer_uses_latest_frame(self):
        daemon = _make_bridge_daemon()
        frames = [
            SimpleNamespace(loudness=100, f_peak=None, spectrum=None),
            SimpleNamespace(loudness=200, f_peak=None, spectrum=None),
        ]
        daemon._on_visualizer_frames(frames)
        assert daemon._bridge_status["visualizer"]["loudness"] == 200

    def test_visualizer_empty_frames_noop(self):
        daemon = _make_bridge_daemon()
        daemon._on_visualizer_frames([])
        assert "visualizer" not in daemon._bridge_status
        assert len(daemon._notified) == 0


class TestConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_handle_server_connection_keeps_status_true_after_replacing_previous_client(self):
        status = {
            "server_connected": True,
            "connected": True,
            "group_id": "old-group",
            "group_name": "Old Group",
            "server_port": 9000,
        }
        daemon = _make_bridge_daemon(status)
        daemon._connection_lock = asyncio.Lock()
        daemon._audio_handler = MagicMock()
        daemon._handle_disconnect = AsyncMock()

        class OldClient:
            connected = True

            async def _send_message(self, _payload):
                return None

            async def disconnect(self):
                daemon._on_server_disconnect()

        class NewClient:
            connected = True

            def add_server_command_listener(self, _listener):
                return None

            async def attach_websocket(self, _ws):
                return None

            def add_disconnect_listener(self, callback):
                callback()
                return lambda: None

        daemon._client = OldClient()
        daemon._create_client = MagicMock(return_value=NewClient())
        ws = SimpleNamespace(_req=SimpleNamespace(remote="192.168.10.10"))

        await daemon._handle_server_connection(ws)

        assert daemon._bridge_status["server_connected"] is True
        assert daemon._bridge_status["connected"] is True
        assert daemon._bridge_status["group_id"] is None
        assert daemon._bridge_status["group_name"] is None
        assert daemon._bridge_status["connected_server_url"] == "192.168.10.10:9000"
        assert daemon._bridge_status["server_connected_at"]


class TestClientHelloRoles:
    def test_create_client_advertises_only_supported_roles(self, monkeypatch):
        # Regression: bridge advertises PLAYER/METADATA/CONTROLLER but NOT
        # the draft VISUALIZER role (current MA releases reject it during
        # ClientHello parsing) and NOT the ARTWORK role (binary-frame relay
        # was dropped in 2.62.0-rc.9 — UI sources artwork from MA's image_url
        # via /api/ma/artwork instead).
        import sendspin_bridge.services.diagnostics.sendspin_compat as compat_mod

        daemon = _make_bridge_daemon()
        daemon._audio_handler = SimpleNamespace(volume=25, muted=False)

        _types_mod.Roles = type(
            "Roles",
            (),
            {
                "PLAYER": "player-role",
                "METADATA": "metadata-role",
                "CONTROLLER": "controller-role",
                "ARTWORK": "artwork-role",
                "VISUALIZER": "visualizer-role",
            },
        )

        player_mod = sys.modules["aiosendspin.models.player"]
        player_mod.ClientHelloPlayerSupport = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]

        captured_kwargs: dict[str, object] = {}

        class FakeSendspinClient:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            def add_group_update_listener(self, _listener):
                return None

            def add_metadata_listener(self, _listener):
                return None

            def add_controller_state_listener(self, _listener):
                return None

            def add_disconnect_listener(self, _listener):
                return None

        client_mod = sys.modules["aiosendspin.client"]
        client_mod.SendspinClient = FakeSendspinClient  # type: ignore[attr-defined]

        monkeypatch.setattr(
            compat_mod,
            "detect_supported_audio_formats_for_device",
            lambda _audio_device: [SimpleNamespace(codec="flac", channels=2, sample_rate=44100, bit_depth=16)],
        )
        monkeypatch.setattr(compat_mod, "filter_supported_call_kwargs", lambda _callable, kwargs: dict(kwargs))

        daemon._create_client()

        assert captured_kwargs["roles"] == ["player-role", "metadata-role", "controller-role"]
        assert "artwork_support" not in captured_kwargs
        assert "visualizer_support" not in captured_kwargs
        # Issue #237: bridge advertises SET_STATIC_DELAY via state_supported_commands
        # so MA exposes the per-player static delay slider.
        PlayerCommand = sys.modules["aiosendspin.models.types"].PlayerCommand
        assert captured_kwargs["state_supported_commands"] == [PlayerCommand.SET_STATIC_DELAY]


class TestServerCommandStaticDelay:
    """Tests for inbound SET_STATIC_DELAY handling (issue #237)."""

    def test_set_static_delay_mirrors_post_clamp_value_into_status(self):
        daemon = _make_bridge_daemon()
        # Simulate aiosendspin client that already auto-applied + clamped the
        # inbound value (e.g. MA pushed 6000 → client clamped to 5000).
        daemon._client = SimpleNamespace(static_delay_ms=5000)
        PlayerCommand = sys.modules["aiosendspin.models.types"].PlayerCommand
        cmd = SimpleNamespace(
            command=PlayerCommand.SET_STATIC_DELAY,
            volume=None,
            mute=None,
            static_delay_ms=6000,  # raw inbound, pre-clamp
        )
        payload = SimpleNamespace(player=cmd)

        daemon._handle_server_command(payload)

        # Mirror the post-clamp value, NOT the raw inbound.
        assert daemon._bridge_status["static_delay_ms"] == 5000
        assert len(daemon._notified) == 1

    def test_set_static_delay_falls_back_to_clamped_cmd_when_client_missing(self):
        daemon = _make_bridge_daemon()
        daemon._client = None  # subprocess startup race — client not yet attached
        PlayerCommand = sys.modules["aiosendspin.models.types"].PlayerCommand
        cmd = SimpleNamespace(
            command=PlayerCommand.SET_STATIC_DELAY,
            volume=None,
            mute=None,
            static_delay_ms=750,
        )
        payload = SimpleNamespace(player=cmd)

        daemon._handle_server_command(payload)

        assert daemon._bridge_status["static_delay_ms"] == 750
        assert len(daemon._notified) == 1

    def test_set_static_delay_fallback_clamps_out_of_range_inbound(self):
        daemon = _make_bridge_daemon()
        daemon._client = None
        PlayerCommand = sys.modules["aiosendspin.models.types"].PlayerCommand
        cmd = SimpleNamespace(
            command=PlayerCommand.SET_STATIC_DELAY,
            volume=None,
            mute=None,
            static_delay_ms=-10,
        )
        daemon._handle_server_command(SimpleNamespace(player=cmd))
        assert daemon._bridge_status["static_delay_ms"] == 0

        cmd2 = SimpleNamespace(
            command=PlayerCommand.SET_STATIC_DELAY,
            volume=None,
            mute=None,
            static_delay_ms=99999,
        )
        daemon._handle_server_command(SimpleNamespace(player=cmd2))
        assert daemon._bridge_status["static_delay_ms"] == 5000

    def test_set_static_delay_with_none_value_is_noop(self):
        daemon = _make_bridge_daemon()
        daemon._client = SimpleNamespace(static_delay_ms=300)
        PlayerCommand = sys.modules["aiosendspin.models.types"].PlayerCommand
        cmd = SimpleNamespace(
            command=PlayerCommand.SET_STATIC_DELAY,
            volume=None,
            mute=None,
            static_delay_ms=None,
        )
        daemon._handle_server_command(SimpleNamespace(player=cmd))
        assert "static_delay_ms" not in daemon._bridge_status
        assert len(daemon._notified) == 0


class TestExtendedMetadata:
    """Tests for extended metadata forwarding (album, shuffle, repeat, etc.)."""

    def _make_metadata(self, **kwargs):
        """Create a metadata object with UndefinedField defaults."""
        UndefinedField = sys.modules["aiosendspin.models.types"].UndefinedField
        defaults = {
            "title": UndefinedField(),
            "artist": UndefinedField(),
            "album": UndefinedField(),
            "album_artist": UndefinedField(),
            "artwork_url": UndefinedField(),
            "year": UndefinedField(),
            "track": UndefinedField(),
            "shuffle": UndefinedField(),
            "repeat": UndefinedField(),
            "progress": None,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _make_payload(self, metadata):
        return SimpleNamespace(metadata=metadata, controller=None)

    def test_album_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(album="Test Album"))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["current_album"] == "Test Album"
        assert len(daemon._notified) == 1

    def test_album_artist_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(album_artist="Various Artists"))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["current_album_artist"] == "Various Artists"

    def test_artwork_url_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(artwork_url="https://example.com/art.jpg"))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["artwork_url"] == "https://example.com/art.jpg"

    def test_year_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(year=2024))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["track_year"] == 2024

    def test_track_number_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(track=5))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["track_number"] == 5

    def test_shuffle_true_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(shuffle=True))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["shuffle"] is True

    def test_shuffle_false_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(shuffle=False))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["shuffle"] is False

    def test_shuffle_none_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(shuffle=None))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["shuffle"] is None

    def test_repeat_mode_enum_forwarded(self):
        daemon = _make_bridge_daemon()
        repeat_enum = SimpleNamespace(value="one")
        payload = self._make_payload(self._make_metadata(repeat=repeat_enum))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["repeat_mode"] == "one"

    def test_repeat_mode_none_forwarded(self):
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata(repeat=None))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["repeat_mode"] is None

    def test_undefined_fields_not_written(self):
        """Fields left as UndefinedField should not appear in status."""
        daemon = _make_bridge_daemon()
        payload = self._make_payload(self._make_metadata())  # all UndefinedField
        daemon._on_metadata_update(payload)
        for key in (
            "current_album",
            "current_album_artist",
            "artwork_url",
            "track_year",
            "track_number",
            "shuffle",
            "repeat_mode",
        ):
            assert key not in daemon._bridge_status
        assert len(daemon._notified) == 0

    def test_all_metadata_fields_together(self):
        daemon = _make_bridge_daemon()
        repeat_enum = SimpleNamespace(value="all")
        payload = self._make_payload(
            self._make_metadata(
                title="Song",
                artist="Artist",
                album="Album",
                album_artist="AA",
                artwork_url="http://art",
                year=2023,
                track=3,
                shuffle=True,
                repeat=repeat_enum,
            )
        )
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["current_track"] == "Song"
        assert daemon._bridge_status["current_artist"] == "Artist"
        assert daemon._bridge_status["current_album"] == "Album"
        assert daemon._bridge_status["current_album_artist"] == "AA"
        assert daemon._bridge_status["artwork_url"] == "http://art"
        assert daemon._bridge_status["track_year"] == 2023
        assert daemon._bridge_status["track_number"] == 3
        assert daemon._bridge_status["shuffle"] is True
        assert daemon._bridge_status["repeat_mode"] == "all"
        assert len(daemon._notified) == 1

    def test_playback_speed_forwarded(self):
        daemon = _make_bridge_daemon()
        progress = SimpleNamespace(track_progress=5000, track_duration=180000, playback_speed=0)
        payload = self._make_payload(self._make_metadata(progress=progress))
        daemon._on_metadata_update(payload)
        assert daemon._bridge_status["playback_speed"] == 0
        assert daemon._bridge_status["track_progress_ms"] == 5000


class TestControllerState:
    """Tests for controller state listener."""

    def test_controller_state_updates_supported_commands(self):
        daemon = _make_bridge_daemon()
        mc_play = SimpleNamespace(value="play")
        mc_pause = SimpleNamespace(value="pause")
        mc_next = SimpleNamespace(value="next")
        controller = SimpleNamespace(
            supported_commands=[mc_play, mc_pause, mc_next],
            volume=75,
            muted=False,
        )
        payload = SimpleNamespace(metadata=None, controller=controller)
        daemon._on_controller_state(payload)
        assert daemon._bridge_status["supported_commands"] == ["play", "pause", "next"]
        assert daemon._bridge_status["group_volume"] == 75
        assert daemon._bridge_status["group_muted"] is False
        assert len(daemon._notified) == 1

    def test_controller_state_with_muted(self):
        daemon = _make_bridge_daemon()
        controller = SimpleNamespace(
            supported_commands=[],
            volume=0,
            muted=True,
        )
        payload = SimpleNamespace(metadata=None, controller=controller)
        daemon._on_controller_state(payload)
        assert daemon._bridge_status["group_muted"] is True
        assert daemon._bridge_status["group_volume"] == 0

    def test_controller_state_none_is_noop(self):
        daemon = _make_bridge_daemon()
        payload = SimpleNamespace(metadata=None, controller=None)
        daemon._on_controller_state(payload)
        assert "supported_commands" not in daemon._bridge_status
        assert len(daemon._notified) == 0


class TestHeartbeatListenerOverride:
    """_run_server_initiated() overrides upstream to add WebSocket heartbeat."""

    def test_has_run_server_initiated_override(self):
        """BridgeDaemon defines its own _run_server_initiated (not inherited)."""
        assert "_run_server_initiated" in BridgeDaemon.__dict__

    @pytest.mark.asyncio
    async def test_run_server_initiated_creates_heartbeat_listener(self):
        """Override creates a listener subclass named _HeartbeatListener."""
        daemon = _make_bridge_daemon()
        daemon._connection_lock = None
        daemon._handle_server_connection = AsyncMock()

        started = asyncio.Event()
        _original_start = _StubClientListener.start

        async def _intercept_start(self_listener):
            started.set()

        _StubClientListener.start = _intercept_start
        try:
            task = asyncio.create_task(daemon._run_server_initiated(0.0))
            try:
                await asyncio.wait_for(started.wait(), timeout=5.0)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            _StubClientListener.start = _original_start

        listener = daemon._listener
        assert listener is not None
        assert type(listener).__name__ == "_HeartbeatListener"
        assert isinstance(listener, _StubClientListener)

    @pytest.mark.asyncio
    async def test_heartbeat_listener_passes_heartbeat_30(self):
        """The _HeartbeatListener._handle_websocket creates WS with heartbeat=30."""
        daemon = _make_bridge_daemon()
        daemon._connection_lock = None
        daemon._handle_server_connection = AsyncMock()

        started = asyncio.Event()
        _original_start = _StubClientListener.start

        async def _intercept_start(self_listener):
            started.set()

        _StubClientListener.start = _intercept_start
        try:
            task = asyncio.create_task(daemon._run_server_initiated(0.0))
            try:
                await asyncio.wait_for(started.wait(), timeout=5.0)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            _StubClientListener.start = _original_start

        listener = daemon._listener
        ws_kwargs: list[dict] = []

        # Patch aiohttp.web.WebSocketResponse inside the override's import scope
        import aiohttp.web as _real_web

        class _CapturingWS:
            closed = True

            def __init__(self, **kwargs):
                ws_kwargs.append(kwargs)

            async def prepare(self, request):
                pass

            async def close(self, **kw):
                pass

        mock_request = MagicMock()
        mock_request.remote = "127.0.0.1"

        from unittest.mock import patch as _patch

        with _patch.object(_real_web, "WebSocketResponse", _CapturingWS):
            try:
                await listener._handle_websocket(mock_request)
            except Exception:
                pass

        assert len(ws_kwargs) >= 1
        assert ws_kwargs[0].get("heartbeat") == 30


class TestConnectionWatchdog:
    """Tests for the connection watchdog that surfaces persistent connection failures."""

    @pytest.mark.asyncio
    async def test_watchdog_sets_last_error_when_not_connected(self):
        """After delay, watchdog sets last_error if server_connected is still False."""
        status = {"server_connected": False, "server_url": "ws://192.168.1.10:9000/sendspin"}
        daemon = _make_bridge_daemon(status)
        task = asyncio.create_task(daemon._connection_watchdog(delay=0.05))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert status.get("last_error") is not None
        assert "9000" in status["last_error"]
        assert len(daemon._notified) >= 1

    @pytest.mark.asyncio
    async def test_watchdog_noop_when_already_connected(self):
        """Watchdog does nothing if server_connected is True before delay expires."""
        status = {"server_connected": True, "server_url": "ws://192.168.1.10:9000/sendspin"}
        daemon = _make_bridge_daemon(status)
        task = asyncio.create_task(daemon._connection_watchdog(delay=0.05))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert status.get("last_error") is None

    @pytest.mark.asyncio
    async def test_watchdog_clears_when_connected_later(self):
        """Watchdog clears last_error once server_connected becomes True."""
        status = {"server_connected": False, "server_url": "ws://host:9000/sendspin"}
        daemon = _make_bridge_daemon(status)
        task = asyncio.create_task(daemon._connection_watchdog(delay=0.05, poll_interval=0.02))
        await asyncio.sleep(0.1)
        assert status.get("last_error") is not None
        # Now simulate connection success
        status["server_connected"] = True
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert status.get("last_error") is None
