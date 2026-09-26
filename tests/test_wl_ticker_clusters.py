"""One WL incident, one slot (operator decision 2026-09-26).

Live on 2026-09-26, ``docs/feed.xml``: line 62 took three of the ten slots
with three display-board tickers published within 86 seconds::

    15:10:21  62: ÖBB Bauarbeiten Betrieb ab Kliebergasse
    15:10:51  62: Züge halte bei Linie 18, Richtung Burggasse
    15:11:47  62: ÖBB Bauarbeiten Kein Betrieb

Over 693 feed versions 242 carried such a group. The shapes below are real
WL cache entries of September 2026 (``cache/wl_9d709a/events.json``).

Mutations checked against this file (each one caught, by the test named):

* the window is widened tenfold → ``test_a_ticker_eleven_minutes_later_stays_apart``.
* the first cause wins instead of the most frequent → ``test_the_most_frequent_cause_names_the_item``.
* the coverage check is dropped → ``test_a_long_message_absorbs_what_the_tickers_repeat``.
* the stock sentence stays beside concrete consequences → ``test_the_most_frequent_cause_names_the_item``.
* the consequences are joined as sentences → ``test_the_feed_shows_all_three_consequences``.
* the merge is not called from ``main`` → ``test_main_merges_before_the_slots_are_filled``.
* the display arrows are kept → ``test_an_arrow_does_not_hide_the_cause``.
* the title's cause stays in front of a consequence → ``test_the_title_cause_is_not_repeated``.
* an open end is closed by a sibling's end → ``test_an_open_end_stays_open``.
* the members are not ordered by time → ``test_the_order_comes_from_the_times_not_the_list``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

T0 = datetime(2026, 9, 26, 13, 10, 21, tzinfo=UTC)  # 15:10:21 in Vienna
END = datetime(2026, 9, 26, 21, 59, 59, tzinfo=UTC)


def _wl(
    title: str,
    description: str,
    seconds: int = 0,
    *,
    guid: str | None = None,
    category: str = "Störung",
    source: str = "Wiener Linien",
    ends_at: datetime | None = END,
    start: datetime = T0,
) -> FeedItem:
    when = start + timedelta(seconds=seconds)
    return {
        "source": source,
        "category": category,
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid or f"g-{title}",
        "pubDate": when,
        "starts_at": when,
        "ends_at": ends_at,
        "_identity": f"wl|{title}",
    }


LINE_62 = [
    _wl("62: ÖBB Bauarbeiten Betrieb ab Kliebergasse", "ÖBB Bauarbeiten\nBetrieb ab Kliebergasse", 0, guid="f1211a44"),
    _wl("62: Züge halte bei Linie 18, Richtung Burggasse", "Züge halte bei\nLinie 18, Richtung Burggasse", 30, guid="e45a951e"),
    _wl("62: ÖBB Bauarbeiten Kein Betrieb", "ÖBB Bauarbeiten\nKein Betrieb", 86, guid="6a1dca08"),
]


def _merged(items: list[FeedItem]) -> list[tuple[str, str]]:
    return [(str(it["title"]), str(it["description"])) for it in bf._merge_wl_ticker_clusters(list(items))]


def test_the_three_tickers_of_line_62_become_one_item() -> None:
    (item,) = bf._merge_wl_ticker_clusters(list(LINE_62))
    assert item["title"] == "62: ÖBB Bauarbeiten"
    assert item["description"] == (
        "Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein Betrieb."
    )
    # The first-published ticker leads: its identity keeps first_seen.
    assert (item["guid"], item["_identity"], item["pubDate"]) == (
        "f1211a44",
        "wl|62: ÖBB Bauarbeiten Betrieb ab Kliebergasse",
        T0,
    )
    assert item["ends_at"] == END


def test_the_order_comes_from_the_times_not_the_list() -> None:
    (item,) = bf._merge_wl_ticker_clusters(list(reversed(LINE_62)))
    assert (item["guid"], item["description"]) == (
        "f1211a44",
        "Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein Betrieb.",
    )


def test_the_feed_shows_all_three_consequences() -> None:
    merged = bf._merge_wl_ticker_clusters(list(LINE_62))
    xml = bf._make_rss(merged, T0 + timedelta(hours=1), {}, lang="de")
    assert xml.count("<item>") == 1
    assert "<![CDATA[62: ÖBB Bauarbeiten]]>" in xml
    assert "Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein Betrieb." in xml


def test_a_ticker_eleven_minutes_later_stays_apart() -> None:
    later = _wl("62: ÖBB Bauarbeiten Betrieb ab Oper", "ÖBB Bauarbeiten\nBetrieb ab Oper", 11 * 60)
    titles = [title for title, _ in _merged([*LINE_62, later])]
    assert titles == ["62: ÖBB Bauarbeiten", "62: ÖBB Bauarbeiten Betrieb ab Oper"]


@pytest.mark.parametrize(
    "other",
    [
        _wl("18: ÖBB Bauarbeiten Betrieb ab Stadion", "ÖBB Bauarbeiten\nBetrieb ab Stadion", 10),  # another line
        _wl("62/18: ÖBB Bauarbeiten Kein Betrieb", "ÖBB Bauarbeiten\nKein Betrieb", 10),  # other lines
        _wl("62: Gleisbauarbeiten Kliebergasse", "Wegen Bauarbeiten …", 10, category="Hinweis"),  # a notice
        _wl("62: Stammstrecke", "Umleitung", 10, source="ÖBB"),  # not WL
    ],
)
def test_other_lines_notices_and_sources_stay_apart(other: FeedItem) -> None:
    single = LINE_62[0]
    assert _merged([single, other]) == [(single["title"], single["description"]), (other["title"], other["description"])]


def test_a_long_message_absorbs_what_the_tickers_repeat() -> None:
    long_text = (
        "Linie 49: Kein Betrieb zwischen Hütteldorfer Straße U und Urban-Loritz-Platz. "
        "Die Züge fahren ab Hütteldorfer Straße bis Joachimsthalerplatz."
    )
    items = [
        _wl("49: Gleisschaden", long_text, 0),
        _wl("49: Gleisschaden Betrieb ab Hütteldorfer Straße", "Gleisschaden Betrieb ab Hütteldorfer Straße >", 0),
        _wl("49: Gleisschaden Betrieb ab Urban-Loritz-Platz", "Gleisschaden Betrieb ab Urban-Loritz-Platz", 0),
    ]
    assert _merged(items) == [
        (
            "49: Gleisschaden",
            "Kein Betrieb zwischen Hütteldorfer Straße U und Urban-Loritz-Platz. "
            "Die Züge fahren ab Hütteldorfer Straße bis Joachimsthalerplatz.",
        )
    ]


def test_the_stock_sentence_stays_when_it_is_all_there_is() -> None:
    items = [
        _wl("37: Fremder Verkehrsunfall", "Linie 37: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."),
        _wl("37: Fahrtbehinderung Fremder Verkehrsunfall", "Fahrtbehinderung Fremder Verkehrsunfall"),
    ]
    assert _merged(items) == [
        ("37: Fremder Verkehrsunfall", "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.")
    ]


def test_the_most_frequent_cause_names_the_item() -> None:
    items = [
        _wl("6: Fremder Verkehrsunfall", "Linie 6: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."),
        _wl("6: Fahrtbehinderung wegen Rettungseinsatz", "Fahrtbehinderung wegen Rettungseinsatz"),
        _wl("6: Fremdunfall Betrieb ab Matzleinsdorfer Platz", "Fremdunfall Betrieb ab Matzleinsdorfer Platz"),
        _wl("6: Fremdunfall Betrieb ab Quellenplatz", "Fremdunfall Betrieb ab Quellenplatz"),
        _wl("6: Fremdunfall Züge halten bei der Linie O", "Fremdunfall Züge halten bei der Linie O"),
    ]
    assert _merged(items) == [
        (
            "6: Fremdunfall",
            # Another cause keeps WL's wording; the stock sentence gives way.
            "Fremder Verkehrsunfall; Fahrtbehinderung wegen Rettungseinsatz; Betrieb ab Matzleinsdorfer Platz; "
            "Betrieb ab Quellenplatz; Züge halten bei der Linie O.",
        )
    ]


def test_without_any_cause_the_first_ticker_keeps_its_title() -> None:
    items = [
        _wl("26E: Busse halten Bessemerstraße 1-3", "Busse halten Bessemerstraße 1-3"),
        _wl("26E: Busse halten auf Hauptfahrbahn", "Busse halten auf Hauptfahrbahn", 1),
        _wl("26E: Busse halten Hoßplatz 11", "Busse halten Hoßplatz 11", 2),
    ]
    assert _merged(items) == [
        ("26E: Busse halten Bessemerstraße 1-3", "Busse halten auf Hauptfahrbahn; Busse halten Hoßplatz 11.")
    ]


def test_the_display_line_above_the_title_is_the_cause() -> None:
    items = [
        _wl("12A: Betrieb ab Johnstraße U", "Gleisbauarbeiten\nBetrieb ab Johnstraße U"),
        _wl("12A: Betrieb ab Schweglerstraße 19-21", "Gleisbauarbeiten\nBetrieb ab Schweglerstraße 19-21"),
    ]
    assert _merged(items) == [("12A: Gleisbauarbeiten", "Betrieb ab Johnstraße U; Betrieb ab Schweglerstraße 19-21.")]


def test_an_arrow_does_not_hide_the_cause() -> None:
    items = [
        _wl("49: Betrieb ab Hütteldorfer Straße mit Linie 46", "Gleisbauarbeiten\nBetrieb ab Hütteldorfer Straße > mit Linie 46"),
        _wl("49: Betrieb ab Urban-Loritz-Platz mit Linie 52", "Gleisbauarbeiten\nBetrieb ab Urban-Loritz-Platz mit Linie 52"),
    ]
    assert _merged(items) == [
        (
            "49: Gleisbauarbeiten",
            "Betrieb ab Hütteldorfer Straße mit Linie 46; Betrieb ab Urban-Loritz-Platz mit Linie 52.",
        )
    ]


def test_the_title_cause_is_not_repeated() -> None:
    items = [
        _wl("43: Stromstörung Betrieb ab Rosensteingasse", "Stromstörung Betrieb ab Rosensteingasse"),
        _wl("43: Stromstörung Züge fahren bis Johann-Nepomuk-Berger-Platz", "Stromstörung Züge fahren bis Johann-Nepomuk-Berger-Platz", 1),
    ]
    assert _merged(items) == [
        ("43: Stromstörung", "Betrieb ab Rosensteingasse; Züge fahren bis Johann-Nepomuk-Berger-Platz.")
    ]


def test_an_open_end_stays_open() -> None:
    items = [_wl("62: ÖBB Bauarbeiten Kein Betrieb", "ÖBB Bauarbeiten\nKein Betrieb", ends_at=None), LINE_62[0]]
    (item,) = bf._merge_wl_ticker_clusters(items)
    assert item["ends_at"] is None


def test_a_single_ticker_is_untouched() -> None:
    items = [LINE_62[0]]
    assert bf._merge_wl_ticker_clusters(items) is items


def test_main_merges_before_the_slots_are_filled() -> None:
    rendered: list[list[str]] = []

    def fake_make_rss(items: list[FeedItem], *args: Any, **kwargs: Any) -> str:
        rendered.append([str(it["title"]) for it in items])
        return ""

    start = datetime.now(UTC) - timedelta(minutes=5)
    until = datetime.now(UTC) + timedelta(hours=6)
    live = [
        _wl(str(item["title"]), str(item["description"]), offset, start=start, ends_at=until)
        for item, offset in zip(LINE_62, (0, 30, 86), strict=True)
    ]
    with patch.object(bf, "_invoke_collect_items", return_value=live), \
         patch.object(bf, "_load_state", return_value={}), \
         patch.object(bf, "_make_rss", side_effect=fake_make_rss), \
         patch.object(bf, "_save_state", MagicMock()), \
         patch.object(bf, "atomic_write", MagicMock()), \
         patch("src.build_feed.validate_path", MagicMock()), \
         patch("src.build_feed.write_feed_health_report", MagicMock()), \
         patch("src.build_feed.write_feed_health_json", MagicMock()):
        assert bf.main() == 0
    assert rendered[0] == ["62: ÖBB Bauarbeiten"]
