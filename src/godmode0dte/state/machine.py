"""Trading-day state machine — godmode's states over tradecore's generic
machine (illegal transitions raise; every transition is a logged event).

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
"""

from __future__ import annotations

from enum import Enum

from tradecore.machine import StateMachine as _StateMachine


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


class StateMachine(_StateMachine):
    def __init__(self) -> None:
        super().__init__(TradingState.BOOT, _ALLOWED, logger_name="state_machine")

    @property
    def state(self) -> TradingState:
        return self._state  # type: ignore[return-value]
