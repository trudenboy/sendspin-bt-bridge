from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def config_client(tmp_path, monkeypatch):
    from flask import Flask

    import sendspin_bridge.config as config
    import sendspin_bridge.web.routes.api_config as api_config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(api_config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{}")

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_config.config_bp)
    return app.test_client()


def test_start_upgrade_job_passes_release_tag(monkeypatch):
    import sendspin_bridge.services.diagnostics.update_checker as update_checker

    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        if cmd[:2] == ["systemctl", "show"]:
            return SimpleNamespace(returncode=0, stdout="inactive\n", stderr="")
        if cmd[:2] == ["systemctl", "reset-failed"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="queued\n", stderr="")

    monkeypatch.setattr(update_checker, "_resolve_upgrade_script", lambda: "/opt/sendspin-client/lxc/upgrade.sh")
    monkeypatch.setattr(update_checker.subprocess, "run", fake_run)

    result = update_checker._start_upgrade_job("2.32.0")

    assert result["success"] is True
    assert result["started"] is True
    assert result["target_ref"] == "v2.32.0"
    assert calls[2][-2:] == ["--branch", "v2.32.0"]


def test_start_upgrade_job_returns_running_state(monkeypatch):
    import sendspin_bridge.services.diagnostics.update_checker as update_checker

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[:2] == ["systemctl", "show"]
        return SimpleNamespace(returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(update_checker, "_resolve_upgrade_script", lambda: "/opt/sendspin-client/lxc/upgrade.sh")
    monkeypatch.setattr(update_checker.subprocess, "run", fake_run)

    result = update_checker._start_upgrade_job("v2.32.0")

    assert result == {
        "success": True,
        "started": False,
        "already_running": True,
        "unit": "sendspin-upgrade.service",
    }


def test_parse_version_orders_beta_rc_and_stable():
    import sendspin_bridge.services.diagnostics.update_checker as update_checker

    assert update_checker._parse_version("v2.41.0-beta.2") < update_checker._parse_version("v2.41.0-rc.1")
    assert update_checker._parse_version("v2.41.0-rc.1") < update_checker._parse_version("v2.41.0")


def test_select_latest_release_filters_by_channel():
    import sendspin_bridge.services.diagnostics.update_checker as update_checker

    releases = [
        {"tag_name": "v2.41.0-beta.2", "prerelease": True, "draft": False},
        {"tag_name": "v2.41.0-rc.1", "prerelease": True, "draft": False},
        {"tag_name": "v2.40.9", "prerelease": False, "draft": False},
        {"tag_name": "v2.41.0", "prerelease": False, "draft": False},
    ]

    assert update_checker._select_latest_release(releases, "beta")["tag_name"] == "v2.41.0-beta.2"
    assert update_checker._select_latest_release(releases, "rc")["tag_name"] == "v2.41.0-rc.1"
    assert update_checker._select_latest_release(releases, "stable")["tag_name"] == "v2.41.0"


def test_select_latest_tag_filters_by_channel():
    import sendspin_bridge.services.diagnostics.update_checker as update_checker

    tags = [
        {"name": "v2.41.0-beta.2"},
        {"name": "v2.41.0-rc.1"},
        {"name": "v2.40.9"},
        {"name": "invalid-tag"},
    ]

    assert update_checker._select_latest_tag(tags, "beta")["name"] == "v2.41.0-beta.2"
    assert update_checker._select_latest_tag(tags, "rc")["name"] == "v2.41.0-rc.1"
    assert update_checker._select_latest_tag(tags, "stable")["name"] == "v2.40.9"


def test_check_latest_version_uses_releases_for_stable(monkeypatch):
    import sendspin_bridge.services.diagnostics.update_checker as update_checker

    update_checker = importlib.reload(update_checker)

    async def fake_fetch_releases():
        return [
            {
                "tag_name": "v2.41.0",
                "html_url": "https://example.invalid/releases/v2.41.0",
                "published_at": "2026-03-19T00:00:00Z",
                "body": "Stable notes",
                "prerelease": False,
                "draft": False,
            }
        ]

    async def fake_fetch_tags():
        raise AssertionError("stable update checks must not query tag-only prerelease metadata")

    monkeypatch.setattr(update_checker, "_fetch_releases", fake_fetch_releases)
    monkeypatch.setattr(update_checker, "_fetch_tags", fake_fetch_tags)

    latest = asyncio.run(update_checker.check_latest_version("stable"))

    assert latest == {
        "version": "2.41.0",
        "tag": "v2.41.0",
        "url": "https://example.invalid/releases/v2.41.0",
        "published_at": "2026-03-19T00:00:00Z",
        "body": "Stable notes",
        "channel": "stable",
        "target_ref": "v2.41.0",
        "prerelease": False,
    }


def test_check_latest_version_uses_tags_for_prerelease(monkeypatch):
    import sendspin_bridge.services.diagnostics.update_checker as update_checker

    update_checker = importlib.reload(update_checker)

    async def fake_fetch_releases():
        raise AssertionError("rc update checks must not depend on GitHub release objects")

    async def fake_fetch_tags():
        return [{"name": "v2.41.0-rc.2"}, {"name": "v2.41.0-rc.1"}]

    async def fake_fetch_changelog_section_for_tag(tag):
        assert tag == "v2.41.0-rc.2"
        return "### Fixed\n- RC tag notes"

    monkeypatch.setattr(update_checker, "_fetch_releases", fake_fetch_releases)
    monkeypatch.setattr(update_checker, "_fetch_tags", fake_fetch_tags)
    monkeypatch.setattr(update_checker, "_fetch_changelog_section_for_tag", fake_fetch_changelog_section_for_tag)

    latest = asyncio.run(update_checker.check_latest_version("rc"))

    assert latest == {
        "version": "2.41.0-rc.2",
        "tag": "v2.41.0-rc.2",
        "url": "https://github.com/trudenboy/sendspin-bt-bridge/tree/v2.41.0-rc.2",
        "published_at": "",
        "body": "### Fixed\n- RC tag notes",
        "channel": "rc",
        "target_ref": "v2.41.0-rc.2",
        "prerelease": True,
    }


def test_api_update_apply_starts_requested_version(config_client, monkeypatch):
    import sendspin_bridge.web.routes.api_config as api_config

    captured = {}
    monkeypatch.setattr(api_config, "_detect_runtime", lambda: "systemd")

    def fake_start_upgrade_job(target_ref):
        captured["target_ref"] = target_ref
        return {"success": True, "started": True, "already_running": False, "unit": "sendspin-upgrade.service"}

    monkeypatch.setattr(api_config, "_start_upgrade_job", fake_start_upgrade_job)

    resp = config_client.post("/api/update/apply", json={"version": "2.32.0"})

    assert resp.status_code == 200
    assert captured["target_ref"] == "2.32.0"
    assert resp.get_json()["message"] == "Upgrade started."


def test_api_update_check_uses_selected_channel(config_client, monkeypatch):
    import sendspin_bridge.web.routes.api_config as api_config

    captured = {}
    monkeypatch.setattr(api_config, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_config, "load_config", lambda: {"UPDATE_CHANNEL": "rc"})

    async def fake_check_latest_version(channel=None):
        captured["channel"] = channel
        return {
            "version": "2.41.0-rc.1",
            "tag": "v2.41.0-rc.1",
            "url": "https://example.invalid/rc",
            "published_at": "",
            "body": "",
            "channel": "rc",
            "target_ref": "v2.41.0-rc.1",
            "prerelease": True,
        }

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    def fake_run_coroutine_threadsafe(coro, loop):
        return SimpleNamespace(result=lambda timeout=None: asyncio.run(coro))

    monkeypatch.setattr(api_config, "check_latest_version", fake_check_latest_version)
    monkeypatch.setattr(api_config.asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    monkeypatch.setattr(api_config.threading, "Thread", _ImmediateThread)

    resp = config_client.post("/api/update/check")

    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]
    assert captured["channel"] == "rc"
    result = config_client.get(f"/api/update/check/result/{job_id}")
    assert result.status_code == 200
    assert result.get_json()["channel"] == "rc"


def test_api_update_check_uses_runtime_version_ref_for_rc_updates(config_client, monkeypatch):
    import sendspin_bridge.web.routes.api_config as api_config

    monkeypatch.setattr(api_config, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_config, "load_config", lambda: {"UPDATE_CHANNEL": "rc"})
    monkeypatch.setattr(api_config, "get_runtime_version", lambda: "2.41.0-rc.1")

    async def fake_check_latest_version(channel=None):
        assert channel == "rc"
        return {
            "version": "2.41.0-rc.2",
            "tag": "v2.41.0-rc.2",
            "url": "https://example.invalid/rc",
            "published_at": "",
            "body": "",
            "channel": "rc",
            "target_ref": "v2.41.0-rc.2",
            "prerelease": True,
        }

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    def fake_run_coroutine_threadsafe(coro, loop):
        return SimpleNamespace(result=lambda timeout=None: asyncio.run(coro))

    monkeypatch.setattr(api_config, "check_latest_version", fake_check_latest_version)
    monkeypatch.setattr(api_config.asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    monkeypatch.setattr(api_config.threading, "Thread", _ImmediateThread)

    resp = config_client.post("/api/update/check")

    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]
    result = config_client.get(f"/api/update/check/result/{job_id}")
    assert result.status_code == 200
    payload = result.get_json()
    assert payload["update_available"] is True
    assert payload["current_version"] == "2.41.0-rc.1"
    assert payload["version"] == "2.41.0-rc.2"


def test_api_update_info_reports_beta_channel_warning(config_client, monkeypatch):
    import sendspin_bridge.bridge.state as state
    import sendspin_bridge.web.routes.api_config as api_config

    monkeypatch.setattr(api_config, "load_config", lambda: {"UPDATE_CHANNEL": "beta", "AUTO_UPDATE": False})
    monkeypatch.setattr(api_config, "_detect_runtime", lambda: "docker")
    state.set_update_available(
        {
            "version": "2.41.0-beta.1",
            "tag": "v2.41.0-beta.1",
            "channel": "beta",
            "url": "https://example.invalid/beta",
        }
    )

    try:
        resp = config_client.get("/api/update/info")
    finally:
        state.set_update_available(None)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["channel"] == "beta"
    assert "Beta channel" in data["channel_warning"]
    assert data["command"] == "docker compose pull && docker compose up -d"
    assert data["docker_image"] == "ghcr.io/trudenboy/sendspin-bt-bridge:beta"


def test_api_update_info_reports_matching_ha_addon_delivery_channel(config_client, monkeypatch):
    import sendspin_bridge.bridge.state as state
    import sendspin_bridge.web.routes.api_config as api_config

    monkeypatch.setattr(api_config, "load_config", lambda: {"UPDATE_CHANNEL": "rc", "AUTO_UPDATE": False})
    monkeypatch.setattr(api_config, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setattr(
        api_config,
        "_get_ha_addon_delivery_details",
        lambda: {
            "channel": "rc",
            "slug": "85b1ecde_sendspin_bt_bridge_rc",
            "name": "Sendspin Bluetooth Bridge (RC)",
        },
    )
    state.set_update_available(None)

    resp = config_client.get("/api/update/info")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["delivery_channel"] == "rc"
    assert data["delivery_slug"] == "85b1ecde_sendspin_bt_bridge_rc"
    assert data["channel_switch_required"] is False
    assert "Sendspin Bluetooth Bridge (RC)" in data["instructions"]


def test_api_update_info_flags_when_selected_channel_differs_from_installed_ha_variant(config_client, monkeypatch):
    import sendspin_bridge.bridge.state as state
    import sendspin_bridge.web.routes.api_config as api_config

    monkeypatch.setattr(api_config, "load_config", lambda: {"UPDATE_CHANNEL": "beta", "AUTO_UPDATE": False})
    monkeypatch.setattr(api_config, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setattr(
        api_config,
        "_get_ha_addon_delivery_details",
        lambda: {
            "channel": "stable",
            "slug": "85b1ecde_sendspin_bt_bridge",
            "name": "Sendspin Bluetooth Bridge",
        },
    )
    state.set_update_available(None)

    resp = config_client.get("/api/update/info")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["delivery_channel"] == "stable"
    assert data["channel_switch_required"] is True
    assert "Selected update channel is `beta`" in data["instructions"]
    assert "installed Home Assistant addon track is `stable`" in data["instructions"]


def test_api_update_apply_in_ha_addon_returns_matching_variant_guidance(config_client, monkeypatch):
    import sendspin_bridge.web.routes.api_config as api_config

    monkeypatch.setattr(api_config, "load_config", lambda: {"UPDATE_CHANNEL": "beta"})
    monkeypatch.setattr(api_config, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setattr(
        api_config,
        "_get_ha_addon_delivery_details",
        lambda: {
            "channel": "stable",
            "slug": "85b1ecde_sendspin_bt_bridge",
            "name": "Sendspin Bluetooth Bridge",
        },
    )

    resp = config_client.post("/api/update/apply")

    assert resp.status_code == 400
    assert "Selected update channel is `beta`" in resp.get_json()["error"]


def test_lxc_scripts_sync_repo_snapshot_recursively():
    """LXC install/upgrade scripts must download a snapshot, copy the
    src-layout package + manifests, and record the release ref.

    Post-2.66 the scripts ship the src/ tree wholesale rather than
    iterating per-file in the repo root.
    """
    repo_root = Path(__file__).resolve().parents[4]

    for relative_path in ("deployment/lxc/install.sh", "deployment/lxc/upgrade.sh"):
        text = (repo_root / relative_path).read_text()
        assert "ARCHIVE_URL=" in text
        assert "download_repo_snapshot()" in text
        # src-layout: package source travels as a tree, manifests as files.
        assert 'cp -a "${src_root}/src" "${dest_root}/src"' in text
        assert 'cp -a "${src_root}/pyproject.toml"' in text
        assert "record_release_ref()" in text
        assert ".release-ref" in text
