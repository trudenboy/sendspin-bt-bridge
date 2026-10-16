"""Finding the Music Assistant add-on through the Supervisor, whatever its slug.

A Music Assistant add-on installed from a local or custom repository gets a
slug the bridge can't know in advance (``local_music_assistant_dev_local`` on a
real install), so the bridge lists the installed add-ons.  Slug and name are
chosen by the add-on's author, so a non-official add-on is trusted only once it
proves to be the Music Assistant the bridge's MA URL reaches.
"""

from __future__ import annotations

import logging

import pytest

from sendspin_bridge.services.ha import ha_addon

LOCAL = "local_music_assistant_dev_local"
MA_URL = "http://192.168.10.10:8095"


def _addon(slug: str, name: str, state: str = "started", **extra) -> dict:
    info = {
        "slug": slug,
        "name": name,
        "state": state,
        "hostname": slug.replace("_", "-"),
        "ingress": True,
        "ingress_port": 8094,
        "ingress_url": f"/api/hassio_ingress/{slug}-token/",
    }
    info.update(extra)
    return info


class FakeSupervisor:
    """The Supervisor API as an add-on with ``hassio_role: default`` sees it.

    That role may read ``/<anything>/info`` (so ``/supervisor/info`` with its
    add-on list, and ``/addons/<slug>/info``) but not ``GET /addons``.
    """

    def __init__(self, addons: list[dict]):
        self.addons = {a["slug"]: a for a in addons}
        self.calls: list[str] = []

    def __call__(self, path: str, timeout: float = 5.0):
        self.calls.append(path)
        if path == "/supervisor/info":
            return {
                "result": "ok",
                "data": {
                    "addons": [
                        {"slug": a["slug"], "name": a["name"], "state": a["state"], "repository": "x"}
                        for a in sorted(self.addons.values(), key=lambda a: a["slug"])
                    ]
                },
            }
        if path.startswith("/addons/") and path.endswith("/info"):
            slug = path[len("/addons/") : -len("/info")]
            if slug in self.addons:
                return {"result": "ok", "data": dict(self.addons[slug])}
            return None  # Supervisor answers 400 for an unknown add-on
        return None  # 403 for the role: GET /addons etc.


class FakeMaNetwork:
    """``GET <url>/info`` → ``server_id`` for whatever answers at that address."""

    def __init__(self, ids: dict[str, str]):
        self.ids = ids

    def __call__(self, base_url: str, timeout: float = 5.0):
        return self.ids.get(base_url.rstrip("/"))


@pytest.fixture(autouse=True)
def _supervisor_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "bridge-supervisor-token")
    ha_addon.reset_ma_addon_cache()
    yield
    ha_addon.reset_ma_addon_cache()


def _install(monkeypatch, addons: list[dict], ids: dict[str, str] | None = None) -> FakeSupervisor:
    fake = FakeSupervisor(addons)
    monkeypatch.setattr(ha_addon, "_get_supervisor_payload", fake)
    monkeypatch.setattr(ha_addon, "ma_server_id", FakeMaNetwork(ids or {}))
    return fake


def test_finds_ma_installed_from_a_local_repository(monkeypatch, caplog):
    _install(
        monkeypatch,
        [_addon("core_mosquitto", "Mosquitto broker"), _addon(LOCAL, "Music Assistant (dev)", host_network=True)],
        {MA_URL: "srv-1", "http://local-music-assistant-dev-local:8095": "srv-1"},
    )

    with caplog.at_level(logging.INFO, logger=ha_addon.__name__):
        info = ha_addon.find_ma_addon_info(MA_URL)

    assert info is not None and info["slug"] == LOCAL
    assert LOCAL in caplog.text


def test_ingress_url_of_a_local_ma_addon(monkeypatch):
    _install(
        monkeypatch,
        [_addon(LOCAL, "Music Assistant (dev)")],
        {MA_URL: "srv-1", "http://local-music-assistant-dev-local:8095": "srv-1"},
    )

    assert ha_addon.get_ma_addon_internal_ingress_url(MA_URL) == "http://local-music-assistant-dev-local:8094"


def test_finds_ma_by_its_name_when_the_slug_says_nothing(monkeypatch):
    # Answers only on its ingress port.
    _install(
        monkeypatch,
        [_addon("a1b2c3d4_mass", "Music Assistant Server")],
        {MA_URL: "srv-1", "http://a1b2c3d4-mass:8094": "srv-1"},
    )

    info = ha_addon.find_ma_addon_info(MA_URL)

    assert info is not None and info["slug"] == "a1b2c3d4_mass"


def test_skips_a_stopped_ma_addon(monkeypatch):
    _install(
        monkeypatch,
        [
            _addon("d5369777_music_assistant", "Music Assistant", state="stopped"),
            _addon(LOCAL, "Music Assistant (dev)"),
        ],
        {MA_URL: "srv-1", "http://local-music-assistant-dev-local:8095": "srv-1"},
    )

    info = ha_addon.find_ma_addon_info(MA_URL)

    assert info is not None and info["slug"] == LOCAL


