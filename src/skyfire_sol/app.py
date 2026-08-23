"""SKYFIRE_SOL composition root.

Builds every component, wires the bus, and runs the supervised task set.
Data flow:

  scanner -> "meme_signals" -> RotationAgent -> "intents" ->
    [CEO verdict (data)] -> SafetyGate (clamps, mints token) ->
      ExecutionAgent (direct call) -> "fills" -> sleeves/journal

The CEO never touches the gate's internals or the executor; its verdicts
and allocation targets are data that the gate clamps. On a fatal crash
with open positions: persist the breaker trip FIRST, then best-effort
flatten (the godmode app.py discipline).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx

from skyfire_sol.blackboard import Blackboard
from skyfire_sol.bus import Bus
from skyfire_sol.ceo.greedy import GreedyCeo
from skyfire_sol.ceo.journal import journal_allocation
from skyfire_sol.chain.helius import HeliusRpc
from skyfire_sol.clients.birdeye import BirdeyeClient
from skyfire_sol.clients.dexscreener import DexScreenerClient
from skyfire_sol.clients.jupiter import JupiterClient
from skyfire_sol.clients.rugcheck import RugCheckClient
from skyfire_sol.config import (
    JITOSOL_MINT,
    USDC_MINT,
    WBTC_MINT,
    WETH_MINT,
    WSOL_MINT,
    AppConfig,
    Credentials,
    resolve_rpc_url,
)
from skyfire_sol.discovery.scanner import Blacklist, Scanner
from skyfire_sol.execution.executor import ExecutionAgent
from skyfire_sol.models import (
    IntentKind,
    RegimeRead,
    SafetyStateView,
    SleeveId,
    SleevePerf,
    Snapshot,
)
from skyfire_sol.monitoring.heartbeat import write_heartbeat
from skyfire_sol.persistence.db import Database
from skyfire_sol.persistence.phantom import BackfillJob, PhantomLog
from skyfire_sol.safety.breakers import BreakerState, PortfolioBreaker
from skyfire_sol.safety.gate import SafetyGate
from skyfire_sol.safety.kill import KillSwitch
from skyfire_sol.safety.probation import Probation
from skyfire_sol.sleeves.core import CoreAgent
from skyfire_sol.sleeves.meme import RotationAgent
from skyfire_sol.sleeves.yield_ import YieldAgent
from skyfire_sol.wallet import Wallet
from tradecore.logging import get_logger, setup_logging
from tradecore.statestore import StateStore
from tradecore.supervise import supervised

log = get_logger("app")

MAJOR_DECIMALS = {WSOL_MINT: 9, USDC_MINT: 6, WBTC_MINT: 8, WETH_MINT: 8, JITOSOL_MINT: 9}
NAV_LOOP_S = 10.0
NAV_HISTORY_EVERY_S = 300.0


class SkyfireApp:
    def __init__(self, cfg: AppConfig, creds: Credentials) -> None:
        self.cfg = cfg
        self.creds = creds
        self._stop = asyncio.Event()
        self._targets: dict[str, float] = dict(cfg.sleeves.boot_allocations)
        self._regime: Optional[RegimeRead] = None
        self._flatten_fired = False
        self._last_nav_history = 0.0

        setup_logging(cfg.data.log_dir, filename="skyfire.jsonl",
                      root_logger_name="skyfire")

        self.http = httpx.AsyncClient(timeout=20.0)
        self.wallet = Wallet.load(cfg.wallet.age_key_path, creds.key_passphrase)
        self.rpc = HeliusRpc(resolve_rpc_url(cfg.rpc, creds), self.http,
                             rps=cfg.rpc.max_requests_per_second,
                             commitment=cfg.rpc.commitment)
        self.jup = JupiterClient(self.http, cfg.jupiter.base_url,
                                 api_key=creds.jupiter_api_key,
                                 rps=cfg.jupiter.max_requests_per_second)
        self.dex = DexScreenerClient(self.http, rps=cfg.discovery.dexscreener_rps)
        self.rug = RugCheckClient(self.http, rps=cfg.discovery.rugcheck_rps)
        self.birdeye = BirdeyeClient(self.http, creds.birdeye_api_key,
                                     rps=cfg.discovery.birdeye_rps)

        self.bus = Bus()
        self.bb = Blackboard()
        self.db = Database(cfg.data.db_path)
        self.store = StateStore(cfg.data.snapshot_path, cfg.data.trade_log_path,
                                cfg.data.decision_log_path)
        self.breaker = PortfolioBreaker(cfg.risk, cfg.data.state_dir)
        self.probation = Probation(cfg.risk, cfg.data.state_dir)
        self.kill = KillSwitch(cfg.data.state_dir)
        self.blacklist = Blacklist(cfg.data.state_dir)
        self.phantom = PhantomLog(self.bus.publish)
        self.ceo = GreedyCeo(cfg)

        self.scanner = Scanner(cfg, self.dex, self.rug, self.birdeye, self.jup,
                               self.rpc, self.phantom, self.blacklist,
                               self.bus.publish)
        self.executor = ExecutionAgent(cfg, self.wallet, self.rpc, self.jup,
                                       self.probation, self.bus.publish,
                                       self._log_trade)
        self.gate = SafetyGate(cfg, self.breaker, self.probation, self.kill,
                               self.bb, self.jup, self.executor,
                               self.scanner.rug_verdict_for,
                               self.scanner.is_blacklisted,
                               self._log_decision,
                               self.scanner.decimals_for)
        self.meme = RotationAgent(cfg, self.bb, self.bus.publish, self.phantom,
                                  self.breaker, self.price_usd,
                                  self.scanner.decimals_for)
        self.core = CoreAgent(cfg, self.bb, self.bus.publish)
        self.yield_ = YieldAgent(cfg, self.bb, self.bus.publish)
        self.backfill = BackfillJob(self.db, self.price_usd)

    # -- shared helpers ---------------------------------------------------

    async def _log_decision(self, record: dict) -> None:
        self.store.log_decision(record)

    async def _log_trade(self, record: dict) -> None:
        self.store.log_trade(record)
        if record.get("kind") == "fill":
            await self.bus.publish("persist", {
                "table": "trades",
                "trade_id": record["intent_id"],
                "sleeve": record.get("sleeve"), "mint": record.get("mint"),
                "side": record.get("side"), "tx_sig": record.get("tx_sig"),
                "quote_out": record.get("quoted_out"),
                "fill_out": record.get("actual_out"),
                "slippage_pct": record.get("realized_slippage_pct"),
                "fill_ts": datetime.now(timezone.utc).isoformat(),
                "probation": 1 if self.probation.active else 0})

    async def price_usd(self, mint: str) -> Optional[float]:
        """Marks: DexScreener first, Jupiter quote fallback."""
        px = await self.dex.price_usd(mint)
        if px is not None:
            return px
        decimals = await self.scanner.decimals_for(mint)
        return await self.jup.price_usd_per_token(mint, decimals)

    # -- runtime ----------------------------------------------------------

    async def start(self) -> None:
        await self.db.open()
        persist_q = self.bus.subscribe("persist")
        log.info("skyfire_boot", wallet=str(self.wallet.pubkey),
                 probation=self.probation.active,
                 breaker=self.breaker.state.value)

        tasks = [
            ("db_writer", lambda: self.db.writer_task(persist_q)),
            ("scanner", self.scanner.run),
            ("meme", self.meme.run),
            ("core", self.core.run),
            ("yield", self.yield_.run),
            ("nav", self._nav_task),
            ("intents", self._intent_pipeline),
            ("signals", self._signals_pipeline),
            ("fills", self._fills_pipeline),
            ("failures", self._failures_pipeline),
            ("ceo", self._ceo_task),
            ("safety_watch", self._safety_watch),
            ("snapshot", self._snapshot_task),
            ("backfill", self.backfill.run),
        ]
        try:
            await asyncio.gather(*(
                supervised(name, fn, self._stop, log) for name, fn in tasks))
        except BaseException:
            open_positions = list(self.meme.book.values())
            if open_positions:
                try:
                    self.breaker.trip("fatal runtime crash with open positions")
                except Exception as e:              # noqa: BLE001 — never skip flatten
                    log.error("crash_trip_failed", error=str(e))
                try:
                    await self.meme.flatten_all("fatal_crash")
                    await self._drain_intents_once()
                except Exception as e:              # noqa: BLE001 — best effort
                    log.error("crash_flatten_failed", error=str(e))
            raise
        finally:
            await self.http.aclose()
            await self.db.close()

    def stop(self) -> None:
        self._stop.set()

    # -- pipelines --------------------------------------------------------

    async def _intent_pipeline(self) -> None:
        q = self.bus.subscribe("intents")
        while True:
            intent = await q.get()
            verdict = None
            if intent.kind is IntentKind.ENTRY \
                    and intent.sleeve is SleeveId.MEME_ROTATION:
                verdict = self.ceo.judge_intent(intent, self.bb.snapshot())
            await self.gate.process(intent, verdict)

    async def _drain_intents_once(self) -> None:
        """Crash path: push any queued flatten exits through the gate."""
        q = self.bus.subscribe("intents")
        await asyncio.sleep(0)                       # let publishers enqueue
        while not q.empty():
            await self.gate.process(q.get_nowait(), None)

    async def _signals_pipeline(self) -> None:
        q = self.bus.subscribe("meme_signals")
        while True:
            signals = await q.get()
            await self.meme.on_signals(signals)

    async def _fills_pipeline(self) -> None:
        q = self.bus.subscribe("fills")
        while True:
            fill = await q.get()
            await self.meme.on_fill(fill)

    async def _failures_pipeline(self) -> None:
        q = self.bus.subscribe("execution_failures")
        while True:
            msg = await q.get()
            await self.meme.on_execution_failure(msg)

    # -- NAV / blackboard (single writer) ---------------------------------

    async def _nav_task(self) -> None:
        while True:
            try:
                await self._nav_pass()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("nav_pass_failed", error=str(e))
            await asyncio.sleep(NAV_LOOP_S)

    async def _nav_pass(self) -> None:
        now = datetime.now(timezone.utc)
        owner = str(self.wallet.pubkey)
        sol_lamports = await self.rpc.get_sol_balance(owner)
        balances = await self.rpc.get_token_accounts(owner)
        balances[WSOL_MINT] = balances.get(WSOL_MINT, 0) + sol_lamports

        prices: dict[str, float] = {USDC_MINT: 1.0}
        for mint in (WSOL_MINT, JITOSOL_MINT, WBTC_MINT, WETH_MINT):
            if balances.get(mint) or mint == WSOL_MINT:
                px = await self.jup.price_usd_per_token(mint, MAJOR_DECIMALS[mint])
                if px:
                    prices[mint] = px
        for pos in self.meme.book.values():
            if pos.last_mark_usd:
                prices[pos.mint] = pos.last_mark_usd

        nav = 0.0
        for mint, raw in balances.items():
            decimals = MAJOR_DECIMALS.get(mint)
            if decimals is None:
                pos = next((p for p in self.meme.book.values() if p.mint == mint), None)
                if pos is None:
                    continue                        # unpriced dust stays out of NAV
                decimals = pos.decimals
            px = prices.get(mint)
            if px:
                nav += raw / 10 ** decimals * px

        sane = self.breaker.accept_nav(nav)
        if sane is None:
            return                                   # quarantined reading
        self.breaker.on_nav(sane, now)
        if WSOL_MINT in prices:
            self.ceo.on_sol_mark(prices[WSOL_MINT], now)

        meme_nav = sum(self.meme._pos_value(p) for p in self.meme.book.values())
        yield_nav = balances.get(JITOSOL_MINT, 0) / 1e9 * prices.get(JITOSOL_MINT, 0.0)
        perps_nav = 0.0                              # margin lives on Drift (Phase 4)
        core_nav = max(0.0, sane - meme_nav - yield_nav - perps_nav)
        sleeve_navs = {SleeveId.MEME_ROTATION.value: meme_nav,
                       SleeveId.CORE_HOLD.value: core_nav,
                       SleeveId.YIELD.value: yield_nav,
                       SleeveId.PERPS.value: perps_nav}
        current = {k: (v / sane * 100.0 if sane > 0 else 0.0)
                   for k, v in sleeve_navs.items()}

        regime = self._regime or self.ceo.read_regime(
            self.scanner.last_breadth, self.scanner.last_agg_volume)
        safety = SafetyStateView(
            breaker=self.breaker.state.value,
            soft_tier_active=self.breaker.soft_tier_active,
            daily_pause_until=self.breaker.pause_until,
            kill=self.kill.engaged,
            probation=self.probation.active,
            clean_fills=self.probation.clean_fills,
            hwm_usd=self.breaker.hwm_usd,
            drawdown_pct=self.breaker.drawdown_pct(sane))

        self.bb.swap(Snapshot(
            ts=now, nav_usd=sane, sleeve_navs=sleeve_navs,
            allocations_current=current, allocations_target=dict(self._targets),
            positions=tuple(self.meme.book.values()), prices_usd=prices,
            balances_raw=balances, regime=regime, safety=safety,
            perf=tuple(await self._perf(sleeve_navs))))

        if (now.timestamp() - self._last_nav_history) >= NAV_HISTORY_EVERY_S:
            self._last_nav_history = now.timestamp()
            for sleeve, v in sleeve_navs.items():
                await self.bus.publish("persist", {
                    "table": "nav_history", "ts": now.isoformat(),
                    "sleeve": sleeve, "nav_usd": v})

    async def _perf(self, sleeve_navs: dict[str, float]) -> list[SleevePerf]:
        out: list[SleevePerf] = []
        cutoff_7d = (datetime.now(timezone.utc).timestamp() - 7 * 86400)
        for sleeve, nav in sleeve_navs.items():
            rows = await self.db.rows(
                "SELECT ts, nav_usd FROM nav_history WHERE sleeve = ? "
                "ORDER BY ts DESC LIMIT 2500", (sleeve,))
            series = [r[1] for r in reversed(rows)]
            first_ts = rows[-1][0] if rows else None
            warmup = (first_ts is None
                      or datetime.fromisoformat(first_ts).timestamp() > cutoff_7d)
            ret24 = None
            day_ago = datetime.now(timezone.utc).timestamp() - 86400
            past = [r for r in rows if datetime.fromisoformat(r[0]).timestamp() <= day_ago]
            if past and past[0][1] > 0:
                ret24 = (nav / past[0][1] - 1.0) * 100.0
            out.append(SleevePerf(
                sleeve=SleeveId(sleeve), nav_usd=nav, ret_24h_pct=ret24,
                sharpe_7d=GreedyCeo.sharpe_7d(series), warmup=warmup))
        return out

    # -- CEO loop ---------------------------------------------------------

    async def _ceo_task(self) -> None:
        while True:
            try:
                await self._ceo_pass()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("ceo_pass_failed", error=str(e))
            await asyncio.sleep(self.cfg.ceo.loop_seconds)

    async def _ceo_pass(self) -> None:
        snap = self.bb.snapshot()
        self._regime = self.ceo.read_regime(
            self.scanner.last_breadth, self.scanner.last_agg_volume)
        proposed = self.ceo.allocate(list(snap.perf), self._regime)
        clamped = self.gate.clamp_allocations(proposed.targets, self._regime.state)
        scores = self.ceo.greedy_scores(list(snap.perf))
        await journal_allocation(self.bus.publish, proposed, clamped, scores)
        changed = any(abs(clamped.get(k, 0) - self._targets.get(k, 0)) > 0.5
                      for k in clamped)
        self._targets = clamped
        if changed:
            log.info("allocation_update", targets=clamped,
                     regime=self._regime.state.value, reasoning=proposed.reasoning)
            await self.meme.on_allocation(clamped)

    # -- safety watch -----------------------------------------------------

    async def _safety_watch(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            tripped = self.breaker.state is BreakerState.TRIPPED
            if (self.kill.engaged or tripped) and not self._flatten_fired:
                self._flatten_fired = True
                reason = "kill_switch" if self.kill.engaged else "breaker_flatten"
                log.error("flatten_all", reason=reason)
                await self.meme.flatten_all("breaker_flatten")
            if tripped and not self.meme.book:
                self.breaker.confirm_flat()
            if not self.kill.engaged and self.breaker.state is BreakerState.ARMED:
                self._flatten_fired = False

    # -- snapshot / heartbeat ---------------------------------------------

    async def _snapshot_task(self) -> None:
        last_hb = 0.0
        while True:
            snap = self.bb.snapshot()
            self.store.write_snapshot({
                "nav_usd": snap.nav_usd,
                "sleeve_navs": snap.sleeve_navs,
                "allocations_current": snap.allocations_current,
                "allocations_target": snap.allocations_target,
                "positions": [vars(p) for p in snap.positions],
                "regime": snap.regime.state.value,
                "safety": vars(snap.safety),
                "probation_clean_fills": self.probation.clean_fills,
            })
            now = datetime.now(timezone.utc).timestamp()
            if now - last_hb >= self.cfg.data.heartbeat_seconds:
                last_hb = now
                state = ("locked" if self.breaker.state is not BreakerState.ARMED
                         else "live")
                write_heartbeat(self.cfg.data.heartbeat_path, self.bb, state)
            await asyncio.sleep(self.cfg.data.snapshot_seconds)
