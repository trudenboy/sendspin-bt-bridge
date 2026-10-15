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
    verdict = decide(OWN, server_id="srv-other", peer_host="192.168.10.229")
    assert not verdict.accept and verdict.own is False


def test_an_unknown_own_server_accepts_everyone():
    verdict = decide(None, server_id="srv-other", peer_host="192.168.10.229")
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
    assert own.hosts == {"ma.lan", "192.168.10.10"}


def test_resolve_own_server_uses_an_explicit_sendspin_host():
    async def resolver(host):
        return []

    own = asyncio.run(resolve_own_server(sendspin_server="10.1.1.1", ma_api_urls=[], resolver=resolver))
    assert own is not None and own.hosts == {"10.1.1.1"}


def test_resolve_own_server_knows_nothing_when_only_loopback_is_configured():
    async def resolver(host):
        return []

    assert (
        asyncio.run(
            resolve_own_server(sendspin_server="auto", ma_api_urls=["http://localhost:8095"], resolver=resolver)
        )
        is None
    )
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
