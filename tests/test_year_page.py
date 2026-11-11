"""The page of a finished year as the statistics run writes it.

Michael on 2026-11-10: the page of a year is created on 01.01., the year
can be chosen on the website under "Aktuelle Meldungen", the page sums up
all cancellations and delays in a statistic and Gemini summarises the
year's roadworks. The parts have their own tests (``test_year_stats.py``,
``test_baustellen_year.py``, ``test_year_review.py``,
``test_site_year_switch.py``); this file drives
``scripts/generate_markdown_stats.py`` through a year change and checks
that the parts reach the page, the summary and the exit code together.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts import generate_markdown_stats as stats_script
from src.markdown_stats import year_review
from src.markdown_stats.review_client import ReviewAnswer
from src.utils.stats import STAMMSTRECKE_HEADER, VIENNA_TZ, WEEKDAY_LABELS
from src.utils.stats_baustellen import append_baustellen_rows

OVERVIEW = "## Das Jahr im Überblick"
BAUSTELLEN = "## Baustellen mit Folgen für den öffentlichen Verkehr"
PENDING = "_Ein kurzer Rückblick in Worten wird hier ergänzt._"

# The four sites and the text of tests/test_year_review.py: the text names
# only what the sites hold, so the guard accepts it.
EVENTS = [
    {
        "guid": "a" * 64,
        "title": "Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke",
        "description": "Aufgrund der Sanierung der Tunneldecke der U4-Trasse wird die "
        "Fahrtrichtung zum Heumarkt umgeleitet.",
        "starts_at": "2026-02-01T00:00:00+01:00",
        "context": {"district": "3", "measure": "Gleisbau"},
    },
    {
        "guid": "b" * 64,
        "title": "Eßlinger Hauptstraße 96",
        "description": "Der Busverkehr der Linie 26A wird über den Kreisverkehr bei der "
        "Gartenheimstraße umgeleitet. Die Arbeiten erfolgen in der Zeit von 20:00 Uhr bis 05:00 Uhr.",
        "starts_at": "2026-05-25T00:00:00+02:00",
        "ends_at": "2026-09-06T00:00:00+02:00",
        "context": {"district": "22", "measure": "Rohrlegung"},
    },
    {
        "guid": "c" * 64,
        "title": "Burggasse 67",
        "description": "Die Haltestelle des betroffenen öffentlichen Verkehrsmittels wird von "
        "Burggasse ONr.67 nach Burggasse ONr. 69 verlegt.",
        "starts_at": "2026-07-28T00:00:00+02:00",
        "ends_at": "2026-10-30T00:00:00+01:00",
        "context": {"district": "7", "measure": "Straßenbau"},
    },
    {
        "guid": "d" * 64,
        "title": "Rennweg von 33A bis 37",
        "description": "Der linke Fahrstreifen wird gesperrt. Die Linie 71 wird über die "
        "Wiedner Hauptstraße umgeleitet.",
        "starts_at": "2026-09-30T00:00:00+02:00",
        "ends_at": "2026-11-15T00:00:00+01:00",
        "context": {"district": "3", "measure": "Gleisbau"},
    },
]
GOOD = (
    "Im Jahr 2026 sind 4 Baustellen mit Folgen für den öffentlichen Verkehr erfasst. "
    "Zwei davon lagen im 3. Bezirk (Landstraße), je eine in Neubau und in der Donaustadt. "
    "In der Burggasse wurde eine Haltestelle verlegt, in der Eßlinger Hauptstraße wurde der Busverkehr "
    "der Linie 26A umgeleitet. "
    "Am Rennweg wurde ab 30.09.2026 an den Gleisen gearbeitet, die Linie 71 fuhr über die Wiedner Hauptstraße. "
    "Die Arbeiten an der U4-Trasse begannen im Februar."
)


@pytest.fixture(autouse=True)
def _quiet_surroundings(monkeypatch: pytest.MonkeyPatch) -> None:
    # The poll record of the working copy would be months old in these runs,
    # and a key in the shell must not send a test to Gemini.
    monkeypatch.setattr(stats_script, "load_poll_status", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _seed(tmp_path: Path, *, baustellen: bool) -> None:
    stats = tmp_path / "stats"
    stats.mkdir()
    lines = []
    when = datetime(2026, 12, 30, 6, 0, tzinfo=VIENNA_TZ)
    while when.year == 2026:
        for direction in ("Meidling", "Praterstern"):
            lines.append(
                f"{when.isoformat(timespec='seconds')},{WEEKDAY_LABELS[when.weekday()]},"
                f"{when.hour:02d},{direction},1.00\n"
            )
        when += timedelta(minutes=30)
    (stats / "stammstrecke_2026.csv").write_text(
        ",".join(STAMMSTRECKE_HEADER) + "\n" + "".join(lines), encoding="utf-8"
    )
    if baustellen:
        written = append_baustellen_rows(
            EVENTS, now=datetime(2026, 10, 1, 12, 0, tzinfo=VIENNA_TZ), stats_dir=stats
        )
        assert written == len(EVENTS)


def _run(tmp_path: Path, now_iso: str, *extra: str) -> int:
    """One run as the Update cycle starts it: README patched, no ``--skip-readme``.

    Until the review of 2026-11-10 every run here passed ``--skip-readme``,
    a way to the exit code the workflow never takes.
    """
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
            "--stats-dir", str(tmp_path / "stats"),
            "--output", str(tmp_path / "statistik.md"),
            "--summary-path", str(tmp_path / "stats-summary.json"),
            "--readme-path", str(readme),
            "--now-iso", now_iso,
            *extra,
        ]
    )  # fmt: skip


def _page(tmp_path: Path) -> list[str]:
    return (tmp_path / "statistik-2026.md").read_text(encoding="utf-8").splitlines()


def _answer(monkeypatch: pytest.MonkeyPatch, text: str | None) -> list[str]:
    """Let Gemini answer *text* (``None``: no answer); returns the prompts it was sent."""
    prompts: list[str] = []

    def ask(prompt: str, *, key: str, deadline_s: float | None = None) -> ReviewAnswer | None:
        assert key == "k"
        prompts.append(prompt)
        return None if text is None else ReviewAnswer(text, "gemini-test")

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(year_review, "ask_review", ask)
    return prompts


def test_year_page_holds_the_overview_and_the_dashboard_does_not(tmp_path: Path) -> None:
    _seed(tmp_path, baustellen=False)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    page = _page(tmp_path)
    headings = [line for line in page if line.startswith("## ")]
    assert headings == [
        "## Kennzahlen auf einen Blick",
        OVERVIEW,
        "## Stammstrecke",
        "## Ausfälle",
        "## Störungen",
    ]
    assert page[4] == (
        "Abgeschlossenes Jahr · [Zur laufenden Statistik](statistik.md) · [Zur Website](site.html)"
    )
    dashboard = (tmp_path / "statistik.md").read_text(encoding="utf-8")
    assert OVERVIEW not in dashboard and BAUSTELLEN not in dashboard


def test_summary_names_the_year_in_the_run_that_writes_its_page(tmp_path: Path) -> None:
    """The website's year switch reads ``archive_years``; no half hour without the year."""
    _seed(tmp_path, baustellen=False)
    assert _run(tmp_path, "2026-12-31T23:31:00+01:00", "--skip-dashboard") == 0
    summary = json.loads((tmp_path / "stats-summary.json").read_text(encoding="utf-8"))
    assert summary["archive_years"] == []
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    summary = json.loads((tmp_path / "stats-summary.json").read_text(encoding="utf-8"))
    assert (summary["year"], summary["archive_years"]) == (2027, [2026])


