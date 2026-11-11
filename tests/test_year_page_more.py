"""The wiring of the year page after the review of 2026-11-10.

``test_year_page.py`` drives a year change through the statistics script;
this file keeps what the review of 2026-11-10 found in the wiring: the
order of the run (no write waits for Gemini, a failed page costs the exit
code and nothing else), one time budget for the requests of all years, the
exit code that only the Update cycle asks for, the lines at the foot of the
page, the rows of another year, and the statistics step of
``update-cycle.yml``.
"""
from __future__ import annotations

# ruff: noqa: S603, S607
import json
import os
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.markdown_stats import review_client, year_archive, year_review
from src.markdown_stats.review_client import ReviewAnswer
from src.utils.stats import (
    AUSFAELLE_HEADER,
    STAMMSTRECKE_HEADER,
    STOERUNGEN_HEADER,
    VIENNA_TZ,
    WEEKDAY_LABELS,
)
from src.utils.stats_baustellen import append_baustellen_rows
from scripts import generate_markdown_stats as stats_script
from tests.test_year_page import (
    EVENTS,
    GOOD,
    PENDING,
    _answer,
    _page,
    _quiet_surroundings,  # noqa: F401 - autouse: no poll record, no key from the shell
    _run,
    _seed,
)

FLAG = "--fail-on-missing-review"
REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
RULE_LEDGERS = "`data/stats/stammstrecke_*.csv`, `data/stats/ausfaelle_*.csv`, `data/stats/stoerungen_*.csv`"


def _footer(lines: list[str]) -> list[str]:
    """The last rule of a page and the two lines before and after it."""
    rule = len(lines) - 1 - lines[::-1].index("---")
    return lines[rule - 2 : rule + 3]


# ---- The foot of the page ---------------------------------------------------------


def test_the_rule_before_the_sources_is_a_rule_on_github_pages(tmp_path: Path) -> None:
    # kramdown read "> Datenquelle …" directly followed by "---" as a
    # setext heading: the attribution became an <h2>, the rule was gone.
    _seed(tmp_path, baustellen=True)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    before, empty, rule, _, sources = _footer(_page(tmp_path))
    assert before.startswith("> Datenquelle: Stadt Wien") and (empty, rule) == ("", "---")
    assert sources == (
        f"_Quellen_: {RULE_LEDGERS}, `data/stats/baustellen_*.csv`. "
        "Generiert von `scripts/generate_markdown_stats.py`."
    )


def test_the_sources_name_the_review_only_when_the_page_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, baustellen=True)
    _answer(monkeypatch, GOOD)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    assert _page(tmp_path)[-1] == (
        f"_Quellen_: {RULE_LEDGERS}, `data/stats/baustellen_*.csv`, "
        "`data/stats/baustellen_rueckblick_*.json`. Generiert von `scripts/generate_markdown_stats.py`."
    )


def test_a_page_without_roadworks_and_the_dashboard_keep_their_foot(tmp_path: Path) -> None:
    _seed(tmp_path, baustellen=False)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    expected = f"_Quellen_: {RULE_LEDGERS}. Generiert von `scripts/generate_markdown_stats.py`."
    for name in ("statistik-2026.md", "statistik.md"):
        lines = (tmp_path / name).read_text(encoding="utf-8").splitlines()
        assert lines[-1] == expected
        assert lines[-4:-2] == ["", "---"]
        # One empty line before the rule, as before: no second one.
        assert lines[-5] != ""


# ---- Rows of another year ---------------------------------------------------------


def _sm_line(when: datetime, delay: str = "1.00") -> str:
    return f"{when.isoformat(timespec='seconds')},{WEEKDAY_LABELS[when.weekday()]},{when.hour:02d},Meidling,{delay}\n"


def _ledgers(stats: Path, *, sm: Sequence[str], st: Sequence[str] = (), au: Sequence[str] = ()) -> None:
    stats.mkdir(exist_ok=True)
    for name, header, lines in (
        ("stammstrecke_2026.csv", STAMMSTRECKE_HEADER, sm),
        ("stoerungen_2026.csv", STOERUNGEN_HEADER, st),
        ("ausfaelle_2026.csv", AUSFAELLE_HEADER, au),
    ):
        (stats / name).write_text(",".join(header) + "\n" + "".join(lines), encoding="utf-8")


