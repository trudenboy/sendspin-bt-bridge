"""A DC offset or mains hum in the recording must not hide the chirps from the per-click path.

The chirps live at 2–6 kHz. The per-click check compared each chirp's correlation with the energy of
the raw segment, DC and hum included, so a cheap microphone's offset or a 50 Hz hum pushed quiet
clicks under the threshold: the most precise path heard nothing and the estimate fell back to the
coarser coherent fold.
"""

from __future__ import annotations

import math
import struct

import pytest

from sendspin_sync import estimator
from sendspin_sync.models import Recording
from tests.support.sync_synth import synth


def _with_low_frequencies(rec: Recording, *, dc: float, hum: float) -> Recording:
    n = len(rec.pcm) // 2
    x = struct.unpack(f"<{n}h", rec.pcm)
    y = [v / 32767 + dc + hum * math.sin(2 * math.pi * 50 * i / rec.sample_rate) for i, v in enumerate(x)]
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, v * 32767))) for v in y))
    return Recording(member=rec.member, start_time_s=rec.start_time_s, sample_rate=rec.sample_rate, pcm=pcm)


@pytest.mark.parametrize(("dc", "hum"), [(0.2, 0.0), (0.0, 0.3), (0.2, 0.3)])
def test_quiet_clicks_under_dc_or_hum_are_measured_per_click(dc, hum):
    rec = _with_low_frequencies(synth("a", lag_s=0.030, gain=0.05), dc=dc, hum=hum)

    m = estimator.estimate_per_click(rec)

    assert m.status == "ok", m.reason
    assert m.phase_ms == pytest.approx(30.0, abs=1.0)