def test_refuses_a_look_alike_that_is_another_server(monkeypatch):
    """An add-on named like MA that sorts first is not the MA the bridge uses."""
    _install(
        monkeypatch,
        [_addon("abcd1234_music_assistant_fake", "Music Assistant"), _addon(LOCAL, "Music Assistant (dev)")],
        {
            MA_URL: "srv-real",
            "http://abcd1234-music-assistant-fake:8095": "srv-fake",
            "http://local-music-assistant-dev-local:8095": "srv-real",
        },
    )

    info = ha_addon.find_ma_addon_info(MA_URL)

    assert info is not None and info["slug"] == LOCAL


def test_refuses_a_look_alike_when_it_is_the_only_candidate(monkeypatch):
    _install(
        monkeypatch,
        [_addon("abcd1234_music_assistant_fake", "Music Assistant")],
        {MA_URL: "srv-real", "http://abcd1234-music-assistant-fake:8095": "srv-fake"},
    )

    lookup = ha_addon.resolve_ma_addon(MA_URL)

    assert lookup.info is None
    assert "abcd1234_music_assistant_fake" in lookup.reason


def test_refuses_to_pick_when_two_add_ons_claim_the_same_server(monkeypatch):
    # A look-alike echoing the real /info makes a tie: refused, nothing is sent anywhere.
    _install(
        monkeypatch,
        [_addon("abcd1234_music_assistant_fake", "Music Assistant"), _addon(LOCAL, "Music Assistant (dev)")],
        {
            MA_URL: "srv-real",
            "http://abcd1234-music-assistant-fake:8095": "srv-real",
            "http://local-music-assistant-dev-local:8095": "srv-real",
        },
    )

    lookup = ha_addon.resolve_ma_addon(MA_URL)

    assert lookup.info is None
    assert "abcd1234_music_assistant_fake" in lookup.reason and LOCAL in lookup.reason


def test_refuses_a_non_official_add_on_without_proof(monkeypatch):
    # No MA URL to compare with: a non-official add-on can't be trusted.
    _install(monkeypatch, [_addon(LOCAL, "Music Assistant (dev)")], {})

    lookup = ha_addon.resolve_ma_addon("")

    assert lookup.info is None
    assert LOCAL in lookup.reason


def test_official_add_on_wins_outright(monkeypatch):
    _install(
        monkeypatch,
        [_addon("d5369777_music_assistant", "Music Assistant"), _addon(LOCAL, "Music Assistant (dev)")],
    )

    info = ha_addon.find_ma_addon_info()

    assert info is not None and info["slug"] == "d5369777_music_assistant"


def test_remembered_add_on_is_per_ma_url(monkeypatch):
    # Official and dev both run; the bridge's MA URL is the dev one.
    _install(
        monkeypatch,
        [_addon("d5369777_music_assistant", "Music Assistant"), _addon(LOCAL, "Music Assistant (dev)")],
        {
            MA_URL: "srv-dev",
            "http://d5369777-music-assistant:8095": "srv-official",
            "http://local-music-assistant-dev-local:8095": "srv-dev",
        },
    )
    first = ha_addon.find_ma_addon_info()  # e.g. discovery, no URL yet

    info = ha_addon.find_ma_addon_info(MA_URL)

    assert first is not None and first["slug"] == "d5369777_music_assistant"
    assert info is not None and info["slug"] == LOCAL


def test_found_slug_is_remembered_until_it_stops(monkeypatch):
    fake = _install(
        monkeypatch,
        [_addon(LOCAL, "Music Assistant (dev)")],
        {MA_URL: "srv-1", "http://local-music-assistant-dev-local:8095": "srv-1"},
    )
    ha_addon.find_ma_addon_info(MA_URL)
    fake.calls.clear()

    info = ha_addon.find_ma_addon_info(MA_URL)
    assert info is not None and info["slug"] == LOCAL
    assert fake.calls == [f"/addons/{LOCAL}/info"]

    fake.addons[LOCAL]["state"] = "stopped"
    assert ha_addon.find_ma_addon_info(MA_URL) is None


def test_not_found_is_remembered_briefly_and_warned_once(monkeypatch, caplog):
    fake = _install(
        monkeypatch,
        [_addon("core_mosquitto", "Mosquitto broker"), _addon(LOCAL, "Music Assistant (dev)", state="stopped")],
    )

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
    _install(monkeypatch, [_addon(LOCAL, "Music Assistant (dev)")])

    urls = [c["url"] for c in ha_addon.get_ma_addon_discovery_candidates()]

    assert urls[0] == "http://local-music-assistant-dev-local:8095"


def test_discovery_does_not_suggest_one_of_several_look_alikes(monkeypatch):
    _install(
        monkeypatch,
        [_addon("abcd1234_music_assistant_fake", "Music Assistant"), _addon(LOCAL, "Music Assistant (dev)")],
    )

    sources = [c["source"] for c in ha_addon.get_ma_addon_discovery_candidates()]

    assert "ha_addon_hostname" not in sources


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