def test_a_row_of_another_year_counts_nowhere_on_the_page(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Hand-added rows: the key figures counted them under 2026, the
    # overview of the same page did not.
    june = datetime(2026, 6, 1, 8, 0, tzinfo=VIENNA_TZ)
    _ledgers(
        tmp_path / "stats",
        sm=[_sm_line(june), _sm_line(datetime(2025, 6, 1, 8, 0, tzinfo=VIENNA_TZ), "80.00")],
        st=["2027-01-01T00:10:00+01:00,Fr,00,WL,Karlsplatz\n"],
        au=["2025-12-31T23:30:00+01:00,Mi,23,Meidling,S 1\n"],
    )
    with caplog.at_level("WARNING"):
        assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    page = _page(tmp_path)
    assert "| Stammstrecke-Beobachtungen (2026) | 1 |" in page
    assert "| ⌀ Verspätung (2026) | 1.0 min |" in page
    assert "| Stammstrecke-Ausfälle (2026) | 0 |" in page
    assert "| Erfasste Störungen (2026) | 0 |" in page
    assert any(line.startswith("| Juni | 1 | 1.0 min |") for line in page)
    assert "Jahresarchiv 2026: 3 Zeilen mit dem Zeitstempel eines anderen Jahres nicht gezählt." in caplog.text


def _warnings(caplog: pytest.LogCaptureFixture, *, level: str = "WARNING") -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelname == level]


def test_each_finding_stands_once_per_run_and_again_in_the_next(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A dashboard run renders the page twice while its review is missing:
    # "1 Zeilen …" stood twice in one log, and so did the WARNING of a
    # stored review that no longer passes the guard.
    _seed(tmp_path, baustellen=True)
    with (tmp_path / "stats" / "stammstrecke_2026.csv").open("a", encoding="utf-8") as ledger:
        ledger.write(_sm_line(datetime(2025, 12, 31, 23, 30, tzinfo=VIENNA_TZ), "5.00"))
    review = tmp_path / "stats" / "baustellen_rueckblick_2026.json"
    review.write_text(json.dumps({"year": 2026, "text": "Zwei  Leerzeichen."}), encoding="utf-8")
    runs: list[list[str]] = []
    for now_iso in ("2027-01-01T00:01:00+01:00", "2027-01-02T00:01:00+01:00"):
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert _run(tmp_path, now_iso) == 0
        runs.append(_warnings(caplog))
    first, second = runs
    assert first.count("Jahresarchiv 2026: 1 Zeile mit dem Zeitstempel eines anderen Jahres nicht gezählt.") == 1
    assert any(line.startswith("Baustellen-Rückblick 2026: der gespeicherte Text") for line in first)
    assert len(set(first)) == len(first)
    assert second == first


def test_a_ledger_with_only_rows_of_another_year_gives_no_page(tmp_path: Path) -> None:
    _ledgers(tmp_path / "stats", sm=[_sm_line(datetime(2025, 6, 1, 8, 0, tzinfo=VIENNA_TZ))])
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    assert not (tmp_path / "statistik-2026.md").exists()


# ---- A year with only roadworks ----------------------------------------------------


def test_a_year_with_only_roadworks_is_never_overdue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # It gets no page, so no run asks for its review: it failed the step
    # on every tick, for good.
    stats = tmp_path / "stats"
    stats.mkdir()
    append_baustellen_rows(EVENTS, now=datetime(2026, 10, 1, 12, 0, tzinfo=VIENNA_TZ), stats_dir=stats)
    prompts = _answer(monkeypatch, GOOD)
    now = datetime(2027, 1, 4, 0, 1, tzinfo=VIENNA_TZ)
    assert year_archive.years_without_review(now=now, stats_dir=stats) == []
    assert year_archive.years_missing_review(before=2027, stats_dir=stats) == []
    assert _run(tmp_path, "2027-01-04T00:01:00+01:00", FLAG) == 0
    assert _run(tmp_path, "2027-01-04T00:31:00+01:00", "--skip-dashboard", FLAG) == 0
    assert prompts == [] and not (tmp_path / "statistik-2026.md").exists()


# ---- The order of a dashboard run --------------------------------------------------


def test_the_request_comes_after_every_write_of_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wait for Gemini, or the step's time limit during it, costs nothing but the review."""
    _seed(tmp_path, baustellen=True)
    seen: list[tuple[object, ...]] = []

    def ask(prompt: str, *, key: str, deadline_s: float | None = None) -> ReviewAnswer | None:
        summary = json.loads((tmp_path / "stats-summary.json").read_text(encoding="utf-8"))
        dashboard = (tmp_path / "statistik.md").read_text(encoding="utf-8")
        seen.append((summary["archive_years"], "2027-01-01T00:01+01:00" in dashboard, _outputs(tmp_path)))
        assert PENDING in _page(tmp_path)
        return ReviewAnswer(GOOD, "gemini-test")

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(year_review, "ask_review", ask)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    assert seen == [([2026], True, (True, True, True))]
    # The second pass wrote the page again, now with the review.
    assert PENDING not in _page(tmp_path) and "### Rückblick" in _page(tmp_path)


def test_no_second_pass_while_every_review_is_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, baustellen=True)
    _answer(monkeypatch, GOOD)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    passes: list[bool] = []
    real = year_archive.write_year_archives

    def spy(**kwargs: Any) -> list[Path]:
        passes.append(bool(kwargs.get("ask")))
        written: list[Path] = real(**kwargs)
        return written

    monkeypatch.setattr(year_archive, "write_year_archives", spy)
    assert _run(tmp_path, "2027-01-02T00:01:00+01:00", FLAG) == 0
    assert passes == [False]
    # A README tick renders no page and asks nothing.
    passes.clear()
    assert _run(tmp_path, "2027-01-02T00:31:00+01:00", "--skip-dashboard", FLAG) == 0
    assert passes == []


