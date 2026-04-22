"""UI-wiring regression tests for the experimental feature toggles.

These tests guard against the bug where the server config supports a key
(defaults, schema, diff classification) but the web UI has no checkbox to
flip it — which is exactly what happened to ``EXPERIMENTAL_PAIR_JUST_WORKS``
in 2.61.0-rc.1. They are intentionally coarse string-level assertions: the
point is to catch a completely missing checkbox or a missing load/save line
in ``static/app.js``, not to pin down exact markup.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "templates" / "index.html"
APP_JS = REPO_ROOT / "static" / "app.js"


EXPERIMENTAL_KEYS = [
    ("EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE", "experimental-a2dp-sink-recovery-dance"),
    ("EXPERIMENTAL_PA_MODULE_RELOAD", "experimental-pa-module-reload"),
    ("EXPERIMENTAL_PAIR_JUST_WORKS", "experimental-pair-just-works"),
]


@pytest.mark.parametrize(("config_key", "dom_id"), EXPERIMENTAL_KEYS)
def test_experimental_toggle_has_template_checkbox(config_key: str, dom_id: str) -> None:
    """Each experimental flag must have a checkbox in index.html, guarded
    by data-experimental so it only renders when 'Show experimental features'
    is on.
    """
    html = INDEX_HTML.read_text()
    # The checkbox must be present by both name= (for form-serialization) and id=
    # (for app.js direct lookup).
    assert f'name="{config_key}"' in html, f'{config_key}: no <input name="{config_key}"> in templates/index.html'
    assert f'id="{dom_id}"' in html, f'{config_key}: no <input id="{dom_id}"> in templates/index.html'
    # It must sit inside a data-experimental container so the toggle only
    # shows up when the user opted into experimental features.
    checkbox_block = _extract_checkbox_label_block(html, dom_id)
    assert "data-experimental" in checkbox_block, f"{config_key}: checkbox is not inside a data-experimental row"


@pytest.mark.parametrize(("config_key", "dom_id"), EXPERIMENTAL_KEYS)
def test_experimental_toggle_is_read_by_buildconfig(config_key: str, dom_id: str) -> None:
    """app.js must assign ``config.<KEY> = !!(document.getElementById('<dom_id>') || {}).checked``
    (or equivalent) so the toggle state round-trips into POST /api/config."""
    js = APP_JS.read_text()
    pattern = re.compile(
        rf"config\.{re.escape(config_key)}\s*=.*getElementById\(['\"]{re.escape(dom_id)}['\"]\)",
    )
    assert pattern.search(js), (
        f"{config_key}: no buildConfig read-line wiring #{dom_id} into config.{config_key} in static/app.js"
    )


@pytest.mark.parametrize(("config_key", "dom_id"), EXPERIMENTAL_KEYS)
def test_experimental_toggle_is_populated_on_load(config_key: str, dom_id: str) -> None:
    """app.js must also populate the checkbox state from the fetched config
    so refreshing the settings page reflects the persisted value."""
    js = APP_JS.read_text()
    populate_pattern = re.compile(
        rf"getElementById\(['\"]{re.escape(dom_id)}['\"]\)"
        rf"[\s\S]{{0,200}}?"
        rf"\.checked\s*=\s*!!\s*config\.{re.escape(config_key)}",
    )
    assert populate_pattern.search(js), (
        f"{config_key}: no populate-line reading config.{config_key} into #{dom_id} in static/app.js"
    )


def _extract_checkbox_label_block(html: str, dom_id: str) -> str:
    """Return the surrounding <label>...</label> block that contains the given
    checkbox id. Used to assert the checkbox sits inside a data-experimental
    container.
    """
    needle = f'id="{dom_id}"'
    idx = html.find(needle)
    if idx == -1:
        return ""
    # Walk backwards to the nearest <label
    start = html.rfind("<label", 0, idx)
    end = html.find("</label>", idx)
    if start == -1 or end == -1:
        return ""
    return html[start : end + len("</label>")]
