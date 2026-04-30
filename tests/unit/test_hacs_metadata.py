"""Guard rails for HACS-facing metadata at the repo root.

Originally HACS rendered the entire repository ``README.md`` as the
integration's description — the v2.66.10-era report was that the
description page was overwhelming and not specific to the integration.

The fix is to keep ``render_readme`` *unset* (or false) and ship a
short ``info.md`` focused on the custom_component.  This regression
test stops a future ``hacs.json`` edit from re-introducing the
verbose-readme behaviour silently.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HACS_JSON = REPO_ROOT / "hacs.json"
INFO_MD = REPO_ROOT / "info.md"


def test_hacs_json_does_not_render_full_readme():
    data = json.loads(HACS_JSON.read_text(encoding="utf-8"))
    assert data.get("render_readme", False) is False, (
        "hacs.json must not enable render_readme — it dumps the entire "
        "repo README into the HACS card. Use info.md for the focused "
        "integration description instead."
    )


def test_info_md_exists_and_describes_integration():
    """``info.md`` is what HACS shows when render_readme is off.  Keep
    a non-trivial file at the repo root (HACS reads it from there)."""
    assert INFO_MD.exists(), "info.md missing — HACS card will fall back to a generic placeholder"
    body = INFO_MD.read_text(encoding="utf-8")
    assert len(body) > 200, "info.md exists but is suspiciously short"
    # Sanity: must mention what this integration does.
    body_lower = body.lower()
    assert "home assistant" in body_lower
    assert "custom_component" in body_lower or "integration" in body_lower
