"""Dependency-free relative acoustic delay estimator for calibration beta."""

from __future__ import annotations

import math
import struct
from dataclasses import asdict, dataclass


def build_calibration_pcm(*, sample_rate: int = 48000, duration_seconds: int = 8) -> bytes:
    """Build a deterministic stereo chirp with lead-in for suspended sinks."""
    total_frames = sample_rate * duration_seconds
    probe_frames = min(round(sample_rate * 0.35), total_frames)
    lead_in_frames = min(round(sample_rate * 0.75), max(0, total_frames - probe_frames))
    frames = bytearray()
    for index in range(total_frames):
        probe_index = index - lead_in_frames
        if 0 <= probe_index < probe_frames and probe_frames > 1:
            elapsed = probe_index / sample_rate
            probe_duration = probe_frames / sample_rate
            sweep_rate = (3600.0 - 900.0) / probe_duration
            phase = 2 * math.pi * (900.0 * elapsed + 0.5 * sweep_rate * elapsed * elapsed)
            envelope = math.sin(math.pi * probe_index / (probe_frames - 1)) ** 2
            sample = int(24000 * envelope * math.sin(phase))
        else:
            sample = 0
        frames.extend(struct.pack("<hh", sample, sample))
    return bytes(frames)


@dataclass(frozen=True, slots=True)
class RelativeDelayEstimate:
    delay_ms: float | None
    confidence: float
    valid: bool
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def estimate_relative_delay_ms(
    reference: list[float],
    target: list[float],
    *,
    sample_rate: int,
    max_lag_ms: int = 1000,
) -> RelativeDelayEstimate:
    """Estimate target-minus-reference delay from acoustic energy envelopes."""
    if sample_rate <= 0 or len(reference) < 8 or len(target) < 8:
        return RelativeDelayEstimate(None, 0.0, False, "insufficient_samples")
    # Bound CPU independently of the accepted recording duration.  Comparing
    # peak-amplitude envelopes instead of raw waveforms keeps the chirp timing
    # comparable when two speakers have different frequency/phase responses.
    longest = max(len(reference), len(target))
    stride = max(1, math.ceil(longest / 3000))
    reference = _amplitude_envelope(reference, stride)
    target = _amplitude_envelope(target, stride)
    effective_sample_rate = sample_rate / stride
    ref_energy = sum(value * value for value in reference)
    target_energy = sum(value * value for value in target)
    if ref_energy <= 1e-12 or target_energy <= 1e-12:
        return RelativeDelayEstimate(None, 0.0, False, "silence")

    max_lag = min(round(effective_sample_rate * max_lag_ms / 1000), len(reference) - 1, len(target) - 1)
    best_lag = 0
    best_score = -1.0
    for lag in range(-max_lag, max_lag + 1):
        ref_start = max(0, -lag)
        target_start = max(0, lag)
        count = min(len(reference) - ref_start, len(target) - target_start)
        if count < 8:
            continue
        dot = ref_window_energy = target_window_energy = 0.0
        for index in range(count):
            ref_value = float(reference[ref_start + index])
            target_value = float(target[target_start + index])
            dot += ref_value * target_value
            ref_window_energy += ref_value * ref_value
            target_window_energy += target_value * target_value
        denom = math.sqrt(ref_window_energy * target_window_energy)
        score = dot / denom if denom > 1e-12 else 0.0
        if score > best_score:
            best_score = score
            best_lag = lag
    valid = best_score >= 0.35
    return RelativeDelayEstimate(
        delay_ms=round(best_lag * 1000.0 / effective_sample_rate, 3) if valid else None,
        confidence=round(max(0.0, min(1.0, best_score)), 4),
        valid=valid,
        reason="" if valid else "weak_correlation",
    )


def _amplitude_envelope(values: list[float], stride: int) -> list[float]:
    envelope = [
        max(abs(float(value)) for value in values[index : index + stride]) for index in range(0, len(values), stride)
    ]
    # Browser microphones commonly apply automatic gain and leave a positive
    # noise floor after rectification.  Removing the median keeps correlation
    # focused on the short calibration probe rather than room noise.
    baseline = sorted(envelope)[len(envelope) // 2]
    return [max(0.0, value - baseline) for value in envelope]
