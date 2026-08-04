"""Priority exit engine — the arbitrated table from docs/DESIGN_SPEC.md §7.

Strict numeric pre-emption; first hit wins per position:

  P0  circuit breaker (daily -6%, kill, heat > 8.5% reconcile) -> flatten all, urgent
  P1  15:30 ET force-flat -> unconditional, urgent
  P2  heat  Σ max(entry_debit, mark) > 7% x E -> close largest-heat position
  P3  hard stop: mark <= 0.50 x debit on 2 consecutive marks -> urgent
  P4a profit: mark >= min(1.65 x debit, 0.80 x width)
  P4b structure: 1m close back through the OR trigger AND P&L < +10% of debit
  P5  time: held >= 90 min with mark < 1.10 x debit; or open at 14:50 with mark < debit

Stops fire on the SPREAD MARK, never the underlying alone — P4b is the only
underlying-referencing rule and it is confirmation, not the stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from godmode0dte.config import ExitConfig
from godmode0dte.models import Direction, Position
from godmode0dte.risk.governor import RiskGovernor


@dataclass(frozen=True)
class ExitDecision:
    trade_id: str
    reason: str
    urgency: str          # "normal" | "urgent"
    detail: str


class ExitEngine:
    def __init__(self, cfg: ExitConfig, governor: RiskGovernor, tz: str = "America/New_York") -> None:
        self._cfg = cfg
        self._gov = governor
        self._tz = ZoneInfo(tz)
        self._stop_hits: dict[str, int] = {}     # P3 consecutive-mark counter

    def evaluate(self, now: datetime, underlying_price: Optional[float]) -> list[ExitDecision]:
        decisions: list[ExitDecision] = []
        open_positions = self._gov.open_positions
        if not open_positions:
            return decisions
        now_et = now.astimezone(self._tz)

        # P0 — circuit breaker: flatten everything, urgently.
        if self._gov.must_flatten:
            return [ExitDecision(p.trade_id, "circuit_breaker", "urgent",
                                 self._gov.breaker.trip_reason)
                    for p in open_positions]

        # P1 — force-flat time: unconditional.
        if now_et.time() >= self._cfg.force_flat:
            return [ExitDecision(p.trade_id, "force_flat", "urgent",
                                 f"past {self._cfg.force_flat} ET")
                    for p in open_positions]

        # P2 — heat breach (marks moved after entry): close the largest-heat position.
        if self._gov.heat_pct > self._gov.max_heat_pct:
            worst = max(open_positions, key=lambda p: p.risk_dollars)
            decisions.append(ExitDecision(worst.trade_id, "heat_breach", "urgent",
                                          f"heat {self._gov.heat_pct:.1f}% above cap"))

        already = {d.trade_id for d in decisions}
        for p in open_positions:
            if p.trade_id in already:
                continue
            d = self._evaluate_position(p, now, now_et, underlying_price)
            if d is not None:
                decisions.append(d)
        return decisions

    def _evaluate_position(self, p: Position, now: datetime, now_et: datetime,
                           underlying_price: Optional[float]) -> Optional[ExitDecision]:
        mark = p.current_value
        debit = p.entry_debit

        # P3 — hard stop on the spread mark, 2 consecutive marks required.
        if mark > 0 and mark <= debit * (1 - self._cfg.hard_stop_pct / 100.0):
            hits = self._stop_hits.get(p.trade_id, 0) + 1
            self._stop_hits[p.trade_id] = hits
            if hits >= 2:
                return ExitDecision(p.trade_id, "hard_stop", "urgent",
                                    f"mark {mark:.2f} <= stop on debit {debit:.2f} (x{hits})")
        else:
            self._stop_hits.pop(p.trade_id, None)

        # P4a — profit target: 1.65 x debit, capped at 0.80 x width.
        target = min(debit * self._cfg.profit_target_mult,
                     p.vertical.width * self._cfg.profit_target_width_frac)
        if mark >= target:
            return ExitDecision(p.trade_id, "profit_target", "normal",
                                f"mark {mark:.2f} >= target {target:.2f}")

        # P4b — structure stop: back through the OR trigger with P&L < +10% of debit.
        if self._cfg.structure_stop and underlying_price is not None:
            broke = (p.vertical.direction is Direction.LONG and underlying_price < p.or_mid) or (
                p.vertical.direction is Direction.SHORT and underlying_price > p.or_mid
            )
            if broke and (mark - debit) < 0.10 * debit:
                return ExitDecision(p.trade_id, "structure_stop", "normal",
                                    f"price {underlying_price:.2f} through OR trigger "
                                    f"{p.or_mid:.2f}, P&L < +10%")

        # P5 — time stops: 90-min stale hold, and the 14:50 losing flush.
        held_min = (now - p.entry_ts).total_seconds() / 60.0
        if held_min >= self._cfg.max_hold_min and mark < 1.10 * debit:
            return ExitDecision(p.trade_id, "time_stop", "normal",
                                f"held {held_min:.0f}min with mark < 1.10 x debit")
        if now_et.time() >= self._cfg.stale_flush and mark < debit:
            return ExitDecision(p.trade_id, "time_stop", "normal",
                                f"open at {self._cfg.stale_flush} with mark < debit")
        return None
