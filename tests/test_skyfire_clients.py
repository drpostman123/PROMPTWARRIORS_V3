"""HTTP clients against httpx.MockTransport: Jupiter quote parsing +
slippage rejection + sell-test, DexScreener normalization, RugCheck
extraction, config ceiling enforcement."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from skyfire_sol.clients.dexscreener import DexScreenerClient
from skyfire_sol.clients.jupiter import JupiterClient, SlippageCapExceeded
from skyfire_sol.clients.rugcheck import RugCheckClient
from skyfire_sol.config import (
    CoreConfig,
    HlConfig,
    MemeConfig,
    PerpsConfig,
    RiskConfig,
    SleevesConfig,
    USDC_MINT,
    WSOL_MINT,
)


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def jup_quote_body(out_amount=92_000_000, impact="0.001"):
    return {"inputMint": WSOL_MINT, "inAmount": "1000000000",
            "outputMint": USDC_MINT, "outAmount": str(out_amount),
            "priceImpactPct": impact, "routePlan": []}


async def test_jupiter_quote_parses_and_normalizes_impact():
    async def handler(request):
        assert request.url.path == "/swap/v1/quote"
        return httpx.Response(200, json=jup_quote_body(impact="0.0123"))
    jup = JupiterClient(make_client(handler), "https://lite-api.jup.ag", rps=1000)
    q = await jup.quote(WSOL_MINT, USDC_MINT, 10**9, max_price_impact_pct=3.0)
    assert q.out_amount == 92_000_000
    assert q.price_impact_pct == pytest.approx(1.23)     # fraction -> percent
    assert json.loads(q.route_json)["outAmount"] == "92000000"


async def test_jupiter_rejects_over_cap_before_returning():
    async def handler(request):
        return httpx.Response(200, json=jup_quote_body(impact="0.04"))  # 4%
    jup = JupiterClient(make_client(handler), "https://x", rps=1000)
    with pytest.raises(SlippageCapExceeded):
        await jup.quote(WSOL_MINT, USDC_MINT, 10**9, max_price_impact_pct=3.0)


async def test_jupiter_sell_test_no_route_is_honeypot():
    async def no_route(request):
        return httpx.Response(400, json={"error": "COULD_NOT_FIND_ANY_ROUTE"})
    jup = JupiterClient(make_client(no_route), "https://x", rps=1000)
    assert await jup.sell_test("SomeMint", 1000) is False

    async def has_route(request):
        return httpx.Response(200, json=jup_quote_body())
    jup2 = JupiterClient(make_client(has_route), "https://x", rps=1000)
    assert await jup2.sell_test("SomeMint", 1000) is True


async def test_dexscreener_picks_most_liquid_pair():
    pairs = [
        {"baseToken": {"address": "M", "symbol": "T"}, "pairAddress": "p1",
         "priceUsd": "0.5", "liquidity": {"usd": 10_000},
         "volume": {"h1": 1, "h6": 6, "h24": 24}, "priceChange": {"h1": 2.0},
         "pairCreatedAt": 1700000000000},
        {"baseToken": {"address": "M", "symbol": "T"}, "pairAddress": "p2",
         "priceUsd": "0.51", "liquidity": {"usd": 900_000},
         "volume": {"h1": 10, "h6": 60, "h24": 240}, "priceChange": {"h1": 3.0},
         "pairCreatedAt": 1700000000000},
    ]

    async def handler(request):
        return httpx.Response(200, json=pairs)
    dex = DexScreenerClient(make_client(handler), rps=1000)
    stats = await dex.token_stats("M")
    assert stats["pair_address"] == "p2"
    assert stats["liquidity_usd"] == 900_000
    assert stats["pair_created_at"].year >= 2023


async def test_rugcheck_extract_lp_lock_and_deployer():
    report = {"rugged": False, "creator": "Dep1", "totalHolders": 1234,
              "markets": [{"lp": {"lpLockedPct": 99.5}}]}
    got = RugCheckClient.extract(report)
    assert got["lp_locked_days"] == 9999.0           # burn/lock >= 90%
    assert got["deployer"] == "Dep1"
    weak = RugCheckClient.extract(
        {"rugged": False, "markets": [{"lp": {"lpLockedPct": 20.0}}]})
    assert weak["lp_locked_days"] == 0.0
    assert RugCheckClient.extract({"markets": []})["lp_locked_days"] is None


def test_config_ceilings_reject_loosening():
    with pytest.raises(ValidationError):
        MemeConfig(slippage_cap_pct=3.5)
    with pytest.raises(ValidationError):
        MemeConfig(max_positions=6)
    with pytest.raises(ValidationError):
        MemeConfig(trail_from_peak_pct=50.0)
    with pytest.raises(ValidationError):
        MemeConfig(time_stop_hours=72.0)
    with pytest.raises(ValidationError):
        CoreConfig(slippage_cap_pct=1.0)
    with pytest.raises(ValidationError):
        RiskConfig(hwm_breaker_pct=70.0)
    with pytest.raises(ValidationError):
        RiskConfig(daily_pause_pct=25.0)             # ceiling is 20
    with pytest.raises(ValidationError):
        RiskConfig(soft_tier_pct=50.0)               # ceiling is 45
    with pytest.raises(ValidationError):
        RiskConfig(corr_risk_on_pct=60.0)
    with pytest.raises(ValidationError):
        RiskConfig(probation_size_frac=0.8)
    with pytest.raises(ValidationError):
        PerpsConfig(max_leverage=4.0)
    with pytest.raises(ValidationError):
        HlConfig(max_leverage=4.0)
    with pytest.raises(ValidationError):
        HlConfig(top_n=6)                            # ceiling is 5 positions
    with pytest.raises(ValidationError):
        HlConfig(slippage_cap_pct=5.0)
    with pytest.raises(ValidationError):
        HlConfig(trail_from_peak_pct=60.0)
    # tightening is welcome — and the generous defaults sit at the ceilingward end
    assert RiskConfig().daily_pause_pct == 15.0
    assert RiskConfig().soft_tier_pct == 40.0
    tighter = RiskConfig(hwm_breaker_pct=40.0, soft_tier_pct=25.0,
                         soft_tier_clear_pct=15.0, daily_pause_pct=10.0)
    assert tighter.hwm_breaker_pct == 40.0
    assert MemeConfig(slippage_cap_pct=1.0).slippage_cap_pct == 1.0


def test_config_cross_field_coherence():
    with pytest.raises(ValidationError):
        RiskConfig(soft_tier_clear_pct=45.0)         # hysteresis must be below tier
    with pytest.raises(ValidationError):
        SleevesConfig(boot_allocations={              # sums to 110
            "MEME_ROTATION": 35.0, "CORE_HOLD": 25.0, "YIELD": 20.0,
            "PERPS": 10.0, "HL_ROTATION": 20.0})
    with pytest.raises(ValidationError):
        SleevesConfig(boot_allocations={              # missing HL_ROTATION key
            "MEME_ROTATION": 40.0, "CORE_HOLD": 25.0, "YIELD": 20.0,
            "PERPS": 15.0})
    with pytest.raises(ValidationError):
        CoreConfig(weights={"SOL": 100.0})           # wrong asset set
