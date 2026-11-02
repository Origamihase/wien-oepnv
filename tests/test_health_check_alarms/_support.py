"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_health_check_alarms.py.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs

import pytest

import scripts.health_check as hc

_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(hc, "REPO_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


def _updater(repo: Path, name: str, exit_code: int, output: str = "") -> str:
    """A stand-in updater that prints *output* and exits with *exit_code*."""
    script = repo / "scripts" / name
    script.write_text(
        f"import sys\nprint({output!r})\nsys.exit({exit_code})\n", encoding="utf-8"
    )
    return name


# --- Feed-Build: a stalled update cycle -----------------------------------


def _feed(repo: Path, built: datetime) -> None:
    (repo / "docs" / "feed.xml").write_text(
        "<rss><channel><lastBuildDate>"
        f"{format_datetime(built)}"
        "</lastBuildDate></channel></rss>",
        encoding="utf-8",
    )


# --- Veröffentlichung: the live copy on GitHub Pages (Architektur-Review 31.10.) ---


def _rss(built: datetime) -> str:
    return (
        "<rss><channel><lastBuildDate>"
        + format_datetime(built)
        + "</lastBuildDate></channel></rss>"
    )


def _both_feeds(repo: Path, built: datetime) -> None:
    for name in hc.PUBLISHED_FEEDS:
        (repo / "docs" / name).write_text(_rss(built), encoding="utf-8")


def _live(monkeypatch: pytest.MonkeyPatch, by_url: dict[str, object]) -> list[str]:
    """Serve *by_url* in place of GitHub Pages; a value that is an exception is raised."""
    seen: list[str] = []

    def fetch(url: str) -> str | None:
        seen.append(url)
        value = by_url[url]
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    monkeypatch.setattr(hc, "_fetch_live_feed", fetch)
    return seen


def _urls() -> dict[str, str]:
    return {name: f"{hc.PAGES_BASE_URL}/{name}" for name in hc.PUBLISHED_FEEDS}


# --- Stationsverzeichnis: the weekly station run --------------------------


def _last_run(repo: Path, **fields: object) -> None:
    payload: dict[str, object] = {
        "timestamp": (_NOW - timedelta(days=3)).isoformat(),
        "sub_scripts": [
            {"name": "update_station_directory.py", "exit_code": 0},
            {"name": "update_wl_stations.py", "exit_code": 0},
        ],
        "validation": {"security_issues": 0},
    }
    payload.update(fields)
    (repo / "data" / "stations_last_run.json").write_text(json.dumps(payload), encoding="utf-8")


# --- main(): a red check must make the run red ----------------------------


def _checks() -> list[str]:
    """Every check function of the module, read from the module itself.

    Until 2026-11-01 the tests below named the checks by hand; the check
    ``Übersetzung`` came to ``main()`` without them, so dropping it went
    unnoticed and the main() tests read the real
    ``data/translation_status.json`` (review of 2026-11-01).
    """
    return sorted(
        name
        for name, value in vars(hc).items()
        if name.startswith("check_")
        and inspect.isfunction(value)
        and value.__module__ == hc.__name__
    )


# Read once at import, before any test replaces a check.
_CHECKS = _checks()

_SIGNATURES = {name: inspect.signature(getattr(hc, name)) for name in _CHECKS}


def _stub(name: str, result: hc.Check) -> Callable[..., hc.Check]:
    """A stand-in that takes only what the real check takes, so a call with
    the wrong arguments in ``main()`` still fails here."""
    signature = _SIGNATURES[name]

    def stub(*args: object, **kwargs: object) -> hc.Check:
        signature.bind(*args, **kwargs)
        return result

    return stub


def _all_green(monkeypatch: pytest.MonkeyPatch) -> None:
    healthy = hc.Check("OK", ok=True, summary="OK")
    for name in _CHECKS:
        monkeypatch.setattr(hc, name, _stub(name, healthy))
    monkeypatch.setattr(hc, "assess_source", lambda probe, *_a: probe)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


# --- Nebenschritte: a side step that cannot run (decision 2026-10-28) -----


def _record(repo: Path, file_name: str, updated: datetime, **steps: dict[str, str]) -> None:
    folder = repo / "data" / "side_steps"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"updated": updated.isoformat(), "steps": steps}
    (folder / file_name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def side_steps(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(hc, "SIDE_STEP_DIR", repo / "data" / "side_steps")
    return repo


def _ok(at: datetime) -> dict[str, str]:
    return {"outcome": "success", "at": at.isoformat(), "last_success": at.isoformat()}


# --- Quellen: three hours without a usable answer (decision 2026-10-28) ---


def _watch_state(repo: Path, monkeypatch: pytest.MonkeyPatch, **fields: object) -> None:
    state: dict[str, object] = {"updated": (_NOW - timedelta(minutes=5)).isoformat(), "fetch": {}}
    state.update(fields)
    path = repo / "data" / "feed_watch.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(hc, "FEED_WATCH_STATE", path)


_OK_PROBE = hc.Check("Wiener Linien", ok=True, summary="OK — 105 Datensätze geholt")

_BAD_PROBE = hc.Check("Wiener Linien", ok=False, summary="FEHLER — Abruf fehlgeschlagen (exit 1)",
                      detail="502 Server Error: Bad Gateway")


# --- Werkzeuge: the latest run of a pull-request workflow (Fund B, 31.10.) ---


class _Listing:
    """Runs as the Actions API serves them: by ``created_at``, newest first,
    only the page the query asks for (``per_page``, ``page``). Registered
    under the path without its query, so a smaller page size or a missing
    page parameter changes what the check reads."""

    def __init__(self, runs: list[dict[str, object]]) -> None:
        self.runs = runs

    def answer(self, query: str) -> dict[str, object]:
        asked = parse_qs(query)
        per_page, page = int(asked["per_page"][0]), int(asked.get("page", ["1"])[0])
        ordered = sorted(self.runs, key=lambda run: str(run["created_at"]), reverse=True)
        return {"workflow_runs": ordered[(page - 1) * per_page : page * per_page]}


def _runs(monkeypatch: pytest.MonkeyPatch, by_path: dict[str, object]) -> list[str]:
    seen: list[str] = []

    def api(path: str) -> object:
        seen.append(path)
        base, _, query = path.partition("?")
        value = by_path[path] if path in by_path else by_path[base]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, _Listing):
            return value.answer(query)
        return value

    monkeypatch.setattr(hc, "_github_api_json", api)
    return seen


_REVIEW_RUNS = (
    f"/actions/workflows/claude-code-review.yml/runs?status=completed&per_page={hc.TOOLING_RUNS_PER_PAGE}"
)


def _run(conclusion: str | None, finished: datetime = _NOW - timedelta(minutes=50)) -> dict[str, object]:
    return {
        "workflow_runs": [
            {
                "conclusion": conclusion,
                "updated_at": finished.isoformat(),
                "html_url": "https://github.com/Origamihase/wien-oepnv/actions/runs/1",
            }
        ]
    }


def _review(number: int, conclusion: str) -> dict[str, object]:
    return {"run_number": number, "id": number, "conclusion": conclusion,
            "updated_at": (_NOW - timedelta(minutes=50)).isoformat(),
            "html_url": f"https://github.com/Origamihase/wien-oepnv/actions/runs/{number}"}


# --- Pages-Build: the latest run of "pages build and deployment" (01.11.) ---

_WORKFLOWS = "/actions/workflows?per_page=100"


def _pages_page(page: int) -> str:
    return f"/actions/workflows/189040121/runs?status=completed&per_page={hc.PAGES_RUNS_PER_PAGE}&page={page}"


_PAGES_RUNS = _pages_page(1)

_PAGES_WORKFLOW = {
    "workflows": [
        {"id": 189039912, "path": ".github/workflows/build-feed.yml"},
        {"id": 189040121, "path": "dynamic/pages/pages-build-deployment"},
    ]
}


def _pages_run(
    run_id: int, conclusion: str, minutes_ago: int, message: str = "chore: update cycle"
) -> dict[str, object]:
    started = _NOW - timedelta(minutes=minutes_ago)
    return {
        "id": run_id,
        "run_number": run_id,
        "conclusion": conclusion,
        "created_at": started.isoformat(),
        "updated_at": (started + timedelta(minutes=1)).isoformat(),
        "html_url": f"https://github.com/Origamihase/wien-oepnv/actions/runs/{run_id}",
        "head_commit": {"message": message},
    }


def _jobs(failed_step: str | None) -> dict[str, object]:
    """Jobs of a Pages run as the Actions API lists them (01.11.2026): the
    steps after a failed one are ``skipped`` (run 41285931114)."""
    build_steps = ["Set up job", "Pull ghcr.io/actions/jekyll-build-pages:v1.0.13", "Checkout",
                   "Build with Jekyll", "Upload artifact"]
    deploy_steps = ["Set up job", "Deploy to GitHub Pages"]

    def steps(names: list[str]) -> list[dict[str, str]]:
        failed = names.index(failed_step) if failed_step in names else len(names)
        return [
            {"name": n, "conclusion": "success" if i < failed else "failure" if i == failed else "skipped"}
            for i, n in enumerate(names)
        ]

    return {
        "jobs": [
            {"id": 7001, "name": "build", "steps": steps(build_steps)},
            {"id": 7002, "name": "deploy", "steps": steps(deploy_steps)},
        ]
    }


_BUILD_ANNOTATIONS = "/check-runs/7001/annotations?per_page=50"


def _annotation(tail: str) -> list[dict[str, object]]:
    """The error annotation of a failed Jekyll step: the build's output on
    one line, as actions/jekyll-build-pages 1.0.13 writes it."""
    return [
        {"annotation_level": "warning", "message": "Node.js 20 is deprecated."},
        {"annotation_level": "failure",
         "message": " Logging at level: debug GitHub Pages: github-pages v232 ... Layout source: theme "
                    "GitHub Metadata: Calling @client.repository(\"Origamihase/wien-oepnv\", "
                    "{:accept=>\"application/vnd.github.drax-preview+json\"}) github-pages 232 | " + tail},
    ]


def _pages(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, object]],
           jobs: dict[int, object] | None = None, *more_pages: object) -> list[str]:
    # The build job's annotations name no error of GitHub's API.
    by_path: dict[str, object] = {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: {"workflow_runs": runs},
                                  _BUILD_ANNOTATIONS: []}
    for page, value in enumerate(more_pages, start=2):
        by_path[_pages_page(page)] = value
    for run_id, value in (jobs or {}).items():
        by_path[f"/actions/runs/{run_id}/jobs?per_page=20"] = value
    return _runs(monkeypatch, by_path)


