"""Tests for the native Sendspin transport command API endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routes.api_transport import transport_bp


@pytest.fixture()
def transport_client():
    """Create a Flask test client with the transport blueprint."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(transport_bp)
    return app.test_client()


def _make_mock_client(supported_commands=None, send_result=True, *, player_id=None, player_name=None):
    """Build a mock SendspinClient with transport support."""
    client = MagicMock()
    status = MagicMock()
    status.get = lambda key, default=None: {"supported_commands": supported_commands}.get(key, default)
    client.status = status
    client.send_transport_command = AsyncMock(return_value=send_result)
    client.player_id = player_id or "player-default"
    client.player_name = player_name or "Default Player"
    return client


def _make_registry_snapshot(clients):
    """Build a mock DeviceRegistrySnapshot."""
    snapshot = MagicMock()
    snapshot.active_clients = clients
    return snapshot


class TestTransportEndpoint:
    def test_invalid_action_returns_400(self, transport_client):
        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"action": "invalid_action", "device_index": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "Invalid action" in data["error"]

    def test_missing_action_returns_400(self, transport_client):
        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"device_index": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_device_index_returns_400(self, transport_client):
        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"action": "play", "device_index": "abc"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_device_not_found_returns_404(self, mock_registry, transport_client):
        mock_registry.return_value = _make_registry_snapshot([])
        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"action": "play", "device_index": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    @patch("routes.api_transport.get_main_loop")
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_player_id_lookup_routes_to_correct_client(self, mock_registry, mock_loop, transport_client):
        """Reproduces the VM 105 mis-route: WH at frontend-index 0 but
        backend ``active_clients`` order is reversed, so
        ``device_index=0`` would dispatch to ENEBY's client.  After the
        fix, looking up by ``player_id`` resolves WH directly regardless
        of list position."""
        wh = _make_mock_client(supported_commands=["next"], player_id="wh-uuid", player_name="WH")
        eneby = _make_mock_client(supported_commands=["next"], player_id="eneby-uuid", player_name="ENEBY")
        # Backend order is [ENEBY, WH] — opposite of the frontend's
        # [WH, ENEBY].  Index 0 → ENEBY (the bug).
        mock_registry.return_value = _make_registry_snapshot([eneby, wh])
        mock_loop.return_value = MagicMock()

        import concurrent.futures

        with patch("routes.api_transport.asyncio") as mock_asyncio:
            future = concurrent.futures.Future()
            future.set_result(True)
            mock_asyncio.run_coroutine_threadsafe.return_value = future

            resp = transport_client.post(
                "/api/transport/cmd",
                data=json.dumps({"action": "next", "player_id": "wh-uuid"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        # The coroutine handed to run_coroutine_threadsafe must have
        # been built from WH's client, not ENEBY's.
        wh.send_transport_command.assert_called_once_with("next", value=None)
        eneby.send_transport_command.assert_not_called()

    @patch("routes.api_transport.get_main_loop")
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_player_id_unknown_returns_404(self, mock_registry, mock_loop, transport_client):
        """A stale frontend that sends a player_id no longer present
        (device removed live) gets a clear 404 with the bad id —
        easier than tracking down a generic 500."""
        existing = _make_mock_client(supported_commands=["next"], player_id="exists")
        mock_registry.return_value = _make_registry_snapshot([existing])
        mock_loop.return_value = MagicMock()

        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"action": "next", "player_id": "missing-uuid"}),
            content_type="application/json",
        )

        assert resp.status_code == 404
        assert "missing-uuid" in resp.get_json()["error"]
        existing.send_transport_command.assert_not_called()

    @patch("routes.api_transport.get_main_loop")
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_legacy_device_index_path_still_works_with_warning(self, mock_registry, mock_loop, transport_client):
        """Backward-compat: callers that haven't updated to player_id
        yet must still work — but the backend logs a warning so we
        spot stragglers in production."""
        client = _make_mock_client(supported_commands=["next"], player_id="x")
        mock_registry.return_value = _make_registry_snapshot([client])
        mock_loop.return_value = MagicMock()

        import concurrent.futures

        with patch("routes.api_transport.asyncio") as mock_asyncio:
            future = concurrent.futures.Future()
            future.set_result(True)
            mock_asyncio.run_coroutine_threadsafe.return_value = future

            resp = transport_client.post(
                "/api/transport/cmd",
                data=json.dumps({"action": "next", "device_index": 0}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        client.send_transport_command.assert_called_once_with("next", value=None)

    @patch("routes.api_transport.get_main_loop")
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_unsupported_command_returns_400(self, mock_registry, mock_loop, transport_client):
        client = _make_mock_client(supported_commands=["play", "pause"])
        mock_registry.return_value = _make_registry_snapshot([client])
        mock_loop.return_value = MagicMock()

        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"action": "stop", "device_index": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "not supported" in resp.get_json()["error"]

    @patch("routes.api_transport.get_main_loop")
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_supported_command_returns_success(self, mock_registry, mock_loop, transport_client):
        client = _make_mock_client(supported_commands=["play", "pause", "next"])
        mock_registry.return_value = _make_registry_snapshot([client])

        import concurrent.futures

        # Patch run_coroutine_threadsafe to directly run the coroutine
        with patch("routes.api_transport.asyncio") as mock_asyncio:
            future = concurrent.futures.Future()
            future.set_result(True)
            mock_asyncio.run_coroutine_threadsafe.return_value = future

            resp = transport_client.post(
                "/api/transport/cmd",
                data=json.dumps({"action": "play", "device_index": 0}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    @patch("routes.api_transport.get_main_loop")
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_volume_command_passes_value(self, mock_registry, mock_loop, transport_client):
        client = _make_mock_client(supported_commands=["volume"])
        mock_registry.return_value = _make_registry_snapshot([client])

        import concurrent.futures

        with patch("routes.api_transport.asyncio") as mock_asyncio:
            future = concurrent.futures.Future()
            future.set_result(True)
            mock_asyncio.run_coroutine_threadsafe.return_value = future

            resp = transport_client.post(
                "/api/transport/cmd",
                data=json.dumps({"action": "volume", "device_index": 0, "value": 75}),
                content_type="application/json",
            )

        assert resp.status_code == 200

    @patch("routes.api_transport.get_main_loop")
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_null_supported_commands_allows_any(self, mock_registry, mock_loop, transport_client):
        """When supported_commands is None (not yet received), allow any valid action."""
        client = _make_mock_client(supported_commands=None)
        mock_registry.return_value = _make_registry_snapshot([client])

        import concurrent.futures

        with patch("routes.api_transport.asyncio") as mock_asyncio:
            future = concurrent.futures.Future()
            future.set_result(True)
            mock_asyncio.run_coroutine_threadsafe.return_value = future

            resp = transport_client.post(
                "/api/transport/cmd",
                data=json.dumps({"action": "play", "device_index": 0}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_null_client_returns_503(self, mock_registry, transport_client):
        mock_registry.return_value = _make_registry_snapshot([None])
        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"action": "play", "device_index": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 503


class TestValidActions:
    """Verify all MediaCommand values are in _VALID_ACTIONS."""

    @pytest.mark.parametrize(
        "action",
        [
            "play",
            "pause",
            "stop",
            "next",
            "previous",
            "volume",
            "mute",
            "repeat_off",
            "repeat_one",
            "repeat_all",
            "shuffle",
            "unshuffle",
            "switch",
        ],
    )
    @patch("routes.api_transport.get_device_registry_snapshot")
    def test_all_media_commands_are_valid(self, mock_registry, action, transport_client):
        """Verify the action passes initial validation (may fail later due to missing device)."""
        mock_registry.return_value = _make_registry_snapshot([])
        resp = transport_client.post(
            "/api/transport/cmd",
            data=json.dumps({"action": action, "device_index": 0}),
            content_type="application/json",
        )
        # Should be 404 (no device), NOT 400 (invalid action)
        assert resp.status_code == 404
