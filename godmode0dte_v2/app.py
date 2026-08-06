"""v2 runtime: scan for extremes, hit hard, sell into strength, survive.

Reuses v1's data plane wholesale (DXLink hub, candle warmup, book pulse,
rel-volume baseline) and runs three supervised loops:

  signal task    snapshot -> PerfectSetupDetector -> (present | auto-enter)
  risk task      equity/lock, marks, V2ExitEngine, scale-outs, shadow book
  snapshot task  state/v2_snapshot.json for the dashboard/operator

`mode: present` (default) surfaces each perfect setup — context, metric
snapshot, suggested contracts/premium — in the log and snapshot and lets
the human fire. `mode: auto` pulls the trigger itself. Every surfaced
setup is shadow-followed either way, so the outcome record accumulates
regardless of what the human did. That record is the only referee this
style gets — v2 deliberately has no statistical promotion gate.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from godmode0dte.config import AppConfig, Credentials
from godmode0dte.data.market_data import MarketDataHub
from godmode0dte.features.volume import RelativeVolume
from godmode0dte.models import Direction, Quote
from godmode0dte.monitoring.logging import get_logger, setup_logging
from godmode0dte.state.store import StateStore
from godmode0dte_v2.config import V2Config
from godmode0dte_v2.conviction import ConvictionSignal, PerfectSetupDetector
from godmode0dte_v2.executor import LiveV2Broker, PaperV2Broker, V2ExitEngine, V2Position
from godmode0dte_v2.risk import V2Risk

log = get_logger("v2.app")


class GodModeV2App:
    def __init__(self, cfg: V2Config) -> None:
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)
        today = datetime.now(self.tz).date()
        hub_cfg = AppConfig()                      # v1 data plane, default knobs
        hub_cfg.timezone = cfg.timezone
        self.md = MarketDataHub(hub_cfg, cfg.instrument.underlying)
        self.rel_volume = RelativeVolume(tz=cfg.timezone)
        self.detector = PerfectSetupDetector(cfg)
        self.risk = V2Risk(cfg, today)
        self.exits = V2ExitEngine(cfg)
        self.store = StateStore(cfg.data)
        self.broker = None
        self.session = None
        self.account = None
        self.positions: dict[str, V2Position] = {}
        self.shadows: dict[str, V2Position] = {}
        self.pending_setup: Optional[dict] = None   # present-mode surface
        self.calls: dict[float, tuple[str, str]] = {}   # strike -> (occ, streamer)
        self.puts: dict[float, tuple[str, str]] = {}
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------

    async def start(self) -> None:
        setup_logging()
        log.info("v2_boot", mode=self.cfg.mode, paper=self.cfg.paper_mode,
                 conviction_risk_pct=self.cfg.sizing.conviction_risk_pct,
                 daily_limit_pct=self.cfg.sizing.daily_loss_limit_pct)
        creds = Credentials.from_env()
        from tastytrade import Account, Session
        self.session = Session(creds.username, creds.password)
        accounts = await Account.a_get(self.session)
        self.account = accounts[self.cfg.account_index]
        equity = float((await self.account.a_get_balances(self.session)).net_liquidating_value)
        self.broker = (PaperV2Broker(self.cfg, equity) if self.cfg.paper_mode
                       else LiveV2Broker(self.session, self.account, self.cfg))
        self.risk.update_equity(equity)
        await self.md.start(self.session)
        await self._load_chain()
        await asyncio.gather(self._supervised("signal", self._signal_task),
                             self._supervised("risk", self._risk_task),
                             self._supervised("snapshot", self._snapshot_task))

    async def _supervised(self, name: str, fn) -> None:
        while not self._stop.is_set():
            try:
                await fn()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:                  # noqa: BLE001
                log.error("v2_task_crashed", task=name, error=str(e))
                await asyncio.sleep(1.0)

    async def _load_chain(self) -> None:
        from tastytrade.instruments import NestedOptionChain
        chains = await NestedOptionChain.a_get(self.session, self.cfg.instrument.underlying)
        chain = chains[0] if isinstance(chains, list) else chains
        today = datetime.now(self.tz).date()
        exp = next((e for e in chain.expirations if e.expiration_date == today), None)
        if exp is None:
            log.error("v2_no_0dte_expiration")
            return
        symbols = []
        for s in exp.strikes:
            k = float(s.strike_price)
            self.calls[k] = (s.call, s.call_streamer_symbol)
            self.puts[k] = (s.put, s.put_streamer_symbol)
            symbols += [s.call_streamer_symbol, s.put_streamer_symbol]
        await self.md.watch_options(symbols)

    # -- selection ------------------------------------------------------

    def _pick_option(self, direction: Direction) -> Optional[tuple[str, str, Quote]]:
        """Closest-to-target |delta| leg passing spread/OI gates."""
        table = self.calls if direction is Direction.LONG else self.puts
        best: Optional[tuple[float, str, str, Quote]] = None
        for _, (occ, streamer) in table.items():
            d = self.md.deltas.get(streamer)
            q = self.md.quotes.get(streamer)
            oi = self.md.open_interest.get(streamer, 0)
            if d is None or q is None or q.bid <= 0 or q.ask <= 0:
                continue
            if oi < self.cfg.instrument.min_open_interest:
                continue
            if q.mid > 0 and 100.0 * q.spread / q.mid > self.cfg.instrument.max_leg_spread_pct_of_mid:
                continue
            gap = abs(abs(d) - self.cfg.instrument.target_delta)
            if best is None or gap < best[0]:
                best = (gap, occ, streamer, q)
        return (best[1], best[2], best[3]) if best else None

    # -- loops ----------------------------------------------------------

    async def _signal_task(self) -> None:
        last_bar = None
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            bars = self.md.session_bars()
            if not bars or bars[-1].ts == last_bar:
                continue
            last_bar = bars[-1].ts
            now = datetime.now(timezone.utc)
            book = self.md.book.state().imbalance if self.md.book.ready else None
            snap = self.detector.compute_snapshot(
                bars, self.md.warm_bars_5m, book,
                self.rel_volume.ratio(bars[-1], bars), self.md.macro.vix, now)
            if snap is None:
                continue
            sig = self.detector.evaluate(snap, now)
            if sig is None:
                continue
            await self._handle_signal(sig)

    async def _handle_signal(self, sig: ConvictionSignal) -> None:
        pick = self._pick_option(sig.direction)
        if pick is None:
            self.store.log_decision({"kind": "v2_no_option", "direction": sig.direction.value})
            return
        occ, streamer, q = pick
        friction = self.cfg.fees.per_contract_round_trip("SPY", legs=1)
        contracts, premium, detail = self.risk.size_premium(q.mid, friction)
        surface = {
            "kind": "v2_setup", "direction": sig.direction.value,
            "option": occ, "mid": q.mid, "suggested_contracts": contracts,
            "premium_at_risk": round(premium, 2), "sizing_detail": detail,
            "metrics": vars(sig.snapshot), "conditions_passed": list(sig.passed),
        }
        self.store.log_decision(surface)
        self.pending_setup = surface
        # Shadow-follow EVERY surfaced setup at mid, taken or not.
        if self.cfg.shadow_all_signals:
            sid = f"v2shadow-{uuid.uuid4().hex[:8]}"
            self.shadows[sid] = V2Position(
                trade_id=sid, symbol=occ, streamer_symbol=streamer,
                direction=sig.direction, entry_premium=q.mid, contracts_open=1,
                contracts_initial=1, entry_ts=sig.ts, high_water=q.mid)
        if self.cfg.mode != "auto":
            log.info("v2_setup_presented", **{k: v for k, v in surface.items()
                                              if k not in ("metrics",)})
            return
        if contracts < 1:
            return
        fill = await self.broker.buy(occ, contracts, q)
        if fill is None:
            self.store.log_decision({"kind": "v2_entry_unfilled", "option": occ})
            return
        self.risk.record_trade()
        tid = f"v2-{uuid.uuid4().hex[:8]}"
        self.positions[tid] = V2Position(
            trade_id=tid, symbol=occ, streamer_symbol=streamer,
            direction=sig.direction, entry_premium=fill, contracts_open=contracts,
            contracts_initial=contracts, entry_ts=sig.ts, high_water=fill)
        self.store.log_trade({"kind": "v2_entry", "trade_id": tid, "option": occ,
                              "fill": fill, "contracts": contracts,
                              "premium_at_risk": round(fill * contracts * 100, 2),
                              "metrics": vars(sig.snapshot)})

    async def _risk_task(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            try:
                self.risk.update_equity(await self.broker.equity())
            except Exception as e:                  # noqa: BLE001
                log.error("v2_equity_failed", error=str(e))
            now = datetime.now(timezone.utc)
            now_et = datetime.now(self.tz).time()
            for book, is_real in ((self.positions, True), (self.shadows, False)):
                for tid in list(book):
                    pos = book[tid]
                    q = self.md.quotes.get(pos.streamer_symbol)
                    mark = q.mid if q and q.bid > 0 else pos.entry_premium
                    decision = self.exits.evaluate(pos, mark, now, now_et,
                                                   locked=self.risk.locked and is_real)
                    if decision is None:
                        continue
                    await self._exit(book, pos, decision.contracts, mark, q,
                                     decision.reason, decision.urgent, is_real)

    async def _exit(self, book, pos: V2Position, qty: int, mark: float,
                    q: Optional[Quote], reason: str, urgent: bool, is_real: bool) -> None:
        friction = self.cfg.fees.per_contract_round_trip("SPY", legs=1)
        if is_real and q is not None:
            fill = await self.broker.sell(pos.symbol, qty, q, urgent)
            if fill is None:
                log.error("v2_exit_unfilled_retrying", trade_id=pos.trade_id)
                return
        else:
            fill = mark                              # shadow: mid, labeled optimistic
        pnl = (fill - pos.entry_premium) * qty * 100 - friction * qty
        pos.realized += pnl
        pos.contracts_open -= qty
        if reason.startswith("scale_out"):
            pos.scale_outs_done += 1
        row = {"kind": "v2_exit" if is_real else "v2_shadow_exit",
               "trade_id": pos.trade_id, "reason": reason, "fill": fill,
               "contracts": qty, "pnl_net": round(pnl, 2),
               "premium_mult": round(fill / pos.entry_premium, 2) if pos.entry_premium else 0}
        (self.store.log_trade if is_real else self.store.log_decision)(row)
        if pos.contracts_open <= 0:
            log.info("v2_position_closed", trade_id=pos.trade_id,
                     total_net=round(pos.realized, 2), real=is_real)
            del book[pos.trade_id]

    async def _snapshot_task(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            self.store.write_snapshot({
                "version": "v2", "mode": self.cfg.mode,
                "paper_mode": self.cfg.paper_mode,
                "equity": self.risk._equity,
                "day_loss_pct": round(self.risk.day_loss_pct(), 2),
                "daily_limit_pct": self.cfg.sizing.daily_loss_limit_pct,
                "locked": self.risk.locked, "lock_reason": self.risk.lock_reason,
                "trades_today": self.risk.trades_today,
                "open_positions": len(self.positions),
                "shadow_open": len(self.shadows),
                "pending_setup": self.pending_setup,
                "price": self.md.underlying_price(),
            })


def main() -> None:
    import argparse
    from godmode0dte_v2.config import load_v2_config
    ap = argparse.ArgumentParser(description="GodMode0DTE v2 — high-conviction runtime")
    ap.add_argument("--config", default="config/config.v2.yaml")
    args = ap.parse_args()
    app = GodModeV2App(load_v2_config(args.config))
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        print("v2 shutdown requested")


if __name__ == "__main__":
    main()
