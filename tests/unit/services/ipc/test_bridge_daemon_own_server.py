"""A player accepts only the bridge's own Music Assistant when it knows which that is."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosendspin.models.types import Activity, GoodbyeReason

from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs
from sendspin_bridge.services.ipc.own_server import OwnServer, peer_host_var

if TYPE_CHECKING:
    from pathlib import Path

OWN = OwnServer(hosts=frozenset({"192.168.10.10"}))


async def _daemon(tmp_path: Path, *, own: OwnServer | None = OWN, only: bool = True) -> BridgeDaemon:
    args = DaemonArgs(
        client_id="legacy-id",
        client_name="Speaker",
        identity_path=str(tmp_path / "identity.key"),
        pairing_store_path=str(tmp_path / "pairing.json"),
    )
    daemon = BridgeDaemon(args, {"server_port": 8927}, None, own_server=own, own_server_only=only)
    daemon._player = MagicMock()
    await daemon._load_identity_and_pairing_store()
    daemon._client = daemon._create_client(0.0)
    return daemon


def _connection(server_id: str, name: str = "MA", activities: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        server_id=server_id,
        server_info=SimpleNamespace(server_id=server_id, name=name),
        activities=activities or [],
        pairing_attempt_in_progress=False,
        is_pairing=False,
        connected=True,
        send_goodbye=AsyncMock(),
        disconnect=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_a_foreign_server_is_refused_with_shutdown(tmp_path: Path, caplog):
    daemon = await _daemon(tmp_path)
    client = daemon._client
    foreign = _connection("srv-foreign", "Test MA")
    peer_host_var.set("192.0.2.229")

    with caplog.at_level(logging.WARNING):
        assert client._should_admit_connection(foreign) is False
        await client._reject_connection(foreign)

    # ``shutdown`` ends that server's retry loop but, unlike ``another_server``,
    # does not make it ignore this player's mDNS adverts for good: the server
    # that owns the bridge after a configuration fix still finds it.
    foreign.send_goodbye.assert_awaited_once_with(GoodbyeReason.SHUTDOWN)
    foreign.disconnect.assert_awaited_once()
    refused = daemon._bridge_status["refused_servers"]
    assert refused[0]["host"] == "192.0.2.229" and refused[0]["name"] == "Test MA"
    assert any("Refused Music Assistant at 192.0.2.229 (Test MA)" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_redial_is_not_logged_again(tmp_path: Path, caplog):
    daemon = await _daemon(tmp_path)
    client = daemon._client
    peer_host_var.set("192.0.2.229")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            conn = _connection("srv-foreign", "Test MA")
            assert client._should_admit_connection(conn) is False
    assert sum("Refused Music Assistant" in r.getMessage() for r in caplog.records) == 1
    assert daemon._bridge_status["refused_servers"][0]["count"] == 3


@pytest.mark.asyncio
async def test_the_own_server_is_admitted(tmp_path: Path):
    daemon = await _daemon(tmp_path)
    peer_host_var.set("192.168.10.10")
    assert daemon._client._should_admit_connection(_connection("srv-own")) is True
    assert "refused_servers" not in daemon._bridge_status or not daemon._bridge_status["refused_servers"]


@pytest.mark.asyncio
async def test_a_foreign_dial_leaves_the_own_connection_alone(tmp_path: Path):
    daemon = await _daemon(tmp_path)
    client = daemon._client
    own_conn = _connection("srv-own")
    client._admitted_connection = own_conn
    peer_host_var.set("192.0.2.229")

    foreign = _connection("srv-foreign")
    assert client._should_admit_connection(foreign) is False
    await client._reject_connection(foreign)

    assert client._admitted_connection is own_conn
    own_conn.disconnect.assert_not_awaited()
    own_conn.send_goodbye.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refusal_does_not_change_how_the_own_server_is_arbitrated(tmp_path: Path):
    """An own server still follows the library's rules (here: equal rank is accepted)."""
    daemon = await _daemon(tmp_path)
    client = daemon._client
    playing = [Activity.PLAYBACK]
    client._admitted_connection = _connection("srv-old", activities=playing)
    peer_host_var.set("192.168.10.10")
    assert client._should_admit_connection(_connection("srv-own", activities=playing)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("own,only", [(None, True), (OWN, False)])
async def test_everything_is_accepted_when_the_own_server_is_unknown_or_the_check_is_off(
    tmp_path: Path, own: OwnServer | None, only: bool
):
    daemon = await _daemon(tmp_path, own=own, only=only)
    peer_host_var.set("192.0.2.229")
    assert daemon._client._should_admit_connection(_connection("srv-foreign")) is True


@pytest.mark.asyncio
async def test_the_connected_server_is_published_with_its_name_and_ownership(tmp_path: Path):
    daemon = await _daemon(tmp_path)
    daemon._client = SimpleNamespace(
        server_info=SimpleNamespace(server_id="srv-own", name="Music Assistant HA"),
    )
    ws = SimpleNamespace(_req=SimpleNamespace(remote="192.168.10.10"))
    daemon._mark_server_connected(ws)
    status = daemon._bridge_status
    assert status["connected_server_name"] == "Music Assistant HA"
    assert status["connected_server_id"] == "srv-own"
    assert status["connected_server_own"] is True


@pytest.mark.asyncio
async def test_an_admitted_foreign_server_is_flagged_not_own(tmp_path: Path):
    daemon = await _daemon(tmp_path, only=False)
    daemon._client = SimpleNamespace(server_info=SimpleNamespace(server_id="srv-x", name="Other"))
    daemon._mark_server_connected(SimpleNamespace(_req=SimpleNamespace(remote="192.0.2.229")))
    # The check is off: nothing is claimed about ownership.
    assert daemon._bridge_status["connected_server_own"] is None
    daemon._server_gate.enforce = True
    daemon._mark_server_connected(SimpleNamespace(_req=SimpleNamespace(remote="192.0.2.229")))
    assert daemon._bridge_status["connected_server_own"] is False


@pytest.mark.asyncio
async def test_the_peer_host_reaches_the_admission_decision(tmp_path: Path):
    daemon = await _daemon(tmp_path)
    seen: list[str | None] = []

    async def attach(ws):
        seen.append(peer_host_var.get())

    daemon._client = SimpleNamespace(attach_websocket=attach, connected=False)
    await daemon._handle_server_connection(SimpleNamespace(_req=SimpleNamespace(remote="192.0.2.229")))
    assert seen == ["192.0.2.229"]
