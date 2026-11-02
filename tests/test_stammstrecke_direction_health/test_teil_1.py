"""Teil 1 der Tests aus tests/test_stammstrecke_direction_health.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import inspect
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.utils.stats import (
    DIRECTION_SILENCE_MIN_PEER_ROWS,
    find_silent_directions,
    summarise_direction_activity,
)

from ._support import (
    DIRECTIONS,
    NOW,
    VIENNA,
    _bound_stub,
    _degraded_records,
    _peer_run,
    _sm_row,
    _ticks,
    _write_ledger,
)


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


def test_health_check_notes_silent_direction_without_failing(stats_dir: Path) -> None:
    """A silent direction is a note, never a red run (decision 2026-10-03).

    The known closure Hbf - Praterstern made every run red from 2026-09-13 on,
    so the failure mail no longer told a real outage apart from it.
    """
    from scripts.health_check import check_stammstrecke_directions

    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))

    check = check_stammstrecke_directions(NOW)
    assert check.ok is True
    assert check.note is True
    assert check.summary.startswith("HINWEIS")
    assert "Praterstern" in check.summary
    assert "Meidling=8" in check.detail


def test_health_check_note_keeps_the_run_green(
    stats_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() exits 0 with only the note, and annotates it as a warning."""
    # ``from``-style only: this file already imports ``scripts.health_check``
    # that way, and mixing both styles trips CodeQL's
    # py/import-and-import-from. Patching goes through the dotted path.
    from scripts.health_check import Check, check_stammstrecke_directions
    from scripts.health_check import main as health_check_main

    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))
    note = check_stammstrecke_directions(NOW)
    healthy = Check("Quelle", ok=True, summary="OK")
    target = "scripts.health_check"
    # Every other check is stubbed, read from the module: they read the real
    # repository, the environment and the network. The test passed only
    # while an earlier test left ``VOR_ACCESS_ID`` set (test-suite audit
    # 2026-10-28), a real Feed-Wächter finding or an aged station source or a
    # side-step record would have decided it too, and since 2026-10-31 the
    # published feeds were fetched for real (the Actions API is asked
    # wherever ``GITHUB_TOKEN`` and ``GITHUB_REPOSITORY`` are set). A list by
    # hand missed ``Pages-Build`` and ``Übersetzung`` (review of 2026-11-01).
    module = sys.modules[target]
    checks = [
        name
        for name, value in vars(module).items()
        if name.startswith("check_")
        and inspect.isfunction(value)
        and value.__module__ == target
    ]
    assert "check_translation" in checks and "check_pages_build" in checks
    for name in checks:
        result = note if name == "check_stammstrecke_directions" else healthy
        monkeypatch.setattr(f"{target}.{name}", _bound_stub(getattr(module, name), result))
    monkeypatch.setattr(f"{target}.assess_source", lambda probe, *_a: probe)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert health_check_main() == 0
    out = capsys.readouterr().out
    assert "::warning title=Health: Stammstrecke-Richtungen::HINWEIS" in out
    assert "::error" not in out
    assert "ℹ️" in out
    assert "Alles gesund" in out


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

    assert check_stammstrecke_directions(NOW).note is True

    monkeypatch.setenv("HEALTH_STAMMSTRECKE_WINDOW_HOURS", "12")
    check = check_stammstrecke_directions(NOW)
    assert check.ok is True
    assert check.note is False


def test_health_check_is_registered_in_the_report() -> None:
    """The check must actually run — an unwired check alarms on nothing."""
    # ``from``-style to match the other ``scripts.health_check`` imports in this
    # file. Mixing ``import x`` and ``from x import y`` for one module trips
    # CodeQL's py/import-and-import-from.
    from scripts.health_check import main as health_check_main

    source = inspect.getsource(health_check_main)
    assert "check_stammstrecke_directions(now)" in source


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
    health_check repeats it as a note.
    """
    import scripts.update_stammstrecke_hbf as hbf

    _write_ledger(stats_dir, _ticks(NOW, 8, "Meidling"))
    assert hbf._report_silent_directions(NOW) == ["Praterstern"]

    # An unreadable ledger must not crash the tick either.
    monkeypatch.setattr(
        hbf, "find_silent_directions", lambda **_kw: (_ for _ in ()).throw(OSError("boom"))
    )
    assert hbf._report_silent_directions(NOW) == []


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
        Path(__file__).resolve().parents[2] / "docs" / "assets" / "site.js"
    ).read_text(encoding="utf-8")
    code_only = re.sub(r"//.*", "", js)

    for word in ("Kabelbrand", "cable-fire", "Bauarbeiten", "Streckensperre"):
        assert word not in code_only, f"site.js still names a cause: {word}"

    match = re.search(r'const COVERAGE_ORIGIN = "([^"]+)";', js)
    assert match is not None, "site.js must declare COVERAGE_ORIGIN"
    assert match.group(1) == COVERAGE_ORIGIN_LABEL, (
        "the measurement point must read the same on both surfaces"
    )


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
