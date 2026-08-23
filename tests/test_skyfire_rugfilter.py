"""Rug/honeypot hard gate: every gate table-driven, ordering, and the
fail-closed rule (missing datum fails its gate)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from skyfire_sol.config import DiscoveryConfig
from skyfire_sol.discovery import rugfilter
from skyfire_sol.discovery.scanner import Blacklist
from skyfire_sol.models import TokenFacts

CFG = DiscoveryConfig()
NOW = datetime.now(timezone.utc)


def facts(**over) -> TokenFacts:
    base = dict(
        mint="M1", symbol="TEST",
        price_usd=0.001, liquidity_usd=250_000.0,
        volume_1h_usd=50_000.0, volume_6h_usd=120_000.0,
        pair_created_at=NOW - timedelta(hours=5),
        mint_authority_revoked=True, freeze_authority_revoked=True,
        lp_locked_or_burned_days=9999.0, top10_holder_pct_ex_lp=12.0,
        deployer_prior_rugs=0, sell_route_exists=True)
    base.update(over)
    return TokenFacts(**base)


def test_clean_token_passes():
    v = rugfilter.evaluate(facts(), CFG)
    assert v.passed and v.first_failure is None
    assert len(v.checks) == len(rugfilter.GATES)


def test_each_gate_fails_on_bad_value():
    cases = {
        "age_over_1h": {"pair_created_at": NOW - timedelta(minutes=30)},
        "liquidity_floor": {"liquidity_usd": 50_000.0},
        "mint_authority_revoked": {"mint_authority_revoked": False},
        "freeze_authority_revoked": {"freeze_authority_revoked": False},
        "lp_locked_or_burned": {"lp_locked_or_burned_days": 5.0},
        "top10_concentration": {"top10_holder_pct_ex_lp": 45.0},
        "sell_route_exists": {"sell_route_exists": False},
        "deployer_history": {"deployer_prior_rugs": 2},
    }
    for gate, over in cases.items():
        v = rugfilter.evaluate(facts(**over), CFG)
        assert not v.passed, gate
        assert v.first_failure == gate


def test_missing_datum_fails_closed():
    none_fields = ["pair_created_at", "liquidity_usd", "mint_authority_revoked",
                   "freeze_authority_revoked", "lp_locked_or_burned_days",
                   "top10_holder_pct_ex_lp", "sell_route_exists",
                   "deployer_prior_rugs"]
    for field in none_fields:
        v = rugfilter.evaluate(facts(**{field: None}), CFG)
        assert not v.passed, field


def test_first_failure_is_earliest_in_order():
    v = rugfilter.evaluate(
        facts(liquidity_usd=1.0, sell_route_exists=False), CFG)
    assert v.first_failure == "liquidity_floor"     # earlier gate wins
    # the full trace still records the later failure
    by_name = {c.gate: c for c in v.checks}
    assert not by_name["sell_route_exists"].passed


def test_boundary_values_fail():
    # exactly at the limits: liquidity must be > floor, top10 < cap
    assert not rugfilter.evaluate(facts(liquidity_usd=100_000.0), CFG).passed
    assert not rugfilter.evaluate(facts(top10_holder_pct_ex_lp=30.0), CFG).passed
    # age gate computes its own now(), so test strictly inside the window
    assert not rugfilter.evaluate(
        facts(pair_created_at=NOW - timedelta(minutes=59)), CFG).passed


def test_blacklist_roundtrip(tmp_path):
    bl = Blacklist(str(tmp_path))
    assert "X" not in bl
    bl.add("X")
    assert "X" in bl
    assert "X" in Blacklist(str(tmp_path))          # persisted
    corrupted = tmp_path / "blacklist.json"
    corrupted.write_text("{not json")
    assert "X" not in Blacklist(str(tmp_path))      # unreadable -> empty, logged
