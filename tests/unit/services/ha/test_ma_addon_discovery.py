"""Finding the Music Assistant add-on through the Supervisor, whatever its slug.

A Music Assistant add-on installed from a local or custom repository gets a
slug the bridge can't know in advance (``local_music_assistant_dev_local`` on a
real install), so the bridge lists the installed add-ons.  Slug, name and the
public ``/info`` are all the add-on author's to choose, so a non-official
add-on is trusted only on facts the Supervisor sets: it comes from the local
repository (or the admin named it in ``MA_ADDON_SLUG``), and the configured MA
URL is one of its addresses.
"""

from __future__ import annotations

import json
import logging

import pytest

from sendspin_bridge.services.ha import ha_addon

LOCAL = "local_music_assistant_dev_local"
LAN_IP = "192.168.10.10"
MA_URL = f"http://{LAN_IP}:8095"


def _addon(slug: str, name: str, state: str = "started", **extra) -> dict:
    repository = slug.split("_", 1)[0] if "_" in slug else "local"
    info = {
        "slug": slug,
        "name": name,
        "state": state,
        "repository": repository,
        "hostname": slug.replace("_", "-"),
        "ip_address": "172.30.33.5",
        "host_network": False,
        "network": {},
        "ingress": True,
        "ingress_port": 8094,
        "ingress_url": f"/api/hassio_ingress/{slug}-token/",
    }
    info.update(extra)
    return info


def _host_ma(slug: str = LOCAL, name: str = "Music Assistant (dev)", **extra) -> dict:
    """Music Assistant as its add-on runs it: on the host network."""
    return _addon(slug, name, host_network=True, ip_address="172.30.32.1", **extra)


class FakeSupervisor:
    """The Supervisor API as an add-on with ``hassio_role: default`` sees it.

    That role may read ``/<anything>/info`` (``/supervisor/info`` with its
    add-on list, ``/network/info``, ``/addons/<slug>/info``) but not ``GET /addons``.
    """

    def __init__(self, addons: list[dict]):
        self.addons = {a["slug"]: a for a in addons}
        self.calls: list[str] = []

    def __call__(self, path: str, timeout: float = 5.0):
        self.calls.append(path)
        if path == "/supervisor/info":
            rows = [
                {k: a[k] for k in ("slug", "name", "state", "repository")}
                for a in sorted(self.addons.values(), key=lambda a: a["slug"])
            ]
            return {"result": "ok", "data": {"addons": rows}}
        if path == "/network/info":
            return {
                "result": "ok",
                "data": {
                    "interfaces": [{"interface": "enp0s18", "ipv4": {"address": [f"{LAN_IP}/24"]}}],
                    "docker": {"interface": "hassio", "address": "172.30.32.0/23", "gateway": "172.30.32.1"},
                },
            }
        if path.startswith("/addons/") and path.endswith("/info"):
            slug = path[len("/addons/") : -len("/info")]
            if slug in self.addons:
                return {"result": "ok", "data": dict(self.addons[slug])}
            return None  # Supervisor answers 400 for an unknown add-on
        return None  # 403 for the role: GET /addons etc.


@pytest.fixture(autouse=True)
def _supervisor_env(monkeypatch, tmp_path):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "bridge-supervisor-token")
    # Fake DNS: the HA host's name resolves to its LAN address.
    dns = {"homeassistant.local": {LAN_IP}}
    monkeypatch.setattr(ha_addon, "_resolve_host", lambda host: {host} if host[0].isdigit() else dns.get(host, set()))
    ha_addon.reset_ma_addon_cache()
    yield
    ha_addon.reset_ma_addon_cache()


def _install(monkeypatch, addons: list[dict], ids: dict[str, str] | None = None) -> FakeSupervisor:
    """The Supervisor, plus what answers ``GET <url>/info`` with which ``server_id``."""
    fake = FakeSupervisor(addons)
    known = ids or {}
    monkeypatch.setattr(ha_addon, "_get_supervisor_payload", fake)
    monkeypatch.setattr(ha_addon, "ma_server_id", lambda url, timeout=5.0: known.get(url.rstrip("/")))
    return fake


def _opt_in(tmp_path, slug: str) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"MA_ADDON_SLUG": slug}))


@pytest.mark.parametrize("ma_url", [MA_URL, "http://homeassistant.local:8095"])
def test_local_host_network_ma_is_used(monkeypatch, caplog, ma_url):
    _install(monkeypatch, [_addon("core_mosquitto", "Mosquitto broker"), _host_ma()])

    with caplog.at_level(logging.INFO, logger=ha_addon.__name__):
        info = ha_addon.find_ma_addon_info(ma_url)

    assert info is not None and info["slug"] == LOCAL
    assert "local repository" in caplog.text


