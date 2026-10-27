"""GstPortClock against a real pulsesink on a null sink (needs pactl + GStreamer)."""

from __future__ import annotations

import subprocess
import time

import pytest

from sendspin_bridge.services.audio.playout.sensor import GstPortClock

pytestmark = pytest.mark.integration

SINK = "playout_test"
BLOCK_FRAMES = 480  # 10 ms at 48 kHz
BLOCK_BYTES = BLOCK_FRAMES * 4  # S16LE stereo
LEAD_NS = 40_000_000
RUN_S = 10.0


def _pactl_available() -> bool:
    try:
        return subprocess.run(["pactl", "info"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _gst_available() -> bool:
    try:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        return Gst.ElementFactory.make("pulsesink", None) is not None
    except Exception:
        return False


def _on_pipewire() -> bool:
    out = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=5).stdout
    return "PipeWire" in out


requires_env = pytest.mark.skipif(
    not (_pactl_available() and _gst_available()), reason="needs a working pactl and GStreamer with pulsesink"
)


@pytest.fixture
def null_sink():
    out = subprocess.run(
        ["pactl", "load-module", "module-null-sink", f"sink_name={SINK}"], capture_output=True, text=True, check=True
    )
    module_id = out.stdout.strip()
    try:
        yield SINK
    finally:
        subprocess.run(["pactl", "unload-module", module_id], capture_output=True)


@requires_env
def test_sensor_tracks_the_pulsesink_clock(null_sink):
    from sendspin_bridge.services.audio.player.gst_support import Gst

    pipeline = Gst.parse_launch(
        "appsrc name=src is-live=true format=time "
        'caps="audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved" '
        "! audioconvert ! audioresample "
        '! capsfilter caps="audio/x-raw,format=S16LE,rate=48000,channels=2" '
        f"! pulsesink name=sink device={null_sink} sync=false provide-clock=true "
        "slave-method=none buffer-time=600000"
    )
    pipeline.use_clock(Gst.SystemClock.obtain())
    src = pipeline.get_by_name("src")
    sink = pipeline.get_by_name("sink")
    sensor = GstPortClock(sink)
    silence = bytes(BLOCK_BYTES)
    block_ns = 10_000_000
    pushed = 0
    samples = []
    try:
        assert pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
        start = time.monotonic()
        next_read = start
        while time.monotonic() - start < RUN_S:
            # keep appsrc about 40 ms full
            while pushed * block_ns < (time.monotonic() - start) * 1e9 + LEAD_NS:
                buf = Gst.Buffer.new_wrapped(silence)
                buf.pts = pushed * block_ns
                buf.duration = block_ns
                src.emit("push-buffer", buf)
                pushed += 1
            if time.monotonic() >= next_read:
                sample = sensor.read()
                if sample is not None:
                    samples.append(sample)
                next_read += 0.1
            time.sleep(0.005)
    finally:
        pipeline.set_state(Gst.State.NULL)

    print(f"reads={len(samples)} rejected={sensor.rejected}")
    assert len(samples) >= 90
    # least-squares slope of (audio clock - raw clock) over the run, in ppm
    xs = [s.t_us for s in samples]
    ys = [s.a_ns / 1000.0 - s.t_us for s in samples]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sum((x - mx) ** 2 for x in xs)
    slope_ppm = slope * 1e6
    print(f"slope={slope_ppm:.1f} ppm")
    if _on_pipewire():
        # pipewire-pulse's stream timing is coarse: the clock sawtooths by tens of ms (measured about
        # -1100 ppm over 10 s), so the rate bound only holds on a native PulseAudio server (HAOS).
        pytest.skip(f"slope {slope_ppm:.0f} ppm not asserted on PipeWire (reads={len(samples)})")
    assert abs(slope_ppm) < 200
