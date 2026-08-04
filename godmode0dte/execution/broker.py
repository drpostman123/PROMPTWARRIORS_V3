"""Broker adapters. Only the RiskGovernor's owner (the runtime) holds one.

``open_position`` demands an :class:`ApprovedTrade` — the token check in
its constructor means signal-side code cannot forge one. Exits close
risk, so they require only a trade_id.

PaperBroker simulates fills at the limit (entry) / near-mid (exit) so
paper mode exercises the identical order path.
TastytradeBroker maps to the tastyware/tastytrade async SDK.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from godmode0dte.config import ExecutionConfig
from godmode0dte.models import Quote, VerticalSpec
from godmode0dte.monitoring.logging import get_logger
from godmode0dte.risk.governor import ApprovedTrade

log = get_logger("broker")


@dataclass(frozen=True)
class Fill:
    trade_id: str
    price: float                     # net debit (entry) or net credit (exit), per share
    ts: datetime
    partial: bool = False


class Broker(ABC):
    @abstractmethod
    async def equity(self) -> float: ...

    @abstractmethod
    async def open_position(self, approved: ApprovedTrade,
                            long_q: Quote, short_q: Quote) -> Optional[Fill]:
        """Enter the vertical with a laddered limit order. None = unfilled/abandoned."""

    @abstractmethod
    async def close_position(self, trade_id: str, vertical: VerticalSpec,
                             long_q: Quote, short_q: Quote,
                             urgency: str = "normal") -> Optional[Fill]:
        """Exit the vertical. 'urgent' ladders faster and further."""


class PaperBroker(Broker):
    """Deterministic fill simulator sharing the real order path."""

    def __init__(self, cfg: ExecutionConfig, starting_equity: float = 25_000.0) -> None:
        self._cfg = cfg
        self._equity = starting_equity
        self._open: dict[str, ApprovedTrade] = {}
        self._entry_fills: dict[str, float] = {}

    async def equity(self) -> float:
        return self._equity

    def mark_equity(self, delta: float) -> None:
        self._equity += delta

    async def open_position(self, approved: ApprovedTrade,
                            long_q: Quote, short_q: Quote) -> Optional[Fill]:
        natural_mid = long_q.mid - short_q.mid
        natural_ask = long_q.ask - short_q.bid
        # Paper assumption: fill one ladder step above mid (realistic-ish slippage).
        price = round(natural_mid + (natural_ask - natural_mid) * self._cfg.ladder_step_frac, 2)
        if price > approved.vertical.debit * 1.10:
            log.warning("paper_entry_abandoned", trade_id=approved.trade_id, price=price)
            return None
        await asyncio.sleep(self._cfg.ladder_step_wait_sec / 10)   # token latency
        self._open[approved.trade_id] = approved
        self._entry_fills[approved.trade_id] = price
        log.info("paper_entry_filled", trade_id=approved.trade_id, price=price,
                 contracts=approved.vertical.contracts)
        return Fill(approved.trade_id, price, datetime.now(timezone.utc))

    async def close_position(self, trade_id: str, vertical: VerticalSpec,
                             long_q: Quote, short_q: Quote,
                             urgency: str = "normal") -> Optional[Fill]:
        natural_mid = long_q.mid - short_q.mid
        natural_bid = long_q.bid - short_q.ask
        haircut = self._cfg.ladder_step_frac * (2 if urgency == "urgent" else 1)
        price = round(natural_mid - (natural_mid - natural_bid) * haircut, 2)
        approved = self._open.pop(trade_id, None)
        if approved is not None:
            entry = self._entry_fills.pop(trade_id, price)
            self._equity += (price - entry) * approved.vertical.contracts * 100
        log.info("paper_exit_filled", trade_id=trade_id, price=price, urgency=urgency)
        return Fill(trade_id, max(price, 0.0), datetime.now(timezone.utc))


class TastytradeBroker(Broker):
    """Live adapter over the tastyware/tastytrade async SDK.

    Integration map:
      Session(username, password)                     -> auth
      Account.a_get(session)[account_index]           -> account
      account.a_get_balances(session).net_liquidating_value -> equity
      NewOrder(time_in_force=Day, order_type=Limit,
               legs=[opt.build_leg(qty, BUY_TO_OPEN),
                     opt.build_leg(qty, SELL_TO_OPEN)],
               price=-debit)                          -> entry order (debit = negative price)
      account.a_place_order(session, order, dry_run=paper) -> placement
      account.a_get_positions(session)                -> reconcile on boot
    """

    def __init__(self, session, account, cfg: ExecutionConfig) -> None:
        self._session = session
        self._account = account
        self._cfg = cfg
        self._live_orders: dict[str, object] = {}

    async def equity(self) -> float:
        balances = await self._account.a_get_balances(self._session)
        return float(balances.net_liquidating_value)

    async def positions(self) -> list:
        return await self._account.a_get_positions(self._session)

    async def open_position(self, approved: ApprovedTrade,
                            long_q: Quote, short_q: Quote) -> Optional[Fill]:
        from decimal import Decimal
        from tastytrade.instruments import Option
        from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

        v = approved.vertical
        long_opt = await Option.a_get(self._session, v.long_symbol)
        short_opt = await Option.a_get(self._session, v.short_symbol)
        natural_mid = long_q.mid - short_q.mid
        natural_ask = long_q.ask - short_q.bid

        # Absolute worst-fill cap: never above 110% of the approved debit AND
        # never above ladder_cap_pct_of_width x width (keeps RR >= 1.22, spec §6.4).
        price_cap = min(v.debit * 1.10, self._cfg.ladder_cap_pct_of_width * v.width)
        for step in range(self._cfg.ladder_max_steps + 1):
            px = round(natural_mid + (natural_ask - natural_mid)
                       * min(1.0, step * self._cfg.ladder_step_frac), 2)
            px = min(px, price_cap)
            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT,
                legs=[
                    long_opt.build_leg(Decimal(v.contracts), OrderAction.BUY_TO_OPEN),
                    short_opt.build_leg(Decimal(v.contracts), OrderAction.SELL_TO_OPEN),
                ],
                price=Decimal(str(-px)),          # debit orders use negative price
            )
            resp = await self._account.a_place_order(self._session, order, dry_run=False)
            placed = resp.order
            filled = await self._await_fill(placed, self._cfg.ladder_step_wait_sec)
            if filled:
                log.info("entry_filled", trade_id=approved.trade_id, price=px, step=step)
                return Fill(approved.trade_id, px, datetime.now(timezone.utc))
            await self._account.a_delete_order(self._session, placed.id)
        log.warning("entry_abandoned", trade_id=approved.trade_id)
        return None

    async def close_position(self, trade_id: str, vertical: VerticalSpec,
                             long_q: Quote, short_q: Quote,
                             urgency: str = "normal") -> Optional[Fill]:
        """Close the vertical for a credit: SELL_TO_CLOSE long, BUY_TO_CLOSE short.

        Ladders from the mid credit toward the natural (bid-side) credit.
        'urgent' doubles the step size, halves the wait, and adds one final
        step a tick through the natural so breaker/force-flat exits always
        clear. Returns None if still unfilled — the risk task retries.
        """
        from decimal import Decimal
        from tastytrade.instruments import Option
        from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

        long_opt = await Option.a_get(self._session, vertical.long_symbol)
        short_opt = await Option.a_get(self._session, vertical.short_symbol)
        mid_credit = long_q.mid - short_q.mid
        natural_credit = long_q.bid - short_q.ask
        step_frac = self._cfg.ladder_step_frac * (2 if urgency == "urgent" else 1)
        wait = self._cfg.ladder_step_wait_sec / (2 if urgency == "urgent" else 1)
        steps = list(range(self._cfg.ladder_max_steps + 1))

        for step in steps:
            px = mid_credit - (mid_credit - natural_credit) * min(1.0, step * step_frac)
            if urgency == "urgent" and step == steps[-1]:
                px = natural_credit - 0.05          # through the market: get out, now
            px = round(max(px, 0.0), 2)
            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT,
                legs=[
                    long_opt.build_leg(Decimal(vertical.contracts), OrderAction.SELL_TO_CLOSE),
                    short_opt.build_leg(Decimal(vertical.contracts), OrderAction.BUY_TO_CLOSE),
                ],
                price=Decimal(str(px)),             # credit orders use positive price
            )
            resp = await self._account.a_place_order(self._session, order, dry_run=False)
            placed = resp.order
            if await self._await_fill(placed, wait):
                log.info("exit_filled", trade_id=trade_id, price=px, step=step, urgency=urgency)
                return Fill(trade_id, px, datetime.now(timezone.utc))
            try:
                await self._account.a_delete_order(self._session, placed.id)
            except Exception as e:                  # noqa: BLE001 — may already be filled
                log.warning("exit_cancel_failed", trade_id=trade_id, error=str(e))
                if await self._await_fill(placed, 2.0):
                    return Fill(trade_id, px, datetime.now(timezone.utc))
        log.error("exit_ladder_exhausted", trade_id=trade_id, urgency=urgency)
        return None

    async def _await_fill(self, placed_order, wait_sec: float) -> bool:
        from tastytrade.order import OrderStatus
        deadline = asyncio.get_event_loop().time() + wait_sec
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)
            orders = await self._account.a_get_live_orders(self._session)
            for o in orders:
                if o.id == placed_order.id:
                    if o.status == OrderStatus.FILLED:
                        return True
                    if o.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
                        return False
        return False
