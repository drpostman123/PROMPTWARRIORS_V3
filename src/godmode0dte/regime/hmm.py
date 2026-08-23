"""Gaussian HMM regime classifier (v2, optional — requires hmmlearn).

Features per 5m bar: log return, realized-vol proxy (|high-low|/close).
Three hidden states are mapped to Trend-Up / Trend-Down / Range by the
sign and magnitude of each state's mean return. Falls back cleanly:
``HMMRegime.available()`` is False when hmmlearn is not installed and
the runtime keeps using :class:`RuleBasedRegime`.
"""

from __future__ import annotations

import numpy as np

from godmode0dte.models import Bar, Regime, RegimeState, VolRegime
from godmode0dte.regime.base import RegimeModel
from godmode0dte.regime.rules import RuleBasedRegime

try:
    from hmmlearn.hmm import GaussianHMM
    _HMM_OK = True
except ImportError:
    _HMM_OK = False


class HMMRegime(RegimeModel):
    def __init__(self, n_states: int = 3, min_train_bars: int = 200) -> None:
        if not _HMM_OK:
            raise ImportError("hmmlearn not installed — pip install 'godmode0dte[hmm]'")
        self._n_states = n_states
        self._min_train = min_train_bars
        self._model: "GaussianHMM | None" = None
        self._state_map: dict[int, Regime] = {}

    @staticmethod
    def available() -> bool:
        return _HMM_OK

    @staticmethod
    def _features(bars: list[Bar]) -> np.ndarray:
        closes = np.array([b.close for b in bars])
        rets = np.diff(np.log(closes))
        rv = np.array([(b.high - b.low) / b.close for b in bars[1:]])
        return np.column_stack([rets, rv])

    def fit(self, bars_5m: list[Bar]) -> None:
        X = self._features(bars_5m)
        if len(X) < self._min_train:
            return
        model = GaussianHMM(n_components=self._n_states, covariance_type="diag", n_iter=100)
        model.fit(X)
        means = model.means_[:, 0]
        order = np.argsort(means)
        self._state_map = {int(order[0]): Regime.TREND_DOWN, int(order[-1]): Regime.TREND_UP}
        for s in range(self._n_states):
            self._state_map.setdefault(s, Regime.RANGE)
        self._model = model

    def classify(self, bars_5m: list[Bar], vix: float | None) -> RegimeState:
        vol = RuleBasedRegime._vol_regime(vix)
        if self._model is None or len(bars_5m) < 30:
            return RegimeState(Regime.UNKNOWN, vol, confidence=0.0, source="hmm")
        X = self._features(bars_5m)
        posteriors = self._model.predict_proba(X)[-1]
        best = int(np.argmax(posteriors))
        probs = {
            self._state_map[s].value: round(float(p), 4)
            for s, p in enumerate(posteriors)
        }
        return RegimeState(
            regime=self._state_map[best],
            vol_regime=vol,
            confidence=round(float(posteriors[best]), 4),
            probabilities=probs,
            source="hmm",
        )
