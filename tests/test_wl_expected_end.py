"""WL's "Voraussichtliche Dauer: <Ende>" reads "Voraussichtlich bis <Ende>".

WL's incident template names the expected END under a duration label
("Voraussichtliche Dauer: 14:10 Uhr."). On the TV slides of 06.10.2026 that
read like a duration of 14 hours. Every value behind the label in the WL
cache since July (942 distinct texts) is an end point, except "Nicht
absehbar". The values below are the real shapes from that history.

The EN feed renders the new sentence without the model, like the label it
replaced: "Expected until 14:10."
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")


def _summary(desc: str) -> str:
    item = cast(
        FeedItem,
        {
            "title": "U6: Rettungseinsatz",
            "description": desc,
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "t",
            "link": "",
        },
    )
    # Far ahead, so the clock time in the text has not passed whenever the
    # test runs (``_drop_passed_expected_end`` reads the real clock).
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2099, 10, 3, 6, 0, tzinfo=UTC),
        ends_at=datetime(2099, 10, 3, 7, 0, tzinfo=UTC),
    )
    return formatted.desc_text_truncated.split("[", 1)[0].strip()


@pytest.mark.parametrize(
    ("value", "rewritten"),
    [
        ("19:10 Uhr", "Voraussichtlich bis 19:10 Uhr"),
        ("16 Uhr", "Voraussichtlich bis 16 Uhr"),
        ("2:45 Uhr", "Voraussichtlich bis 2:45 Uhr"),
        ("13.50 Uhr", "Voraussichtlich bis 13.50 Uhr"),
        ("10:00", "Voraussichtlich bis 10:00"),
        ("ca. 22 Uhr", "Voraussichtlich bis ca. 22 Uhr"),
        ("circa 22:00 Uhr", "Voraussichtlich bis circa 22:00 Uhr"),
        # WL's own "bis" is not doubled.
        ("bis 22:00 Uhr", "Voraussichtlich bis 22:00 Uhr"),
        ("Bis Betriebsschluss", "Voraussichtlich bis Betriebsschluss"),
        ("Betriebsschluss", "Voraussichtlich bis Betriebsschluss"),
        ("Betriebsschluß", "Voraussichtlich bis Betriebsschluß"),
        ("Betriebschluss", "Voraussichtlich bis Betriebschluss"),
        ("Sonntag Betriebsschluss", "Voraussichtlich bis Sonntag Betriebsschluss"),
        ("Ende August", "Voraussichtlich bis Ende August"),
        ("Ende 2026", "Voraussichtlich bis Ende 2026"),
        ("Anfang September", "Voraussichtlich bis Anfang September"),
        ("Mitte Juli", "Voraussichtlich bis Mitte Juli"),
        ("31. August", "Voraussichtlich bis 31. August"),
        ("31.07.2026", "Voraussichtlich bis 31.07.2026"),
        (
            "Montag, 03. August 2026, 04:00 Uhr",
            "Voraussichtlich bis Montag, 03. August 2026, 04:00 Uhr",
        ),
        # WL's typos in the clock time.
        ("15;40 Uhr", "Voraussichtlich bis 15:40 Uhr"),
        ("12:15 Uht", "Voraussichtlich bis 12:15 Uhr"),
        ("06:30Uhr", "Voraussichtlich bis 06:30 Uhr"),
    ],
)
def test_end_point_becomes_until(value: str, rewritten: str) -> None:
    text = f"Voraussichtliche Dauer: {value}. Grund: Rettungseinsatz."
    assert build_feed._expected_end_sentence(text) == f"{rewritten}. Grund: Rettungseinsatz."


@pytest.mark.parametrize(
    "value",
    [
        # Not an end: the label is right as WL wrote it.
        "Nicht absehbar",
        # A real duration keeps its label too.
        "2 Stunden",
        "ca. 30 Minuten",
        "etwa zwei Wochen",
        "unbekannt",
    ],
)
def test_other_values_keep_the_label(value: str) -> None:
    text = f"Voraussichtliche Dauer: {value}. Grund: Feuerwehreinsatz."
    assert build_feed._expected_end_sentence(text) == text


def test_feed_text_says_until() -> None:
    summary = _summary(
        "Linie U6: Fahrtbehinderung in Richtung Floridsdorf. Voraussichtliche Dauer: "
        "19:10 Uhr. Grund: Rettungseinsatz im Haltestellenbereich Westbahnhof."
    )
    assert summary == (
        "Linie U6: Fahrtbehinderung in Richtung Floridsdorf. Voraussichtlich bis "
        "19:10 Uhr. Grund: Rettungseinsatz im Haltestellenbereich Westbahnhof."
    )


def test_shorter_sentence_keeps_the_reason_within_180() -> None:
    """Six characters shorter: the reason no longer falls to the cut (U4 Stadtpark, 2026)."""
    summary = _summary(
        "Die Linie U4 fährt derzeit in der Station Stadtpark in beiden Richtungen über "
        "Gleis 1. Voraussichtliche Dauer: 20:00 Uhr. Grund: Schadhaftes Fahrzeug im "
        "Haltestellenbereich Stadtpark."
    )
    assert summary.endswith("Voraussichtlich bis 20:00 Uhr. Grund: Schadhaftes Fahrzeug im Haltestellenbereich Stadtpark.")


@pytest.mark.parametrize(
    ("text", "record"),
    [
        (
            "Fahrtbehinderung in beiden Richtungen. Voraussichtlich bis 16:15 Uhr. "
            "Grund: Verkehrsunfall.",
            "Voraussichtlich bis 16:15 Uhr. Grund: Verkehrsunfall.",
        ),
        # Alone and last, it is a record of its own.
        (
            "Betrieb nur zwischen Schottentor U und Nußdorfer Straße U. Voraussichtlich bis 06:00 Uhr.",
            "Voraussichtlich bis 06:00 Uhr.",
        ),
        ("Kein Betrieb. Voraussichtlich bis 19. September, etwa 01:00 Uhr.",
         "Voraussichtlich bis 19. September, etwa 01:00 Uhr."),
    ],
)
def test_en_record_starts_at_the_expected_end(text: str, record: str) -> None:
    assert build_feed._split_label_record(text)[1] == record


@pytest.mark.parametrize(
    "text",
    [
        # Prose follows: not a table.
        "Voraussichtlich bis 14:10 Uhr. Weichen Sie ersatzweise auf die Linie 5 aus.",
        # Lower case is prose (ÖBB, WL's "Die Störung dauert voraussichtlich bis …").
        "Die Störung dauert voraussichtlich bis 23:30 Uhr.",
    ],
)
def test_en_prose_stays_prose(text: str) -> None:
    assert build_feed._split_label_record(text) == (text, "")


@pytest.mark.parametrize(
    ("record", "english"),
    [
        ("Voraussichtlich bis 14:10 Uhr.", "Expected until 14:10."),
        ("Voraussichtlich bis ca. 22 Uhr.", "Expected until approx. 22:00."),
        ("Voraussichtlich bis circa 22:00 Uhr.", "Expected until approx. 22:00."),
        ("Voraussichtlich bis Betriebsschluss.", "Expected until end of service."),
        ("Voraussichtlich bis Betriebsschluß.", "Expected until end of service."),
        ("Voraussichtlich bis Sonntag Betriebsschluss.", "Expected until Sunday end of service."),
        ("Voraussichtlich bis Ende August.", "Expected until end of August."),
        ("Voraussichtlich bis Mitte Juli.", "Expected until middle of July."),
        (
            "Voraussichtlich bis 19. September, etwa 01:00 Uhr.",
            "Expected until 19 September, approx. 01:00.",
        ),
        (
            "Voraussichtlich bis 16:15 Uhr. Grund: Verkehrsunfall.",
            "Expected until 16:15. Reason: traffic accident.",
        ),
    ],
)
def test_en_record_renders_expected_until(record: str, english: str) -> None:
    rendered = build_feed._render_record_table(
        record, source="Wiener Linien", category="Störung"
    )
    assert rendered == english


def test_station_wien_mitte_keeps_its_name_in_a_record() -> None:
    rendered = build_feed._render_record_table(
        "Von: Wien Mitte. Nach: Landstraße.", source="Wiener Linien", category="Störung"
    )
    assert "middle" not in rendered


def test_label_before_bis_and_a_day_is_no_house_number_range() -> None:
    """The digit closing a label's placeholder is no house number: "Duration:-19 September"."""
    rendered = build_feed._render_record_table(
        "Haltestelle: Fultonstraße. Dauer: bis 19. September 2026.",
        source="Wiener Linien",
        category="Hinweis",
    )
    assert rendered == "Stop: Fultonstraße. Duration: Until 19 September 2026."
    assert build_feed._render_record_table(
        "Von: Eipeldauer Straße 12 bis 14.", source="Wiener Linien", category="Hinweis"
    ) == "From: Eipeldauer Straße 12-14."


