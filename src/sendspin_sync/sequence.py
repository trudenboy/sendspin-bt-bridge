"""Measuring every speaker in one recording (the phone capture page).

A phone page that cannot use the microphone live (plain HTTP) records with the phone's own recorder
and uploads a file; separate files share no clock, so their phases cannot be compared. Instead the
bridge plays a *sequence* while the phone records once: every member muted (a lead-in), then each
member alone for a while, with everyone muted again in between. One file, one clock: each member's
stretch is cut out at its offset in the file and goes to the estimator with that offset as its
start time, so the phases compare exactly as recordings from one microphone session do.

Where the sequence starts in the file is unknown (the operator starts the recorder by hand): the
clicks found in the file are lined up against the sequence's own timeline (the solos as the bridge
timed them); the silent gaps between members make that robust.
"""

from __future__ import annotations

import bisect
import math
import sys
from array import array
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sendspin_sync.models import Recording

if TYPE_CHECKING:
    from collections.abc import Sequence

LEAD_S = 3.0  # everyone muted before the first member: the recorder is running by then
RECORDER_LEAD_S = 10.0  # the same when the phone's own recorder is started by hand
MAX_LEAD_S = 15.0
GAP_S = 2.0  # everyone muted between two members: the clicks of one never reach the other's window
WALK_S = 5.0  # the gap when the phone is carried from one speaker to the next
SETTLE_S = 0.8  # left out at the start of a member's stretch (the mutes take a moment)
TAIL_S = 0.3  # left out at its end
_FRAME_S = 0.01
_EVENT_RATIO = 30.0  # a click frame stands this far (≈15 dB) above the recording's median
_STEP_S = 0.02  # alignment search step
# Hard limits, whatever the upload holds (security review): the members of one sequence, the click
# events kept from a recording (the strongest), and the events alignment ever looks at.
MAX_MEMBERS = 16
_EVENTS_PER_PERIOD = 6  # a pair of chirps and their reflections every PERIOD_S
_MAX_EVENTS = 4000


def solo_seconds(point: str) -> float:
    """How long each member plays alone: a little more than the per-member recording of the live flow."""
    return 13.0 if point == "position" else 7.0


def gap_seconds(point: str) -> float:
    """Everyone muted between two members: long enough to carry the phone to the next speaker when
    measuring at each speaker."""
    return GAP_S if point == "position" else WALK_S


@dataclass(frozen=True)
class Slot:
    """One member's stretch, in seconds since the sequence began (everyone muted at 0)."""

    member: str
    start_s: float
    end_s: float


