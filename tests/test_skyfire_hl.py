"""Hyperliquid sleeve: momentum universe filters + ranking, the gate's
process_hl pipeline (clamps, probation, breaker/kill behavior), venue
order-result parsing, and the EVM wallet age roundtrip."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from skyfire_sol.config import AppConfig, HlConfig
from skyfire_sol.execution.hl_venue import HlVenue
from skyfire_sol.models import (
    HlIntent,
    HlMarketStat,
    RegimeState,
    Rejection,
)
from skyfire_sol.safety.gate import ApprovedHlOrder
from skyfire_sol.sleeves.hl_rotation import eligible, rank, regime_factor

from test_skyfire_gate_order import Harness, snapshot

NOW = datetime.now(timezone.utc)
CFG = HlConfig()


def stat(coin="PUMP", vol=100e6, ret=10.0, funding=0.001, mark=0.005,
         oi=50e6, lev=10) -> HlMarketStat:
    return HlMarketStat(coin=coin, mark_px=mark, day_volume_usd=vol,
                        ret_24h_pct=ret, funding_pct_hr=funding,
                        open_interest_usd=oi, max_leverage=lev)


# -- universe filters + ranking -------------------------------------------

def test_eligible_filters():
    stats = [
        stat("PUMP", ret=13.0),
        stat("BTC", ret=5.0),                        # excluded major
        stat("THIN", vol=1e6, ret=50.0),             # below volume floor
        stat("CROWD", funding=0.09, ret=20.0),       # rich funding
        stat("DOWN", ret=-4.0),                      # negative momentum
        stat("TRUMP", ret=8.0),
    ]
    got = {s.coin for s in eligible(stats, CFG)}
    assert got == {"PUMP", "TRUMP"}


def test_rank_orders_by_momentum_top_n():
    stats = [stat(f"C{i}", ret=float(i)) for i in range(1, 8)]
    top = rank(stats, CFG)
    assert [s.coin for s in top] == ["C7", "C6", "C5"]     # top_n = 3


def test_regime_factor_gates_gross():
    assert regime_factor(RegimeState.RISK_ON) == 1.0
    assert regime_factor(RegimeState.CHOPPY) == 0.5
    assert regime_factor(RegimeState.RISK_OFF) == 0.0
    assert regime_factor(RegimeState.UNKNOWN) == 0.0


# -- gate pipeline ---------------------------------------------------------

def hl_intent(action="open", notional=500.0, current=0.0, coin="PUMP") -> HlIntent:
    return HlIntent(uuid.uuid4().hex[:12], coin, action, notional, current,
                    mark_px=0.005, reason="test", ts=NOW)


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    harness.breaker.accept_nav(10_000.0)
    harness.breaker.on_nav(10_000.0, NOW)
    # give the snapshot an HL allocation target so per-position caps exist
    snap = snapshot()
    snap.allocations_target["HL_ROTATION"] = 20.0
    harness.bb.swap(snap)
    return harness


async def test_open_approved_with_probation_resize(h):
    r = await h.gate.process_hl(hl_intent(notional=500.0))
    assert isinstance(r, ApprovedHlOrder)
    # per-position cap: sleeve target 2000 * lev 1.0 / top_n 3 = 666.67;
    # 500 fits under it, then probation halves it
    assert r.notional_usd == pytest.approx(250.0)
    assert h.executor.orders == [r]


async def test_open_clamped_to_per_position_cap(h):
    r = await h.gate.process_hl(hl_intent(notional=5_000.0))
    assert isinstance(r, ApprovedHlOrder)
    assert r.notional_usd == pytest.approx(2_000.0 * 1.0 / 3 * 0.5)


async def test_open_respects_correlation_bucket(h):
    # risk-off cap 30% => room = 3000 - meme 2950 - hl 0 = 50 => below $15 min
    h.bb.swap(snapshot(nav=10_000.0, meme_nav=2_950.0,
                       regime=RegimeState.RISK_OFF))
    r = await h.gate.process_hl(hl_intent(notional=500.0))
    assert isinstance(r, Rejection) and r.check == "S8_sleeve_caps"


async def test_close_passes_tripped_breaker_and_kill(h):
    h.kill_file.write_text("halt")
    h.breaker.trip("test")
    open_r = await h.gate.process_hl(hl_intent())
    assert isinstance(open_r, Rejection) and open_r.check == "S0_kill"
    close_r = await h.gate.process_hl(
        hl_intent(action="close", notional=0.0, current=400.0))
    assert isinstance(close_r, ApprovedHlOrder)


async def test_open_blocked_by_pause_and_soft_tier(h):
    from datetime import timedelta
    h.breaker.pause_until = NOW + timedelta(hours=3)
    assert (await h.gate.process_hl(hl_intent())).check == "S2_daily_pause"
    h.breaker.pause_until = None
    h.breaker.soft_tier_active = True
    assert (await h.gate.process_hl(hl_intent())).check == "S3_soft_tier"


# -- venue result parsing --------------------------------------------------

def test_venue_check_parses_results():
    ok = {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"totalSz": "100", "avgPx": "0.005"}}]}}}
    err = {"status": "ok", "response": {"data": {"statuses": [
        {"error": "Order must have minimum value of $10"}]}}}
    bad = {"status": "err", "response": "Invalid signature"}
    assert HlVenue._check(ok, "t") is not None
    assert HlVenue._check(err, "t") is None
    assert HlVenue._check(bad, "t") is None
    assert HlVenue._check({}, "t") is None


# -- EVM wallet ------------------------------------------------------------

def test_evm_wallet_age_roundtrip(tmp_path):
    from skyfire_sol.wallet_evm import EvmWallet
    w = EvmWallet.generate()
    blob = w.encrypted("test-passphrase-123")
    path = tmp_path / "wallet_hl.age"
    path.write_bytes(blob)
    w2 = EvmWallet.load(str(path), "test-passphrase-123")
    assert w2.address == w.address
    assert w.address.startswith("0x") and len(w.address) == 42


def test_boot_allocations_include_hl():
    cfg = AppConfig()
    assert cfg.sleeves.boot_allocations["HL_ROTATION"] == 20.0
    bucket = (cfg.sleeves.boot_allocations["MEME_ROTATION"]
              + cfg.sleeves.boot_allocations["PERPS"]
              + cfg.sleeves.boot_allocations["HL_ROTATION"])
    assert bucket == pytest.approx(cfg.risk.corr_risk_on_pct)


def test_clamp_allocations_includes_hl_in_bucket(tmp_path):
    h = Harness(tmp_path)
    hostile = {"MEME_ROTATION": 40.0, "CORE_HOLD": 0.0, "YIELD": 0.0,
               "PERPS": 20.0, "HL_ROTATION": 40.0}
    clamped = h.gate.clamp_allocations(hostile, RegimeState.RISK_ON)
    bucket = (clamped["MEME_ROTATION"] + clamped["PERPS"]
              + clamped["HL_ROTATION"])
    assert bucket <= h.cfg.risk.corr_risk_on_pct + 1e-6
    assert sum(clamped.values()) == pytest.approx(100.0)