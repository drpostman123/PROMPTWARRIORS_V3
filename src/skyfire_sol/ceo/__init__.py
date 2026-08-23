"""CEO package — the greedy allocator.

IMPORT LAYERING (enforced by tests/test_skyfire_token.py): this package
imports ONLY models, bus, blackboard, config, persistence and tradecore.
Never safety/, execution/, wallet, chain/, or clients/ — the CEO
produces data (verdicts, allocation targets); it cannot act.
"""
