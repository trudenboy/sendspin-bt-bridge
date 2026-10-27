"""Calibrate the coherent click-track estimator's quality gate (sendspin_sync.estimator, issue #553).

Prints, per window length and noise type, the quality noise alone reaches (grouped by the clicks the window holds)
and the quality the click track reaches at a few chirp-band SNRs. The gate must sit above every noise-only value.

SNR here: the click track's highest 1 ms band-energy frame over the noise's median band-energy frame
(``estimator.band_envelope``), i.e. a peak SNR for the 2-6 kHz chirp band.

    uv run python scripts/calibrate_sync_estimator.py [--seeds 60]
"""

from __future__ import annotations

import argparse
import io
import math
import random
from collections import defaultdict

from sendspin_sync import click_track as ct
from sendspin_sync import estimator as est
from sendspin_sync.models import Recording

SR = 16000
DURATIONS = (2.0, 3.0, 4.0, 6.0, 10.0)


def white(n: int, rnd: random.Random) -> list[float]:
    return [rnd.gauss(0.0, 1.0) for _ in range(n)]


def band(n: int, rnd: random.Random) -> list[float]:
    g = white(n + 2, rnd)
    return [g[i + 2] - g[i] for i in range(n)]  # a lobe on 4 kHz at 16 kHz: the chirp band


def taps(n: int, rnd: random.Random) -> list[float]:
    """Sparse impulsive noise: taps (a few ms of decaying noise) at 2-20 a second on a faint floor."""
    x = [rnd.gauss(0.0, 0.01) for _ in range(n)]
    rate = rnd.uniform(2.0, 20.0)
    i = 0
    while True:
        i += int(rnd.expovariate(rate) * SR)
        if i >= n:
            return x
        level, length = rnd.uniform(0.2, 1.0), int(rnd.uniform(0.001, 0.006) * SR)
        for j in range(i, min(n, i + length)):
            x[j] += level * rnd.gauss(0.0, 1.0) * math.exp(-(j - i) / (0.3 * length + 1))


NOISES = {"white": white, "band": band, "taps": taps}


def track() -> list[float]:
    buf = io.BytesIO()
    ct.write_wav(buf, duration_s=40.0, sample_rate=SR)
    pcm = buf.getvalue()[len(ct.wav_header(duration_s=40.0, sample_rate=SR)) :]
    samples = memoryview(pcm).cast("h")
    return [samples[i] / 32768.0 for i in range(0, len(samples), 2)]


def coherent(x: list[float], clock_s: float) -> est._Coherent | None:
    return est._coherent(x, est.band_envelope(x, SR), SR, clock_s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--noise-only", action="store_true")
    args = ap.parse_args()
    seeds = args.seeds
    print("noise alone: max quality by clicks in the window (n windows), and windows the estimator would accept")
    worst: dict[int, float] = defaultdict(float)
    worst_heard: dict[int, float] = defaultdict(float)  # among windows passing every gate but quality
    for dur in DURATIONS:
        n = round(dur * SR)
        for name, make in NOISES.items():
            by_clicks: dict[int, list[float]] = defaultdict(list)
            accepted = unstable = 0
            for seed in range(seeds):
                rnd = random.Random(f"{name}-{dur}-{seed}")
                x = [0.05 * v for v in make(n, rnd)]
                found = coherent(x, rnd.uniform(0.0, 100.0))
                if found is None:
                    continue
                by_clicks[found.expected].append(found.quality)
                worst[found.expected] = max(worst[found.expected], found.quality)
                if found.expected >= est.COHERENT_MIN_CLICKS and found.heard >= est.COHERENT_MIN_CLICKS:
                    worst_heard[found.expected] = max(worst_heard[found.expected], found.quality)
                status = est._coherent_measurement("a", found).status
                accepted += status == "ok"
                unstable += status == "unstable"
            cells = ", ".join(f"{k}: {max(v):.2f} ({len(v)})" for k, v in sorted(by_clicks.items()))
            print(flush=True, end="")
            print(f"  {dur:4.0f} s {name:5s}  ok {accepted} unstable {unstable} /{seeds}  {cells}")
    print("  worst by clicks: " + ", ".join(f"{k}: {v:.2f}" for k, v in sorted(worst.items())))
    print("  worst passing the other gates: " + ", ".join(f"{k}: {v:.2f}" for k, v in sorted(worst_heard.items())))
    if args.noise_only:
        return
    print("click track in band noise: quality min/median, and estimate() ok within 1 ms")
    clicks = track()
    for dur in (5.9, *DURATIONS[2:], 18.0):
        n = round(dur * SR)
        for snr in (10.0, 6.0, 3.0, 0.0):
            qs, ok = [], 0
            for seed in range(max(4, seeds // 6)):
                rnd = random.Random(f"sig-{dur}-{snr}-{seed}")
                lag, start = rnd.uniform(0.0, 0.5), rnd.uniform(3.0, 40.0 - dur - 2.0)
                lo = round((start - lag) * SR)
                x = clicks[lo : lo + n]
                noise = band(n, rnd)
                ex, en = est.band_envelope(x, SR), est.band_envelope(noise, SR)
                gain = math.sqrt(max(ex) / (sorted(en)[len(en) // 2] * 10 ** (snr / 10)))
                x = [v + gain * w for v, w in zip(x, noise, strict=True)]
                found = coherent(x, start)
                if found is not None:
                    qs.append(found.quality)
                pcm = b"".join(
                    int(max(-32767, min(32767, round(v * 32767)))).to_bytes(2, "little", signed=True) for v in x
                )
                m = est.estimate(Recording(member="a", start_time_s=start, sample_rate=SR, pcm=pcm))
                truth = lag * 1000.0
                if m.status == "ok" and m.phase_ms is not None:
                    ok += abs((m.phase_ms - truth + 5000.0) % 10000.0 - 5000.0) <= 1.0
            qs.sort()
            print(f"  {dur:4.1f} s {snr:5.1f} dB  q {qs[0]:.2f}/{qs[len(qs) // 2]:.2f}  ok {ok}/{max(4, seeds // 6)}")


if __name__ == "__main__":
    main()
