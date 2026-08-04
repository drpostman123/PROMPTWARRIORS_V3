"""Circuit breaker: daily loss lockout with on-disk persistence.

State machine:  ARMED -> TRIPPED -> LOCKED
- ARMED:   normal trading, monitors equity vs starting-day equity.
- TRIPPED: -6% daily loss (or manual trip) detected; flatten-all issued.
- LOCKED:  everything flat, no new entries until the next session.

The lockout is written to disk (``state/lockout.json``) so a process
restart cannot reset it — on boot, a lockout dated today re-enters
LOCKED immediately.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from godmode0dte.config import RiskConfig
from godmode0dte.monitoring.logging import get_logger

log = get_logger("circuit_breaker")


class BreakerState(str, Enum):
    ARMED = "armed"
    TRIPPED = "tripped"
    LOCKED = "locked"


class CircuitBreaker:
    """Daily loss circuit breaker. Owned exclusively by the RiskGovernor."""

    def __init__(self, cfg: RiskConfig, session_date: date) -> None:
        self._cfg = cfg
        self._session_date = session_date
        self._state = BreakerState.ARMED
        self._starting_equity: Optional[float] = None
        self._trip_reason: str = ""
        self._lockout_path = Path(cfg.lockout_file)
        self._baseline_path = self._lockout_path.with_name("baseline.json")
        self._restore_lockout()
        self._restore_baseline()

    # -- lifecycle -----------------------------------------------------

    def set_starting_equity(self, equity: float) -> None:
        """Record starting-day equity once per SESSION DAY, persisted to disk.

        Without persistence a mid-day restart re-anchors the -6% limit to
        depleted equity, allowing ~-11% cumulative before tripping (audit B6).
        """
        if self._starting_equity is None:
            self._starting_equity = equity
            self._baseline_path.parent.mkdir(parents=True, exist_ok=True)
            self._baseline_path.write_text(json.dumps(
                {"date": self._session_date.isoformat(), "starting_equity": equity}))
            log.info("breaker_armed", starting_equity=equity, limit_pct=self._cfg.daily_loss_limit_pct)

    def _restore_baseline(self) -> None:
        if not self._baseline_path.exists():
            return
        try:
            payload = json.loads(self._baseline_path.read_text())
            if payload.get("date") == self._session_date.isoformat():
                self._starting_equity = float(payload["starting_equity"])
                log.info("breaker_baseline_restored", starting_equity=self._starting_equity)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            log.error("breaker_baseline_unreadable")

    def check(self, current_equity: float) -> BreakerState:
        """Evaluate the daily loss limit. Called on every equity update."""
        if self._state is not BreakerState.ARMED or self._starting_equity is None:
            return self._state
        drawdown_pct = 100.0 * (self._starting_equity - current_equity) / self._starting_equity
        if drawdown_pct >= self._cfg.daily_loss_limit_pct:
            self.trip(f"daily loss {drawdown_pct:.2f}% >= {self._cfg.daily_loss_limit_pct}% limit")
        return self._state

    def trip(self, reason: str) -> None:
        """Trip the breaker. Irreversible for the session."""
        if self._state is BreakerState.ARMED:
            self._state = BreakerState.TRIPPED
            self._trip_reason = reason
            self._persist_lockout(reason)
            log.error("breaker_tripped", reason=reason)

    def confirm_flat(self) -> None:
        """Called by the governor once all positions are confirmed closed."""
        if self._state is BreakerState.TRIPPED:
            self._state = BreakerState.LOCKED
            log.error("breaker_locked", reason=self._trip_reason)

    # -- queries -------------------------------------------------------

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def allows_entries(self) -> bool:
        return self._state is BreakerState.ARMED

    @property
    def starting_equity(self) -> Optional[float]:
        return self._starting_equity

    @property
    def trip_reason(self) -> str:
        return self._trip_reason

    # -- persistence ---------------------------------------------------

    def _persist_lockout(self, reason: str) -> None:
        self._lockout_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": self._session_date.isoformat(),
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._lockout_path.write_text(json.dumps(payload, indent=2))

    def _restore_lockout(self) -> None:
        if not self._lockout_path.exists():
            return
        try:
            payload = json.loads(self._lockout_path.read_text())
        except (json.JSONDecodeError, OSError):
            # Unreadable lockout file: fail SAFE — assume locked today.
            self._state = BreakerState.LOCKED
            self._trip_reason = "unreadable lockout file (fail-safe)"
            log.error("breaker_restore_failsafe")
            return
        if payload.get("date") == self._session_date.isoformat():
            self._state = BreakerState.LOCKED
            self._trip_reason = f"restored lockout: {payload.get('reason', 'unknown')}"
            log.error("breaker_restored_locked", reason=self._trip_reason)
