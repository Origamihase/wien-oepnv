"""When a WL measure begins: ``am`` in the title and the "Zeitraum:" section.

WL's ``time.start`` is the publication day. On 2026-10-01, 7 of 34 notices
began later than their ``starts_at``; two of them stood in the German feed
with a wrong time line ("29B/N25: Adolf-Loos-Gasse [30.09.2026 – 31.12.2026]"
for works from 05.10., "Veranstaltung am 04.10.2026 [30.09.2026 –
04.10.2026]" for a two-hour event).
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from src.providers.wl_fetch import _effective_start
from src.providers.wl_text import extract_date_from_title, extract_start_from_description

VIENNA = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 1, 22, 0, tzinfo=VIENNA)
PUBLISHED = datetime(2026, 9, 30, 0, 0, tzinfo=VIENNA)


def _day(month: int, day: int, year: int = 2026) -> datetime:
    return datetime(year, month, day, tzinfo=VIENNA)


# --- title: "am" beside "ab" ---------------------------------------------


def test_title_am_date_is_recognised() -> None:
    assert extract_date_from_title("Veranstaltung am 04.10.2026") == _day(10, 4)
    assert extract_date_from_title("Sperre am 4. Oktober 2026") == _day(10, 4)


def test_title_date_needs_a_word_boundary() -> None:
    # "Damm 3.10." / "Grab 12.10." are no "am"/"ab" announcements.
    assert extract_date_from_title("Kein Betrieb Hauptdamm 03.10.2026") is None
    assert extract_date_from_title("Halt Grab 12.10.2026") is None


# --- description: "Zeitraum:" --------------------------------------------

_ADOLF_LOOS = (
    "<h2>Straßenbauarbeiten</h2> <p>Wegen Straßenbauarbeiten werden die Linien 29B und "
    "N25 umgeleitet.</p> <p><span><strong>Zeitraum:</strong></span><br />Ab Montag, "
    "05. Oktober 2026, etwa 06:30 Uhr auf Dauer von etwa sechs Wochen.</p>"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_ADOLF_LOOS, _day(10, 5)),
        ("<p><strong>Zeitraum:</strong><br />Von 08. September 2026 bis Ende Oktober 2026</p>", _day(9, 8)),
        ("Zeitraum: Montag, 3. August 2026, bis Ende September 2026.", _day(8, 3)),
        (
            "Zeitraum: Ab Samstag, 12. September 2026, Betriebsbeginn (Nacht von 11. auf 12. September)",
            _day(9, 12),
        ),
        ("Zeitraum: Am 04. Oktober 2026, ab etwa 11:30 Uhr bis 04. Oktober 2026, etwa 13:00 Uhr.", _day(10, 4)),
        ("Zeitraum: ab 05.10.2026 bis 20.11.2026", _day(10, 5)),
        ("Zeitraum: Ab 28. September, 04:00 Uhr", _day(9, 28)),  # year from the reference
        ("Zeitraum: bis auf Weiteres.", None),
        ("Wegen Bauarbeiten ab 05. Oktober 2026 umgeleitet.", None),  # no heading
        ("Zeitraum: auf derzeit unbestimmte Zeit. Maßnahmen: Linie 37A " + "x " * 80 + "ab 1. Mai 2027", None),
        ("", None),
    ],
)
def test_start_from_description(text: str, expected: datetime | None) -> None:
    assert extract_start_from_description(text, reference_date=PUBLISHED) == expected


# --- _effective_start ------------------------------------------------------


def test_description_start_moves_a_notice_to_its_begin() -> None:
    got = _effective_start("Adolf-Loos-Gasse", _ADOLF_LOOS, PUBLISHED, _day(12, 31), NOW)
    assert got == _day(10, 5)


def test_title_date_wins_over_the_description() -> None:
    got = _effective_start("Veranstaltung am 04.10.2026", _ADOLF_LOOS, PUBLISHED, None, NOW)
    assert got == _day(10, 4)


def test_earlier_date_never_moves_the_start_back() -> None:
    # A running phase: the text names a begin before the publication day.
    text = "Zeitraum: Ab Montag, 07. September 2026 bis Oktober 2027."
    assert _effective_start("Phase 2", text, PUBLISHED, None, NOW) == PUBLISHED


def test_description_date_past_the_end_is_not_taken() -> None:
    text = "Zeitraum: Ab 20. Dezember 2026."
    assert _effective_start("Umleitung", text, PUBLISHED, _day(11, 30), NOW) == PUBLISHED


def test_without_any_date_the_api_start_stays() -> None:
    assert _effective_start("Umleitung", "Kein Zeitraum.", PUBLISHED, None, NOW) == PUBLISHED


# --- end to end through fetch_events ---------------------------------------


class _Session:
    headers: dict[str, str] = {}

    def __enter__(self) -> _Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False


def test_fetch_events_takes_the_begin_from_the_description(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.providers import wl_fetch

    news: dict[str, Any] = {
        "title": "Adolf-Loos-Gasse",
        "description": _ADOLF_LOOS,
        "time": {"start": "2026-09-30T00:00:00+02:00", "end": "2026-12-31T11:11:00+01:00"},
        "relatedLines": ["29B", "N25"],
        "attributes": {},
    }
    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda *a, **kw: [])
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda *a, **kw: [news])
    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *a, **kw: _Session())

    (event,) = wl_fetch.fetch_events()

    assert event["starts_at"] == _day(10, 5)
    assert event["pubDate"] == PUBLISHED  # the publication stays the publication
