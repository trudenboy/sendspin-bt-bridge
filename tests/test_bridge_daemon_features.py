"""Tests for BridgeDaemon artwork and visualizer callback methods."""

from __future__ import annotations

import base64
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# Ensure aiosendspin/sendspin stubs are available for import
_MOCK_MODULES = [
    "aiosendspin",
    "aiosendspin.client",
    "aiosendspin.models",
    "aiosendspin.models.core",
    "aiosendspin.models.player",
    "aiosendspin.models.types",
    "aiosendspin.models.artwork",
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
_types_mod.PlayerCommand = type("PlayerCommand", (), {"VOLUME": "volume", "MUTE": "mute"})  # type: ignore[attr-defined]
_types_mod.UndefinedField = type("UndefinedField", (), {})  # type: ignore[attr-defined]
_types_mod.BinaryMessageType = type(  # type: ignore[attr-defined]
    "BinaryMessageType",
    (),
    {
        "ARTWORK_CHANNEL_0": SimpleNamespace(value=8),
        "ARTWORK_CHANNEL_1": SimpleNamespace(value=9),
        "ARTWORK_CHANNEL_2": SimpleNamespace(value=10),
        "ARTWORK_CHANNEL_3": SimpleNamespace(value=11),
        "AUDIO_CHUNK": SimpleNamespace(value=4),
        "VISUALIZATION_DATA": SimpleNamespace(value=16),
    },
)

_daemon_mod = sys.modules["sendspin.daemon.daemon"]
_daemon_mod.DaemonArgs = MagicMock  # type: ignore[attr-defined]
_daemon_mod.SendspinDaemon = type("SendspinDaemon", (), {"__init__": lambda self, args: None})  # type: ignore[attr-defined]

sys.modules.pop("services.bridge_daemon", None)

from services.bridge_daemon import BridgeDaemon  # noqa: E402


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
    daemon._on_volume_save = None
    daemon._on_status_change = lambda: notified.append(True)
    daemon._background_tasks = set()
    daemon._client = None
    daemon._listener = None
    daemon._audio_handler = None
    daemon._settings = mock_args.settings
    daemon._mpris = None
    daemon._static_delay_ms = 0.0
    daemon._connection_lock = None
    daemon._server_url = None

    daemon._notified = notified
    return daemon


class TestArtworkCallback:
    def test_artwork_frame_stores_b64_in_status(self):
        daemon = _make_bridge_daemon()
        image_data = b"\x89PNG\r\n\x1a\nfake_image_data"
        daemon._on_artwork_frame(0, image_data)

        assert daemon._bridge_status["artwork_b64"] == base64.b64encode(image_data).decode("ascii")
        assert daemon._bridge_status["artwork_channel"] == 0
        assert len(daemon._notified) == 1

    def test_artwork_frame_different_channels(self):
        daemon = _make_bridge_daemon()
        daemon._on_artwork_frame(1, b"ch1")
        assert daemon._bridge_status["artwork_channel"] == 1
        daemon._on_artwork_frame(3, b"ch3")
        assert daemon._bridge_status["artwork_channel"] == 3


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


class TestArtworkMonkeyPatch:
    def test_patch_artwork_handler_forwards_artwork_bytes(self):
        daemon = _make_bridge_daemon()

        # Create a mock client with _handle_binary_message
        client = MagicMock()
        original_called = []
        client._handle_binary_message = lambda payload: original_called.append(payload)

        daemon._patch_artwork_handler(client)

        # Artwork channel 0 = type byte 8
        artwork_payload = bytes([8]) + b"\x00" * 8 + b"JPEG_DATA"
        client._handle_binary_message(artwork_payload)

        # Should NOT call original handler
        assert len(original_called) == 0
        # Should have stored artwork
        assert daemon._bridge_status.get("artwork_b64") is not None
        assert daemon._bridge_status["artwork_channel"] == 0

    def test_patch_artwork_handler_passes_audio_through(self):
        daemon = _make_bridge_daemon()

        client = MagicMock()
        original_called = []
        client._handle_binary_message = lambda payload: original_called.append(payload)

        daemon._patch_artwork_handler(client)

        # Audio chunk = type byte 4
        audio_payload = bytes([4]) + b"\x00" * 8 + b"AUDIO_DATA"
        client._handle_binary_message(audio_payload)

        assert len(original_called) == 1
        assert "artwork_b64" not in daemon._bridge_status


class TestUpstreamVolumeController:
    def test_has_upstream_volume_controller_false_by_default(self):
        daemon = _make_bridge_daemon()
        assert daemon._has_upstream_volume_controller() is False

    def test_has_upstream_volume_controller_true_with_external(self):
        daemon = _make_bridge_daemon()
        handler = MagicMock()
        handler.uses_external_volume_controller = True
        daemon._audio_handler = handler
        assert daemon._has_upstream_volume_controller() is True


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
