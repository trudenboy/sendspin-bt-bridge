"""Public UI/config contract for advanced Bluetooth compatibility tools."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INDEX_HTML = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "templates" / "index.html"
APP_JS = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "app.js"
SCHEMA = REPO_ROOT / "src" / "sendspin_bridge" / "config" / "schema.json"


def _label_for(html: str, dom_id: str) -> str:
    index = html.index(f'id="{dom_id}"')
    start = html.rfind("<label", 0, index)
    end = html.index("</label>", index) + len("</label>")
    return html[start:end]


def test_master_toggle_is_named_advanced_compatibility_tools() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "Advanced compatibility tools" in html
    assert "Show experimental features" not in html


def test_pair_quiesce_is_stable_and_describes_disconnect() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    block = _label_for(html, "pair-quiesce-adapter")
    assert "data-experimental" not in block
    assert "Temporarily disconnect other speakers" in block
    assert "experimental" not in block.lower()


def test_hfp_is_a_per_pair_option_not_a_persisted_setting() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert 'id="pair-allow-hfp-profile"' in html
    assert 'name="ALLOW_HFP_PROFILE"' not in html
    assert "config.ALLOW_HFP_PROFILE" not in js
    assert "ALLOW_HFP_PROFILE" not in schema["properties"]


def test_legacy_global_just_works_setting_is_removed_from_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "EXPERIMENTAL_PAIR_JUST_WORKS" not in schema["properties"]


def test_room_fields_are_stable_area_metadata() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    room_name = js[js.index("<label>Room name") - 80 : js.index("<label>Room ID")]
    room_id = js[js.index("<label>Room ID") - 80 : js.index("<label>Room ID") + 360]
    assert "data-experimental" not in room_name
    assert "data-experimental" not in room_id
    assert "MA/HA/MassDroid" not in room_name + room_id


def test_cod_widget_is_contextual_not_experimental() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    start = js.index("function _buildAdapterClassOfDeviceHtml")
    end = js.index("function _bindAdapterClassOfDevice", start)
    block = js[start:end]
    assert "data-cod-context" in block
    assert "data-experimental" not in block
    assert "_applyCodContextVisibility" in js


def test_rssi_copy_matches_runtime_five_second_cadence() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "Poll every 5 s" in html
    assert "every 5 s" in schema["properties"]["RSSI_BADGE"]["description"]


def test_recovery_controls_render_capability_status() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    assert 'id="pa-reload-support"' in html
    assert 'id="adapter-recovery-support"' in html
    assert "_compatibility_capabilities" in js
    assert "_applyCompatibilityCapabilities" in js


def test_transfer_readiness_badge_uses_compact_label() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    assert "ready ? 'Transfer' : 'Not ready'" in js
    assert "Transfer ready" not in js
