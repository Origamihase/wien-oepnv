"""Teil 2 der Tests aus tests/test_stammstrecke_direction_health.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta


from ._support import (
    NOW,
    VIENNA,
    _peer_run,
    _sm_row,
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
        Path(__file__).resolve().parents[2] / "docs" / "assets" / "site.js"
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
        Path(__file__).resolve().parents[2] / "docs" / "assets" / "site.js"
    ).read_text(encoding="utf-8")
    match = re.search(r"const STAMMSTRECKE_DIRECTIONS = \[([^\]]*)\]", js)
    assert match, "site.js no longer declares STAMMSTRECKE_DIRECTIONS"
    js_dirs = tuple(re.findall(r'"([^"]+)"', match.group(1)))
    assert js_dirs == tuple(STAMMSTRECKE_DIRECTIONS)


def test_yearly_note_does_not_contradict_the_yearly_table() -> None:
    """``docs/statistik.md`` shows the year, not the README's 30-day window.

    On 25.10.2026 the dashboard said the figures below covered Meidling only,
    right above a direction table with Praterstern 3,831: those rows are from
    before 14.08.2026 and still part of the year. Judged against the year's
    rows, the note says how far they reach instead.
    """
    from scripts.generate_markdown_stats import render_direction_coverage_note

    southbound = _peer_run(NOW, "Meidling", 12, step_minutes=5)
    northbound = _sm_row(datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern")
    year = [northbound, *southbound]

    note = render_direction_coverage_note(year, now=NOW, all_rows=year, yearly=True)

    assert "(zuletzt am 14.08.2026)" in note
    assert "Die Jahreszahlen unten enthalten Richtung **Praterstern** daher nur bis dahin." in note
    assert "Korridor-Gesamtwert" not in note

    # A direction absent from the whole year still gets the part-corridor caveat.
    note = render_direction_coverage_note(
        southbound, now=NOW, all_rows=year, yearly=True
    )
    assert "kein Korridor-Gesamtwert" in note


def test_site_judges_the_caveat_against_the_yearly_figures() -> None:
    """The page's figures are the year's too; its banner reads them, not the window."""
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2] / "docs" / "assets" / "site.js"
    ).read_text(encoding="utf-8")
    assert "renderCoverageNotice(stats.coverage, byDirection)" in js
    assert "const rowsIn = (dir) => bucket(yearByDirection, dir);" in js
    assert '"coverage-body-yearly"' in js