def _outputs(tmp_path: Path) -> tuple[bool, bool, bool]:
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    return (
        (tmp_path / "statistik.md").is_file(),
        (tmp_path / "stats-summary.json").is_file(),
        "STATS:STAMMSTRECKE_LIVE:BEGIN -->\n<!-- STATS:STAMMSTRECKE_LIVE:END" not in readme,
    )


def test_a_page_that_cannot_be_written_costs_only_the_exit_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # It ended the run before the summary, the dashboard and the README.
    _seed(tmp_path, baustellen=False)
    (tmp_path / "statistik-2026.md").mkdir()
    with caplog.at_level("ERROR"):
        assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 1
    assert "Konnte Jahresarchiv nicht schreiben (IsADirectoryError)" in caplog.text
    assert _outputs(tmp_path) == (True, True, True)


def test_a_page_that_fails_in_both_passes_is_one_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # While its review is missing the page is written twice per dashboard
    # run, and its ERROR stood twice in the log.
    _seed(tmp_path, baustellen=True)
    (tmp_path / "statistik-2026.md").mkdir()
    passes: list[bool] = []
    real = year_archive.write_year_archives

    def spy(**kwargs: Any) -> list[Path]:
        passes.append(bool(kwargs.get("ask")))
        written: list[Path] = real(**kwargs)
        return written

    monkeypatch.setattr(year_archive, "write_year_archives", spy)
    for _ in range(2):
        caplog.clear()
        with caplog.at_level("ERROR"):
            assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 1
        (line,) = _warnings(caplog, level="ERROR")
        assert line.startswith("Konnte Jahresarchiv nicht schreiben (IsADirectoryError)")
    assert passes == [False, True, False, True]


@pytest.mark.parametrize("name", ["write_stats_summary", "write_dashboard", "patch_readme_stats"])
def test_a_failed_write_says_that_the_run_ends_without_a_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    # The early return stays (the failed write is the bigger alarm); until
    # 2026-11-11 the log did not say that it skips the request.
    _seed(tmp_path, baustellen=True)
    prompts = _answer(monkeypatch, GOOD)
    real = getattr(stats_script, name)
    blocked = [True]

    def write(*args: Any, **kwargs: Any) -> Any:
        if blocked[0]:
            raise OSError("blockiert")
        return real(*args, **kwargs)

    def error_line(now_iso: str, *extra: str) -> str:
        caplog.clear()
        with caplog.at_level("ERROR"):
            assert _run(tmp_path, now_iso, *extra) == 1
        (line,) = _warnings(caplog, level="ERROR")
        return line

    monkeypatch.setattr(stats_script, name, write)
    assert error_line("2027-01-01T00:01:00+01:00").endswith(
        ": blockiert — der Lauf endet hier, ohne Anfrage für den fehlenden Rückblick der Baustellen 2026; "
        "ein späterer Dashboard-Lauf fragt erneut."
    )
    assert prompts == []
    # A README tick sends no request anyway, and a stored review needs none.
    if name != "write_dashboard":
        assert error_line("2027-01-01T00:31:00+01:00", "--skip-dashboard").endswith(": blockiert")
    blocked[0] = False
    assert _run(tmp_path, "2027-01-01T00:05:00+01:00") == 0 and len(prompts) == 1
    blocked[0] = True
    assert error_line("2027-01-02T00:01:00+01:00").endswith(": blockiert")


