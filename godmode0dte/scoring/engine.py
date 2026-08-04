"""Setup Score engine: transparent 0-100 score with component breakdown.

Weights come from config (must sum to 100). Any hard-gate failure zeroes
the tradeability of the setup regardless of points. The scorer never
touches risk or execution — it emits a SetupScore and nothing else.

Components (default weights):
  opening_range (15)          OR completed + width in band; tighter = better
  breakout_confirmation (20)  close beyond edge, rel volume, close location
  mtf_alignment (20)          1m/5m/15m EMA order + VWAP side + RSI band + ADX
  regime (15)                 regime direction matches, confidence-scaled
  macro_cluster (10)          DXY/VIX/yields confirmation (deduped)
  event_sentiment (5)         clean calendar; blackout is a hard gate
  dow_vix_preference (5)      day-of-week prior + VIX sweet spot
  microstructure (10)         leg spreads/OI/size vs limits, graded
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from godmode0dte.config import SignalConfig
from godmode0dte.data.calendar import EventVerdict
from godmode0dte.features.indicators import atr, ema, vwap
from godmode0dte.features.bars import resample
from godmode0dte.features.orderbook import BookState
from godmode0dte.models import (
    Bar, Direction, Quote, Regime, RegimeState, ScoreComponent, SetupScore, VolRegime,
)


@dataclass(frozen=True)
class ScoringInputs:
    bars_1m: list[Bar]
    direction: Optional[Direction]        # from breakout detection; None = no signal
    breakout_evidence: dict
    or_width_ok: bool
    or_width_reason: str
    rel_volume: float
    breakout_age_min: float
    regime: RegimeState
    macro_points: float                   # pre-computed by MacroCluster for direction
    macro_detail: str
    event: EventVerdict
    vix: Optional[float]
    long_leg_quote: Optional[Quote]
    short_leg_quote: Optional[Quote]
    max_leg_spread_pct: float             # from execution config
    min_open_interest_ok: bool
    book: Optional[BookState] = None      # L1 imbalance — logged; gate-only, zero weight
    now: Optional[datetime] = None


class ScoreEngine:
    def __init__(self, cfg: SignalConfig) -> None:
        self._cfg = cfg

    def score(self, inp: ScoringInputs) -> SetupScore:
        w = self._cfg.weights
        now = inp.now or datetime.now(timezone.utc)
        gates: list[str] = []
        comps: list[ScoreComponent] = []
        direction = inp.direction

        # ---- hard gates first --------------------------------------------
        if direction is None:
            gates.append("no confirmed breakout signal")
        if not inp.or_width_ok:
            gates.append(f"opening range filter: {inp.or_width_reason}")
        if inp.event.blackout:
            gates.append(f"event blackout: {inp.event.reason}")
        if inp.event.forced_bias is not None and direction is not None and direction != inp.event.forced_bias:
            gates.append(f"event layer forces {inp.event.forced_bias.value} bias only")
        if inp.regime.vol_regime is VolRegime.EXTREME:
            gates.append("extreme volatility regime")
        if inp.vix is not None and inp.vix > self._cfg.vix_hard_max:
            gates.append(f"VIX {inp.vix:.1f} above hard max {self._cfg.vix_hard_max}")
        if direction is Direction.LONG and inp.regime.regime is Regime.TREND_DOWN:
            gates.append("long signal against trend-down regime")
        if direction is Direction.SHORT and inp.regime.regime is Regime.TREND_UP:
            gates.append("short signal against trend-up regime")
        # Order-book gates (execution hazard, zero score weight — spec §1.2 rule).
        if self._cfg.book_gate_enabled and inp.book is not None and direction is not None:
            i = inp.book.imbalance
            against = -i if direction is Direction.LONG else i
            if against > self._cfg.book_conflict_threshold:
                gates.append(f"book stacked against direction (I={i:+.2f})")
            if inp.book.thinning:
                gates.append(
                    f"liquidity thinning: depth {inp.book.depth:.0f} < "
                    f"{self._cfg.book_thin_frac:.0%} of median {inp.book.depth_median:.0f}"
                )

        # ---- components ---------------------------------------------------
        comps.append(self._score_opening_range(inp, w.opening_range))
        comps.append(self._score_breakout(inp, w.breakout_confirmation))
        comps.append(self._score_mtf(inp, w.mtf_alignment))
        comps.append(self._score_regime(inp, w.regime))
        comps.append(ScoreComponent("macro_cluster", min(inp.macro_points, w.macro_cluster),
                                    w.macro_cluster, inp.macro_detail))
        comps.append(ScoreComponent("event_sentiment", min(inp.event.points, w.event_sentiment),
                                    w.event_sentiment, inp.event.reason))
        comps.append(self._score_dow_vix(inp, w.dow_vix_preference, now))
        comps.append(self._score_microstructure(inp, w.microstructure))

        total = round(sum(c.points for c in comps), 2)
        return SetupScore(
            total=total if not gates else 0.0,
            direction=direction,
            components=tuple(comps),
            hard_gate_failures=tuple(gates),
            ts=now,
        )

    # ---- component scorers -----------------------------------------------

    def _score_opening_range(self, inp: ScoringInputs, mx: int) -> ScoreComponent:
        if not inp.or_width_ok:
            return ScoreComponent("opening_range", 0, mx, inp.or_width_reason)
        return ScoreComponent("opening_range", mx, mx, "OR complete, width in band")

    def _score_breakout(self, inp: ScoringInputs, mx: int) -> ScoreComponent:
        if inp.direction is None:
            return ScoreComponent("breakout_confirmation", 0, mx, "no breakout")
        clv = float(inp.breakout_evidence.get("clv", 0.5))
        clv_eff = clv if inp.direction is Direction.LONG else 1.0 - clv
        # Half for relative volume beyond threshold, half for CLV; the whole
        # component decays linearly with breakout age (spec §1.2 ORB decay) —
        # a 25-minute-old breakout is nearly worthless information.
        rv_frac = min(1.0, (inp.rel_volume - self._cfg.min_rel_volume) / self._cfg.min_rel_volume + 0.5)
        clv_frac = min(1.0, max(0.0, (clv_eff - self._cfg.min_close_location) / (1 - self._cfg.min_close_location)))
        decay = max(0.0, 1.0 - inp.breakout_age_min / self._cfg.breakout_decay_min)
        pts = mx * (0.5 * max(0.0, rv_frac) + 0.5 * clv_frac) * decay
        detail = f"rel_vol={inp.rel_volume:.2f}, clv={clv_eff:.2f}, decay={decay:.2f}"
        return ScoreComponent("breakout_confirmation", round(pts, 2), mx, detail)

    def _score_mtf(self, inp: ScoringInputs, mx: int) -> ScoreComponent:
        """VWAP structure + higher-timeframe alignment (spec §1.2 VWAP/MTF rows).

        Five sub-parts scaled to mx (nominal 6/5/4/4/2 of 21). Every sub-part
        is deterministically evaluable or scores an explicit zero — an
        unevaluable timeframe FAILS rather than silently vanishing from the
        denominator (audit U4c).
        """
        if inp.direction is None or len(inp.bars_1m) < 10:
            return ScoreComponent("mtf_alignment", 0, mx, "insufficient bars")
        sign = 1 if inp.direction is Direction.LONG else -1
        scale = mx / 21.0
        pts = 0.0
        notes: list[str] = []

        v = vwap(inp.bars_1m)
        price = inp.bars_1m[-1].close
        bars5 = resample(inp.bars_1m, 5, drop_partial=True)
        atr5 = float(np.nan_to_num(atr(bars5, min(14, max(2, len(bars5) - 1)))[-1])) if len(bars5) >= 3 else 0.0

        # vwap_side (6): price on the right side of session VWAP.
        side_ok = sign * (price - v[-1]) > 0
        pts += 6 * scale if side_ok else 0
        notes.append(f"vwap_side:{'Y' if side_ok else 'N'}")

        # vwap_slope (5): VWAP drifting with the trade over the last 6 bars.
        slope_ok = False
        if len(v) >= 7 and atr5 > 0:
            slope_ok = sign * (v[-1] - v[-7]) > 0.05 * atr5
        pts += 5 * scale if slope_ok else 0
        notes.append(f"vwap_slope:{'Y' if slope_ok else 'N'}")

        # vwap_pullback (4): a recent touch near VWAP that HELD.
        pull_ok = False
        if atr5 > 0:
            for b in inp.bars_1m[-12:]:
                near = (b.low if sign > 0 else b.high)
                if abs(near - v[-1]) <= 0.25 * atr5 and sign * (b.close - v[-1]) > 0:
                    pull_ok = True
                    break
        pts += 4 * scale if pull_ok else 0
        notes.append(f"vwap_pullback:{'Y' if pull_ok else 'N'}")

        # mtf_15m (4): close > EMA9 > EMA21 on closed 15m bars (mirrored short).
        m15_ok = False
        bars15 = resample(inp.bars_1m, 15, drop_partial=True)
        if len(bars15) >= 2:
            c15 = np.array([b.close for b in bars15])
            e9, e21 = ema(c15, self._cfg.ema_fast)[-1], ema(c15, self._cfg.ema_slow)[-1]
            m15_ok = (c15[-1] > e9 > e21) if sign > 0 else (c15[-1] < e9 < e21)
        pts += 4 * scale if m15_ok else 0
        notes.append(f"ema_15m:{'Y' if m15_ok else 'N'}")

        # daily (2): needs historical daily bars — honest zero until wired.
        notes.append("daily:n/a")

        return ScoreComponent("mtf_alignment", round(pts, 2), mx, ", ".join(notes))

    def _score_regime(self, inp: ScoringInputs, mx: int) -> ScoreComponent:
        """Banded per spec §1.2: conf >= 0.85 -> full; 0.70-0.85 -> 12/20; else 0."""
        r = inp.regime
        if inp.direction is None or r.regime is Regime.UNKNOWN:
            return ScoreComponent("regime", 0, mx, f"regime={r.regime.value}")
        aligned = (
            (inp.direction is Direction.LONG and r.regime is Regime.TREND_UP)
            or (inp.direction is Direction.SHORT and r.regime is Regime.TREND_DOWN)
        )
        if not aligned:
            return ScoreComponent("regime", 0, mx, f"{r.regime.value} not aligned")
        if r.confidence >= 0.85:
            pts = float(mx)
        elif r.confidence >= self._cfg.min_regime_confidence:
            pts = mx * 12.0 / 20.0
        else:
            pts = 0.0
        return ScoreComponent("regime", round(pts, 2), mx,
                              f"{r.regime.value} conf={r.confidence:.2f} ({r.source})")

    def _score_dow_vix(self, inp: ScoringInputs, mx: int, now: datetime) -> ScoreComponent:
        dow_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
        dow_pts = min(self._cfg.dow_points.get(dow_key, 0), mx)
        vix_pts = 0.0
        if inp.vix is not None and self._cfg.vix_sweet_low <= inp.vix <= self._cfg.vix_sweet_high:
            vix_pts = mx - max(self._cfg.dow_points.values())
        pts = min(float(mx), dow_pts + vix_pts)
        return ScoreComponent("dow_vix_preference", round(pts, 2), mx,
                              f"dow={dow_key}({dow_pts}), vix={inp.vix}")

    def _score_microstructure(self, inp: ScoringInputs, mx: int) -> ScoreComponent:
        if inp.long_leg_quote is None or inp.short_leg_quote is None:
            return ScoreComponent("microstructure", 0, mx, "missing leg quotes")
        if not inp.min_open_interest_ok:
            return ScoreComponent("microstructure", 0, mx, "open interest below minimum")
        fracs = []
        for leg in (inp.long_leg_quote, inp.short_leg_quote):
            if leg.mid <= 0 or leg.bid <= 0:
                return ScoreComponent("microstructure", 0, mx, f"bad quote on {leg.symbol}")
            spread_pct = 100.0 * leg.spread / leg.mid
            if spread_pct > inp.max_leg_spread_pct:
                return ScoreComponent("microstructure", 0, mx,
                                      f"{leg.symbol} spread {spread_pct:.1f}% > limit")
            fracs.append(1.0 - spread_pct / inp.max_leg_spread_pct)
        pts = mx * (sum(fracs) / len(fracs))
        return ScoreComponent("microstructure", round(pts, 2), mx,
                              f"leg spread quality {fracs[0]:.2f}/{fracs[1]:.2f}")
