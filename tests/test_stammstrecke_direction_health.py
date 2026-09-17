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

    note = render_direction_coverage_note(window, now=NOW, all_rows=window + older)

    assert "Eingeschränkte Abdeckung" in note
    assert "Praterstern" in note
    assert "14.08.2026" in note, "the note must name when the direction was last seen"
    assert "Meidling" in note
    assert "kein Korridor-Gesamtwert" in note


def test_coverage_note_is_empty_when_both_directions_report() -> None:
    """Self-clearing: the caveat must vanish the moment coverage is complete."""
    from scripts.generate_markdown_stats import render_direction_coverage_note

    window = [
        _sm_row(NOW - timedelta(minutes=m), d)
        for m in range(5, 125, 20)
        for d in DIRECTIONS
    ]

    assert render_direction_coverage_note(window, now=NOW, all_rows=window) == ""


def test_coverage_note_works_for_either_direction() -> None:
    """Symmetric by construction, in both directions.

    The note is derived from the data, so whichever direction goes silent
    reads the same way. Pinned because the 2026 outage only ever exercised
    one of the two, and a hard-coded "Praterstern" would have passed every
    test written against it.
    """
    from scripts.generate_markdown_stats import render_direction_coverage_note

    for silent, reporting in (("Praterstern", "Meidling"), ("Meidling", "Praterstern")):
        window = [_sm_row(NOW - timedelta(hours=h), reporting) for h in range(1, 9)]
        older = [_sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), silent)]

        note = render_direction_coverage_note(window, now=NOW, all_rows=window + older)

        assert f"Richtung **{silent}** auf der Stammstrecke" in note
        assert f"nur Richtung **{reporting}** ab" in note
        assert "14.08.2026" in note, "the note must name when it was last seen"
        assert "kein Korridor-Gesamtwert" in note


def test_coverage_note_covers_both_directions_at_once() -> None:
    """The case the banner used to suppress.

    A whole-corridor gap was treated as the freshness checks' business — but
    those live in ``scripts/health_check.py`` and reach an operator, never a
    reader. The README and the website simply showed stale numbers with no
    caveat at all, which is the worse failure of the two: a partial sample at
    least still describes *something* current.
    """
    from scripts.generate_markdown_stats import render_direction_coverage_note

    history = [
        _sm_row(datetime(2026, 8, 20, 5, 30, tzinfo=VIENNA), "Meidling"),
        _sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern"),
    ]

    note = render_direction_coverage_note([], now=NOW, all_rows=history)

    assert "Richtung **Meidling** und **Praterstern** auf der Stammstrecke" in note
    assert "20.08.2026" in note and "14.08.2026" in note, (
        "with several directions silent each one needs its own date — a single "
        "date would stand for a sentence covering all of them"
    )
    assert "älteren Daten" in note, (
        "with nothing reporting the figures are not restricted, they are old"
    )
    assert "Korridor-Gesamtwert" not in note, (
        "that clause belongs to the partial case, where something IS shown"
    )


def test_coverage_note_stays_silent_without_any_history() -> None:
    """A fresh clone has nothing to caveat.

    Distinguishing this from the whole-corridor gap is the reason the note
    consults *all_rows* and not just the window: "never measured" and
    "stopped measuring" look identical inside the window alone.
    """
    from scripts.generate_markdown_stats import render_direction_coverage_note

    assert render_direction_coverage_note([], now=NOW, all_rows=[]) == ""
    # Rows exist, but for no canonical direction → still nothing that stopped.
    odd = [_sm_row(NOW - timedelta(hours=1), "Unbekannt")]
    assert render_direction_coverage_note(odd, now=NOW, all_rows=odd) == ""


