"""``scripts/hci_monitor_probe.py`` is a standalone copy-in probe; keep its constants in step."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from sendspin_bridge.services.bluetooth import hci_socket as hs

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "hci_monitor_probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("hci_monitor_probe", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["hci_monitor_probe"] = module
    spec.loader.exec_module(module)
    return module


def test_constants_match_the_bridge(probe):
    names = (
        "AF_BLUETOOTH",
        "BTPROTO_HCI",
        "HCI_CHANNEL_MONITOR",
        "HCI_DEV_NONE",
        "SO_RCVBUFFORCE",
        "SO_TIMESTAMPNS",
        "SCM_TIMESTAMPNS",
        "SO_RXQ_OVFL",
        "SO_MEMINFO",
        "SK_MEMINFO_DROPS",
        "SK_MEMINFO_VARS",
    )
    assert {n: getattr(probe, n) for n in names} == {n: getattr(hs, n) for n in names}


def test_main_reports_an_unopenable_socket(probe, monkeypatch, capsys):
    def refuse():
        raise OSError(1, "bind(HCI_CHANNEL_MONITOR) failed (needs CAP_NET_RAW)")

    monkeypatch.setattr(probe, "open_monitor", refuse)
    assert probe.main(["--seconds", "0"]) == 2
    assert "cannot open the HCI monitor socket" in capsys.readouterr().out


def test_probe_counts_cmsgs_and_reads_meminfo_drops(probe):
    import socket
    import struct

    class FakeMonitor:
        def __init__(self):
            self.records = [
                [(socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("qq", 1, 2))],
                [(socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("qq", 1, 3))],
            ]

        def setsockopt(self, level, opt, value):
            if opt == hs.SO_RCVBUFFORCE:
                raise PermissionError(1, "Operation not permitted")

        def settimeout(self, value):
            pass

        def getsockopt(self, level, opt, buflen):
            return struct.pack("9I", 0, 0, 0, 0, 0, 0, 0, 0, 4)

        def recvmsg(self, bufsize, ancbufsize):
            if not self.records:
                raise TimeoutError
            return b"\x00" * 6, self.records.pop(), 0, None

    result = probe.probe(FakeMonitor(), 0.05)
    assert result["records"] == 2
    assert result["scm_timestampns_cmsgs"] == 2
    assert result["so_rxq_ovfl_cmsgs"] == 0
    assert result["drop_path"] == "meminfo"
    assert result["meminfo_drops_end"] == 4
    assert result["sockopts"]["SO_RCVBUFFORCE"].startswith("no")
    assert result["sockopts"]["SO_RCVBUF"] == "yes"
    assert result["bracket_pairs"] >= 1
