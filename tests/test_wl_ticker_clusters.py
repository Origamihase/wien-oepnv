"""One WL incident, one slot (operator decision 2026-09-26).

Live on 2026-09-26, ``docs/feed.xml``: line 62 took three of the ten slots
with three display-board tickers published within 86 seconds::

    15:10:21  62: ÖBB Bauarbeiten Betrieb ab Kliebergasse
    15:10:51  62: Züge halte bei Linie 18, Richtung Burggasse
    15:11:47  62: ÖBB Bauarbeiten Kein Betrieb

Over 693 feed versions 242 carried such a group. The shapes below are real
WL cache entries of September 2026 (``cache/wl_9d709a/events.json``), read
the way the feed reads them (``_read``): ``_post_filter_wl`` strips a long
message's ``Linie 48A:`` label before the merge sees it. Tests on the raw
cache shapes missed that, and on 2026-09-27 the feed showed "48A:
Falschparker" over "Grund: Fremder Verkehrsunfall".

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
* a long message is told by its label, not its sentences → ``test_a_long_message_stands_as_it_would_alone``.
* a long message with only the stock sentence stands alone → ``test_the_most_frequent_cause_names_the_item``.
* the earliest start of the group wins → ``test_the_start_stays_the_leads``.
* a ticker leads on a tie with the long message → ``test_a_long_message_absorbs_what_the_tickers_repeat``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
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
    identity: str | None = None,
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
        "_identity": identity or f"wl|{title}",
    }


LINE_62 = [
    _wl("62: ÖBB Bauarbeiten Betrieb ab Kliebergasse", "ÖBB Bauarbeiten\nBetrieb ab Kliebergasse", 0, guid="f1211a44"),
    _wl("62: Züge halte bei Linie 18, Richtung Burggasse", "Züge halte bei\nLinie 18, Richtung Burggasse", 30, guid="e45a951e"),
    _wl("62: ÖBB Bauarbeiten Kein Betrieb", "ÖBB Bauarbeiten\nKein Betrieb", 86, guid="6a1dca08"),
]


def _read(items: list[FeedItem]) -> list[FeedItem]:
    """*items* as ``read_cache_wl`` hands them to the feed."""
    return cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))


def _merge(items: list[FeedItem]) -> list[FeedItem]:
    return bf._merge_wl_ticker_clusters(_read(items))


def _merged(items: list[FeedItem]) -> list[tuple[str, str]]:
    return [(str(it["title"]), str(it["description"])) for it in _merge(items)]


def _built(items: list[FeedItem]) -> list[FeedItem]:
    """What ``main`` makes of *items*: read, deduplicated, fuzzy-merged, merged."""
    deduped = bf._dedupe_items(_read(items))
    return bf._merge_wl_ticker_clusters(cast(list[FeedItem], deduplicate_fuzzy(cast(list[dict[str, Any]], deduped))))


def test_the_three_tickers_of_line_62_become_one_item() -> None:
    (item,) = _merge(LINE_62)
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
    (item,) = _merge(list(reversed(LINE_62)))
    assert (item["guid"], item["description"]) == (
        "f1211a44",
        "Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein Betrieb.",
    )


def test_the_feed_shows_all_three_consequences() -> None:
    merged = _merge(LINE_62)
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
        _wl("49: Gleisschaden", long_text, 0, guid="z-long"),
        _wl("49: Gleisschaden Betrieb ab Hütteldorfer Straße", "Gleisschaden Betrieb ab Hütteldorfer Straße >", 0, guid="a-1"),
        _wl("49: Gleisschaden Betrieb ab Urban-Loritz-Platz", "Gleisschaden Betrieb ab Urban-Loritz-Platz", 0, guid="a-2"),
    ]
    # Published in the same second, the long message leads and keeps its identity.
    assert [it["guid"] for it in _merge(items)] == ["z-long"]
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


MORNING = datetime(2026, 9, 27, 3, 26, tzinfo=UTC)  # 05:26 in Vienna
MORNING_END = datetime(2026, 9, 27, 21, 55, tzinfo=UTC)
# Live 2026-09-27 05:31: the feed read "48A: Falschparker" over this text.
LONG_48A = (
    "Linie 48A: Fahrtbehinderung in Richtung Klinik Penzing. Voraussichtliche Dauer: 06:00 Uhr. "
    "Grund: Fremder Verkehrsunfall im Bereich Lerchenfelder Gürtel # Koppstraße ."
)
# Some long messages never carry the label.
LONG_48A_UNLABELLED = (
    "Die Linie 48A wird in Richtung Klinik Penzing umgeleitet. Voraussichtliche Dauer: 06:00 Uhr. "
    "Grund: Fremder Verkehrsunfall im Bereich Lerchenfelder Gürtel."
)


@pytest.mark.parametrize("long_text", [LONG_48A, LONG_48A_UNLABELLED])
@pytest.mark.parametrize("ticker_offset", [17, -60])  # 48A: the ticker 17 s later; 69A on 24.09.: a minute earlier
def test_a_long_message_stands_as_it_would_alone(long_text: str, ticker_offset: int) -> None:
    long_message = _wl("48A: Fremder Verkehrsunfall", long_text, start=MORNING, ends_at=MORNING_END, guid="7da5c613")
    ticker = _wl(
        "48A: Fahrtbehinderung Falschparker", "Fahrtbehinderung\nFalschparker", ticker_offset, start=MORNING, guid="05cf9172"
    )
    (alone,) = _read([long_message])
    (item,) = _merge([long_message, ticker])
    assert (item["title"], item["description"]) == (alone["title"], alone["description"])
    xml = bf._make_rss([item], MORNING + timedelta(minutes=5), {}, lang="de")
    assert "<![CDATA[48A: Fremder Verkehrsunfall]]>" in xml
    assert "Grund: Fremder Verkehrsunfall im Bereich Lerchenfelder Gürtel" in xml
    assert "Falschparker" not in xml


def test_the_start_stays_the_leads() -> None:
    """Live 2026-09-27 06:01: "25: Schadhafter Zug … [23.09.2026 – 27.09.2026]".

    WL reused a display ticker of 23.09. for the breakdown; the fuzzy merge
    joined it with the new ticker and kept its old start.
    """
    breakdown = datetime(2026, 9, 27, 3, 48, tzinfo=UTC)  # 05:48 in Vienna
    reused = datetime(2026, 9, 22, 22, 0, 28, tzinfo=UTC)  # 23.09. 00:00:28 in Vienna
    tickers_end = datetime(2026, 9, 27, 4, 52, tzinfo=UTC)
    items = [
        _wl(
            "25: Schadhafter Zug Betrieb ab Kagran",
            "Schadhafter Zug\nBetrieb ab Kagran >",
            start=reused,
            ends_at=tickers_end,
            guid="034925fe",
            identity="wl|störung|L=25|D=2026-09-23|TK=schadhafter zug betrieb ab kagran",
        ),
        _wl(
            "25: Schadhaftes Fahrzeug",
            "Linie 25: Kein Betrieb zwischen Kagran U und Donauspital U. Weichen Sie ersatzweise auf die Linien "
            "U2 und 26A aus. Voraussichtliche Dauer: 06:20 Uhr. Grund: Schadhaftes Fahrzeug im "
            "Haltestellenbereich Langobardenstraße, Kapellenweg.",
            start=breakdown,
            ends_at=MORNING_END,
            guid="76ec3528",
            identity="wl|störung|L=25|D=2026-09-27|TK=25 schadhaftes fahrzeug",
        ),
        _wl(
            "25: Schadhafter Zug Betrieb ab Donauspital",
            "Schadhafter Zug\nBetrieb ab Donauspital",
            293,
            start=breakdown,
            ends_at=tickers_end,
            guid="7c5ca1cd",
            identity="wl|störung|L=25|D=2026-09-27|TK=schadhafter zug betrieb ab donauspital",
        ),
    ]
    # As live: the fuzzy merge keeps the reused ticker, with its old start.
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(_read(items))))
    assert [it["starts_at"] for it in fuzzy if it["guid"] == "034925fe"] == [reused]
    (item,) = _built(items)
    assert (item["title"], item["starts_at"]) == ("25: Schadhaftes Fahrzeug", breakdown)
    xml = bf._make_rss([item], breakdown + timedelta(minutes=13), {}, lang="de")
    assert "[Am 27.09.2026]" in xml


def test_an_open_end_stays_open() -> None:
    items = [_wl("62: ÖBB Bauarbeiten Kein Betrieb", "ÖBB Bauarbeiten\nKein Betrieb", ends_at=None), LINE_62[0]]
    (item,) = _merge(items)
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
