"""Self-improving loop — grounded policy learning.

IMPORT LAYERING (enforced by tests/test_skyfire_token.py): this package
imports ONLY models, config, persistence and tradecore. Never safety/,
execution/, wallet, chain/, clients/, or ceo/ — the learner produces a
Policy (data); it cannot act, and it cannot touch a safety limit.

The Goodhart guard applies twice over here: the learner tunes ALPHA
parameters only (entry selectivity, score weights, the CEO's press
gain), each clamped to hard bounds it cannot widen, while every safety
parameter (slippage caps, breakers, correlation bucket, probation, rug
gates) lives outside its reach entirely.
"""