def test_roadworks_without_a_key_are_listed_and_the_review_is_announced(tmp_path: Path) -> None:
    _seed(tmp_path, baustellen=True)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    page = _page(tmp_path)
    assert [line for line in page if line.startswith("## ")][-1] == BAUSTELLEN
    assert "Im Jahr 2026 sind **4** solche Baustellen erfasst." in page
    assert PENDING in page and "### Rückblick" not in page
    assert sum(line.startswith("| ") and "Burggasse 67" in line for line in page) == 1
    assert not (tmp_path / "stats" / "baustellen_rueckblick_2026.json").exists()


def test_accepted_review_is_asked_once_stored_and_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, baustellen=True)
    prompts = _answer(monkeypatch, GOOD)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    page = _page(tmp_path)
    # The page escapes the text for Markdown ("\\(Landstraße\\)").
    assert page[page.index("### Rückblick") + 2].replace("\\", "") == GOOD
    assert PENDING not in page
    assert len(prompts) == 1 and "Burggasse 67" in prompts[0]
    stored = json.loads(
        (tmp_path / "stats" / "baustellen_rueckblick_2026.json").read_text(encoding="utf-8")
    )
    assert (stored["text"], stored["model"], stored["year"]) == (GOOD, "gemini-test", 2026)
    # The next days read the stored text: no second request, the page stays.
    before = (tmp_path / "statistik-2026.md").read_bytes()
    assert _run(tmp_path, "2027-01-02T00:01:00+01:00") == 0
    assert _run(tmp_path, "2027-03-01T00:01:00+01:00") == 0
    assert len(prompts) == 1
    assert (tmp_path / "statistik-2026.md").read_bytes() == before


