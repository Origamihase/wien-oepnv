"""An all-clear only takes a feed slot that nothing else needs.

Operator decision 2026-09-25 (audit A.2): a running disruption matters more
than an all-clear ("Aufhebung Verkehrseinschränkung: …"), but an all-clear is
better than an empty slot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem


def _item(title: str, source: str = "ÖBB", category: str = "Störung") -> FeedItem:
    return {"title": title, "description": f"{title}.", "link": "", "source": source, "category": category}


@pytest.mark.parametrize(
    "title",
    [
        "Aufhebung Verkehrseinschränkung: St. Pölten Hauptbahnhof",
        "Aufhebung Streckenunterbrechung: Wien Meidling",
        "Update 5 (25.09.2026 10:48) Aufhebung Verkehrseinschränkung: St.Pölten",
        "REX 50: Aufhebung Verkehrseinschränkung: Wien Hütteldorf",
        "S 1/S 2/S 3/S 4/S 7: Aufhebung Streckenunterbrechung: Wien Praterstern",
        "aufhebung verkehrseinschränkung: Wien Handelskai",
    ],
)
def test_all_clear_titles_are_recognised(title: str) -> None:
    assert bf._is_all_clear(_item(title))


@pytest.mark.parametrize(
    "title",
    [
        "Verkehrseinschränkung: St. Pölten Hauptbahnhof",
        "S 1: Wien Leopoldau ↔ Deutsch Wagram",
        "N31: Stammersdorf ersatzlos aufgelassen",
        "U2: Klapprampensperre am 27.09.2026",
        "Aufhebungen im Nachtverkehr",
        "Wien Hauptbahnhof ↔ Wien Westbahnhof: Einschränkung nach Aufhebung",
        "",
    ],
)
def test_other_titles_are_not_all_clears(title: str) -> None:
    assert not bf._is_all_clear(_item(title))


def test_all_clear_moves_behind_the_whole_field() -> None:
    all_clear = _item("Aufhebung Verkehrseinschränkung: St. Pölten Hauptbahnhof")
    disruption = _item("16A: Rettungseinsatz", source="Wiener Linien")
    relocation = _item("72A: Kraftwerk Simmering", source="Wiener Linien", category="Hinweis")
    site = _item("Burggasse 67", source="Stadt Wien", category="Baustelle")

    ordered = bf._defer_all_clear_items([all_clear, disruption, relocation, site])

    assert [it["title"] for it in ordered] == [
        "16A: Rettungseinsatz",
        "72A: Kraftwerk Simmering",
        "Burggasse 67",
        "Aufhebung Verkehrseinschränkung: St. Pölten Hauptbahnhof",
    ]


def test_several_all_clears_keep_their_order() -> None:
    first = _item("Aufhebung Verkehrseinschränkung: Wien Handelskai")
    second = _item("Aufhebung Streckenunterbrechung: Wien Meidling")
    other = _item("U1: Weichenstörung", source="Wiener Linien")

    ordered = bf._defer_all_clear_items([first, other, second])

    assert ordered == [other, first, second]


def test_without_all_clear_the_list_is_untouched() -> None:
    items = [_item("16A: Rettungseinsatz"), _item("S 45: Wien Hütteldorf ↔ Wien Handelskai")]

    assert bf._defer_all_clear_items(items) is items


def test_main_fills_a_free_slot_with_the_all_clear() -> None:
    now = datetime.now(UTC)
    all_clear = _item("Aufhebung Verkehrseinschränkung: St. Pölten Hauptbahnhof")
    disruption = _item("16A: Rettungseinsatz", source="Wiener Linien")
    for guid, it, age in (("gA", all_clear, 5), ("gD", disruption, 40)):
        it["guid"] = guid
        it["pubDate"] = now - timedelta(minutes=age)
        it["ends_at"] = now + timedelta(hours=6)
    # The all-clear is newer: without the rule it would lead the feed.
    state = {
        "gA": {"first_seen": (now - timedelta(minutes=5)).isoformat()},
        "gD": {"first_seen": (now - timedelta(minutes=40)).isoformat()},
    }
    rendered: list[list[str]] = []

    def fake_make_rss(items: list[FeedItem], *args: Any, **kwargs: Any) -> str:
        rendered.append([it["title"] for it in items])
        return ""

    with patch.object(bf, "_invoke_collect_items", return_value=[all_clear, disruption]), \
         patch.object(bf, "_load_state", return_value=state), \
         patch.object(bf, "_make_rss", side_effect=fake_make_rss), \
         patch.object(bf, "_save_state", MagicMock()), \
         patch.object(bf, "atomic_write", MagicMock()), \
         patch("src.build_feed.validate_path", MagicMock()), \
         patch("src.build_feed.write_feed_health_report", MagicMock()), \
         patch("src.build_feed.write_feed_health_json", MagicMock()):
        assert bf.main() == 0

    # Behind the disruption, but still in the feed: a slot was free.
    assert rendered[0] == [
        "16A: Rettungseinsatz",
        "Aufhebung Verkehrseinschränkung: St. Pölten Hauptbahnhof",
    ]
