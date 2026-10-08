"""WL's 11:11 expiry date is no end: the time line leaves it out.

WL closes a notice whose end it does not know at 11:11, mostly a year after
publication. Of the 124 such notices WL ended between 21.02. and 08.10.2026,
122 left before that date. On 08.10.2026 "72A: Haidestraße S" read "[Bis
08.10.2027]" for a stop relocation with no end in its text. Operator
decision 2026-10-08 ("Kein Ende"): such a line reads like any other without
an end. The texts below are the real shapes of the raw data of that day.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from src import build_feed
from src.feed_types import FeedItem
from src.providers.wl_plausibility import end_unknown, plausible_end
from src.providers.wl_text import extract_duration_from_description

pytestmark = pytest.mark.usefixtures("time_line_today")  # 2026-10-02 12:00 Vienna

VIENNA = ZoneInfo("Europe/Vienna")


def _stop(duration: str) -> str:
    return (
        "<p><strong><u>Haltestellenverlegung der Linie 72A in Richtung Hasenleiten</u></strong></p>"
        "<p><strong><u>Haltestelle:</u></strong> Haidestra&szlig;e S</p>"
        "<p><strong><u>Von:</u></strong> Lautenschl&auml;gergasse nach Haidestra&szlig;e</p>"
        "<p><strong><u>Nach:</u></strong> 1. Haidequerstra&szlig;e gegen&uuml;ber 3A</p>"
        f"<p><strong><u>Dauer:</u></strong> {duration}</p>"
        "<p><strong><u>Grund:</u></strong> Arbeiten an einer Gasleitung</p>"
    )


def _works(period: str) -> str:
    return (
        "<h2>Bauarbeiten</h2><p>Wegen Bauarbeiten wird die Linie 12A umgeleitet.</p>"
        f"<p><strong>Zeitraum:</strong> {period}</p><p><strong>Maßnahmen:</strong> Umleitung.</p>"
    )


EXPIRY = datetime(2027, 10, 8, 11, 11, tzinfo=VIENNA)


@pytest.mark.parametrize(
    ("desc", "unknown"),
    [
        (_stop("Ab 08. Oktober 2026, etwa 16:00 Uhr"), True),  # 72A, nothing
        (_stop("07. Juli 2026, etwa 12:00 Uhr auf derzeit unbekannte Zeit"), True),  # 38A
        (_stop("Ab 31. August 2026, etwa 08:00 Uhr, Dauer unbekannt"), True),  # 49A/50B
        (_stop("Ab 05. September 2026 Betriebsbeginn, bis auf weiteres."), True),  # 78A
        # a vague end names no day (61A, 12A)
        (_stop("03. Dezember 2025, etwa 09:00 Uhr bis etwa Frühjahr 2027"), True),
        (_works("Ab Mittwoch, 15. Juli 2026, Betriebsbeginn bis voraussichtlich Mitte August 2026."), True),
        # an end or a duration in the text keeps the end
        (_stop("Ab 07. September 2026, etwa 06:00 Uhr, voraussichtlich bis Dezember 2027"), False),
        (_stop("Ab 13. Oktober 2026, etwa 07:00 Uhr, für etwa sieben Wochen"), False),
        (_works("Von Montag, 05. Oktober 2026, auf Dauer von etwa vier Wochen, täglich von 20:00 Uhr bis 05:00 Uhr."), False),
    ],
)
def test_end_unknown(desc: str, unknown: bool) -> None:
    assert end_unknown(desc, EXPIRY) is unknown


def test_only_an_11_11_end_is_an_expiry_date() -> None:
    desc = _stop("Ab 08. Oktober 2026, etwa 16:00 Uhr")
    assert end_unknown(desc, datetime(2027, 10, 8, 11, 12, tzinfo=VIENNA)) is False
    assert end_unknown(desc, datetime(2026, 12, 31, 23, 59, tzinfo=VIENNA)) is False
    assert end_unknown(desc, None) is False
    # 11:11 Vienna, whatever offset it is given in
    assert end_unknown(desc, datetime(2027, 10, 8, 9, 11, tzinfo=ZoneInfo("UTC"))) is True


def _time_line(title: str, desc: str, start: datetime, end: datetime, **extra: str) -> str:
    item = cast(
        FeedItem,
        {
            "title": title,
            "description": desc,
            "source": "Wiener Linien",
            "category": "Hinweis",
            "guid": "t",
            "link": "",
            **extra,
        },
    )
    formatted = build_feed._format_item_content(item, ident="t", starts_at=start, ends_at=end)
    return "[" + formatted.desc_text_truncated.rsplit("[", 1)[1].replace(" ", " ")


@pytest.mark.parametrize(
    ("desc", "start", "end", "line"),
    [
        # 72A Haidestraße S: running, nothing in the text
        (_stop("Ab 08. Oktober 2026, etwa 16:00 Uhr"), datetime(2026, 9, 28, 16, 0), EXPIRY, "[Seit 28.09.]"),
        # 48A Neustiftgasse: announced, nothing in the text
        (_stop("Ab 19. Oktober 2026, etwa 08:00 Uhr"), datetime(2026, 10, 19), EXPIRY, "[Ab 19.10.]"),
        # announced within the week keeps its weekday
        (_stop("Ab 05. Oktober 2026, etwa 08:00 Uhr"), datetime(2026, 10, 5), EXPIRY, "[Ab Mo 05.10.]"),
        # 63A: the duration lasts past the 11:11 end, which stays and is shown
        (
            _works("Von Montag, 05. Oktober 2026, auf Dauer von etwa vier Wochen, täglich von 20:00 Uhr bis 05:00 Uhr."),
            datetime(2026, 10, 5),
            datetime(2026, 11, 11, 11, 11),
            "[Ab Mo 05.10. bis 11.11.]",
        ),
    ],
)
def test_time_line_of_a_wl_notice(desc: str, start: datetime, end: datetime, line: str) -> None:
    start, end = start.replace(tzinfo=VIENNA), end.replace(tzinfo=VIENNA)
    assert _time_line("72A: Haidestraße S", desc, start, end) == line


@pytest.mark.parametrize(("source", "category"), [("Wiener Linien", "Störung"), ("ÖBB", "Hinweis")])
def test_other_items_keep_an_11_11_end(source: str, category: str) -> None:
    """Only WL notices carry the expiry date; an 11:11 elsewhere is a real end."""
    line = _time_line(
        "72A: Haidestraße S",
        _stop("Ab 08. Oktober 2026, etwa 16:00 Uhr"),
        datetime(2026, 9, 28, 16, 0, tzinfo=VIENNA),
        datetime(2026, 10, 20, 11, 11, tzinfo=VIENNA),
        source=source,
        category=category,
    )
    assert line == "[Bis 20.10.]"


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("für etwa ein Jahr", 365),  # 1A Habsburgergasse
        ("für ca. 1 Jahr", 365),  # 57A Haus des Meeres
        ("für etwa 1,5 Jahre", 547.5),  # 26A/N20 Siebeckstraße
        ("auf Dauer von etwa einem Jahr", 365),  # 5A/5B/N31
        ("für eineinhalb Jahre", 547.5),
        ("für zwei Jahre", 730),
        ("für etwa einem Monat", 30),
    ],
)
def test_duration_in_years(text: str, days: float) -> None:
    desc = _stop(f"Ab 19. Jänner 2026, etwa 08:00 Uhr {text}")
    assert extract_duration_from_description(desc) == timedelta(days=days)


def test_a_year_shortens_the_expiry_date() -> None:
    """1A Habsburgergasse: from 19.01.2026 for about a year, expiry 13.01.2028."""
    desc = _stop("Ab 19. Jänner 2026, etwa 08:00 Uhr für etwa ein Jahr")
    start = datetime(2026, 1, 13, 15, 10, tzinfo=VIENNA)
    end = plausible_end(desc, datetime(2028, 1, 13, 11, 11, tzinfo=VIENNA), start)
    # 19.01.2027 plus half a year of buffer
    assert end == datetime(2027, 7, 20, 23, 59, tzinfo=VIENNA)
    assert end_unknown(desc, end) is False
