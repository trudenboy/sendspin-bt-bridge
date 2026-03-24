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
