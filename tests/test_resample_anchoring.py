"""Golden tests for resample() clock anchoring (audit U2).

A mid-bucket boot (first bar at 13:32) must snap buckets to :30/:35/:40
clock boundaries — first-bar anchoring would put 13:32-13:36 in one bar
and shift every subsequent bucket.
"""

from __future__ import annotations

from datetime import datetime, timezone

from godmode0dte.features.bars import resample
from godmode0dte.models import Bar

UTC = timezone.utc


def _m(minute: int) -> Bar:
    p = float(minute)
    return Bar(ts=datetime(2026, 8, 4, 13, minute, tzinfo=UTC),
               open=p, high=p + 0.5, low=p - 0.5, close=p + 0.25, volume=100.0)


def test_resample_clock_anchored_golden():
    bars = [_m(m) for m in range(32, 45)]              # boot mid-bucket at 13:32
    out = resample(bars, 5)
    assert len(out) == 3
    b0, b1, b2 = out
    # First bucket is the 13:30-13:35 CLOCK bucket: only 3 bars (32, 33, 34).
    assert b0.ts == datetime(2026, 8, 4, 13, 32, tzinfo=UTC)
    assert b0.volume == 300.0
    assert (b0.open, b0.close, b0.high, b0.low) == (32.0, 34.25, 34.5, 31.5)
    # Second bucket starts exactly on the 13:35 boundary with all 5 bars.
    assert b1.ts == datetime(2026, 8, 4, 13, 35, tzinfo=UTC)
    assert b1.volume == 500.0
    assert (b1.open, b1.close) == (35.0, 39.25)
    # Third bucket (13:40-13:44) is still forming.
    assert b2.ts == datetime(2026, 8, 4, 13, 40, tzinfo=UTC)
    assert b2.volume == 500.0
    # Indicator callers drop the live partial bucket.
    assert len(resample(bars, 5, drop_partial=True)) == 2


def test_resample_skips_empty_buckets_after_feed_gap():
    bars = [_m(m) for m in (32, 33, 34, 47)]           # dead feed 13:35-13:45
    out = resample(bars, 5)
    assert [b.ts.minute for b in out] == [32, 47]      # no phantom empty bars
    assert out[0].volume == 300.0 and out[1].volume == 100.0
    assert resample(bars, 5, drop_partial=True)[-1].ts.minute == 32
