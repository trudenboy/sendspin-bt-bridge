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


def align(events: Sequence[float], slots: Sequence[Slot], duration_s: float) -> float | None:
    """The file time at which the sequence began: the offset that puts the most clicks inside the
    members' stretches and the fewest in the lead-in and the gaps; the middle of the best run of
    offsets. None when no click was found."""
    if not events or not slots:
        return None
    slots = list(slots)[:MAX_MEMBERS]
    times = sorted(events)[:_MAX_EVENTS]
    end = slots[-1].end_s
    best_score: float = -math.inf
    best: list[float] = []
    steps = int(min(max(0.0, duration_s), 3600.0) / _STEP_S) + 1
    for k in range(-int(end / _STEP_S), steps):
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
            best_score, best = score, [offset]
        elif score == best_score and best and abs(offset - best[-1] - _STEP_S) < 1e-9:
            best.append(offset)
    if best_score <= 0 or not best:
        return None
    return (best[0] + best[-1]) / 2


def windows(pcm: bytes, sample_rate: int, slots: Sequence[Slot], offset: float) -> list[Recording]:
    """Each member's stretch of the recording, trimmed at both ends, with its offset in the file as
    the capture clock (one clock for all of them)."""
    n = len(pcm) // 2
    out: list[Recording] = []
    for s in slots:
        lo = max(0, round((offset + s.start_s + SETTLE_S) * sample_rate))
        hi = min(n, round((offset + s.end_s - TAIL_S) * sample_rate))
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
