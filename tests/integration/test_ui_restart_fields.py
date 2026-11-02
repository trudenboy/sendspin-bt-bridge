"""The Settings page marks sections whose fields need a restart.

The list lives twice by necessity (Python classifies the change, TypeScript
renders the note), so this test makes the copy falsifiable instead of letting
the two drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

from sendspin_bridge.services.infrastructure.config_diff import iter_restart_required_fields

REPO_ROOT = Path(__file__).resolve().parents[2]
LAYOUT = REPO_ROOT / "ui" / "src" / "settings" / "layout.ts"


def test_the_ui_restart_field_list_matches_the_backend() -> None:
    text = LAYOUT.read_text()
    start = text.index("export const RESTART_REQUIRED_FIELDS")
    end = text.index("])", start)
    keys = set(re.findall(r"'([A-Z0-9_]+)'", text[start:end]))
    assert keys == set(iter_restart_required_fields())
