"""Teil 2 der Tests aus tests/test_health_check_alarms.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

import scripts.health_check as hc
import src.health_check.github_runs as hc_github_runs

from ._support import (
    _BUILD_ANNOTATIONS,
    _Listing,
    _NOW,
    _PAGES_RUNS,
    _PAGES_WORKFLOW,
    _REVIEW_RUNS,
    _WORKFLOWS,
    _annotation,
    _failed_page,
    _jobs,
    _pages,
    _pages_annotated,
    _pages_page,
    _pages_run,
    _run,
    _runs,
)


def test_without_a_token_the_api_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Origamihase/wien-oepnv")
    monkeypatch.setattr(hc_github_runs, "fetch_content_safe", lambda *_a, **_kw: pytest.fail("no request expected"))
    with pytest.raises(RuntimeError):
        hc._github_api_json(_REVIEW_RUNS)


def test_the_review_workflow_fails_when_the_review_cannot_run() -> None:
    """Fund B of the Architektur-Review (31.10.): no continue-on-error on the
    review step any more, the Health check holds ``actions: read`` and the
    token to read the run's conclusion, fork PRs (no secrets) are skipped."""
    repo_root = Path(__file__).resolve().parents[2]
    review = (repo_root / ".github" / "workflows" / "claude-code-review.yml").read_text(encoding="utf-8")
    step = review.split("id: claude-review", 1)[1].split("- name:", 1)[0]
    code = "\n".join(line for line in step.splitlines() if not line.strip().startswith("#"))
    assert "continue-on-error" not in code
    assert "github.event.pull_request.head.repo.full_name == github.repository" in review
    health = (repo_root / ".github" / "workflows" / "health-check.yml").read_text(encoding="utf-8")
    assert "actions: read" in health
    assert "GITHUB_TOKEN: ${{ github.token }}" in health
    assert {name for name, _label in hc.TOOLING_WORKFLOWS} == {"claude-code-review.yml"}