def _band_energies(pcm: bytes, sample_rate: int) -> array:
    """Energy of the click band per 10 ms frame (the estimator's comb ``x[i] - x[i-d]``, d = sr/8000),
    computed on the int16 samples in place: no per-sample float list, so a 5-minute recording costs
    its own size plus a few hundred kB (review I3)."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    frame = max(1, round(sample_rate * _FRAME_S))
    d = max(1, round(sample_rate / 8000))
    scale = 1.0 / (32768.0 * 32768.0)
    out = array("d")
    for lo in range(0, n, frame):
        hi = min(n, lo + frame)
        energy = 0
        for k in range(max(lo, d), hi):
            diff = samples[k] - samples[k - d]
            energy += diff * diff
        out.append(energy * scale)
    return out


def max_events(duration_s: float) -> int:
    """The most click events a recording of *duration_s* can plausibly hold (more is noise)."""
    from sendspin_sync.click_track import PERIOD_S

    return min(_MAX_EVENTS, _EVENTS_PER_PERIOD * (math.ceil(max(0.0, duration_s) / PERIOD_S) + 1))


def click_times(pcm: bytes, sample_rate: int) -> list[float]:
    """Seconds into the recording where a click-band burst stands well above the room's floor; at
    most ``max_events`` of them (the strongest), so a crafted or noisy upload cannot blow up the work."""
    env = _band_energies(pcm, sample_rate)
    if not env:
        return []
    floor = max(sorted(env)[len(env) // 2], 1e-12)
    found: list[tuple[float, float]] = []  # (time, energy)
    for i, e in enumerate(env):
        if (
            e > floor * _EVENT_RATIO
            and e >= (env[i - 1] if i else 0.0)
            and e >= (env[i + 1] if i + 1 < len(env) else 0.0)
        ):
            if not found or i * _FRAME_S - found[-1][0] > 0.03:  # one per chirp pair is plenty
                found.append((i * _FRAME_S, e))
    limit = max_events(len(pcm) / 2 / sample_rate)
    if len(found) > limit:
        found = sorted(sorted(found, key=lambda te: te[1], reverse=True)[:limit])
    return [t for t, _e in found]


_SAME_FIT_S = 2.0  # best-scoring runs closer than this are one fit, not two


def offset_bounds(slots: Sequence[Slot], duration_s: float) -> tuple[float, float]:
    """The offsets at which the whole sequence lies in a recording of *duration_s*, give or take half
    a member's period at either end.

    Every member plays for the same time with the same gap, so the slot pattern shifted by one member
    explains the clicks as well as the true one whenever the first or the last member is silent. That
    shift moves the sequence a whole member's period past one end of the file; a recording that holds
    the sequence (the recorder runs from before the first member until it has finished) rules it out.
    Half a period of slack keeps a recording that starts or stops a little late measurable.
    """
    slots = list(slots)
    period = slots[1].start_s - slots[0].start_s if len(slots) > 1 else math.inf
    slack = min(period / 2, MAX_LEAD_S)
    return -slots[0].start_s - slack, max(0.0, duration_s) - slots[-1].end_s + slack


def align(
    events: Sequence[float],
    slots: Sequence[Slot],
    duration_s: float,
    *,
    expected: tuple[float, float] | None = None,
) -> float | None:
    """The file time at which the sequence began: the offset that puts the most clicks inside the
    members' stretches and the fewest in the lead-in and the gaps; the middle of the best run of
    offsets.

    Only offsets at which the sequence lies in the recording are considered (``offset_bounds``);
    *expected* narrows them further when the caller knows roughly where the sequence began (a
    recorder the bridge started itself). None when no click was found, and when two fits a member
    apart score the same: which speakers were heard is then undecidable, and a guess would measure
    the wrong ones.
    """
    if not events or not slots:
        return None
    slots = list(slots)[:MAX_MEMBERS]
    times = sorted(events)[:_MAX_EVENTS]
    end = slots[-1].end_s
    lo_s, hi_s = offset_bounds(slots, min(max(0.0, duration_s), 3600.0))
    if expected is not None:
        lo_s, hi_s = max(lo_s, expected[0]), min(hi_s, expected[1])
    if hi_s < lo_s:
        return None
    best_score: float = -math.inf
    runs: list[list[float]] = []  # the runs of consecutive offsets that share the best score
    for k in range(math.ceil(lo_s / _STEP_S), math.floor(hi_s / _STEP_S) + 1):
        offset = k * _STEP_S
        # counted with binary searches: O(members * log events) per offset
        lo = bisect.bisect_left(times, offset - MAX_LEAD_S)
        hi = bisect.bisect_right(times, offset + end + WALK_S)
        inside = sum(
            bisect.bisect_right(times, offset + s.end_s) - bisect.bisect_left(times, offset + s.start_s) for s in slots
        )
        outside = (hi - lo) - inside
        score = inside - 2 * outside
        if score > best_score:
            best_score, runs = score, [[offset]]
        elif score == best_score:
            if abs(offset - runs[-1][-1] - _STEP_S) < 1e-9:
                runs[-1].append(offset)
            else:
                runs.append([offset])
    if best_score <= 0 or not runs:
        return None
    first = runs[0]
    if any(run[0] - first[-1] > _SAME_FIT_S for run in runs[1:]):
        return None
    return (first[0] + first[-1]) / 2


# -- alignment in a noisy room ------------------------------------------------------------------
#
# ``click_times`` needs a 10 ms frame of the click band 15 dB above the room; the estimator measures
# rooms far noisier than that, because it correlates against the chirp itself. When no click stands
# out on its own, the recording is aligned on the chirp's matched-filter envelope instead: every
# frame's excess over the room counts towards the slot it falls in, so the clicks of a whole solo add
# up even when none of them could be found alone.

_SOFT_FRAME_S = 0.01
_SOFT_CAP = 30.0  # a frame counts at most this many times the room's level: a door slam is one frame
_SOFT_MIN_Z = 4.0  # the best fit stands this many noise deviations clear of a fit made by noise alone
_SOFT_AMBIGUOUS = 0.85  # a fit more than _SAME_FIT_S away scoring this share of the best: undecidable
_SOFT_RAMP_S = 1.5


def _matched_envelope(pcm: bytes, sample_rate: int):  # -> numpy.ndarray | None
    """The chirp's matched-filter energy per frame, in units of the room's level minus its mean; None
    without numpy (the event-based alignment is then the only one)."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the bridge
        return None
    from sendspin_sync import estimator

    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2")
    c, q = estimator._quadrature(sample_rate)
    taps = len(c)
    n = len(samples)
    frame = max(1, round(sample_rate * _SOFT_FRAME_S))
    if n <= taps or n < frame:
        return None
    # the matched filter block by block (overlap-save), keeping only each frame's peak: a five-minute
    # recording costs a few hundred kB beyond its own size, as the event-based path does
    size = 1 << 16
    step = (size - taps + 1) // frame * frame  # whole frames per block
    kernels = [np.fft.rfft(np.asarray(t[::-1], dtype=np.float32), size) for t in (c, q)]
    usable = n // frame * frame
    parts = []
    for start in range(0, usable, step):
        block = samples[start : start + size].astype(np.float32) / 32768.0
        spectrum = np.fft.rfft(block, size)
        count = min(step, usable - start)
        acc = np.zeros(count, dtype=np.float32)
        for kernel in kernels:
            y = np.fft.irfft(spectrum * kernel, size)[taps - 1 : taps - 1 + count]
            acc += y * y
        parts.append(acc.reshape(-1, frame).max(axis=1))
    peaks = np.concatenate(parts).astype(np.float64)
    floor = float(np.median(peaks))
    if floor <= 0.0:
        return None
    level = np.minimum(peaks / floor, _SOFT_CAP)
    return level - float(np.median(level))  # the room sits at 0: frames outside the file (padding) score like it


