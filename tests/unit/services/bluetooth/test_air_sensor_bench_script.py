"""``scripts/air_sensor_bench.py``: one pass over the fixture yields the live-path batches and a cost line."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "air_sensor_bench.py"
FIXTURE = ROOT / "tests" / "fixtures" / "a2dp_air" / "eneby_clicks.btsnoop.gz"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("air_sensor_bench", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["air_sensor_bench"] = module
    spec.loader.exec_module(module)
    return module


def test_one_pass_produces_the_link_batches(bench):
    records = bench.load(FIXTURE)
    frames = bench.run_once(records, check_crc="first")
    assert frames == {(1, 70): 2065}  # every SBC frame of the click link reaches a batch


def test_cli_prints_cost_per_record_and_core_fraction(bench, capsys):
    assert bench.main([str(FIXTURE), "--repeat", "1"]) == 0
    out = capsys.readouterr().out
    assert "us/record" in out and "core fraction at 150 records/s" in out
