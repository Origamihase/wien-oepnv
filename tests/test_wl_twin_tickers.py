"""One ticker sent for several lines takes one slot (fund B, 2026-10-02).

At 01:00:12 WL sent "Busse halten Laxenburger Straße 66" for N65 and for N66.
Grouped by line, the stop stood in two of the ten slots: "N65: Busse halten
Laxenburger Straße 66" and "N66: Bauarbeiten" with the same stop beside
Salvatorianerplatz.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_types import FeedItem


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(title: str, description: str, guid: str, start: str, end: str) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": _at(start),
        "starts_at": _at(start),
        "ends_at": _at(end),
        "_identity": f"wl|{guid}",
    }


def _shown(items: list[FeedItem]) -> list[tuple[str, str]]:
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read)))
    return [(str(it["title"]), str(it["description"])) for it in bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))]


_N65 = _wl("N65: Busse halten Laxenburger Straße 66", "Bauarbeiten\nBusse halten Laxenburger Straße 66",
           "46039872", "2026-10-02T01:00:12+02:00", "2026-10-02T05:00:00+02:00")
_N66 = _wl("N66: Busse halten Laxenburger Straße 66", "Bauarbeiten\nBusse halten Laxenburger Straße 66",
           "de5bef89", "2026-10-02T01:00:12+02:00", "2026-10-02T05:00:00+02:00")
_N66_SALVATOR = _wl("N66: Busse halten Salvatorianerplatz", "Bauarbeiten\nBusse halten Salvatorianerplatz",
                    "24aa68a1", "2026-10-02T01:00:14+02:00", "2026-10-02T04:20:00+02:00")


def test_the_live_case_takes_one_slot_and_labels_what_only_n66_announced() -> None:
    assert _shown([_N65, _N66, _N66_SALVATOR]) == [
        ("N65/N66: Bauarbeiten", "Busse halten Laxenburger Straße 66; N66: Busse halten Salvatorianerplatz."),
    ]


def test_twins_alone_become_one_entry_under_both_lines() -> None:
    shown = _shown([_N66, _N65])
    assert len(shown) == 1
    assert shown[0][0].startswith("N65/N66: ")
    assert "N65:" not in shown[0][1] and "N66:" not in shown[0][1]


def test_different_texts_of_different_lines_stay_apart() -> None:
    other = _wl("N65: Busse halten Gußriegelstraße", "Bauarbeiten\nBusse halten Gußriegelstraße",
                "aa11bb22", "2026-10-02T01:00:12+02:00", "2026-10-02T05:00:00+02:00")
    titles = sorted(title for title, _ in _shown([other, _N66_SALVATOR]))
    assert titles == ["N65: Busse halten Gußriegelstraße", "N66: Busse halten Salvatorianerplatz"]


def test_the_same_text_outside_the_window_stays_apart() -> None:
    later = _wl("N65: Busse halten Laxenburger Straße 66", "Bauarbeiten\nBusse halten Laxenburger Straße 66",
                "cc33dd44", "2026-10-02T01:30:12+02:00", "2026-10-02T05:00:00+02:00")
    titles = [title for title, _ in _shown([later, _N66])]
    assert sorted(titles) == ["N65: Busse halten Laxenburger Straße 66", "N66: Busse halten Laxenburger Straße 66"]


def test_a_group_with_a_long_message_is_not_joined() -> None:
    long_message = _wl("N66: Bauarbeiten", "Linie N66: Busse halten Laxenburger Straße 66. Grund: Bauarbeiten.",
                       "ee55ff66", "2026-10-02T01:00:10+02:00", "2026-10-02T05:00:00+02:00")
    titles = [title for title, _ in _shown([_N65, _N66, long_message])]
    assert any(title.startswith("N65:") for title in titles)
    assert not any(title.startswith("N65/N66") for title in titles)


# --- Long messages sent once per line (2026-10-03) ---------------------------

_STOCK = "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."


def _long(line: str, cause: str, guid: str, start: str = "2026-07-19T11:11:30+02:00", text: str = _STOCK) -> FeedItem:
    return _wl(f"{line}: {cause}", f"Linie {line}: {text}", guid, start, "2026-07-19T12:41:00+02:00")


def test_the_same_long_message_of_two_lines_takes_one_slot() -> None:
    shown = _shown([_long("6", "Schadhaftes Fahrzeug", "a1"), _long("18", "Schadhaftes Fahrzeug", "b2", "2026-07-19T11:11:45+02:00")])
    assert shown == [("6/18: Schadhaftes Fahrzeug", _STOCK)]


def test_four_lines_of_one_level_crossing_take_one_slot() -> None:
    lines = ["67B", "16A", "67A", "17A"]
    shown = _shown([_long(line, "Störung an einem Bahnübergang", f"g{n}") for n, line in enumerate(lines)])
    assert shown == [("16A/17A/67A/67B: Störung an einem Bahnübergang", _STOCK)]


def test_long_messages_with_different_texts_stay_apart() -> None:
    shown = _shown([
        _long("44", "Schadhaftes Fahrzeug", "c3", text="Unregelmäßige Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug."),
        _long("60", "Schadhaftes Fahrzeug", "d4", text="Verspätungen in beiden Richtungen. Grund: Schadhaftes Fahrzeug."),
    ])
    assert sorted(title for title, _ in shown) == ["44: Schadhaftes Fahrzeug", "60: Schadhaftes Fahrzeug"]


def test_the_same_long_message_outside_the_window_stays_apart() -> None:
    shown = _shown([_long("6", "Verkehrsunfall", "e5"), _long("18", "Verkehrsunfall", "f6", "2026-07-19T11:31:30+02:00")])
    assert sorted(title for title, _ in shown) == ["18: Verkehrsunfall", "6: Verkehrsunfall"]


def test_a_long_message_twin_with_a_ticker_only_one_line_sent_stays_apart() -> None:
    ticker = _wl("6: Betrieb ab Westbahnhof", "Schadhaftes Fahrzeug\nBetrieb ab Westbahnhof",
                 "g7", "2026-07-19T11:12:00+02:00", "2026-07-19T12:41:00+02:00")
    shown = _shown([_long("6", "Schadhaftes Fahrzeug", "a1"), ticker, _long("18", "Schadhaftes Fahrzeug", "b2")])
    assert sorted(title.split(":")[0] for title, _ in shown) == ["18", "6"]
