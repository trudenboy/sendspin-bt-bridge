#!/usr/bin/env python3
"""Write the list of ``server.*`` UI keys the bridge's coded texts need.

The web UI's tests read ``ui/tests/fixtures/server-text-keys.json`` and check
that English and Russian define every key.  ``--check`` fails when the file is
stale (a test also does).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sendspin_bridge.services.diagnostics.server_text import registry_keys

TARGET = Path(__file__).resolve().parents[1] / "ui" / "tests" / "fixtures" / "server-text-keys.json"


def main(argv: list[str]) -> int:
    text = json.dumps(registry_keys(), indent=2) + "\n"
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text() != text:
            print(f"{TARGET} is stale: run `uv run python scripts/export_server_text_keys.py`")
            return 1
        return 0
    TARGET.write_text(text)
    print(f"wrote {len(registry_keys())} keys to {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
