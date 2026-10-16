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

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sendspin_sync.estimator import band_envelope, pcm_to_floats
from sendspin_sync.models import Recording

if TYPE_CHECKING:
    from collections.abc import Sequence

LEAD_S = 3.0  # everyone muted before the first member: the recorder is running by then
GAP_S = 2.0  # everyone muted between two members: the clicks of one never reach the other's window
WALK_S = 5.0  # the gap when the phone is carried from one speaker to the next
SETTLE_S = 0.8  # left out at the start of a member's stretch (the mutes take a moment)
TAIL_S = 0.3  # left out at its end
_FRAME_S = 0.01
_EVENT_RATIO = 30.0  # a click frame stands this far (≈15 dB) above the recording's median
_STEP_S = 0.02  # alignment search step


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


def click_times(pcm: bytes, sample_rate: int) -> list[float]:
    """Seconds into the recording where a click-band burst stands well above the room's floor."""
    x = pcm_to_floats(pcm)
    env = band_envelope(x, sample_rate, frame_ms=_FRAME_S * 1000)
    if not env:
        return []
    floor = max(sorted(env)[len(env) // 2], 1e-12)
    out: list[float] = []
    for i, e in enumerate(env):
        if (
            e > floor * _EVENT_RATIO
            and e >= (env[i - 1] if i else 0.0)
            and e >= (env[i + 1] if i + 1 < len(env) else 0.0)
        ):
            if not out or i * _FRAME_S - out[-1] > 0.03:  # one per chirp pair is plenty
                out.append(i * _FRAME_S)
    return out


def align(events: Sequence[float], slots: Sequence[Slot], duration_s: float) -> float | None:
    """The file time at which the sequence began: the offset that puts the most clicks inside the
    members' stretches and the fewest in the lead-in and the gaps; the middle of the best run of
    offsets. None when no click was found."""
    if not events or not slots:
        return None
    end = slots[-1].end_s
    best_score: float = -math.inf
    best: list[float] = []
    steps = int(max(0.0, duration_s) / _STEP_S) + 1
    for k in range(-int(end / _STEP_S), steps):
        offset = k * _STEP_S
        inside = outside = 0
        for t in events:
            rel = t - offset
            if rel < -LEAD_S or rel > end + WALK_S:
                continue  # before the sequence or after it: says nothing
            if any(s.start_s <= rel <= s.end_s for s in slots):
                inside += 1
            else:
                outside += 1
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
    return LEAD_S + members * (solo_seconds(point) + gap_seconds(point)) + 60.0
