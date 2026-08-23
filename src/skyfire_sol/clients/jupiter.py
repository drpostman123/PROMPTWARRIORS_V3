"""Jupiter swap API client.

Probed 2026-08: the spec's quote-api.jup.ag/v6 no longer answers; the
same schema now lives at {lite-api,api}.jup.ag/swap/v1 (lite = free
tier, api = keyed). Base URL and key are config/env driven.

The slippage cap is asserted HERE as well as in the SafetyGate: quote()
raises SlippageCapExceeded rather than return a quote over the cap, so
no caller can even see a non-compliant route. sell_test() is the
honeypot probe: a token with no route back to SOL/USDC cannot be sold.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import httpx

from skyfire_sol.clients.ratelimit import TokenBucket
from skyfire_sol.config import USDC_MINT, WSOL_MINT
from skyfire_sol.models import JupQuote
from tradecore.logging import get_logger

log = get_logger("jupiter")


class SlippageCapExceeded(RuntimeError):
    def __init__(self, price_impact_pct: float, cap_pct: float) -> None:
        super().__init__(f"price impact {price_impact_pct:.3f}% > cap {cap_pct:.3f}%")
        self.price_impact_pct = price_impact_pct
        self.cap_pct = cap_pct


class NoRouteError(RuntimeError):
    pass


class JupiterClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str,
                 api_key: Optional[str] = None, rps: float = 1.0) -> None:
        self._http = client
        self._base = base_url.rstrip("/")
        self._headers = {"x-api-key": api_key} if api_key else {}
        self._bucket = TokenBucket(rps)

    async def quote(self, input_mint: str, output_mint: str, amount_raw: int,
                    max_price_impact_pct: float) -> JupQuote:
        """Quote with the slippage cap enforced. slippageBps for the swap tx is
        set to the same cap so the on-chain check matches the off-chain one."""
        await self._bucket.acquire()
        slippage_bps = int(max_price_impact_pct * 100)
        resp = await self._http.get(f"{self._base}/swap/v1/quote", params={
            "inputMint": input_mint, "outputMint": output_mint,
            "amount": str(amount_raw), "slippageBps": slippage_bps,
            "restrictIntermediateTokens": "true",
        }, headers=self._headers)
        if resp.status_code == 400 and b"ROUTE" in resp.content.upper():
            raise NoRouteError(f"{input_mint} -> {output_mint}")
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise NoRouteError(str(body["error"]))
        # priceImpactPct is a fraction ("0.0123" = 1.23%) in swap/v1 — normalize to %.
        impact = float(body.get("priceImpactPct") or 0.0) * 100.0
        if impact > max_price_impact_pct:
            raise SlippageCapExceeded(impact, max_price_impact_pct)
        return JupQuote(
            input_mint=input_mint, output_mint=output_mint,
            in_amount=int(body["inAmount"]), out_amount=int(body["outAmount"]),
            price_impact_pct=impact, slippage_bps=slippage_bps,
            route_json=json.dumps(body), ts=datetime.now(timezone.utc))

    async def swap_transaction(self, quote: JupQuote, user_pubkey: str,
                               priority_fee_lamports: Optional[int] = None) -> str:
        """Build the serialized (unsigned) swap transaction, base64."""
        await self._bucket.acquire()
        payload: dict = {
            "quoteResponse": json.loads(quote.route_json),
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
        }
        if priority_fee_lamports is not None:
            payload["prioritizationFeeLamports"] = priority_fee_lamports
        else:
            payload["prioritizationFeeLamports"] = "auto"
        resp = await self._http.post(f"{self._base}/swap/v1/swap",
                                     json=payload, headers=self._headers)
        resp.raise_for_status()
        return resp.json()["swapTransaction"]

    async def sell_test(self, mint: str, amount_raw: int) -> bool:
        """Honeypot probe: can this token route back to SOL or USDC?
        True = a sell route exists. Failure to route to BOTH = honeypot."""
        for out in (WSOL_MINT, USDC_MINT):
            try:
                await self._bucket.acquire()
                resp = await self._http.get(f"{self._base}/swap/v1/quote", params={
                    "inputMint": mint, "outputMint": out,
                    "amount": str(amount_raw), "slippageBps": 500,
                }, headers=self._headers)
                if resp.status_code == 200 and "outAmount" in resp.json():
                    return True
            except httpx.HTTPError as e:
                log.warning("sell_test_http_error", mint=mint, error=str(e))
        return False

    async def price_usd_per_token(self, mint: str, decimals: int,
                                  probe_tokens: float = 1.0) -> Optional[float]:
        """USD mark for one whole token via a small USDC quote (fallback when
        DexScreener has no price). None if unroutable."""
        probe_raw = max(1, int(probe_tokens * 10 ** decimals))
        try:
            q = await self.quote(mint, USDC_MINT, probe_raw, max_price_impact_pct=50.0)
            return (q.out_amount / 1e6) / probe_tokens
        except (NoRouteError, SlippageCapExceeded, httpx.HTTPError):
            return None
