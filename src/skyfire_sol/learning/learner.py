"""The learning loop: every few hours, replay the grounded record and
propose better alpha parameters.

Discipline (all of it deliberate, all of it tested):
- Minimum evidence: no proposal until enough resolved samples exist.
- Walk-forward validation: candidates are searched on the OLDER 70% of
  the data and adopted only if they also beat the incumbent on the
  NEWEST 30% by a margin — yesterday's overfit dies on today's data.
- Bounds: every parameter clamps to LEARN_BOUNDS; the search cannot
  widen its own box.
- Freeze: if state/skyfire/POLICY_FREEZE exists the learner still
  evaluates and journals its proposal, but applies nothing.
- Audit: every pass (adopted, rejected, or frozen) writes a
  policy_updates row with before/after, sample counts and rewards.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from skyfire_sol.learning import evaluator as ev
from skyfire_sol.learning.policy import LEARN_BOUNDS, PolicyStore
from skyfire_sol.persistence.db import Database
from tradecore.logging import get_logger

log = get_logger("learner")

MIN_PHANTOM_SAMPLES = 200
MIN_PRESS_EVENTS = 15
ENTRY_ADOPT_MARGIN_PCT = 1.0        # val reward must beat incumbent by this
PRESS_ADOPT_MARGIN_PCT = 0.10
SEARCH_CANDIDATES = 40
TRAIN_FRAC = 0.7
ENTRY_PARAMS = ("min_vol_accel", "w_accel", "w_growth", "w_liq")


class Learner:
    def __init__(self, db: Database, store: PolicyStore,
                 publish: Callable[[str, object], Awaitable[None]],
                 interval_s: float = 6 * 3600.0,
                 seed: Optional[int] = None) -> None:
        self._db = db
        self._store = store
        self._publish = publish
        self._interval = interval_s
        self._rng = random.Random(seed)

    async def run(self) -> None:
        while True:
            try:
                await self.learn_once()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("learn_pass_failed", error=str(e))
            await asyncio.sleep(self._interval)

    # -- one full pass ----------------------------------------------------

    async def learn_once(self) -> None:
        await self._learn_entry()
        await self._learn_press()

    # -- entry policy -----------------------------------------------------

    async def _phantom_samples(self) -> list[ev.PhantomSample]:
        rows = await self._db.rows(
            "SELECT ts, features_json, fwd_6h, dead FROM phantom_log "
            "WHERE stage IN ('entry_reject', 'near_miss', 'ceo_veto') "
            "AND (fwd_6h IS NOT NULL OR dead = 1) ORDER BY ts")
        out = []
        for ts, features_json, fwd, dead in rows:
            try:
                s = ev.sample_from_row(json.loads(features_json), fwd, dead, ts)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if s is not None:
                out.append(s)
        return out

    def _entry_candidates(self, incumbent: dict[str, float]) -> list[dict[str, float]]:
        cands = [dict(incumbent)]
        for _ in range(SEARCH_CANDIDATES):
            cands.append({name: self._rng.uniform(*LEARN_BOUNDS[name])
                          for name in ENTRY_PARAMS})
        return cands

    async def _learn_entry(self) -> None:
        samples = await self._phantom_samples()
        if len(samples) < MIN_PHANTOM_SAMPLES:
            log.info("learn_entry_waiting", samples=len(samples),
                     need=MIN_PHANTOM_SAMPLES)
            return
        cut = int(len(samples) * TRAIN_FRAC)
        train, val = samples[:cut], samples[cut:]

        pol = self._store.current
        incumbent = {k: getattr(pol, k) for k in ENTRY_PARAMS}
        best, best_train = dict(incumbent), None
        for cand in self._entry_candidates(incumbent):
            r = ev.entry_reward(train, **cand)
            if r is not None and (best_train is None or r > best_train):
                best, best_train = cand, r

        inc_val = ev.entry_reward(val, **incumbent)
        best_val = ev.entry_reward(val, **best)
        adopt = (best != incumbent
                 and best_val is not None
                 and best_val > (inc_val if inc_val is not None
                                 else 0.0) + ENTRY_ADOPT_MARGIN_PCT)
        await self._conclude("entry", incumbent, best, best_train,
                             best_val, inc_val, len(samples), adopt)

    # -- press gain -------------------------------------------------------

    async def _press_events(self) -> list[ev.PressEvent]:
        rows = await self._db.rows(
            "SELECT ts, greedy_scores_json, targets_json FROM allocations "
            "WHERE regime = 'risk_on' AND applied = 1 ORDER BY ts")
        nav: dict[str, list[tuple[datetime, float]]] = {}
        for sleeve, ts, v in await self._db.rows(
                "SELECT sleeve, ts, nav_usd FROM nav_history ORDER BY ts"):
            nav.setdefault(sleeve, []).append((datetime.fromisoformat(ts), v))

        def ret24(sleeve: str, t0: datetime) -> Optional[float]:
            series = nav.get(sleeve, [])
            base = next((v for t, v in series if t >= t0), None)
            after = next((v for t, v in series
                          if t >= t0 + timedelta(hours=24)), None)
            if base and after and base > 0:
                return (after / base - 1.0) * 100.0
            return None

        events = []
        for ts, scores_json, targets_json in rows:
            try:
                scores = json.loads(scores_json)
                boot = json.loads(targets_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not scores:
                continue
            winner = max(scores, key=scores.get)
            t0 = datetime.fromisoformat(ts)
            rets = {s: r for s in boot
                    if (r := ret24(s, t0)) is not None}
            if len(rets) < len(boot):
                continue                             # 24h not elapsed yet
            events.append(ev.PressEvent(ts=ts, winner=winner, boot=boot,
                                        next24_returns=rets))
        return events

    async def _learn_press(self) -> None:
        events = await self._press_events()
        if len(events) < MIN_PRESS_EVENTS:
            log.info("learn_press_waiting", events=len(events),
                     need=MIN_PRESS_EVENTS)
            return
        cut = int(len(events) * TRAIN_FRAC)
        train, val = events[:cut], events[cut:]
        lo, hi = LEARN_BOUNDS["winner_press_gain"]
        grid = [round(lo + i * 0.25, 2) for i in range(int((hi - lo) / 0.25) + 1)]

        incumbent = {"winner_press_gain": self._store.current.winner_press_gain}
        best_gain, best_train = incumbent["winner_press_gain"], None
        for g in grid:
            r = ev.press_reward(train, g)
            if r is not None and (best_train is None or r > best_train):
                best_gain, best_train = g, r
        best = {"winner_press_gain": best_gain}

        inc_val = ev.press_reward(val, incumbent["winner_press_gain"])
        best_val = ev.press_reward(val, best_gain)
        adopt = (best != incumbent
                 and best_val is not None and inc_val is not None
                 and best_val > inc_val + PRESS_ADOPT_MARGIN_PCT)
        await self._conclude("press", incumbent, best, best_train,
                             best_val, inc_val, len(events), adopt)

    # -- conclusion + audit -----------------------------------------------

    async def _conclude(self, kind: str, incumbent: dict, best: dict,
                        train_reward: Optional[float], val_reward: Optional[float],
                        incumbent_val: Optional[float], samples: int,
                        adopt: bool) -> None:
        frozen = self._store.frozen
        applied = adopt and not frozen
        if applied:
            self._store.apply(best)
        reasoning = (
            f"{kind}: {'ADOPTED' if applied else 'frozen' if adopt else 'kept incumbent'}"
            f" — val {val_reward if val_reward is None else round(val_reward, 3)}"
            f" vs incumbent {incumbent_val if incumbent_val is None else round(incumbent_val, 3)}"
            f" over {samples} samples")
        await self._publish("persist", {
            "table": "policy_updates",
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "before_json": incumbent, "after_json": best,
            "train_reward": train_reward, "val_reward": val_reward,
            "incumbent_val_reward": incumbent_val,
            "samples": samples,
            "applied": 1 if applied else 0,
            "frozen": 1 if frozen else 0,
            "policy_version": self._store.current.version,
            "reasoning": reasoning})
        log.info("learn_conclusion", kind=kind, adopted=applied,
                 frozen=frozen, reasoning=reasoning)