def soft_align(
    pcm: bytes,
    sample_rate: int,
    slots: Sequence[Slot],
    *,
    expected: tuple[float, float] | None = None,
) -> float | None:
    """The sequence's start in the file from the chirp's matched-filter envelope (see above); None when
    the best fit is no better than noise, or when a fit a member away is almost as good."""
    import numpy as np

    excess = _matched_envelope(pcm, sample_rate)
    if excess is None or not slots:
        return None
    slots = list(slots)[:MAX_MEMBERS]
    frames = len(excess)
    fr = _SOFT_FRAME_S
    # the lead-in and the gaps count against a fit (the group is muted there), and one gap after the
    # last member; before the sequence began nothing counts — every member still played there, and
    # a recorder started first holds that
    end = slots[-1].end_s
    tail_s = slots[1].start_s - slots[0].end_s if len(slots) > 1 else GAP_S
    # the weight of a frame at each place of the sequence: a member's stretch counts, rising over
    # _SOFT_RAMP_S at both ends so the best fit puts the clicks in the middle of the stretches (a flat
    # mask scores a whole click period of offsets alike); the lead-in and the gaps count against
    span = round((end + tail_s) / fr) + 1
    weight = np.full(span, -1.0)
    ramp = max(1, round(_SOFT_RAMP_S / fr))
    inside_frames = 0
    for slot in slots:
        a_i, b_i = round(slot.start_s / fr), round(slot.end_s / fr)
        n_i = max(0, b_i - a_i)
        inside_frames += n_i
        k = np.arange(n_i)
        weight[a_i:b_i] = np.minimum(1.0, np.minimum(k + 1, n_i - k) / ramp)
    # every frame offset scored at once; the file is padded so a fit may put part of the sequence
    # outside it
    padded = np.concatenate([np.zeros(span), excess, np.zeros(span)])
    # index i: the weights laid on padded[i:i+span] (a correlation, through the FFT: a long group's
    # weights and recording are tens of thousands of frames each)
    size = 1 << int(np.ceil(np.log2(len(padded) + span)))
    corr = np.fft.irfft(np.fft.rfft(padded, size) * np.conj(np.fft.rfft(weight, size)), size)
    scores_all = corr[: len(padded) - span + 1]
    lo_s, hi_s = offset_bounds(slots, frames * fr)
    if expected is not None:
        lo_s, hi_s = max(lo_s, expected[0]), min(hi_s, expected[1])
    if hi_s < lo_s:
        return None
    # the weights start at the sequence's start: offset o sits at padded index span + o/fr
    first_i, last_i = span + math.ceil(lo_s / fr), span + math.floor(hi_s / fr)
    first_i, last_i = max(0, first_i), min(len(scores_all) - 1, last_i)
    if last_i < first_i:
        return None
    scores = scores_all[first_i : last_i + 1]
    best = int(np.argmax(scores))
    # how far noise alone could push a score: the room's per-frame deviation over the weighted frames
    deviation = 1.4826 * float(np.median(np.abs(excess - np.median(excess)))) or 1e-12
    noise = deviation * math.sqrt(float(np.sum(weight * weight)))
    if scores[best] < _SOFT_MIN_Z * noise:
        return None
    median = float(np.median(scores))
    keep = round(_SAME_FIT_S / fr)
    far = np.concatenate([scores[: max(0, best - keep)], scores[best + keep + 1 :]])
    if far.size and float(far.max()) - median >= _SOFT_AMBIGUOUS * (float(scores[best]) - median):
        return None
    return (first_i + best - span) * fr


