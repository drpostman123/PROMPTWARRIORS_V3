"""OR tracker edge days: gap-and-go that never re-enters the range, a day
with no OR data at all, and a breakout on the very first post-OR bar."""

from __future__ import annotations

from datetime import timedelta

from godmode0dte.config import SignalConfig
from godmode0dte.features.opening_range import OpeningRangeTracker
from godmode0dte.models import Direction
from tests.test_audit_round2 import bar, or_tracker_with_range


def test_gap_and_go_day_never_qualifies_and_never_latches():
    """Price gaps away and never re-enters the OR: every candidate bar is
    beyond the G-S4 chase limit, so no direction ever confirms and the
    breakout-age clock never starts."""
    t = or_tracker_with_range()                        # OR 559-561, width 2
    runaway = [bar(13, 40, 563.0, 563.6, 562.9, 563.5),
               bar(13, 45, 564.0, 564.7, 563.9, 564.6),
               bar(13, 50, 566.0, 566.8, 565.9, 566.7)]
    for b in runaway:
        direction, ev = t.classify_breakout(b, rel_volume=3.0)
        assert direction is None
        assert ev["chase"] > 0.5                       # chase gate refused it
    assert t.breakout_age_min(
        Direction.LONG, runaway[-1].ts + timedelta(minutes=30)) == 0.0


def test_no_or_data_day_blocks_everything():
    """Feed gap: the first bar arrives AFTER the OR window. The OR must
    never complete, so the width gate and breakout classifier stay closed."""
    t = OpeningRangeTracker(SignalConfig())
    t.add_bar(bar(13, 40, 565.0, 565.5, 564.5, 565.0))
    assert t.range is None
    ok, why = t.width_ok(565.0, atr5m=1.0)
    assert not ok and "not complete" in why
    assert t.classify_breakout(bar(13, 41, 566.0, 566.5, 565.5, 566.2), 3.0)[0] is None


def test_breakout_on_first_post_or_bar():
    """The 09:35 bar BOTH completes the OR and is itself the breakout
    candidate — it must confirm, and its volume must not pollute OR volume."""
    t = OpeningRangeTracker(SignalConfig())
    for m in range(30, 35):
        t.add_bar(bar(13, m, 560.0, 561.0, 559.0, 560.5))
    first_post = bar(13, 35, 561.0, 561.6, 560.9, 561.55)
    t.add_bar(first_post)
    assert t.range.complete
    assert t.range.volume == 5 * 1000                  # post-OR volume excluded
    direction, _ = t.classify_breakout(first_post, rel_volume=3.0)
    assert direction is Direction.LONG
    assert t.breakout_age_min(Direction.LONG, first_post.ts) == 0.0
