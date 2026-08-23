"""Generic state machine with logged transitions.

Every transition is logged as a structured event; illegal transitions
raise (they indicate a programming error, never a market condition).
Systems supply their own state Enum and allowed-transition adjacency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TypeVar

from tradecore.logging import get_logger

S = TypeVar("S", bound=Enum)


class StateMachine:
    def __init__(self, initial: Enum, allowed: dict[Enum, set[Enum]],
                 logger_name: str = "state_machine") -> None:
        self._state = initial
        self._allowed = allowed
        self._log = get_logger(logger_name)
        self.history: list[dict] = []

    @property
    def state(self) -> Enum:
        return self._state

    def transition(self, to: Enum, reason: str) -> None:
        if to == self._state:
            return
        if to not in self._allowed[self._state]:
            raise RuntimeError(f"illegal transition {self._state.value} -> {to.value} ({reason})")
        event = {
            "from": self._state.value,
            "to": to.value,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.history.append(event)
        self._log.info("state_transition", **event)
        self._state = to
