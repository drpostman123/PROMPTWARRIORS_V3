"""Self-improving loop: policy bounds + persistence fail-safes, the
grounded evaluators, and the learner's walk-forward adoption discipline
(minimum evidence, validation margin, freeze)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skyfire_sol.config import AppConfig, MemeConfig
from skyfire_sol.learning import evaluator as ev
from skyfire_sol.learning.learner import Learner
from skyfire_sol.learning.policy import LEARN_BOUNDS, PolicyStore, default_policy
from skyfire_sol.meme import entry as entry_engine
from skyfire_sol.models import TokenFacts
from skyfire_sol.persistence.db import Database

NOW = datetime.now(timezone.utc)
CFG = AppConfig()


# -- policy store ----------------------------------------------------------

def test_policy_bounds_clamp_and_version(tmp_path):
    store = PolicyStore(CFG, str(tmp_path))
    assert store.current.version == 0 and store.current.origin == "default"
    p = store.apply({"min_vol_accel": 99.0, "winner_press_gain": 0.1})
    assert p.min_vol_accel == LEARN_BOUNDS["min_vol_accel"][1]   # clamped down
    assert p.winner_press_gain == LEARN_BOUNDS["winner_press_gain"][0]
    assert p.version == 1 and p.origin == "learned"


def test_policy_persistence_and_failsafe(tmp_path):
    store = PolicyStore(CFG, str(tmp_path))
    store.apply({"min_vol_accel": 2.5})
    again = PolicyStore(CFG, str(tmp_path))
    assert again.current.min_vol_accel == 2.5 and again.current.version == 1
    (tmp_path / "policy.json").write_text("{corrupt")
    fresh = PolicyStore(CFG, str(tmp_path))
    d = default_policy(CFG)                          # unreadable -> defaults
    assert fresh.current.version == 0 and fresh.current.origin == "default"
    assert fresh.current.min_vol_accel == d.min_vol_accel


def test_policy_freeze_file(tmp_path):
    store = PolicyStore(CFG, str(tmp_path))
    assert not store.frozen
    (tmp_path / "POLICY_FREEZE").write_text("hold")
    assert store.frozen


# -- evaluator -------------------------------------------------------------

def sample(accel, fwd, growth=0.1, liq=500_000.0, momo=5.0, ts="t") -> ev.PhantomSample:
    return ev.PhantomSample(ts=ts, vol_accel=accel, holder_growth=growth,
                            liquidity_usd=liq, price_change_1h_pct=momo,
                            fwd_pct=fwd)


def test_sample_from_row_math_and_winsorizing():
    feats = {"volume_1h_usd": 60_000.0, "volume_6h_usd": 120_000.0,
             "holder_count": 110, "holder_count_prev": 100,
             "liquidity_usd": 250_000.0, "price_change_1h_pct": 4.0}
    s = ev.sample_from_row(feats, 900.0, 0, "t")
    assert s.vol_accel == pytest.approx(3.0)
    assert s.holder_growth == pytest.approx(0.10)
    assert s.fwd_pct == ev.REWARD_CAP_PCT            # winsorized at +300
    dead = ev.sample_from_row(feats, None, 1, "t")
    assert dead.fwd_pct == ev.REWARD_FLOOR_PCT       # dead token = -100
    assert ev.sample_from_row(feats, None, 0, "t") is None   # unresolved


def test_entry_reward_prefers_separating_threshold():
    # 60 hourly batches, each with one genuine mover and one fader:
    # slots are plentiful per batch, so only the GATE separates them.
    samples = []
    for h in range(60):
        ts = (NOW - timedelta(hours=h)).isoformat()
        samples.append(sample(3.5, +60.0, ts=ts))
        samples.append(sample(1.2, -40.0, ts=ts))
    loose = ev.entry_reward(samples, 1.0, 10.0, 100.0, 5.0)
    strict = ev.entry_reward(samples, 2.5, 10.0, 100.0, 5.0)
    assert strict == pytest.approx(60.0)
    assert loose == pytest.approx(10.0)              # took the faders too
    assert strict > loose
    # below the evidence floor: no reward at all
    assert ev.entry_reward(samples[:20], 1.0, 10.0, 100.0, 5.0) is None


def test_entry_reward_ranking_matters_when_slots_scarce():
    # one hour, 10 passing candidates, 5 slots: the score ordering decides.
    ts = NOW.isoformat()
    winners = [sample(4.0, +80.0, ts=ts) for _ in range(5)]
    losers = [sample(2.0, -30.0, ts=ts) for _ in range(5)]
    many_hours = []
    for h in range(30):                              # replicate to clear MIN_SELECTED
        hts = (NOW - timedelta(hours=h)).isoformat()
        many_hours += [ev.PhantomSample(hts, s.vol_accel, s.holder_growth,
                                        s.liquidity_usd, s.price_change_1h_pct,
                                        s.fwd_pct) for s in winners + losers]
    # accel-weighted scoring puts the 4.0-accel winners in the 5 slots
    r = ev.entry_reward(many_hours, 1.0, 10.0, 100.0, 5.0)
    assert r == pytest.approx(80.0)


def test_entry_reward_replays_pass_gates():
    shrinking = [sample(3.0, 50.0, growth=-0.2) for _ in range(40)]
    assert ev.entry_reward(shrinking, 1.0, 10, 100, 5) is None   # all vetoed
    red = [sample(3.0, 50.0, momo=-1.0) for _ in range(40)]
    assert ev.entry_reward(red, 1.0, 10, 100, 5) is None


def test_press_reward_math():
    boot = {"MEME_ROTATION": 25.0, "CORE_HOLD": 25.0, "YIELD": 20.0,
            "PERPS": 10.0, "HL_ROTATION": 20.0}
    events = [ev.PressEvent("t", "MEME_ROTATION", boot,
                            {"MEME_ROTATION": 10.0, "CORE_HOLD": 0.0,
                             "YIELD": 0.0, "PERPS": 0.0, "HL_ROTATION": 0.0})]
    r1 = ev.press_reward(events, 1.0)
    r2 = ev.press_reward(events, 2.0)
    assert r2 > r1                                   # pressing a real winner pays
    assert r1 == pytest.approx(2.5)                  # 25% weight x 10% return
    assert ev.press_reward([], 1.5) is None


# -- learner ---------------------------------------------------------------

async def seeded_db(tmp_path, n=300):
    """Synthetic world the incumbent gate (1.5) cannot separate: movers at
    accel 3.5 earn +50%, faders at accel 2.0 lose 40%, interleaved in
    time. A learned threshold in (2.0, 3.5] is the real edge."""
    db = Database(str(tmp_path / "learn.db"))
    await db.open()
    for i in range(n):
        good = i % 2 == 0
        feats = {"volume_1h_usd": (70_000.0 if good else 40_000.0),
                 "volume_6h_usd": 120_000.0,       # accel 3.5 vs 2.0
                 "holder_count": 110, "holder_count_prev": 100,
                 "liquidity_usd": 400_000.0, "price_change_1h_pct": 5.0}
        await db._write({
            "table": "phantom_log",
            "ts": (NOW - timedelta(hours=n - i)).isoformat(),
            "mint": f"m{i}", "symbol": f"T{i}", "stage": "near_miss",
            "reject_reason": "no_slot", "features_json": feats,
            "price_usd": 1.0, "fwd_6h": (50.0 if good else -40.0)})
    return db


def make_learner(db, tmp_path):
    store = PolicyStore(CFG, str(tmp_path))
    published = []

    async def publish(topic, msg):
        published.append((topic, msg))

    return Learner(db, store, publish, seed=42), store, published


async def test_learner_adopts_separating_threshold(tmp_path):
    db = await seeded_db(tmp_path)
    learner, store, published = make_learner(db, tmp_path)
    await learner.learn_once()
    # the learned gate should exclude the 2.0-accel faders
    assert store.current.version == 1
    assert store.current.min_vol_accel > 2.0
    rows = [m for t, m in published if m.get("table") == "policy_updates"]
    entry_row = next(r for r in rows if r["kind"] == "entry")
    assert entry_row["applied"] == 1
    assert entry_row["val_reward"] > (entry_row["incumbent_val_reward"] or 0)
    await db.close()


async def test_learner_waits_below_evidence_floor(tmp_path):
    db = await seeded_db(tmp_path, n=60)                    # 60 < 200
    learner, store, published = make_learner(db, tmp_path)
    await learner.learn_once()
    assert store.current.version == 0                       # untouched
    assert not [m for _, m in published if m.get("table") == "policy_updates"]
    await db.close()


async def test_frozen_learner_journals_but_never_applies(tmp_path):
    db = await seeded_db(tmp_path)
    learner, store, published = make_learner(db, tmp_path)
    (tmp_path / "POLICY_FREEZE").write_text("hold")
    await learner.learn_once()
    assert store.current.version == 0                       # nothing applied
    rows = [m for t, m in published if m.get("table") == "policy_updates"]
    entry_row = next(r for r in rows if r["kind"] == "entry")
    assert entry_row["applied"] == 0 and entry_row["frozen"] == 1
    await db.close()


async def test_learner_keeps_incumbent_without_real_edge(tmp_path):
    """A world where returns are flat regardless of features: nothing
    should beat the incumbent by the adoption margin."""
    db = Database(str(tmp_path / "flat.db"))
    await db.open()
    for i in range(300):
        feats = {"volume_1h_usd": 40_000.0 + (i % 7) * 5_000.0,
                 "volume_6h_usd": 120_000.0,
                 "holder_count": 110, "holder_count_prev": 100,
                 "liquidity_usd": 400_000.0, "price_change_1h_pct": 5.0}
        await db._write({
            "table": "phantom_log",
            "ts": (NOW - timedelta(hours=300 - i)).isoformat(),
            "mint": f"m{i}", "symbol": f"T{i}", "stage": "entry_reject",
            "reject_reason": "momentum_criteria", "features_json": feats,
            "price_usd": 1.0, "fwd_6h": 1.0})       # flat everywhere
    learner, store, published = make_learner(db, tmp_path)
    await learner.learn_once()
    assert store.current.version == 0               # no adoption without edge
    await db.close()


# -- policy overrides reach the entry engine -------------------------------

def test_entry_score_honors_policy():
    facts = TokenFacts(mint="M", symbol="T", price_usd=1.0,
                       liquidity_usd=500_000.0, volume_1h_usd=40_000.0,
                       volume_6h_usd=120_000.0, price_change_1h_pct=5.0,
                       holder_count=110, holder_count_prev=100)
    cfg = MemeConfig()                              # min_vol_accel default 1.5
    assert entry_engine.score(facts, cfg) is not None       # accel 2.0 passes
    strict = default_policy(CFG)
    strict = type(strict)(**{**strict.__dict__, "min_vol_accel": 3.0})
    assert entry_engine.score(facts, cfg, policy=strict) is None
    loose = type(strict)(**{**strict.__dict__, "min_vol_accel": 1.0,
                            "w_accel": 20.0})
    sig = entry_engine.score(facts, cfg, policy=loose)
    assert sig is not None and sig.score > 0