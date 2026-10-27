"""Find the click track in a phone recording and return its phase in the 10 s cycle."""

from __future__ import annotations

import math
import operator
import struct
from dataclasses import dataclass

from sendspin_sync import click_track as ct
from sendspin_sync.models import Measurement, Recording

MIN_CLICKS = 2
MAX_SPREAD_MS = 3.0
MIN_SNR_DB = 10.0
MIN_SAMPLE_RATE = 12000
MAX_SAMPLE_RATE = 48000
MIN_DURATION_S = 2.0
MAX_DURATION_S = 20.0
MAX_CLOCK_S = 1e10  # |capture clock| and |epoch| beyond this are refused: a float there cannot hold milliseconds
_CANDIDATE_RATIO = 3.0  # band energy over the median floor that opens a candidate event (4.8 dB)
_ABS_FLOOR_LSB = 4.0  # floor never drops below the band energy of ±4 LSB white noise (digital silence, dither)
_REFINE_MS = 3.0  # matched-filter search around an event's centre
_MIN_NCC = 0.4  # normalised correlation an event needs to count as a chirp; pair + sequence reject the rest
_EARLIER_RATIO = 0.7  # an earlier peak this strong is the direct sound, the later one an overlapping reflection
_PAIR_DB = 10.0  # both chirps of a pair are played equally loud; allows a reflection landing on one of them
_GAP_TOL_MS = 5.0
_CLUSTER_S = 0.4  # one click (pair + reflections) fits in this window; clicks are PERIOD_S apart
_EVENTS_PER_PERIOD = 6  # 2 chirps x direct + 2 reflections; more than this is kept-strongest
_MAX_RATIO = 10 ** (MIN_SNR_DB / 10)  # a crowded envelope raises the threshold up to the SNR a click needs anyway
_RATIO_STEP = 1.5


def pcm_to_floats(pcm: bytes) -> list[float]:
    return [v / 32768.0 for v in struct.unpack(f"<{len(pcm) // 2}h", pcm)]


def band_envelope(x: list[float], sample_rate: int, frame_ms: float = 1.0) -> list[float]:
    """Energy of the chirp band per frame.

    The comb difference ``x[i] - x[i-d]`` with ``d = sr / 8000`` has its first lobe centred on 4 kHz and zeros at
    DC and 8 kHz, so it keeps the 2–6 kHz chirp and drops bass and hiss; frames sum squares."""
    frame = max(1, round(sample_rate * frame_ms / 1000))
    d = max(1, round(sample_rate / 8000))
    bp = [x[i] - x[i - d] for i in range(d, len(x))]
    return [sum(v * v for v in bp[i : i + frame]) for i in range(0, len(bp), frame)]


def matched_peak(
    x: list[float], template: list[float], centre: int, half_window: int, step: int = 1
) -> tuple[int, float]:
    """Offset in ``centre ± half_window`` where ``template`` correlates best (by magnitude).

    ``step`` > 1 searches a coarse grid first and then refines around its winner."""
    n = len(template)
    lo, hi = max(0, centre - half_window), min(len(x) - n, centre + half_window)
    best_i, best = lo, -1.0
    for i in range(lo, hi + 1, max(1, step)):
        acc = abs(sum(map(operator.mul, x[i : i + n], template)))
        if acc > best:
            best, best_i = acc, i
    if step > 1:
        return matched_peak(x, template, best_i, step - 1)
    return best_i, best


def circular_mean_ms(phases_ms: list[float], cycle_ms: float) -> tuple[float, float]:
    s = sum(math.sin(2 * math.pi * p / cycle_ms) for p in phases_ms)
    c = sum(math.cos(2 * math.pi * p / cycle_ms) for p in phases_ms)
    mean = (math.atan2(s, c) / (2 * math.pi) * cycle_ms) % cycle_ms
    devs = [((p - mean + cycle_ms / 2) % cycle_ms) - cycle_ms / 2 for p in phases_ms]
    return mean, math.sqrt(sum(d * d for d in devs) / len(devs))


