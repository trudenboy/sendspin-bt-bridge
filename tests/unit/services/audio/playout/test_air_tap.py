"""WrittenEnvelope: the air sensor's tap on the input PCM the playout engine writes (Task A7)."""

from __future__ import annotations

import math
import struct

import pytest

from sendspin_bridge.services.audio.playout.air_tap import (
    LOOKBACK_FRAMES,
    WrittenEnvelope,
    comb_lag,
    comb_peak_log2,
    to_s16,
)
from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import Block, InputFormat
from tests.unit.services.audio.playout.test_gst_output import (
    PCM_S16_STEREO_48K,
    _player,
    _TailRig,
    requires_gst,
)


def interleave(left, right=None):
    return [v for pair in zip(left, right or left, strict=True) for v in pair]


def s16(vals):
    return struct.pack(f"<{len(vals)}h", *vals)


def s24(vals):
    return b"".join(int(v * 256).to_bytes(3, "little", signed=True) for v in vals)


def s32(vals):
    return b"".join(int(v * 65536).to_bytes(4, "little", signed=True) for v in vals)


def ref_comb(xs, prev, lag):
    full = list(prev) + list(xs)
    return math.log2(1 + max(abs(full[n] - full[n - lag]) for n in range(lag, len(full))))


@pytest.mark.parametrize(("pack", "nbytes"), [(s16, 2), (s24, 3), (s32, 4)])
def test_formats_reduce_to_the_same_16_bit_samples(pack, nbytes):
    vals = interleave([1000, -2000, 32000, -32768])
    assert list(to_s16(pack(vals), nbytes)) == vals


def test_comb_lag_per_rate():
    assert (comb_lag(44100), comb_lag(48000)) == (3, 3)


def test_comb_matches_a_plain_reference():
    xs = [int(8000 * math.sin(2 * math.pi * 7350 * n / 44100)) for n in range(128)]
    got = comb_peak_log2(to_s16(s16(interleave(xs)), 2), 2, 0, 128, (0,) * 6, 3)
    assert got == pytest.approx(ref_comb(xs, [0, 0, 0], 3), abs=1e-9)


def test_comb_uses_the_history_of_each_channel():
    left = [100 * n for n in range(16)]
    right = [-50 * n for n in range(16)]
    prev = (7, -7, 9, -9, 11, -11)  # three frames, interleaved
    got = comb_peak_log2(to_s16(s16(interleave(left, right)), 2), 2, 0, 16, prev, 3)
    want = max(ref_comb(left, [7, 9, 11], 3), ref_comb(right, [-7, -9, -11], 3))
    assert got == pytest.approx(want, abs=1e-9)


def test_comb_rejects_bass_and_passes_the_band():
    lo = [int(8000 * math.sin(2 * math.pi * 200 * n / 44100)) for n in range(1280)]
    hi = [int(8000 * math.sin(2 * math.pi * 5000 * n / 44100)) for n in range(1280)]

    def f(xs):
        return comb_peak_log2(to_s16(s16(interleave(xs)), 2), 2, 0, 1280, (0,) * 6, 3)

    assert f(hi) - f(lo) > 4.0


def block(fmt, x0_ns, frames, value=1000, program=True):
    pack = {2: s16, 3: s24, 4: s32}[fmt.sample_bytes]
    return Block(
        pcm=pack(interleave([value if n % 2 else -value for n in range(frames)])),
        x0_ns=x0_ns,
        duration_ns=round(frames * 1e9 / fmt.sample_rate),
        program=program,
        dropped=0,
        inserted=0,
        late_drop_us=0.0,
    )


F16, F24 = InputFormat(44100, 2, 2), InputFormat(48000, 2, 3)


def test_cells_carry_s_t_kind_and_mute():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 441), F16, lambda x: 1e6 + x / 1000.0, lambda x: "program", d_out_us=180_000.0)
    sl = tap.slice(0, 3)
    assert sl.cell_us == pytest.approx(128 / 44100 * 1e6)
    assert list(sl.s) == pytest.approx([1e6 + i * sl.cell_us for i in range(3)], abs=0.05)
    assert list(sl.t) == pytest.approx([1e6 + 180_000.0 + i * sl.cell_us for i in range(3)], abs=0.05)
    assert sl.program == b"\x01\x01\x01" and sl.muted == b"\x00\x00\x00"


