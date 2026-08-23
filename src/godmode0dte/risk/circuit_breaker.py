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
        self._peak_path = self._lockout_path.with_name("peak.json")
        self._peak: float = 0.0
        self._restore_lockout()
        self._restore_baseline()
        self._restore_peak()

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
        """Evaluate the daily loss limit AND the survival rule (drawdown from
        all-time peak). Called on every equity update."""
        if current_equity > 0:
            self._update_peak(current_equity)
        if self._state is not BreakerState.ARMED or self._starting_equity is None:
            return self._state
        drawdown_pct = 100.0 * (self._starting_equity - current_equity) / self._starting_equity
        if drawdown_pct >= self._cfg.daily_loss_limit_pct:
            self.trip(f"daily loss {drawdown_pct:.2f}% >= {self._cfg.daily_loss_limit_pct}% limit")
        # Survival-first rule (round 6): a drawdown from PEAK beyond the
        # configured limit trips a PERSISTENT lock — it survives restarts and
        # new sessions, and only GODMODE_ACK_DRAWDOWN=YES re-arms it. This is
        # the "measurement capital must survive" law, not a daily breaker.
        if self._peak > 0 and current_equity > 0:
            dd_peak = 100.0 * (self._peak - current_equity) / self._peak
            if dd_peak >= self._cfg.max_drawdown_from_peak_pct:
                self._persist_peak(survival_tripped=True)
                self.trip(f"SURVIVAL: drawdown from peak {dd_peak:.1f}% >= "
                          f"{self._cfg.max_drawdown_from_peak_pct}% — persistent lock; "
                          "re-arm requires GODMODE_ACK_DRAWDOWN=YES after review")
        return self._state

    # -- survival rule persistence -------------------------------------

    def _update_peak(self, equity: float) -> None:
        if equity > self._peak:
            self._peak = equity
            self._persist_peak()

    def _persist_peak(self, survival_tripped: bool = False) -> None:
        try:
            self._peak_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"peak": self._peak}
            if survival_tripped:
                payload["survival_tripped"] = True
            self._peak_path.write_text(json.dumps(payload))
        except OSError as e:
            log.error("peak_persist_failed", error=str(e))

    def _restore_peak(self) -> None:
        import os
        if not self._peak_path.exists():
            return
        try:
            payload = json.loads(self._peak_path.read_text())
            self._peak = float(payload.get("peak", 0.0))
            if payload.get("survival_tripped"):
                if os.environ.get("GODMODE_ACK_DRAWDOWN") == "YES":
                    # Operator acknowledged: clear the flag, keep the peak.
                    log.error("survival_lock_acknowledged_and_cleared", peak=self._peak)
                    self._persist_peak(survival_tripped=False)
                else:
                    self._state = BreakerState.LOCKED
                    self._trip_reason = ("survival lock: drawdown from peak exceeded "
                                         f"{self._cfg.max_drawdown_from_peak_pct}%; set "
                                         "GODMODE_ACK_DRAWDOWN=YES to re-arm after review")
                    log.error("survival_lock_active", peak=self._peak)
        except (json.JSONDecodeError, ValueError, TypeError, OSError):
            log.error("peak_file_unreadable")

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
        payload = {
            "date": self._session_date.isoformat(),
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._lockout_path.parent.mkdir(parents=True, exist_ok=True)
            self._lockout_path.write_text(json.dumps(payload, indent=2))
        except OSError as e:
            # In-memory TRIPPED stays authoritative for this process; only the
            # cross-restart persistence is lost (audit R3 #6b).
            log.error("lockout_persist_failed", error=str(e))

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
