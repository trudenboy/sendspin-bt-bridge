#!/usr/bin/env python3
"""Validate that the installed sendspin DaemonArgs API is still runtime-compatible."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sendspin_bridge.services.diagnostics.sendspin_compat import (
    analyze_audio_api_compatibility,
    analyze_daemon_args_compatibility,
    get_runtime_dependency_versions,
    load_sendspin_audio_api,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expect-aiosendspin",
        default="",
        help="Fail when the installed aiosendspin version differs from this exact release pin.",
    )
    args = parser.parse_args(argv)

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
        "static_delay_ms": 0.0,
        "listen_port": 9000,
        "use_mpris": False,
        "use_hardware_volume": False,
        "preferred_format": None,
    }
    compatibility = analyze_daemon_args_compatibility(DaemonArgs, candidate_kwargs)
    audio_compatibility = analyze_audio_api_compatibility(load_sendspin_audio_api())
    dependencies = get_runtime_dependency_versions()
    expected_aiosendspin = str(args.expect_aiosendspin or "").strip()
    aiosendspin_pin_matches = not expected_aiosendspin or dependencies.get("aiosendspin") == expected_aiosendspin
    result = {
        "dependencies": dependencies,
        "audio_api": audio_compatibility,
        "expected_aiosendspin": expected_aiosendspin or None,
        "aiosendspin_pin_matches": aiosendspin_pin_matches,
        **compatibility,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if compatibility["compatible"] and audio_compatibility["compatible"] and aiosendspin_pin_matches:
        return 0
    if not aiosendspin_pin_matches:
        print(
            f"Installed aiosendspin {dependencies.get('aiosendspin')!r} does not match release pin "
            f"{expected_aiosendspin!r}",
            file=sys.stderr,
        )
    if not compatibility["compatible"] or not audio_compatibility["compatible"]:
        print("Installed sendspin runtime is not compatible with bridge startup requirements", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
