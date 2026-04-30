"""Static guard against undefined globals in shipped JS.

The ``_markConfigDirty`` typo lurked in ``app.js`` from the 28-Apr
commit until the v2.66.13 standalone MQTT-probe path made it
reachable — at which point the operator saw "Probe failed:
_markConfigDirty is not defined" instead of the expected suggestion
hint.  This test would have caught it on the first commit.

The check is intentionally narrow: it only complains about names
the project itself defines elsewhere using a near-miss spelling.
Anything truly external (browser globals, JSON.parse, etc.) is fine.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_JS = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "app.js"


# Functions we know don't exist but were once mistakenly referenced.
# Add to this list any time a similar typo bug ships and gets fixed.
_KNOWN_TYPO_REFERENCES = ("_markConfigDirty",)


def test_app_js_has_no_known_typo_function_references():
    body = APP_JS.read_text(encoding="utf-8")
    for name in _KNOWN_TYPO_REFERENCES:
        # Match standalone identifier — not as substring of a longer name.
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        matches = pattern.findall(body)
        assert not matches, (
            f"app.js references {name!r} but no such function is defined; "
            f"this would throw 'Can't find variable: {name}' at runtime. "
            f"Replace with the correct helper (likely _recomputeConfigDirtyState)."
        )
