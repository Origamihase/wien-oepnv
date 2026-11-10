"""Dates written in a text, the Stammstrecke monitor and ``lint`` under the rule of the plausible years.

Operator decision of 2026-11-10: a time stamp whose year lies outside
2000 to 2100 counts as not set (``src.utils.vienna_clock.PLAUSIBLE_YEARS``).
The review of that change named four places the rule did not reach yet.
Each has its section here:

* Wiener Linien write a date into a title ("ab 01.12.2026") and into the
  "Zeitraum:" paragraph of a notice; ``src/providers/wl_text.py`` reads
  both. With the year 9999 there, ``wl_fetch.fetch_events`` ended with
  ``OverflowError`` (``wl_plausibility._duration_end`` adds the duration to
  the start) or handed on an item that started or ended in the year 9999.
  Such a date is not set now: the item reads as it would without it.
* An ÖBB description begins with its period ("05.12.2026 - 07.12.2026").
  Each side counts on its own; the item shows what that means for
  ``starts_at``, ``ends_at`` and the time line of the feed.
* The Stammstrecke monitor reads the date and time of a VAO departure
  (``src.stammstrecke.legs._parse_vao_dt``) and then adds or subtracts a
  day and moves the value to UTC. A date in the year 1 or 9999 is
  unreadable now, and the departure is left out like any other without a
  readable time.
* ``src.build_feed.lint`` runs the chain of a build over the caches. Over a
  cache with edge dates it reports the items and returns 0.

The parsers on their own, with every edge value, are pinned in
``tests/test_plausible_years.py``; the WL time stamps on their way from the
fetch to the feed in ``tests/test_plausible_years_chain.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.providers.oebb import _build_item_from_xml
from src.providers.wl_text import extract_date_from_title, extract_end_from_description, extract_start_from_description
from src.stammstrecke.hbf_classify import _collect_hbf_observations, _departure_delay_minutes
from src.stammstrecke.legs import _leg_departure_delay_minutes, _parse_vao_dt
from src.utils import vienna_clock
from tests.test_plausible_years_chain import (
    NOTICE_START,
    WL_NOTICE_END,
    WL_NOTICE_START,
    _assert_ordinary_disruption,
    _edge_items,
    _fetch,
    _notice,
    _ordinary_disruption,
    _redirect_build,
    _write_wl_cache,
)

VIENNA = ZoneInfo("Europe/Vienna")
CET = timezone(timedelta(hours=1))


def _exactly(result: object, expected: datetime | None) -> None:
    """*result* is the moment *expected* with the same offset, or both are not set."""
    if expected is None:
        assert result is None
        return
    assert isinstance(result, datetime)
    assert result == expected
    assert result.utcoffset() == expected.utcoffset()


def _vienna(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=VIENNA)


# --- Wiener Linien: the three readers of a written date --------------------------

_TEXT_DATES: list[tuple[Callable[[str], datetime | None], str, datetime | None]] = [
    (extract_date_from_title, "U1: Umleitung ab 05.12.2026", _vienna(2026, 12, 5)),
    (extract_date_from_title, "U1: Umleitung ab 5. Dezember 2026", _vienna(2026, 12, 5)),
    (extract_date_from_title, "U1: Umleitung am 05.12.26", _vienna(2026, 12, 5)),
    (extract_date_from_title, "U1: Umleitung ab 01.01.2000", _vienna(2000, 1, 1)),
    (extract_date_from_title, "U1: Umleitung ab 31.12.2100", _vienna(2100, 12, 31)),
    (extract_date_from_title, "U1: Umleitung ab 31.12.1999", None),
    (extract_date_from_title, "U1: Umleitung ab 01.01.2101", None),
    (extract_date_from_title, "U1: Umleitung ab 01.01.9999", None),
    (extract_date_from_title, "U1: Umleitung ab 1. Jänner 0001", None),
    (extract_start_from_description, "<p>Zeitraum: Ab Samstag, 5. Dezember 2026, etwa 06:00 Uhr</p>", _vienna(2026, 12, 5)),
    (extract_start_from_description, "<p>Zeitraum: Ab 05.12.2026</p>", _vienna(2026, 12, 5)),
    (extract_start_from_description, "<p>Zeitpunkt: Ab 01.01.2000</p>", _vienna(2000, 1, 1)),
    (extract_start_from_description, "<p>Zeitraum: Ab 31.12.2100</p>", _vienna(2100, 12, 31)),
    (extract_start_from_description, "<p>Zeitraum: Ab 31.12.1999</p>", None),
    (extract_start_from_description, "<p>Zeitraum: Ab 01.01.2101</p>", None),
    (extract_start_from_description, "<p>Zeitraum: Ab 01.12.9999 auf Dauer von etwa sechs Wochen</p>", None),
    (extract_start_from_description, "<p>Zeitraum: Ab 1. Dezember 9999</p>", None),
    (extract_start_from_description, "<p>Zeitraum: Ab 01.01.0001</p>", None),
    (extract_end_from_description, "<p>Zeitraum: Ab 05.12.2026 bis 20.12.2026</p>", _vienna(2026, 12, 20, 23, 59)),
    (extract_end_from_description, "<p>Zeitraum: Ab 5. Dezember 2026 bis Sonntag, 20. Dezember 2026</p>", _vienna(2026, 12, 20, 23, 59)),
    (extract_end_from_description, "<p>Zeitraum: bis Jänner 2000</p>", _vienna(2000, 1, 31, 23, 59)),
    (extract_end_from_description, "<p>Zeitraum: bis Ende 2100</p>", _vienna(2100, 12, 31, 23, 59)),
    (extract_end_from_description, "<p>Zeitraum: bis Ende 1999</p>", None),
    (extract_end_from_description, "<p>Zeitraum: bis Ende 2101</p>", None),
    (extract_end_from_description, "<p>Zeitraum: bis 31.12.9999</p>", None),
    (extract_end_from_description, "<p>Zeitraum: Von 03.11.2026 bis 31. Dezember 9999</p>", None),
    (extract_end_from_description, "<p>Zeitraum: bis Dezember 9999</p>", None),
    (extract_end_from_description, "<p>Zeitraum: bis Ende Jänner 0001</p>", None),
]


@pytest.mark.parametrize(
    ("read", "text", "expected"), _TEXT_DATES, ids=[f"{read.__name__}: {text}" for read, text, _ in _TEXT_DATES]
)
def test_written_date_is_read_within_the_plausible_years_only(
    read: Callable[[str], datetime | None], text: str, expected: datetime | None
) -> None:
    _exactly(read(text), expected)


# --- Wiener Linien: a written date on its way through the fetch -----------------

# The end WL sets by hand on a notice without a known end: 11:11, a year
# after publication (``wl_plausibility.PLACEHOLDER_END``). Only such an end
# gives way to the end or the duration the text names.
WL_OPEN_END = "2027-11-03T11:11:00.000+0100"
OPEN_END = datetime(2027, 11, 3, 11, 11, tzinfo=CET)


def _notice_with(
    period: str = "", *, title: str | None = None, start: str | None = WL_NOTICE_START, end: str | None = WL_OPEN_END
) -> dict[str, Any]:
    """A newsList record; *period* is the text behind "Zeitraum:", *title* replaces the title."""
    record = _notice("N20261103-0001", "U1", start=start, end=end)
    if period:
        record["description"] += f"<p>Zeitraum: {period}</p>"
    if title is not None:
        record["title"] = title
    return record


def _fetch_notice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """The item ``fetch_events`` makes of *record*, fetched beside an ordinary disruption."""
    items = _fetch(monkeypatch, tmp_path, [_ordinary_disruption()], [record])
    _assert_ordinary_disruption(items.pop("U4: Rettungseinsatz"))
    assert len(items) == 1
    (item,) = items.values()
    first, last = vienna_clock.PLAUSIBLE_YEARS
    years = {key: value.year for key, value in item.items() if isinstance(value, datetime)}
    assert all(first <= year <= last for year in years.values()), years
    return item


def test_notice_without_a_written_date_has_the_dates_wl_sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # What the tests below compare with: publication as start, WL's own end.
    item = _fetch_notice(monkeypatch, tmp_path, _notice_with())

    assert (item["pubDate"], item["starts_at"], item["ends_at"]) == (NOTICE_START, NOTICE_START, OPEN_END)


@pytest.mark.parametrize(
    "period",
    [
        # The start of a duration: the fetch ended here before (``_duration_end``).
        "Ab 01.12.9999 auf Dauer von etwa sechs Wochen",
        "Ab 1. Dezember 9999 auf Dauer von etwa sechs Wochen",
        "Ab 01.01.0001 auf Dauer von etwa sechs Wochen",
        # An end: the item ended, and with no start before it also began, in the year 9999.
        "bis 31.12.9999",
        "Von 03.11.2026 bis 31.12.9999",
        "Von 3. November 2026 bis 31. Dezember 9999",
        "Ab 01.11.2026 bis Ende 9999",
        "Ab 01.11.2026 bis Dezember 9999",
    ],
)
def test_notice_with_an_edge_year_in_its_period_reads_as_without_the_date(
    period: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    item = _fetch_notice(monkeypatch, tmp_path, _notice_with(period))

    assert (item["pubDate"], item["starts_at"], item["ends_at"]) == (NOTICE_START, NOTICE_START, OPEN_END)


def test_edge_start_in_the_period_leaves_the_end_the_same_text_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without ``time.start`` the written start would be the only one.
    record = _notice_with("Ab 01.01.0001 bis 31.12.2026", start=None)

    item = _fetch_notice(monkeypatch, tmp_path, record)

    assert (item["pubDate"], item["starts_at"]) == (None, None)
    _exactly(item["ends_at"], _vienna(2026, 12, 31, 23, 59))


@pytest.mark.parametrize(
    "title",
    [
        "U1: Nestroyplatz ab 01.01.9999 kein Halt",
        "U1: Nestroyplatz ab 1. Jänner 9999 kein Halt",
        "U1: Nestroyplatz ab 31.12.9999 kein Halt",
        "U1: Nestroyplatz ab 01.01.0001 kein Halt",
        "U1: Nestroyplatz ab 01.01.2101 kein Halt",
    ],
)
def test_notice_with_an_edge_year_in_its_title_has_no_start(
    title: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No ``time.start`` and no ``time.end``: the date of the title would be
    # the only one, and it became ``starts_at``.
    item = _fetch_notice(monkeypatch, tmp_path, _notice_with(title=title, start=None, end=None))

    assert (item["pubDate"], item["starts_at"], item["ends_at"]) == (None, None, None)


@pytest.mark.parametrize(
    ("record", "starts_at", "ends_at"),
    [
        (_notice_with("Ab 05.11.2026 bis 20.11.2026"), _vienna(2026, 11, 5), _vienna(2026, 11, 20, 23, 59)),
        # Six weeks and the buffer of half the duration: 05.11. + 63 days.
        (_notice_with("Ab 05.11.2026 auf Dauer von etwa sechs Wochen"), _vienna(2026, 11, 5), _vienna(2027, 1, 7, 23, 59)),
        (
            _notice_with(title="U1: Nestroyplatz ab 05.11.2026 kein Halt", end=WL_NOTICE_END),
            _vienna(2026, 11, 5),
            _vienna(2026, 11, 30, 23, 59),
        ),
    ],
    ids=["period with an end", "period with a duration", "title"],
)
def test_ordinary_written_dates_are_used_as_before(
    record: dict[str, Any], starts_at: datetime, ends_at: datetime, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    item = _fetch_notice(monkeypatch, tmp_path, record)

    assert item["pubDate"] == NOTICE_START
    _exactly(item["starts_at"], starts_at)
    _exactly(item["ends_at"], ends_at)


# --- ÖBB: the period as the item shows it ---------------------------------------

OEBB_PUBLISHED = datetime(2026, 11, 9, 10, 7, 13, tzinfo=CET)
# The time lines are read on this day.
READ_AT = _vienna(2026, 11, 10, 13, 0)


def _oebb_item(period: str) -> ET.Element:
    """One ``<item>`` in the shape of the ÖBB RSS, its description led by *period*."""
    element = ET.Element("item")
    for tag, text in (
        ("title", "Bauarbeiten: Wien Hbf - Gramatneusiedl"),
        (
            "description",
            f"{period}<br/><br/>Wegen Bauarbeiten können zwischen <b>Wien Hbf (U)</b> und "
            "<b>Gramatneusiedl Bahnhof</b> keine Nahverkehrszüge fahren.",
        ),
        ("link", "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty"),
        ("guid", "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&900001"),
        ("pubDate", "Mon, 09 Nov 2026 10:07:13 +0100"),
    ):
        ET.SubElement(element, tag).text = text
    return element


@pytest.mark.parametrize(
    ("period", "starts_at", "ends_at", "time_line"),
    [
        ("05.12.2026 - 07.12.2026", _vienna(2026, 12, 5), _vienna(2026, 12, 7, 23, 59, 59), "Ab 05.12. bis 07.12."),
        # An edge end is "no end known": the work still begins on its day.
        ("05.12.2026 - 31.12.9999", _vienna(2026, 12, 5), None, "Ab 05.12."),
        # An edge start is unreadable: the item starts with its publication and keeps its end.
        ("01.01.0001 - 07.12.2026", OEBB_PUBLISHED, _vienna(2026, 12, 7, 23, 59, 59), "Bis 07.12."),
        # No side is left: publication as start and no end, as without a period.
        ("31.12.9999", OEBB_PUBLISHED, None, "Seit 09.11."),
        ("01.01.0001 - 31.12.9999", OEBB_PUBLISHED, None, "Seit 09.11."),
    ],
)
def test_oebb_item_keeps_the_plausible_side_of_its_period(
    period: str, starts_at: datetime, ends_at: datetime | None, time_line: str
) -> None:
    item = _build_item_from_xml(_oebb_item(period))

    assert not isinstance(item, str), item
    _exactly(item["pubDate"], OEBB_PUBLISHED)
    _exactly(item["starts_at"], starts_at)
    _exactly(item["ends_at"], ends_at)
    # ``format_local_times`` joins its words with no-break spaces.
    assert bf.format_local_times(item["starts_at"], item["ends_at"], READ_AT).replace(" ", " ") == time_line


# --- Stammstrecke: the date and time of a VAO departure -------------------------


@pytest.mark.parametrize(
    ("date", "time", "expected"),
    [
        ("2026-11-10", "08:15", _vienna(2026, 11, 10, 8, 15)),
        ("2026-11-10", "08:15:30", _vienna(2026, 11, 10, 8, 15)),
        ("2026-07-11", "", _vienna(2026, 7, 11)),
        ("2000-01-01", "00:00", _vienna(2000, 1, 1)),
        ("2100-12-31", "23:59", _vienna(2100, 12, 31, 23, 59)),
        ("1999-12-31", "23:59", None),
        ("2101-01-01", "00:00", None),
        ("0001-01-01", "00:00", None),
        ("9999-12-31", "23:55", None),
        ("kein Datum", "08:15", None),
        ("", "08:15", None),
    ],
)
def test_vao_date_is_read_within_the_plausible_years_only(date: str, time: str, expected: datetime | None) -> None:
    _exactly(_parse_vao_dt(date, time), expected)


# A real-time reading on the other side of midnight has no date of its own:
# both functions then add or subtract a day, which year 1 and 9999 cannot take.
_EDGE_DEPARTURES: list[dict[str, str]] = [
    {"date": "0001-01-01", "time": "00:00", "rtTime": "00:05"},
    {"date": "0001-01-01", "time": "00:05", "rtTime": "23:54"},
    {"date": "9999-12-31", "time": "23:55", "rtTime": "00:05"},
    {"date": "9999-12-31", "time": "08:15", "rtTime": "08:20"},
    # The edge date as the date of the real time.
    {"date": "2026-11-10", "time": "08:15", "rtTime": "08:20", "rtDate": "9999-12-31"},
    {"date": "2026-11-10", "time": "08:15", "rtTime": "08:20", "rtDate": "0001-01-01"},
]
_ORDINARY_DEPARTURES: list[tuple[dict[str, str], float]] = [
    ({"date": "2026-11-10", "time": "08:15", "rtTime": "08:20"}, 5.0),
    ({"date": "2026-11-10", "time": "08:15", "rtTime": "08:15"}, 0.0),
    ({"date": "2026-11-10", "time": "23:55", "rtTime": "00:05"}, 10.0),
    ({"date": "2026-11-10", "time": "00:05", "rtTime": "23:54"}, -11.0),
    ({"date": "2026-11-10", "time": "23:55", "rtTime": "00:05", "rtDate": "2026-11-11"}, 10.0),
]


@pytest.mark.parametrize("departure", _EDGE_DEPARTURES, ids=str)
def test_departure_with_an_edge_date_has_no_delay(departure: dict[str, str]) -> None:
    assert _departure_delay_minutes(departure) is None
    assert _leg_departure_delay_minutes({"Origin": departure}) is None


@pytest.mark.parametrize(("departure", "minutes"), _ORDINARY_DEPARTURES, ids=[str(d) for d, _ in _ORDINARY_DEPARTURES])
def test_ordinary_departure_has_its_delay_as_before(departure: dict[str, str], minutes: float) -> None:
    assert _departure_delay_minutes(departure) == minutes
    assert _leg_departure_delay_minutes({"Origin": departure}) == minutes


def _board_entry(departure: dict[str, str], **fields: Any) -> dict[str, Any]:
    """*departure* as an entry of the Hbf departure board: an S1 on track 1 towards Meidling."""
    return {"name": "S 1", "rtTrack": "1", "direction": "Wien Meidling", **departure, **fields}


def test_board_with_edge_dates_still_gives_its_ordinary_departure() -> None:
    ordinary, minutes = _ORDINARY_DEPARTURES[0]
    board = [_board_entry(departure) for departure in _EDGE_DEPARTURES]
    # A cancelled train needs no real time, only its planned one.
    board.append(_board_entry({"date": "9999-12-31", "time": "23:55"}, cancelled=True))
    board.append(_board_entry(ordinary))

    by_direction, diagnostics = _collect_hbf_observations(board)

    observations = [observation for found in by_direction.values() for observation in found]
    assert len(observations) == 1
    _exactly(observations[0].scheduled, _vienna(2026, 11, 10, 8, 15))
    assert (observations[0].name, observations[0].delay_minutes, observations[0].cancelled) == ("S1", minutes, False)
    # Every entry passed the gates before the time is read.
    assert diagnostics.on_stammstrecke == len(board)


# --- lint -----------------------------------------------------------------------


def test_lint_over_a_cache_with_edge_dates_reports_every_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ``lint`` returned 2 here and printed no report: the chain ended with
    # ``OverflowError`` on the end of the year 9999.
    _redirect_build(monkeypatch, tmp_path)
    _write_wl_cache(_edge_items(datetime.now(UTC)))

    exit_code = bf.lint()

    report = capsys.readouterr().out
    assert exit_code == 0
    assert "Rohdaten: 4\n" in report
    assert "Nach Altersfilter: 4\n" in report
    assert "Nach Deduplizierung: 4 (entfernte Duplikate: 0)\n" in report
    assert "Keine strukturellen Probleme gefunden." in report
    assert not (tmp_path / "feed.xml").exists()
