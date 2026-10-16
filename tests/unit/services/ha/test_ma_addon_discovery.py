"""Finding the Music Assistant add-on through the Supervisor, whatever its slug.

A Music Assistant add-on installed from a local or custom repository gets a
slug the bridge can't know in advance (``local_music_assistant_dev_local`` on a
real install), so the bridge lists the installed add-ons and picks the one that
is Music Assistant instead of only trying the official repository's slugs.
"""

from __future__ import annotations

import logging

import pytest

from sendspin_bridge.services.ha import ha_addon


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
                        for a in self.addons.values()
                    ]
                },
            }
        if path.startswith("/addons/") and path.endswith("/info"):
            slug = path[len("/addons/") : -len("/info")]
            if slug in self.addons:
                return {"result": "ok", "data": dict(self.addons[slug])}
            return None  # Supervisor answers 400 for an unknown add-on
        return None  # 403 for the role: GET /addons etc.


@pytest.fixture(autouse=True)
def _supervisor_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "bridge-supervisor-token")
    ha_addon.reset_ma_addon_cache()
    yield
    ha_addon.reset_ma_addon_cache()


def _install(monkeypatch, addons: list[dict]) -> FakeSupervisor:
    fake = FakeSupervisor(addons)
    monkeypatch.setattr(ha_addon, "_get_supervisor_payload", fake)
    return fake


def test_finds_ma_installed_from_a_local_repository(monkeypatch, caplog):
    _install(
        monkeypatch,
        [
            _addon("core_mosquitto", "Mosquitto broker"),
            _addon("local_music_assistant_dev_local", "Music Assistant (dev)", host_network=True),
        ],
    )

    with caplog.at_level(logging.INFO, logger=ha_addon.__name__):
        info = ha_addon.find_ma_addon_info("http://192.168.10.10:8095")

    assert info is not None
    assert info["slug"] == "local_music_assistant_dev_local"
    assert "local_music_assistant_dev_local" in caplog.text


def test_ingress_url_of_a_local_ma_addon(monkeypatch):
    _install(monkeypatch, [_addon("local_music_assistant_dev_local", "Music Assistant (dev)")])

    assert ha_addon.get_ma_addon_internal_ingress_url() == "http://local-music-assistant-dev-local:8094"


def test_finds_ma_by_its_name_when_the_slug_says_nothing(monkeypatch):
    _install(monkeypatch, [_addon("a1b2c3d4_mass", "Music Assistant Server")])

    info = ha_addon.find_ma_addon_info()

    assert info is not None and info["slug"] == "a1b2c3d4_mass"


def test_skips_a_stopped_ma_addon(monkeypatch):
    _install(
        monkeypatch,
        [
            _addon("d5369777_music_assistant", "Music Assistant", state="stopped"),
            _addon("local_music_assistant_dev_local", "Music Assistant (dev)"),
        ],
    )

    info = ha_addon.find_ma_addon_info()

    assert info is not None and info["slug"] == "local_music_assistant_dev_local"


def test_prefers_the_addon_the_bridge_talks_to(monkeypatch):
    # Both run; the bridge's MA URL names the dev add-on by its hostname.
    _install(
        monkeypatch,
        [
            _addon("d5369777_music_assistant", "Music Assistant"),
            _addon("local_music_assistant_dev_local", "Music Assistant (dev)"),
        ],
    )

    info = ha_addon.find_ma_addon_info("http://local-music-assistant-dev-local:8095")

    assert info is not None and info["slug"] == "local_music_assistant_dev_local"


def test_prefers_the_addon_publishing_the_ma_port(monkeypatch):
    _install(
        monkeypatch,
        [
            _addon("d5369777_music_assistant", "Music Assistant", network={"8095/tcp": None}),
            _addon("local_music_assistant_dev_local", "Music Assistant (dev)", network={"8095/tcp": 8095}),
        ],
    )

    info = ha_addon.find_ma_addon_info("http://192.168.10.10:8095")

    assert info is not None and info["slug"] == "local_music_assistant_dev_local"


def test_official_slug_is_still_found_without_listing(monkeypatch):
    fake = _install(monkeypatch, [_addon("d5369777_music_assistant", "Music Assistant")])

    info = ha_addon.find_ma_addon_info()

    assert info is not None and info["slug"] == "d5369777_music_assistant"
    assert "/supervisor/info" not in fake.calls


def test_found_slug_is_remembered(monkeypatch):
    fake = _install(monkeypatch, [_addon("local_music_assistant_dev_local", "Music Assistant (dev)")])
    ha_addon.find_ma_addon_info()
    fake.calls.clear()

    info = ha_addon.find_ma_addon_info()

    assert info is not None and info["slug"] == "local_music_assistant_dev_local"
    assert fake.calls == ["/addons/local_music_assistant_dev_local/info"]


def test_warns_with_what_was_found_when_no_ma_addon_runs(monkeypatch, caplog):
    _install(
        monkeypatch,
        [
            _addon("core_mosquitto", "Mosquitto broker"),
            _addon("local_music_assistant_dev_local", "Music Assistant (dev)", state="stopped"),
        ],
    )

    with caplog.at_level(logging.WARNING, logger=ha_addon.__name__):
        info = ha_addon.find_ma_addon_info()

    assert info is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("local_music_assistant_dev_local" in m and "stopped" in m for m in warnings)


def test_no_supervisor_means_no_addon(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN")

    assert ha_addon.find_ma_addon_info() is None


@pytest.mark.parametrize(
    ("slug", "name", "expected"),
    [
        ("local_music_assistant_dev_local", "Whatever", True),
        ("d5369777_music_assistant_beta", "Music Assistant Beta", True),
        ("0123abcd_mass", "Music Assistant", True),
        ("core_mosquitto", "Mosquitto broker", False),
        ("sendspin_bt_bridge", "Sendspin Bluetooth Bridge", False),
    ],
)
def test_what_counts_as_a_music_assistant_addon(slug, name, expected):
    assert ha_addon.is_ma_addon({"slug": slug, "name": name}) is expected
