"""The Greedy CEO — full allocation authority, inside the gate.

Objective: maximize expected portfolio return, with risk appetite that
INCREASES in confirmed momentum regimes. Greedy score per sleeve =
sharpe_weight * 7d Sharpe + ret24_weight * 24h return, tilted toward
recent winners by winner_press_gain. Regime gates the greed:

  risk_on  -> press the winner (concentrate, up to the gate's clamps)
  choppy   -> spread toward boot allocations
  risk_off -> collapse to CORE/USDC (and the gate compresses MEME+PERPS
              to the 30% bucket regardless of what the CEO asks)

The §1 table is boot state only; the CEO may target 0-100% per sleeve.
Everything it emits is DATA — the SafetyGate clamps it before it moves
capital, and the CEO structurally cannot reach the executor (see
ceo/__init__.py and the AST test).
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import pstdev
from typing import Optional

from skyfire_sol.config import AppConfig
from skyfire_sol.models import (
    AllocationTargets,
    CeoVerdict,
    IntentKind,
    RegimeRead,
    RegimeState,
    SleeveId,
    SleevePerf,
    Snapshot,
    TradeIntent,
)
from tradecore.logging import get_logger

log = get_logger("ceo")


def _ema(values: list[float], period: int) -> Optional[float]:
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1.0 - k)
    return e


class GreedyCeo:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._sol_hourly: list[tuple[datetime, float]] = []   # hour-bucketed marks

    # -- inputs -----------------------------------------------------------

    def on_sol_mark(self, price_usd: float, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        hour = now.replace(minute=0, second=0, microsecond=0)
        if self._sol_hourly and self._sol_hourly[-1][0] == hour:
            self._sol_hourly[-1] = (hour, price_usd)
        else:
            self._sol_hourly.append((hour, price_usd))
            max_keep = self._cfg.ceo.sol_trend_ema_slow_h * 3
            if len(self._sol_hourly) > max_keep:
                self._sol_hourly = self._sol_hourly[-max_keep:]

    # -- regime -----------------------------------------------------------

    def read_regime(self, meme_breadth: float, agg_meme_volume_usd: float,
                    funding_rate_pct_hr: Optional[float] = None,
                    now: Optional[datetime] = None) -> RegimeRead:
        now = now or datetime.now(timezone.utc)
        prices = [p for _, p in self._sol_hourly]
        fast = _ema(prices, self._cfg.ceo.sol_trend_ema_fast_h)
        slow = _ema(prices, self._cfg.ceo.sol_trend_ema_slow_h)
        if fast is None or slow is None or slow <= 0 or len(prices) < 3:
            state = RegimeState.UNKNOWN
            trend = 0.0
        else:
            trend = fast / slow - 1.0
            c = self._cfg.ceo
            if trend > 0 and meme_breadth >= c.breadth_risk_on:
                state = RegimeState.RISK_ON
            elif trend < 0 or meme_breadth <= c.breadth_risk_off:
                state = RegimeState.RISK_OFF
            else:
                state = RegimeState.CHOPPY
            # Crowded-carry veto: paying rich funding flips risk-on to choppy.
            if (state is RegimeState.RISK_ON and funding_rate_pct_hr is not None
                    and funding_rate_pct_hr > self._cfg.sleeves.perps.max_funding_pct_hr):
                state = RegimeState.CHOPPY
        return RegimeRead(state=state, sol_trend=round(trend, 5),
                          meme_breadth=round(meme_breadth, 3),
                          agg_meme_volume_usd=agg_meme_volume_usd,
                          funding_rate_pct_hr=funding_rate_pct_hr, ts=now)

    # -- performance scoring ---------------------------------------------

    @staticmethod
    def sharpe_7d(nav_series: list[float]) -> Optional[float]:
        """Annualization-free Sharpe over a per-period NAV series."""
        if len(nav_series) < 3:
            return None
        rets = [nav_series[i] / nav_series[i - 1] - 1.0
                for i in range(1, len(nav_series)) if nav_series[i - 1] > 0]
        if not rets:
            return None
        sd = pstdev(rets)
        if sd == 0:
            return 0.0
        return (sum(rets) / len(rets)) / sd

    def greedy_scores(self, perf: list[SleevePerf]) -> dict[str, float]:
        c = self._cfg.ceo
        scores: dict[str, float] = {}
        for p in perf:
            sharpe = p.sharpe_7d if p.sharpe_7d is not None else 0.0
            ret24 = p.ret_24h_pct if p.ret_24h_pct is not None else 0.0
            scores[p.sleeve.value] = c.sharpe_weight * sharpe + c.ret24_weight * (ret24 / 10.0)
        return scores

    # -- allocation -------------------------------------------------------

    def allocate(self, perf: list[SleevePerf], regime: RegimeRead,
                 now: Optional[datetime] = None) -> AllocationTargets:
        now = now or datetime.now(timezone.utc)
        boot = dict(self._cfg.sleeves.boot_allocations)
        scores = self.greedy_scores(perf)

        if regime.state is RegimeState.RISK_OFF or regime.state is RegimeState.UNKNOWN:
            targets = {SleeveId.MEME_ROTATION.value: 10.0,
                       SleeveId.CORE_HOLD.value: 55.0,
                       SleeveId.YIELD.value: 30.0,
                       SleeveId.PERPS.value: 5.0}
            reasoning = (f"{regime.state.value}: collapse to CORE/USDC "
                         f"(sol_trend {regime.sol_trend:+.3f}, breadth {regime.meme_breadth:.2f})")
        elif regime.state is RegimeState.CHOPPY:
            targets = dict(boot)
            reasoning = ("choppy: spread at boot weights "
                         f"(sol_trend {regime.sol_trend:+.3f}, breadth {regime.meme_breadth:.2f})")
        else:
            # RISK_ON: press the winner. Start from boot, multiply the top
            # scorer's weight by winner_press_gain, renormalize (data only —
            # the gate clamps the MEME+PERPS bucket to 55%).
            targets = dict(boot)
            if scores:
                winner = max(scores, key=scores.get)          # type: ignore[arg-type]
                targets[winner] = targets.get(winner, 0.0) * self._cfg.ceo.winner_press_gain
                reasoning = (f"risk_on: pressing {winner} x{self._cfg.ceo.winner_press_gain} "
                             f"(scores {dict((k, round(v, 3)) for k, v in scores.items())})")
            else:
                reasoning = "risk_on: no perf history yet, boot weights"
        total = sum(targets.values()) or 1.0
        targets = {k: v / total * 100.0 for k, v in targets.items()}
        return AllocationTargets(targets=targets, regime=regime.state,
                                 reasoning=reasoning, ts=now)

    # -- intent verdicts --------------------------------------------------

    def judge_intent(self, intent: TradeIntent, snap: Snapshot) -> CeoVerdict:
        """Entries only — exits never reach the CEO. Data out; the gate can
        shrink further but never grow the CEO's size_mult above 1."""
        now = datetime.now(timezone.utc)
        if intent.kind is not IntentKind.ENTRY:
            return CeoVerdict(intent.intent_id, "approve", 1.0, "non-entry passthrough", now)
        state = snap.regime.state
        if state is RegimeState.RISK_ON:
            return CeoVerdict(intent.intent_id, "approve", 1.0,
                              "risk_on: full size", now)
        if state is RegimeState.CHOPPY:
            return CeoVerdict(intent.intent_id, "resize", 0.75,
                              "choppy: three-quarter size", now)
        if state is RegimeState.RISK_OFF:
            return CeoVerdict(intent.intent_id, "veto", 0.0,
                              "risk_off: no new meme risk", now)
        return CeoVerdict(intent.intent_id, "resize", 0.5,
                          "regime unknown: half size until regime confirms", now)
