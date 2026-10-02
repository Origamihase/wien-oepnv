"""Plausibility check of a WL item's validity (``src/providers/wl_plausibility.py``).

On 2026-10-02 the title "47B: Laufveranstaltung am 04.10.2027" (a typo; text
and ``time.end`` say 04.10.2026) moved ``starts_at`` a year past ``ends_at``
and the German feed read "[Ab 04.10.2027]" for a run that Sunday.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.providers import wl_plausibility as wp
from src.providers.wl_fetch import _effective_start

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
