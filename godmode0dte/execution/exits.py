"""Priority exit engine.

Rules evaluate in strict priority order — the first hit wins and higher
priorities always override lower ones:

  1. circuit_breaker  breaker tripped/locked -> flatten (urgent)
  2. heat_breach      heat above cap (equity dropped) -> trim worst position
  3. hard_stop        vertical value <= (1 - hard_stop_pct) x debit
  4. structure_stop   underlying closes back through the OR midpoint
  5. time_stop        past exits.time_stop (ET) -> close (normal)
  6. force_flat       past exits.force_flat -> close (urgent), no exceptions
  7. profit_target    value >= debit x (1 + target_pct(score))
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


def profit_target_pct(score: float, cfg: ExitConfig) -> float:
    """Score-dependent profit target (% of debit)."""
    pct = min(cfg.profit_target_pct.values())
    for band, p in sorted(cfg.profit_target_pct.items()):
        if score >= band:
            pct = p
    return pct


class ExitEngine:
    def __init__(self, cfg: ExitConfig, governor: RiskGovernor, tz: str = "America/New_York") -> None:
        self._cfg = cfg
        self._gov = governor
        self._tz = ZoneInfo(tz)

    def evaluate(self, now: datetime, underlying_price: Optional[float]) -> list[ExitDecision]:
        decisions: list[ExitDecision] = []
        open_positions = self._gov.open_positions
        if not open_positions:
            return decisions
        now_et = now.astimezone(self._tz).time()

        # 1. Circuit breaker: flatten everything, urgently.
        if self._gov.must_flatten:
            return [
                ExitDecision(p.trade_id, "circuit_breaker", "urgent",
                             self._gov.breaker.trip_reason)
                for p in open_positions
            ]

        # 2. Heat breach (equity dropped after entry): trim the worst performer.
        if self._gov.heat_pct > self._gov.max_heat_pct and len(open_positions) > 0:
            worst = min(open_positions, key=lambda p: p.pnl)
            decisions.append(ExitDecision(worst.trade_id, "heat_breach", "urgent",
                                          f"heat {self._gov.heat_pct:.1f}% above cap"))

        already = {d.trade_id for d in decisions}
        for p in open_positions:
            if p.trade_id in already:
                continue
            d = self._evaluate_position(p, now_et, underlying_price)
            if d is not None:
                decisions.append(d)
        return decisions

    def _evaluate_position(self, p: Position, now_et, underlying_price: Optional[float]) -> Optional[ExitDecision]:
        value = p.current_value
        # 3. Hard stop
        if value > 0 and value <= p.entry_debit * (1 - self._cfg.hard_stop_pct / 100.0):
            return ExitDecision(p.trade_id, "hard_stop", "urgent",
                                f"value {value:.2f} <= stop on debit {p.entry_debit:.2f}")
        # 4. Structure stop: close back through OR mid against the position.
        if self._cfg.structure_stop and underlying_price is not None:
            broke = (p.vertical.direction is Direction.LONG and underlying_price < p.or_mid) or (
                p.vertical.direction is Direction.SHORT and underlying_price > p.or_mid
            )
            if broke:
                return ExitDecision(p.trade_id, "structure_stop", "normal",
                                    f"price {underlying_price:.2f} back through OR mid {p.or_mid:.2f}")
        # 5/6. Time stops
        if now_et >= self._cfg.force_flat:
            return ExitDecision(p.trade_id, "force_flat", "urgent", f"past {self._cfg.force_flat}")
        if now_et >= self._cfg.time_stop:
            return ExitDecision(p.trade_id, "time_stop", "normal", f"past {self._cfg.time_stop}")
        # 7. Profit target
        target = profit_target_pct(p.score_at_entry, self._cfg)
        if value >= p.entry_debit * (1 + target / 100.0):
            return ExitDecision(p.trade_id, "profit_target", "normal",
                                f"value {value:.2f} >= +{target:.0f}% of debit")
        return None
