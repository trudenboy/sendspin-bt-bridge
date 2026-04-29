#!/usr/bin/env python3
"""Smoke-check the packaged runtime/container contract."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RUNTIME_MODULES = (
    "sendspin_bridge.bridge.orchestrator",
    "sendspin_client",  # B6 moves this to sendspin_bridge.bridge.client
    "sendspin_bridge.web.interface",
)
TRANSLATOR_PATH = REPO_ROOT / "scripts" / "translate_ha_config.py"


def _import_runtime_modules() -> list[str]:
    imported: list[str] = []
    for module_name in RUNTIME_MODULES:
        importlib.import_module(module_name)
        imported.append(module_name)
    return imported


def _build_minimal_options() -> dict[str, object]:
    return {
        "sendspin_server": "auto",
        "sendspin_port": 9000,
        "bridge_name": "SmokeTest",
        "bluetooth_devices": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Smoke Speaker"}],
        "bluetooth_adapters": [],
        "tz": "UTC",
        "log_level": "info",
        "volume_via_ma": True,
    }


def _run_translation_smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="sendspin-ha-smoke-") as temp_dir:
        temp_path = Path(temp_dir)
        options_path = temp_path / "options.json"
        config_path = temp_path / "config.json"
        options_path.write_text(json.dumps(_build_minimal_options(), indent=2), encoding="utf-8")

        env = os.environ.copy()
        env["SENDSPIN_HA_OPTIONS_FILE"] = str(options_path)
        env["SENDSPIN_HA_CONFIG_FILE"] = str(config_path)
        env["HOSTNAME"] = "sendspin-bt-bridge-rc"

        result = subprocess.run(
            [sys.executable, str(TRANSLATOR_PATH)],
            cwd="/",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "translate_ha_config.py failed",
                {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
        if not config_path.exists():
            raise FileNotFoundError(config_path)
        translated = json.loads(config_path.read_text(encoding="utf-8"))
        if translated.get("UPDATE_CHANNEL") != "rc":
            raise AssertionError(f"Unexpected UPDATE_CHANNEL: {translated.get('UPDATE_CHANNEL')!r}")
        devices = translated.get("BLUETOOTH_DEVICES", [])
        if not devices or devices[0].get("enabled") is not True:
            raise AssertionError(f"Unexpected translated devices: {devices!r}")
        return translated


def main() -> int:
    try:
        imported_modules = _import_runtime_modules()
        translated = _run_translation_smoke()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "imported_modules": imported_modules,
                "translated_channel": translated.get("UPDATE_CHANNEL"),
                "translated_devices": len(translated.get("BLUETOOTH_DEVICES", [])),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