def test_a_defect_in_the_page_code_costs_only_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Only an OSError was caught; anything else was a traceback before the
    # other outputs of the run.
    _seed(tmp_path, baustellen=True)

    def broken(*args: object, **kwargs: object) -> list[str]:
        raise ValueError("kaputt")

    monkeypatch.setattr(year_archive, "render_year_overview", broken)
    with caplog.at_level("ERROR"):
        assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 1
    assert "Konnte Jahresarchiv nicht schreiben (ValueError): kaputt" in caplog.text
    assert _outputs(tmp_path) == (True, True, True)
    assert not (tmp_path / "statistik-2026.md").exists()
    # The README ticks of the day do not render the page and end with 0.
    assert _run(tmp_path, "2027-01-01T00:31:00+01:00", "--skip-dashboard") == 0


def test_a_failure_while_asking_keeps_the_page_of_the_first_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _seed(tmp_path, baustellen=True)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def broken(*args: object, **kwargs: object) -> year_review.ReviewOutcome:
        raise RuntimeError("weg")

    monkeypatch.setattr(year_archive, "ensure_review", broken)
    with caplog.at_level("ERROR"):
        assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 1
    assert "Konnte Jahresarchiv nicht schreiben (RuntimeError): weg" in caplog.text
    assert PENDING in _page(tmp_path) and _outputs(tmp_path) == (True, True, True)


# ---- One time budget for the requests of all years ---------------------------------


def _second_year(stats: Path) -> None:
    """Rows and roadworks of 2025 beside those of 2026 (``_seed``)."""
    when = datetime(2025, 12, 30, 6, 0, tzinfo=VIENNA_TZ)
    (stats / "stammstrecke_2025.csv").write_text(
        ",".join(STAMMSTRECKE_HEADER) + "\n" + _sm_line(when), encoding="utf-8"
    )
    shifted = [
        {key: value.replace("2026-", "2025-") if isinstance(value, str) else value for key, value in event.items()}
        for event in EVENTS
    ]
    assert append_baustellen_rows(shifted, now=datetime(2025, 10, 1, 12, 0, tzinfo=VIENNA_TZ), stats_dir=stats) == 4


def test_a_failed_write_names_every_missing_review_in_the_plural(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # With 2025 and 2026 waiting it read "für den fehlenden Rückblick der
    # Baustellen 2025, 2026" (review of 2026-11-11).
    _seed(tmp_path, baustellen=True)
    _second_year(tmp_path / "stats")

    def full(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("[Errno 28] No space left on device")

    monkeypatch.setattr(stats_script, "patch_readme_stats", full)
    with caplog.at_level("ERROR"):
        assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 1
    (line,) = _warnings(caplog, level="ERROR")
    assert line.endswith(
        ": [Errno 28] No space left on device — der Lauf endet hier, ohne Anfrage für die fehlenden "
        "Rückblicke der Baustellen 2025, 2026; ein späterer Dashboard-Lauf fragt erneut."
    )


@pytest.mark.parametrize(("first_takes", "second_gets"), [(50.0, 60.0), (100.0, None)])
def test_all_years_share_one_budget_newest_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    first_takes: float,
    second_gets: float | None,
) -> None:
    # Each year got the client's whole deadline: two years reached the
    # step's four minutes.
    _seed(tmp_path, baustellen=True)
    _second_year(tmp_path / "stats")
    clock = [1000.0]
    deadlines: list[float | None] = []

    def ask(prompt: str, *, key: str, deadline_s: float | None = None) -> ReviewAnswer | None:
        deadlines.append(deadline_s)
        clock[0] += first_takes
        return None

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(year_review, "ask_review", ask)
    with caplog.at_level("WARNING"):
        year_archive.write_year_archives(
            now=datetime(2027, 1, 1, 0, 1, tzinfo=VIENNA_TZ),
            year=2027,
            stats_dir=tmp_path / "stats",
            output_path=tmp_path / "statistik.md",
            ask=True,
            clock=lambda: clock[0],
        )
    # 2026 first (test_the_newest_year_is_asked_first), with the whole budget.
    assert deadlines[0] == review_client.DEFAULT_DEADLINE_S
    if second_gets is None:
        assert len(deadlines) == 1
        # It promised "der nächste Dashboard-Lauf fragt" while the newer
        # year can use up the budget on every run.
        assert (
            "Baustellen-Rückblick 2025: in diesem Lauf keine Anfrage mehr, die Zeit für Anfragen ist "
            "aufgebraucht; ein späterer Dashboard-Lauf fragt erneut."
        ) in caplog.text
    else:
        assert deadlines[1:] == [second_gets]
    assert sorted(p.name for p in tmp_path.glob("statistik-*.md")) == ["statistik-2025.md", "statistik-2026.md"]


def test_the_newest_year_is_asked_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, baustellen=True)
    _second_year(tmp_path / "stats")
    years: list[int] = []
    real = year_review.ensure_review

    def spy(year: int, *args: Any, **kwargs: Any) -> year_review.ReviewOutcome:
        years.append(year)
        assert kwargs["deadline_s"] <= review_client.DEFAULT_DEADLINE_S
        outcome: year_review.ReviewOutcome = real(year, *args, **kwargs)
        return outcome

    monkeypatch.setattr(year_archive, "ensure_review", spy)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    assert years == [2026, 2025]
    assert year_archive.years_missing_review(before=2027, stats_dir=tmp_path / "stats") == [2025, 2026]