def test_coverage_note_asserts_no_cause() -> None:
    """The note reports the observation, never a reason.

    It used to carry an operator-maintained cause ("Streckensperre –
    Bauarbeiten und Kabelbrand-Folgen"), which was right for the 2026 outage
    and wrong for every other way a direction falls silent — a timetable
    change, a provider renaming the platform, our own monitor failing. The
    ledger records that measurements stopped, never why.
    """
    # Bound as a module rather than by name: the point is that a name is
    # ABSENT, which ``from ... import`` cannot express. Imported off the
    # package so the module path is not pulled in by two different import
    # forms in one file (CodeQL py/import-and-import-from).
    from scripts import generate_markdown_stats as gms

    assert not hasattr(gms, "DIRECTION_OUTAGE_CAUSE"), (
        "the hard-coded cause is gone; re-adding it re-creates a caption that "
        "outlives the outage it describes"
    )

    window = [_sm_row(NOW - timedelta(hours=h), "Meidling") for h in range(1, 9)]
    older = [_sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern")]
    note = gms.render_direction_coverage_note(window, now=NOW, all_rows=window + older)

    for word in ("Kabelbrand", "Bauarbeiten", "Streckensperre"):
        assert word not in note, f"the note names a specific cause: {word}"


def test_site_coverage_texts_name_no_cause_and_match_python() -> None:
    """Both surfaces say the same thing, and neither invents a reason."""
    import re
    from pathlib import Path

    from scripts.generate_markdown_stats import COVERAGE_ORIGIN_LABEL

    js = (
        Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js"
    ).read_text(encoding="utf-8")
    code_only = re.sub(r"//.*", "", js)

    for word in ("Kabelbrand", "cable-fire", "Bauarbeiten", "Streckensperre"):
        assert word not in code_only, f"site.js still names a cause: {word}"

    match = re.search(r'const COVERAGE_ORIGIN = "([^"]+)";', js)
    assert match is not None, "site.js must declare COVERAGE_ORIGIN"
    assert match.group(1) == COVERAGE_ORIGIN_LABEL, (
        "the measurement point must read the same on both surfaces"
    )


def _peer_run(now: datetime, direction: str, count: int, *, step_minutes: int = 20) -> list[Any]:
    """*count* journeys in *direction*, newest ``step_minutes`` before *now*."""
    return [
        _sm_row(now - timedelta(minutes=step_minutes * (i + 1)), direction)
        for i in range(count)
    ]


def test_coverage_note_waits_an_hour_before_it_appears() -> None:
    """Below the hour there is no story yet.

    A gap of a few minutes is the corridor's normal rhythm — observations
    arrive every ~30 min at best — so anything shorter than the hour would
    describe the sampler, not the service.
    """
    from scripts.generate_markdown_stats import (
        DIRECTION_SILENCE_NOTICE_HOURS,
        render_direction_coverage_note,
    )

    assert DIRECTION_SILENCE_NOTICE_HOURS == 1.0

    for minutes, expected in ((30, False), (59, False), (61, True)):
        rows = [
            *_peer_run(NOW, "Meidling", 12, step_minutes=5),
            _sm_row(NOW - timedelta(minutes=minutes), "Praterstern"),
        ]
        note = render_direction_coverage_note(rows, now=NOW, all_rows=rows)
        assert bool(note) is expected, (
            f"{minutes} min of silence should "
            f"{'raise' if expected else 'not raise'} the note"
        )


def test_coverage_note_clears_the_moment_the_direction_returns() -> None:
    """The restart has to be picked up without anyone editing Markdown.

    This is the requirement the whole rule exists to serve: the closure is
    temporary, and a caveat that outlives it is worse than none at all.
    """
    from scripts.generate_markdown_stats import render_direction_coverage_note

    silent = [
        *_peer_run(NOW, "Meidling", 12, step_minutes=5),
        _sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern"),
    ]
    assert render_direction_coverage_note(silent, now=NOW, all_rows=silent) != ""

    # One northbound journey, and the caveat is gone on the next tick.
    resumed = [*silent, _sm_row(NOW - timedelta(minutes=4), "Praterstern")]
    assert render_direction_coverage_note(resumed, now=NOW, all_rows=resumed) == ""


def test_a_quiet_night_in_both_directions_raises_nothing() -> None:
    """The reason the hour is not applied to the clock alone.

    The Stammstrecke stops for about 3:41 h every night (01:12 -> 04:53,
    strikingly consistent across 2026). A bare "quiet for an hour" rule
    raised the banner at 10.6 % of 4 324 replayed healthy ticks, 87 % of them
    in the small hours — a caveat that appears every night is one readers
    stop seeing. Silence only counts against evidence that trains are in
    fact running.
    """
    from scripts.generate_markdown_stats import (
        CORRIDOR_SILENCE_NOTICE_HOURS,
        find_silent_coverage_directions,
        render_direction_coverage_note,
    )

    for hours in (1.5, 3.0, 5.0, 7.5):
        rows = [
            _sm_row(NOW - timedelta(hours=hours) - timedelta(minutes=20 * i), d)
            for i in range(20)
            for d in DIRECTIONS
        ]
        assert find_silent_coverage_directions(rows, now=NOW) == [], (
            f"a corridor-wide pause of {hours} h is a timetable, not a fault"
        )
        assert render_direction_coverage_note(rows, now=NOW, all_rows=rows) == ""

    # Past any healthy pause it is no longer a night — then it must be said.
    long_gap = [
        _sm_row(
            NOW
            - timedelta(hours=CORRIDOR_SILENCE_NOTICE_HOURS + 1)
            - timedelta(minutes=20 * i),
            d,
        )
        for i in range(20)
        for d in DIRECTIONS
    ]
    assert find_silent_coverage_directions(long_gap, now=NOW) == list(DIRECTIONS)


def test_silence_counts_only_against_proof_that_trains_are_running() -> None:
    """A peer direction is what turns an hour of quiet into a statement.

    Without it the rule cannot tell "this direction stopped" from "nothing
    was sampled" — and it is the asymmetry, not the clock, that makes the
    northbound gap worth a reader's attention.
    """
    from scripts.generate_markdown_stats import (
        DIRECTION_SILENCE_NOTICE_PEER_ROWS,
        find_silent_coverage_directions,
    )

    below = DIRECTION_SILENCE_NOTICE_PEER_ROWS - 1
    rows = [
        *_peer_run(NOW, "Meidling", below, step_minutes=5),
        _sm_row(NOW - timedelta(hours=3), "Praterstern"),
    ]
    assert find_silent_coverage_directions(rows, now=NOW) == [], (
        f"{below} peer journeys do not yet prove the corridor is running"
    )

    enough = [
        *_peer_run(NOW, "Meidling", DIRECTION_SILENCE_NOTICE_PEER_ROWS, step_minutes=5),
        _sm_row(NOW - timedelta(hours=3), "Praterstern"),
    ]
    assert find_silent_coverage_directions(enough, now=NOW) == ["Praterstern"]


def test_peer_evidence_does_not_expire_overnight() -> None:
    """The banner must not blink off during the nightly pause of an outage.

    Peer rows are counted over the whole silent stretch rather than over a
    trailing window, so an ongoing outage stays flagged at 03:00 — when the
    peer is asleep too — exactly as it is at noon. Pinned because the
    obvious implementation (is a peer reporting *right now*?) flickers off
    every night, and a caveat that comes and goes reads like a glitch.
    """
    from scripts.generate_markdown_stats import find_silent_coverage_directions

    # Southbound ran all day, then stopped for the night; northbound has been
    # gone for weeks. It is 03:00 and nothing is moving in either direction.
    night = datetime(2026, 9, 14, 3, 0, tzinfo=VIENNA)
    rows = [
        *_peer_run(night - timedelta(hours=2), "Meidling", 40, step_minutes=20),
        _sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern"),
    ]

    assert find_silent_coverage_directions(rows, now=night) == ["Praterstern"], (
        "the northbound gap is still the story at 03:00, and southbound "
        "being asleep is not evidence that it recovered"
    )


def test_coverage_note_does_not_call_fresh_figures_a_partial_sample() -> None:
    """Two statements, true at different times.

    A direction that fell quiet an hour ago still contributes thousands of
    rows to a 30-day window. Saying those figures "cover only the other
    direction" would be plainly false, so the note states the observation and
    stops there until the silence has actually hollowed out the window.
    """
    from scripts.generate_markdown_stats import render_direction_coverage_note

    window = [
        *_peer_run(NOW, "Meidling", 12, step_minutes=5),
        # Northbound is quiet now, but reported plenty inside the window.
        *_peer_run(NOW - timedelta(hours=3), "Praterstern", 300, step_minutes=20),
    ]
    note = render_direction_coverage_note(window, now=NOW, all_rows=window)

    assert "Aktuell keine Fahrten" in note and "Praterstern" in note
    assert "Korridor-Gesamtwert" not in note, (
        "the window still holds northbound rows — the figures are not a "
        "part-corridor sample yet"
    )
    assert "älteren Daten" not in note

    # Once the silence has outlived the window, the caveat lands. The window
    # is the 30-day slice; the ledger still remembers when it stopped.
    southbound = _peer_run(NOW, "Meidling", 12, step_minutes=5)
    ledger = [
        *southbound,
        _sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern"),
    ]
    assert "kein Korridor-Gesamtwert" in render_direction_coverage_note(
        southbound, now=NOW, all_rows=ledger
    )


def test_summary_ships_the_evidence_the_browser_cannot_derive() -> None:
    """``site.js`` re-decides on the reader's clock, so it needs the inputs.

    The raw ledger stopped being shipped when the dashboard moved to
    ``stats-summary.json``; ``peer_rows_since`` is the one number the page
    cannot recompute. The thresholds travel with it so the two surfaces
    cannot drift into different rules.
    """
    from scripts.generate_markdown_stats import (
        CORRIDOR_SILENCE_NOTICE_HOURS,
        DIRECTION_SILENCE_NOTICE_HOURS,
        DIRECTION_SILENCE_NOTICE_PEER_ROWS,
        _direction_coverage,
    )

    rows = [
        *_peer_run(NOW, "Meidling", 9, step_minutes=20),
        _sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern"),
    ]
    payload = _direction_coverage(rows, all_rows=rows, window_days=30)

    assert payload["silence_hours"] == DIRECTION_SILENCE_NOTICE_HOURS
    assert payload["peer_rows_required"] == DIRECTION_SILENCE_NOTICE_PEER_ROWS
    assert payload["corridor_silence_hours"] == CORRIDOR_SILENCE_NOTICE_HOURS

    directions = payload["directions"]
    assert isinstance(directions, dict)
    assert directions["Praterstern"]["peer_rows_since"] == 9, (
        "every southbound journey since the last northbound one is evidence"
    )
    assert directions["Meidling"]["peer_rows_since"] == 0, (
        "nothing ran after the newest southbound row"
    )


def test_site_applies_the_same_thresholds_as_python() -> None:
    """The rule lives on both surfaces; only one of them may define it."""
    import re
    from pathlib import Path

    from scripts.generate_markdown_stats import (
        CORRIDOR_SILENCE_NOTICE_HOURS,
        DIRECTION_SILENCE_NOTICE_PEER_ROWS,
    )

    js = (
        Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js"
    ).read_text(encoding="utf-8")

    # Each threshold must actually be READ off the payload. A bare substring
    # check would pass on "corridor_silence_hours" alone, and on a page that
    # merely mentions the key while applying a constant of its own.
    for key in ("silence_hours", "corridor_silence_hours", "peer_rows_required"):
        assert re.search(rf"coverage\s*&&\s*coverage\.{key}\b", js), (
            f"site.js must read {key!r} from the summary, not hard-code it"
        )

    # The hard-coded fallbacks guard a stale cached summary; they must agree
    # with Python, or a cached page would quietly apply a different rule.
    corridor = re.search(r"coverage\.corridor_silence_hours,\s*(\d+)", js)
    peers = re.search(r"coverage\.peer_rows_required,\s*(\d+)", js)
    assert corridor and int(corridor.group(1)) == int(CORRIDOR_SILENCE_NOTICE_HOURS)
    assert peers and int(peers.group(1)) == DIRECTION_SILENCE_NOTICE_PEER_ROWS


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
