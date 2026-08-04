"""Vectorized indicator kernels on plain lists/arrays of bars.

Pure functions — no I/O, no state. Wilder smoothing is used for RSI,
ATR, and ADX to match standard platform values.
"""

from __future__ import annotations

import numpy as np

from godmode0dte.models import Bar


def ema(values: np.ndarray, period: int, sma_seed: bool = False) -> np.ndarray:
    """EMA. ``sma_seed=True`` seeds with the running mean of the available
    prefix (up to `period` values) instead of the first value — on short
    series a first-value seed over-weights the opening print (audit R3 #6g)."""
    if len(values) == 0:
        return values
    alpha = 2.0 / (period + 1)
    out = np.empty_like(values, dtype=float)
    if sma_seed:
        k = min(period, len(values))
        out[:k] = np.cumsum(values[:k]) / np.arange(1, k + 1)
        start = k
    else:
        out[0] = values[0]
        start = 1
    for i in range(start, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(values, dtype=float)
    out[: period] = np.nan
    if len(values) < period:
        return out
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(closes, prepend=closes[0])
    gains = np.clip(delta, 0, None)
    losses = np.clip(-delta, 0, None)
    avg_gain = _wilder(gains, period)
    avg_loss = _wilder(losses, period)
    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.inf), where=avg_loss != 0)
    return 100.0 - 100.0 / (1.0 + rs)


def true_range(bars: list[Bar]) -> np.ndarray:
    tr = np.empty(len(bars))
    for i, b in enumerate(bars):
        prev_close = bars[i - 1].close if i > 0 else b.open
        tr[i] = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
    return tr


def atr(bars: list[Bar], period: int = 14) -> np.ndarray:
    return _wilder(true_range(bars), period)


def adx(bars: list[Bar], period: int = 14) -> np.ndarray:
    n = len(bars)
    if n < 2 * period + 1:
        return np.full(n, np.nan)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    tr_s = _wilder(true_range(bars), period)
    plus_di = 100.0 * _wilder(plus_dm, period) / np.where(tr_s == 0, np.nan, tr_s)
    minus_di = 100.0 * _wilder(minus_dm, period) / np.where(tr_s == 0, np.nan, tr_s)
    dx = 100.0 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) == 0, np.nan, plus_di + minus_di)
    # Seed the second Wilder pass at the first VALID DX index — averaging the
    # warmup NaNs as zeros depressed ADX for the whole morning (audit U3).
    first_valid = period - 1
    out = np.full(n, np.nan)
    valid = _wilder(dx[first_valid:], period)
    out[first_valid:] = valid
    return out


def vwap(bars: list[Bar]) -> np.ndarray:
    """Session VWAP — caller passes bars from 09:30 only."""
    tp = np.array([(b.high + b.low + b.close) / 3.0 for b in bars])
    vol = np.array([b.volume for b in bars])
    cum_vol = np.cumsum(vol)
    return np.cumsum(tp * vol) / np.where(cum_vol == 0, np.nan, cum_vol)


def close_location_value(bar: Bar) -> float:
    """Where the close sits in the bar's range: 1.0 = at high, 0.0 = at low."""
    rng = bar.high - bar.low
    return 0.5 if rng <= 0 else (bar.close - bar.low) / rng
