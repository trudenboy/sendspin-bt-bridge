from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INDEX_HTML = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "templates" / "index.html"
APP_JS = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "app.js"


def test_pairing_setting_is_wired_through_the_settings_ui():
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")

    assert 'name="SENDSPIN_PAIRING"' in html
    assert "config.SENDSPIN_PAIRING = !!(document.getElementById('sendspin-pairing')" in js
    assert "sendspinPairingCheck.checked = !!config.SENDSPIN_PAIRING" in js


def test_pairing_action_uses_stable_player_id_and_handles_api_errors():
    js = APP_JS.read_text(encoding="utf-8")
    function_body = js.split("async function openSendspinPairing(i) {", 1)[1].split("// ---- BT Actions", 1)[0]

    assert "JSON.stringify({player_id: playerId})" in function_body
    assert "JSON.stringify({player_name: playerName})" not in function_body
    assert "if (!dev.sendspin_pairing || dev.enabled === false)" in function_body
    assert "showToast((data && data.error)" in function_body


def test_pairing_modal_tracks_status_by_player_id_in_card_and_list_views():
    js = APP_JS.read_text(encoding="utf-8")

    assert "_pairingModal.playerId && dev.player_id !== _pairingModal.playerId" in js
    assert "_updatePairingModalFromDevice(dev);" in js
    assert "entries.forEach(function(entry) { _updatePairingModalFromDevice(entry.dev); });" in js
    assert "ssPairBtn.disabled = !dev.sendspin_pairing || _isDeviceDisabled(dev);" in js