def soft_widen_s(point: str) -> float:
    """How far a member's window reaches into the silent gaps on either side when the alignment came
    from the envelope: clicks fix the sequence only to within half a click period, and a quiet room
    needs every click of the solo. The gap stays wider than that error plus the reach, so a
    neighbour's clicks never enter."""
    return max(0.0, min(1.0, gap_seconds(point) / 2 - 0.5))


def cut(
    pcm: bytes,
    sample_rate: int,
    slots: Sequence[Slot],
    point: str,
    *,
    expected: tuple[float, float] | None = None,
) -> tuple[float, list[Recording]] | None:
    """Where the sequence began in the recording, and each member's stretch of it: from the clicks
    that stand out (exact), else from the chirp's matched-filter envelope (a noisy room, with the
    windows reaching into the gaps). None when neither can place the sequence."""
    duration = len(pcm) / 2 / sample_rate
    offset = align(click_times(pcm, sample_rate), slots, duration, expected=expected)
    widen = 0.0
    if offset is None:
        offset = soft_align(pcm, sample_rate, slots, expected=expected)
        widen = soft_widen_s(point)
    if offset is None:
        return None
    return offset, windows(pcm, sample_rate, slots, offset, widen_s=widen)


def windows(
    pcm: bytes, sample_rate: int, slots: Sequence[Slot], offset: float, *, widen_s: float = 0.0
) -> list[Recording]:
    """Each member's stretch of the recording, trimmed at both ends (or reaching *widen_s* into the
    gaps), with its offset in the file as the capture clock (one clock for all of them)."""
    n = len(pcm) // 2
    out: list[Recording] = []
    for s in slots:
        lo = max(0, round((offset + s.start_s + SETTLE_S - widen_s) * sample_rate))
        hi = min(n, round((offset + s.end_s - TAIL_S + widen_s) * sample_rate))
        if hi - lo < sample_rate:  # less than a second of it in the file
            out.append(Recording(member=s.member, start_time_s=lo / sample_rate, sample_rate=sample_rate, pcm=b""))
            continue
        out.append(
            Recording(member=s.member, start_time_s=lo / sample_rate, sample_rate=sample_rate, pcm=pcm[2 * lo : 2 * hi])
        )
    return out


def max_duration_s(members: int, point: str) -> float:
    """The longest recording that can hold a sequence of *members* (with a generous margin)."""
    return MAX_LEAD_S + members * (solo_seconds(point) + gap_seconds(point)) + 60.0


def length_s(members: int, point: str, lead_s: float = LEAD_S) -> float:
    """How long a sequence of *members* plays, lead-in included."""
    return lead_s + members * solo_seconds(point) + max(0, members - 1) * gap_seconds(point)


STEP_OVERHEAD_S = 1.0  # each member's solo and silence: Music Assistant round trips the waits do not count
MARGIN_S = 5.0


def needed_s(members: int, point: str, lead_s: float = LEAD_S) -> float:
    """How much click track a sequence of *members* uses up, Music Assistant's steps included: the
    clicks must still play when the last member's solo ends."""
    return length_s(members, point, lead_s) + members * STEP_OVERHEAD_S + MARGIN_S
