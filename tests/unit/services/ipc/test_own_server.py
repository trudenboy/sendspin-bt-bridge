"""Which Music Assistant a player may belong to."""

from __future__ import annotations

import asyncio

from sendspin_bridge.services.ipc.own_server import (
    OwnServer,
    RefusalLog,
    decide,
    resolve_own_server,
)

OWN = OwnServer(hosts=frozenset({"192.168.10.10"}), server_ids=frozenset({"srv-own"}))


def test_the_own_host_is_accepted():
    verdict = decide(OWN, server_id="x", peer_host="192.168.10.10")
    assert verdict.accept and verdict.own is True


def test_the_own_server_id_is_accepted_from_any_host():
    verdict = decide(OWN, server_id="srv-own", peer_host="10.0.0.9")
    assert verdict.accept and verdict.own is True


def test_a_foreign_server_is_refused():
    verdict = decide(OWN, server_id="srv-other", peer_host="192.0.2.229")
    assert not verdict.accept and verdict.own is False


def test_an_unknown_own_server_accepts_everyone():
    verdict = decide(None, server_id="srv-other", peer_host="192.0.2.229")
    assert verdict.accept and verdict.own is None
    assert decide(OwnServer(), server_id="a", peer_host="b").own is None


def test_the_setting_off_accepts_everyone():
    verdict = decide(OWN, server_id="srv-other", peer_host="1.2.3.4", enforce=False)
    assert verdict.accept and verdict.own is None


def test_an_id_learned_from_a_host_match_is_trusted_behind_nat():
    trusted: set[str] = set()
    first = decide(OWN, server_id="srv-real", peer_host="192.168.10.10", trusted_ids=trusted)
    assert first.accept
    trusted.add("srv-real")
    later = decide(OWN, server_id="srv-real", peer_host="172.30.32.1", trusted_ids=trusted)
    assert later.accept and later.own is True


def test_ipv4_mapped_peer_addresses_match():
    assert decide(OWN, server_id="x", peer_host="::ffff:192.168.10.10").accept


def test_host_names_compare_case_insensitively():
    own = OwnServer(hosts=frozenset({"ma.local"}))
    assert decide(own, server_id="x", peer_host="MA.local").accept


def test_refusals_are_logged_once_per_server_per_period():
    now = [1000.0]
    log = RefusalLog(period_s=600, clock=lambda: now[0])
    assert log.note(server_id="s", host="1.1.1.1", name="Test MA") is True
    assert log.note(server_id="s", host="1.1.1.1", name="Test MA") is False
    other = log.note(server_id="t", host="2.2.2.2", name="Other")
    assert other is True
    now[0] += 601
    assert log.note(server_id="s", host="1.1.1.1", name="Test MA") is True
    recent = log.recent()
    assert {r["host"] for r in recent} == {"1.1.1.1", "2.2.2.2"}
    assert next(r for r in recent if r["host"] == "1.1.1.1")["count"] == 3


def test_resolve_own_server_collects_hosts_from_the_configured_sources():
    async def go():
        async def resolver(host):
            return {"ma.lan": ["192.168.10.10"]}.get(host, [])

        return await resolve_own_server(
            sendspin_server="auto",
            ma_api_urls=["http://ma.lan:8095", "", "http://localhost:8095"],
            resolver=resolver,
        )

    own = asyncio.run(go())
    assert own is not None
    assert own.hosts == {"ma.lan", "192.168.10.10", "localhost"}


def test_resolve_own_server_uses_an_explicit_sendspin_host():
    async def resolver(host):
        return []

    own = asyncio.run(resolve_own_server(sendspin_server="10.1.1.1", ma_api_urls=[], resolver=resolver))
    assert own is not None and own.hosts == {"10.1.1.1"}


def test_a_loopback_music_assistant_makes_only_this_machine_its_server():
    # Music Assistant reached over loopback runs on this machine, so a server
    # dialling from another machine is not it.
    async def resolver(host):
        return []

    own = asyncio.run(
        resolve_own_server(sendspin_server="auto", ma_api_urls=["http://localhost:8095"], resolver=resolver)
    )
    assert own is not None
    assert decide(own, server_id="srv", peer_host="127.0.0.1").accept
    assert not decide(own, server_id="srv", peer_host="192.0.2.229").accept


def test_resolve_own_server_knows_nothing_when_nothing_is_configured():
    async def resolver(host):
        return []

    assert asyncio.run(resolve_own_server(sendspin_server="auto", ma_api_urls=[], resolver=resolver)) is None


def test_own_server_round_trips_through_the_daemon_params():
    own = OwnServer(hosts=frozenset({"a", "b"}), server_ids=frozenset({"i"}), name="MA")
    assert OwnServer.from_params(own.to_params()) == own
    assert OwnServer.from_params(None) is None
    assert OwnServer.from_params({"hosts": []}) is None


def test_refusals_carry_who_owns_the_bridge():
    log = RefusalLog()
    log.note(server_id="s", host="1.1.1.1", name="Test MA", owner="192.168.10.10")
    assert log.recent()[0]["owner"] == "192.168.10.10"


def test_a_server_on_this_machine_is_always_accepted():
    # The Home Assistant add-on reaches Music Assistant by an internal name or
    # address, while Music Assistant on the same host dials from a local one.
    own = OwnServer(hosts=frozenset({"d5369777-music-assistant-dev", "172.30.33.5"}))
    verdict = decide(own, server_id="srv", peer_host="127.0.0.1")
    assert verdict.accept and verdict.own is True


def test_a_server_dialling_from_a_local_interface_address_is_accepted(monkeypatch):
    from sendspin_bridge.services.ipc import own_server

    monkeypatch.setattr(own_server, "is_local_address", lambda host: host == "192.168.10.10")
    own = OwnServer(hosts=frozenset({"172.30.33.5"}))
    assert decide(own, server_id="srv", peer_host="192.168.10.10").accept
    assert not decide(own, server_id="srv", peer_host="192.0.2.229").accept


def test_an_own_name_that_does_not_resolve_makes_the_bridge_accept_everyone():
    async def resolver(host):
        return []

    own = asyncio.run(
        resolve_own_server(sendspin_server="auto", ma_api_urls=["http://ma-gone.lan:8095"], resolver=resolver)
    )
    assert own is None


def test_is_local_address_knows_loopback_and_not_a_foreign_address():
    from sendspin_bridge.services.ipc.own_server import is_local_address

    assert is_local_address("127.0.0.1")
    assert not is_local_address("192.0.2.77")
    assert not is_local_address("not-an-ip")
