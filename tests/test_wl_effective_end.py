"""WL ends at 11:11 give way to the end the "Zeitraum:" section names.

23 of 34 notices (2026-10-01) ended at exactly 11:11, mostly a year after
publication. "44A: Kurzführung" ended "Ende September 2026" by its text and
on 22.07.2027 by ``time.end``; the finished notice stayed a candidate for
the ten feed slots.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from src.providers.wl_fetch import _effective_end
from src.providers.wl_text import extract_end_from_description

VIENNA = ZoneInfo("Europe/Vienna")
START = datetime(2026, 8, 3, tzinfo=VIENNA)


def _end(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 23, 59, tzinfo=VIENNA)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Zeitraum: Montag, 3. August 2026, bis Ende September 2026. Maßnahmen: …", _end(2026, 9, 30)),
        ("Zeitraum: Ab 12. September 2026, bis voraussichtlich Ende Oktober 2026.", _end(2026, 10, 31)),
        ("<strong>Zeitraum:</strong><br />Freitag, 11. September 2026 bis Ende 2026.", _end(2026, 12, 31)),
        ("Zeitraum: Ab 4. Juli 2026 bis Sonntag, 6. September 2026.", _end(2026, 9, 6)),
        ("Zeitraum: ab 05.10.2026 bis 20.11.2026", _end(2026, 11, 20)),
        ("Zeitraum: Ab Montag, 07. September 2026 bis Oktober 2027.", _end(2027, 10, 31)),
        ("Zeitraum: Ab 3. August 2026 bis Ende Februar.", _end(2027, 2, 28)),  # year from the start
        # no day named: stays with WL's end
        ("Zeitraum: Ab Montag, 05. Oktober 2026, etwa 06:30 Uhr auf Dauer von etwa sechs Wochen.", None),
        ("Zeitraum: Ab 28. September 2026, 04:00 Uhr bis etwa Mitte November 2026.", None),
        ("Zeitraum: Ab 21. Mai 2026, etwa 08:30 Uhr, auf derzeit unbestimmte Zeit.", None),
        # a nightly window or a detour after the section is no end date
        ("Zeitraum: Ab 5. Oktober 2026, täglich von 20:00 Uhr bis 05:00 Uhr. Maßnahmen: Umleitung bis 30. Mai", None),
        # phases: the first end is not the end
        ("Zeitraum: Phase 1: Ab 4. Juli 2026 bis 6. September 2026. Phase 2: Ab 7. September 2026 bis Ende 2027.", None),
        ("Wegen Bauarbeiten bis Ende Oktober 2026 umgeleitet.", None),  # no heading
        ("", None),
    ],
)
def test_end_from_description(text: str, expected: datetime | None) -> None:
    assert extract_end_from_description(text, reference_date=START) == expected


_KURZFUEHRUNG = "Zeitraum: Montag, 3. August 2026, bis Ende September 2026. Maßnahmen: Linie 44A: Kein Betrieb."


def test_placeholder_end_gives_way_to_the_text() -> None:
    placeholder = datetime(2027, 7, 22, 11, 11, tzinfo=VIENNA)
    assert _effective_end(_KURZFUEHRUNG, placeholder, START) == _end(2026, 9, 30)


def test_any_other_end_stays() -> None:
    exact = datetime(2027, 7, 22, 23, 59, tzinfo=VIENNA)
    assert _effective_end(_KURZFUEHRUNG, exact, START) == exact


def test_open_end_stays_open() -> None:
    assert _effective_end(_KURZFUEHRUNG, None, START) is None


def test_placeholder_stays_without_a_named_end() -> None:
    placeholder = datetime(2027, 7, 22, 11, 11, tzinfo=VIENNA)
    text = "Zeitraum: Ab 3. August 2026 bis etwa Mitte November 2026."
    assert _effective_end(text, placeholder, START) == placeholder
    # a duration gives an end of its own (tests/test_wl_duration_end.py)


def test_text_end_before_the_start_is_not_taken() -> None:
    placeholder = datetime(2027, 7, 22, 11, 11, tzinfo=VIENNA)
    text = "Zeitraum: bis 1. Juli 2026."
    assert _effective_end(text, placeholder, START) == placeholder


def test_placeholder_is_read_in_vienna_time() -> None:
    # 11:11 Vienna in winter is 10:11 UTC.
    placeholder = datetime(2027, 1, 1, 11, 11, tzinfo=VIENNA).astimezone(ZoneInfo("UTC"))
    text = "Zeitraum: Ab 03. August 2026 bis voraussichtlich Ende November 2026."
    assert _effective_end(text, placeholder, START) == _end(2026, 11, 30)


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


def _notice(text: str, end: str) -> dict[str, Any]:
    return {
        "title": "Umleitung",
        "description": text,
        "time": {"start": "2020-01-01T00:00:00+01:00", "end": end},
        "relatedLines": ["N71"],
        "attributes": {},
    }


def test_fetch_events_keeps_a_notice_wl_lists_past_its_text_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # "93A/96A/N91: Schillwasserweg" ("bis etwa Ende Juli 2026") was listed
    # by WL until 10.09.2026: the passed text end gives the 11:11 end back.
    text = "Zeitraum: Ab 3. Jänner 2020 bis Ende März 2020. Maßnahmen: Umleitung."
    (event,) = _fetch(monkeypatch, _notice(text, "2099-01-01T11:11:00+01:00"))
    assert event["ends_at"] == datetime(2099, 1, 1, 11, 11, tzinfo=VIENNA)


def test_fetch_events_drops_a_notice_past_its_11_11_end(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Zeitraum: Ab 3. Jänner 2020 bis Ende März 2020. Maßnahmen: Umleitung."
    assert _fetch(monkeypatch, _notice(text, "2021-01-01T11:11:00+01:00")) == []


def test_fetch_events_keeps_the_text_end(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Zeitraum: Ab 3. Jänner 2020 bis Ende 2098. Maßnahmen: Umleitung."
    (event,) = _fetch(monkeypatch, _notice(text, "2099-01-01T11:11:00+01:00"))
    assert event["ends_at"] == _end(2098, 12, 31)
