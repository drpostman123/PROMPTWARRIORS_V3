"""tradecore — infrastructure shared by the trading systems in this repo.

Only genuinely generic, domain-free code lives here: structured logging,
the atomic snapshot/JSONL state store, the generic state machine, and the
supervised-task restart wrapper. Domain logic (risk, execution, scoring)
stays inside each system — in particular, capability tokens are never
shared: each system mints its own.
"""

from tradecore.logging import get_logger, setup_logging
from tradecore.machine import StateMachine
from tradecore.statestore import StateStore, jsonable
from tradecore.supervise import supervised

__all__ = ["get_logger", "setup_logging", "StateMachine", "StateStore", "jsonable", "supervised"]