def _invalid(member: str) -> Measurement:
    return Measurement(member=member, status="not_heard", reason="invalid_recording")


@dataclass(frozen=True)
class _Chirp:
    pos: int  # sample offset of the chirp start
    strength: float  # matched-filter magnitude
    env_peak: float  # band-energy peak of the event


def _events(env: list[float], threshold: float) -> list[tuple[int, float]]:
    """Runs of frames above ``threshold``, each as (loudest frame, its energy).

    The loudest frame, not the run's middle, locates the chirp: noise touching a chirp merges into its run."""
    out: list[tuple[int, float]] = []
    peak_i, peak = -1, 0.0
    for i, e in enumerate(env):
        if e > threshold:
            if peak_i < 0 or e > peak:
                peak_i, peak = i, e
        elif peak_i >= 0:
            out.append((peak_i, peak))
            peak_i = -1
    if peak_i >= 0:
        out.append((peak_i, peak))
    return out


def _chirps(x: list[float], sr: int, events: list[tuple[int, float]], frame: int) -> list[_Chirp]:
    template = ct.chirp(sr)
    t_norm = math.sqrt(sum(v * v for v in template))
    n = len(template)
    half, step = round(sr * _REFINE_MS / 1000), max(1, sr // 16000)
    found: list[_Chirp] = []
    for peak_frame, env_peak in events:
        centre = round((peak_frame + 0.5) * frame) - n // 2  # the band peaks mid-chirp (4 kHz)
        pos, strength = matched_peak(x, template, centre, half, step)
        # a reflection overlapping the chirp can out-correlate it; the direct sound is the earliest strong peak
        lo = max(0, pos - n + 1)
        hi = pos - max(1, round(sr * 0.0005))
        if hi > lo:
            prev, prev_strength = matched_peak(x, template, (lo + hi) // 2, (hi - lo) // 2, step)
            if prev < pos and prev_strength >= _EARLIER_RATIO * strength:
                pos, strength = prev, prev_strength
        seg_norm = math.sqrt(sum(v * v for v in x[pos : pos + n]))
        if seg_norm <= 0.0 or strength / (seg_norm * t_norm) < _MIN_NCC:
            continue
        if found and pos - found[-1].pos < n // 2:  # two fragments of the same chirp
            if strength > found[-1].strength:
                found[-1] = _Chirp(pos, strength, max(env_peak, found[-1].env_peak))
            continue
        found.append(_Chirp(pos, strength, env_peak))
    return found


def _clusters(chirps: list[_Chirp], sr: int) -> list[list[_Chirp]]:
    out: list[list[_Chirp]] = []
    for c in chirps:
        if out and c.pos - out[-1][0].pos <= _CLUSTER_S * sr:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def _pair(cluster: list[_Chirp], k: int, sr: int) -> tuple[_Chirp, _Chirp] | None:
    """Earliest chirp with a credible partner ``gap_ms(k)`` later. Earliest, because the direct sound
    arrives before its reflections; credible, because both chirps of a pair are played equally loud."""
    gap, tol = round(sr * ct.gap_ms(k) / 1000), round(sr * _GAP_TOL_MS / 1000)
    ratio = 10 ** (-_PAIR_DB / 20)
    for a in cluster:
        partners = [
            b
            for b in cluster
            if abs(b.pos - a.pos - gap) <= tol and min(a.strength, b.strength) >= ratio * max(a.strength, b.strength)
        ]
        if partners:
            return a, max(partners, key=lambda b: b.strength)
    return None


def _click_sequence(clusters: list[list[_Chirp]], sr: int) -> list[tuple[_Chirp, _Chirp, int]]:
    """Pairs whose indexes advance by one per PERIOD_S — the hypothesis that explains the most clusters.
    A reflection that happens to sit one valid gap after a chirp fits one cluster, never the sequence."""
    if not clusters:
        return []
    t0 = clusters[0][0].pos
    steps = [round((c[0].pos - t0) / (sr * ct.PERIOD_S)) for c in clusters]
    best: list[tuple[_Chirp, _Chirp, int]] = []
    best_score: tuple[int, int, int, float] | None = None
    for k0 in range(ct.CYCLE_CLICKS):
        found: list[tuple[_Chirp, _Chirp, int]] = []
        for cluster, s in zip(clusters, steps, strict=True):
            k = (k0 + s) % ct.CYCLE_CLICKS
            pair = _pair(cluster, k, sr)
            if pair is not None:
                found.append((pair[0], pair[1], k))
        # most clicks; then earliest first chirps and shortest gaps (reflections only ever arrive later, so a
        # hypothesis built from them starts later or pairs with a later partner); then the louder pairs
        score = (
            len(found),
            -sum(a.pos for a, _b, _k in found),
            -sum(b.pos - a.pos for a, b, _k in found),
            sum(a.strength + b.strength for a, b, _k in found),
        )
        if found and (best_score is None or score > best_score):
            best, best_score = found, score
    return best


def _valid(recording: Recording, epoch_s: float) -> bool:
    sr = recording.sample_rate
    if not MIN_SAMPLE_RATE <= sr <= MAX_SAMPLE_RATE or len(recording.pcm) % 2:
        return False
    if not (math.isfinite(recording.start_time_s) and math.isfinite(epoch_s)):  # a phase of nan poisons planning
        return False
    if abs(recording.start_time_s) > MAX_CLOCK_S or abs(epoch_s) > MAX_CLOCK_S:  # the clock comes from the upload
        return False
    return MIN_DURATION_S * sr <= len(recording.pcm) // 2 <= MAX_DURATION_S * sr


def estimate_per_click(recording: Recording, *, epoch_s: float = 0.0) -> Measurement:
    """Each chirp pair detected on its own, the click sequence decoded from their gaps: the most precise
    phase when every click stands well above the room's noise."""
    if not _valid(recording, epoch_s):
        return _invalid(recording.member)
    x = pcm_to_floats(recording.pcm)
    return _per_click(recording, epoch_s, x, band_envelope(x, recording.sample_rate))[0]


def _per_click(
    recording: Recording, epoch_s: float, x: list[float], env: list[float]
) -> tuple[Measurement, float | None]:
    """The per-click measurement, and the phase of a click sequence it found but judged too weak (else None)."""
    sr, member = recording.sample_rate, recording.member
    n = len(x)
    frame = max(1, round(sr / 1000))
    abs_floor = 2.0 * (_ABS_FLOOR_LSB / 32768.0) ** 2 * frame
    floor = max(sorted(env)[len(env) // 2], abs_floor)
    # A phone's band envelope crackles (AAC artefacts, desk and room noise): far more weak events than a click
    # track can make. Raise the threshold until the count is plausible — never above the SNR a click needs to be
    # accepted, so no measurable click is lost — then keep the strongest. One matched-filter pass either way.
    plausible = _EVENTS_PER_PERIOD * (math.ceil(n / sr / ct.PERIOD_S) + 1)
    ratio = _CANDIDATE_RATIO
    events = _events(env, ratio * floor)
    while len(events) > plausible and ratio < _MAX_RATIO:
        ratio = min(ratio * _RATIO_STEP, _MAX_RATIO)
        events = _events(env, ratio * floor)
    crowded = ratio > _CANDIDATE_RATIO
    if len(events) > plausible:
        events = sorted(sorted(events, key=lambda e: e[1], reverse=True)[:plausible])
    pairs = _click_sequence(_clusters(_chirps(x, sr, events, frame), sr), sr)
    if not pairs:  # in a crowded room "no clicks" means they did not stand out of the noise
        return Measurement(member=member, status="not_heard", reason="low_snr" if crowded else "no_clicks"), None
    if len(pairs) < MIN_CLICKS:
        return Measurement(member=member, status="not_heard", reason="too_few_clicks", clicks=len(pairs)), None
    cycle_ms = ct.CYCLE_S * 1000.0
    phases = [
        ((recording.start_time_s + a.pos / sr - ct.LEAD_IN_S - k * ct.PERIOD_S - epoch_s) * 1000.0) % cycle_ms
        for a, _b, k in pairs
    ]
    # SNR: band energy of the weaker chirp of each pair over the room's floor, median over clicks
    snrs = sorted(10 * math.log10(max(min(a.env_peak, b.env_peak), 1e-12) / floor) for a, b, _ in pairs)
    snr_db = snrs[len(snrs) // 2]
    mean, spread = circular_mean_ms(phases, cycle_ms)
    if snr_db < MIN_SNR_DB:
        return Measurement(member=member, status="not_heard", reason="low_snr", clicks=len(pairs)), mean
    confidence = round(max(0.0, min(1.0, (snr_db - MIN_SNR_DB) / 20.0)) * (1.0 if spread <= 1.0 else 0.7), 3)
    if spread > MAX_SPREAD_MS:
        return Measurement(
            member=member,
            status="unstable",
            reason="spread",
            phase_ms=round(mean, 2),
            clicks=len(pairs),
            spread_ms=round(spread, 2),
            confidence=confidence,
        ), None
    return Measurement(
        member=member,
        status="ok",
        phase_ms=round(mean, 2),
        clicks=len(pairs),
        spread_ms=round(spread, 2),
        confidence=confidence,
    ), None


# --- coherent estimate: every click of the window accumulated on the click track's known schedule ----------------
#
# A headset or laptop microphone in an ordinary room hears each chirp only a few dB above the room's noise: too weak
# to detect one by one, yet the window holds up to ten pairs on a schedule known to the millisecond. Folding the
# band envelope over the 10 s cycle and scoring every cycle phase against the ten expected chirp offsets adds them
# all up (coarse, 1 ms); a matched filter summed over every expected chirp then places it to a sample (refine).

# The quality a coherent estimate needs (best phase score over the runner-up, both above the median; see
# _coarse_phase), by the clicks the window holds. scripts/calibrate_sync_estimator.py measures it: 1800 windows of
# noise alone (white, chirp-band, sparse taps; 2-10 s, 120 seeds each) reached at most 1.48 with 3 clicks, 1.24
# with 4 and 1.51 with 5, and none passed every gate. The click track in chirp-band noise (SNR: its loudest 1 ms
# band frame over the noise's median one) reaches q >= 1.58 at 6 dB in 5.9 s windows and >= 1.51 at 3 dB in 18 s
# ones; ~2 clean.
COHERENT_MIN_CLICKS = 3  # clicks the window must hold, and clicks heard among them
_QUALITY_BY_CLICKS = {3: 1.6}
_QUALITY_MANY = 1.55
_FOLD_CAP = 10.0  # see _coarse_phase: at least this many times the floor counts in full
_COARSE_WINDOW_MS = 8  # a chirp's band energy spreads over its 8 ms
_RUNNER_UP_MS = 50  # the runner-up phase lies outside the best one's neighbourhood
_COHERENT_REFINE_MS = 4.0  # matched-filter search around the coarse position
_COHERENT_EARLIER = 0.7  # the earliest profile peak this strong (above its floor) is the direct sound
# A click is heard when, at its own arrival, each of its two chirps' matched-filter energy is this many times the
# room's median at the same filter. The room's energy at one position is exponentially distributed (median = ln 2 x
# mean), so 8x the median is ~5.5x (7.4 dB over) the room's mean energy.
_HEARD_RATIO = 8.0


@dataclass(frozen=True)
class _Coherent:
    phase_ms: float | None  # None: no arrival inside the refine search
    quality: float  # (best - median) / (runner-up - median) of the phase scores; a click track gives ~2
    chirps: int  # expected chirp positions inside the window, all used
    expected: int  # clicks with a chirp inside the window
    heard: int  # clicks whose peak matched-filter energy stands _HEARD_RATIO over the room's
    spread_ms: float  # RMS of each heard click's own arrival around the window's


def _quadrature(sr: int) -> tuple[list[float], list[float]]:
    """The chirp and its 90 degree shifted twin: ``|x.c|^2 + |x.c90|^2`` is a smooth envelope of its arrival."""
    c = ct.chirp(sr)
    n = len(c)
    rate = (ct.CHIRP_F1 - ct.CHIRP_F0) / (n / sr)
    q = []
    for i in range(n):
        t = i / sr
        window = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))
        q.append(window * math.cos(2 * math.pi * (ct.CHIRP_F0 * t + 0.5 * rate * t * t)))
    return c, q


def _mf_energy(x: list[float], c: list[float], q: list[float], i: int) -> float:
    seg = x[i : i + len(c)]
    a = sum(map(operator.mul, seg, c))
    b = sum(map(operator.mul, seg, q))
    return a * a + b * b


def _coarse_phase(env: list[float], sr: int, clock_s: float) -> tuple[int, float]:
    """The cycle phase (ms) of click 0's first chirp on ``clock_s`` (the capture clock minus the epoch), and the
    quality of that choice."""
    frame = max(1, round(sr / 1000))
    d = max(1, round(sr / 8000))  # the comb's delay: band_envelope's frame i starts at sample d + i * frame
    bins = round(ct.CYCLE_S * 1000)
    ranked = sorted(env)
    floor = ranked[len(ranked) // 2]
    # the frames a click track can fill: 2 chirps per period, _COARSE_WINDOW_MS frames each
    chirp_frames = round(2 * len(env) * frame / sr / ct.PERIOD_S * _COARSE_WINDOW_MS)
    cap = max(ranked[max(0, len(ranked) - 1 - chirp_frames)], _FOLD_CAP * floor) - floor
    acc, cnt = [0.0] * bins, [0] * bins
    for i, e in enumerate(env):
        b = int(((clock_s + (d + i * frame) / sr) % ct.CYCLE_S) * 1000) % bins
        # steady noise adds nothing, only what stands above the floor counts. No frame counts more than the window's
        # loudest frames a click track could fill (or 10 dB over the floor, if more): in a noisy room a chirp stands
        # only a few dB out, and a few loud taps must not outvote all of them; a clean recording is left as it is
        acc[b] += min(max(0.0, e - floor), cap)
        cnt[b] += 1
    folded = [a / c if c else 0.0 for a, c in zip(acc, cnt, strict=True)]
    ring = folded + folded[:_COARSE_WINDOW_MS]
    prefix = [0.0]
    for v in ring:
        prefix.append(prefix[-1] + v)
    chirp_energy = [prefix[p + _COARSE_WINDOW_MS] - prefix[p] for p in range(bins)]
    period_ms = round(ct.PERIOD_S * 1000)
    offsets = [k * period_ms + g for k in range(ct.CYCLE_CLICKS) for g in (0, ct.gap_ms(k))]
    score = [0.0] * bins
    for o in offsets:
        shifted = chirp_energy[o:] + chirp_energy[:o]
        score = list(map(operator.add, score, shifted))
    best = max(range(bins), key=score.__getitem__)
    median = sorted(score)[bins // 2]
    runner_up = max(
        (s for p, s in enumerate(score) if min((p - best) % bins, (best - p) % bins) > _RUNNER_UP_MS), default=median
    )
    if score[best] <= median:
        return best, 0.0
    # a click track repeats every PERIOD_S, so the best phase shifted by a period still matches every first chirp:
    # clean, the quality is ~2; a phase that only matches noise has a runner-up as good as itself (~1)
    return best, (score[best] - median) / max(runner_up - median, 1e-12 * score[best])


def _earliest_peak(profile: list[float]) -> int | None:
    """Index of the earliest interior local maximum within _COHERENT_EARLIER of the highest (both above the floor);
    None when the highest lies on the search's edge (the arrival is outside it) or nothing stands out."""
    top, low = max(profile), sorted(profile)[len(profile) // 4]
    last = len(profile) - 1
    if top <= low or profile.index(top) in (0, last):
        return None
    threshold = low + _COHERENT_EARLIER * (top - low)
    for i in range(1, last):
        v = profile[i]
        if v >= threshold and v >= profile[i - 1] and v >= profile[i + 1]:
            return i
    return None


def _coherent(x: list[float], env: list[float], sr: int, clock_s: float) -> _Coherent | None:
    clock_s %= ct.CYCLE_S  # only the clock's place in the cycle matters; the rest works relative to the window
    coarse_ms, quality = _coarse_phase(env, sr, clock_s)
    c, q = _quadrature(sr)
    n_t, n_x = len(c), len(x)
    half = round(sr * _COHERENT_REFINE_MS / 1000)
    # every expected chirp of the window: (sample where it starts at the coarse phase, click id); a bounded count
    # of cycles, from the one starting before the window to the one after it
    first = coarse_ms / 1000 - clock_s  # a cycle's start relative to the window, within one cycle of it
    chirps: list[tuple[int, int]] = []
    for j in range(-1, math.ceil(n_x / sr / ct.CYCLE_S) + 2):
        for k in range(ct.CYCLE_CLICKS):
            for g in (0.0, ct.gap_ms(k) / 1000):
                s = round((first + j * ct.CYCLE_S + k * ct.PERIOD_S + g) * sr)
                if s - half >= 0 and s + half + n_t <= n_x:
                    chirps.append((s, j * ct.CYCLE_CLICKS + k))
    if len(chirps) < 2:
        return None
    # refine: the matched-filter energy summed over every chirp, on a grid, then to the sample around its peak
    step = max(1, sr // 16000)
    grid = list(range(-half, half + 1, step))
    energy = [[_mf_energy(x, c, q, s + dlt) for dlt in grid] for s, _ in chirps]
    profile = [sum(col) for col in zip(*energy, strict=True)]
    pick = _earliest_peak(profile)  # the direct sound arrives before its reflections
    expected = len({click for _, click in chirps})
    if pick is None:  # the summed arrival lies on the search's edge: the clicks do not line up on one
        return _Coherent(phase_ms=None, quality=quality, chirps=len(chirps), expected=expected, heard=0, spread_ms=0.0)
    best_d, best_e = grid[pick], profile[pick]
    for dlt in range(max(-half, grid[pick] - step + 1), min(half, grid[pick] + step - 1) + 1):
        if dlt != grid[pick]:
            e = sum(_mf_energy(x, c, q, s + dlt) for s, _ in chirps)
            if e > best_e:
                best_d, best_e = dlt, e
    # the room's energy at the same filter: a second after (or before) a click the track is silent
    noise = []
    for s, _ in chirps:
        quiet = s + sr if s + sr + 2 * n_t <= n_x else s - sr
        noise += [_mf_energy(x, c, q, i) for i in (quiet - n_t, quiet, quiet + n_t) if 0 <= i <= n_x - n_t]
    noise.sort()
    room = noise[len(noise) // 2] if noise else 0.0
    clicks: dict[int, list[list[float]]] = {}
    for (_, click), row in zip(chirps, energy, strict=True):
        clicks.setdefault(click, []).append(row)
    # each heard click's own arrival (its earliest strong peak, the same rule) around the window's: a speaker whose
    # timing wanders during the window shows here, the sum alone would average it into one confident number
    devs = []
    for rows in clicks.values():
        per = [sum(col) / len(rows) for col in zip(*rows, strict=True)]
        own = _earliest_peak(per)
        at = own if own is not None else per.index(max(per))  # on the search's edge: it arrived at least that far off
        # heard: both chirps of the pair stand out there - a tap can line up with one chirp, hardly with its partner
        if len(rows) == 2 and min(r[at] for r in rows) > _HEARD_RATIO * room:
            devs.append((grid[at] - grid[pick]) / sr * 1000)
    spread = math.sqrt(sum(v * v for v in devs) / len(devs)) if devs else 0.0
    phase = ((coarse_ms / 1000 + best_d / sr - ct.LEAD_IN_S) * 1000.0) % (ct.CYCLE_S * 1000.0)
    return _Coherent(
        phase_ms=phase, quality=quality, chirps=len(chirps), expected=expected, heard=len(devs), spread_ms=spread
    )


def _min_quality(expected: int) -> float:
    return _QUALITY_BY_CLICKS.get(expected, _QUALITY_MANY)


def _coherent_measurement(member: str, found: _Coherent | None) -> Measurement:
    """``clicks`` counts the clicks heard. ``confidence`` here scales with the quality margin over the gate (0 at
    the gate, 1 at a clean ~2); the per-click path scales it with the clicks' SNR over MIN_SNR_DB: both are 0..1,
    1 meaning "as good as it gets", but they are not the same measure."""
    if found is None or found.expected < COHERENT_MIN_CLICKS:
        return Measurement(
            member=member, status="not_heard", reason="too_few_clicks", clicks=found.heard if found else 0
        )
    gate = _min_quality(found.expected)
    if found.quality < gate:
        return Measurement(member=member, status="not_heard", reason="low_snr", clicks=found.heard)
    if found.phase_ms is None:
        return Measurement(member=member, status="not_heard", reason="spread")
    if found.heard < COHERENT_MIN_CLICKS:
        return Measurement(member=member, status="not_heard", reason="too_few_clicks", clicks=found.heard)
    confidence = max(0.0, min(1.0, (found.quality - gate) / max(2.0 - gate, 0.1)))
    stable = found.spread_ms <= MAX_SPREAD_MS
    return Measurement(
        member=member,
        status="ok" if stable else "unstable",
        reason="" if stable else "spread",
        phase_ms=round(found.phase_ms, 2),
        clicks=found.heard,
        spread_ms=round(found.spread_ms, 2),
        confidence=round(confidence, 3),
    )


def estimate_coherent(recording: Recording, *, epoch_s: float = 0.0) -> Measurement:
    """The click track's phase from all clicks of the window at once: works where each chirp alone is too weak
    to detect (issue #553)."""
    if not _valid(recording, epoch_s):
        return _invalid(recording.member)
    x = pcm_to_floats(recording.pcm)
    env = band_envelope(x, recording.sample_rate)
    found = _coherent(x, env, recording.sample_rate, recording.start_time_s - epoch_s)
    return _coherent_measurement(recording.member, found)


def estimate(recording: Recording, *, epoch_s: float = 0.0) -> Measurement:
    """The per-click estimate where it holds (the most precise on a clean recording); where it does not hear the
    clicks, the coherent estimate when that measures them (status ok).

    A per-click verdict on clicks it did find stands: ``unstable`` (they disagree: the coherent sum would average
    a speaker whose timing moved into one confident number) and ``low_snr`` with the sequence found."""
    if not _valid(recording, epoch_s):
        return _invalid(recording.member)
    x = pcm_to_floats(recording.pcm)
    env = band_envelope(x, recording.sample_rate)
    per_click, _weak_phase = _per_click(recording, epoch_s, x, env)
    if per_click.status != "not_heard" or per_click.clicks >= MIN_CLICKS:
        return per_click
    found = _coherent(x, env, recording.sample_rate, recording.start_time_s - epoch_s)
    coherent = _coherent_measurement(recording.member, found)
    return coherent if coherent.status == "ok" else per_click
