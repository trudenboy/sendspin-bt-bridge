#!/usr/bin/env python3
"""Write sync-contract v1, the JSON Schema of the group sync tuning resources.

    uv run python scripts/export_sync_contract.py            # write docs/contracts/sync-contract-v1.json
    uv run python scripts/export_sync_contract.py --check    # fail when it is stale (CI)

The bridge's OpenAPI models are the source; a future Music Assistant provider
implements the same contract.
"""

from __future__ import annotations

import argparse
import json
import sys
import typing
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "contracts" / "sync-contract-v1.json"
REF_TEMPLATE = "#/$defs/{model}"


def build() -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from sendspin_bridge.application.models.sync import CONTRACT_MODELS, ErrorCode

    defs: dict[str, Any] = {}
    for name, model in CONTRACT_MODELS.items():
        schema = model.model_json_schema(ref_template=REF_TEMPLATE, mode="serialization")
        defs.update(schema.pop("$defs", {}))  # nested models are top-level entries too
        defs[name] = schema
    defs["ErrorCode"] = {
        "title": "ErrorCode",
        "description": "Stable `code` of the problem responses (RFC 9457) the sync endpoints return.",
        "type": "string",
        "enum": sorted(typing.get_args(ErrorCode)),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/trudenboy/sendspin-bt-bridge/docs/contracts/sync-contract-v1.json",
        "title": "sync-contract v1",
        "description": "Resources of /api/v1/sync/*; the payload of `sync` events is SyncSessionOut.",
        "$defs": dict(sorted(defs.items())),
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the contract file is out of date")
    args = parser.parse_args()
    document = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != document:
            print(f"{OUTPUT.relative_to(REPO_ROOT)} is stale: run `uv run python scripts/export_sync_contract.py`")
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(document, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
