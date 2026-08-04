"""Regime model interface. Rule-based v1 and HMM v2 implement the same
contract, so scoring code never knows which is running."""

from __future__ import annotations

from abc import ABC, abstractmethod

from godmode0dte.models import Bar, RegimeState


class RegimeModel(ABC):
    """Classifies market state into (trend regime, vol regime, confidence)."""

    @abstractmethod
    def classify(self, bars_5m: list[Bar], vix: float | None) -> RegimeState:
        """Return the current regime state with a 0-1 confidence.

        Confidence below the configured minimum should be treated by the
        scorer as 'no regime edge' (zero regime points, or a hard gate
        in extreme vol).
        """