def test_ticks_between_midnights_never_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, baustellen=True)
    prompts = _answer(monkeypatch, GOOD)
    assert _run(tmp_path, "2027-01-01T00:31:00+01:00", "--skip-dashboard") == 0
    assert _run(tmp_path, "2027-01-01T12:01:00+01:00", "--skip-dashboard") == 0
    assert prompts == []
    assert not (tmp_path / "statistik-2026.md").exists()


def test_rejected_review_is_not_shown_and_is_asked_again_the_next_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, baustellen=True)
    # The Praterstraße is in no site: the guard rejects the text.
    prompts = _answer(monkeypatch, GOOD + " Auch in der Praterstraße wurde gebaut.")
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00") == 0
    page = _page(tmp_path)
    assert PENDING in page and not any("Praterstraße" in line for line in page)
    assert not (tmp_path / "stats" / "baustellen_rueckblick_2026.json").exists()
    assert _run(tmp_path, "2027-01-02T00:01:00+01:00") == 0
    assert len(prompts) == 2
    _answer(monkeypatch, GOOD)
    assert _run(tmp_path, "2027-01-03T00:01:00+01:00") == 0
    page = [line.replace("\\", "") for line in _page(tmp_path)]
    assert PENDING not in page and GOOD in page


def test_missing_review_fails_the_step_from_the_fourth_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Three days are a log line, then every run ends with 1 until the review is there.

    Only with ``--fail-on-missing-review``, which the Update cycle passes on
    both calls. Until the review of 2026-11-10 every caller got the 1, also
    build-feed.yml and the manual refresh, which then skipped their publish.
    """
    _seed(tmp_path, baustellen=True)
    _answer(monkeypatch, None)
    flag = "--fail-on-missing-review"
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00", flag) == 0
    assert _run(tmp_path, "2027-01-03T23:31:00+01:00", "--skip-dashboard", flag) == 0
    assert _run(tmp_path, "2027-01-04T00:01:00+01:00", flag) == 1
    assert "Rückblick der Baustellen fehlt für 2026" in caplog.text
    # The run wrote everything all the same.
    assert "2027-01-04T00:01+01:00" in (tmp_path / "statistik.md").read_text(encoding="utf-8")
    assert PENDING in _page(tmp_path)
    # Not only the run after midnight: the health check reads the last run.
    assert _run(tmp_path, "2027-01-04T00:31:00+01:00", "--skip-dashboard", flag) == 1
    # The two other workflows call the script without the flag.
    assert _run(tmp_path, "2027-01-04T00:31:00+01:00", "--skip-dashboard") == 0
    assert _run(tmp_path, "2027-01-04T01:01:00+01:00") == 0
    _answer(monkeypatch, GOOD)
    assert _run(tmp_path, "2027-01-05T00:01:00+01:00", flag) == 0
    assert _run(tmp_path, "2027-01-05T00:31:00+01:00", "--skip-dashboard", flag) == 0


def test_year_without_roadworks_never_fails_the_step(tmp_path: Path) -> None:
    _seed(tmp_path, baustellen=False)
    assert _run(tmp_path, "2027-01-01T00:01:00+01:00", "--fail-on-missing-review") == 0
    assert _run(tmp_path, "2027-06-01T00:01:00+02:00", "--fail-on-missing-review") == 0
    assert BAUSTELLEN not in _page(tmp_path)


def test_replay_of_another_year_does_not_judge_the_review(tmp_path: Path) -> None:
    _seed(tmp_path, baustellen=True)
    flag = "--fail-on-missing-review"
    assert _run(tmp_path, "2027-02-01T00:01:00+01:00", "--year", "2026", "--skip-dashboard", flag) == 0