# --- A passed expected end goes (operator decision 2026-10-06, "Weglassen") ---

_VIE = build_feed._VIENNA_TZ


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=_VIE)


def _ticker(pub: datetime) -> FeedItem:
    return cast(FeedItem, {"source": "Wiener Linien", "category": "Störung", "pubDate": pub})


_TEXT = (
    "Linie U6: Fahrtbehinderung in Richtung Floridsdorf. Voraussichtlich bis 19:10 Uhr. "
    "Grund: Rettungseinsatz."
)


def test_passed_clock_time_is_dropped() -> None:
    """U6 on 2026-10-06: "bis 19:10" still stood at 20:31."""
    out = build_feed._drop_passed_expected_end(
        _TEXT, _ticker(_at(6, 18, 58)), _at(6, 18, 58), now=_at(6, 20, 31)
    )
    assert out == "Linie U6: Fahrtbehinderung in Richtung Floridsdorf. Grund: Rettungseinsatz."


def test_coming_clock_time_stays() -> None:
    out = build_feed._drop_passed_expected_end(
        _TEXT, _ticker(_at(6, 18, 58)), _at(6, 18, 58), now=_at(6, 19, 9)
    )
    assert out == _TEXT


def test_estimate_far_ahead_stays() -> None:
    """13A on 2026-09-26: "ca. 22 Uhr" entered at 04:30 for an event."""
    text = "Busse halten Amerlingstraße 8-10. Voraussichtlich bis ca. 22 Uhr. Grund: Veranstaltung."
    pub = datetime(2026, 9, 26, 4, 30, tzinfo=_VIE)
    now = datetime(2026, 9, 26, 17, 0, tzinfo=_VIE)
    assert build_feed._drop_passed_expected_end(text, _ticker(pub), pub, now=now) == text


