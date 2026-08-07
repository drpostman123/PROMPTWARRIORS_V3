"""Trading-day state machine with logged transitions.

States:
  BOOT           process start, config load, broker reconcile
  PRE_MARKET     before 09:30 ET
  OPENING_RANGE  09:30-09:35, accumulating the OR
  SCANNING       OR complete, hunting for >= min_score setups
  ENTERING       intent approved, working the entry ladder
  MANAGING       >= 1 open position, exit engine active
  FLATTENING     closing everything (breaker / force-flat)
  LOCKED_OUT     circuit breaker locked — no entries until next session
  END_OF_DAY     after force-flat time, flat, reporting

Every transition is logged as a structured TransitionEvent; illegal
transitions raise (they indicate a programming error, never a market
condition).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from godmode0dte.monitoring.logging import get_logger

log = get_logger("state_machine")


class TradingState(str, Enum):
    BOOT = "boot"
    PRE_MARKET = "pre_market"
    OPENING_RANGE = "opening_range"
    SCANNING = "scanning"
    ENTERING = "entering"
    MANAGING = "managing"
    FLATTENING = "flattening"
    LOCKED_OUT = "locked_out"
    END_OF_DAY = "end_of_day"


_ALLOWED: dict[TradingState, set[TradingState]] = {
    TradingState.BOOT: {TradingState.PRE_MARKET, TradingState.LOCKED_OUT, TradingState.MANAGING},
    TradingState.PRE_MARKET: {TradingState.OPENING_RANGE, TradingState.LOCKED_OUT},
    TradingState.OPENING_RANGE: {TradingState.SCANNING, TradingState.LOCKED_OUT,
                                 TradingState.END_OF_DAY},
    TradingState.SCANNING: {TradingState.ENTERING, TradingState.END_OF_DAY,
                            TradingState.LOCKED_OUT, TradingState.MANAGING},
    TradingState.ENTERING: {TradingState.MANAGING, TradingState.SCANNING,
                            TradingState.FLATTENING, TradingState.LOCKED_OUT},
    TradingState.MANAGING: {TradingState.SCANNING, TradingState.FLATTENING,
                            TradingState.END_OF_DAY, TradingState.LOCKED_OUT},
    TradingState.FLATTENING: {TradingState.LOCKED_OUT, TradingState.END_OF_DAY,
                              TradingState.SCANNING},
    TradingState.LOCKED_OUT: {TradingState.END_OF_DAY},
    TradingState.END_OF_DAY: set(),
}


class StateMachine:
    def __init__(self) -> None:
        self._state = TradingState.BOOT
        self.history: list[dict] = []

    @property
    def state(self) -> TradingState:
        return self._state

    def transition(self, to: TradingState, reason: str) -> None:
        if to == self._state:
            return
        if to not in _ALLOWED[self._state]:
            raise RuntimeError(f"illegal transition {self._state.value} -> {to.value} ({reason})")
        event = {
            "from": self._state.value,
            "to": to.value,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.history.append(event)
        log.info("state_transition", **event)
        self._state = to
