"""RiskGovernor — the single gateway between signals and the broker.

The signal engine produces :class:`TradeIntent` objects and nothing else.
Only the governor can mint an :class:`ApprovedTrade` (enforced with a
module-private token checked in ``__post_init__``), and the broker
adapter refuses to open a position without one. The governor owns the
circuit breaker and the only broker reference — the scoring layer never
sees the broker, so no signal-side code path can reach an order.

Checks run in fixed order; first failure rejects the intent:

  1.  Circuit breaker armed (daily -6% lockout not tripped/locked)
  2.  Trading enabled / kill switch
  3.  Equity sane and above the floor
  4.  Score >= configured minimum
  5.  Data freshness (quote staleness)
  6.  Entry time window
  7.  Duplicate/burst protection (min spacing between approvals)
  8.  Concurrency: open positions < 2
  9.  Sizing: risk <= 4% of live equity (resize down, never up)
  10. Heat: open risk + new risk <= 7% of equity
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from godmode0dte.config import AppConfig
from godmode0dte.models import Position, Rejection, TradeIntent, VerticalSpec
from godmode0dte.monitoring.logging import get_logger
from godmode0dte.risk.circuit_breaker import BreakerState, CircuitBreaker
from godmode0dte.risk.sizing import size_trade

log = get_logger("risk_governor")

# Module-private capability token. Nothing outside this module can build
# an ApprovedTrade without raising.
_APPROVAL_TOKEN = object()


@dataclass(frozen=True)
class ApprovedTrade:
    """Proof of risk approval. Constructible only by RiskGovernor."""

    trade_id: str
    vertical: VerticalSpec
    risk_dollars: float
    score: float
    approved_ts: datetime
    _token: object = None

    def __post_init__(self) -> None:
        if self._token is not _APPROVAL_TOKEN:
            raise PermissionError(
                "ApprovedTrade may only be created by the RiskGovernor. "
                "Signal-side code must submit a TradeIntent instead."
            )


class RiskGovernor:
    """Sole authority over order flow, sizing, heat, and the breaker."""

    def __init__(self, cfg: AppConfig, breaker: CircuitBreaker) -> None:
        self._cfg = cfg
        self._breaker = breaker
        self._tz = ZoneInfo(cfg.timezone)
        self._positions: dict[str, Position] = {}
        self._last_approval_ts: Optional[datetime] = None
        self._equity: float = 0.0
        self._equity_ts: Optional[datetime] = None
        self._kill_switch = False

    # -- account state (fed by the runtime, from the broker) -----------

    def update_equity(self, equity: float, ts: datetime) -> BreakerState:
        """Update live equity; runs the breaker check on every update."""
        if equity > 0:
            self._equity = equity
            self._equity_ts = ts
            self._breaker.set_starting_equity(equity)
        return self._breaker.check(self._equity)

    def register_position(self, pos: Position) -> None:
        self._positions[pos.trade_id] = pos

    def update_position(self, pos: Position) -> None:
        self._positions[pos.trade_id] = pos
        if not any(p.open for p in self._positions.values()):
            self._breaker.confirm_flat()

    def kill(self, reason: str) -> None:
        """Manual kill switch: no new entries, positions to be flattened."""
        self._kill_switch = True
        self._breaker.trip(f"kill switch: {reason}")

    # -- queries -------------------------------------------------------

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.open]

    @property
    def open_risk_dollars(self) -> float:
        return sum(p.risk_dollars for p in self.open_positions)

    @property
    def heat_pct(self) -> float:
        return 100.0 * self.open_risk_dollars / self._equity if self._equity > 0 else 0.0

    @property
    def max_heat_pct(self) -> float:
        return self._cfg.risk.max_heat_pct

    @property
    def must_flatten(self) -> bool:
        return self._breaker.state in (BreakerState.TRIPPED, BreakerState.LOCKED) and bool(
            self.open_positions
        )

    # -- the gate ------------------------------------------------------

    def evaluate(self, intent: TradeIntent) -> ApprovedTrade | Rejection:
        """Run every check in order. Approve (possibly resized down) or reject."""
        now = datetime.now(timezone.utc)

        def reject(reason: str, detail: str) -> Rejection:
            r = Rejection(reason=reason, detail=detail, ts=now)
            log.warning("intent_rejected", reason=reason, detail=detail, score=intent.score.total)
            return r

        # 1. Circuit breaker
        if not self._breaker.allows_entries:
            return reject("circuit_breaker", f"breaker state: {self._breaker.state.value}")
        # 2. Kill switch
        if self._kill_switch:
            return reject("kill_switch", "manual kill switch engaged")
        # 3. Equity sanity
        if self._equity < self._cfg.risk.min_equity:
            return reject("equity_floor", f"equity {self._equity:.2f} < min {self._cfg.risk.min_equity}")
        if (
            self._equity_ts is None
            or (now - self._equity_ts).total_seconds() > 10 * self._cfg.risk.max_data_staleness_sec
        ):
            return reject("equity_stale", "no fresh equity reading from broker")
        # 4. Score threshold
        if intent.score.total < self._cfg.signal.min_score:
            return reject(
                "score_below_min", f"{intent.score.total:.1f} < {self._cfg.signal.min_score}"
            )
        if intent.score.hard_gate_failures:
            return reject("hard_gate", ", ".join(intent.score.hard_gate_failures))
        # 5. Data freshness
        quote_age = (now - intent.quote_ts).total_seconds()
        if quote_age > self._cfg.risk.max_data_staleness_sec:
            return reject("stale_quotes", f"option quotes {quote_age:.1f}s old")
        # 6. Entry window
        now_et = now.astimezone(self._tz).time()
        if not (self._cfg.signal.entry_window_start <= now_et <= self._cfg.signal.entry_window_end):
            return reject("entry_window", f"{now_et} outside entry window")
        # 7. Burst protection
        if (
            self._last_approval_ts is not None
            and (now - self._last_approval_ts).total_seconds() < self._cfg.risk.min_intent_spacing_sec
        ):
            return reject("burst_protection", "approval too soon after previous approval")
        # 8. Concurrency
        if len(self.open_positions) >= self._cfg.risk.max_concurrent_positions:
            return reject(
                "max_concurrent", f"{len(self.open_positions)} positions already open"
            )
        # 9. Per-trade sizing (resize down from intent; never up)
        contracts, risk_dollars = size_trade(
            score=intent.score.total,
            equity=self._equity,
            debit_per_share=intent.vertical.debit,
            contract_multiplier=100,
            cfg=self._cfg.risk,
        )
        contracts = min(contracts, intent.vertical.contracts)
        risk_dollars = contracts * intent.vertical.debit * 100
        if contracts < 1:
            return reject("size_zero", "cannot fit one contract under the 4% per-trade cap")
        # 10. Portfolio heat
        heat_cap = self._equity * (self._cfg.risk.max_heat_pct / 100.0)
        while contracts >= 1 and self.open_risk_dollars + contracts * intent.vertical.debit * 100 > heat_cap:
            contracts -= 1
        if contracts < 1:
            return reject(
                "heat_cap",
                f"open risk {self.open_risk_dollars:.0f} + new trade would breach "
                f"{self._cfg.risk.max_heat_pct}% heat cap",
            )
        risk_dollars = contracts * intent.vertical.debit * 100

        vertical = VerticalSpec(
            underlying=intent.vertical.underlying,
            direction=intent.vertical.direction,
            expiration=intent.vertical.expiration,
            long_strike=intent.vertical.long_strike,
            short_strike=intent.vertical.short_strike,
            width=intent.vertical.width,
            debit=intent.vertical.debit,
            contracts=contracts,
            long_symbol=intent.vertical.long_symbol,
            short_symbol=intent.vertical.short_symbol,
        )
        approved = ApprovedTrade(
            trade_id=uuid.uuid4().hex[:12],
            vertical=vertical,
            risk_dollars=risk_dollars,
            score=intent.score.total,
            approved_ts=now,
            _token=_APPROVAL_TOKEN,
        )
        self._last_approval_ts = now
        log.info(
            "intent_approved",
            trade_id=approved.trade_id,
            contracts=contracts,
            risk_dollars=round(risk_dollars, 2),
            heat_pct_after=round(100.0 * (self.open_risk_dollars + risk_dollars) / self._equity, 2),
            score=intent.score.total,
        )
        return approved
