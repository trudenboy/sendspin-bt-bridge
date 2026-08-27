#!/usr/bin/env python3
"""Validate the installed aiosendspin pin and GStreamer audio stack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sendspin_bridge.services.diagnostics.sendspin_compat import get_runtime_dependency_versions


def _gstreamer_report() -> dict[str, object]:
    try:
        from sendspin_bridge.services.audio.player.gst_support import Gst
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    pulsesink = Gst.ElementFactory.make("pulsesink", None) is not None
    fakesink = Gst.ElementFactory.make("fakesink", None) is not None
    return {
        "available": True,
        "version": Gst.version_string(),
        "pulsesink": pulsesink,
        "fakesink": fakesink,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expect-aiosendspin",
        default="",
        help="Fail when the installed aiosendspin version differs from this exact release pin.",
    )
    args = parser.parse_args(argv)

    dependencies = get_runtime_dependency_versions()
    expected_aiosendspin = str(args.expect_aiosendspin or "").strip()
    aiosendspin_pin_matches = not expected_aiosendspin or dependencies.get("aiosendspin") == expected_aiosendspin
    gst = _gstreamer_report()
    result = {
        "dependencies": dependencies,
        "expected_aiosendspin": expected_aiosendspin or None,
        "aiosendspin_pin_matches": aiosendspin_pin_matches,
        "gstreamer": gst,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if not aiosendspin_pin_matches:
        print(
            f"Installed aiosendspin {dependencies.get('aiosendspin')!r} does not match release pin "
            f"{expected_aiosendspin!r}",
            file=sys.stderr,
        )
        return 1
    if not gst.get("available"):
        print(f"GStreamer is not importable: {gst.get('error')}", file=sys.stderr)
        return 1
    if not gst.get("pulsesink"):
        print("GStreamer pulsesink element is not available", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
