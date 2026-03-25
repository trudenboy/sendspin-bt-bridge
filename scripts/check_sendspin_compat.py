#!/usr/bin/env python3
"""Validate that the installed sendspin DaemonArgs API is still runtime-compatible."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sendspin_compat import (
    analyze_audio_api_compatibility,
    analyze_daemon_args_compatibility,
    get_runtime_dependency_versions,
    load_sendspin_audio_api,
)


def main() -> int:
    try:
        from sendspin.daemon.daemon import DaemonArgs
    except ModuleNotFoundError as exc:
        print(
            json.dumps(
                {
                    "compatible": False,
                    "signature": None,
                    "filtered_keys": [],
                    "dropped_keys": [],
                    "missing_required": [],
                    "bind_error": f"sendspin import failed: {exc}",
                    "dependencies": get_runtime_dependency_versions(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    candidate_kwargs = {
        "audio_device": object(),
        "client_id": "compat-smoke-player",
        "client_name": "Compat Smoke Player",
        "settings": object(),
        "url": "ws://localhost:9000/sendspin",
        "static_delay_ms": -500.0,
        "listen_port": 9000,
        "use_mpris": False,
        "use_hardware_volume": False,
        "preferred_format": None,
    }
    compatibility = analyze_daemon_args_compatibility(DaemonArgs, candidate_kwargs)
    audio_compatibility = analyze_audio_api_compatibility(load_sendspin_audio_api())
    result = {
        "dependencies": get_runtime_dependency_versions(),
        "audio_api": audio_compatibility,
        **compatibility,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if compatibility["compatible"] and audio_compatibility["compatible"]:
        return 0
    print("Installed sendspin runtime is not compatible with bridge startup requirements", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
