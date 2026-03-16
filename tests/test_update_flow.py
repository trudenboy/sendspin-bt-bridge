from __future__ import annotations

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
    monkeypatch.setattr(api_config, "CONFIG_DIR", tmp_path)
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


def test_lxc_scripts_sync_repo_snapshot_recursively():
    repo_root = Path(__file__).resolve().parent.parent

    for relative_path in ("lxc/install.sh", "lxc/upgrade.sh"):
        text = (repo_root / relative_path).read_text()
        assert "ARCHIVE_URL=" in text
        assert "download_repo_snapshot()" in text
        assert 'find "${src_root}" -maxdepth 1 -type f' in text
        assert "for dir in services routes demo templates static lxc; do" in text
