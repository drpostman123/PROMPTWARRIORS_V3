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
import uuid
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
from godmode0dte.models import Direction, Position, Rejection, TradeIntent, VerticalSpec
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
        self._equity_failures = 0
        self._eod_done = False
        self._extremes: dict[str, tuple[float, float]] = {}   # trade_id -> (min mark, max mark)

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

        # Supervised tasks (audit B2): one exception must never silently kill a
        # safety loop with positions open — restart the loop, and if the whole
        # runtime dies anyway, trip the breaker (persisted) and flatten.
        try:
            await asyncio.gather(*(
                self._supervised(name, fn) for name, fn in [
                    ("clock", self._clock_task), ("signal", self._signal_task),
                    ("risk", self._risk_task), ("snapshot", self._snapshot_task),
                ]
            ))
        except BaseException:
            if self.governor.open_positions:
                self.breaker.trip("fatal runtime crash with open positions")
                for p in self.governor.open_positions:
                    try:
                        await self._exit(p.trade_id, "fatal_crash", "urgent", "runtime dying")
                    except Exception as e:          # noqa: BLE001 — best effort on the way down
                        log.error("crash_flatten_failed", trade_id=p.trade_id, error=str(e))
            raise

    async def _supervised(self, name: str, factory) -> None:
        while not self._stop.is_set():
            try:
                await factory()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:                  # noqa: BLE001 — log, restart the loop
                log.error("task_crashed", task=name, error=str(e))
                await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._stop.set()

    # -- boot helpers ---------------------------------------------------

    async def _load_chain(self) -> None:
        """Fetch today's 0DTE chain and normalize; subscribe leg quotes."""
        from tastytrade.instruments import NestedOptionChain
        chains = await NestedOptionChain.a_get(self.session, self.underlying)
        if not isinstance(chains, list):
            chains = [chains]
        if self.underlying == "SPX":
            # PM-settled weeklys only — AM-settled SPX 0DTE is already settled
            # by the entry window (spec §6.1, audit U5a).
            chains = [c for c in chains if getattr(c, "root_symbol", "") == "SPXW"] or chains
        chain = chains[0]
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
        """On boot, ADOPT any live open positions so exits keep managing them.

        Audit B5: the old body only killed — the governor never saw the
        positions, so the exit engine managed nothing and lockout left live
        0DTE spreads decaying to expiry. Now paired legs become tracked
        positions; only unpairable legs (not a known debit vertical) engage
        the kill switch for manual review — and the kill switch blocks new
        entries only, so exits keep working on everything adopted.
        """
        if self.cfg.paper_mode:
            return
        raw = await self.broker.positions()
        if not raw:
            return
        log.warning("boot_with_open_positions", count=len(raw))
        legs = []
        for p in raw:
            occ = getattr(p, "symbol", "")
            try:
                strike = float(occ[-8:]) / 1000.0
                is_call = occ[-9] == "C"
            except (ValueError, IndexError):
                legs.append((p, None, None))
                continue
            legs.append((p, strike, is_call))
        longs = [(p, s, c) for p, s, c in legs if s is not None and getattr(p, "quantity_direction", "") == "Long"]
        shorts = {(round(s, 3), c): p for p, s, c in legs
                  if s is not None and getattr(p, "quantity_direction", "") == "Short"}
        adopted_syms: set[str] = set()
        for lp, ls, lc in longs:
            width = self.cfg.execution.width_strikes_spy if self.underlying == "SPY" \
                else self.cfg.execution.width_points_spx
            target = round(ls + width, 3) if lc else round(ls - width, 3)
            sp = shorts.get((target, lc))
            if sp is None or abs(float(lp.quantity)) != abs(float(sp.quantity)):
                continue
            debit = abs(float(getattr(lp, "average_open_price", 0))) - abs(
                float(getattr(sp, "average_open_price", 0)))
            vertical = VerticalSpec(
                underlying=self.underlying,
                direction=Direction.LONG if lc else Direction.SHORT,
                expiration=datetime.now(self.tz).date().isoformat(),
                long_strike=ls, short_strike=target, width=float(width),
                debit=max(debit, 0.01), contracts=int(abs(float(lp.quantity))),
                long_symbol=lp.symbol, short_symbol=sp.symbol,
            )
            pos = Position(
                trade_id=f"adopted-{uuid.uuid4().hex[:8]}",
                vertical=vertical, entry_debit=vertical.debit,
                entry_ts=datetime.now(timezone.utc), score_at_entry=0.0,
                or_mid=ls, current_value=vertical.debit,
            )
            self.governor.register_position(pos)
            adopted_syms.update((lp.symbol, sp.symbol))
            log.warning("position_adopted", trade_id=pos.trade_id,
                        strikes=f"{ls}/{target}", contracts=vertical.contracts)
        orphans = [p for p, s, _ in legs if getattr(p, "symbol", "") not in adopted_syms]
        if orphans:
            log.error("unpairable_legs_at_boot", symbols=[getattr(p, "symbol", "?") for p in orphans])
            self.governor.kill("unpairable option legs at boot — manual review required")

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
            elif s in (TradingState.SCANNING, TradingState.MANAGING, TradingState.ENTERING,
                       TradingState.FLATTENING) and t >= self.cfg.exits.force_flat:
                # ENTERING included (audit B4): a wedged entry must not dodge force-flat.
                if self.governor.open_positions:
                    if s is not TradingState.FLATTENING:
                        self.machine.transition(TradingState.FLATTENING, "force-flat time")
                elif s is not TradingState.FLATTENING:
                    self.machine.transition(TradingState.END_OF_DAY, "force-flat time, already flat")
                    self._end_of_day_baseline()
            await asyncio.sleep(1.0)

    async def _signal_task(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            bars = self.md.session_bars()      # session-anchored: no premarket (audit U2)
            if not bars:
                continue
            for b in bars[-3:]:
                self.or_tracker.add_bar(b)     # idempotent: tracker dedupes by ts
            if self.machine.state is not TradingState.SCANNING:
                continue
            last = bars[-1]
            if self._last_scored_bar == last.ts:
                continue
            self._last_scored_bar = last.ts
            await self._score_and_maybe_trade()

    def _end_of_day_baseline(self) -> None:
        """Feed today's bars into the rel-volume baseline exactly once —
        at EVERY terminal transition, so traded/locked days count too (audit U6b)."""
        if not self._eod_done:
            self._eod_done = True
            self.rel_volume.end_of_day_update(self.md.session_bars())

    async def _score_and_maybe_trade(self) -> None:
        now = datetime.now(timezone.utc)
        bars = self.md.session_bars()
        last = bars[-1]
        price = self.md.underlying_price() or last.close

        bars5 = resample(bars, 5, drop_partial=True)
        atr5 = float(atr(bars5, 14)[-1]) if len(bars5) >= 15 else 0.0
        width_ok, width_reason = self.or_tracker.width_ok(price, atr5)
        rel = self.rel_volume.ratio(last, bars)
        direction, evidence = self.or_tracker.classify_breakout(last, rel)

        regime_state = self.regime.classify(bars5, self.md.macro.vix)
        event_verdict = self.calendar.evaluate(now, self.cfg.signal.weights.event_sentiment)
        if event_verdict.forced_bias is not None and direction is not None:
            if direction != event_verdict.forced_bias:
                direction = None  # bias-only day: wrong-way signals do not exist

        # One-shot rule (spec G-S9): a losing trade in this direction today
        # closes that side for the rest of the session.
        if direction is not None and any(
            p.vertical.direction == direction and p.pnl < 0
            for p in self.governor.closed_positions
        ):
            self.store.log_decision({"kind": "reject", "reason": "one_shot_rule",
                                     "detail": f"{direction.value} already lost today"})
            direction = None

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
        book_state = self.md.book.state() if self.md.book.ready else None
        breakout_age = self.or_tracker.breakout_age_min(direction, now) if direction else 0.0
        score = self.scorer.score(ScoringInputs(
            bars_1m=bars,
            direction=direction,
            breakout_evidence=evidence,
            or_width_ok=width_ok,
            or_width_reason=width_reason,
            rel_volume=rel,
            breakout_age_min=breakout_age,
            regime=regime_state,
            macro_points=macro_pts,
            macro_detail=macro_detail,
            event=event_verdict,
            vix=self.md.macro.vix,
            long_leg_quote=build.long_quote if build else None,
            short_leg_quote=build.short_quote if build else None,
            max_leg_spread_pct=self.cfg.execution.max_leg_spread_pct_of_mid,
            min_open_interest_ok=build is not None and build.vertical is not None,
            book=book_state,
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
            "book": {
                "imbalance": book_state.imbalance,
                "imbalance_raw": book_state.imbalance_raw,
                "depth": book_state.depth,
                "depth_median": book_state.depth_median,
                "thinning": book_state.thinning,
            } if book_state else None,
        }
        # price + rel_volume ride along so scripts/backtest_imbalance.py can
        # compute forward returns and run the earn-your-weight logistic test.
        self.store.log_decision({"kind": "score", "price": price, "rel_volume": round(rel, 3),
                                 **self.last_score_snapshot})

        if score.total < self.cfg.signal.min_score or not score.tradeable:
            return
        if build is None or build.vertical is None:
            self.store.log_decision({"kind": "reject", "reason": "vertical_build",
                                     "detail": build.reason if build else "no build"})
            return

        # Size optimistically at the concurrency-aware cap; governor resizes down.
        # Clamp to displayed book depth and the fat-finger ceiling (spec §6.3).
        intent_vertical = build.vertical
        max_contracts = int(self.governor.equity * self.cfg.risk.max_trade_risk_pct / 100.0
                            // (intent_vertical.debit * 100)) or 1
        lq, sq = build.long_quote, build.short_quote
        depth_cap = int(min(lq.bid_size, lq.ask_size, sq.bid_size, sq.ask_size)) if lq and sq else 1
        ceiling = self.cfg.execution.max_contracts_ceiling if self.underlying == "SPY" else 5
        max_contracts = max(1, min(max_contracts, depth_cap, ceiling))
        intent = TradeIntent(
            score=score,
            vertical=intent_vertical.__class__(**{**intent_vertical.__dict__,
                                                  "contracts": max_contracts}),
            ts=now,
            # Freshness is judged by the OLDEST leg quote, not the long only.
            quote_ts=min(lq.ts, sq.ts) if lq and sq else now,
        )
        result = self.governor.evaluate(intent)
        if isinstance(result, Rejection):
            self.store.log_decision({"kind": "reject", "reason": result.reason,
                                     "detail": result.detail, "score": score.total})
            return
        await self._enter(result, build)

    def _leg_quote_getter(self, vertical: VerticalSpec):
        """Fresh leg quotes for the broker's between-rung re-peg (audit U5c)."""
        def getter():
            lq = self.md.quotes.get(self._streamer_symbol(vertical.long_symbol))
            sq = self.md.quotes.get(self._streamer_symbol(vertical.short_symbol))
            return (lq, sq) if lq and sq else None
        return getter

    async def _enter(self, approved: ApprovedTrade, build: BuildResult) -> None:
        self.machine.transition(TradingState.ENTERING, f"approved {approved.trade_id}")
        try:
            fill = await self.broker.open_position(
                approved, build.long_quote, build.short_quote,
                quote_getter=self._leg_quote_getter(approved.vertical))
        except Exception as e:                      # noqa: BLE001 — audit B4: never wedge ENTERING
            log.error("entry_error", trade_id=approved.trade_id, error=str(e))
            self.store.log_decision({"kind": "entry_error", "trade_id": approved.trade_id,
                                     "detail": str(e)})
            self.machine.transition(TradingState.SCANNING, "entry error")
            return
        if fill is None:
            self.store.log_decision({"kind": "entry_abandoned", "trade_id": approved.trade_id})
            self.machine.transition(TradingState.SCANNING, "entry unfilled")
            return
        or_range = self.or_tracker.range
        # Structure-stop reference = the OR trigger level that was broken
        # (OR high for longs, OR low for shorts), per spec §7 P4b.
        if or_range is not None:
            trigger = or_range.high if approved.vertical.direction is Direction.LONG else or_range.low
        else:
            trigger = fill.price
        pos = Position(
            trade_id=approved.trade_id,
            vertical=approved.vertical,
            entry_debit=fill.price,
            entry_ts=fill.ts,
            score_at_entry=approved.score,
            or_mid=trigger,
            current_value=fill.price,
        )
        self.governor.register_position(pos)
        self._extremes[pos.trade_id] = (fill.price, fill.price)
        # Full context at entry so the calibration joins need no reconstruction
        # (audit U6c): score breakdown, regime, VIX, rel-vol, cap price, heat.
        self.store.log_trade({"kind": "entry", "trade_id": pos.trade_id,
                              "vertical": pos.vertical, "fill": fill.price,
                              "score": approved.score, "risk_dollars": approved.risk_dollars,
                              "cap_price": approved.cap_price,
                              "heat_after_pct": round(self.governor.heat_pct, 2),
                              "score_snapshot": self.last_score_snapshot,
                              "vix": self.md.macro.vix})
        self.machine.transition(TradingState.MANAGING, f"filled {approved.trade_id}")

    async def _risk_task(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            # Audit B1: an equity-fetch failure must NEVER disable marks and
            # exits — the try covers the fetch only, and a persistently dead
            # equity feed with open positions trips the breaker (fail-flat).
            try:
                equity = await self.broker.equity() if self.broker else 0.0
                self._equity_failures = 0
                self.governor.update_equity(equity, datetime.now(timezone.utc))
            except Exception as e:                  # noqa: BLE001
                self._equity_failures += 1
                log.error("equity_fetch_failed", error=str(e),
                          consecutive=self._equity_failures)
                if self._equity_failures >= 15 and self.governor.open_positions:
                    self.breaker.trip("equity feed dead ~30s with open positions")
            try:
                self._mark_positions()
                decisions = self.exit_engine.evaluate(datetime.now(timezone.utc),
                                                      self.md.underlying_price())
                for d in decisions:
                    try:
                        await self._exit(d.trade_id, d.reason, d.urgency, d.detail)
                    except Exception as e:          # noqa: BLE001 — retry next tick
                        log.error("exit_attempt_failed", trade_id=d.trade_id, error=str(e))
                if self.machine.state is TradingState.MANAGING and not self.governor.open_positions:
                    self.machine.transition(TradingState.SCANNING, "all positions closed")
                if self.machine.state is TradingState.FLATTENING and not self.governor.open_positions:
                    if self.breaker.allows_entries:
                        self.machine.transition(TradingState.END_OF_DAY, "flat after force-flat")
                    else:
                        self.machine.transition(TradingState.LOCKED_OUT, "flat after breaker trip")
                    self._end_of_day_baseline()
            except Exception as e:                  # noqa: BLE001 — keep the safety loop alive
                log.error("risk_iteration_failed", error=str(e))

    def _mark_positions(self) -> None:
        """Refresh spread marks. Audit B7: a deep-OTM short leg legitimately
        bids 0 — requiring bid>0 on BOTH legs froze marks exactly when the
        position was winning. Two-sidedness is required on the long leg only;
        a one-sided short leg marks at ask/2."""
        now = datetime.now(timezone.utc)
        for pos in self.governor.open_positions:
            lq = self.md.quotes.get(self._streamer_symbol(pos.vertical.long_symbol))
            sq = self.md.quotes.get(self._streamer_symbol(pos.vertical.short_symbol))
            if lq and lq.bid > 0 and lq.ask > 0 and sq and sq.ask > 0:
                sq_mid = sq.mid if sq.bid > 0 else sq.ask / 2.0
                value = round(max(0.0, lq.mid - sq_mid), 2)
                lo, hi = self._extremes.get(pos.trade_id, (value, value))
                self._extremes[pos.trade_id] = (min(lo, value), max(hi, value))
                self.governor.update_position(
                    Position(**{**pos.__dict__, "current_value": value, "mark_ts": now})
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
        heat_before = round(self.governor.heat_pct, 2)
        fill = await self.broker.close_position(trade_id, pos.vertical, lq, sq, urgency)
        if fill is None:
            log.error("exit_unfilled_retrying", trade_id=trade_id, reason=reason)
            return
        closed = Position(**{**pos.__dict__, "exit_ts": fill.ts,
                             "exit_value": fill.price, "exit_reason": reason})
        self.governor.update_position(closed)
        lo, hi = self._extremes.pop(trade_id, (pos.entry_debit, pos.entry_debit))
        mult = pos.vertical.contracts * 100
        fees = round(self.cfg.execution.friction_per_contract * pos.vertical.contracts, 2)
        self.store.log_trade({"kind": "exit", "trade_id": trade_id, "reason": reason,
                              "detail": detail, "fill": fill.price, "pnl": closed.pnl,
                              "fees": fees, "pnl_net": round(closed.pnl - fees, 2),
                              "mae": round((lo - pos.entry_debit) * mult, 2),
                              "mfe": round((hi - pos.entry_debit) * mult, 2),
                              "heat_before_pct": heat_before,
                              "heat_after_pct": round(self.governor.heat_pct, 2)})

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
