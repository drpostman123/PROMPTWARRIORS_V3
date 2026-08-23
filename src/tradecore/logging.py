"""Structured JSON logging via structlog.

Every decision, transition, rejection, order, and fill is a structured
event. Console gets pretty output; ``logs/<filename>`` gets JSON for
post-hoc calibration.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def setup_logging(
    log_dir: str = "logs",
    level: int = logging.INFO,
    filename: str = "trade.jsonl",
    root_logger_name: str = "tradecore",
) -> structlog.BoundLogger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(Path(log_dir) / filename)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    console_handler = logging.StreamHandler(sys.stdout)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [file_handler, console_handler]

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger(root_logger_name)


def get_logger(name: str = "tradecore") -> structlog.BoundLogger:
    return structlog.get_logger(name)
