"""Round 5: MC proof of the -6% law, fee model, sequential phase gate,
predictive risk."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from godmode0dte.config import AppConfig, FeeModelConfig


# ---- Monte-Carlo proof (small in-suite run; full run via the script) -------

def test_minus_6_law_holds_under_sequential_losses():
    from mc_risk_proof import run
    violations, worst = run(days=300, p_win=0.0, seed=7, verbose=False)
    assert violations == 0
    assert worst <= 6.05          # 6% + fee slack


def test_minus_6_law_holds_on_mixed_paths():
    from mc_risk_proof import run
    violations, worst = run(days=300, p_win=0.55, seed=11, verbose=False)
    assert violations == 0


# ---- fee model -------------------------------------------------------------

def test_fee_model_spy_round_trip():
    f = FeeModelConfig()
    # 2 legs x (1.00+0.10+0.03 open) + 2 x (0+0.10+0.03 close) + 0.05 = 2.57
    assert f.per_contract_round_trip("SPY") == pytest.approx(2.57)


def test_fee_model_spx_is_materially_higher():
    f = FeeModelConfig()
    spx = f.per_contract_round_trip("SPX")
    assert spx == pytest.approx(2.57 + 4 * 0.65)      # +index fee per leg-direction
    assert spx > 2 * f.per_contract_round_trip("SPY")


def test_fee_model_overwrites_single_knob():
    cfg = AppConfig()
    assert cfg.execution.friction_per_contract == pytest.approx(2.57)
    cfg2 = AppConfig.model_validate({"fees": {"use_model": False},
                                     "execution": {"friction_per_contract": 9.99}})
    assert cfg2.execution.friction_per_contract == pytest.approx(9.99)


# ---- sequential phase gate -------------------------------------------------

def test_sprt_promotes_a_real_edge_and_rejects_none():
    import random
    from phase_gate import adjudicate
    rng = random.Random(3)
    p_be = 0.60
    # True p = p_be + 0.10: a real edge must eventually PROMOTE.
    strong = [1.0 if rng.random() < 0.70 else -1.0 for _ in range(400)]
    assert adjudicate(strong, p_be)["verdict"] == "PROMOTE"
    # True p = p_be - 0.05: must terminate in REJECT, not linger forever.
    weak = [1.0 if rng.random() < 0.55 else -1.0 for _ in range(400)]
    assert adjudicate(weak, p_be)["verdict"] == "REJECT"


def test_gate_never_promotes_under_power_floor():
    from phase_gate import MIN_N, adjudicate
    # A perfect-looking tiny sample must not promote.
    assert adjudicate([1.0] * (MIN_N - 30), 0.60)["verdict"] != "PROMOTE" or MIN_N <= 30


def test_posterior_tail_sane():
    from phase_gate import beta_posterior_tail
    assert beta_posterior_tail(70, 30, 0.63) > 0.90       # clear edge
    assert beta_posterior_tail(50, 50, 0.63) < 0.05       # coin flip
    assert 0.0 <= beta_posterior_tail(0, 0, 0.5) <= 1.0   # flat prior


# ---- predictive risk -------------------------------------------------------

def test_projected_day_risk_matches_gate_arithmetic(cfg, governor):
    from tests.test_risk_governor import entry_window_now
    entry_window_now(cfg)
    per = 0.90 * 100 + cfg.execution.friction_per_contract
    base = governor.projected_day_risk_pct(per)
    assert base == pytest.approx(100.0 * per / 100_000.0, rel=1e-6)
    # After a drawdown the projection includes it.
    from datetime import datetime, timezone
    governor.update_equity(98_000.0, datetime.now(timezone.utc))
    assert governor.projected_day_risk_pct(per) == pytest.approx(
        100.0 * (2_000.0 + per) / 100_000.0, rel=1e-6)
