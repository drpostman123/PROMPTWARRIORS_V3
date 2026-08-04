"""Rule-based regime classifier (v1).

Trend: EMA(9) vs EMA(21) on 5m bars, EMA slope, and ADX(14).
Vol: VIX bands. Confidence blends trend strength (ADX) and EMA separation.
"""

from __future__ import annotations

import numpy as np

from godmode0dte.features.indicators import adx, atr, ema
from godmode0dte.models import Bar, Regime, RegimeState, VolRegime
from godmode0dte.regime.base import RegimeModel


class RuleBasedRegime(RegimeModel):
    def __init__(self, adx_trend_floor: float = 20.0) -> None:
        self._adx_floor = adx_trend_floor

    def classify(self, bars_5m: list[Bar], vix: float | None) -> RegimeState:
        vol = self._vol_regime(vix)
        if len(bars_5m) < 30:
            return RegimeState(Regime.UNKNOWN, vol, confidence=0.0, source="rules")

        closes = np.array([b.close for b in bars_5m])
        e_fast = ema(closes, 9)
        e_slow = ema(closes, 21)
        adx_now = float(np.nan_to_num(adx(bars_5m, 14)[-1]))
        atr_now = float(np.nan_to_num(atr(bars_5m, 14)[-1]))
        sep = e_fast[-1] - e_slow[-1]
        slope = e_fast[-1] - e_fast[-4]           # ~15 minutes of fast-EMA drift

        # Normalize separation by ATR so confidence is price-scale free.
        sep_norm = abs(sep) / atr_now if atr_now > 0 else 0.0
        trending = adx_now >= self._adx_floor and sep_norm >= 0.25

        if trending and sep > 0 and slope > 0:
            regime = Regime.TREND_UP
        elif trending and sep < 0 and slope < 0:
            regime = Regime.TREND_DOWN
        else:
            regime = Regime.RANGE

        adx_conf = min(1.0, max(0.0, (adx_now - self._adx_floor) / 20.0))
        sep_conf = min(1.0, sep_norm / 1.0)
        confidence = round(0.6 * adx_conf + 0.4 * sep_conf, 3) if trending else round(
            min(0.6, 1.0 - sep_conf), 3
        )
        probs = {regime.value: confidence}
        return RegimeState(regime, vol, confidence=confidence, probabilities=probs, source="rules")

    @staticmethod
    def _vol_regime(vix: float | None) -> VolRegime:
        if vix is None:
            return VolRegime.NORMAL
        if vix < 13:
            return VolRegime.LOW
        if vix < 20:
            return VolRegime.NORMAL
        if vix < 28:
            return VolRegime.ELEVATED
        return VolRegime.EXTREME
