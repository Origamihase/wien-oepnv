"""A construction site stays in the feed on its last day (audit 2026-10-03).

Stadt Wien delivers ends as dates ("2026-09-04Z"), read as Vienna midnight.
The time line shows "Bis Fr 04.09.", but the age filter dropped the item at
that midnight: every site left the feed at the start of its last day.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import src.build_feed as bf
from src.feed_types import FeedItem

_VIENNA = ZoneInfo("Europe/Vienna")


def _item(source: str, end: datetime) -> FeedItem:
    return {"source": source, "title": "Atzgersdorfer Straße", "guid": f"g-{source}", "ends_at": end}


def _kept(item: FeedItem, now: datetime) -> bool:
    kept, _ = bf._drop_old_items([item], now, {})
    return bool(kept)


_END = datetime(2026, 9, 4, tzinfo=_VIENNA)


def test_a_site_ending_on_a_date_stays_through_that_day() -> None:
    site = _item("Stadt Wien – Baustellen", _END)
    assert _kept(site, datetime(2026, 9, 4, 0, 30, tzinfo=_VIENNA))
    assert _kept(site, datetime(2026, 9, 4, 23, 50, tzinfo=_VIENNA))


def test_it_leaves_after_that_day() -> None:
    site = _item("Stadt Wien – Baustellen", _END)
    assert not _kept(site, datetime(2026, 9, 5, 0, 30, tzinfo=_VIENNA))


def test_the_day_is_the_vienna_day_across_dst() -> None:
    # 25.10.2026 has 25 hours in Vienna; the end is the next Vienna midnight.
    end = datetime(2026, 10, 25, tzinfo=_VIENNA)
    assert bf._valid_until(_item("Stadt Wien – Baustellen", end)) == datetime(2026, 10, 26, tzinfo=_VIENNA)


def test_a_site_ending_at_a_clock_time_keeps_it() -> None:
    end = datetime(2026, 9, 4, 18, 0, tzinfo=_VIENNA)
    assert bf._valid_until(_item("Stadt Wien – Baustellen", end)) == end


def test_other_sources_keep_their_midnight() -> None:
    end = datetime(2026, 9, 4, tzinfo=_VIENNA)
    assert bf._valid_until(_item("Wiener Linien", end)) == end
    assert not _kept(_item("Wiener Linien", end), datetime(2026, 9, 4, 0, 30, tzinfo=_VIENNA).astimezone(UTC))
