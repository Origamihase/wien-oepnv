"""Regression tests for silent-direction detection (audit B.1 / B.2).

Audit ``docs/archive/audits/audit-2026-09-13.md`` (§ *Audit Update 19:20*):

**B.1** — the Stammstrecke monitor stopped producing rows for the northbound
direction (``Praterstern``) on 2026-08-14. The cause is upstream and real (a
closure diverting those services off the two trunk platforms at Wien Hbf), not
a code defect, and the direction must stay wired up so the restart is picked up
automatically.

**B.2** — the actual defect: nothing noticed for 30 days. ``_process_tick``
logged the per-direction counts at INFO and returned ``"ok"`` regardless, and
``health_check.py`` did not look at the Stammstrecke ledgers at all, so the
published 30-day figures kept presenting a half-corridor sample as a
whole-corridor one.

The detection rule is deliberately *relative* (a direction is silent only while
a peer direction is demonstrably active) rather than an absolute staleness
threshold. Measured over the healthy period 2026-05-17..08-13 (7589 rows), the
gap between consecutive rows has a median of 0.5 h and a p99 of 3.5-3.7 h, but
reaches 7.8 h (Meidling) and 11.5 h (Praterstern) overnight. A 6 h absolute rule
would have produced 5 false alarms; the relative rule produced none across 2113
hourly samples while still flagging the real outage at all 692 samples after it
began. ``test_quiet_night_in_both_directions_is_not_an_alarm`` pins exactly that
distinction — it is the test that stops someone "simplifying" the rule back into
an absolute threshold.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from zoneinfo import ZoneInfo

from src.utils import stats as stats_mod
from src.utils.stats import (
    DIRECTION_SILENCE_MIN_PEER_ROWS,
    STAMMSTRECKE_HEADER,
    WEEKDAY_LABELS,
    find_silent_directions,
    summarise_direction_activity,
)

VIENNA = ZoneInfo("Europe/Vienna")
DIRECTIONS = ("Meidling", "Praterstern")
NOW = datetime(2026, 9, 13, 20, 0, tzinfo=VIENNA)


def _write_ledger(stats_dir: Path, rows: list[tuple[datetime, str]]) -> None:
    """Write a ``stammstrecke_<year>.csv`` containing *rows* (timestamp, direction)."""
    stats_dir.mkdir(parents=True, exist_ok=True)
    by_year: dict[int, list[tuple[datetime, str]]] = {}
    for ts, direction in rows:
        by_year.setdefault(ts.year, []).append((ts, direction))
    for year, year_rows in by_year.items():
        path = stats_dir / f"stammstrecke_{year:04d}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(STAMMSTRECKE_HEADER)
            for ts, direction in sorted(year_rows):
                writer.writerow(
                    [
                        ts.isoformat(),
                        WEEKDAY_LABELS[ts.weekday()],
                        f"{ts.hour:02d}",
                        direction,
                        "0.0",
                    ]
                )


def _ticks(end: datetime, count: int, direction: str, *, step_min: int = 30) -> list[tuple[datetime, str]]:
    """``count`` rows for *direction*, every ``step_min`` minutes, ending at *end*."""
    return [(end - timedelta(minutes=step_min * i), direction) for i in range(count)]


@pytest.fixture()
def stats_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "stats"
    folder.mkdir()
    monkeypatch.setattr(stats_mod, "DEFAULT_STATS_DIR", folder)
    return folder


# --- the rule itself --------------------------------------------------------


def test_silent_direction_is_detected_while_peer_reports(stats_dir: Path) -> None:
    """The B.1 shape: one direction dead, the other healthy."""
    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))

    assert find_silent_directions(directions=DIRECTIONS, now=NOW) == ["Praterstern"]


def test_both_directions_reporting_is_not_an_alarm(stats_dir: Path) -> None:
    _write_ledger(
        stats_dir, _ticks(NOW, 8, "Meidling") + _ticks(NOW, 8, "Praterstern")
    )

    assert find_silent_directions(directions=DIRECTIONS, now=NOW) == []


def test_quiet_night_in_both_directions_is_not_an_alarm(stats_dir: Path) -> None:
    """Both directions quiet together must never alarm.

    This is the false-positive guard the relative rule exists for. The healthy
    ledger contains gaps of 7.8 h and 11.5 h with NO rows in either direction
    (overnight); an absolute "last row older than 6 h" rule fires here, the
    relative rule does not. A whole-corridor outage is the feed-freshness and
    updater checks' business, not a direction fault.
    """
    # Last activity 12 h ago in BOTH directions — wider than the 11.5 h maximum
    # observed during healthy operation.
    long_ago = NOW - timedelta(hours=12)
    _write_ledger(
        stats_dir,
        _ticks(long_ago, 6, "Meidling") + _ticks(long_ago, 6, "Praterstern"),
    )

    assert find_silent_directions(directions=DIRECTIONS, now=NOW) == []


def test_peer_below_min_rows_is_not_enough_to_accuse(stats_dir: Path) -> None:
    """A barely-awake peer does not justify calling the other direction dead."""
    _write_ledger(
        stats_dir, _ticks(NOW, DIRECTION_SILENCE_MIN_PEER_ROWS - 1, "Meidling")
    )

    assert find_silent_directions(directions=DIRECTIONS, now=NOW) == []


def test_empty_ledger_is_not_an_alarm(stats_dir: Path) -> None:
    """No ledger at all → nothing to compare; other checks own that case."""
    assert find_silent_directions(directions=DIRECTIONS, now=NOW) == []


def test_single_direction_has_no_peer_to_compare_against(stats_dir: Path) -> None:
    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))

    assert find_silent_directions(directions=("Meidling",), now=NOW) == []


def test_recovery_clears_the_alarm(stats_dir: Path) -> None:
    """The direction must be usable again the moment upstream restarts.

    B.1 explicitly requires the northbound direction to stay wired up so the
    restart is picked up automatically — this pins that a single fresh row is
    enough to clear the alarm.
    """
    _write_ledger(
        stats_dir, _ticks(NOW, 8, "Meidling") + [(NOW - timedelta(minutes=5), "Praterstern")]
    )

    assert find_silent_directions(directions=DIRECTIONS, now=NOW) == []


def test_summarise_direction_activity_counts_rows(stats_dir: Path) -> None:
    _write_ledger(
        stats_dir, _ticks(NOW, 5, "Meidling") + _ticks(NOW, 2, "Praterstern")
    )

    activity = {
        a.direction: a.rows
        for a in summarise_direction_activity(directions=DIRECTIONS, now=NOW)
    }
    assert activity == {"Meidling": 5, "Praterstern": 2}


def test_rows_outside_the_window_do_not_count(stats_dir: Path) -> None:
    _write_ledger(
        stats_dir,
        _ticks(NOW, 8, "Meidling")
        + [(NOW - timedelta(hours=9), "Praterstern")],
    )

    assert find_silent_directions(directions=DIRECTIONS, now=NOW) == ["Praterstern"]


# --- health_check integration ----------------------------------------------


def test_health_check_fails_on_silent_direction(stats_dir: Path) -> None:
    from scripts.health_check import check_stammstrecke_directions

    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))

    check = check_stammstrecke_directions(NOW)
    assert check.ok is False
    assert "Praterstern" in check.summary
    assert "Meidling=8" in check.detail


def test_health_check_passes_when_both_report(stats_dir: Path) -> None:
    from scripts.health_check import check_stammstrecke_directions

    _write_ledger(
        stats_dir, _ticks(NOW, 8, "Meidling") + _ticks(NOW, 8, "Praterstern")
    )

    check = check_stammstrecke_directions(NOW)
    assert check.ok is True
    assert "beide Richtungen liefern" in check.summary


def test_health_check_passes_when_corridor_is_quiet(stats_dir: Path) -> None:
    from scripts.health_check import check_stammstrecke_directions

    check = check_stammstrecke_directions(NOW)
    assert check.ok is True
    assert "ruhig" in check.summary


def test_health_check_window_is_env_tunable(
    stats_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.health_check import check_stammstrecke_directions

    # Praterstern's only row is 9 h old: inside a 12 h window, outside 6 h.
    _write_ledger(
        stats_dir,
        _ticks(NOW, 20, "Meidling")
        + [(NOW - timedelta(hours=9), "Praterstern")],
    )

    assert check_stammstrecke_directions(NOW).ok is False

    monkeypatch.setenv("HEALTH_STAMMSTRECKE_WINDOW_HOURS", "12")
    assert check_stammstrecke_directions(NOW).ok is True


def test_health_check_is_registered_in_the_report() -> None:
    """The check must actually run — an unwired check alarms on nothing."""
    import inspect

    # ``from``-style to match the other ``scripts.health_check`` imports in this
    # file. Mixing ``import x`` and ``from x import y`` for one module trips
    # CodeQL's py/import-and-import-from.
    from scripts.health_check import main as health_check_main

    source = inspect.getsource(health_check_main)
    assert "check_stammstrecke_directions(now)" in source


# --- monitor-script integration --------------------------------------------


def _degraded_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    """DEGRADED lines captured on the monitor's logger.

    Uses ``record.getMessage()`` rather than ``record.message``: the latter is
    only populated once some handler has formatted the record, which depends on
    what the rest of the session left attached to the root logger. Interpolating
    here is deterministic regardless of handler state.
    """
    return [
        rec.getMessage()
        for rec in caplog.records
        if "DEGRADED" in rec.getMessage()
    ]


def test_monitor_reports_silent_direction(
    stats_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import scripts.update_stammstrecke_hbf as hbf

    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))

    with caplog.at_level(logging.WARNING, logger=hbf.LOGGER.name):
        silent = hbf._report_silent_directions(NOW)

    assert silent == ["Praterstern"]
    degraded = _degraded_records(caplog)
    assert degraded, "the monitor must log the degraded state loudly"
    assert "Praterstern" in degraded[0]


def test_monitor_silent_when_healthy(
    stats_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import scripts.update_stammstrecke_hbf as hbf

    _write_ledger(
        stats_dir, _ticks(NOW, 8, "Meidling") + _ticks(NOW, 8, "Praterstern")
    )

    with caplog.at_level(logging.WARNING, logger=hbf.LOGGER.name):
        assert hbf._report_silent_directions(NOW) == []
    assert not _degraded_records(caplog)


def test_monitor_emits_actions_annotation_only_in_ci(
    stats_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.update_stammstrecke_hbf as hbf

    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    hbf._report_silent_directions(NOW)
    assert "::warning" not in capsys.readouterr().out

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    hbf._report_silent_directions(NOW)
    out = capsys.readouterr().out
    assert "::warning title=stammstrecke-direction::" in out
    assert "Praterstern" in out


def test_monitor_degradation_never_fails_the_tick(
    stats_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent direction must not abort the cycle.

    The workflow runs the monitor under ``bash -e``, so a non-zero exit would
    stop the feed build every 30 minutes for the whole duration of a known
    closure. Collection has to keep running while half the corridor is down;
    the alarm is health_check's job.
    """
    import scripts.update_stammstrecke_hbf as hbf

    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))
    assert hbf._report_silent_directions(NOW) == ["Praterstern"]

    # An unreadable ledger must not crash the tick either.
    monkeypatch.setattr(
        hbf, "find_silent_directions", lambda **_kw: (_ for _ in ()).throw(OSError("boom"))
    )
    assert hbf._report_silent_directions(NOW) == []


