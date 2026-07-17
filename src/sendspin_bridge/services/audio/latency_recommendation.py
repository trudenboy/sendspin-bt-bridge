"""Pure latency recommendation model.

Recommendations are intentionally advisory and always require confirmation.
Observed transport delay, configured correction, and acoustic calibration are
kept as separate concepts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

_CODEC_FALLBACK_MS = {
    "sbc": 125,
    "aac": 125,
    "aptx": 125,
    "ldac": 125,
    "aptx_ll": 40,
    "aptx-ll": 40,
    "faststream": 40,
    "lc3": 20,
}


@dataclass(frozen=True, slots=True)
class LatencyRecommendation:
    value_ms: int | None
    source: str
    confidence: str
    explanation: str
    requires_confirmation: bool = True
    observed_bt_delay_ms: float | None = None
    backend_adjustment_ms: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_latency_recommendation(
    *,
    reported_bt_delay_ms: float | None,
    codec_name: str | None,
    calibrated_delay_ms: float | None = None,
    calibration_source: str | None = None,
) -> LatencyRecommendation:
    """Return the best available recommendation without mutating settings."""
    if calibrated_delay_ms is not None:
        value = max(0, min(5000, round(calibrated_delay_ms)))
        return LatencyRecommendation(
            value_ms=value,
            source=calibration_source or "manual_calibration",
            confidence="high",
            explanation="Previously confirmed calibration for this device.",
            observed_bt_delay_ms=reported_bt_delay_ms,
        )
    if reported_bt_delay_ms is not None:
        value = max(0, min(5000, round(reported_bt_delay_ms)))
        return LatencyRecommendation(
            value_ms=value,
            source="bluez_delay_report",
            confidence="medium",
            explanation="Delay reported by the Bluetooth speaker through AVDTP.",
            observed_bt_delay_ms=reported_bt_delay_ms,
        )
    normalized_codec = str(codec_name or "").strip().lower().replace(" ", "_")
    fallback = _CODEC_FALLBACK_MS.get(normalized_codec)
    if fallback is not None:
        return LatencyRecommendation(
            value_ms=fallback,
            source="codec_fallback",
            confidence="low",
            explanation=f"Approximate starting point for the {normalized_codec} codec.",
        )
    return LatencyRecommendation(
        value_ms=None,
        source="unavailable",
        confidence="none",
        explanation="No speaker report or trustworthy codec fallback is available.",
    )
