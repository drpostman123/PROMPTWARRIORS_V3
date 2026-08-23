"""Tastytrade market data via DXLinkStreamer (tastyware/tastytrade SDK).

One streamer session feeds:
  - underlying trades  -> BarAggregator (1m bars)
  - underlying + macro quotes -> latest quote cache / MacroCluster
  - option quotes for candidate legs -> quote cache with staleness stamps
  - historical 5m candles (prior session + premarket) -> indicator warmup,
    so ATR14/EMA/regime are REAL at 09:35 instead of arming ~10:45 — the
    first 15 minutes after the open are the window this system hunts.

The feed layer knows nothing about signals or orders — it only publishes
data into in-memory stores the feature layer reads.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from godmode0dte.models import Bar


def prior_session_date(today: date) -> date:
    """Previous weekday (holiday-naive: on a holiday the warmup comes back
    empty and the system degrades to the session-only arming path)."""
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def in_warmup_window(ts: datetime, today: date, tz: ZoneInfo) -> bool:
    """Candles eligible for warmup: prior-session RTH (09:30-16:00 ET) and
    TODAY'S premarket (04:00-09:30 ET). Overnight/weekend candles are
    excluded — their vol regime would drag ATR toward fiction."""
    local = ts.astimezone(tz)
    if local.date() == today:
        return dtime(4, 0) <= local.time() < dtime(9, 30)
    return dtime(9, 30) <= local.time() < dtime(16, 0)

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
        self.warm_bars_5m: list[Bar] = []       # prior-session RTH + today's premarket
        self.warmup_done = asyncio.Event()

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
            asyncio.create_task(self._warmup_candles(), name="md-warmup"),
        ]
        log.info("market_data_started", underlying=self.underlying)

    async def _warmup_candles(self) -> None:
        """One-shot backfill: prior-session + premarket 5m candles via DXLink,
        so every ATR/EMA-based feature is calibrated at the opening bell.

        Fully guarded — on any failure the warmup list stays empty and the
        system degrades to session-only arming (~10:45), never crashes."""
        try:
            from tastytrade.dxfeed import Candle as DXCandle
            today = datetime.now(self._tz).date()
            start = datetime.combine(prior_session_date(today), dtime(9, 30), tzinfo=self._tz)
            session_open_utc = datetime.combine(today, dtime(9, 30),
                                                tzinfo=self._tz).astimezone(timezone.utc)
            await self._streamer.subscribe_candle([self.underlying], interval="5m",
                                                  start_time=start, extended_trading=True)
            collected: dict[datetime, Bar] = {}
            deadline = asyncio.get_event_loop().time() + 60.0
            async for c in self._streamer.listen(DXCandle):
                raw_ts = int(getattr(c, "time", 0) or 0)
                ts = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc)
                if ts >= session_open_utc:
                    break                            # caught up to today's session
                if in_warmup_window(ts, today, self._tz) and c.close:
                    collected[ts] = Bar(ts=ts, open=float(c.open), high=float(c.high),
                                        low=float(c.low), close=float(c.close),
                                        volume=float(c.volume or 0))
                if asyncio.get_event_loop().time() > deadline:
                    break
            self.warm_bars_5m = [collected[k] for k in sorted(collected)]
            log.info("warmup_candles_loaded", bars=len(self.warm_bars_5m))
            try:
                await self._streamer.unsubscribe_candle(self.underlying, interval="5m")
            except Exception:                        # noqa: BLE001 — cosmetic
                pass
        except Exception as e:                       # noqa: BLE001 — degrade, never die
            log.warning("warmup_candles_failed", error=str(e))
        finally:
            self.warmup_done.set()

    async def _connect(self) -> None:
        """(Re)build the streamer and re-establish every subscription."""
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Greeks as DXGreeks, Quote as DXQuote, Summary as DXSummary
        from tastytrade.dxfeed import Trade as DXTrade

        old, self._streamer = self._streamer, None
        if old is not None:
            try:
                await old.close()                    # audit R3 #6d: no socket leak per reconnect
            except Exception:                        # noqa: BLE001
                pass
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
