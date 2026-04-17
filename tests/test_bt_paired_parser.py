"""Robustness of ``_parse_paired_stdout`` against real-world bluetoothctl noise.

Interactive ``bluetoothctl`` emits asynchronous discovery notifications
(``[CHG] Device <mac> RSSI: …``, ``[NEW] Device …``, ``[DEL] Device …``,
``[CHG] Device <mac> ManufacturerData.Key: …``, etc.) to the same stdout
stream while a caller pipes in ``select <mac>\\ndevices Paired\\n``. The
parser used to accept any line containing ``Device <mac> <rest>``, so
these async notifications leaked into the Already-Paired list — users on
HAOS saw rows whose ``bluetoothctl info`` actually reported ``Paired: no``.

Only lines that are literally a *response* to ``devices Paired`` count:
- start with ``Device <mac>`` (no ``[CHG]``/``[NEW]``/``[DEL]`` bracket);
- no residual control keywords (``RSSI:``, ``ManufacturerData.*``) in the
  name portion.
"""

from __future__ import annotations

from routes.api_bt import _parse_paired_stdout


def test_parse_ignores_chg_rssi_async_notifications():
    stdout = (
        "[\x1b[0;94mbluetoothctl]> \x1b[0m"
        "[\x1b[0;93mCHG\x1b[0m] Device 68:3A:48:D3:62:68 RSSI: 0xffffffaa (-86)\n"
        "[\x1b[0;93mCHG\x1b[0m] Device 7F:13:03:93:77:DF RSSI: 0xffffffb3 (-77)\n"
        "Device FC:58:FA:EB:08:6C ENEBY20\n"
        "Device 30:21:0E:0A:AE:5A Lenco LS-500\n"
    )
    result = _parse_paired_stdout(stdout)
    assert sorted(result) == sorted(
        [
            ("FC:58:FA:EB:08:6C", "ENEBY20"),
            ("30:21:0E:0A:AE:5A", "Lenco LS-500"),
        ]
    )


def test_parse_ignores_chg_manufacturerdata_and_multiline_hex_dumps():
    stdout = (
        "[CHG] Device 54:66:39:DC:B9:4D RSSI: 0xffffffb0 (-80)\n"
        "[CHG] Device 54:66:39:DC:B9:4D ManufacturerData.Key: 0x004c (76)\n"
        "[CHG] Device 54:66:39:DC:B9:4D ManufacturerData.Value:\n"
        "  01 00 00 00 00 00 00 00 00 00 00 00 00 80 00 00  ................\n"
        "  00                                               .               \n"
        "Device AA:BB:CC:DD:EE:FF Real Speaker\n"
    )
    result = _parse_paired_stdout(stdout)
    assert result == [("AA:BB:CC:DD:EE:FF", "Real Speaker")]


def test_parse_ignores_new_and_del_notifications():
    stdout = (
        "[NEW] Device 11:22:33:44:55:66 Ghost Discovery\n"
        "[DEL] Device 11:22:33:44:55:66 Ghost Discovery\n"
        "Device AA:BB:CC:DD:EE:01 Real Speaker\n"
    )
    result = _parse_paired_stdout(stdout)
    assert result == [("AA:BB:CC:DD:EE:01", "Real Speaker")]


def test_parse_accepts_ansi_coloured_prompt_prefix_on_real_line():
    """Prompt echo (``[ENEBY20]> ``) may prefix the first real line."""
    stdout = "\x1b[0;94m[ENEBY20]> \x1b[0mdevices Paired\nDevice FC:58:FA:EB:08:6C ENEBY20\n"
    result = _parse_paired_stdout(stdout)
    assert result == [("FC:58:FA:EB:08:6C", "ENEBY20")]
