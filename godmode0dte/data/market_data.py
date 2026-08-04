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
from datetime import datetime, time as dtime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from godmode0dte.models import Bar

from godmode0dte.config import AppConfig
from godmode0dte.data.macro import MacroCluster
from godmode0dte.features.bars import BarAggregator
from godmode0dte.features.orderbook import BookPulse
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
        self.deltas: dict[str, float] = {}          # streamer symbol -> delta
        self.open_interest: dict[str, int] = {}     # streamer symbol -> OI
        self.book = BookPulse(ewma_alpha=cfg.signal.book_ewma_alpha,
                              thin_frac=cfg.signal.book_thin_frac)
        self._option_symbols: set[str] = set()
        self._streamer = None
        self._session = None
        self._tasks: list[asyncio.Task] = []
        self._tz = ZoneInfo(cfg.timezone)
        self._stopped = False
        self._generation = 0
        self._restart_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------

    async def start(self, session) -> None:
        """Start streaming. `session` is a tastytrade.Session."""
        self._session = session
        await self._connect()
        self._tasks = [
            asyncio.create_task(self._supervised(self._quote_loop), name="md-quotes"),
            asyncio.create_task(self._supervised(self._trade_loop), name="md-trades"),
            asyncio.create_task(self._supervised(self._greeks_loop), name="md-greeks"),
            asyncio.create_task(self._supervised(self._summary_loop), name="md-summary"),
        ]
        log.info("market_data_started", underlying=self.underlying)

    async def _connect(self) -> None:
        """(Re)build the streamer and re-establish every subscription."""
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Greeks as DXGreeks, Quote as DXQuote, Summary as DXSummary
        from tastytrade.dxfeed import Trade as DXTrade

        self._streamer = await DXLinkStreamer(self._session)
        macro_syms = list(self._cfg.data.macro_symbols.values())
        await self._streamer.subscribe(DXQuote, [self.underlying, *macro_syms])
        await self._streamer.subscribe(DXTrade, [self.underlying])
        if self._option_symbols:
            syms = sorted(self._option_symbols)
            await self._streamer.subscribe(DXQuote, syms)
            await self._streamer.subscribe(DXGreeks, syms)
            await self._streamer.subscribe(DXSummary, syms)

    async def _supervised(self, loop_fn) -> None:
        """A listen loop dies silently when the websocket drops (audit B8):
        supervise each one — on any exit, coordinate a single reconnect
        (generation-guarded so four loops don't race four rebuilds), then
        resume listening on the new streamer."""
        backoff = 2.0
        while not self._stopped:
            gen = self._generation
            try:
                await loop_fn()
                if self._stopped:
                    return
                raise ConnectionError("listen loop ended (stream closed)")
            except asyncio.CancelledError:
                return
            except Exception as e:                  # noqa: BLE001 — reconnect on anything
                log.error("stream_loop_died", loop=loop_fn.__name__, error=str(e))
                async with self._restart_lock:
                    if self._generation == gen:     # first loop in rebuilds for all
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30.0)
                        try:
                            await self._connect()
                            self._generation += 1
                            backoff = 2.0
                            log.info("stream_reconnected", generation=self._generation)
                        except Exception as ce:     # noqa: BLE001
                            log.error("stream_reconnect_failed", error=str(ce))

    async def stop(self) -> None:
        self._stopped = True
        for t in self._tasks:
            t.cancel()
        if self._streamer is not None:
            await self._streamer.close()

    async def watch_options(self, streamer_symbols: list[str]) -> None:
        """Subscribe option legs (DXFeed streamer symbols): quotes, greeks, OI.

        Symbols are remembered so a reconnect re-subscribes everything."""
        from tastytrade.dxfeed import Greeks as DXGreeks, Quote as DXQuote, Summary as DXSummary
        new = [s for s in streamer_symbols if s not in self._option_symbols]
        if new and self._streamer is not None:
            self._option_symbols.update(new)
            await self._streamer.subscribe(DXQuote, new)
            await self._streamer.subscribe(DXGreeks, new)
            await self._streamer.subscribe(DXSummary, new)

    def session_bars(self) -> list[Bar]:
        """Bars from today's 09:30 ET open only — premarket prints captured
        during boot must never feed VWAP/indicators/rel-volume (audit U2)."""
        now_local = datetime.now(self._tz)
        open_utc = datetime.combine(now_local.date(), dtime(9, 30),
                                    tzinfo=self._tz).astimezone(timezone.utc)
        return [b for b in self.bars.bars if b.ts >= open_utc]

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
            if q.event_symbol == self.underlying:
                self.book.update(quote.bid_size, quote.ask_size, now)
            if q.event_symbol in reverse_macro and bid > 0:
                self.macro.update(reverse_macro[q.event_symbol], quote.mid, now)

    async def _trade_loop(self) -> None:
        from tastytrade.dxfeed import Trade as DXTrade
        async for t in self._streamer.listen(DXTrade):
            if t.event_symbol == self.underlying and t.price:
                self.bars.add(float(t.price), float(t.size or 0), datetime.now(timezone.utc))

    async def _greeks_loop(self) -> None:
        from tastytrade.dxfeed import Greeks as DXGreeks
        async for g in self._streamer.listen(DXGreeks):
            if g.delta is not None:
                self.deltas[g.event_symbol] = float(g.delta)

    async def _summary_loop(self) -> None:
        from tastytrade.dxfeed import Summary as DXSummary
        async for s in self._streamer.listen(DXSummary):
            if s.open_interest is not None:
                self.open_interest[s.event_symbol] = int(s.open_interest)

    # -- freshness -----------------------------------------------------

    def quote_age_sec(self, symbol: str) -> Optional[float]:
        q = self.quotes.get(symbol)
        if q is None:
            return None
        return (datetime.now(timezone.utc) - q.ts).total_seconds()

    def underlying_price(self) -> Optional[float]:
        q = self.quotes.get(self.underlying)
        return q.mid if q and q.bid > 0 else None
