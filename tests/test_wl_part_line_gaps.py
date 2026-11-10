"""Two gaps of the join of part-line WL disruptions, closed on 2026-11-10.

On 2026-11-09 from 12:30 to 14:30 Vienna time (``docs/feed.xml``) one damaged
overhead line took four of the ten slots: "2/12/31: Beschädigte Oberleitung
durch einen LKW" and the display tickers "12: Fahrleitungsgebr.", "31:
Fahrleitungsgebr." and "2: Fahrleitungsgebr". On 2026-10-23 and 2026-11-10
the display tickers named every line and WL's numbered message only some.
The shapes below are WL cache entries of those days
(``cache/wl_9d709a/events.json``), read as the feed reads them.

Mutations checked against this file (each one caught, by the test named):

* the agent behind "durch" stays in the entry's compared cause → ``test_the_damaged_overhead_line_takes_one_slot``.
* a cut cause is not written out → ``test_the_damaged_overhead_line_takes_one_slot``.
* only a numbered entry may take others → ``test_the_numbered_message_of_some_lines_joins_the_tickers``.
* WL's stock sentence is appended → ``test_the_numbered_message_of_some_lines_joins_the_tickers``.
* two numbered messages join → ``test_two_numbered_messages_keep_their_slots``.
* a cut cause opening two groups is written out → ``test_a_cut_cause_of_two_groups_stays_as_written``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy, member_guids
from src.feed_build.wl_tickers import _cause_key
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(
    title: str, description: str, guid: str, start: str, end: str | None, *, number: str | None = None
) -> FeedItem:
    item: FeedItem = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": _at(start),
        "starts_at": _at(start),
        "ends_at": _at(end) if end else None,
        "_identity": f"wl|{guid}",
    }
    if number:
        item["_wl_ids"] = [number]
    return item


def _built(items: list[FeedItem]) -> list[FeedItem]:
    """What ``main`` makes of *items*: read, deduplicated, fuzzy-merged, merged."""
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read)))
    return bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))


OVERHEAD_TEXT = (
    "Betrieb nur zwischen Dornbach und Rebhanngasse. Weiterfahrt bis Bruno-Marek-Allee. Linie 12: Umleitung in "
    "beiden Richtungen zwischen Bruno-Marek-Allee und Wallensteinplatz über die Strecke der Linien O und 5. "
    "Linie 31: Kein Betrieb zwischen Floridsdorf S U und Wexstraße. Ersatz: Linien U4, U6,. Voraussichtliche "
    "Dauer: 14:00 Uhr. Grund: Fahrleitungsgebrechen durch LKW im Haltestellenbereich Friedrich-Engels-Platz."
)
OVERHEAD = _wl("2/12/31: Beschädigte Oberleitung durch einen LKW", OVERHEAD_TEXT, "be0a3984",
               "2026-11-09T11:57:00+01:00", None, number="I20261109-0018")
OVERHEAD_TICKERS = [
    _wl("12: Fahrleitungsgebr. Betrieb ab Bruno Marek Allee", "Fahrleitungsgebr.\nBetrieb ab Bruno Marek Allee",
        "3fb4c35d", "2026-11-09T12:10:23+01:00", "2026-11-09T13:09:00+01:00"),
    _wl("12: Fahrleitungsgebr. Betrieb ab Wallensteinplatz", "Fahrleitungsgebr.\nBetrieb ab Wallensteinplatz",
        "180ecf15", "2026-11-09T12:10:54+01:00", "2026-11-09T13:10:00+01:00"),
    _wl("2: Fahrleitungsgebr. Betrieb ab Am Tabor", "Fahrleitungsgebr.\nBetrieb ab Am Tabor",
        "ca3be0ac", "2026-11-09T12:02:52+01:00", "2026-11-09T13:00:00+01:00"),
    _wl("2: Fahrtbehinderung wegen Fahrleitungsgebr.", "Fahrtbehinderung\nwegen Fahrleitungsgebr.",
        "e01f996b", "2026-11-09T12:01:22+01:00", "2026-11-09T12:59:00+01:00"),
    _wl("31: Fahrleitungsgebr. Betrieb ab Floridsdorf S U", "Fahrleitungsgebr.\nBetrieb ab Floridsdorf S U",
        "6a768d81", "2026-11-09T12:04:20+01:00", "2026-11-09T13:03:00+01:00"),
    _wl("31: Fahrleitungsgebr. Betrieb ab Wexstraße", "Fahrleitungsgebr.\nBetrieb ab Wexstraße",
        "60af6998", "2026-11-09T12:05:52+01:00", "2026-11-09T13:04:00+01:00"),
]


def test_the_damaged_overhead_line_takes_one_slot() -> None:
    # The text speaks of every line, so no display adds to it.
    (alone,) = _built([OVERHEAD])
    (item,) = _built([*OVERHEAD_TICKERS, OVERHEAD])
    assert (item["guid"], item["title"], item["description"]) == (
        "be0a3984", "2/12/31: Beschädigte Oberleitung durch einen LKW", alone["description"]
    )
    assert set(member_guids(item)) >= {"be0a3984", "3fb4c35d", "ca3be0ac", "6a768d81"}


def test_a_cut_cause_is_written_out() -> None:
    assert _cause_key("Fahrleitungsgebr.") == _cause_key("Fahrleitungsgebr") == _cause_key("Beschädigte Oberleitung")
    assert _cause_key("Rettungseinsatz") == "rettungseinsatz"


def test_a_cut_cause_of_two_groups_stays_as_written() -> None:
    # "Schadhaft" opens "schadhaftes fahrzeug" and other groups; too short to tell.
    assert _cause_key("Schadh.") == "schadh."
    assert _cause_key("Bau") == "bau"


STOCK = "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."
# 2026-11-10 at 07:31: the displays of 37, 40, 41 and 42 against WL's "41: Verkehrsunfall".
ACCIDENT_TICKERS = [
    _wl("37: Fahrtbehinderung Verkehrsunfall",
        "Fahrtbehinderung\nVerkehrsunfall", "9a256ddc", "2026-11-10T06:13:59+01:00", "2026-11-10T08:13:00+01:00"),
    _wl("37: Verkehrsunfall Betrieb ab Nußdorfer Straße",
        "Verkehrsunfall\nBetrieb ab Nußdorfer Straße", "14043cd0", "2026-11-10T06:13:01+01:00", "2026-11-10T08:11:00+01:00"),
    _wl("37: Verkehrsunfall Einstieg am Währinger Gürtel",
        "Verkehrsunfall\nEinstieg am Währinger Gürtel", "13d3426d", "2026-11-10T06:13:29+01:00", "2026-11-10T08:12:00+01:00"),
    _wl("40: Fahrtbehinderung Verkehrsunfall",
        "Fahrtbehinderung\nVerkehrsunfall", "171280fe", "2026-11-10T06:17:59+01:00", "2026-11-10T08:17:00+01:00"),
    _wl("40: Verkehrsunfall Betrieb ab Währinger Straße, Volksoper",
        "Verkehrsunfall\nBetrieb ab Währinger Straße, Volksoper", "99ff4fc3", "2026-11-10T06:18:31+01:00", "2026-11-10T08:17:00+01:00"),
    _wl("40: Verkehrsunfall Einstieg am Währinger Gürtel",
        "Verkehrsunfall\nEinstieg am Währinger Gürtel", "bdd41d3a", "2026-11-10T06:19:30+01:00", "2026-11-10T08:18:00+01:00"),
    _wl("41: Fahrtbehinderung Verkehrsunfall",
        "Fahrtbehinderung\nVerkehrsunfall", "1e687669", "2026-11-10T06:20:30+01:00", "2026-11-10T08:19:00+01:00"),
    _wl("41: Verkehrsunfall Betrieb ab Währinger Straße, Volksoper",
        "Verkehrsunfall\nBetrieb ab Währinger Straße, Volksoper", "4eae3c7c", "2026-11-10T06:20:32+01:00", "2026-11-10T08:20:00+01:00"),
    _wl("41: Verkehrsunfall Einstieg am Währinger Gürtel",
        "Verkehrsunfall\nEinstieg am Währinger Gürtel", "6365b0d4", "2026-11-10T06:21:00+01:00", "2026-11-10T08:20:00+01:00"),
    _wl("42: Verkehrsunfall Betrieb ab Währinger Straße, Volksoper",
        "Verkehrsunfall\nBetrieb ab Währinger Straße, Volksoper", "d3e45c39", "2026-11-10T06:22:02+01:00", "2026-11-10T08:21:00+01:00"),
    _wl("42: Verkehrsunfall Einstieg am Währinger Gürtel",
        "Verkehrsunfall\nEinstieg am Währinger Gürtel", "b2bf740d", "2026-11-10T06:23:01+01:00", "2026-11-10T08:22:00+01:00"),
]
ACCIDENT = _wl("41: Verkehrsunfall", STOCK, "b8938e94", "2026-11-10T06:07:00+01:00", None, number="I20261110-0002")


def test_the_numbered_message_of_some_lines_joins_the_tickers() -> None:
    (alone,) = _built(ACCIDENT_TICKERS)
    (item,) = _built([*ACCIDENT_TICKERS, ACCIDENT])
    assert (item["title"], item["description"]) == (alone["title"], alone["description"])
    assert str(item["title"]).startswith("37/40/41/42: Verkehrsunfall")
    assert "b8938e94" in member_guids(item)
    assert "unterschiedlichen Intervallen" not in str(item["description"])


def test_the_police_operation_of_2026_10_23_takes_one_slot() -> None:
    tickers = _wl("40/41/42: Polizeieinsatz Linien 40,41,42 Betrieb ab Währinger Str./ Volksoper",
                  "Polizeieinsatz\nLinien 40,41,42 Betrieb ab Währinger Str./ Volksoper >", "c649df8a",
                  "2026-10-23T16:20:43+02:00", "2026-10-23T17:19:00+02:00")
    message = _wl("40: Polizeieinsatz", STOCK, "a29e671a", "2026-10-23T16:14:00+02:00", None, number="I20261023-0037")
    (item,) = _built([tickers, message])
    assert str(item["title"]).startswith("40/41/42: Polizeieinsatz")
    assert "unterschiedlichen Intervallen" not in str(item["description"])


def test_two_numbered_messages_keep_their_slots() -> None:
    other = _wl("41: Verkehrsunfall", STOCK, "other", "2026-11-10T06:10:00+01:00", None, number="I20261110-0003")
    wide = _wl("37/40/41/42: Verkehrsunfall", "Linie 41: Kurzführung. Grund: Verkehrsunfall.", "wide",
               "2026-11-10T06:12:00+01:00", None, number="I20261110-0004")
    assert len(_built([wide, other])) == 2


def test_another_cause_keeps_its_slot_either_way() -> None:
    # 2026-11-10 11:31: "60: Falschparker" beside "10/60: Polizeieinsatz".
    police = _wl("10/60: Polizeieinsatz Betrieb ab Hietzing", "Polizeieinsatz\nBetrieb ab Hietzing", "police",
                 "2026-11-10T11:17:00+01:00", "2026-11-10T12:17:00+01:00")
    parked = _wl("60: Falschparker", STOCK, "parked", "2026-11-10T11:12:00+01:00", None, number="I20261110-0011")
    assert len(_built([police, parked])) == 2
