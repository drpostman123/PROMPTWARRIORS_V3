"""Rule-based regime classifier per DESIGN_SPEC §4.2 (audit round 2, U3).

Three ATR-normalized features on closed 5m bars:

    f_ema  = (EMA9 - EMA21) / ATR14          trend structure
    f_vwap = (close - session VWAP) / ATR14  session positioning
    f_day  = (close - day open) / ATR14      day direction

TREND_UP   iff f_ema > +0.30 AND f_vwap > +0.25 AND f_day > -0.25
TREND_DOWN mirrored. Otherwise RANGE. Probabilities are fixed at
0.85 / 0.10 / 0.05 (matched / adjacent / opposite) — an honest "rules
can't do calibrated posteriors" stance; the HMM upgrade path replaces
them behind the same interface. UNKNOWN only until 4 closed 5m bars,
which is why the entry window opens at 09:50.
"""

from __future__ import annotations

import numpy as np

from godmode0dte.features.indicators import atr, ema, vwap
from godmode0dte.models import Bar, Regime, RegimeState, VolRegime
from godmode0dte.regime.base import RegimeModel


class RuleBasedRegime(RegimeModel):
    def __init__(self, f_ema_floor: float = 0.30, f_vwap_floor: float = 0.25,
                 f_day_floor: float = -0.25) -> None:
        self._f_ema = f_ema_floor
        self._f_vwap = f_vwap_floor
        self._f_day = f_day_floor

    def classify(self, bars_5m: list[Bar], vix: float | None) -> RegimeState:
        vol = self._vol_regime(vix)
        # 15-bar floor (audit R3 #6f): ATR(3) at 09:50 miscalibrates every
        # ATR-normalized feature. Until daily-ATR plumbing lands (deferred),
        # a real ATR14 needs 15 closed 5m bars — regime (and therefore entries)
        # arms ~10:45. Safety over opportunity.
        if len(bars_5m) < 15:
            return RegimeState(Regime.UNKNOWN, vol, confidence=0.0, source="rules")

        closes = np.array([b.close for b in bars_5m])
        atr_now = float(np.nan_to_num(atr(bars_5m, 14)[-1]))
        if atr_now <= 0:
            return RegimeState(Regime.UNKNOWN, vol, confidence=0.0, source="rules")

        f_ema = (ema(closes, 9)[-1] - ema(closes, 21)[-1]) / atr_now
        f_vwap = (closes[-1] - vwap(bars_5m)[-1]) / atr_now
        f_day = (closes[-1] - bars_5m[0].open) / atr_now

        if f_ema > self._f_ema and f_vwap > self._f_vwap and f_day > self._f_day:
            regime = Regime.TREND_UP
            probs = {"trend_up": 0.85, "range": 0.10, "trend_down": 0.05}
        elif f_ema < -self._f_ema and f_vwap < -self._f_vwap and f_day < -self._f_day:
            regime = Regime.TREND_DOWN
            probs = {"trend_down": 0.85, "range": 0.10, "trend_up": 0.05}
        else:
            regime = Regime.RANGE
            probs = {"range": 0.85, "trend_up": 0.075, "trend_down": 0.075}

        return RegimeState(regime, vol, confidence=0.85, probabilities=probs, source="rules")

    @staticmethod
    def _vol_regime(vix: float | None) -> VolRegime:
        """Bands per spec §4.2: 14 / 22 / 30."""
        if vix is None:
            return VolRegime.NORMAL
        if vix < 14:
            return VolRegime.LOW
        if vix < 22:
            return VolRegime.NORMAL
        if vix < 30:
            return VolRegime.ELEVATED
        return VolRegime.EXTREME
