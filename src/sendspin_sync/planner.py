"""Turn measured phases into new delays: the later a player sounds, the more delay it gets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sendspin_sync import click_track as ct
from sendspin_sync.models import Measurement, Member, Proposal, ProposalRow

if TYPE_CHECKING:
    from collections.abc import Iterable

MAX_DELAY_MS = 5000
WARN_ABOVE_MS = 2000
VERIFY_SPREAD_MS = 5.0


class PlanError(ValueError):
    pass


def unwrap_lag_ms(phase_ms: float, ref_phase_ms: float, cycle_ms: float = ct.CYCLE_S * 1000.0) -> float:
    lag = (phase_ms - ref_phase_ms) % cycle_ms
    return lag - cycle_ms if lag > cycle_ms / 2 else lag


def spread_ms(measurements: Iterable[Measurement]) -> float:
    oks = [m for m in measurements if m.status == "ok" and m.phase_ms is not None]
    if len(oks) < 2:
        return 0.0
    lags = [unwrap_lag_ms(m.phase_ms, oks[0].phase_ms) for m in oks]  # type: ignore[arg-type]
    return max(lags) - min(lags)


def plan(measurements: Iterable[Measurement], members: Iterable[Member], *, reference: str | None = None) -> Proposal:
    members = list(members)
    by_id = {m.ma_player_id: m for m in members}
    oks = {
        m.member: m
        for m in measurements
        if m.status == "ok" and m.phase_ms is not None and by_id.get(m.member) and by_id[m.member].adjustable
    }
    if not oks:
        raise PlanError("no usable measurement")
    ref = reference or next(iter(oks))
    if ref not in oks:
        raise PlanError(f"reference {ref} was not measured")
    ref_phase = oks[ref].phase_ms or 0.0
    lags = {mid: unwrap_lag_ms(m.phase_ms or 0.0, ref_phase) for mid, m in oks.items()}
    earliest = min(lags.values())
    wanted = {mid: (by_id[mid].delay_ms or 0) + (lags[mid] - earliest) for mid in oks}
    unverified = tuple(m.ma_player_id for m in members if m.ma_player_id not in oks)
    every_adjustable_measured = all(m.ma_player_id in oks for m in members if m.adjustable)
    if every_adjustable_measured:
        floor = min(wanted.values())
        wanted = {mid: v - floor for mid, v in wanted.items()}
    rows, warnings, residuals = [], [], []
    for mid, v in wanted.items():
        before = by_id[mid].delay_ms or 0
        after = max(0, min(MAX_DELAY_MS, round(v)))
        clamped = after != round(v)
        residuals.append(round(v) - after)
        rows.append(ProposalRow(member=mid, before_ms=before, after_ms=after, delta_ms=after - before, clamped=clamped))
    predicted = float(max(residuals) - min(residuals)) if residuals else 0.0
    if any(r.after_ms > WARN_ABOVE_MS for r in rows):
        warnings.append(
            f"A delay above {WARN_ABOVE_MS} ms needs the server to send audio far ahead; small buffers may underrun."
        )
    if any(r.clamped for r in rows):
        warnings.append(f"Some delays were clamped to {MAX_DELAY_MS} ms; predicted residual {predicted:.0f} ms.")
    return Proposal(
        reference=ref,
        rows=tuple(rows),
        predicted_spread_ms=round(predicted, 1),
        unverified=unverified,
        warnings=tuple(warnings),
    )
