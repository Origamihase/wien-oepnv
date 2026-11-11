"""Plausibility check of a WL item's validity (``src/providers/wl_plausibility.py``).

On 2026-10-02 the title "47B: Laufveranstaltung am 04.10.2027" (a typo; text
and ``time.end`` say 04.10.2026) moved ``starts_at`` a year past ``ends_at``
and the German feed read "[Ab 04.10.2027]" for a run that Sunday.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.providers import wl_plausibility as wp
from src.providers.wl_fetch import _effective_end, _effective_start

VIENNA = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=VIENNA)
PUBLISHED = datetime(2026, 9, 28, 13, 30, tzinfo=VIENNA)
RUN_END = datetime(2026, 10, 4, 13, 0, tzinfo=VIENNA)

_RUN_TEXT = (
    "<h2>Veranstaltung</h2><p>Wegen einer Laufveranstaltung werden die Busse der Linie 47B "
    "umgeleitet.</p><p><strong>Zeitraum:</strong><br />Sonntag, 4. Oktober 2026 zwischen "
    "etwa 11:30 und 13:00 Uhr.</p>"
)


def _day(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=VIENNA)


@pytest.fixture(autouse=True)
def _fresh_collection() -> None:
    wp.reset_corrections()


def _kinds(corrections: list[wp.Correction]) -> list[str]:
    return [c.kind for c in corrections]


# --- rule 1: majority on a contradiction ------------------------------------


def test_title_year_typo_is_outvoted_by_text_and_publication() -> None:
    start, corrections = wp.plausible_start(
        "47B: Laufveranstaltung am 04.10.2027", _RUN_TEXT, PUBLISHED, RUN_END, NOW
    )
    assert start == _day(2026, 10, 4)
    assert start <= RUN_END
    assert _kinds(corrections) == [wp.YEAR_CONFLICT]
    assert "04.10.2027" in corrections[0].detail and "gilt 04.10.2026" in corrections[0].detail


def test_text_year_typo_is_outvoted_the_same_way() -> None:
    text = "<p><strong>Zeitraum:</strong><br />Sonntag, 4. Oktober 2027, 11:30 Uhr.</p>"
    start, corrections = wp.plausible_start(
        "47B: Laufveranstaltung am 04.10.2026", text, PUBLISHED, RUN_END, NOW
    )
    assert start == _day(2026, 10, 4)
    assert _kinds(corrections) == [wp.YEAR_CONFLICT]


def test_different_days_are_no_contradiction_and_the_title_leads() -> None:
    text = "<p><strong>Zeitraum:</strong><br />Ab Montag, 5. Oktober 2026.</p>"
    start, corrections = wp.plausible_start("29B: Umleitung ab 06.10.2026", text, PUBLISHED, None, NOW)
    assert start == _day(2026, 10, 6)
    assert corrections == []


# --- rule 1, second half: the year of an older notice -------------------------

# "39B: Friedhofsverkehr" as WL published it on 11.11.2026: the text of an
# older notice, "Am 24. Dezember 2024", beside ``time.end`` 24.12.2026 17:00.
CEMETERY_PUBLISHED = datetime(2026, 11, 11, 15, 55, tzinfo=VIENNA)
CEMETERY_END = datetime(2026, 12, 24, 17, 0, tzinfo=VIENNA)
_CEMETERY_TEXT = (
    "<h2>Inbetriebnahme einer Autobuslinie</h2><p></p><p>Anlässlich des 24. Dezember wird die "
    "Autobuslinie 39B in Betrieb genommen und diverse Linien zu Friedhöfen verstärkt geführt.</p>"
    "<p><span><strong><u>Zeitraum:<br></u></strong></span>Am 24. Dezember 2024, zwischen ca. "
    "8:00 Uhr und 17:00 Uhr.</p><p><span><strong><u>Maßnahmen:<br></u></strong></span></p>"
)


def _period(text: str) -> str:
    return f"<p><strong>Zeitraum:</strong><br />{text}</p>"


def test_stale_year_in_the_text_takes_the_year_between_publication_and_end() -> None:
    start, corrections = wp.plausible_start(
        "39B: Friedhofsverkehr", _CEMETERY_TEXT, CEMETERY_PUBLISHED, CEMETERY_END, CEMETERY_PUBLISHED
    )
    assert start == _day(2026, 12, 24)
    assert _kinds(corrections) == [wp.STALE_YEAR]
    assert corrections[0].detail == (
        "Zeitraum 24.12.2024 liegt vor der Veröffentlichung 11.11.2026; "
        "gilt 24.12.2026 (Jahr einer älteren Meldung, Ende 24.12.2026)"
    )


def test_stale_start_year_beside_the_right_end_year() -> None:
    # 53A, published 10.06.2026: 15.06.2026 is the Monday the text names.
    text = _period("Von Montag, 15. Juni 2025, etwa 07:30 Uhr bis Freitag, 19. Juni 2026, etwa 12:00 Uhr.")
    published = datetime(2026, 6, 10, 15, 13, tzinfo=VIENNA)
    end = datetime(2026, 6, 19, 12, 0, tzinfo=VIENNA)
    start, corrections = wp.plausible_start("53A: Umleitung", text, published, end, published)
    assert start == _day(2026, 6, 15)
    assert _kinds(corrections) == [wp.STALE_YEAR]


def test_stale_year_across_new_year_takes_the_coming_january() -> None:
    # Published in December for January, the year written as the old one.
    published = datetime(2026, 12, 20, 10, 0, tzinfo=VIENNA)
    end = datetime(2027, 1, 31, 23, 0, tzinfo=VIENNA)
    start, corrections = wp.plausible_start("D: Gleisbau", _period("Ab 7. Jänner 2026."), published, end, published)
    assert start == _day(2027, 1, 7)
    assert _kinds(corrections) == [wp.STALE_YEAR]


def test_stale_year_in_the_title_is_restated_too() -> None:
    published = datetime(2026, 10, 20, 9, 0, tzinfo=VIENNA)
    end = datetime(2026, 11, 1, 18, 0, tzinfo=VIENNA)
    start, corrections = wp.plausible_start("38B/39B: Allerheiligenverkehr am 01.11.2025", "", published, end, published)
    assert start == _day(2026, 11, 1)
    assert _kinds(corrections) == [wp.STALE_YEAR]
    assert corrections[0].detail.startswith("Titel 01.11.2025 ")


def test_one_stale_date_in_title_and_text_is_one_correction() -> None:
    published = datetime(2026, 10, 20, 9, 0, tzinfo=VIENNA)
    end = datetime(2026, 11, 1, 18, 0, tzinfo=VIENNA)
    text = _period("Am 1. November 2025, ganztägig.")
    start, corrections = wp.plausible_start("38B/39B: Allerheiligenverkehr am 01.11.2025", text, published, end, published)
    assert start == _day(2026, 11, 1)
    assert _kinds(corrections) == [wp.STALE_YEAR]
    assert corrections[0].detail.startswith("Titel 01.11.2025 ")


def test_title_and_text_in_two_stale_years_settle_first() -> None:
    # The nearer of the two years wins (rule 1), and that one is stale too.
    published = datetime(2026, 10, 20, 9, 0, tzinfo=VIENNA)
    end = datetime(2026, 11, 1, 18, 0, tzinfo=VIENNA)
    text = _period("Am 1. November 2025, ganztägig.")
    start, corrections = wp.plausible_start("38B/39B: Allerheiligenverkehr am 01.11.2024", text, published, end, published)
    assert start == _day(2026, 11, 1)
    assert _kinds(corrections) == [wp.YEAR_CONFLICT, wp.STALE_YEAR]
    assert "gilt 01.11.2025" in corrections[0].detail and "gilt 01.11.2026" in corrections[1].detail


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("D: Gleisbau ab 20.11.2026", _day(2026, 11, 20)),  # a later title date leads
        ("D: Gleisbau ab 01.11.2026", CEMETERY_PUBLISHED),  # a running phase leads
    ],
    ids=["later title date", "running title date"],
)
def test_a_stale_text_date_the_title_outvotes_is_no_correction(title: str, expected: datetime) -> None:
    end = datetime(2026, 12, 31, 18, 0, tzinfo=VIENNA)
    start, corrections = wp.plausible_start(title, _CEMETERY_TEXT, CEMETERY_PUBLISHED, end, CEMETERY_PUBLISHED)
    assert start == expected
    assert corrections == []


def test_days_of_a_stale_year_are_vienna_days() -> None:
    # 01.01.2026 23:30 UTC is 02.01. in Vienna: 02.04. lies 90 days after it.
    published = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    end = datetime(2026, 12, 31, 18, 0, tzinfo=VIENNA)
    start, _ = wp.plausible_start("D: Gleisbau", _period("Ab 2. April 2025."), published, end, published)
    assert start == _day(2026, 4, 2)
    # 23.12.2026 23:30 UTC is 24.12. in Vienna: Christmas Eve is not after the end.
    late_end = datetime(2026, 12, 23, 23, 30, tzinfo=UTC)
    start, corrections = wp.plausible_start(
        "39B: Friedhofsverkehr", _CEMETERY_TEXT, CEMETERY_PUBLISHED, late_end, CEMETERY_PUBLISHED
    )
    assert start == _day(2026, 12, 24)
    assert _kinds(corrections) == [wp.STALE_YEAR]


# 5B Muthgasse as WL published it on 09.03.2026 beside ``time.end`` 09.03.2027
# 23:59, the date set by hand a year on.
_MUTHGASSE_TEXT = _period("Dienstag, 17. März 2025, etwa 08:00 Uhr bis voraussichtlich Juni 2026.")
MUTHGASSE_PUBLISHED = datetime(2026, 3, 9, 0, 0, tzinfo=VIENNA)
MUTHGASSE_WL_END = datetime(2027, 3, 9, 23, 59, tzinfo=VIENNA)


def test_the_end_the_text_names_for_a_placeholder_confirms_the_year() -> None:
    end = _effective_end(_MUTHGASSE_TEXT, MUTHGASSE_WL_END, MUTHGASSE_PUBLISHED)
    assert end == datetime(2026, 6, 30, 23, 59, tzinfo=VIENNA)
    start = _effective_start("5B: Umleitung Muthgasse", _MUTHGASSE_TEXT, MUTHGASSE_PUBLISHED, end, MUTHGASSE_PUBLISHED)
    assert start == _day(2026, 3, 17)  # the Tuesday the text names
    assert [c.kind for _, c in wp.collected_corrections()] == [wp.STALE_YEAR]


def test_an_end_estimated_from_a_duration_confirms_no_year() -> None:
    # "1A: Habsburgergasse" (published 13.01.2026, an 11:11 end), as if WL
    # published it anew in December 2026 while the measure ran: the end the
    # duration gives counts from 19.01.2026, the date in question.
    text = (
        "<p><strong>Haltestellenverlegung der Linie 1A in Richtung Stephansplatz U</strong></p>"
        "<p><strong>Haltestelle:</strong> Habsburgergasse</p>"
        "<p><strong>Von:</strong> Habsburgergasse 9 nach Stallburggasse</p>"
        "<p><strong>Nach:</strong> Habsburgergasse 3</p>"
        "<p><strong>Dauer:</strong> Ab 19. Jänner 2026, etwa 08:00 Uhr für etwa ein Jahr</p>"
        "<p><strong>Grund:</strong> Haussanierung</p>"
    )
    published = datetime(2026, 12, 1, 10, 0, tzinfo=VIENNA)
    end = _effective_end(text, datetime(2027, 12, 1, 11, 11, tzinfo=VIENNA), published)
    assert end == datetime(2027, 7, 20, 23, 59, tzinfo=VIENNA)
    start = _effective_start("1A: Habsburgergasse", text, published, end, published)
    assert start == published
    assert wp.collected_corrections() == []


@pytest.mark.parametrize(
    "end",
    [
        datetime(2027, 5, 19, 11, 11, tzinfo=VIENNA),  # WL's 11:11 expiry (N25/SEV U1)
        datetime(2027, 5, 13, 23, 59, tzinfo=VIENNA),  # 23:59 a year after the publication
        None,
    ],
    ids=["11:11", "23:59 a year on", "no end"],
)
def test_without_a_real_end_the_publication_stays_the_start(end: datetime | None) -> None:
    text = _period("Ab 19. Mai 2025, etwa 08:00 Uhr, Dauer unbekannt")
    published = datetime(2026, 5, 13, 18, 30, tzinfo=VIENNA)
    start, corrections = wp.plausible_start("N25/SEVU1: Kaisermühlen, V.I.C.", text, published, end, published)
    assert start == published
    assert corrections == []


@pytest.mark.parametrize(
    ("text", "published", "end"),
    [
        # A phase that runs since earlier this year: its day comes again only next year.
        ("Ab 1. Oktober 2026.", datetime(2026, 10, 20, 9, 0, tzinfo=VIENNA), datetime(2027, 12, 31, 23, 0, tzinfo=VIENNA)),
        # Running since December, published anew in January (18: Markhofgasse, 2026).
        ("Ab 19. Dezember 2025.", datetime(2026, 1, 28, 16, 0, tzinfo=VIENNA), datetime(2026, 12, 31, 23, 0, tzinfo=VIENNA)),
        # The day in the new year would lie past the end.
        ("Am 24. Dezember 2024.", CEMETERY_PUBLISHED, datetime(2026, 12, 20, 17, 0, tzinfo=VIENNA)),
    ],
    ids=["same year", "across new year", "past the end"],
)
def test_a_date_before_the_publication_is_otherwise_a_running_phase(
    text: str, published: datetime, end: datetime
) -> None:
    start, corrections = wp.plausible_start("D: Gleisbau", _period(text), published, end, published)
    assert start == published
    assert corrections == []


def test_stale_year_lead_limit_is_inclusive() -> None:
    published = datetime(2026, 1, 1, 9, 0, tzinfo=VIENNA)
    end = datetime(2026, 12, 31, 18, 0, tzinfo=VIENNA)
    at_limit = published.date() + timedelta(days=wp.STALE_YEAR_MAX_LEAD_DAYS)
    past_limit = at_limit + timedelta(days=1)
    for day, expected in ((at_limit, _day(at_limit.year, at_limit.month, at_limit.day)), (past_limit, published)):
        text = _period(f"Ab {day.day}.{day.month}.2025.")
        start, _ = wp.plausible_start("D: Gleisbau", text, published, end, published)
        assert start == expected, day


def test_stale_year_landing_on_the_publication_day_changes_nothing() -> None:
    published = datetime(2026, 11, 11, 10, 0, tzinfo=VIENNA)
    end = datetime(2026, 11, 11, 18, 0, tzinfo=VIENNA)
    start, corrections = wp.plausible_start("D: Umleitung", _period("Am 11. November 2025."), published, end, published)
    assert start == published
    assert corrections == []


def test_stale_29_february_without_a_leap_year_stays_unread() -> None:
    published = datetime(2026, 2, 10, 9, 0, tzinfo=VIENNA)
    end = datetime(2026, 3, 31, 18, 0, tzinfo=VIENNA)
    start, corrections = wp.plausible_start("D: Gleisbau", _period("Ab 29. Februar 2024."), published, end, published)
    assert start == published
    assert corrections == []


def test_stale_year_is_a_known_correction_kind(tmp_path: Path) -> None:
    assert wp.STALE_YEAR in wp.CORRECTION_KINDS
    path = tmp_path / "wl_plausibility_anomalies.json"
    wp.write_collection(path, [])
    description = json.loads(path.read_text(encoding="utf-8"))["description"]
    assert all(kind in description for kind in wp.CORRECTION_KINDS)


# --- rule 2: hard limits ------------------------------------------------------


def test_title_date_past_the_end_is_outvoted_by_the_end() -> None:
    start, corrections = wp.plausible_start("47B: Laufveranstaltung am 04.10.2027", "", PUBLISHED, RUN_END, NOW)
    assert start == PUBLISHED
    assert _kinds(corrections) == [wp.BEGIN_AFTER_END]


def test_far_lead_on_one_source_only_falls_back_to_time_start() -> None:
    start, corrections = wp.plausible_start("47B: Laufveranstaltung am 04.10.2027", "", PUBLISHED, None, NOW)
    assert start == PUBLISHED
    assert _kinds(corrections) == [wp.UNCONFIRMED_LEAD]


def test_far_lead_confirmed_by_the_text_stands() -> None:
    text = "<p><strong>Zeitraum:</strong><br />Montag, 4. Oktober 2027.</p>"
    start, corrections = wp.plausible_start("47B: Umleitung am 04.10.2027", text, PUBLISHED, None, NOW)
    assert start == _day(2027, 10, 4)
    assert corrections == []


def test_far_lead_confirmed_by_a_later_end_stands() -> None:
    end = datetime(2027, 12, 31, 23, 59, tzinfo=VIENNA)
    start, corrections = wp.plausible_start("U2: Sperre ab 04.10.2027", "", PUBLISHED, end, NOW)
    assert start == _day(2027, 10, 4)
    assert corrections == []


def test_source_start_after_source_end_is_recorded_not_hidden() -> None:
    start, corrections = wp.plausible_start("5: Störung", "", RUN_END, PUBLISHED, NOW)
    assert start == RUN_END
    assert _kinds(corrections) == [wp.SOURCE_START_AFTER_END]


# --- established rules are no correction --------------------------------------


def test_consistent_item_has_no_correction() -> None:
    text = "<p><strong>Zeitraum:</strong><br />Ab Montag, 05. Oktober 2026, etwa 06:30 Uhr.</p>"
    start, corrections = wp.plausible_start("29B/N25: Adolf-Loos-Gasse", text, PUBLISHED, None, NOW)
    assert start == _day(2026, 10, 5)
    assert corrections == []


def test_text_date_past_the_end_is_a_phase_not_a_contradiction() -> None:
    text = "<p><strong>Zeitraum:</strong><br />Ab 10. Oktober 2026 (Phase 2).</p>"
    start, corrections = wp.plausible_start("D: Gleisbau", text, PUBLISHED, RUN_END, NOW)
    assert start == PUBLISHED
    assert corrections == []


def test_placeholder_end_is_still_replaced_silently() -> None:
    end = datetime(2027, 9, 28, 11, 11, tzinfo=VIENNA)
    text = "<p><strong>Zeitraum:</strong><br />Von 1. Oktober 2026 bis 31. Oktober 2026.</p>"
    assert wp.plausible_end(text, end, PUBLISHED) == datetime(2026, 10, 31, 23, 59, tzinfo=VIENNA)


# --- recording ------------------------------------------------------------------


def test_effective_start_notes_its_corrections() -> None:
    start = _effective_start("47B: Laufveranstaltung am 04.10.2027", _RUN_TEXT, PUBLISHED, RUN_END, NOW)
    assert start == _day(2026, 10, 4)
    collected = wp.collected_corrections()
    assert [(title, c.kind) for title, c in collected] == [
        ("47B: Laufveranstaltung am 04.10.2027", wp.YEAR_CONFLICT)
    ]
    wp.reset_corrections()
    assert wp.collected_corrections() == []


def test_merge_counts_a_correction_once_per_day() -> None:
    noted = [("47B: Lauf", wp.Correction(wp.YEAR_CONFLICT, "a"))]
    first = wp.merge_corrections([], noted, date(2026, 10, 2))
    again = wp.merge_corrections(first, noted, date(2026, 10, 2))
    later = wp.merge_corrections(again, [("47B: Lauf", wp.Correction(wp.YEAR_CONFLICT, "b"))], date(2026, 10, 3))
    assert again == first
    assert later == [
        {
            "kind": wp.YEAR_CONFLICT,
            "title": "47B: Lauf",
            "first_seen": "2026-10-02",
            "last_seen": "2026-10-03",
            "days_seen": 2,
            "detail": "b",
        }
    ]


def test_merge_drops_invalid_records() -> None:
    junk: list[object] = [
        "x",
        {"kind": "unknown", "title": "t", "first_seen": "2026-10-01", "last_seen": "2026-10-01", "days_seen": 1},
        {"kind": wp.YEAR_CONFLICT, "title": "t", "first_seen": "gestern", "last_seen": "2026-10-01", "days_seen": 1},
    ]
    assert wp.merge_corrections(junk, [], date(2026, 10, 2)) == []


def test_record_writes_only_when_there_is_something(tmp_path: Path) -> None:
    path = tmp_path / "wl_plausibility_anomalies.json"
    assert wp.record_corrections(path, [], date(2026, 10, 2)) == 0
    assert not path.exists()
    noted = [("47B: Lauf", wp.Correction(wp.BEGIN_AFTER_END, "d"))]
    assert wp.record_corrections(path, noted, date(2026, 10, 2)) == 1
    assert wp.record_corrections(path, noted, date(2026, 10, 3)) == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["anomalies"][0]["days_seen"] == 2


def _load_update_script() -> object:
    path = Path(__file__).resolve().parents[1] / "scripts" / "update_wl_cache.py"
    spec = importlib.util.spec_from_file_location("update_wl_cache_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cache_script_collects_the_fetch_corrections(tmp_path: Path) -> None:
    script = _load_update_script()
    path = tmp_path / "anomalies.json"
    script.record_plausibility_anomalies(path)  # type: ignore[attr-defined]
    assert not path.exists()
    wp.note_corrections("47B: Lauf", [wp.Correction(wp.YEAR_CONFLICT, "d")])
    script.record_plausibility_anomalies(path)  # type: ignore[attr-defined]
    records = json.loads(path.read_text(encoding="utf-8"))["anomalies"]
    assert [(r["kind"], r["title"]) for r in records] == [(wp.YEAR_CONFLICT, "47B: Lauf")]
