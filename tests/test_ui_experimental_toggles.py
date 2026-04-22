"""UI-wiring regression tests for the experimental feature toggles.

These tests guard against the bug where the server config supports a key
(defaults, schema, diff classification) but the web UI has no way to flip
it — which is exactly what happened to ``EXPERIMENTAL_PAIR_JUST_WORKS``
in 2.61.0-rc.1. They are intentionally coarse string-level assertions: the
point is to catch a completely missing checkbox or missing wiring, not to
pin down exact markup.

Two experimental flags live in the Settings page's experimental section
(they require a restart when flipped, so they belong with global config):

- ``EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE``
- ``EXPERIMENTAL_PA_MODULE_RELOAD``

The NoInputNoOutput pairing-agent toggle lives in the scan modal's
toolbar instead — it's context-local to a pair attempt (next to the
pair-quiesce toggle), takes effect on the next pair without a restart,
and travels as a ``no_input_no_output_agent`` per-request override in
the ``/api/bt/pair_new`` POST body rather than being persisted via the
Settings form. The legacy ``EXPERIMENTAL_PAIR_JUST_WORKS`` config key is
still honoured as a fallback for hand-edited config.json / options.json.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "templates" / "index.html"
APP_JS = REPO_ROOT / "static" / "app.js"


SETTINGS_EXPERIMENTAL_KEYS = [
    ("EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE", "experimental-a2dp-sink-recovery-dance"),
    ("EXPERIMENTAL_PA_MODULE_RELOAD", "experimental-pa-module-reload"),
]

PAIR_AGENT_TOGGLE_ID = "experimental-no-input-no-output-agent"


@pytest.mark.parametrize(("config_key", "dom_id"), SETTINGS_EXPERIMENTAL_KEYS)
def test_settings_experimental_toggle_has_template_checkbox(config_key: str, dom_id: str) -> None:
    """Settings-page experimental flags must have a checkbox in index.html
    guarded by data-experimental so it only renders when the master
    'Show experimental features' switch is on.
    """
    html = INDEX_HTML.read_text()
    assert f'name="{config_key}"' in html, f'{config_key}: no <input name="{config_key}"> in templates/index.html'
    assert f'id="{dom_id}"' in html, f'{config_key}: no <input id="{dom_id}"> in templates/index.html'
    checkbox_block = _extract_checkbox_label_block(html, dom_id)
    assert "data-experimental" in checkbox_block, f"{config_key}: checkbox is not inside a data-experimental row"


@pytest.mark.parametrize(("config_key", "dom_id"), SETTINGS_EXPERIMENTAL_KEYS)
def test_settings_experimental_toggle_is_read_by_buildconfig(config_key: str, dom_id: str) -> None:
    """app.js must wire Settings-page experimental checkboxes into POST /api/config."""
    js = APP_JS.read_text()
    pattern = re.compile(
        rf"config\.{re.escape(config_key)}\s*=.*getElementById\(['\"]{re.escape(dom_id)}['\"]\)",
    )
    assert pattern.search(js), (
        f"{config_key}: no buildConfig read-line wiring #{dom_id} into config.{config_key} in static/app.js"
    )


@pytest.mark.parametrize(("config_key", "dom_id"), SETTINGS_EXPERIMENTAL_KEYS)
def test_settings_experimental_toggle_is_populated_on_load(config_key: str, dom_id: str) -> None:
    """app.js must populate Settings checkboxes from the fetched config."""
    js = APP_JS.read_text()
    populate_pattern = re.compile(
        rf"getElementById\(['\"]{re.escape(dom_id)}['\"]\)[\s\S]{{0,200}}?"
        rf"\.checked\s*=\s*!!\s*config\.{re.escape(config_key)}",
    )
    assert populate_pattern.search(js), (
        f"{config_key}: no populate-line reading config.{config_key} into #{dom_id} in static/app.js"
    )


# --- NoInputNoOutput pair-agent toggle: scan modal, per-pair override ---


def test_pair_agent_toggle_is_not_in_settings_experimental_section() -> None:
    """Regression: the NoInputNoOutput pair-agent toggle must NOT live
    under Settings → Show experimental features. It belongs in the scan
    modal's toolbar next to pair-quiesce because it's a per-pair context
    option, not a global restart-required setting."""
    html = INDEX_HTML.read_text()
    block = _extract_checkbox_label_block(html, PAIR_AGENT_TOGGLE_ID)
    assert "config-setting-row" not in block, (
        f"{PAIR_AGENT_TOGGLE_ID} is still living inside a Settings config-setting-row "
        "- it should be moved to the scan modal toolbar."
    )


def test_pair_agent_toggle_lives_in_scan_modal_toolbar() -> None:
    """The NoInputNoOutput pair-agent checkbox must sit inside the scan
    modal (alongside pair-quiesce) with bt-scan-toggle styling and be
    guarded by data-experimental."""
    html = INDEX_HTML.read_text()
    assert f'id="{PAIR_AGENT_TOGGLE_ID}"' in html, f"{PAIR_AGENT_TOGGLE_ID} checkbox missing from templates/index.html"
    scan_modal_start = html.find('id="bt-scan-modal-overlay"')
    scan_modal_end = html.find("</div>", html.find("bt-scan-modal-footer"))
    assert scan_modal_start != -1, "could not locate bt-scan-modal-overlay in index.html"
    scan_modal_html = html[scan_modal_start:] if scan_modal_end == -1 else html[scan_modal_start:scan_modal_end]
    assert f'id="{PAIR_AGENT_TOGGLE_ID}"' in scan_modal_html, (
        f"{PAIR_AGENT_TOGGLE_ID} is not inside the scan modal — it must live in the bt-scan-toolbar"
    )
    block = _extract_checkbox_label_block(html, PAIR_AGENT_TOGGLE_ID)
    assert "bt-scan-toggle" in block, (
        f"{PAIR_AGENT_TOGGLE_ID} must use bt-scan-toggle class to match pair-quiesce styling"
    )
    assert "data-experimental" in block, f"{PAIR_AGENT_TOGGLE_ID} must be inside a data-experimental container"


def test_pair_agent_toggle_is_not_wired_to_buildconfig() -> None:
    """The scan-modal toggle is a per-pair request override — it must
    NOT be wired into the Settings save path. Neither the old
    ``just_works`` references nor the new ``no_input_no_output_agent``
    id should appear in buildConfig."""
    js = APP_JS.read_text()
    forbidden = [
        # legacy names — must not resurface in buildConfig
        r"config\.EXPERIMENTAL_PAIR_JUST_WORKS\s*=.*getElementById",
        r"config\.EXPERIMENTAL_NO_INPUT_NO_OUTPUT_AGENT\s*=.*getElementById",
    ]
    for pat in forbidden:
        assert not re.search(pat, js), (
            f"Scan-modal toggle must not be saved via buildConfig — matched forbidden pattern: {pat!r}"
        )


def test_pair_agent_state_is_passed_in_pair_request_body() -> None:
    """pairAndAdd in app.js must read the scan-modal checkbox and include
    its state as ``no_input_no_output_agent`` in the POST body of
    /api/bt/pair_new so the server uses it as a per-request override."""
    js = APP_JS.read_text()
    idx = js.find("async function pairAndAdd(")
    assert idx != -1, "pairAndAdd not found in app.js"
    end = js.find("\nasync function ", idx + 1)
    fn_body = js[idx : end if end != -1 else len(js)]
    assert PAIR_AGENT_TOGGLE_ID in fn_body, f"pairAndAdd does not reference the {PAIR_AGENT_TOGGLE_ID} checkbox"
    assert "no_input_no_output_agent" in fn_body, (
        "pairAndAdd does not pass no_input_no_output_agent in the /api/bt/pair_new request body"
    )


def _extract_checkbox_label_block(html: str, dom_id: str) -> str:
    """Return the surrounding <label>...</label> block that contains the
    given checkbox id. Used to assert the checkbox sits inside the right
    container (data-experimental settings row, or bt-scan-toggle)."""
    needle = f'id="{dom_id}"'
    idx = html.find(needle)
    if idx == -1:
        return ""
    start = html.rfind("<label", 0, idx)
    end = html.find("</label>", idx)
    if start == -1 or end == -1:
        return ""
    return html[start : end + len("</label>")]
