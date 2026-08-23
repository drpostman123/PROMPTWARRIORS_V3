"""RotationAgent — the MEME_ROTATION sleeve (the alpha engine).

Consumes scored, rug-clean signals from the scanner; sizes entries to
the per-position cap within the sleeve's target NAV; manages the book
with the pure exit engine (2x scale-out, -40% trail from peak, 48h time
stop); logs near-misses to the phantom log. All orders leave as
TradeIntents — the sleeve holds no executor and no wallet.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from skyfire_sol.blackboard import Blackboard
from skyfire_sol.config import USDC_MINT, AppConfig
from skyfire_sol.meme import exits as exit_engine
from skyfire_sol.models import (
    EntrySignal,
    Fill,
    IntentKind,
    PhantomCandidate,
    Position,
    Side,
    SleeveId,
    Urgency,
)
from skyfire_sol.persistence.phantom import PhantomLog
from skyfire_sol.safety.breakers import BreakerState, PortfolioBreaker
from skyfire_sol.sleeves.base import SleeveAgent
from tradecore.logging import get_logger

log = get_logger("meme_sleeve")

MARK_LOOP_S = 20.0


class RotationAgent(SleeveAgent):
    sleeve = SleeveId.MEME_ROTATION

    def __init__(self, cfg: AppConfig, blackboard: Blackboard,
                 publish: Callable[[str, object], Awaitable[None]],
                 phantom: PhantomLog,
                 breaker: PortfolioBreaker,
                 price_usd: Callable[[str], Awaitable[Optional[float]]],
                 decimals_for: Callable[[str], Awaitable[int]]) -> None:
        super().__init__(cfg, blackboard, publish)
        self._phantom = phantom
        self._breaker = breaker
        self._price = price_usd
        self._decimals_for = decimals_for
        self.book: dict[str, Position] = {}          # position_id -> Position
        self._pending_entries: dict[str, EntrySignal] = {}   # intent_id -> signal
        self._pending_exits: dict[str, int] = {}     # position_id -> qty_raw working

    # -- signal intake ----------------------------------------------------

    async def on_signals(self, signals: list[tuple[EntrySignal, dict]]) -> None:
        cfg = self._cfg.sleeves.meme
        open_and_pending = len(self.book) + len(self._pending_entries)
        held = {p.mint for p in self.book.values()}
        now = datetime.now(timezone.utc)

        ranked = sorted((s for s, _ in signals), key=lambda s: s.score, reverse=True)
        features = {s.mint: f for s, f in signals}
        slots = max(0, cfg.max_positions - open_and_pending)

        for sig in ranked:
            if sig.mint in held:
                continue
            if slots <= 0:
                await self._phantom.record(PhantomCandidate(
                    mint=sig.mint, symbol=sig.symbol, stage="near_miss",
                    reject_reason="no_slot", features=features.get(sig.mint, {}),
                    price_usd=sig.price_usd, ts=now))
                continue
            size = self._entry_size_usd()
            if size < cfg.min_entry_usd:
                await self._phantom.record(PhantomCandidate(
                    mint=sig.mint, symbol=sig.symbol, stage="near_miss",
                    reject_reason="no_capital", features=features.get(sig.mint, {}),
                    price_usd=sig.price_usd, ts=now))
                continue
            intent = await self.publish_intent(
                IntentKind.ENTRY, Side.BUY, sig.mint, USDC_MINT, size,
                reason=f"momentum score {sig.score}")
            self._pending_entries[intent.intent_id] = sig
            slots -= 1
            log.info("entry_intent", mint=sig.mint, score=sig.score, size_usd=size)

    def _entry_size_usd(self) -> float:
        """Per-position cap within the sleeve target, less deployed value.
        The gate re-clamps; this is the sleeve being a good citizen."""
        cfg = self._cfg.sleeves.meme
        target = self.target_nav_usd()
        deployed = sum(self._pos_value(p) for p in self.book.values())
        available = max(0.0, target - deployed)
        return min(target * cfg.max_position_pct_of_sleeve / 100.0, available)

    def _pos_value(self, p: Position) -> float:
        px = p.last_mark_usd or p.entry_price_usd
        return p.qty_raw / 10 ** p.decimals * px

    # -- fills ------------------------------------------------------------

    async def on_fill(self, fill: Fill) -> None:
        if fill.sleeve is not SleeveId.MEME_ROTATION:
            return
        if fill.side is Side.BUY:
            sig = self._pending_entries.pop(fill.intent_id, None)
            decimals = await self._decimals_for(fill.mint)
            tokens = fill.actual_out / 10 ** decimals
            # Implied fill price beats the signal mark (it includes slippage).
            entry_px = sig.price_usd if sig else 0.0
            if tokens > 0 and fill.quote_mint == USDC_MINT:
                entry_px = (fill.in_amount_raw / 1e6) / tokens
            pos = Position(
                position_id=fill.position_id, sleeve=self.sleeve, mint=fill.mint,
                symbol=sig.symbol if sig else "?", quote_mint=fill.quote_mint,
                qty_raw=fill.actual_out, decimals=decimals,
                entry_price_usd=entry_px, entry_ts=fill.ts,
                peak_price_usd=entry_px, last_mark_usd=entry_px, last_mark_ts=fill.ts)
            self.book[fill.position_id] = pos
            log.info("position_opened", position_id=pos.position_id, mint=pos.mint,
                     qty_raw=pos.qty_raw, entry_price=entry_px)
        else:
            sold_raw = self._pending_exits.pop(fill.position_id, fill.in_amount_raw)
            pos = self.book.get(fill.position_id)
            if pos is None:
                return
            pos.qty_raw = max(0, pos.qty_raw - sold_raw)
            if pos.qty_raw <= 0:
                del self.book[fill.position_id]
                log.info("position_closed", position_id=fill.position_id)

    async def on_execution_failure(self, msg: dict) -> None:
        self._pending_entries.pop(msg.get("intent_id", ""), None)
        pid = msg.get("position_id")
        if pid:
            self._pending_exits.pop(pid, None)

    # -- mark + exit loop -------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                await self._mark_and_exit_pass()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("meme_mark_pass_failed", error=str(e))
            await asyncio.sleep(MARK_LOOP_S)

    async def _mark_and_exit_pass(self) -> None:
        now = datetime.now(timezone.utc)
        locked = self._breaker.state is not BreakerState.ARMED
        for pos in list(self.book.values()):
            px = await self._price(pos.mint)
            if px is not None and px > 0:
                pos.last_mark_usd = px
                pos.last_mark_ts = now
                pos.peak_price_usd = max(pos.peak_price_usd, px)
            mark = pos.last_mark_usd or pos.entry_price_usd
            if pos.position_id in self._pending_exits:
                continue
            order = exit_engine.evaluate(pos, mark, now, locked, self._cfg.sleeves.meme)
            if order is None:
                continue
            qty = int(pos.qty_raw * order.frac)
            if qty <= 0:
                continue
            if order.reason == "take_profit_2x":
                pos.scaled_out = True
            self._pending_exits[pos.position_id] = qty
            await self.publish_intent(
                IntentKind.EXIT, Side.SELL, pos.mint, pos.quote_mint,
                size_usd=qty / 10 ** pos.decimals * mark, reason=order.reason,
                urgency=order.urgency, position_id=pos.position_id, qty_raw=qty)
            log.info("exit_intent", position_id=pos.position_id, reason=order.reason,
                     frac=order.frac, urgency=order.urgency.value)

    async def flatten_all(self, reason: str) -> None:
        """Breaker/kill path: exit everything, urgent."""
        for pos in list(self.book.values()):
            if pos.position_id in self._pending_exits:
                continue
            self._pending_exits[pos.position_id] = pos.qty_raw
            mark = pos.last_mark_usd or pos.entry_price_usd
            await self.publish_intent(
                IntentKind.EXIT, Side.SELL, pos.mint, pos.quote_mint,
                size_usd=pos.qty_raw / 10 ** pos.decimals * mark,
                reason=reason, urgency=Urgency.URGENT,
                position_id=pos.position_id, qty_raw=pos.qty_raw)

    async def on_allocation(self, targets: dict[str, float]) -> None:
        """Sell down weakest positions when the sleeve is over target."""
        snap = self._bb.snapshot()
        target_usd = snap.nav_usd * targets.get(self.sleeve.value, 0.0) / 100.0
        deployed = sum(self._pos_value(p) for p in self.book.values())
        excess = deployed - target_usd
        if excess <= max(50.0, 0.1 * max(target_usd, 1.0)):
            return
        by_perf = sorted(
            self.book.values(),
            key=lambda p: (p.last_mark_usd or p.entry_price_usd) / p.entry_price_usd
            if p.entry_price_usd > 0 else 0.0)
        for pos in by_perf:
            if excess <= 0:
                break
            if pos.position_id in self._pending_exits:
                continue
            value = self._pos_value(pos)
            self._pending_exits[pos.position_id] = pos.qty_raw
            await self.publish_intent(
                IntentKind.EXIT, Side.SELL, pos.mint, pos.quote_mint,
                size_usd=value, reason="ceo_reallocation", urgency=Urgency.NORMAL,
                position_id=pos.position_id, qty_raw=pos.qty_raw)
            excess -= value
