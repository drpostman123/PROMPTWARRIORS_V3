"""GodMode0DTE async runtime orchestrator.

Single event loop, four periodic tasks over shared in-memory state:

  clock/state task   drives the trading-day state machine off ET time
  signal task        on each completed 1m bar: features -> regime ->
                     scoring -> (maybe) TradeIntent -> RiskGovernor
  risk/exit task     equity refresh, breaker check, exit engine, flatten
  snapshot task      writes dashboard state every 2s

Only this module holds the broker; scoring receives data and returns
scores. All rejections are logged with reasons and appended to the
decision log for calibration.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from godmode0dte.config import AppConfig, Credentials
from godmode0dte.data.calendar import EventCalendar
from godmode0dte.data.market_data import MarketDataHub
from godmode0dte.execution.broker import Broker, PaperBroker, TastytradeBroker
from godmode0dte.execution.exits import ExitEngine
from godmode0dte.execution.verticals import BuildResult, ChainOption, build_vertical
from godmode0dte.features.indicators import atr
from godmode0dte.features.bars import resample
from godmode0dte.features.opening_range import OpeningRangeTracker
from godmode0dte.features.volume import RelativeVolume
from godmode0dte.models import Direction, Position, Rejection, TradeIntent
from godmode0dte.monitoring.logging import get_logger, setup_logging
from godmode0dte.regime.base import RegimeModel
from godmode0dte.regime.rules import RuleBasedRegime
from godmode0dte.risk.circuit_breaker import CircuitBreaker
from godmode0dte.risk.governor import ApprovedTrade, RiskGovernor
from godmode0dte.scoring.engine import ScoreEngine, ScoringInputs
from godmode0dte.state.machine import StateMachine, TradingState
from godmode0dte.state.store import StateStore

log = get_logger("app")


class GodModeApp:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)
        today = datetime.now(self.tz).date()
        self.breaker = CircuitBreaker(cfg.risk, today)
        self.governor = RiskGovernor(cfg, self.breaker)
        self.machine = StateMachine()
        self.store = StateStore(cfg.data)
        self.calendar = EventCalendar(cfg.events, cfg.timezone)
        self.regime: RegimeModel = RuleBasedRegime(cfg.signal.adx_floor)
        self.scorer = ScoreEngine(cfg.signal)
        self.rel_volume = RelativeVolume(tz=cfg.timezone)
        self.underlying = "SPY" if cfg.execution.underlying in ("SPY", "AUTO") else "SPX"
        self.md = MarketDataHub(cfg, self.underlying)
        self.or_tracker = OpeningRangeTracker(cfg.signal, cfg.timezone)
        self.exit_engine = ExitEngine(cfg.exits, self.governor, cfg.timezone)
        self.broker: Optional[Broker] = None
        self.session = None
        self.account = None
        self.chain: list[ChainOption] = []
        self.last_score_snapshot: dict = {}
        self._last_scored_bar: Optional[datetime] = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------

    async def start(self) -> None:
        setup_logging()
        log.info("boot", paper_mode=self.cfg.paper_mode, underlying=self.underlying)
        creds = Credentials.from_env()

        from tastytrade import Account, Session
        self.session = Session(creds.username, creds.password)
        accounts = await Account.a_get(self.session)
        self.account = accounts[self.cfg.account_index]

        if self.cfg.paper_mode:
            equity = float((await self.account.a_get_balances(self.session)).net_liquidating_value)
            self.broker = PaperBroker(self.cfg.execution, starting_equity=equity)
        else:
            self.broker = TastytradeBroker(self.session, self.account, self.cfg.execution)

        await self.md.start(self.session)
        await self._load_chain()
        await self._reconcile()

        if self.breaker.allows_entries:
            self.machine.transition(TradingState.PRE_MARKET, "boot complete")
        else:
            self.machine.transition(TradingState.LOCKED_OUT, "restored lockout from disk")

        await asyncio.gather(
            self._clock_task(), self._signal_task(), self._risk_task(), self._snapshot_task()
        )

    def stop(self) -> None:
        self._stop.set()

    # -- boot helpers ---------------------------------------------------

    async def _load_chain(self) -> None:
        """Fetch today's 0DTE chain and normalize; subscribe leg quotes."""
        from tastytrade.instruments import NestedOptionChain
        chains = await NestedOptionChain.a_get(self.session, self.underlying)
        chain = chains[0] if isinstance(chains, list) else chains
        today = datetime.now(self.tz).date()
        exp = next((e for e in chain.expirations if e.expiration_date == today), None)
        if exp is None:
            log.warning("no_0dte_expiration", underlying=self.underlying)
            return
        rows: list[ChainOption] = []
        symbols: list[str] = []
        for strike in exp.strikes:
            for is_call, occ, streamer in (
                (True, strike.call, strike.call_streamer_symbol),
                (False, strike.put, strike.put_streamer_symbol),
            ):
                rows.append(ChainOption(symbol=occ, streamer_symbol=streamer,
                                        strike=float(strike.strike_price), is_call=is_call,
                                        delta=None, open_interest=0))
                symbols.append(streamer)
        self.chain = rows
        await self.md.watch_options(symbols)
        log.info("chain_loaded", strikes=len(exp.strikes))

    async def _reconcile(self) -> None:
        """On boot, adopt any live open positions so exits keep managing them."""
        if self.cfg.paper_mode:
            return
        positions = await self.broker.positions()
        if positions:
            log.warning("boot_with_open_positions", count=len(positions))
            self.governor.kill("unreconciled positions at boot — manual review required")

    # -- periodic tasks -------------------------------------------------

    async def _clock_task(self) -> None:
        while not self._stop.is_set():
            now_et = datetime.now(self.tz)
            t = now_et.time()
            s = self.machine.state
            if s is TradingState.PRE_MARKET and t >= self.cfg.signal.or_start:
                self.machine.transition(TradingState.OPENING_RANGE, "09:30 ET")
            elif s is TradingState.OPENING_RANGE and t >= self.cfg.signal.or_end:
                self.machine.transition(TradingState.SCANNING, "opening range complete")
            elif s in (TradingState.SCANNING, TradingState.MANAGING) and t >= self.cfg.exits.force_flat:
                if self.governor.open_positions:
                    self.machine.transition(TradingState.FLATTENING, "force-flat time")
                else:
                    self.machine.transition(TradingState.END_OF_DAY, "force-flat time, already flat")
                    self.rel_volume.end_of_day_update(self.md.bars.bars)
            await asyncio.sleep(1.0)

    async def _signal_task(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            bars = self.md.bars.bars
            if not bars:
                continue
            for b in bars[-3:]:
                self.or_tracker.add_bar(b)
            if self.machine.state is not TradingState.SCANNING:
                continue
            last = bars[-1]
            if self._last_scored_bar == last.ts:
                continue
            self._last_scored_bar = last.ts
            await self._score_and_maybe_trade()

    async def _score_and_maybe_trade(self) -> None:
        now = datetime.now(timezone.utc)
        bars = self.md.bars.bars
        last = bars[-1]
        price = self.md.underlying_price() or last.close

        bars5 = resample(bars, 5)
        atr5 = float(atr(bars5, 14)[-1]) if len(bars5) >= 15 else 0.0
        width_ok, width_reason = self.or_tracker.width_ok(price, atr5)
        rel = self.rel_volume.ratio(last, bars)
        direction, evidence = self.or_tracker.classify_breakout(last, rel)

        regime_state = self.regime.classify(bars5, self.md.macro.vix)
        event_verdict = self.calendar.evaluate(now, self.cfg.signal.weights.event_sentiment)
        if event_verdict.forced_bias is not None and direction is not None:
            if direction != event_verdict.forced_bias:
                direction = None  # bias-only day: wrong-way signals do not exist

        build: Optional[BuildResult] = None
        if direction is not None:
            live_chain = [
                ChainOption(symbol=o.symbol, streamer_symbol=o.streamer_symbol,
                            strike=o.strike, is_call=o.is_call,
                            delta=self.md.deltas.get(o.streamer_symbol),
                            open_interest=self.md.open_interest.get(o.streamer_symbol, 0))
                for o in self.chain
            ]
            build = build_vertical(direction, self.underlying, live_chain,
                                   self.md.quotes, self.cfg.execution,
                                   datetime.now(self.tz).date())

        macro_pts, macro_detail = (
            self.md.macro.confirmation_points(direction, self.cfg.signal.weights.macro_cluster)
            if direction is not None else (0.0, "no direction")
        )
        score = self.scorer.score(ScoringInputs(
            bars_1m=bars,
            direction=direction,
            breakout_evidence=evidence,
            or_width_ok=width_ok,
            or_width_reason=width_reason,
            rel_volume=rel,
            regime=regime_state,
            macro_points=macro_pts,
            macro_detail=macro_detail,
            event=event_verdict,
            vix=self.md.macro.vix,
            long_leg_quote=build.long_quote if build else None,
            short_leg_quote=build.short_quote if build else None,
            max_leg_spread_pct=self.cfg.execution.max_leg_spread_pct_of_mid,
            min_open_interest_ok=build is not None and build.vertical is not None,
            now=now,
        ))
        self.last_score_snapshot = {
            "total": score.total,
            "direction": direction.value if direction else None,
            "components": [
                {"name": c.name, "points": c.points, "max": c.max_points, "detail": c.detail}
                for c in score.components
            ],
            "gates": list(score.hard_gate_failures),
            "regime": regime_state.regime.value,
            "vol_regime": regime_state.vol_regime.value,
        }
        self.store.log_decision({"kind": "score", **self.last_score_snapshot})

        if score.total < self.cfg.signal.min_score or not score.tradeable:
            return
        if build is None or build.vertical is None:
            self.store.log_decision({"kind": "reject", "reason": "vertical_build",
                                     "detail": build.reason if build else "no build"})
            return

        # Size optimistically at the concurrency-aware cap; governor resizes down.
        intent_vertical = build.vertical
        max_contracts = int(self.governor.equity * self.cfg.risk.max_trade_risk_pct / 100.0
                            // (intent_vertical.debit * 100)) or 1
        intent = TradeIntent(
            score=score,
            vertical=intent_vertical.__class__(**{**intent_vertical.__dict__,
                                                  "contracts": max_contracts}),
            ts=now,
            quote_ts=build.long_quote.ts if build.long_quote else now,
        )
        result = self.governor.evaluate(intent)
        if isinstance(result, Rejection):
            self.store.log_decision({"kind": "reject", "reason": result.reason,
                                     "detail": result.detail, "score": score.total})
            return
        await self._enter(result, build)

    async def _enter(self, approved: ApprovedTrade, build: BuildResult) -> None:
        self.machine.transition(TradingState.ENTERING, f"approved {approved.trade_id}")
        fill = await self.broker.open_position(approved, build.long_quote, build.short_quote)
        if fill is None:
            self.store.log_decision({"kind": "entry_abandoned", "trade_id": approved.trade_id})
            self.machine.transition(TradingState.SCANNING, "entry unfilled")
            return
        or_range = self.or_tracker.range
        pos = Position(
            trade_id=approved.trade_id,
            vertical=approved.vertical,
            entry_debit=fill.price,
            entry_ts=fill.ts,
            score_at_entry=approved.score,
            or_mid=or_range.mid if or_range else fill.price,
            current_value=fill.price,
        )
        self.governor.register_position(pos)
        self.store.log_trade({"kind": "entry", "trade_id": pos.trade_id,
                              "vertical": pos.vertical, "fill": fill.price,
                              "score": approved.score, "risk_dollars": approved.risk_dollars})
        self.machine.transition(TradingState.MANAGING, f"filled {approved.trade_id}")

    async def _risk_task(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            try:
                equity = await self.broker.equity() if self.broker else 0.0
            except Exception as e:                      # noqa: BLE001 - keep the loop alive
                log.error("equity_fetch_failed", error=str(e))
                continue
            self.governor.update_equity(equity, datetime.now(timezone.utc))
            self._mark_positions()
            decisions = self.exit_engine.evaluate(datetime.now(timezone.utc),
                                                  self.md.underlying_price())
            for d in decisions:
                await self._exit(d.trade_id, d.reason, d.urgency, d.detail)
            if self.machine.state is TradingState.MANAGING and not self.governor.open_positions:
                self.machine.transition(TradingState.SCANNING, "all positions closed")
            if self.machine.state is TradingState.FLATTENING and not self.governor.open_positions:
                if self.breaker.allows_entries:
                    self.machine.transition(TradingState.END_OF_DAY, "flat after force-flat")
                else:
                    self.machine.transition(TradingState.LOCKED_OUT, "flat after breaker trip")

    def _mark_positions(self) -> None:
        for pos in self.governor.open_positions:
            lq = self.md.quotes.get(self._streamer_symbol(pos.vertical.long_symbol))
            sq = self.md.quotes.get(self._streamer_symbol(pos.vertical.short_symbol))
            if lq and sq and lq.bid > 0 and sq.bid > 0:
                value = max(0.0, lq.mid - sq.mid)
                self.governor.update_position(
                    Position(**{**pos.__dict__, "current_value": round(value, 2)})
                )

    def _streamer_symbol(self, occ_symbol: str) -> str:
        for o in self.chain:
            if o.symbol == occ_symbol:
                return o.streamer_symbol
        return occ_symbol

    async def _exit(self, trade_id: str, reason: str, urgency: str, detail: str) -> None:
        pos = next((p for p in self.governor.open_positions if p.trade_id == trade_id), None)
        if pos is None:
            return
        if self.governor.must_flatten and self.machine.state not in (
            TradingState.FLATTENING, TradingState.LOCKED_OUT
        ):
            self.machine.transition(TradingState.FLATTENING, reason)
        lq = self.md.quotes.get(self._streamer_symbol(pos.vertical.long_symbol))
        sq = self.md.quotes.get(self._streamer_symbol(pos.vertical.short_symbol))
        if lq is None or sq is None:
            log.error("exit_no_quotes", trade_id=trade_id)
            return
        fill = await self.broker.close_position(trade_id, pos.vertical, lq, sq, urgency)
        if fill is None:
            log.error("exit_unfilled_retrying", trade_id=trade_id, reason=reason)
            return
        closed = Position(**{**pos.__dict__, "exit_ts": fill.ts,
                             "exit_value": fill.price, "exit_reason": reason})
        self.governor.update_position(closed)
        self.store.log_trade({"kind": "exit", "trade_id": trade_id, "reason": reason,
                              "detail": detail, "fill": fill.price, "pnl": closed.pnl})

    async def _snapshot_task(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            self.store.write_snapshot({
                "state": self.machine.state.value,
                "paper_mode": self.cfg.paper_mode,
                "underlying": self.underlying,
                "price": self.md.underlying_price(),
                "equity": self.governor.equity,
                "starting_equity": self.breaker.starting_equity,
                "heat_pct": round(self.governor.heat_pct, 2),
                "breaker": self.breaker.state.value,
                "breaker_reason": self.breaker.trip_reason,
                "score": self.last_score_snapshot,
                "macro": self.md.macro.snapshot(),
                "positions": [p for p in self.governor.open_positions],
                "transitions": self.machine.history[-20:],
            })
