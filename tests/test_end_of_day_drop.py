"""An item leaves the feed by the midnight after the day its time line names.

Independent check 2026-10-24: ``ENDS_AT_GRACE_MINUTES`` (10 min) kept an end
of 19.10. 23:59:59 over midnight, and the feed of 20.10. 00:00 Vienna showed
"Wien Hütteldorf ↔ Wien Hauptbahnhof … [Bis 19.10.]" (Feed-Wächter issue
#2044). The grace stays for every end earlier in the day.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import src.build_feed as bf
from src.feed_types import FeedItem

_VIENNA = ZoneInfo("Europe/Vienna")


def _item(source: str, end: datetime) -> FeedItem:
    return {
        "source": source,
        "title": "Wien Hütteldorf ↔ Wien Hauptbahnhof",
        "description": "Teilausfall.",
        "link": "",
        "guid": f"g-{source}",
        "ends_at": end,
    }


def _kept(item: FeedItem, now: datetime) -> bool:
    kept, _ = bf._drop_old_items([item], now, {})
    return bool(kept)


def test_an_end_of_day_leaves_at_midnight() -> None:
    item = _item("ÖBB", datetime(2026, 10, 19, 23, 59, 59, tzinfo=_VIENNA))
    assert _kept(item, datetime(2026, 10, 19, 23, 59, 59, tzinfo=_VIENNA))
    assert not _kept(item, datetime(2026, 10, 20, 0, 0, 45, tzinfo=_VIENNA))


def test_an_earlier_end_keeps_its_grace() -> None:
    item = _item("Wiener Linien", datetime(2026, 10, 19, 18, 0, tzinfo=_VIENNA))
    assert _kept(item, datetime(2026, 10, 19, 18, 9, tzinfo=_VIENNA))
    assert not _kept(item, datetime(2026, 10, 19, 18, 11, tzinfo=_VIENNA))


def test_a_site_leaves_at_the_midnight_after_its_last_day() -> None:
    site = _item("Stadt Wien – Baustellen", datetime(2026, 9, 4, tzinfo=_VIENNA))
    assert _kept(site, datetime(2026, 9, 4, 23, 59, tzinfo=_VIENNA))
    assert not _kept(site, datetime(2026, 9, 5, 0, 1, tzinfo=_VIENNA))


def test_the_midnight_is_the_vienna_one_across_dst() -> None:
    # 25.10.2026 has 25 hours in Vienna.
    item = _item("ÖBB", datetime(2026, 10, 25, 23, 59, 59, tzinfo=_VIENNA))
    assert bf._drop_after(item) == datetime(2026, 10, 25, 23, 0, tzinfo=ZoneInfo("UTC"))
