"""GodMode0DTE v2 — high-conviction aggressive style.

A deliberate parallel system to godmode0dte (v1). Different philosophy:
v1 measures a small edge with tiny size and statistical gates; v2 hunts
overbought/oversold EXTREMES and goes heavy on long single-leg 0DTE
options when a perfect setup fires, selling into strength fast.

Shared with v1 (imported, not forked): market data hub + candle warmup,
indicators, order-book pulse, fee model, structured logging.
Kept by design: hard daily loss breaker, full decision logging, shadow
outcome book, fee awareness. Dropped by design: SPRT/Phase gates, the 2%
ladder, the -6%/4%/7% caps (v2 has its own, larger, coherent set).

v2's one law: per-trade risk never exceeds the REMAINING daily-loss
headroom — the breaker you kept is only real if no single trade can blow
through it.
"""

__version__ = "2.0.0"