# --- published-figure transparency (audit B.1, Aufgabe 1.2) -----------------


def _sm_row(ts: datetime, direction: str, delay: float = 0.0) -> Any:
    from scripts.generate_markdown_stats import StammstreckeRow

    return StammstreckeRow(
        timestamp=ts,
        weekday=WEEKDAY_LABELS[ts.weekday()],
        hour=ts.hour,
        direction=direction,
        delay_minutes=delay,
    )


def test_canonical_directions_match_the_producer() -> None:
    """The stats tuple and the monitor's labels must not drift apart.

    ``src.utils.stats`` re-declares the labels instead of importing them (it is
    stdlib-only and the monitor pulls in ``requests``), so this is the guard
    that keeps the copy honest.
    """
    # Module-style to match the other ``scripts.update_stammstrecke_hbf``
    # imports in this file (the monitor tests below monkeypatch attributes on
    # the module object, so that style has to stay). Mixing both trips CodeQL's
    # py/import-and-import-from.
    import scripts.update_stammstrecke_hbf as hbf

    from src.utils.stats import STAMMSTRECKE_DIRECTIONS

    assert tuple(hbf.DIRECTION_LABELS) == tuple(STAMMSTRECKE_DIRECTIONS)


def test_coverage_note_names_direction_and_last_seen_date() -> None:
    from scripts.generate_markdown_stats import render_direction_coverage_note

    window = [_sm_row(NOW - timedelta(hours=h), "Meidling") for h in range(1, 9)]
    older = [_sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern")]

    note = render_direction_coverage_note(window, all_rows=window + older)

    assert "Eingeschränkte Abdeckung" in note
    assert "Praterstern" in note
    assert "14.08.2026" in note, "the note must name when the direction was last seen"
    assert "Meidling" in note
    assert "kein Korridor-Gesamtwert" in note


def test_coverage_note_is_empty_when_both_directions_report() -> None:
    """Self-clearing: the caveat must vanish the moment coverage is complete."""
    from scripts.generate_markdown_stats import render_direction_coverage_note

    window = [
        _sm_row(NOW - timedelta(hours=h), d)
        for h in range(1, 5)
        for d in DIRECTIONS
    ]

    assert render_direction_coverage_note(window, all_rows=window) == ""


def test_coverage_note_is_empty_for_an_empty_or_fully_dark_window() -> None:
    from scripts.generate_markdown_stats import render_direction_coverage_note

    assert render_direction_coverage_note([], all_rows=[]) == ""
    # Rows exist but none for any canonical direction → not a coverage caveat.
    odd = [_sm_row(NOW - timedelta(hours=1), "Unbekannt")]
    assert render_direction_coverage_note(odd, all_rows=odd) == ""


def test_readme_blocks_carry_the_coverage_note() -> None:
    from scripts.generate_markdown_stats import (
        render_readme_ausfaelle_block,
        render_readme_stammstrecke_block,
    )

    window = [_sm_row(NOW - timedelta(hours=h), "Meidling") for h in range(1, 9)]
    note = "> ⚠️ **Eingeschränkte Abdeckung:** Testhinweis.\n\n"

    sm_block = render_readme_stammstrecke_block(window, now=NOW, coverage_note=note)
    assert sm_block.startswith("> ⚠️ **Eingeschränkte Abdeckung:**")
    assert "| Beobachtungen (gesamt) |" in sm_block

    au_block = render_readme_ausfaelle_block([], now=NOW, coverage_note=note)
    assert au_block.startswith("> ⚠️ **Eingeschränkte Abdeckung:**")


def test_dashboard_direction_section_carries_the_coverage_note() -> None:
    from scripts.generate_markdown_stats import (
        StammstreckeAggregate,
        _format_directions_section,
    )

    agg = StammstreckeAggregate(by_direction={"Meidling": 500, "Praterstern": 400})
    note = "> ⚠️ **Eingeschränkte Abdeckung:** Testhinweis."

    # The annual aggregate still holds pre-outage rows for both directions, so
    # only the caller-supplied (window-derived) note can surface the problem.
    lines = _format_directions_section(agg, coverage_note=note)
    assert any("Eingeschränkte Abdeckung" in line for line in lines)

    clean = _format_directions_section(agg)
    assert not any("Eingeschränkte Abdeckung" in line for line in clean)


def test_dashboard_flags_a_direction_absent_from_the_whole_year() -> None:
    from scripts.generate_markdown_stats import (
        StammstreckeAggregate,
        _format_directions_section,
    )

    agg = StammstreckeAggregate(by_direction={"Meidling": 500})
    lines = _format_directions_section(agg)
    assert any("Praterstern" in line for line in lines)


def test_site_js_and_python_agree_on_the_directions() -> None:
    """The static site re-declares the labels; keep it in step with Python."""
    import re
    from pathlib import Path

    from src.utils.stats import STAMMSTRECKE_DIRECTIONS

    js = (
        Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js"
    ).read_text(encoding="utf-8")
    match = re.search(r"const STAMMSTRECKE_DIRECTIONS = \[([^\]]*)\]", js)
    assert match, "site.js no longer declares STAMMSTRECKE_DIRECTIONS"
    js_dirs = tuple(re.findall(r'"([^"]+)"', match.group(1)))
    assert js_dirs == tuple(STAMMSTRECKE_DIRECTIONS)
