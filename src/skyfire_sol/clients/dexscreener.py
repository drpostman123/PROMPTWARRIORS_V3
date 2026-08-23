"""DexScreener API client — discovery + market stats. No key required;
documented ~300 rpm, budgeted well under that."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from skyfire_sol.clients.ratelimit import TokenBucket
from tradecore.logging import get_logger

log = get_logger("dexscreener")

BASE = "https://api.dexscreener.com"


def _pair_to_stats(p: dict[str, Any]) -> dict[str, Any]:
    liq = (p.get("liquidity") or {}).get("usd")
    vol = p.get("volume") or {}
    created = p.get("pairCreatedAt")
    return {
        "mint": (p.get("baseToken") or {}).get("address"),
        "symbol": (p.get("baseToken") or {}).get("symbol") or "?",
        "pair_address": p.get("pairAddress"),
        "price_usd": float(p["priceUsd"]) if p.get("priceUsd") else None,
        "liquidity_usd": float(liq) if liq is not None else None,
        "volume_1h_usd": float(vol["h1"]) if vol.get("h1") is not None else None,
        "volume_6h_usd": float(vol["h6"]) if vol.get("h6") is not None else None,
        "volume_24h_usd": float(vol["h24"]) if vol.get("h24") is not None else None,
        "price_change_1h_pct": (p.get("priceChange") or {}).get("h1"),
        "pair_created_at": (
            datetime.fromtimestamp(created / 1000, tz=timezone.utc)
            if created else None),
    }


class DexScreenerClient:
    def __init__(self, client: httpx.AsyncClient, rps: float = 4.0) -> None:
        self._http = client
        self._bucket = TokenBucket(rps)

    async def _get(self, path: str, **params: Any) -> Any:
        await self._bucket.acquire()
        resp = await self._http.get(f"{BASE}{path}", params=params or None)
        resp.raise_for_status()
        return resp.json()

    async def latest_solana_profiles(self) -> list[str]:
        """Freshly-profiled token addresses on Solana (discovery source)."""
        try:
            body = await self._get("/token-profiles/latest/v1")
            return [t["tokenAddress"] for t in body
                    if t.get("chainId") == "solana" and t.get("tokenAddress")]
        except (httpx.HTTPError, KeyError, TypeError) as e:
            log.warning("profiles_fetch_failed", error=str(e))
            return []

    async def boosted_solana_tokens(self) -> list[str]:
        """Trending/boosted tokens on Solana (second discovery source)."""
        try:
            body = await self._get("/token-boosts/top/v1")
            return [t["tokenAddress"] for t in body
                    if t.get("chainId") == "solana" and t.get("tokenAddress")]
        except (httpx.HTTPError, KeyError, TypeError) as e:
            log.warning("boosts_fetch_failed", error=str(e))
            return []

    async def token_stats(self, mint: str) -> Optional[dict[str, Any]]:
        """Best (most liquid) Solana pair stats for a token, normalized."""
        try:
            body = await self._get(f"/tokens/v1/solana/{mint}")
            pairs = [p for p in (body or [])
                     if (p.get("baseToken") or {}).get("address") == mint]
            if not pairs:
                return None
            best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0.0)
            return _pair_to_stats(best)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            log.warning("token_stats_fetch_failed", mint=mint, error=str(e))
            return None

    async def price_usd(self, mint: str) -> Optional[float]:
        stats = await self.token_stats(mint)
        return stats["price_usd"] if stats else None
