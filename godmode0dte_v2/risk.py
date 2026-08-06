"""v2 risk: aggressive by design, coherent by law.

Three rules, none bypassable by the conviction detector:

  1. Per-trade premium at risk = conviction_risk_pct of live equity,
     CLAMPED to the remaining daily-loss headroom. The daily breaker is
     the one survival lock this style keeps; no single trade may be able
     to blow through it.
  2. Daily loss limit (default 25%): breach -> flatten everything, lock
     the day, persist the lockout (same disk pattern as v1).
  3. Max trades/day (default 2): heavy hits are rare by definition;
     a third "perfect" setup on one day means the definition is wrong.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from godmode0dte.monitoring.logging import get_logger
from godmode0dte_v2.config import V2Config

log = get_logger("v2.risk")


class V2Risk:
    def __init__(self, cfg: V2Config, session_date: date) -> None:
        self._cfg = cfg
        self._session_date = session_date
        self._path = Path(cfg.sizing.lockout_file)
        self._start_equity: Optional[float] = None
        self._equity: float = 0.0
        self._trades_today = 0
        self._locked = False
        self._lock_reason = ""
        self._restore()

    # -- state ----------------------------------------------------------

    def update_equity(self, equity: float) -> None:
        if equity <= 0:
            return
        self._equity = equity
        if self._start_equity is None:
            self._start_equity = equity
        if not self._locked and self.day_loss_pct() >= self._cfg.sizing.daily_loss_limit_pct:
            self.lock(f"daily loss {self.day_loss_pct():.1f}% >= "
                      f"{self._cfg.sizing.daily_loss_limit_pct}% limit")

    def day_loss_pct(self) -> float:
        if not self._start_equity or self._equity <= 0:
            return 0.0
        return max(0.0, 100.0 * (self._start_equity - self._equity) / self._start_equity)

    def lock(self, reason: str) -> None:
        if not self._locked:
            self._locked = True
            self._lock_reason = reason
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps({
                    "date": self._session_date.isoformat(), "reason": reason,
                    "ts": datetime.now(timezone.utc).isoformat()}))
            except OSError as e:
                log.error("v2_lockout_persist_failed", error=str(e))
            log.error("v2_locked", reason=reason)

    def record_trade(self) -> None:
        self._trades_today += 1

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def lock_reason(self) -> str:
        return self._lock_reason

    @property
    def trades_today(self) -> int:
        return self._trades_today

    # -- the sizing law --------------------------------------------------

    def size_premium(self, option_mid: float, friction_per_contract: float) -> tuple[int, float, str]:
        """(contracts, premium_at_risk, detail) for a perfect setup.

        Risk budget = conviction% of equity, clamped to remaining daily
        headroom. Returns (0, 0, why) when the law refuses.
        """
        s = self._cfg.sizing
        if self._locked:
            return 0, 0.0, f"locked: {self._lock_reason}"
        if self._equity < s.min_equity:
            return 0, 0.0, f"equity {self._equity:.0f} < min {s.min_equity:.0f}"
        if self._trades_today >= s.max_trades_per_day:
            return 0, 0.0, f"{self._trades_today} trades today (max {s.max_trades_per_day})"
        if option_mid <= 0:
            return 0, 0.0, "no option quote"
        start = self._start_equity or self._equity
        headroom = start * (s.daily_loss_limit_pct / 100.0) - (start - self._equity)
        budget = min(self._equity * (s.conviction_risk_pct / 100.0), max(0.0, headroom))
        per_contract = option_mid * 100.0 + friction_per_contract
        contracts = math.floor(budget / per_contract)
        if contracts < 1:
            return 0, 0.0, (f"budget ${budget:.0f} (conviction {s.conviction_risk_pct}% "
                            f"clamped to day headroom) < 1 contract ${per_contract:.0f}")
        risk = contracts * per_contract
        assert risk <= budget + 1e-6
        detail = (f"{contracts}x @ ${option_mid:.2f} = ${risk:.0f} at risk "
                  f"({100 * risk / self._equity:.1f}% of equity; day headroom ${headroom:.0f})")
        return contracts, risk, detail

    # -- persistence ------------------------------------------------------

    def _restore(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            self._locked, self._lock_reason = True, "unreadable v2 lockout (fail-safe)"
            return
        if payload.get("date") == self._session_date.isoformat():
            self._locked = True
            self._lock_reason = f"restored: {payload.get('reason', '')}"
            log.error("v2_lockout_restored", reason=self._lock_reason)
