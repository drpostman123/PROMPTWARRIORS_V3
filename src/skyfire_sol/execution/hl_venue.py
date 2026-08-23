"""Hyperliquid venue wrapper — the ONLY module that talks to the HL
exchange endpoint (pinned by the source-scan test, like the Solana
executor's submit path and the Drift venue).

Uses the official hyperliquid-python-sdk (sync; calls are wrapped in
asyncio.to_thread). Reads are public /info queries; orders are IOC
market orders with the SDK's slippage bound, signed locally by the
age-encrypted EVM wallet. All imports are guarded so the rest of the
system runs without the SDK installed.

Verified against api.hyperliquid.xyz (2026-08): metaAndAssetCtxs returns
232 perp markets with dayNtlVlm/prevDayPx/markPx/funding/openInterest.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from skyfire_sol.models import HlMarketStat
from tradecore.logging import get_logger

log = get_logger("hl_venue")


class HlUnavailable(RuntimeError):
    pass


class HlVenue:
    def __init__(self, base_url: str, account, address: str) -> None:
        self._base = base_url
        self._account = account
        self._address = address
        self._info = None
        self._exchange = None
        self._sz_decimals: dict[str, int] = {}

    async def connect(self) -> None:
        try:
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
        except ImportError as e:
            raise HlUnavailable("hyperliquid-python-sdk not installed") from e

        def _mk():
            info = Info(self._base, skip_ws=True)
            exchange = Exchange(self._account, self._base,
                                account_address=self._address)
            return info, exchange

        self._info, self._exchange = await asyncio.to_thread(_mk)
        meta = await asyncio.to_thread(self._info.meta)
        for asset in meta["universe"]:
            self._sz_decimals[asset["name"]] = int(asset["szDecimals"])
        log.info("hl_connected", address=self._address,
                 markets=len(self._sz_decimals))

    @property
    def connected(self) -> bool:
        return self._exchange is not None

    # -- reads ------------------------------------------------------------

    async def market_stats(self) -> list[HlMarketStat]:
        if self._info is None:
            return []
        meta, ctxs = await asyncio.to_thread(self._info.meta_and_asset_ctxs)
        out: list[HlMarketStat] = []
        for asset, ctx in zip(meta["universe"], ctxs):
            try:
                mark = float(ctx.get("markPx") or 0)
                prev = float(ctx.get("prevDayPx") or 0)
                out.append(HlMarketStat(
                    coin=asset["name"], mark_px=mark,
                    day_volume_usd=float(ctx.get("dayNtlVlm") or 0),
                    ret_24h_pct=(mark / prev - 1) * 100.0 if prev > 0 else 0.0,
                    funding_pct_hr=float(ctx.get("funding") or 0) * 100.0,
                    open_interest_usd=float(ctx.get("openInterest") or 0) * mark,
                    max_leverage=int(asset.get("maxLeverage") or 1)))
            except (TypeError, ValueError):
                continue
        return out

    async def equity_usd(self) -> float:
        if self._info is None:
            return 0.0
        try:
            state = await asyncio.to_thread(self._info.user_state, self._address)
            return float(state["marginSummary"]["accountValue"])
        except Exception as e:                       # noqa: BLE001 — read is advisory
            log.warning("hl_equity_read_failed", error=str(e))
            return 0.0

    async def positions(self) -> dict[str, dict]:
        """coin -> {szi, entry_px, notional_usd, upnl_usd} for open positions."""
        if self._info is None:
            return {}
        try:
            state = await asyncio.to_thread(self._info.user_state, self._address)
            out: dict[str, dict] = {}
            for ap in state.get("assetPositions", []):
                p = ap.get("position") or {}
                szi = float(p.get("szi") or 0)
                if szi == 0:
                    continue
                out[p["coin"]] = {
                    "szi": szi,
                    "entry_px": float(p.get("entryPx") or 0),
                    "notional_usd": abs(float(p.get("positionValue") or 0)),
                    "upnl_usd": float(p.get("unrealizedPnl") or 0)}
            return out
        except Exception as e:                       # noqa: BLE001
            log.warning("hl_positions_read_failed", error=str(e))
            return {}

    # -- orders (the single HL submit path) -------------------------------

    def _round_sz(self, coin: str, sz: float) -> float:
        return round(sz, self._sz_decimals.get(coin, 2))

    async def market_open_long(self, coin: str, notional_usd: float,
                               mark_px: float, slippage_pct: float) -> Optional[dict]:
        if self._exchange is None or mark_px <= 0:
            return None
        sz = self._round_sz(coin, notional_usd / mark_px)
        if sz <= 0:
            return None
        result = await asyncio.to_thread(
            self._exchange.market_open, coin, True, sz, None, slippage_pct / 100.0)
        return self._check(result, f"open {coin} {sz}")

    async def market_close(self, coin: str, slippage_pct: float) -> Optional[dict]:
        if self._exchange is None:
            return None
        result = await asyncio.to_thread(
            self._exchange.market_close, coin, None, None, slippage_pct / 100.0)
        return self._check(result, f"close {coin}")

    @staticmethod
    def _check(result: dict, what: str) -> Optional[dict]:
        try:
            if result.get("status") != "ok":
                log.error("hl_order_rejected", what=what, result=str(result)[:300])
                return None
            statuses = result["response"]["data"]["statuses"]
            for st in statuses:
                if "error" in st:
                    log.error("hl_order_error", what=what, error=st["error"])
                    return None
            log.info("hl_order_filled", what=what, statuses=str(statuses)[:300])
            return result
        except (KeyError, TypeError, AttributeError):
            log.error("hl_order_unparseable", what=what, result=str(result)[:300])
            return None
