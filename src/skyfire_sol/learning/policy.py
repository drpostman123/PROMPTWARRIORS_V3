"""The learned Policy: the small set of alpha parameters the
self-improving loop may tune, with hard bounds it cannot widen.

Persisted to state/skyfire/policy.json (atomic write, versioned).
Fail-safe: an unreadable file boots the config defaults, never a
half-parsed policy. The operator can freeze learning at any time by
creating state/skyfire/POLICY_FREEZE (MCP freeze_policy) — the learner
keeps evaluating and journaling proposals but applies nothing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from skyfire_sol.config import AppConfig
from tradecore.logging import get_logger

log = get_logger("policy")

# Hard bounds — the law for the learner. A candidate outside its bound is
# clamped, not rejected, so a runaway search can never widen the box.
LEARN_BOUNDS: dict[str, tuple[float, float]] = {
    "min_vol_accel": (1.0, 5.0),        # entry gate: 1h vol vs 6h baseline
    "w_accel": (1.0, 30.0),             # entry score weights
    "w_growth": (0.0, 300.0),
    "w_liq": (0.0, 20.0),
    "winner_press_gain": (1.0, 2.5),    # CEO risk-on concentration
}


@dataclass(frozen=True)
class Policy:
    version: int
    min_vol_accel: float
    w_accel: float
    w_growth: float
    w_liq: float
    winner_press_gain: float
    origin: str                          # "default" | "learned"
    updated_at: str

    def clamped(self) -> "Policy":
        fixes = {}
        for name, (lo, hi) in LEARN_BOUNDS.items():
            v = getattr(self, name)
            if not (lo <= v <= hi):
                fixes[name] = min(max(v, lo), hi)
        return replace(self, **fixes) if fixes else self


def default_policy(cfg: AppConfig) -> Policy:
    return Policy(
        version=0,
        min_vol_accel=cfg.sleeves.meme.min_vol_accel,
        w_accel=10.0, w_growth=100.0, w_liq=5.0,     # entry.score's historical weights
        winner_press_gain=cfg.ceo.winner_press_gain,
        origin="default",
        updated_at=datetime.now(timezone.utc).isoformat()).clamped()


class PolicyStore:
    def __init__(self, cfg: AppConfig, state_dir: str) -> None:
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self._file = d / "policy.json"
        self._freeze_file = d / "POLICY_FREEZE"
        self._policy = default_policy(cfg)
        self._restore(cfg)

    def _restore(self, cfg: AppConfig) -> None:
        if not self._file.exists():
            return
        try:
            raw = json.loads(self._file.read_text())
            self._policy = Policy(**raw).clamped()
            log.info("policy_restored", version=self._policy.version,
                     origin=self._policy.origin)
        except (json.JSONDecodeError, TypeError, ValueError, OSError) as e:
            self._policy = default_policy(cfg)
            log.error("policy_unreadable_using_defaults", error=str(e))

    @property
    def current(self) -> Policy:
        return self._policy

    @property
    def frozen(self) -> bool:
        return self._freeze_file.exists()

    def apply(self, updates: dict[str, float], origin: str = "learned") -> Policy:
        """Clamp, version-bump, persist atomically. Caller journals."""
        candidate = replace(
            self._policy, **updates,
            version=self._policy.version + 1, origin=origin,
            updated_at=datetime.now(timezone.utc).isoformat()).clamped()
        try:
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(candidate), indent=2))
            tmp.replace(self._file)
        except OSError as e:
            log.error("policy_persist_failed", error=str(e))
        self._policy = candidate
        log.info("policy_applied", version=candidate.version,
                 **{k: getattr(candidate, k) for k in LEARN_BOUNDS})
        return candidate
