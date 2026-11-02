"""Teil 2 der Tests aus tests/scripts/test_generate_markdown_stats.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


from scripts import generate_markdown_stats as script  # noqa: E402

from ._support import (
    VIENNA_TZ,
)


def test_main_returns_zero_with_no_input_files(tmp_path: Path) -> None:
    output = tmp_path / "statistik.md"
    rc = script.main(
        [
            "--year", "2026",
            "--stats-dir", str(tmp_path / "missing"),
            "--output", str(output),
            "--skip-readme",
        ]
    )
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "Keine Daten verfügbar" in body


def test_render_markdown_names_vao_start_as_basis() -> None:
    """VAO "Start" contract, point 5.3 (Michael, 17.10.2026; NOTICE.md).

    The statistics page states under the Stammstrecke heading that the VAO
    "Start" access is the basis of its data, also when there is no data.
    """
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        stammstrecke=script.StammstreckeAggregate(),
        stoerungen=script.StoerungAggregate(),
        ausfaelle=script.AusfallAggregate(),
    )
    assert (
        "## Stammstrecke\n\n> Stammstrecken-Daten: Grundlage ist der "
        "VAO-„Start“-Zugang der Verkehrsauskunft Österreich VAO GmbH.\n" in md
    )
    # The Ausfälle come from the same VAO queries; the disruption statistics
    # name each of their four sources (Michael, 17.10.2026).
    assert "## Ausfälle\n\n> Stammstrecken-Daten: Grundlage ist der VAO" in md
    assert (
        "## Störungen\n\n> Quellen: die Meldungen des Feeds, jede unter ihrer "
        "eigenen Quelle – Wiener Linien, ÖBB, Stadt Wien (Baustellen) und "
        "VOR/VAO (Stammstrecke).\n" in md
    )


def test_render_readme_stammstrecke_live_block_empty_window_wording() -> None:
    """An empty live window reads "Keine Fahrten" only behind a working fetch.

    Operator decision 2026-10-30: on 30.10. every train of the hour was
    cancelled and "N/A" read like a broken fetch. Without a fetch inside
    the window nothing was measured, so "N/A" stays.
    """
    now = datetime(2026, 10, 30, 16, 1, tzinfo=VIENNA_TZ)
    ran_none = script.render_readme_stammstrecke_live_block(
        [], now=now, fetch_covers_window=True
    )
    unmeasured = script.render_readme_stammstrecke_live_block([], now=now)
    assert "| Durchschnittliche Verspätung | Keine Fahrten |" in ran_none
    assert "| Durchschnittliche Verspätung | N/A |" in unmeasured


def test_live_fetch_covers_is_bound_to_the_live_window() -> None:
    """The fetch must have worked inside the 60-minute window, not just in 3 h."""
    now = datetime(2026, 10, 30, 16, 1, tzinfo=VIENNA_TZ)

    from src.utils.stammstrecke_poll import PollStatus

    def status(minutes_ago: int | None) -> PollStatus:
        last = None if minutes_ago is None else now - timedelta(minutes=minutes_ago)
        return PollStatus(
            last_attempt=now,
            last_result="ok" if last else "error",
            last_error="",
            last_success=last,
            last_success_departures=39,
            last_success_trains=2,
            last_train_seen=last,
        )

    assert script._live_fetch_covers(status(1), now=now)
    assert script._live_fetch_covers(status(60), now=now)
    assert not script._live_fetch_covers(status(61), now=now)
    assert not script._live_fetch_covers(status(None), now=now)
    assert not script._live_fetch_covers(None, now=now)
