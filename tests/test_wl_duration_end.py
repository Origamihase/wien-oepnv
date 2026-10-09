"""An 11:11 end gives way to the text's start plus its duration and a buffer.

Four WL notices on 2026-10-02 named a duration instead of an end ("auf Dauer
von etwa sechs Wochen"), all with an 11:11 end. "65A/66A" (from 12.08.,
"etwa zwei Wochen") was still listed with 31.08.2027.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from src.providers.wl_plausibility import plausible_end
from src.providers.wl_text import extract_duration_from_description

VIENNA = ZoneInfo("Europe/Vienna")


def _end(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 23, 59, tzinfo=VIENNA)


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("Zeitraum: Ab Montag, 05. Oktober 2026, etwa 06:30 Uhr auf Dauer von etwa sechs Wochen. Maßnahmen: …", 42),
        ("Zeitraum: Von Montag, 05. Oktober 2026, auf Dauer von etwa vier Wochen, täglich von 20:00 Uhr bis 05:00 Uhr.", 28),
        ("<strong>Zeitraum:</strong><br />Ab 3. Mai 2026 für 10 Tage.", 10),
        ("Zeitraum: Ab 3. Mai 2026 für einen Monat.", 30),
        ("Zeitraum: Ab 3. Mai 2026 auf die Dauer von ca. drei Wochen.", 21),
        # no duration
        ("Zeitraum: Ab 21. Mai 2026, etwa 08:30 Uhr, auf derzeit unbestimmte Zeit.", None),
        ("Zeitraum: Ab 3. Mai 2026 bis Ende Oktober 2026.", None),
        # phases: a duration would be the first phase's only
        ("Zeitraum: Phase 1: Ab 4. Juli 2026 für zwei Wochen. Phase 2: Ab 7. September 2026 bis Ende 2027.", None),
        # after the section, the measures' own durations do not count
        ("Zeitraum: Ab 3. Mai 2026. Maßnahmen: Haltestelle für zwei Wochen verlegt.", None),
        ("Wegen Bauarbeiten auf Dauer von etwa zwei Wochen umgeleitet.", None),  # no heading
        ("", None),
    ],
)
def test_duration_from_description(text: str, days: int | None) -> None:
    expected = None if days is None else timedelta(days=days)
    assert extract_duration_from_description(text) == expected


_PUBLISHED = datetime(2026, 8, 5, tzinfo=VIENNA)  # 65A/66A, the earliest of the four


@pytest.mark.parametrize(
    ("text", "placeholder", "expected"),
    [
        # 29B/N25: 05.10. + 6 weeks + 3 weeks
        (
            "Zeitraum: Ab Montag, 05. Oktober 2026, etwa 06:30 Uhr auf Dauer von etwa sechs Wochen. Maßnahmen: …",
            datetime(2026, 12, 31, 11, 11, tzinfo=VIENNA),
            _end(2026, 12, 7),
        ),
        # 36A/36B: 21.09. + 5 weeks + 17.5 days
        (
            "Zeitraum: Ab Montag, 21. September 2026, etwa 06:30 Uhr auf Dauer von etwa fünf Wochen. Maßnahmen: …",
            datetime(2027, 9, 16, 11, 11, tzinfo=VIENNA),
            _end(2026, 11, 12),
        ),
        # 65A/66A: 12.08. + 2 weeks + at least one week
        (
            "Zeitraum: Ab Mittwoch, 12. August 2026, etwa 06:00 Uhr auf Dauer von etwa zwei Wochen. Maßnahmen: …",
            datetime(2027, 8, 31, 11, 11, tzinfo=VIENNA),
            _end(2026, 9, 2),
        ),
        # 63A: 05.10. + 4 weeks + 2 weeks lies past WL's 11.11. – never extended
        (
            "Zeitraum: Von Montag, 05. Oktober 2026, auf Dauer von etwa vier Wochen, täglich von 20:00 Uhr bis 05:00 Uhr.",
            datetime(2026, 11, 11, 11, 11, tzinfo=VIENNA),
            datetime(2026, 11, 11, 11, 11, tzinfo=VIENNA),
        ),
    ],
)
def test_placeholder_gives_way_to_the_duration(text: str, placeholder: datetime, expected: datetime) -> None:
    assert plausible_end(text, placeholder, _PUBLISHED) == expected


def test_a_named_end_wins_over_a_duration() -> None:
    text = "Zeitraum: Ab 5. Oktober 2026 auf Dauer von etwa zwei Wochen, bis 31.10.2026. Maßnahmen: …"
    assert plausible_end(text, datetime(2027, 9, 1, 11, 11, tzinfo=VIENNA), _PUBLISHED) == _end(2026, 10, 31)


def test_a_duration_without_a_start_keeps_the_placeholder() -> None:
    text = "Zeitraum: Auf Dauer von etwa zwei Wochen. Maßnahmen: …"
    placeholder = datetime(2027, 9, 1, 11, 11, tzinfo=VIENNA)
    assert plausible_end(text, placeholder, _PUBLISHED) == placeholder


def test_any_other_end_ignores_the_duration() -> None:
    text = "Zeitraum: Ab 5. Oktober 2026 auf Dauer von etwa zwei Wochen. Maßnahmen: …"
    exact = datetime(2027, 9, 1, 23, 59, tzinfo=VIENNA)
    assert plausible_end(text, exact, _PUBLISHED) == exact


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


def _fetch(monkeypatch: pytest.MonkeyPatch, news: dict[str, Any]) -> list[dict[str, Any]]:
    from src.providers import wl_fetch

    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda *a, **kw: [])
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda *a, **kw: [news])
    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *a, **kw: _Session())
    return wl_fetch.fetch_events()


def _notice(begin: datetime, weeks: int) -> dict[str, Any]:
    return {
        "title": "Inzersdorfer Straße",
        "description": f"Zeitraum: Ab {begin:%d.%m.%Y} auf Dauer von etwa {weeks} Wochen. Maßnahmen: Umleitung.",
        "time": {
            "start": (begin - timedelta(days=7)).isoformat(),
            "end": (begin + timedelta(days=365)).replace(hour=11, minute=11).isoformat(),
        },
        "relatedLines": ["65A"],
        "attributes": {},
    }


def _midnight(offset: int) -> datetime:
    today = datetime.now(VIENNA).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=offset)


def test_fetch_events_keeps_a_notice_wl_lists_past_its_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    # two weeks plus one week of buffer ended before today, but WL still
    # lists the notice ("65A/66A: Inzersdorfer Straße" until 07.10.2026):
    # the 11:11 end is the end again
    begin = _midnight(-51)
    (event,) = _fetch(monkeypatch, _notice(begin, 2))
    assert event["ends_at"] == (begin + timedelta(days=365)).replace(hour=11, minute=11)


def test_fetch_events_keeps_a_notice_within_the_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    # two weeks are over, the week of buffer is not
    (event,) = _fetch(monkeypatch, _notice(_midnight(-17), 2))
    last = _midnight(-17) + timedelta(days=21)
    assert event["ends_at"] == _end(last.year, last.month, last.day)
