"""Birdeye API client — optional enrichment (holder counts/deltas).
Requires BIRDEYE_API_KEY; without one this client degrades to no-ops and
the rug filter's holder gates rely on RugCheck + on-chain data."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from skyfire_sol.clients.ratelimit import TokenBucket
from tradecore.logging import get_logger

log = get_logger("birdeye")

BASE = "https://public-api.birdeye.so"


class BirdeyeClient:
    def __init__(self, client: httpx.AsyncClient, api_key: Optional[str],
                 rps: float = 0.8) -> None:
        self._http = client
        self._key = api_key
        self._bucket = TokenBucket(rps)

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    async def _get(self, path: str, **params: Any) -> Optional[dict]:
        if not self._key:
            return None
        await self._bucket.acquire()
        try:
            resp = await self._http.get(
                f"{BASE}{path}", params=params,
                headers={"X-API-KEY": self._key, "x-chain": "solana"})
            if resp.status_code != 200:
                log.warning("birdeye_non_200", path=path, status=resp.status_code)
                return None
            body = resp.json()
            return body.get("data") if body.get("success") else None
        except (httpx.HTTPError, ValueError) as e:
            log.warning("birdeye_fetch_failed", path=path, error=str(e))
            return None

    async def holder_count(self, mint: str) -> Optional[int]:
        data = await self._get("/defi/token_overview", address=mint)
        if data and data.get("holder") is not None:
            try:
                return int(data["holder"])
            except (TypeError, ValueError):
                return None
        return None