def test_cell_value_is_the_comb_peak_of_the_cell():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 256, value=1000), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    sl = tap.slice(0, 2)
    # +-1000 alternating: x[n] - x[n-3] = +-2000 (the first cell starts from a zero history).
    assert list(sl.w) == pytest.approx([math.log2(2001)] * 2, abs=1e-5)


def test_24_bit_48k_input_is_supported():
    tap = WrittenEnvelope()
    tap.add_block(block(F24, 0, 480), F24, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.unsupported is None and tap.cell_us == pytest.approx(128 / 48000 * 1e6)
    assert tap.newest_cell() == 2


@pytest.mark.parametrize(
    ("rate", "nbytes", "reason"),
    [(96000, 3, "input:96000/24"), (88200, 2, "input:88200/16"), (44100, 1, "input:44100/8")],
)
def test_other_inputs_are_unsupported(rate, nbytes, reason):
    tap = WrittenEnvelope()
    fmt = InputFormat(rate, 2, nbytes)
    tap.add_block(
        Block(
            pcm=bytes(nbytes * 2 * 256), x0_ns=0, duration_ns=1, program=True, dropped=0, inserted=0, late_drop_us=0.0
        ),
        fmt,
        lambda x: 0.0,
        lambda x: "program",
        d_out_us=0.0,
    )
    assert tap.unsupported == reason and tap.newest_cell() is None
    assert tap.cell_us is None


def test_a_supported_format_after_an_unsupported_one_records_again():
    tap = WrittenEnvelope()
    hi = InputFormat(96000, 2, 3)
    tap.add_block(block(hi, 0, 512), hi, lambda x: 0.0, lambda x: "program", d_out_us=0.0)
    tap.add_block(block(F16, 0, 256), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.unsupported is None and tap.newest_cell() == 1


def test_mute_is_resolved_by_port_time_not_push_time():
    """Cells are pushed ~0.8 s before their port time; a mute applied after the push still covers them."""
    tap = WrittenEnvelope()

    def port(x):
        return 800_000.0 + x / 1000.0  # s = push origin + 0.8 s

    tap.add_block(block(F16, 0, 4 * 128), F16, port, lambda x: "program", d_out_us=0.0)
    cell = 128 / 44100 * 1e6
    tap.mute_transition(800_000.0 + 2 * cell, True)  # applied later, acts at the port
    sl = tap.slice(0, 4)
    assert sl.muted == b"\x00\x00\x01\x01"
    assert all(v > 0 for v in sl.w)  # the PCM is unchanged by mute


def test_mute_that_ends_before_the_port_does_not_count():
    tap = WrittenEnvelope()
    tap.mute_transition(0.0, True)
    tap.mute_transition(100_000.0, False)
    tap.add_block(block(F16, 0, 256), F16, lambda x: 800_000.0 + x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.slice(0, 2).muted == b"\x00\x00"


def test_the_mute_timeline_keeps_30_seconds_and_the_state_before_them():
    tap = WrittenEnvelope()
    tap.mute_transition(0.0, True)  # still the state 60 s later: nothing since
    for k in range(1, 7):
        tap.mute_transition(60e6 + k * 10e6, k % 2 == 0)  # 70 s .. 120 s, alternating
    times = tap.mute_timeline()
    assert times[0][0] >= 120e6 - 30e6 - 10e6  # the newest transition before the 30 s cutoff stays
    assert times[-1] == (120e6, True)
    assert len(times) <= 5


def test_mute_survives_a_reset():
    """The player records a transition only when the effective mute changes: a reopen keeps it."""
    tap = WrittenEnvelope()
    tap.mute_transition(0.0, True)
    tap.reset()
    tap.add_block(block(F16, 0, 128), F16, lambda x: 1e6 + x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.slice(0, 1).muted == b"\x01"


def test_silence_cells_are_zero():
    tap = WrittenEnvelope()
    tap.add_block(
        block(F16, 0, 256, value=0, program=False), F16, lambda x: x / 1000.0, lambda x: "silence", d_out_us=0.0
    )
    sl = tap.slice(0, 2)
    assert sl.program == b"\x00\x00" and list(sl.w) == [0.0, 0.0]


def test_kind_is_taken_per_cell_and_silence_cells_carry_no_energy():
    tap = WrittenEnvelope()
    edge = round(128e9 / 44100)  # the second cell starts a silence segment
    tap.add_block(
        block(F16, 0, 256), F16, lambda x: x / 1000.0, lambda x: "program" if x < edge else "silence", d_out_us=0.0
    )
    sl = tap.slice(0, 2)
    assert sl.program == b"\x01\x00"
    assert sl.w[0] > 0 and sl.w[1] == 0.0


def test_unknown_schedule_is_nan():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 128), F16, lambda x: None, lambda x: None, d_out_us=5.0)
    sl = tap.slice(0, 1)
    assert math.isnan(sl.s[0]) and math.isnan(sl.t[0])
    assert sl.program == b"\x00" and sl.muted == b"\x00"


def test_cells_split_across_blocks_are_whole():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 100), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.newest_cell() is None
    tap.add_block(block(F16, round(100e9 / 44100), 156), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.newest_cell() == 1


def test_a_split_cell_has_the_schedule_of_its_first_frame_and_the_peak_of_both_parts():
    tap = WrittenEnvelope()
    quiet = block(F16, 0, 100, value=10)
    loud = block(F16, round(100e9 / 44100), 28, value=3000)
    tap.add_block(quiet, F16, lambda x: 5e5 + x / 1000.0, lambda x: "program", d_out_us=0.0)
    tap.add_block(loud, F16, lambda x: 5e5 + x / 1000.0, lambda x: "program", d_out_us=0.0)
    sl = tap.slice(0, 1)
    assert sl.s[0] == pytest.approx(5e5)
    # Across the block edge the comb sees the quiet history: |3000 - (-10)| at most.
    assert sl.w[0] == pytest.approx(math.log2(1 + 6000), abs=0.01)


def test_comb_is_continuous_across_blocks():
    """One block split in two gives the same cells as the whole block."""
    xs = [int(8000 * math.sin(2 * math.pi * 3000 * n / 44100)) for n in range(1000)]
    pcm = s16(interleave(xs))

    def mk(a, b):
        return Block(
            pcm=pcm[a * 4 : b * 4],
            x0_ns=round(a * 1e9 / 44100),
            duration_ns=round((b - a) * 1e9 / 44100),
            program=True,
            dropped=0,
            inserted=0,
            late_drop_us=0.0,
        )

    whole, split = WrittenEnvelope(), WrittenEnvelope()
    whole.add_block(mk(0, 1000), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    split.add_block(mk(0, 301), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    split.add_block(mk(301, 1000), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    n = whole.newest_cell()
    assert split.newest_cell() == n
    assert list(split.slice(0, n + 1).w) == list(whole.slice(0, n + 1).w)


def test_lookup_handles_s_stepping_back():
    """A placement insert makes s go back: the newest matching cell wins, no binary search."""
    tap = WrittenEnvelope()

    def sched(x):  # s steps back 100 ms at 0.5 s
        return x / 1000.0 if x < 500_000_000 else x / 1000.0 - 100_000.0

    tap.add_block(block(F16, 0, 44100), F16, sched, lambda x: "program", d_out_us=0.0)
    i = tap.cell_near(450_000.0, around=tap.newest_cell())
    assert tap.slice(i, i + 1).s[0] == pytest.approx(450_000.0, abs=tap.cell_us / 2)
    assert i * tap.cell_us > 500_000  # the later, re-scheduled cell


def test_lookup_outside_the_span_finds_nothing():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 44100), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.cell_near(900_000.0, around=0, span_s=0.1) is None
    assert tap.cell_near(5e6, around=tap.newest_cell()) is None


def test_reset_clears_the_ring():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 4410), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    tap.reset()
    assert tap.newest_cell() is None
    tap.add_block(block(F16, 0, 128), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.newest_cell() == 0


def test_ring_forgets_old_cells():
    tap = WrittenEnvelope(seconds=1.0)
    for k in range(30):
        tap.add_block(
            block(F16, round(k * 4410e9 / 44100), 4410), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0
        )
    n = tap.newest_cell()
    assert tap.slice(0, 10) is None and tap.slice(n - 10, n) is not None
    sl = tap.slice(n - 10, n + 1)
    assert sl.i0 == n - 10 and len(sl.w) == 11
    assert list(sl.s) == pytest.approx([(n - 10 + k) * sl.cell_us for k in range(11)], abs=0.05)
    assert tap.slice(n, n + 2) is None  # beyond the newest cell


def test_a_failed_tap_reports_internal_error_and_records_nothing_until_reset():
    tap = WrittenEnvelope()
    tap.fail()
    assert tap.error == "internal_error"
    tap.add_block(block(F16, 0, 256), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.newest_cell() is None
    tap.reset()
    assert tap.error is None


# -- the engine's read-only schedule accessors ---------------------------------------------


class _NoPort:
    def read(self):
        return None


def test_engine_exposes_the_schedule_map_read_only():
    engine = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=_NoPort())
    assert engine.schedule_at(0) is None and engine.kind_at(0) is None
    engine.set_format(InputFormat(48000, 2, 2))
    engine.next_block(1_000_000.0)
    assert engine.schedule_at(0) is not None
    assert engine.kind_at(0) in ("program", "silence")
    assert engine.schedule_at(0) == engine._map.at(0)


# -- the player hook -----------------------------------------------------------------------


@pytest.fixture
def playout_player_factory():
    made = []

    def _make(**kw):
        p = _player(**kw)
        made.append(p)
        return p

    yield _make
    for p in made:
        p.stop()


def test_player_without_tap_installs_no_hook(playout_player_factory):
    p = playout_player_factory(air_tap=None)
    assert p._air_tap is None


class _RecordingTap(WrittenEnvelope):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[int, float]] = []
        self.transitions: list[bool] = []

    def add_block(self, block, fmt, schedule_at, kind_at, d_out_us):
        self.blocks.append((block.x0_ns, d_out_us))
        super().add_block(block, fmt, schedule_at, kind_at, d_out_us)

    def mute_transition(self, t_raw_us, muted):
        self.transitions.append(muted)
        super().mute_transition(t_raw_us, muted)


@requires_gst
def test_the_player_feeds_every_pushed_block_to_the_tap_with_its_schedule(playout_player_factory):
    tap = _RecordingTap()
    p = playout_player_factory(air_tap=tap, output_delay_us=lambda: 123_000.0)
    p.start(PCM_S16_STEREO_48K)
    p.submit(1_100_000, bytes(19_200))  # 100 ms
    p._tick(now_us=1_000_000.0)
    assert p.metrics()["rendered_buffers"] == len(tap.blocks) > 0
    assert [x for x, _ in tap.blocks] == sorted(x for x, _ in tap.blocks)
    assert {d for _, d in tap.blocks} == {123_000.0}
    n = tap.newest_cell()
    assert n is not None
    sl = tap.slice(0, n + 1)
    engine = p._engine
    for k in range(n + 1):
        x = round(k * 128 * 1e9 / 48000)
        assert sl.s[k] == pytest.approx(engine.schedule_at(x))
        assert sl.t[k] == pytest.approx(engine.schedule_at(x) + 123_000.0)


@requires_gst
def test_user_mute_and_volume_zero_are_recorded_on_change_only():
    tap = _RecordingTap()
    rig_player = _player(air_tap=tap)
    rig_player.start(PCM_S16_STEREO_48K)
    rig_player.set_volume(0.5, False)
    rig_player._tick(now_us=1_000_000.0)
    rig_player.set_volume(0.6, False)  # no change of the effective mute
    rig_player._tick(now_us=1_005_000.0)
    rig_player.set_volume(0.0, False)  # volume 0 = muted
    rig_player._tick(now_us=1_010_000.0)
    rig_player.set_volume(0.3, True)
    rig_player._tick(now_us=1_015_000.0)
    rig_player.set_volume(0.3, False)
    rig_player._tick(now_us=1_020_000.0)
    rig_player.stop()
    assert tap.transitions == [False, True, False]


@requires_gst
def test_tail_mute_start_and_end_are_recorded():
    rig = _TailRig.__new__(_TailRig)
    tap = _RecordingTap()
    from tests.unit.services.audio.playout.test_gst_output import _MuteProbe, _SetPort

    rig.port = _SetPort()
    rig.player = _player(port_factory=lambda sink: rig.port, air_tap=tap)
    rig.player.start(PCM_S16_STEREO_48K)
    rig.probe = _MuteProbe()
    rig.player._sink = rig.probe
    rig.now = 1_000_000.0
    rig.player.submit(int(rig.now) + 100_000, bytes(192_000))
    rig.run(40)
    x_end = rig.clear()
    rig.run(20)
    rig.port.a_ns = x_end + 30_000_000
    rig.run(25)
    assert not rig.muted
    rig.close()
    assert tap.transitions == [True, False]


class _BrokenTap(WrittenEnvelope):
    def add_block(self, block, fmt, schedule_at, kind_at, d_out_us):
        raise ValueError("boom")


@requires_gst
def test_a_tap_exception_never_reaches_the_feeder(playout_player_factory, caplog):
    tap = _BrokenTap()
    p = playout_player_factory(air_tap=tap)
    p.start(PCM_S16_STEREO_48K)
    p.submit(1_100_000, bytes(19_200))
    p._tick(now_us=1_000_000.0)
    p._tick(now_us=1_005_000.0)
    assert p.metrics()["rendered_buffers"] > 1  # the feeder carried on
    assert not p._feeder_failed
    assert tap.error == "internal_error"
    assert sum("air tap" in r.getMessage() for r in caplog.records) == 1


def test_the_generation_changes_on_reset_and_on_a_format_change():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 441), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    g0 = tap.gen
    assert tap.slice(0, 2).gen == g0
    tap.add_block(block(F16, 10_000_000, 441), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.gen == g0  # same format, same stream
    tap.add_block(block(F24, 0, 480), F24, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    g1 = tap.gen
    assert g1 != g0 and tap.slice(0, 2).gen == g1  # cell indexes restarted
    tap.reset()
    assert tap.gen not in (g0, g1)


def _impulse_tap(at_frame: int, frames: int = 640, value: int = 4000) -> WrittenEnvelope:
    xs = [0] * frames
    xs[at_frame] = value
    tap = WrittenEnvelope()
    blk = Block(
        pcm=s16(interleave(xs)),
        x0_ns=0,
        duration_ns=round(frames * 1e9 / 44100),
        program=True,
        dropped=0,
        inserted=0,
        late_drop_us=0.0,
    )
    tap.add_block(blk, F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    return tap


def test_a_cell_also_sees_the_frames_an_sbc_frame_looks_back_on():
    """An SBC frame's scale factors see its 128 samples and the 72 before them (the 10·M-tap
    analysis window, M = 8); a written cell covers the same span, so both envelopes rise and fall
    on the same samples whatever the attack and decay of the sound."""
    assert LOOKBACK_FRAMES == 72
    inside = _impulse_tap(3 * 128 - 60).slice(0, 5)  # the comb peaks at -60 and -57 frames before cell 3
    assert inside.w[3] == pytest.approx(math.log2(4001), abs=1e-4)
    assert inside.w[4] == 0.0
    outside = _impulse_tap(3 * 128 - 76).slice(0, 5)  # peaks at -76 and -73: beyond the look-back
    assert outside.w[3] == 0.0 and outside.w[2] == pytest.approx(math.log2(4001), abs=1e-4)


def test_the_look_back_is_continuous_across_blocks():
    xs = [0] * 640
    xs[3 * 128 - 10] = 4000
    pcm = s16(interleave(xs))

    def mk(a, b):
        return Block(
            pcm=pcm[a * 4 : b * 4],
            x0_ns=round(a * 1e9 / 44100),
            duration_ns=round((b - a) * 1e9 / 44100),
            program=True,
            dropped=0,
            inserted=0,
            late_drop_us=0.0,
        )

    split = WrittenEnvelope()
    for a, b in ((0, 380), (380, 383), (383, 640)):
        split.add_block(mk(a, b), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert list(split.slice(0, 5).w) == list(_impulse_tap(3 * 128 - 10).slice(0, 5).w)


def test_the_look_back_follows_the_sbc_sub_bands():
    """9·M input frames: 72 for M = 8 (the default), 36 for M = 4; scaled when the rates differ."""
    tap = WrittenEnvelope()
    assert tap.lookback_frames == LOOKBACK_FRAMES == 72
    tap.set_sbc_format(4, 44100)
    xs = [0] * 640
    xs[3 * 128 - 30] = 4000  # comb peaks at -30 and -27: inside 36
    tap.add_block(
        Block(s16(interleave(xs)), 0, round(640e9 / 44100), True, 0, 0, 0.0),
        F16,
        lambda x: x / 1000.0,
        lambda x: "program",
        d_out_us=0.0,
    )
    assert tap.lookback_frames == 36 and tap.slice(0, 5).w[3] > 0
    far = WrittenEnvelope()
    far.set_sbc_format(4, 44100)
    xs = [0] * 640
    xs[3 * 128 - 45] = 4000  # beyond 36, inside 72
    far.add_block(
        Block(s16(interleave(xs)), 0, round(640e9 / 44100), True, 0, 0, 0.0),
        F16,
        lambda x: x / 1000.0,
        lambda x: "program",
        d_out_us=0.0,
    )
    assert far.slice(0, 5).w[3] == 0.0
    scaled = WrittenEnvelope()
    scaled.set_sbc_format(8, 44100)
    scaled.add_block(block(F24, 0, 256), F24, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert scaled.lookback_frames == round(72 * 48000 / 44100)


# -- written stream time per cell (B2) -----------------------------------------------------


def _expected_x(x0_ns, k):
    return x0_ns + k * 128e9 / 48000


def test_cells_carry_the_written_stream_time():
    tap = WrittenEnvelope()
    for b in range(2):
        tap.add_block(block(F24, 5_000_000_000 + b * 10_000_000, 480), F24, lambda x: x / 1e3, lambda x: "program", 0.0)
    n = tap.newest_cell() + 1
    sl = tap.slice(0, n)
    assert n == 7 and sl.x.typecode == "d"
    for k in range(n):
        assert sl.x[k] == pytest.approx(_expected_x(5e9, k), abs=1.0)


def test_x_stays_aligned_with_s_when_the_ring_wraps():
    tap = WrittenEnvelope(seconds=1.0)
    for b in range(300):
        tap.add_block(block(F24, b * 10_000_000, 480), F24, lambda x: x / 1e3, lambda x: "program", 0.0)
    n = tap.newest_cell()
    sl = tap.slice(n - 20, n + 1)
    assert sl.i0 == n - 20
    for k in range(21):
        assert sl.x[k] == pytest.approx(_expected_x(0, n - 20 + k), abs=1.0)
        assert sl.x[k] == pytest.approx(sl.s[k] * 1e3, abs=1.0)


def test_x_restarts_from_the_new_block_after_a_reset():
    tap = WrittenEnvelope()
    tap.add_block(block(F24, 5_000_000_000, 960), F24, lambda x: x / 1e3, lambda x: "program", 0.0)
    tap.reset()
    tap.add_block(block(F24, 9_000_000_000, 960), F24, lambda x: x / 1e3, lambda x: "program", 0.0)
    sl = tap.slice(0, 2)
    assert list(sl.x) == pytest.approx([9e9, 9e9 + 128e9 / 48000], abs=1.0)


def test_x_follows_the_block_across_a_format_change():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 441), F16, lambda x: x / 1e3, lambda x: "program", 0.0)
    tap.add_block(block(F24, 3_000_000_000, 480), F24, lambda x: x / 1e3, lambda x: "program", 0.0)
    sl = tap.slice(0, 3)
    assert list(sl.x) == pytest.approx([_expected_x(3e9, k) for k in range(3)], abs=1.0)
