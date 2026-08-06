"""v2 executor: long single-leg 0DTE options, in fast, out faster.

Entry: marketable limit ladder from mid toward the ask (3 rungs, 3s).
Exit engine priorities per position:
  P0 lock/flatten   P1 force-flat 15:30   P2 hard stop (-50% premium)
  P3 scale-outs into strength (sell 50% at 2x, 25% at 4x by default)
  P4 runner trail (-40% from high-water)   P5 max-hold 60 min

PaperV2 fills entries one tick above mid and exits one tick below — the
same order path, honest-ish slippage, friction charged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Optional

from godmode0dte.execution.broker import snap_tick
from godmode0dte.models import Direction, Quote
from godmode0dte.monitoring.logging import get_logger
from godmode0dte_v2.config import V2Config

log = get_logger("v2.executor")


@dataclass
class V2Position:
    trade_id: str
    symbol: str                    # OCC
    streamer_symbol: str
    direction: Direction           # LONG=calls, SHORT=puts (bought either way)
    entry_premium: float           # per share
    contracts_open: int
    contracts_initial: int
    entry_ts: datetime
    high_water: float = 0.0
    scale_outs_done: int = 0
    realized: float = 0.0          # net $ realized so far (fees included)
    stop_hits: int = 0


@dataclass(frozen=True)
class V2Exit:
    trade_id: str
    reason: str
    contracts: int                 # how many to sell now
    urgent: bool


class V2ExitEngine:
    def __init__(self, cfg: V2Config) -> None:
        self._cfg = cfg

    def evaluate(self, pos: V2Position, mark: float, now: datetime,
                 now_et: time, locked: bool) -> Optional[V2Exit]:
        e = self._cfg.exits
        if locked:
            return V2Exit(pos.trade_id, "lock_flatten", pos.contracts_open, True)
        if now_et >= e.force_flat:
            return V2Exit(pos.trade_id, "force_flat", pos.contracts_open, True)
        # P2 hard stop — 2 consecutive marks, same discipline as v1.
        if mark <= pos.entry_premium * (1 - e.hard_stop_pct / 100.0):
            pos.stop_hits += 1
            if pos.stop_hits >= 2:
                return V2Exit(pos.trade_id, "hard_stop", pos.contracts_open, True)
        else:
            pos.stop_hits = 0
        pos.high_water = max(pos.high_water, mark)
        # P3 scale-outs into strength.
        if pos.scale_outs_done < len(e.scale_outs):
            mult, frac = e.scale_outs[pos.scale_outs_done]
            if mark >= pos.entry_premium * mult:
                qty = max(1, int(pos.contracts_initial * frac))
                qty = min(qty, pos.contracts_open)
                return V2Exit(pos.trade_id, f"scale_out_{mult:g}x", qty, False)
        # P4 runner trail once any scale-out has banked.
        if pos.scale_outs_done > 0 and pos.high_water > 0 and \
                mark <= pos.high_water * (1 - e.runner_trail_pct / 100.0):
            return V2Exit(pos.trade_id, "runner_trail", pos.contracts_open, False)
        # P5 the extreme resolved or it didn't.
        if (now - pos.entry_ts).total_seconds() / 60.0 >= e.max_hold_min:
            return V2Exit(pos.trade_id, "max_hold", pos.contracts_open, False)
        return None


class PaperV2Broker:
    """Same decision path, simulated fills, friction charged."""

    def __init__(self, cfg: V2Config, starting_equity: float) -> None:
        self._cfg = cfg
        self.equity_value = starting_equity

    async def equity(self) -> float:
        return self.equity_value

    async def buy(self, symbol: str, contracts: int, q: Quote) -> Optional[float]:
        px = snap_tick(q.mid + 0.01, "SPY", round_up=True)
        if px > q.ask:
            return None
        friction = self._cfg.fees.per_contract_round_trip("SPY", legs=1) / 2
        self.equity_value -= px * contracts * 100 + friction * contracts
        return px

    async def sell(self, symbol: str, contracts: int, q: Quote, urgent: bool) -> Optional[float]:
        px = snap_tick(max(0.01, q.mid - (0.02 if urgent else 0.01)), "SPY", round_up=False)
        friction = self._cfg.fees.per_contract_round_trip("SPY", legs=1) / 2
        self.equity_value += px * contracts * 100 - friction * contracts
        return px


class LiveV2Broker:
    """tastytrade single-leg orders; entry/exit ladders mirror v1 discipline
    (status resolved to terminal, never two live orders per trade)."""

    def __init__(self, session, account, cfg: V2Config) -> None:
        self._session = session
        self._account = account
        self._cfg = cfg

    async def equity(self) -> float:
        balances = await self._account.a_get_balances(self._session)
        return float(balances.net_liquidating_value)

    async def _ladder(self, symbol: str, contracts: int, q: Quote,
                      action_name: str, buying: bool, urgent: bool = False) -> Optional[float]:
        from decimal import Decimal
        from tastytrade.instruments import Option
        from tastytrade.order import NewOrder, OrderAction, OrderStatus, OrderTimeInForce, OrderType

        opt = await Option.a_get(self._session, symbol)
        action = OrderAction.BUY_TO_OPEN if buying else OrderAction.SELL_TO_CLOSE
        steps = self._cfg.instrument.entry_ladder_max_steps
        for step in range(steps + 1):
            span = (q.ask - q.mid) if buying else (q.mid - q.bid)
            px = q.mid + span * (step / max(1, steps)) * (1 if buying else -1)
            if urgent and step == steps:
                px = q.ask + 0.01 if buying else max(0.01, q.bid - 0.01)
            px = snap_tick(max(0.01, px), "SPY", round_up=buying)
            order = NewOrder(time_in_force=OrderTimeInForce.DAY, order_type=OrderType.LIMIT,
                             legs=[opt.build_leg(Decimal(contracts), action)],
                             price=Decimal(str(-px if buying else px)))
            resp = await self._account.a_place_order(self._session, order, dry_run=False)
            placed = resp.order
            if str(getattr(placed, "status", "")).upper().endswith("REJECTED"):
                log.error("v2_order_rejected", action=action_name,
                          reason=str(getattr(resp, "errors", ""))[:200])
                return None
            deadline = asyncio.get_event_loop().time() + self._cfg.instrument.entry_ladder_wait_sec
            filled = False
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(1.0)
                try:
                    live = await self._account.a_get_live_orders(self._session)
                except Exception:                    # noqa: BLE001
                    continue
                match = next((o for o in live if o.id == placed.id), None)
                if match is None or match.status == OrderStatus.FILLED:
                    filled = match is None or match.status == OrderStatus.FILLED
                    break
            if filled:
                return px
            try:
                await self._account.a_delete_order(self._session, placed.id)
            except Exception:                        # noqa: BLE001 — resolve like v1
                await asyncio.sleep(2.0)
                try:
                    live = await self._account.a_get_live_orders(self._session)
                    if not any(o.id == placed.id for o in live):
                        return px                     # filled-and-dropped
                except Exception:                    # noqa: BLE001
                    return None                       # unknown: never re-ladder blind
        return None

    async def buy(self, symbol: str, contracts: int, q: Quote) -> Optional[float]:
        return await self._ladder(symbol, contracts, q, "buy", buying=True)

    async def sell(self, symbol: str, contracts: int, q: Quote, urgent: bool) -> Optional[float]:
        return await self._ladder(symbol, contracts, q, "sell", buying=False, urgent=urgent)