def test_a_green_pages_run_is_green_and_cancelled_runs_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub cancels a run a newer commit supersedes (165 of 1,972 from 01.10. to 01.11.2026)."""
    seen = _pages(monkeypatch, [_pages_run(3, "cancelled", 5), _pages_run(2, "success", 30)])
    check = hc.check_pages_build(_NOW)
    assert check.ok and not check.note
    assert check.summary == "OK — letzter Lauf sauber am 28.10. 12:31"
    assert seen == [_WORKFLOWS, _PAGES_RUNS]


def test_a_failed_jekyll_build_is_red_at_once_and_names_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026: from the merge of #2092 every build failed in Jekyll."""
    runs = [
        _pages_run(5, "failure", 20),
        _pages_run(4, "cancelled", 25),
        _pages_run(3, "failure", 50, "Merge pull request #2092 from Origamihase/x\n\nKI-Wächter"),
        _pages_run(2, "success", 80),
    ]
    _pages(monkeypatch, runs, {5: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.summary == (
        "FEHLER — Jekyll-Build gescheitert am 28.10. 12:41, 2 Läufe in Folge seit 28.10. 12:10 "
        "(Inhalt unter docs/ prüfen); GitHub Pages liefert den alten Stand aus"
    )
    assert check.detail == (
        "zuerst nach Commit „Merge pull request #2092 from Origamihase/x“ — "
        "https://github.com/Origamihase/wien-oepnv/actions/runs/5"
    )


def test_one_failed_jekyll_build_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
           {3: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Jekyll-Build gescheitert am 28.10. 12:41 (Inhalt unter docs/ prüfen)" in check.summary
    assert check.detail.startswith("Commit „chore: update cycle“ — ")


@pytest.mark.parametrize(
    "failed_step", ["Deploy to GitHub Pages", "Pull ghcr.io/actions/jekyll-build-pages:v1.0.13", None]
)
def test_one_failure_on_githubs_side_is_a_note(monkeypatch: pytest.MonkeyPatch, failed_step: str | None) -> None:
    """October 2026: four deploy failures at GitHub, each gone with the next
    run; on 05.10. the deploy job was cancelled without a failed step."""
    _pages(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)], {3: _jobs(failed_step)})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert check.summary == (
        "HINWEIS — Auslieferung durch GitHub gescheitert am 28.10. 12:41; der nächste Lauf liefert neu aus"
    )


@pytest.mark.parametrize(
    "tail",
    [
        # Pages run #23181, 16.07.2026 22:31 UTC (and #23182, #23183): the next run built.
        "Error: GET https://api.github.com/repos/Origamihase/wien-oepnv: 503 - <!DOCTYPE html>",
        # Pages run #20334, 23.05.2026 10:01 UTC: the next run, five minutes later, built.
        "Error: The GitHub API credentials you provided aren't valid. ",
        # jekyll-github-metadata 2.16.1 asks for /pages first; Octokit::BadGateway.
        "Error:  GET https://api.github.com/repos/Origamihase/wien-oepnv/pages: 502 - Server Error",
    ],
)
def test_one_jekyll_failure_on_githubs_api_is_a_note(monkeypatch: pytest.MonkeyPatch, tail: str) -> None:
    seen = _pages_annotated(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
                            {3: _jobs("Build with Jekyll")}, _annotation(tail))
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note, check.summary
    assert check.summary == (
        "HINWEIS — Jekyll-Build an GitHubs API gescheitert am 28.10. 12:41; der nächste Lauf baut neu"
    )
    assert _BUILD_ANNOTATIONS in seen


def test_two_jekyll_failures_on_githubs_api_in_a_row_are_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.07.2026: three runs in a row failed on a 503 of the API (22:31-23:31 UTC)."""
    runs = [_pages_run(4, "failure", 20), _pages_run(3, "failure", 50), _pages_run(2, "success", 80)]
    _pages_annotated(monkeypatch, runs, {4: _jobs("Build with Jekyll")}, _annotation(
        "Error: GET https://api.github.com/repos/Origamihase/wien-oepnv: 503 - <!DOCTYPE html>"))
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.summary == (
        "FEHLER — Jekyll-Build an GitHubs API gescheitert am 28.10. 12:41, 2 Läufe in Folge seit "
        "28.10. 12:10; GitHub Pages liefert den alten Stand aus"
    )


@pytest.mark.parametrize(
    "annotations",
    [
        # 01.11.2026: the output was cut at 4096 characters before the error.
        [{"annotation_level": "failure", "message": " Logging at level: debug ... Rendering: reference/trip.md"}],
        _annotation("Error:  Liquid syntax error (line 1): Unknown tag 'endraw'"),
        # A warning that quotes the API is not the step's error.
        [{"annotation_level": "warning",
          "message": "github-pages 232 | Error: GET https://api.github.com/repos/x/y: 503 - x"}],
        RuntimeError("HTTP 403 Resource not accessible by integration"),
        {"message": "Not Found"},
    ],
)
def test_a_jekyll_failure_not_shown_to_be_githubs_api_stays_red(
    monkeypatch: pytest.MonkeyPatch, annotations: object
) -> None:
    _pages_annotated(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
                     {3: _jobs("Build with Jekyll")}, annotations)
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Jekyll-Build gescheitert am 28.10. 12:41 (Inhalt unter docs/ prüfen)" in check.summary


def test_the_health_check_may_read_the_annotations() -> None:
    health = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "health-check.yml").read_text(
        encoding="utf-8"
    )
    assert "checks: read" in health


def test_two_failures_on_githubs_side_in_a_row_are_red(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [_pages_run(4, "failure", 20), _pages_run(3, "failure", 50), _pages_run(2, "success", 80)]
    _pages(monkeypatch, runs, {4: _jobs("Deploy to GitHub Pages")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Auslieferung durch GitHub gescheitert am 28.10. 12:41, 2 Läufe in Folge seit 28.10. 12:10" in check.summary


@pytest.mark.parametrize("jobs", [RuntimeError("HTTP 502"), {"jobs": []}, {}])
def test_a_failed_run_whose_jobs_cannot_be_read_is_red(monkeypatch: pytest.MonkeyPatch, jobs: object) -> None:
    _pages(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)], {3: jobs})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Pages-Lauf gescheitert am 28.10. 12:41" in check.summary


@pytest.mark.parametrize("conclusion", ["timed_out", "startup_failure", "action_required"])
def test_every_other_conclusion_counts_as_failed(monkeypatch: pytest.MonkeyPatch, conclusion: str) -> None:
    _pages(monkeypatch, [_pages_run(3, conclusion, 20)], {3: _jobs("Build with Jekyll")})
    assert not hc.check_pages_build(_NOW).ok


def test_while_every_run_read_failed_the_next_page_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026, 10:02 UTC: the 20 runs before reached back only to 08:30
    UTC (merges came in), so the start of the streak (#2092, 07:43 UTC) lay
    on the next page."""
    page1 = _failed_page(1)["workflow_runs"]
    assert isinstance(page1, list)
    page2 = [_pages_run(980, "failure", 150, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(979, "success", 160)]
    seen = _pages(monkeypatch, page1, {1000: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert f", {hc.PAGES_RUNS_PER_PAGE + 1} Läufe in Folge seit 28.10. 10:30 (Inhalt" in check.summary
    assert check.detail.startswith("zuerst nach Commit „Merge pull request #2092 from Origamihase/x“")
    assert seen == [_WORKFLOWS, _PAGES_RUNS, _pages_page(2), "/actions/runs/1000/jobs?per_page=20",
                    _BUILD_ANNOTATIONS]


def test_a_streak_longer_than_the_runs_read_says_at_least(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [_failed_page(page) for page in range(1, hc.PAGES_MAX_PAGES + 1)]
    first = pages[0]["workflow_runs"]
    assert isinstance(first, list)
    seen = _pages(monkeypatch, first, {1000: _jobs("Build with Jekyll")}, *pages[1:])
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    total = hc.PAGES_RUNS_PER_PAGE * hc.PAGES_MAX_PAGES
    assert f"mindestens {total} Läufe in Folge (Inhalt" in check.summary
    assert check.detail == "https://github.com/Origamihase/wien-oepnv/actions/runs/1000"
    assert _pages_page(hc.PAGES_MAX_PAGES + 1) not in seen


def test_a_success_listed_ahead_of_newer_runs_does_not_end_the_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """01.11.2026: the API listed #29084 (created 08:56 UTC) as 09:56 UTC,
    ahead of the runs of 09:00-09:55 UTC. Had it succeeded and the build
    broken with the next run, page 1 would have held that success and page 2
    the first failed run: the streak counted from page 1 alone was one short
    and named the wrong commit."""
    page1 = [_pages_run(n, "failure", 10 + 5 * (120 - n)) for n in range(120, 101, -1)]
    page1.insert(10, _pages_run(100, "success", 55))
    assert len(page1) == hc.PAGES_RUNS_PER_PAGE
    page2 = [_pages_run(101, "failure", 120, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(99, "success", 130)]
    seen = _pages(monkeypatch, page1, {120: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert ", 20 Läufe in Folge seit 28.10. 11:00 (Inhalt" in check.summary
    assert check.detail.startswith("zuerst nach Commit „Merge pull request #2092 from Origamihase/x“")
    assert _pages_page(2) in seen


def test_cancelled_runs_take_places_on_a_page_and_the_next_page_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """01.11.2026, from 09:53 UTC: 17 failed and 3 cancelled runs filled
    page 1; the start of the streak lay on page 2."""
    page1 = [_pages_run(1000 - i, "cancelled" if i in (3, 8, 9) else "failure", 10 + 5 * i)
             for i in range(hc.PAGES_RUNS_PER_PAGE)]
    page2 = [_pages_run(980, "failure", 150, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(979, "success", 160)]
    seen = _pages(monkeypatch, page1, {1000: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok and ", 18 Läufe in Folge seit" in check.summary, check.summary
    assert check.detail.startswith("zuerst nach Commit „Merge pull request #2092 from Origamihase/x“")
    assert _pages_page(2) in seen


def test_a_run_listed_on_two_pages_counts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that finishes between two page requests moves every run down by
    one, so the last run of page 1 comes back first on page 2."""
    page1 = _failed_page(1)["workflow_runs"]
    assert isinstance(page1, list)
    page2 = [page1[-1], _pages_run(980, "failure", 150, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(979, "success", 160)]
    _pages(monkeypatch, page1, {1000: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert f", {hc.PAGES_RUNS_PER_PAGE + 1} Läufe in Folge seit 28.10. 10:30 (Inhalt" in check.summary


def test_a_later_page_that_does_not_answer_keeps_what_was_read(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _failed_page(1)["workflow_runs"]
    assert isinstance(first, list)
    _pages(monkeypatch, first, {1000: _jobs("Build with Jekyll")}, RuntimeError("HTTP 502"))
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert f"mindestens {hc.PAGES_RUNS_PER_PAGE} Läufe in Folge (Inhalt" in check.summary


def test_a_good_run_on_a_full_first_page_ends_the_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each further page is some 260 KB and one more call per check."""
    runs = [_pages_run(1000, "failure", 10)] + [_pages_run(999 - i, "success", 40 + 30 * i) for i in range(30)]
    seen = _runs(monkeypatch, {_WORKFLOWS: _PAGES_WORKFLOW, "/actions/workflows/189040121/runs": _Listing(runs),
                               "/actions/runs/1000/jobs?per_page=20": _jobs("Build with Jekyll"),
                               _BUILD_ANNOTATIONS: []})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert seen == [_WORKFLOWS, _PAGES_RUNS, "/actions/runs/1000/jobs?per_page=20", _BUILD_ANNOTATIONS]


def test_a_first_page_that_does_not_answer_is_a_note_that_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: RuntimeError("HTTP 502")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert check.summary == "HINWEIS — Actions-API nicht abgefragt (RuntimeError: HTTP 502)"


def test_a_page_that_is_not_full_ends_the_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """A streak that began with the first run of all: no further page."""
    seen = _pages(monkeypatch, [_pages_run(4, "failure", 20), _pages_run(3, "failure", 50)],
                  {4: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "mindestens 2 Läufe in Folge" in check.summary
    assert _pages_page(2) not in seen


def test_the_latest_pages_run_is_chosen_by_run_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026: the API listed #29084 (created 08:56 UTC, shown as 09:56
    UTC) before #29099 (09:55 UTC). By the API's order a fix after a broken
    build stayed red, and a break after a good run stayed green."""
    shifted = _pages_run(29084, "failure", 10)
    newer = _pages_run(29099, "success", 15)
    older = _pages_run(29083, "success", 70)
    _pages(monkeypatch, [shifted, newer, older], {29084: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and not check.note, check.summary

    shifted["conclusion"], newer["conclusion"] = "success", "failure"
    _pages(monkeypatch, [shifted, newer, older], {29099: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.detail.endswith("/actions/runs/29099")


def test_a_run_time_after_the_check_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026, 09:01 UTC: run #29084 had ended at 08:57 UTC, the API
    said 09:57 UTC."""
    late = _pages_run(4, "failure", -50)
    _pages(monkeypatch, [late, _pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
           {4: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.summary.startswith("FEHLER — Jekyll-Build gescheitert, 2 Läufe in Folge seit 28.10. 12:40 (")

    _runs(monkeypatch, {_REVIEW_RUNS: _run("success", _NOW + timedelta(minutes=60))})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and check.summary == "OK — Code-Review zuletzt sauber"

    # A run that ended while the check ran keeps its time.
    _runs(monkeypatch, {_REVIEW_RUNS: _run("success", _NOW + timedelta(minutes=3))})
    check = hc.check_tooling_runs(_NOW)
    assert check.summary == "OK — Code-Review zuletzt sauber am 28.10. 13:03"

    # A streak whose first run the API dates after the check: no start time.
    _pages(monkeypatch, [_pages_run(5, "failure", -50), _pages_run(4, "failure", -40), _pages_run(3, "success", 80)],
           {5: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert check.summary.startswith("FEHLER — Jekyll-Build gescheitert, 2 Läufe in Folge (Inhalt"), check.summary


def test_one_failed_jekyll_build_with_nothing_before_names_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(monkeypatch, [_pages_run(3, "failure", 20)], {3: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "in Folge" not in check.summary
    assert check.detail.startswith("Commit „chore: update cycle“ — ")


def test_a_github_failure_after_a_good_run_is_a_note_whatever_came_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The streak ends at the last good run: an older failure does not count."""
    runs = [_pages_run(5, "failure", 20), _pages_run(4, "success", 50), _pages_run(3, "failure", 80)]
    _pages(monkeypatch, runs, {5: _jobs("Deploy to GitHub Pages")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note, check.summary


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "neutral"])
def test_runs_github_did_not_finish_are_skipped(monkeypatch: pytest.MonkeyPatch, conclusion: str) -> None:
    _pages(monkeypatch, [_pages_run(3, conclusion, 5), _pages_run(2, "success", 30)])
    check = hc.check_pages_build(_NOW)
    assert check.ok and not check.note, check.summary


def test_malformed_answers_are_a_note_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_WORKFLOWS: {"workflows": [{"id": "189040121", "path": hc.PAGES_WORKFLOW_PATH}]}})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein Workflow" in check.summary

    _runs(monkeypatch, {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: {"workflow_runs": ["kein Lauf", None]}})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein abgeschlossener Pages-Lauf" in check.summary


def test_the_pages_of_runs_fit_the_api_cap() -> None:
    """A run is about 13 to 15 KB of JSON as the check reads it (20 Pages
    runs were 257,496 bytes and 30 review runs 405,393 bytes on 01.11.2026;
    GitHub sends more, pretty-printed, to the default User-Agents of curl and
    wget); an answer
    over GITHUB_API_MAX_BYTES would leave the check a note for good."""
    assert hc.PAGES_RUNS_PER_PAGE * 16_000 < hc.GITHUB_API_MAX_BYTES
    assert hc.TOOLING_RUNS_PER_PAGE * 16_000 < hc.GITHUB_API_MAX_BYTES


def test_pages_notes_when_nothing_can_be_said(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_WORKFLOWS: RuntimeError("GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "Actions-API nicht abgefragt (RuntimeError: GITHUB_TOKEN" in check.summary

    _runs(monkeypatch, {_WORKFLOWS: {"workflows": [{"id": 1, "path": ".github/workflows/test.yml"}]}})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein Workflow dynamic/pages/pages-build-deployment gefunden" in check.summary

    _pages(monkeypatch, [_pages_run(3, "cancelled", 20)])
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein abgeschlossener Pages-Lauf" in check.summary
