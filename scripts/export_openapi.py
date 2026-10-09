#!/usr/bin/env python3
"""Write the API v1 OpenAPI document that the SPA's typed client is generated from.

    uv run python scripts/export_openapi.py            # write ui/openapi.json
    uv run python scripts/export_openapi.py --check    # fail when it is stale (CI)

The bridge version is replaced by the contract version, so the file only
changes when the API does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "ui" / "openapi.json"
CONTRACT_VERSION = "1.0.0"


def render() -> str:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from sendspin_bridge.api.app import create_app

    app = create_app(config={"AUTH_ENABLED": False, "SECRET_KEY": "openapi-export"}, serve_spa=False)
    schema = app.openapi()
    schema["info"]["version"] = CONTRACT_VERSION
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when ui/openapi.json is out of date")
    args = parser.parse_args()
    document = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != document:
            print("ui/openapi.json is stale: run `uv run python scripts/export_openapi.py`", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(document, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
