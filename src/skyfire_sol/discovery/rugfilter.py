"""Rug/honeypot hard gate. ALL gates must pass; a missing datum FAILS
its gate (fail-closed — an unverifiable token is an untradeable token).

Ordered so the cheapest/highest-kill-rate checks run first; the verdict
carries the first failure plus the full per-check trace for the phantom
log. Pure predicates over TokenFacts — no I/O in this module; the
scanner assembles TokenFacts from Helius/RugCheck/DexScreener/Jupiter.

These gates are the spec's non-negotiables: they are what stops the bot
from buying tokens that cannot be sold.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from skyfire_sol.config import DiscoveryConfig
from skyfire_sol.models import RugCheckResult, RugVerdict, TokenFacts

Gate = tuple[str, Callable[[TokenFacts, DiscoveryConfig], tuple[Optional[bool], str]]]


def _g_age(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    if f.pair_created_at is None:
        return None, "pair age unknown"
    age_min = (datetime.now(timezone.utc) - f.pair_created_at).total_seconds() / 60
    return age_min > cfg.min_token_age_minutes, f"age {age_min:.0f}m"


def _g_liquidity(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    if f.liquidity_usd is None:
        return None, "liquidity unknown"
    return f.liquidity_usd > cfg.min_liquidity_usd, f"liq ${f.liquidity_usd:,.0f}"


def _g_mint_auth(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    return f.mint_authority_revoked, "mint authority"


def _g_freeze_auth(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    return f.freeze_authority_revoked, "freeze authority"


def _g_lp_lock(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    if f.lp_locked_or_burned_days is None:
        return None, "LP lock unknown"
    return (f.lp_locked_or_burned_days >= cfg.min_lp_lock_days,
            f"LP locked {f.lp_locked_or_burned_days:.0f}d")


def _g_top10(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    if f.top10_holder_pct_ex_lp is None:
        return None, "holder concentration unknown"
    return (f.top10_holder_pct_ex_lp < cfg.max_top10_holder_pct,
            f"top10 {f.top10_holder_pct_ex_lp:.1f}%")


def _g_sell_route(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    return f.sell_route_exists, "jupiter sell-test"


def _g_deployer(f: TokenFacts, cfg: DiscoveryConfig) -> tuple[Optional[bool], str]:
    if f.deployer_prior_rugs is None:
        return None, "deployer history unknown"
    return f.deployer_prior_rugs == 0, f"deployer prior rugs {f.deployer_prior_rugs}"


# Order: cheap structural kills first, network-verified facts after.
GATES: list[Gate] = [
    ("age_over_1h", _g_age),
    ("liquidity_floor", _g_liquidity),
    ("mint_authority_revoked", _g_mint_auth),
    ("freeze_authority_revoked", _g_freeze_auth),
    ("lp_locked_or_burned", _g_lp_lock),
    ("top10_concentration", _g_top10),
    ("sell_route_exists", _g_sell_route),
    ("deployer_history", _g_deployer),
]


def evaluate(facts: TokenFacts, cfg: DiscoveryConfig) -> RugVerdict:
    checks: list[RugCheckResult] = []
    first_failure: Optional[str] = None
    for name, fn in GATES:
        ok, detail = fn(facts, cfg)
        passed = bool(ok)  # None (unknown) fails closed
        if ok is None:
            detail = f"{detail} -> FAIL (unknown data fails closed)"
        checks.append(RugCheckResult(gate=name, passed=passed, detail=detail))
        if not passed and first_failure is None:
            first_failure = name
    return RugVerdict(
        mint=facts.mint, passed=first_failure is None,
        first_failure=first_failure, checks=tuple(checks),
        ts=datetime.now(timezone.utc))
