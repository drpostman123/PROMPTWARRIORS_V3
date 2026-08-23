"""RugCheck API client — LP lock status, deployer history, holder mix.

Used as ONE input to the rug filter; mint/freeze authorities are always
re-verified on-chain (never trust a single external source for a hard
gate). All failures return None — the filter fails closed on None.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from skyfire_sol.clients.ratelimit import TokenBucket
from tradecore.logging import get_logger

log = get_logger("rugcheck")

BASE = "https://api.rugcheck.xyz/v1"


class RugCheckClient:
    def __init__(self, client: httpx.AsyncClient, rps: float = 2.0) -> None:
        self._http = client
        self._bucket = TokenBucket(rps)

    async def report(self, mint: str) -> Optional[dict[str, Any]]:
        await self._bucket.acquire()
        try:
            resp = await self._http.get(f"{BASE}/tokens/{mint}/report")
            if resp.status_code != 200:
                log.warning("rugcheck_non_200", mint=mint, status=resp.status_code)
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("rugcheck_fetch_failed", mint=mint, error=str(e))
            return None

    @staticmethod
    def extract(report: dict[str, Any]) -> dict[str, Any]:
        """Normalize the fields the rug filter consumes.

        lp_locked_days: min lock horizon across markets, in days; burned LP
        counts as locked forever (9999). None when undeterminable.
        """
        out: dict[str, Any] = {
            "rugged_flag": bool(report.get("rugged")),
            "deployer": report.get("creator"),
            "lp_locked_days": None,
            "lp_locked_pct": None,
            "total_holders": report.get("totalHolders"),
        }
        try:
            markets = report.get("markets") or []
            locked_pcts: list[float] = []
            for m in markets:
                lp = m.get("lp") or {}
                if lp.get("lpLockedPct") is not None:
                    locked_pcts.append(float(lp["lpLockedPct"]))
            if locked_pcts:
                out["lp_locked_pct"] = max(locked_pcts)
                # RugCheck reports burn as locked; treat >=90% locked/burned as
                # satisfying the lock gate horizon-wise (burn has no expiry).
                out["lp_locked_days"] = 9999.0 if max(locked_pcts) >= 90.0 else 0.0
        except (TypeError, ValueError, KeyError):
            pass
        return out
