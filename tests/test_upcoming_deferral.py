"""An announced item only takes a feed slot that nothing running needs.

Operator decision 2026-10-02: on 02.10. at 18:00 five of the ten slots held
items that had not begun yet ("20A: Bauarbeiten" from 13.10., the R 40
closure from 31.10.), while two disruptions valid that evening stood on
places 11 and 12.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import src.build_feed as bf
from src.feed_types import FeedItem

VIENNA = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 2, 20, 0, tzinfo=VIENNA)


def _item(title: str, start: datetime | None) -> FeedItem:
    item: FeedItem = {"title": title, "description": f"{title}.", "link": "", "source": "Wiener Linien"}
    if start is not None:
        item["starts_at"] = start
    return item


def test_items_beyond_the_preview_move_behind_the_field() -> None:
    works = _item("20A: Bauarbeiten", datetime(2026, 10, 13, tzinfo=VIENNA))
    accident = _item("34A: Verkehrsunfall", datetime(2026, 10, 2, 19, 10, tzinfo=VIENNA))
    closure = _item("R 40: Wien Franz-Josefs-Bahnhof ↔ Tulln", datetime(2026, 10, 31, tzinfo=VIENNA))
    detour = _item("29B/N25: Adolf-Loos-Gasse", datetime(2026, 10, 5, tzinfo=VIENNA))
    replacement = _item("N71: Ersatzverkehr", datetime(2026, 10, 1, tzinfo=VIENNA))

    ordered = bf._defer_upcoming_items([works, accident, closure, detour, replacement], NOW, 3)

    # Monday 05.10. is within three days of Friday, so 29B keeps its place.
    assert [it["title"] for it in ordered] == [
        "34A: Verkehrsunfall",
        "29B/N25: Adolf-Loos-Gasse",
        "N71: Ersatzverkehr",
        "20A: Bauarbeiten",
        "R 40: Wien Franz-Josefs-Bahnhof ↔ Tulln",
    ]


def test_the_preview_counts_vienna_days() -> None:
    # 22:30 UTC on 02.10. is already 03.10. in Vienna: a start on 04.10. is
    # one day ahead, not two.
    late = datetime(2026, 10, 2, 22, 30, tzinfo=UTC)
    sunday = _item("Sonntag", datetime(2026, 10, 4, tzinfo=VIENNA))
    running = _item("läuft", datetime(2026, 10, 1, tzinfo=VIENNA))

    assert bf._defer_upcoming_items([sunday, running], late, 1) == [sunday, running]
    assert bf._defer_upcoming_items([sunday, running], late, 0) == [running, sunday]


def test_items_without_start_and_running_items_stay() -> None:
    items = [_item("ohne Beginn", None), _item("läuft", datetime(2026, 9, 1, tzinfo=VIENNA))]

    assert bf._defer_upcoming_items(items, NOW, 0) is items


def test_main_fills_free_slots_with_announcements() -> None:
    now = datetime.now(UTC)
    announced = _item("20A: Bauarbeiten", now + timedelta(days=11))
    running = _item("N71: Ersatzverkehr", now - timedelta(days=1))
    for guid, it, age in (("gA", announced, 5), ("gR", running, 40)):
        it["guid"] = guid
        it["category"] = "Hinweis"
        it["pubDate"] = now - timedelta(minutes=age)
        it["ends_at"] = now + timedelta(days=20)
    # The announcement is newer: without the rule it would lead the feed.
    state = {
        "gA": {"first_seen": (now - timedelta(minutes=5)).isoformat()},
        "gR": {"first_seen": (now - timedelta(minutes=40)).isoformat()},
    }
    rendered: list[list[str]] = []

    def fake_make_rss(items: list[FeedItem], *args: Any, **kwargs: Any) -> str:
        rendered.append([it["title"] for it in items])
        return ""

    with patch.object(bf, "_invoke_collect_items", return_value=[announced, running]), \
         patch.object(bf, "_load_state", return_value=state), \
         patch.object(bf, "_make_rss", side_effect=fake_make_rss), \
         patch.object(bf, "_save_state", MagicMock()), \
         patch.object(bf, "atomic_write", MagicMock()), \
         patch.object(bf, "validate_path", MagicMock()), \
         patch.object(bf, "write_feed_health_report", MagicMock()), \
         patch.object(bf, "write_feed_health_json", MagicMock()):
        assert bf.main() == 0

    # Behind the running item, but still in the feed: a slot was free.
    assert rendered[0] == ["N71: Ersatzverkehr", "20A: Bauarbeiten"]


def test_by_default_an_announcement_leads_from_the_day_before() -> None:
    # Audit 2026-10-03: with three days, announcements took 1,442 slots in
    # 1,083 of 3,875 feed versions since 15.07., each time while a running
    # item stood on place 11 or later.
    assert bf.feed_config.UPCOMING_PREVIEW_DAYS == 1
    friday = datetime(2026, 10, 2, 20, 0, tzinfo=VIENNA)
    sunday = datetime(2026, 10, 4, 20, 0, tzinfo=VIENNA)
    monday_detour = _item("29B/N25: Adolf-Loos-Gasse", datetime(2026, 10, 5, tzinfo=VIENNA))
    running = _item("N71: Ersatzverkehr", datetime(2026, 10, 1, tzinfo=VIENNA))

    days = bf.feed_config.UPCOMING_PREVIEW_DAYS
    assert bf._defer_upcoming_items([monday_detour, running], friday, days) == [running, monday_detour]
    assert bf._defer_upcoming_items([monday_detour, running], sunday, days) == [monday_detour, running]
