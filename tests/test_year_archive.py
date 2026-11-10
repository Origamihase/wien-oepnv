"""The archive page of a finished year and the year files behind the coverage note.

Review of 2026-11-10, before the first turn of a year: the real ledger was
replayed over New Year (the night of Thursday 05.11.2026 moved onto
31.12.2026, and the ledger moved up to 13 weeks ahead; report
``jahresarchiv-pruefung-2026-11-10.md`` in the project files). The tests
keep what that replay found.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_markdown_stats as stats_script  # noqa: E402
from src.markdown_stats import rows as rows_module  # noqa: E402
from src.markdown_stats.direction_coverage import (  # noqa: E402
    render_finished_year_coverage_note,
)
from src.markdown_stats.rows import StammstreckeRow  # noqa: E402
from src.utils.stats import (  # noqa: E402
    STAMMSTRECKE_DIRECTIONS,
    STAMMSTRECKE_HEADER,
    STOERUNGEN_HEADER,
    WEEKDAY_LABELS,
)

VIENNA = ZoneInfo("Europe/Vienna")
STAMP = "_Automatisch erzeugt am "


@pytest.fixture(autouse=True)
def _no_poll_record(monkeypatch: pytest.MonkeyPatch) -> None:
    # The runs below are set in other years; the poll record of the working
    # copy would be months old or months ahead there.
    monkeypatch.setattr(stats_script, "load_poll_status", lambda: None)


def _sm_line(when: datetime, direction: str) -> str:
    return (
        f"{when.isoformat(timespec='seconds')},{WEEKDAY_LABELS[when.weekday()]},"
        f"{when.hour:02d},{direction},1.00\n"
    )


def _write_sm(stats: Path, rows: list[tuple[datetime, str]]) -> None:
    stats.mkdir(exist_ok=True)
    by_year: dict[int, list[str]] = {}
    for when, direction in rows:
        by_year.setdefault(when.year, []).append(_sm_line(when, direction))
    for year, lines in by_year.items():
        (stats / f"stammstrecke_{year}.csv").write_text(
            ",".join(STAMMSTRECKE_HEADER) + "\n" + "".join(lines), encoding="utf-8"
        )


def _half_hourly(start: datetime, end: datetime, direction: str) -> list[tuple[datetime, str]]:
    rows = []
    when = start
    while when <= end:
        rows.append((when, direction))
        when += timedelta(minutes=30)
    return rows


def _sm_rows(rows: list[tuple[datetime, str]]) -> list[StammstreckeRow]:
    return [
        StammstreckeRow(
            timestamp=when,
            weekday=WEEKDAY_LABELS[when.weekday()],
            hour=when.hour,
            direction=direction,
            delay_minutes=1.0,
        )
        for when, direction in rows
    ]


def _run(tmp_path: Path, now_iso: str, *extra: str) -> int:
    readme = tmp_path / "README.md"
    if not readme.exists():
        readme.write_text(
            "".join(
                f"<!-- STATS:{name}:BEGIN -->\n<!-- STATS:{name}:END -->\n"
                for name in ("STAMMSTRECKE_LIVE", "STAMMSTRECKE", "AUSFAELLE_LIVE", "AUSFAELLE")
            ),
            encoding="utf-8",
        )
    return stats_script.main(
        [
            "--stats-dir",
            str(tmp_path / "stats"),
            "--output",
            str(tmp_path / "statistik.md"),
            "--summary-path",
            str(tmp_path / "stats-summary.json"),
            "--readme-path",
            str(readme),
            "--now-iso",
            now_iso,
            *extra,
        ]
    )


def _body(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(STAMP))


# ---- The pages name each other -------------------------------------------------


def test_dashboard_and_archive_name_each_other(tmp_path: Path) -> None:
    # The archive was a page no other page named: the dashboard of 01.01.
    # showed an almost empty year and no way to the one just finished.
    _write_sm(tmp_path / "stats", [(datetime(2026, 12, 31, 23, 30, tzinfo=VIENNA), "Meidling")])
    assert _run(tmp_path, "2026-12-31T00:01:00+01:00") == 0
    before = (tmp_path / "statistik.md").read_text(encoding="utf-8")
    assert "Frühere Jahre" not in before
    assert not (tmp_path / "statistik-2026.md").exists()

    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    dashboard = (tmp_path / "statistik.md").read_text(encoding="utf-8").splitlines()
    archive = (tmp_path / "statistik-2026.md").read_text(encoding="utf-8").splitlines()
    assert dashboard[0] == "# Wien ÖPNV — Statistik 2027"
    assert dashboard[4] == "Frühere Jahre: [2026](statistik-2026.md)"
    assert archive[0] == "# Wien ÖPNV — Statistik 2026"
    assert archive[4] == "Abgeschlossenes Jahr · [Zur laufenden Statistik](statistik.md)"


def test_dashboard_lists_every_archive_newest_first(tmp_path: Path) -> None:
    _write_sm(tmp_path / "stats", [(datetime(2028, 1, 1, 0, 0, tzinfo=VIENNA), "Meidling")])
    for year in (2026, 2027):
        (tmp_path / f"statistik-{year}.md").write_text("alt\n", encoding="utf-8")
    # Not archive pages: another name, and a year that is not over.
    (tmp_path / "statistik-entwurf.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "statistik-2028.md").write_text("x\n", encoding="utf-8")
    assert _run(tmp_path, "2028-01-02T00:01:00+01:00") == 0
    text = (tmp_path / "statistik.md").read_text(encoding="utf-8")
    assert "Frühere Jahre: [2027](statistik-2027.md) · [2026](statistik-2026.md)\n" in text


# ---- The archive page carries the coverage note of its year --------------------


def _year_2026_like_the_real_one() -> list[tuple[datetime, str]]:
    # Both directions from 16.05., Praterstern until 14.08. 13:57, Meidling
    # to the last minutes of the year.
    return _half_hourly(
        datetime(2026, 5, 16, 5, 0, tzinfo=VIENNA), datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern"
    ) + _half_hourly(
        datetime(2026, 5, 16, 5, 0, tzinfo=VIENNA), datetime(2026, 12, 31, 23, 30, tzinfo=VIENNA), "Meidling"
    )


def test_archive_says_how_far_a_silent_direction_reaches(tmp_path: Path) -> None:
    # The dashboard said so until 31.12.; the archive of the same year,
    # written a day later, showed Praterstern with half the rows and no word.
    _write_sm(tmp_path / "stats", _year_2026_like_the_real_one())
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    archive = (tmp_path / "statistik-2026.md").read_text(encoding="utf-8")
    assert (
        "> ⚠️ **Eingeschränkte Abdeckung:** Von Wien Hbf Richtung **Praterstern** sind auf der "
        "Stammstrecke Fahrten nur bis 14.08.2026 erfasst. Die Jahreszahlen unten enthalten "
        "Richtung **Praterstern** daher nur bis dahin.\n"
    ) in archive
    assert "Aktuell" not in archive


def test_finished_year_note_is_judged_at_the_end_of_the_year() -> None:
    whole = _half_hourly(
        datetime(2026, 12, 1, 5, 0, tzinfo=VIENNA), datetime(2026, 12, 31, 23, 30, tzinfo=VIENNA), "Meidling"
    )
    # Both directions to the last evening: nothing to say.
    both = whole + [(when, "Praterstern") for when, _ in whole]
    assert render_finished_year_coverage_note(_sm_rows(both), year=2026) == ""
    # One direction ends on the afternoon of 31.12.: the dates of the note
    # would name the last day of the year, so there is no note.
    afternoon = whole + [
        (when, "Praterstern") for when, _ in whole if when < datetime(2026, 12, 31, 15, 0, tzinfo=VIENNA)
    ]
    assert render_finished_year_coverage_note(_sm_rows(afternoon), year=2026) == ""
    # One direction ends a day earlier.
    day_early = whole + [
        (when, "Praterstern") for when, _ in whole if when < datetime(2026, 12, 30, 15, 0, tzinfo=VIENNA)
    ]
    assert "Fahrten nur bis 30.12.2026 erfasst" in render_finished_year_coverage_note(
        _sm_rows(day_early), year=2026
    )
    # The page reads the same whenever it is written: rows of the next year
    # are not its business, the function only gets the year's own.
    assert render_finished_year_coverage_note(_sm_rows(day_early), year=2026) == (
        render_finished_year_coverage_note(_sm_rows(day_early), year=2026)
    )


def test_finished_year_note_names_both_directions_with_their_dates() -> None:
    rows = _half_hourly(
        datetime(2026, 12, 1, 5, 0, tzinfo=VIENNA), datetime(2026, 12, 20, 22, 0, tzinfo=VIENNA), "Meidling"
    ) + _half_hourly(
        datetime(2026, 12, 1, 5, 0, tzinfo=VIENNA), datetime(2026, 12, 10, 22, 0, tzinfo=VIENNA), "Praterstern"
    )
    note = render_finished_year_coverage_note(_sm_rows(rows), year=2026)
    assert "Richtung **Praterstern** sind" in note and "nur bis 10.12.2026" in note
    # Meidling ran on alone, so only the whole corridor's silence names it;
    # that needs no peer, and here Praterstern is named by the peer rule.
    assert "Meidling" not in note
    corridor = [row for row in rows if row[1] == "Meidling"] + [
        (when, "Praterstern") for when, d in rows if d == "Meidling"
    ]
    note = render_finished_year_coverage_note(_sm_rows(corridor), year=2026)
    assert (
        "Richtung **Meidling** und **Praterstern** sind auf der Stammstrecke Fahrten nicht bis zum "
        "Jahresende erfasst (Meidling bis 20.12.2026, Praterstern bis 20.12.2026)."
    ) in note


# ---- The coverage note past 30.01. ----------------------------------------------


def _silent_since_august(until: datetime) -> list[tuple[datetime, str]]:
    return _half_hourly(
        datetime(2026, 8, 1, 5, 0, tzinfo=VIENNA), datetime(2026, 8, 14, 13, 57, tzinfo=VIENNA), "Praterstern"
    ) + _half_hourly(datetime(2026, 8, 1, 5, 0, tzinfo=VIENNA), until, "Meidling")


@pytest.mark.parametrize("now_iso", ["2027-01-29T12:00:00+01:00", "2027-01-31T12:00:00+01:00", "2027-06-15T12:00:00+02:00"])
def test_note_survives_the_year_leaving_the_window(tmp_path: Path, now_iso: str) -> None:
    # Until 30.01. the 30-day window touches 2026 and its file was read. From
    # 31.01. it was not: Praterstern counted as never measured, README and
    # website dropped the note, and Meidling alone stood for the corridor.
    now = datetime.fromisoformat(now_iso)
    _write_sm(tmp_path / "stats", _silent_since_august(now - timedelta(minutes=20)))
    assert _run(tmp_path, now_iso, "--skip-dashboard") == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert readme.count(
        "Aktuell keine Fahrten von Wien Hbf Richtung **Praterstern** auf der Stammstrecke "
        "(zuletzt am 14.08.2026). Die Zahlen unten decken daher nur Richtung **Meidling** ab"
    ) == 2
    coverage = json.loads((tmp_path / "stats-summary.json").read_text(encoding="utf-8"))["stammstrecke"]["coverage"]
    praterstern = coverage["directions"]["Praterstern"]
    assert praterstern["last_seen"] == "2026-08-14T13:30:00+02:00"
    assert praterstern["rows_in_window"] == 0
    assert praterstern["peer_rows_since"] >= coverage["peer_rows_required"]


def test_earlier_years_are_read_only_for_a_missing_direction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stats = tmp_path / "stats"
    _write_sm(
        stats,
        [
            (datetime(2025, 3, 1, 8, 0, tzinfo=VIENNA), "Praterstern"),
            (datetime(2026, 3, 1, 8, 0, tzinfo=VIENNA), "Meidling"),
            (datetime(2027, 3, 1, 8, 0, tzinfo=VIENNA), "Meidling"),
            (datetime(2027, 3, 1, 9, 0, tzinfo=VIENNA), "Praterstern"),
        ],
    )
    read: list[str] = []
    real = rows_module._iter_csv_rows

    def spy(path: Path, header: tuple[str, ...]):  # type: ignore[no-untyped-def]
        read.append(path.name)
        return real(path, header)

    monkeypatch.setattr(rows_module, "_iter_csv_rows", spy)
    both = _sm_rows(
        [(datetime(2027, 3, 1, 8, 0, tzinfo=VIENNA), "Meidling"), (datetime(2027, 3, 1, 9, 0, tzinfo=VIENNA), "Praterstern")]
    )
    earlier = rows_module.earlier_rows_until_every_direction(
        both, before_year=2027, directions=STAMMSTRECKE_DIRECTIONS, stats_dir=stats
    )
    assert earlier == [] and read == []
    # One direction missing: back through 2026 (not there) to 2025, no further.
    earlier = rows_module.earlier_rows_until_every_direction(
        both[:1], before_year=2027, directions=STAMMSTRECKE_DIRECTIONS, stats_dir=stats
    )
    assert read == ["stammstrecke_2026.csv", "stammstrecke_2025.csv"]
    assert [(row.timestamp.year, row.direction) for row in earlier] == [(2026, "Meidling"), (2025, "Praterstern")]
    # A direction that never reported ends the search at the first year without a file.
    read.clear()
    (stats / "stammstrecke_2025.csv").unlink()
    assert len(
        rows_module.earlier_rows_until_every_direction(
            both[:1], before_year=2027, directions=STAMMSTRECKE_DIRECTIONS, stats_dir=stats
        )
    ) == 1
    assert read == ["stammstrecke_2026.csv"]


def test_a_year_without_stammstrecke_rows_still_archives(tmp_path: Path) -> None:
    stats = tmp_path / "stats"
    stats.mkdir()
    (stats / "stoerungen_2026.csv").write_text(
        ",".join(STOERUNGEN_HEADER) + "\n2026-12-31T23:10:00+01:00,Do,23,WL,Karlsplatz\n", encoding="utf-8"
    )
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    archive = (tmp_path / "statistik-2026.md").read_text(encoding="utf-8")
    assert "| Erfasste Störungen (2026) | 1 |" in archive
    assert "Eingeschränkte Abdeckung" not in archive


# ---- The archive page follows its ledger ------------------------------------------


def test_archive_takes_in_rows_that_arrive_after_new_year(tmp_path: Path) -> None:
    # The 00:01 tick of 01.01. could not poll; the trains of the last half
    # hour of 2026 are entered by the next tick, after the page was written.
    rows = _half_hourly(
        datetime(2026, 12, 31, 5, 0, tzinfo=VIENNA), datetime(2026, 12, 31, 23, 0, tzinfo=VIENNA), "Meidling"
    )
    _write_sm(tmp_path / "stats", rows)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    first = (tmp_path / "statistik-2026.md").read_text(encoding="utf-8")
    assert "| Stammstrecke-Beobachtungen (2026) | 37 |" in first

    _write_sm(tmp_path / "stats", rows + [(datetime(2026, 12, 31, 23, 30, tzinfo=VIENNA), "Meidling")])
    # A README tick renders no page.
    assert _run(tmp_path, "2027-01-01T00:31:00+01:00", "--skip-dashboard") == 0
    assert (tmp_path / "statistik-2026.md").read_text(encoding="utf-8") == first
    assert _run(tmp_path, "2027-01-02T00:01:00+01:00") == 0
    second = (tmp_path / "statistik-2026.md").read_text(encoding="utf-8")
    assert "| Stammstrecke-Beobachtungen (2026) | 38 |" in second
    assert "erzeugt am 2027-01-02T00:01+01:00" in second

    # Unchanged ledger: the page is not written again, on no later day.
    stamp = (tmp_path / "statistik-2026.md").stat().st_mtime_ns
    for now_iso in ("2027-01-03T00:01:00+01:00", "2027-07-01T00:01:00+02:00"):
        assert _run(tmp_path, now_iso) == 0
        assert (tmp_path / "statistik-2026.md").read_text(encoding="utf-8") == second
        assert (tmp_path / "statistik-2026.md").stat().st_mtime_ns == stamp


def test_every_finished_year_with_a_ledger_has_its_page(tmp_path: Path) -> None:
    _write_sm(
        tmp_path / "stats",
        [
            (datetime(2026, 6, 1, 8, 0, tzinfo=VIENNA), "Meidling"),
            (datetime(2027, 6, 1, 8, 0, tzinfo=VIENNA), "Meidling"),
            (datetime(2028, 1, 1, 0, 0, tzinfo=VIENNA), "Meidling"),
        ],
    )
    # Header only: a year without rows gets no page.
    (tmp_path / "stats" / "ausfaelle_2025.csv").write_text("timestamp,weekday,hour,direction,line\n", encoding="utf-8")
    assert _run(tmp_path, "2028-01-01T00:01:00+01:00") == 0
    assert sorted(p.name for p in tmp_path.glob("statistik-*.md")) == ["statistik-2026.md", "statistik-2027.md"]
    assert "Frühere Jahre: [2027](statistik-2027.md) · [2026](statistik-2026.md)" in (
        tmp_path / "statistik.md"
    ).read_text(encoding="utf-8")
    # An explicit other year writes and changes none.
    (tmp_path / "statistik-2026.md").unlink()
    assert _run(tmp_path, "2028-01-02T00:01:00+01:00", "--year", "2027") == 0
    assert not (tmp_path / "statistik-2026.md").exists()


def test_archive_write_only_counts_what_the_page_says(tmp_path: Path) -> None:
    _write_sm(tmp_path / "stats", [(datetime(2026, 6, 1, 8, 0, tzinfo=VIENNA), "Meidling")])
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    page = tmp_path / "statistik-2026.md"
    first = page.read_text(encoding="utf-8")
    # A blank line at the end of the ledger is the same year.
    ledger = tmp_path / "stats" / "stammstrecke_2026.csv"
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert _run(tmp_path, "2027-01-02T00:01:00+01:00") == 0
    assert page.read_text(encoding="utf-8") == first
    # A page edited by hand is brought back to its ledger.
    page.write_text(first.replace("| 1 |", "| 99 |", 1), encoding="utf-8")
    assert _run(tmp_path, "2027-01-03T00:01:00+01:00") == 0
    assert _body(page.read_text(encoding="utf-8")) == _body(first)
