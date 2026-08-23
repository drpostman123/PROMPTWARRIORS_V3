"""SafetyGate pipeline: first-failure order, exit bypasses, resize-down,
CEO veto handling, slippage cap at S10."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from skyfire_sol.blackboard import Blackboard
from skyfire_sol.clients.jupiter import SlippageCapExceeded
from skyfire_sol.config import USDC_MINT, AppConfig
from skyfire_sol.models import (
    CeoVerdict,
    IntentKind,
    JupQuote,
    Position,
    RegimeRead,
    RegimeState,
    Rejection,
    RugVerdict,
    SafetyStateView,
    Side,
    SleeveId,
    Snapshot,
    TradeIntent,
    Urgency,
)
from skyfire_sol.safety.breakers import PortfolioBreaker
from skyfire_sol.safety.gate import ApprovedOrder, SafetyGate
from skyfire_sol.safety.kill import KillSwitch
from skyfire_sol.safety.probation import Probation

MEME = "MemeMint111"
NOW = datetime.now(timezone.utc)


class FakeJupiter:
    def __init__(self):
        self.impact_pct = 0.5
        self.raise_cap = False

    async def quote(self, input_mint, output_mint, amount_raw, cap_pct):
        if self.raise_cap or self.impact_pct > cap_pct:
            raise SlippageCapExceeded(self.impact_pct, cap_pct)
        return JupQuote(input_mint, output_mint, amount_raw,
                        out_amount=amount_raw, price_impact_pct=self.impact_pct,
                        slippage_bps=int(cap_pct * 100), route_json="{}", ts=NOW)


class FakeExecutor:
    def __init__(self):
        self.orders = []

    async def execute(self, approved):
        self.orders.append(approved)

    async def execute_perp(self, approved):
        self.orders.append(approved)


def snapshot(nav=10_000.0, meme_nav=0.0, positions=(), regime=RegimeState.RISK_ON,
             targets=None, ts=None) -> Snapshot:
    return Snapshot(
        ts=ts or NOW, nav_usd=nav,
        sleeve_navs={"MEME_ROTATION": meme_nav, "CORE_HOLD": nav - meme_nav,
                     "YIELD": 0.0, "PERPS": 0.0},
        allocations_current={}, allocations_target=targets or {
            "MEME_ROTATION": 40.0, "CORE_HOLD": 25.0, "YIELD": 20.0, "PERPS": 15.0},
        positions=tuple(positions), prices_usd={USDC_MINT: 1.0},
        balances_raw={}, regime=RegimeRead(regime, 0.01, 0.6, 1e6, None, NOW),
        safety=SafetyStateView())


class Harness:
    def __init__(self, tmp_path):
        self.cfg = AppConfig()
        self.breaker = PortfolioBreaker(self.cfg.risk, str(tmp_path))
        self.probation = Probation(self.cfg.risk, str(tmp_path))
        self.kill = KillSwitch(str(tmp_path))
        self.kill_file = tmp_path / "KILL"
        self.bb = Blackboard()
        self.bb.swap(snapshot())
        self.jup = FakeJupiter()
        self.executor = FakeExecutor()
        self.decisions = []
        self.verdicts = {MEME: RugVerdict(MEME, True, None, (), NOW)}
        self.blacklisted = set()

        async def log_decision(rec):
            self.decisions.append(rec)

        async def decimals_for(mint):
            return 6

        self.gate = SafetyGate(
            self.cfg, self.breaker, self.probation, self.kill, self.bb,
            self.jup, self.executor, self.verdicts.get,
            lambda m: m in self.blacklisted, log_decision, decimals_for)

    def intent(self, kind=IntentKind.ENTRY, side=Side.BUY, mint=MEME,
               size=100.0, sleeve=SleeveId.MEME_ROTATION, reason="test",
               urgency=Urgency.NORMAL, qty_raw=None) -> TradeIntent:
        return TradeIntent(uuid.uuid4().hex[:12], sleeve, kind, side, mint,
                           USDC_MINT, size, urgency, reason, NOW, None, qty_raw)


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    # NAV must be sane for entries; seed the breaker
    harness.breaker.accept_nav(10_000.0)
    harness.breaker.on_nav(10_000.0, NOW)
    return harness


async def test_clean_entry_approved_and_executed(h):
    r = await h.gate.process(h.intent())
    assert isinstance(r, ApprovedOrder)
    assert h.executor.orders == [r]
    trace = json.loads(r.gate_trace)
    assert [t["check"] for t in trace][:3] == ["S0_kill", "S1_breaker", "S2_daily_pause"]


async def test_s0_kill_rejects_first(h):
    h.kill_file.write_text("halt")
    h.breaker.trip("test")                          # S1 would also fail
    r = await h.gate.process(h.intent())
    assert isinstance(r, Rejection) and r.check == "S0_kill"


async def test_s1_breaker_locked_rejects_exits_too(h):
    h.breaker.trip("test")
    h.breaker.confirm_flat()
    r = await h.gate.process(h.intent(kind=IntentKind.EXIT, side=Side.SELL,
                                      reason="trail_stop", qty_raw=10))
    assert isinstance(r, Rejection) and r.check == "S1_breaker"


async def test_tripped_passes_flatten_exit_only(h):
    h.breaker.trip("test")
    entry = await h.gate.process(h.intent())
    assert isinstance(entry, Rejection) and entry.check == "S1_breaker"
    flat = await h.gate.process(h.intent(
        kind=IntentKind.EXIT, side=Side.SELL, reason="breaker_flatten",
        urgency=Urgency.URGENT, qty_raw=10))
    assert isinstance(flat, ApprovedOrder)


async def test_s2_daily_pause_blocks_entry_not_exit(h):
    h.breaker.pause_until = NOW + timedelta(hours=3)
    r = await h.gate.process(h.intent())
    assert isinstance(r, Rejection) and r.check == "S2_daily_pause"
    exit_r = await h.gate.process(h.intent(
        kind=IntentKind.EXIT, side=Side.SELL, reason="time_stop", qty_raw=10))
    assert isinstance(exit_r, ApprovedOrder)


async def test_s3_soft_tier_blocks_entry(h):
    h.breaker.soft_tier_active = True
    r = await h.gate.process(h.intent())
    assert isinstance(r, Rejection) and r.check == "S3_soft_tier"


async def test_s4_stale_nav_blocks_entry(h):
    h.bb.swap(snapshot(ts=NOW - timedelta(minutes=10)))
    r = await h.gate.process(h.intent())
    assert isinstance(r, Rejection) and r.check == "S4_nav"


async def test_s5_blacklist(h):
    h.blacklisted.add(MEME)
    r = await h.gate.process(h.intent())
    assert isinstance(r, Rejection) and r.check == "S5_sanity"


async def test_s6_rug_verdict_missing_stale_failing(h):
    del h.verdicts[MEME]
    assert (await h.gate.process(h.intent())).check == "S6_rug"
    h.verdicts[MEME] = RugVerdict(MEME, False, "liquidity_floor", (), NOW)
    assert (await h.gate.process(h.intent())).check == "S6_rug"
    h.verdicts[MEME] = RugVerdict(MEME, True, None, (),
                                  NOW - timedelta(minutes=11))
    assert (await h.gate.process(h.intent())).check == "S6_rug"


async def test_s7_correlation_bucket_risk_off_30pct(h):
    h.bb.swap(snapshot(nav=10_000.0, meme_nav=2_950.0, regime=RegimeState.RISK_OFF))
    r = await h.gate.process(h.intent(size=200.0))
    assert isinstance(r, Rejection) and r.check == "S7_correlation_bucket"
    # same book under risk-on (55% cap) passes
    h.bb.swap(snapshot(nav=10_000.0, meme_nav=2_950.0, regime=RegimeState.RISK_ON))
    assert isinstance(await h.gate.process(h.intent(size=200.0)), ApprovedOrder)


async def test_s8_max_positions(h):
    positions = tuple(
        Position(f"p{i}", SleeveId.MEME_ROTATION, f"m{i}", "T", USDC_MINT,
                 10, 6, 1.0, NOW, 1.0) for i in range(5))
    h.bb.swap(snapshot(meme_nav=100.0, positions=positions))
    r = await h.gate.process(h.intent())
    assert isinstance(r, Rejection) and r.check == "S8_sleeve_caps"


async def test_s8_position_cap_resizes_down_never_up(h):
    # sleeve target 40% of 10k = 4000; per-position cap 20% = 800
    r = await h.gate.process(h.intent(size=5_000.0))
    assert isinstance(r, ApprovedOrder)
    assert r.size_usd == pytest.approx(800.0 * h.probation.size_mult)


async def test_s9_probation_halves_size(h):
    assert h.probation.active
    r = await h.gate.process(h.intent(size=100.0))
    assert isinstance(r, ApprovedOrder)
    assert r.size_usd == pytest.approx(50.0)
    for _ in range(10):
        h.probation.record_fill(0.1)
    r2 = await h.gate.process(h.intent(size=100.0))
    assert r2.size_usd == pytest.approx(100.0)


async def test_s10_slippage_cap_rejects(h):
    h.jup.impact_pct = 3.5                          # meme cap is 3.0
    r = await h.gate.process(h.intent())
    assert isinstance(r, Rejection) and r.check == "S10_quote"
    h.jup.impact_pct = 2.5
    assert isinstance(await h.gate.process(h.intent()), ApprovedOrder)


async def test_ceo_veto_kills_entry_and_resize_shrinks(h):
    veto = CeoVerdict("x", "veto", 0.0, "risk_off", NOW)
    r = await h.gate.process(h.intent(), veto)
    assert isinstance(r, Rejection) and r.check == "CEO_veto"
    resize = CeoVerdict("x", "resize", 0.5, "choppy", NOW)
    r2 = await h.gate.process(h.intent(size=100.0), resize)
    # 100 * 0.5 (CEO) * 0.5 (probation)
    assert isinstance(r2, ApprovedOrder) and r2.size_usd == pytest.approx(25.0)
    # a hostile mult > 1 cannot grow the size
    grow = CeoVerdict("x", "resize", 5.0, "greedy", NOW)
    r3 = await h.gate.process(h.intent(size=100.0), grow)
    assert r3.size_usd == pytest.approx(50.0)


async def test_ceo_cannot_veto_exits(h):
    veto = CeoVerdict("x", "veto", 0.0, "nope", NOW)
    r = await h.gate.process(h.intent(
        kind=IntentKind.EXIT, side=Side.SELL, reason="trail_stop", qty_raw=10),
        veto)
    assert isinstance(r, ApprovedOrder)


async def test_every_decision_is_journaled(h):
    await h.gate.process(h.intent())
    h.jup.impact_pct = 9.9
    await h.gate.process(h.intent())
    kinds = [d["kind"] for d in h.decisions]
    assert "gate_approve" in kinds and "gate_reject" in kinds


def test_clamp_allocations_hostile_ceo(tmp_path):
    h = Harness(tmp_path)
    hostile = {"MEME_ROTATION": 100.0, "CORE_HOLD": 0.0, "YIELD": 0.0, "PERPS": 0.0}
    clamped = h.gate.clamp_allocations(hostile, RegimeState.RISK_OFF)
    bucket = clamped["MEME_ROTATION"] + clamped["PERPS"]
    assert bucket <= h.cfg.risk.corr_risk_off_pct + 1e-6
    assert sum(clamped.values()) == pytest.approx(100.0)
    clamped_on = h.gate.clamp_allocations(hostile, RegimeState.RISK_ON)
    assert clamped_on["MEME_ROTATION"] + clamped_on["PERPS"] \
        <= h.cfg.risk.corr_risk_on_pct + 1e-6
