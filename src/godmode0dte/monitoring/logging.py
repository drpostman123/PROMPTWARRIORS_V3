"""Structured JSON logging — thin shim over tradecore.logging.

Kept so every godmode module's ``from godmode0dte.monitoring.logging
import get_logger`` continues to work; godmode keeps its historical
``logs/godmode.jsonl`` filename and root logger name.
"""

from __future__ import annotations

import logging

import structlog

from tradecore.logging import get_logger as _get_logger
from tradecore.logging import setup_logging as _setup_logging


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> structlog.BoundLogger:
    return _setup_logging(log_dir, level, filename="godmode.jsonl", root_logger_name="godmode")


def get_logger(name: str = "godmode") -> structlog.BoundLogger:
    return _get_logger(name)
