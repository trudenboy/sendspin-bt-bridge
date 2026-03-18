from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def config_client(tmp_path, monkeypatch):
    from flask import Flask

    import config
    import routes.api_config as api_config

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
    import services.update_checker as update_checker

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
    import services.update_checker as update_checker

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
    import services.update_checker as update_checker

    assert update_checker._parse_version("v2.41.0-beta.2") < update_checker._parse_version("v2.41.0-rc.1")
    assert update_checker._parse_version("v2.41.0-rc.1") < update_checker._parse_version("v2.41.0")


def test_select_latest_release_filters_by_channel():
    import services.update_checker as update_checker

    releases = [
        {"tag_name": "v2.41.0-beta.2", "prerelease": True, "draft": False},
        {"tag_name": "v2.41.0-rc.1", "prerelease": True, "draft": False},
        {"tag_name": "v2.40.9", "prerelease": False, "draft": False},
        {"tag_name": "v2.41.0", "prerelease": False, "draft": False},
    ]

    assert update_checker._select_latest_release(releases, "beta")["tag_name"] == "v2.41.0-beta.2"
    assert update_checker._select_latest_release(releases, "rc")["tag_name"] == "v2.41.0-rc.1"
    assert update_checker._select_latest_release(releases, "stable")["tag_name"] == "v2.41.0"


def test_api_update_apply_starts_requested_version(config_client, monkeypatch):
    import routes.api_config as api_config

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
    import routes.api_config as api_config
    import state

    captured = {}
    monkeypatch.setattr(state, "get_main_loop", lambda: object())
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

    def fake_run_coroutine_threadsafe(coro, loop):
        return SimpleNamespace(result=lambda timeout=None: asyncio.run(coro))

    monkeypatch.setattr(api_config, "check_latest_version", fake_check_latest_version)
    monkeypatch.setattr(api_config.asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    resp = config_client.post("/api/update/check")

    assert resp.status_code == 200
    assert captured["channel"] == "rc"
    assert resp.get_json()["channel"] == "rc"


def test_api_update_info_reports_beta_channel_warning(config_client, monkeypatch):
    import routes.api_config as api_config
    import state

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
    assert ":beta" in data["instructions"]


def test_lxc_scripts_sync_repo_snapshot_recursively():
    repo_root = Path(__file__).resolve().parent.parent

    for relative_path in ("lxc/install.sh", "lxc/upgrade.sh"):
        text = (repo_root / relative_path).read_text()
        assert "ARCHIVE_URL=" in text
        assert "download_repo_snapshot()" in text
        assert 'find "${src_root}" -maxdepth 1 -type f' in text
        assert "for dir in services routes demo templates static lxc; do" in text
