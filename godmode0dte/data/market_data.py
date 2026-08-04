"""Tastytrade market data via DXLinkStreamer (tastyware/tastytrade SDK).

One streamer session feeds:
  - underlying trades  -> BarAggregator (1m bars)
  - underlying + macro quotes -> latest quote cache / MacroCluster
  - option quotes for candidate legs -> quote cache with staleness stamps

The feed layer knows nothing about signals or orders — it only publishes
data into in-memory stores the feature layer reads.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from godmode0dte.config import AppConfig
from godmode0dte.data.macro import MacroCluster
from godmode0dte.features.bars import BarAggregator
from godmode0dte.models import Quote
from godmode0dte.monitoring.logging import get_logger

log = get_logger("market_data")


class MarketDataHub:
    """Owns the DXLink streamer and all live data caches."""

    def __init__(self, cfg: AppConfig, underlying: str) -> None:
        self._cfg = cfg
        self.underlying = underlying
        self.bars = BarAggregator(seconds=cfg.data.bar_seconds)
        self.macro = MacroCluster()
        self.quotes: dict[str, Quote] = {}
        self._option_symbols: set[str] = set()
        self._streamer = None
        self._session = None
        self._tasks: list[asyncio.Task] = []

    # -- lifecycle -----------------------------------------------------

    async def start(self, session) -> None:
        """Start streaming. `session` is a tastytrade.Session."""
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Quote as DXQuote, Trade as DXTrade

        self._session = session
        self._streamer = await DXLinkStreamer(session)
        macro_syms = list(self._cfg.data.macro_symbols.values())
        await self._streamer.subscribe(DXQuote, [self.underlying, *macro_syms])
        await self._streamer.subscribe(DXTrade, [self.underlying])
        self._tasks = [
            asyncio.create_task(self._quote_loop(), name="md-quotes"),
            asyncio.create_task(self._trade_loop(), name="md-trades"),
        ]
        log.info("market_data_started", underlying=self.underlying, macro=macro_syms)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._streamer is not None:
            await self._streamer.close()

    async def watch_options(self, streamer_symbols: list[str]) -> None:
        """Subscribe option legs (DXFeed streamer symbols)."""
        from tastytrade.dxfeed import Quote as DXQuote
        new = [s for s in streamer_symbols if s not in self._option_symbols]
        if new and self._streamer is not None:
            self._option_symbols.update(new)
            await self._streamer.subscribe(DXQuote, new)

    # -- consumers -----------------------------------------------------

    async def _quote_loop(self) -> None:
        from tastytrade.dxfeed import Quote as DXQuote
        reverse_macro = {v: k for k, v in self._cfg.data.macro_symbols.items()}
        async for q in self._streamer.listen(DXQuote):
            now = datetime.now(timezone.utc)
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            quote = Quote(
                symbol=q.event_symbol, bid=bid, ask=ask,
                bid_size=float(q.bid_size or 0), ask_size=float(q.ask_size or 0), ts=now,
            )
            self.quotes[q.event_symbol] = quote
            if q.event_symbol in reverse_macro and bid > 0:
                self.macro.update(reverse_macro[q.event_symbol], quote.mid, now)

    async def _trade_loop(self) -> None:
        from tastytrade.dxfeed import Trade as DXTrade
        async for t in self._streamer.listen(DXTrade):
            if t.event_symbol == self.underlying and t.price:
                self.bars.add(float(t.price), float(t.size or 0), datetime.now(timezone.utc))

    # -- freshness -----------------------------------------------------

    def quote_age_sec(self, symbol: str) -> Optional[float]:
        q = self.quotes.get(symbol)
        if q is None:
            return None
        return (datetime.now(timezone.utc) - q.ts).total_seconds()

    def underlying_price(self) -> Optional[float]:
        q = self.quotes.get(self.underlying)
        return q.mid if q and q.bid > 0 else None