def test_local_bridged_ma_is_used_at_its_hostname(monkeypatch):
    _install(monkeypatch, [_addon(LOCAL, "Music Assistant (dev)", network={"8095/tcp": None})])
    url = "http://local-music-assistant-dev-local:8095"

    assert ha_addon.get_ma_addon_internal_ingress_url(url) == "http://local-music-assistant-dev-local:8094"


def test_local_ma_on_another_port_is_not_the_one(monkeypatch):
    _install(monkeypatch, [_host_ma()])

    lookup = ha_addon.resolve_ma_addon(f"http://{LAN_IP}:9000")

    assert lookup.info is None
    assert LOCAL in lookup.reason


def test_lone_custom_repository_look_alike_echoing_info_is_refused(monkeypatch):
    """Named "Music Assistant", on the host network, and proxying the real /info:
    still nothing the Supervisor vouches for."""
    fake = _host_ma("abcd1234_music_assistant", "Music Assistant")
    _install(
        monkeypatch,
        [fake],
        {MA_URL: "srv-real", "http://abcd1234-music-assistant:8095": "srv-real"},
    )

    lookup = ha_addon.resolve_ma_addon(MA_URL)

    assert lookup.info is None
    assert "abcd1234_music_assistant" in lookup.reason and "MA_ADDON_SLUG" in lookup.reason


def test_opted_in_custom_repository_add_on_is_used_at_its_address(monkeypatch, tmp_path, caplog):
    _opt_in(tmp_path, "abcd1234_music_assistant")
    _install(monkeypatch, [_host_ma("abcd1234_music_assistant", "Music Assistant")])

    with caplog.at_level(logging.INFO, logger=ha_addon.__name__):
        info = ha_addon.find_ma_addon_info(MA_URL)

    assert info is not None and info["slug"] == "abcd1234_music_assistant"
    assert "MA_ADDON_SLUG" in caplog.text


def test_opted_in_add_on_must_still_be_at_the_ma_url(monkeypatch, tmp_path):
    _opt_in(tmp_path, "abcd1234_music_assistant")
    _install(monkeypatch, [_addon("abcd1234_music_assistant", "Music Assistant", network={"8095/tcp": None})])

    lookup = ha_addon.resolve_ma_addon(MA_URL)  # the host's address, but this add-on isn't on the host network

    assert lookup.info is None


def test_two_local_host_network_candidates_are_refused(monkeypatch):
    _install(monkeypatch, [_host_ma(), _host_ma("local_music_assistant", "Music Assistant")])

    lookup = ha_addon.resolve_ma_addon(MA_URL)

    assert lookup.info is None
    assert LOCAL in lookup.reason and "local_music_assistant " in lookup.reason


def test_server_id_mismatch_refuses_an_otherwise_trusted_add_on(monkeypatch):
    _install(
        monkeypatch, [_host_ma()], {MA_URL: "srv-real", "http://local-music-assistant-dev-local:8095": "srv-other"}
    )

    assert ha_addon.find_ma_addon_info(MA_URL) is None


def test_skips_a_stopped_official_add_on(monkeypatch):
    _install(monkeypatch, [_host_ma("d5369777_music_assistant", "Music Assistant", state="stopped"), _host_ma()])

    info = ha_addon.find_ma_addon_info(MA_URL)

    assert info is not None and info["slug"] == LOCAL


def test_official_add_on_wins_outright(monkeypatch):
    _install(monkeypatch, [_host_ma("d5369777_music_assistant", "Music Assistant"), _host_ma()])

    info = ha_addon.find_ma_addon_info()

    assert info is not None and info["slug"] == "d5369777_music_assistant"


def test_non_official_add_on_needs_the_ma_url(monkeypatch):
    _install(monkeypatch, [_host_ma()])

    lookup = ha_addon.resolve_ma_addon("")

    assert lookup.info is None
    assert LOCAL in lookup.reason


def test_remembered_add_on_is_per_ma_url(monkeypatch):
    # Official and dev both run; the bridge's MA URL is the dev one.
    _install(
        monkeypatch,
        [_addon("d5369777_music_assistant", "Music Assistant"), _host_ma()],
        {MA_URL: "srv-dev", "http://d5369777-music-assistant:8095": "srv-official"},
    )
    first = ha_addon.find_ma_addon_info()  # e.g. discovery, no URL yet

    info = ha_addon.find_ma_addon_info(MA_URL)

    assert first is not None and first["slug"] == "d5369777_music_assistant"
    assert info is not None and info["slug"] == LOCAL