def _pages_annotated(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, object]],
                     jobs: dict[int, object], annotations: object) -> list[str]:
    """Like :func:`_pages`, and the build job's annotations answer *annotations*."""
    by_path: dict[str, object] = {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: {"workflow_runs": runs},
                                  _BUILD_ANNOTATIONS: annotations}
    for run_id, value in jobs.items():
        by_path[f"/actions/runs/{run_id}/jobs?per_page=20"] = value
    return _runs(monkeypatch, by_path)


def _failed_page(page: int) -> dict[str, object]:
    first = (page - 1) * hc.PAGES_RUNS_PER_PAGE
    return {"workflow_runs": [_pages_run(1000 - i, "failure", 10 + 5 * i)
                              for i in range(first, first + hc.PAGES_RUNS_PER_PAGE)]}


__all__ = [
    "_BAD_PROBE",
    "_BUILD_ANNOTATIONS",
    "_CHECKS",
    "_Listing",
    "_NOW",
    "_OK_PROBE",
    "_PAGES_RUNS",
    "_PAGES_WORKFLOW",
    "_REVIEW_RUNS",
    "_SIGNATURES",
    "_WORKFLOWS",
    "_all_green",
    "_annotation",
    "_both_feeds",
    "_checks",
    "_failed_page",
    "_feed",
    "_jobs",
    "_last_run",
    "_live",
    "_ok",
    "_pages",
    "_pages_annotated",
    "_pages_page",
    "_pages_run",
    "_record",
    "_review",
    "_rss",
    "_run",
    "_runs",
    "_stub",
    "_updater",
    "_urls",
    "_watch_state",
    "repo",
    "side_steps",
]
