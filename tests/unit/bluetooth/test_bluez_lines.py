"""Line hygiene for bluetoothctl output: ANSI strip, prompt grammars, events.

Both existing consumers are reproduced from one classification:

* the connect-summary blacklist (``bluetooth/manager.py`` today) drops
  prompts, async ``[CHG]/[NEW]/[DEL]`` notifications and the agent banner,
  keeping everything else as CONTENT;
* the paired-list whitelist (``web/routes/api_bt.py`` today) keeps only
  CONTENT lines whose text starts with ``Device ``.
"""

from __future__ import annotations

from sendspin_bridge.bluetooth.bluez import LineKind, classify_line, classify_lines


def test_empty_and_whitespace_lines_are_empty_kind():
    assert classify_line("").kind is LineKind.EMPTY
    assert classify_line("   \t  ").kind is LineKind.EMPTY
    assert classify_line("\x1b[0;94m\x1b[0m   ").kind is LineKind.EMPTY


def test_bare_bluetooth_prompt_is_prompt_kind():
    line = classify_line("\x1b[0;94m[bluetooth]\x1b[0m#                         ")
    assert line.kind is LineKind.PROMPT
    assert line.text == ""


def test_prompt_echoed_command_is_prompt_kind():
    line = classify_line("\x1b[0;94m[bluetooth]\x1b[0m#             show")
    assert line.kind is LineKind.PROMPT


def test_bluetoothctl_gt_prompt_is_prompt_kind():
    line = classify_line("[\x1b[0;94mbluetoothctl\x1b[0m]> \x1b[0m")
    assert line.kind is LineKind.PROMPT


def test_waiting_banner_glued_to_prompt_is_prompt_kind():
    # Piped-stdin startup: the banner and the first prompt share one line.
    line = classify_line("Waiting to connect to bluetoothd...\x1b[0;94m[bluetooth]\x1b[0m#")
    assert line.kind is LineKind.PROMPT


def test_agent_registered_is_banner():
    assert classify_line("Agent registered").kind is LineKind.BANNER
    assert classify_line("agent registered").kind is LineKind.BANNER


def test_device_named_prompt_with_echo_stays_content():
    # A prompt named after the default device is only recognisable as a
    # prompt when it is bare; with an echoed command glued on, the
    # historical connect-summary consumer kept the line. BluezLine keeps
    # that behaviour so the summary fallback is unchanged.
    line = classify_line("[ENEBY20]# show")
    assert line.kind is LineKind.CONTENT


def test_async_events_are_classified_with_mac():
    chg = classify_line("[\x1b[0;93mCHG\x1b[0m] Device 68:3A:48:D3:62:68 RSSI: 0xffffffaa (-86)")
    assert chg.kind is LineKind.EVENT_CHG
    assert chg.event_mac == "68:3A:48:D3:62:68"

    new = classify_line("[NEW] Device 11:22:33:44:55:66 Ghost Discovery")
    assert new.kind is LineKind.EVENT_NEW
    assert new.event_mac == "11:22:33:44:55:66"

    dele = classify_line("[DEL] Device 11:22:33:44:55:66 Ghost Discovery")
    assert dele.kind is LineKind.EVENT_DEL
    assert dele.event_mac == "11:22:33:44:55:66"


def test_plain_content_line():
    line = classify_line("Device 6C:5C:3D:35:17:99 ENEBY Portable")
    assert line.kind is LineKind.CONTENT
    assert line.text == "Device 6C:5C:3D:35:17:99 ENEBY Portable"
    assert line.event_mac is None


def test_gt_prompt_prefix_stripped_from_text():
    line = classify_line("[\x1b[0;94mENEBY20\x1b[0m]> \x1b[0mdevices Paired")
    assert line.text == "devices Paired"


def test_event_lines_keep_bracket_in_text():
    # The ``]>``-grammar strip must not eat ``[CHG]`` style prefixes —
    # downstream whitelist checks rely on them failing ``Device `` tests.
    line = classify_line("[CHG] Device 7F:13:03:93:77:DF RSSI: 0xffffffb3 (-77)")
    assert line.text.startswith("[CHG]")


def test_classify_lines_skips_nothing_and_preserves_order():
    out = classify_lines("[NEW] Device 11:22:33:44:55:66 X\n\nDevice 6C:5C:3D:35:17:99 ENEBY\n")
    assert [line.kind for line in out] == [LineKind.EVENT_NEW, LineKind.EMPTY, LineKind.CONTENT]