def test_end_after_midnight_counts_for_the_next_morning() -> None:
    text = "Kein Betrieb. Voraussichtlich bis 02:45 Uhr."
    pub = _at(6, 23, 10)
    assert build_feed._drop_passed_expected_end(text, _ticker(pub), pub, now=_at(7, 1, 0)) == text
    assert build_feed._drop_passed_expected_end(text, _ticker(pub), pub, now=_at(7, 3, 0)) == "Kein Betrieb."


def test_end_already_passed_when_entered_is_dropped() -> None:
    """N29 on 2026-07-30: "00:30 Uhr" in a message of 01:12."""
    text = "Fahrtbehinderung. Voraussichtlich bis 00:30 Uhr. Grund: Rettungseinsatz."
    pub = datetime(2026, 7, 30, 1, 12, tzinfo=_VIE)
    now = datetime(2026, 7, 30, 1, 30, tzinfo=_VIE)
    assert build_feed._drop_passed_expected_end(text, _ticker(pub), pub, now=now) == (
        "Fahrtbehinderung. Grund: Rettungseinsatz."
    )


def test_newest_time_of_the_item_is_the_anchor() -> None:
    """A bundle's pubDate is its earliest message, starts_at its latest."""
    text = "Fahrtbehinderung. Voraussichtlich bis 08:00 Uhr."
    pub, start = _at(5, 7, 30), _at(6, 7, 40)
    assert build_feed._drop_passed_expected_end(text, _ticker(pub), start, now=_at(6, 7, 50)) == text


@pytest.mark.parametrize(
    "text",
    [
        "Kein Betrieb. Voraussichtlich bis Betriebsschluss.",
        "Kein Betrieb. Voraussichtlich bis 19. September, etwa 01:00 Uhr.",
        "Kein Betrieb. Voraussichtlich bis 31.07.2026.",
        "Kein Betrieb. Voraussichtliche Dauer: Nicht absehbar.",
    ],
)
def test_only_a_clock_time_is_dropped(text: str) -> None:
    pub = datetime(2026, 7, 1, 8, 0, tzinfo=_VIE)
    assert build_feed._drop_passed_expected_end(text, _ticker(pub), pub, now=_at(6, 12)) == text