def test_found_slug_is_remembered_until_it_stops(monkeypatch):
    fake = _install(monkeypatch, [_host_ma()])
    ha_addon.find_ma_addon_info(MA_URL)
    fake.calls.clear()

    info = ha_addon.find_ma_addon_info(MA_URL)
    assert info is not None and info["slug"] == LOCAL
    assert "/supervisor/info" not in fake.calls  # no new listing, only a re-check

    fake.addons[LOCAL]["state"] = "stopped"
    assert ha_addon.find_ma_addon_info(MA_URL) is None


def test_not_found_is_remembered_briefly_and_warned_once(monkeypatch, caplog):
    fake = _install(monkeypatch, [_addon("core_mosquitto", "Mosquitto broker"), _host_ma(state="stopped")])

    with caplog.at_level(logging.WARNING, logger=ha_addon.__name__):
        assert ha_addon.find_ma_addon_info(MA_URL) is None
        calls = len(fake.calls)
        assert ha_addon.find_ma_addon_info(MA_URL) is None

    assert len(fake.calls) == calls
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert LOCAL in warnings[0] and "stopped" in warnings[0]


def test_no_supervisor_means_no_addon(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN")

    assert ha_addon.find_ma_addon_info() is None


def test_discovery_suggests_a_lone_local_add_on(monkeypatch):
    _install(monkeypatch, [_host_ma()])

    urls = [c["url"] for c in ha_addon.get_ma_addon_discovery_candidates()]

    assert urls[0] == "http://local-music-assistant-dev-local:8095"


def test_discovery_does_not_suggest_a_custom_repository_look_alike(monkeypatch):
    _install(monkeypatch, [_host_ma("abcd1234_music_assistant", "Music Assistant")])

    sources = [c["source"] for c in ha_addon.get_ma_addon_discovery_candidates()]

    assert "ha_addon_hostname" not in sources


def test_host_addresses_come_from_the_supervisor(monkeypatch):
    _install(monkeypatch, [])

    assert ha_addon.host_addresses() == {LAN_IP, "172.30.32.1"}


@pytest.mark.parametrize(
    ("slug", "name", "expected"),
    [
        (LOCAL, "Whatever", True),
        ("d5369777_music_assistant_beta", "Music Assistant Beta", True),
        ("0123abcd_mass", "Music Assistant", True),
        ("core_mosquitto", "Mosquitto broker", False),
        ("sendspin_bt_bridge", "Sendspin Bluetooth Bridge", False),
    ],
)
def test_what_counts_as_a_music_assistant_candidate(slug, name, expected):
    assert ha_addon.is_ma_addon({"slug": slug, "name": name}) is expected


def test_remembered_add_on_is_dropped_when_the_opt_in_is_withdrawn(monkeypatch, tmp_path):
    _opt_in(tmp_path, "abcd1234_music_assistant")
    _install(monkeypatch, [_host_ma("abcd1234_music_assistant", "Music Assistant")])
    assert ha_addon.find_ma_addon_info(MA_URL) is not None

    (tmp_path / "config.json").write_text(json.dumps({}))

    assert ha_addon.find_ma_addon_info(MA_URL) is None


def test_remembered_add_on_is_dropped_when_it_leaves_the_address(monkeypatch):
    fake = _install(monkeypatch, [_host_ma()])
    assert ha_addon.find_ma_addon_info(MA_URL) is not None

    fake.addons[LOCAL]["host_network"] = False

    assert ha_addon.find_ma_addon_info(MA_URL) is None


def test_opted_in_add_on_counts_whatever_it_is_called(monkeypatch, tmp_path):
    _opt_in(tmp_path, "abcd1234_mass_server")
    _install(monkeypatch, [_host_ma("abcd1234_mass_server", "My media server")])

    info = ha_addon.find_ma_addon_info(MA_URL)

    assert info is not None and info["slug"] == "abcd1234_mass_server"


@pytest.mark.parametrize("ma_url", ["http://localhost:8095", "http://127.0.0.1:8095"])
def test_loopback_is_the_host_for_a_host_network_add_on(monkeypatch, ma_url):
    monkeypatch.setattr(ha_addon, "_resolve_host", lambda host: {"127.0.0.1"} if host == "localhost" else {host})
    _install(monkeypatch, [_host_ma()])

    info = ha_addon.find_ma_addon_info(ma_url)

    assert info is not None and info["slug"] == LOCAL


def test_official_add_on_must_be_at_the_ma_url_too(monkeypatch):
    # Official add-on runs bridged without publishing 8095: the host's 8095 is someone else.
    _install(monkeypatch, [_addon("d5369777_music_assistant", "Music Assistant")])

    assert ha_addon.find_ma_addon_info(MA_URL) is None