# ---- The exit code ----------------------------------------------------------------


@pytest.mark.parametrize("key", ["", "k"])
def test_the_error_line_names_no_cause_it_does_not_know(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, key: str
) -> None:
    # It said "Gemini hat bisher keinen Text geliefert" also when nothing
    # was sent because the key was empty.
    _seed(tmp_path, baustellen=True)
    monkeypatch.setenv("GEMINI_API_KEY", key)
    with caplog.at_level("ERROR"):
        assert _run(tmp_path, "2027-01-04T00:31:00+01:00", "--skip-dashboard", FLAG) == 1
    (line,) = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert line.startswith(
        "Rückblick der Baustellen fehlt für 2026: kein Text gespeichert, der die Prüfung besteht "
        "(Grund im Protokoll des Laufs kurz nach Mitternacht)."
    )
    assert line.endswith(" GEMINI_API_KEY ist in diesem Lauf leer.") is (key == "")
    assert "Gemini hat" not in line


@pytest.mark.parametrize(
    ("now_iso", "extra", "readme_changes"),
    [
        ("2027-01-04T00:31:00+01:00", ("--skip-dashboard",), True),
        ("2027-03-10T00:31:00+01:00", ("--skip-dashboard",), False),
        ("2027-03-10T00:01:00+01:00", (), False),
        ("2027-01-04T00:31:00+01:00", ("--skip-dashboard", "--skip-readme"), False),
    ],
    ids=["readme-patched", "window-empty", "dashboard-window-empty", "skip-readme"],
)
def test_every_end_of_a_run_reports_the_overdue_review(
    tmp_path: Path, now_iso: str, extra: tuple[str, ...], readme_changes: bool
) -> None:
    _seed(tmp_path, baustellen=True)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert _run(tmp_path, now_iso, *extra, FLAG) == 1
    assert ((tmp_path / "README.md").read_text(encoding="utf-8") != readme) is readme_changes
    assert _run(tmp_path, now_iso, *extra) == 0


# ---- Folders that cannot be listed -------------------------------------------------


def test_folders_that_cannot_be_listed_have_no_years(tmp_path: Path) -> None:
    missing = tmp_path / "fehlt"
    assert year_archive.finished_years(missing, before=2030) == []
    assert year_archive.archived_years(missing / "statistik.md", before=2030) == []
    assert year_archive.dashboard_nav(missing / "statistik.md", year=2030) == ""


