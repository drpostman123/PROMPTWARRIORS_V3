"""Shadow outcome book (round 6): counterfactual measurement, zero risk.

Every tradeable signal that scores >= shadow_min_score but does NOT result
in a real fill (sub-93 score, daily cap, heat, one-shot, size_zero, entry
abandoned...) is paper-followed here: entered at the combo MID at signal
time, marked from live quotes, and exited by the same priority rules the
real book uses (hard stop, profit target, 90-min stale hold, force-flat).
Exits append ``kind: shadow_exit`` rows — with the score attached — to the
decision log, so score-bucket calibration (including the buckets BELOW the
93 threshold, which real trades can never populate) accumulates outcomes
5-10x faster than live trading alone.

This module lives in the monitoring layer: it imports models and config
only, holds no broker reference, and can never place an order. Shadow
fills at MID are optimistic vs the real ladder — every consumer of
shadow rows must treat them as an UPPER bound on the edge, never proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Callable, Optional

from godmode0dte.config import AppConfig
from godmode0dte.models import Direction, Quote, VerticalSpec
from godmode0dte.monitoring.logging import get_logger

log = get_logger("shadow")


@dataclass
class ShadowPosition:
    shadow_id: str
    vertical: VerticalSpec
    entry_debit: float          # combo MID at signal time (optimistic; labeled)
    entry_ts: datetime
    score: float
    rejection_reason: str       # why the real book didn't take it
    stop_hits: int = 0
    current_value: float = 0.0


class ShadowBook:
    """Paper-follows untaken signals through the real exit rules."""

    def __init__(self, cfg: AppConfig, log_decision: Callable[[dict], None]) -> None:
        self._cfg = cfg
        self._log_decision = log_decision
        self._open: dict[str, ShadowPosition] = {}
        self._sides_today: set[Direction] = set()
        self._count = 0

    @property
    def open_count(self) -> int:
        return len(self._open)

    def maybe_open(self, score: float, vertical: VerticalSpec, mid_debit: float,
                   now: datetime, rejection_reason: str) -> None:
        if not self._cfg.signal.shadow_tracking:
            return
        if score < self._cfg.signal.shadow_min_score or mid_debit <= 0:
            return
        if len(self._open) >= self._cfg.signal.shadow_max_open:
            return
        if vertical.direction in self._sides_today:
            return                          # one shadow per side per session: no junk stacking
        self._sides_today.add(vertical.direction)
        self._count += 1
        sid = f"shadow-{self._count:04d}"
        self._open[sid] = ShadowPosition(
            shadow_id=sid, vertical=vertical, entry_debit=round(mid_debit, 2),
            entry_ts=now, score=score, rejection_reason=rejection_reason,
            current_value=round(mid_debit, 2))
        log.info("shadow_opened", shadow_id=sid, score=round(score, 2),
                 debit=round(mid_debit, 2), reason=rejection_reason)

    def mark_and_exit(self, now: datetime, now_et_time: time,
                      quote_getter: Callable[[str], Optional[Quote]]) -> None:
        """Mark every shadow from live quotes and apply the exit priorities."""
        exits_cfg = self._cfg.exits
        for sid in list(self._open):
            p = self._open[sid]
            lq = quote_getter(p.vertical.long_symbol)
            sq = quote_getter(p.vertical.short_symbol)
            if lq and lq.bid > 0 and lq.ask > 0 and sq and sq.ask > 0:
                sq_mid = sq.mid if sq.bid > 0 else sq.ask / 2.0
                p.current_value = round(max(0.0, lq.mid - sq_mid), 2)
            mark, debit = p.current_value, p.entry_debit

            reason = None
            if now_et_time >= exits_cfg.force_flat:
                reason = "force_flat"
            elif mark <= debit * (1 - exits_cfg.hard_stop_pct / 100.0):
                p.stop_hits += 1
                if p.stop_hits >= 2:
                    reason = "hard_stop"
            else:
                p.stop_hits = 0
            if reason is None:
                target = min(debit * exits_cfg.profit_target_mult,
                             p.vertical.width * exits_cfg.profit_target_width_frac)
                if mark >= target:
                    reason = "profit_target"
                elif (now - p.entry_ts).total_seconds() / 60.0 >= exits_cfg.max_hold_min \
                        and mark < 1.10 * debit:
                    reason = "time_stop"
            if reason is None:
                continue

            friction = self._cfg.execution.friction_per_contract
            pnl_net = (mark - debit) * 100 - friction     # per contract, net
            self._log_decision({
                "kind": "shadow_exit", "shadow_id": sid, "score": round(p.score, 2),
                "reason": reason, "entry_debit": debit, "exit_value": mark,
                "pnl_net_per_contract": round(pnl_net, 2),
                "win": pnl_net > 0, "held_min": round((now - p.entry_ts).total_seconds() / 60, 1),
                "untaken_because": p.rejection_reason,
                "fill_basis": "mid_optimistic",           # upper bound, never proof
            })
            log.info("shadow_closed", shadow_id=sid, reason=reason,
                     pnl_net=round(pnl_net, 2), score=round(p.score, 2))
            del self._open[sid]
