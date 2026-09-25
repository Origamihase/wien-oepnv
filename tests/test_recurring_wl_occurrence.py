"""A recurring Wiener-Linien disruption must not inherit an old ``first_seen``.

Live case 2026-09-25 15:01: the WL guid has no date, so "94A: Verkehrsunfall"
(started 14:43) carried the ``first_seen`` of the 94A accident on 2026-07-04.
The FIFO sort put it behind every stop relocation of the last week, and six
live disruptions (94A, O, 5, 12, U2, U1) were missing from the German feed
while its ten slots showed stop relocations and four newer disruptions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

NOW = datetime(2026, 9, 25, 13, 1, tzinfo=UTC)  # 15:01 Vienna


def _item(
    title: str,
    guid: str,
    pub: datetime | None,
    *,
    source: str = "Wiener Linien",
    category: str = "Störung",
) -> FeedItem:
    return {
        "source": source,
        "category": category,
        "title": title,
        "description": f"{title}.",
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": pub,
        "starts_at": pub,
        "ends_at": NOW + timedelta(hours=9),
    }


def _entry(first_seen: datetime, last_seen: datetime | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"first_seen": first_seen.isoformat()}
    if last_seen is not None:
        entry["last_seen"] = last_seen.isoformat()
    return entry


def test_recurring_incident_takes_its_own_start() -> None:
    pub = NOW - timedelta(minutes=18)
    state = {"g94": _entry(datetime(2026, 7, 4, 14, 30, tzinfo=UTC))}

    moved = bf._restart_recurring_occurrences(
        [_item("94A: Verkehrsunfall", "g94", pub)], state, NOW
    )

    assert moved == 1
    assert state["g94"]["first_seen"] == pub.isoformat()
    assert state["g94"]["last_seen"] == NOW.isoformat()


def test_reissued_measure_keeps_its_place() -> None:
    # WL re-issues running measures with a fresh validity window; the message
    # was in the build right before the new window started.
    pub = datetime(2026, 9, 22, 22, 0, 28, tzinfo=UTC)
    first = datetime(2026, 9, 14, 2, 30, tzinfo=UTC)
    state = {"g25": _entry(first, last_seen=pub - timedelta(minutes=30))}

    moved = bf._restart_recurring_occurrences(
        [_item("25: Linie 26E hält Donaufelder Straße 148", "g25", pub)], state, NOW
    )

    assert moved == 0
    assert state["g25"]["first_seen"] == first.isoformat()
    assert state["g25"]["last_seen"] == NOW.isoformat()


@pytest.mark.parametrize(
    ("absent_for", "restarted"),
    [
        (bf._OCCURRENCE_GAP - timedelta(minutes=1), False),
        (bf._OCCURRENCE_GAP, False),
        (bf._OCCURRENCE_GAP + timedelta(minutes=1), True),
        (timedelta(days=30), True),
    ],
)
def test_gap_before_the_new_start_decides(absent_for: timedelta, restarted: bool) -> None:
    pub = NOW - timedelta(minutes=10)
    first = NOW - timedelta(days=40)
    state = {"gU1": _entry(first, last_seen=pub - absent_for)}

    bf._restart_recurring_occurrences(
        [_item("U1: Weichenstörung", "gU1", pub)], state, NOW
    )

    expected = pub if restarted else first
    assert state["gU1"]["first_seen"] == expected.isoformat()


def test_running_message_is_untouched() -> None:
    first = datetime(2026, 9, 18, 12, 31, tzinfo=UTC)
    pub = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)  # validity began before first_seen
    state = {"g72": _entry(first)}

    moved = bf._restart_recurring_occurrences(
        [_item("72A: Kraftwerk Simmering", "g72", pub, category="Hinweis")], state, NOW
    )

    assert moved == 0
    assert state["g72"]["first_seen"] == first.isoformat()
    assert state["g72"]["last_seen"] == NOW.isoformat()


def test_future_pubdate_is_not_a_new_occurrence() -> None:
    first = NOW - timedelta(days=10)
    state = {"gF": _entry(first)}

    bf._restart_recurring_occurrences(
        [_item("U2: Polizeieinsatz", "gF", NOW + timedelta(hours=1))], state, NOW
    )

    assert state["gF"]["first_seen"] == first.isoformat()


def test_missing_pubdate_only_stamps() -> None:
    first = NOW - timedelta(days=10)
    state = {"gN": _entry(first)}

    moved = bf._restart_recurring_occurrences(
        [_item("U2: Polizeieinsatz", "gN", None)], state, NOW
    )

    assert moved == 0
    assert state["gN"] == {"first_seen": first.isoformat(), "last_seen": NOW.isoformat()}


def test_string_pubdate_is_parsed() -> None:
    pub = NOW - timedelta(minutes=5)
    state = {"gS": _entry(NOW - timedelta(days=90))}
    item = cast(FeedItem, {**_item("O: Schadhaftes Fahrzeug", "gS", None), "pubDate": pub.isoformat()})

    assert bf._restart_recurring_occurrences([item], state, NOW) == 1
    assert state["gS"]["first_seen"] == pub.isoformat()


def test_other_providers_are_untouched() -> None:
    # An ÖBB pubDate is the time of the latest update, not an occurrence start.
    first = datetime(2026, 8, 26, 13, 31, tzinfo=UTC)
    state = {"gO": _entry(first)}

    moved = bf._restart_recurring_occurrences(
        [_item("S 1: Wien Leopoldau ↔ Deutsch Wagram", "gO", NOW - timedelta(days=11), source="ÖBB")],
        state,
        NOW,
    )

    assert moved == 0
    assert state["gO"] == {"first_seen": first.isoformat()}


def test_items_without_entry_stay_unseen() -> None:
    state: dict[str, dict[str, Any]] = {}

    bf._restart_recurring_occurrences(
        [_item("16A: Rettungseinsatz", "g16", NOW - timedelta(minutes=11))], state, NOW
    )

    assert state == {}


def test_restarted_incident_ranks_ahead_of_older_relocation() -> None:
    incident = _item("94A: Verkehrsunfall", "g94", NOW - timedelta(minutes=18))
    relocation = _item(
        "72A: Kraftwerk Simmering", "g72", datetime(2026, 9, 17, 22, 0, tzinfo=UTC), category="Hinweis"
    )
    state = {
        "g94": _entry(datetime(2026, 7, 4, 14, 30, tzinfo=UTC)),
        "g72": _entry(datetime(2026, 9, 18, 12, 31, tzinfo=UTC)),
    }

    def order() -> list[str]:
        ranked = sorted([incident, relocation], key=lambda it: bf._recency_sort_key(it, state, NOW))
        return [it["title"] for it in ranked]

    assert order() == ["72A: Kraftwerk Simmering", "94A: Verkehrsunfall"]  # the bug
    bf._restart_recurring_occurrences([incident, relocation], state, NOW)
    assert order() == ["94A: Verkehrsunfall", "72A: Kraftwerk Simmering"]


def test_main_orders_a_recurring_incident_first() -> None:
    now = datetime.now(UTC)
    incident = _item("94A: Verkehrsunfall", "g94", now - timedelta(minutes=18))
    relocation = _item(
        "72A: Kraftwerk Simmering", "g72", now - timedelta(days=7, hours=12), category="Hinweis"
    )
    for it in (incident, relocation):
        it["ends_at"] = now + timedelta(hours=9)
    state = {
        "g94": _entry(now - timedelta(days=83)),
        "g72": _entry(now - timedelta(days=7)),
    }
    rendered: list[list[str]] = []
    saved: list[dict[str, dict[str, Any]]] = []

    def fake_make_rss(items: list[FeedItem], *args: Any, **kwargs: Any) -> str:
        rendered.append([it["title"] for it in items])
        return ""

    def fake_save_state(st: dict[str, dict[str, Any]], deletions: set[str] | None = None) -> None:
        saved.append(st)

    with patch.object(bf, "_invoke_collect_items", return_value=[incident, relocation]), \
         patch.object(bf, "_load_state", return_value=state), \
         patch.object(bf, "_make_rss", side_effect=fake_make_rss), \
         patch.object(bf, "_save_state", side_effect=fake_save_state), \
         patch.object(bf, "atomic_write", MagicMock()), \
         patch("src.build_feed.validate_path", MagicMock()), \
         patch("src.build_feed.write_feed_health_report", MagicMock()), \
         patch("src.build_feed.write_feed_health_json", MagicMock()):
        assert bf.main() == 0

    assert rendered[0] == ["94A: Verkehrsunfall", "72A: Kraftwerk Simmering"]
    assert "last_seen" in saved[0]["g94"] and "last_seen" in saved[0]["g72"]