def test_without_a_budget_only_the_stored_review_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, baustellen=True)
    prompts = _answer(monkeypatch, GOOD)
    kwargs: dict[str, Any] = {
        "now": datetime(2027, 1, 1, 0, 1, tzinfo=VIENNA_TZ),
        "year": 2026,
        "stats_dir": tmp_path / "stats",
        "output_path": tmp_path / "statistik.md",
    }
    page = year_archive.render_year_archive(**kwargs)
    assert page is not None and PENDING in page and prompts == []
    page = year_archive.render_year_archive(**kwargs, budget=year_archive.ReviewBudget())
    assert page is not None and "### Rückblick" in page and len(prompts) == 1
    page = year_archive.render_year_archive(**kwargs)
    assert page is not None and "### Rückblick" in page and len(prompts) == 1


def test_the_budget_starts_at_its_first_use() -> None:
    ticks = iter([5.0, 30.0, 200.0])
    budget = year_archive.ReviewBudget(lambda: next(ticks))
    assert budget.left() == review_client.DEFAULT_DEADLINE_S
    assert budget.left() == review_client.DEFAULT_DEADLINE_S - 25.0
    assert budget.left() < review_client.MIN_REQUEST_S


# ---- The statistics step of update-cycle.yml ---------------------------------------


def _workflow(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return doc


def _stats_step() -> dict[str, Any]:
    (step,) = [s for s in _workflow("update-cycle.yml")["jobs"]["cycle"]["steps"] if s.get("id") == "stats"]
    assert isinstance(step, dict)
    return step


def test_the_statistics_step_keeps_the_feed_publish_going() -> None:
    # From 04.01. the step ends with 1 on every tick while a review is
    # overdue; only continue-on-error keeps the publish step after it.
    assert _stats_step()["continue-on-error"] is True


def test_the_requests_end_inside_the_step_limit() -> None:
    step = _stats_step()
    worst = review_client.DEFAULT_DEADLINE_S + review_client.CONNECT_TIMEOUT_S
    assert worst + 60 <= step["timeout-minutes"] * 60
    assert step["timeout-minutes"] < _workflow("update-cycle.yml")["jobs"]["cycle"]["timeout-minutes"]


def test_only_the_statistics_step_of_the_cycle_has_the_key() -> None:
    assert _stats_step()["env"] == {"GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}"}
    assert (WORKFLOWS / "update-cycle.yml").read_text(encoding="utf-8").count("secrets.GEMINI_API_KEY") == 1


@pytest.mark.parametrize(
    ("hhmm", "arguments"),
    [
        ("0000", FLAG), ("0029", FLAG),
        ("0030", f"--skip-dashboard {FLAG}"), ("0100", f"--skip-dashboard {FLAG}"),
        ("1200", f"--skip-dashboard {FLAG}"), ("2359", f"--skip-dashboard {FLAG}"),
    ],
)  # fmt: skip
def test_only_the_half_hour_after_midnight_is_a_dashboard_run(tmp_path: Path, hhmm: str, arguments: str) -> None:
    """The step's script with a ``date`` that answers *hhmm* and a ``python`` that notes its arguments."""
    script = str(_stats_step()["run"])
    assert "${{" not in script and "TZ=Europe/Vienna date +%H%M" in script
    calls = tmp_path / "calls"
    for name, body in (
        ("date", f'[ "$TZ" = "Europe/Vienna" ] && [ "$1" = "+%H%M" ] && echo {hhmm}\n'),
        ("python", f'echo "$*" >> "{calls}"\n'),
    ):
        tool = tmp_path / name
        tool.write_text(f"#!/bin/bash\n{body}", encoding="utf-8")
        tool.chmod(0o755)
    env = {"PATH": f"{tmp_path}{os.pathsep}/usr/bin:/bin"}
    subprocess.run(["bash", "-e", "-c", script], env=env, check=True, capture_output=True)
    assert calls.read_text(encoding="utf-8").splitlines() == [f"scripts/generate_markdown_stats.py {arguments}"]


def test_only_a_step_that_tolerates_the_one_asks_for_it() -> None:
    # build-feed.yml and the manual refresh publish after the statistics
    # step and have no continue-on-error: an overdue review must not stop them.
    callers = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in _workflow(path.name).get("jobs", {}).values():
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                if "generate_markdown_stats.py" not in run:
                    continue
                callers += 1
                calls = [line for line in run.splitlines() if "scripts/generate_markdown_stats.py" in line]
                if path.name == "update-cycle.yml":
                    assert len(calls) == 2 and all(FLAG in line for line in calls)
                    assert step["continue-on-error"] is True
                else:
                    assert not any(FLAG in line for line in calls), path.name
    assert callers == 3

