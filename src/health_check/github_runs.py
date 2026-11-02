"""Health checks that read the GitHub Actions API: tooling runs and the Pages build.

Moved verbatim from ``scripts/health_check.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from src.health_check.common import LIVE_FETCH_TIMEOUT_S, Check, _parse_utc, _vienna_hhmm
from src.utils.files import loads_finite
from src.utils.http import fetch_content_safe, session_with_retries
from src.utils.logging import sanitize_log_arg


# Workflows that run on pull requests and therefore cannot record a side step
# into ``data/side_steps/`` (no write to ``main``): the Health check reads the
# conclusion of their latest completed run from the Actions API instead
# (file name, label). Architektur-Review 2026-10-31, Fund B: until then the
# review step ran with ``continue-on-error`` and an expired
# ``CLAUDE_CODE_OAUTH_TOKEN`` left no trace but a green check without comments.
TOOLING_WORKFLOWS: tuple[tuple[str, str], ...] = (
    ("claude-code-review.yml", "Code-Review"),
)
GITHUB_API_MAX_BYTES = 1024 * 1024
# Runs read per tooling workflow: the newest is chosen by run number (see
# _newest_first), and a run the API lists an hour late can stand only ahead
# of the runs of the following hour. Runs GitHub skipped do not count, and
# Dependabot can skip one run per open pull request in a row (up to 10 open
# by .github/dependabot.yml; 9 in a row on 2026-11-01). 30 runs were some
# 400 to 410 KB of JSON on 2026-11-01 (at most 440 KB over all runs so far),
# under GITHUB_API_MAX_BYTES.
TOOLING_RUNS_PER_PAGE = 30


def _github_api_json(path: str) -> Any:
    """GET ``/repos/<GITHUB_REPOSITORY><path>`` from the GitHub API as JSON.

    Read-only, with the run's own ``GITHUB_TOKEN`` (``actions: read``; the
    annotations of a check run need ``checks: read``). Raises
    on a missing token or repository, on an HTTP error and on bad JSON; the
    caller turns every exception into a note.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise RuntimeError("GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")
    api = os.environ.get("GITHUB_API_URL", "").strip().rstrip("/") or "https://api.github.com"
    url = f"{api}/repos/{repository}{path}"
    with session_with_retries("wien-oepnv-health-check", timeout=LIVE_FETCH_TIMEOUT_S) as session:
        session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        content: bytes = fetch_content_safe(
            session,
            url,
            max_bytes=GITHUB_API_MAX_BYTES,
            timeout=LIVE_FETCH_TIMEOUT_S,
            allowed_content_types=("application/json",),
        )
    return loads_finite(content)


def _newest_first(runs: Iterable[object]) -> list[dict[str, Any]]:
    """The runs among *runs*, newest first by run number.

    The Actions API sorts runs by ``created_at``, and on 2026-11-01 GitHub
    gave every run created between 08:00 and 08:59 UTC a ``created_at`` and
    ``updated_at`` one hour late (the end of US daylight saving time): the
    list showed Pages run #29084 (created 08:56 UTC) before #29099 (09:55
    UTC), so the first listed run was not the latest. The run number counts
    up with every run of a workflow and depends on no clock; the id breaks a
    tie. The times shown stay the API's and can be an hour late for runs of
    that hour.
    """
    def key(run: dict[str, Any]) -> tuple[int, int]:
        number, run_id = run.get("run_number"), run.get("id")
        return (number if isinstance(number, int) else -1, run_id if isinstance(run_id, int) else -1)

    return sorted((run for run in runs if isinstance(run, dict)), key=key, reverse=True)


def _conclusion(run: dict[str, Any]) -> str:
    return str(run.get("conclusion") or "")


# Conclusions of a run that did not run: GitHub skipped it (a job-level
# ``if``, such as Dependabot and fork pull requests on the Code-Review) or
# cancelled it. Such a run says nothing about the tool or the build.
_NOT_RUN = frozenset({"cancelled", "skipped", "neutral"})


def _run_time(raw: object, now: datetime) -> str:
    """An Actions API time in Vienna time, or ``""``.

    A time after the check cannot be right: on 2026-11-01 the API gave the
    runs of 08:00-08:59 UTC their times one hour late (see
    :func:`_newest_first`), so at the check of 09:01 UTC a run that had
    ended at 08:57 UTC read 09:57 UTC (10:57 Vienna time). Such a time is
    left out rather than shown.
    """
    moment = _parse_utc(raw)
    if moment is None or moment > now + timedelta(minutes=5):
        return ""
    return _vienna_hhmm(moment)


def check_tooling_runs(now: datetime) -> Check:
    """Red while the latest run of a tooling workflow failed.

    Same rule as ``Nebenschritte`` (operator decision 2026-10-28, "ein Fehler
    sollte auffallen"), for the workflows that run on pull requests and so
    cannot write a record into ``data/side_steps/``: the Code-Review
    (``claude-code-review.yml``) with ``CLAUDE_CODE_OAUTH_TOKEN``. Its step
    ran with ``continue-on-error`` until 2026-10-31, so an expired token left
    only a green check without comments (Architektur-Review 2026-10-31,
    Fund B). Now the run fails, and this check reads the conclusion of the
    latest completed run that ran (by run number, see :func:`_newest_first`;
    runs GitHub skipped or cancelled, such as those of Dependabot pull
    requests, are passed over): ``success`` is green, every other conclusion
    (``failure``, ``timed_out``, ``startup_failure``) is red and names the
    run. A window of :data:`TOOLING_RUNS_PER_PAGE` runs that all did not run
    is a note. An API that cannot be
    asked, or a workflow that never ran, is a note: nothing says the tool is
    broken.
    """
    name = "Werkzeuge"
    problems: list[str] = []
    notes: list[str] = []
    oks: list[str] = []
    for file_name, label in TOOLING_WORKFLOWS:
        try:
            data = _github_api_json(
                f"/actions/workflows/{file_name}/runs?status=completed&per_page={TOOLING_RUNS_PER_PAGE}"
            )
        except Exception as exc:
            reason = str(sanitize_log_arg(str(exc)))[:120]
            notes.append(f"{label}: Actions-API nicht abgefragt ({type(exc).__name__}: {reason})")
            continue
        listed = data.get("workflow_runs") if isinstance(data, dict) else None
        runs = _newest_first(listed) if isinstance(listed, list) else []
        if not runs:
            notes.append(f"{label}: noch kein abgeschlossener Lauf")
            continue
        ran = [run for run in runs if _conclusion(run) not in _NOT_RUN]
        run = ran[0] if ran else runs[0]
        conclusion = _conclusion(run)
        finished = _run_time(run.get("updated_at"), now)
        when = f" am {finished}" if finished else ""
        link = str(run.get("html_url") or "")
        if conclusion == "success":
            oks.append(f"{label} zuletzt sauber{when}")
        elif conclusion in _NOT_RUN:
            notes.append(f"{label}: letzter Lauf {conclusion}{when}")
        else:
            text = f"{label} gescheitert{when} ({conclusion or 'ohne Ergebnis'})"
            if link:
                text += f" — {link}"
            problems.append(text)
    if problems:
        return Check(name, ok=False, summary=f"FEHLER — {problems[0]}",
                     detail="; ".join(problems[1:] + notes + oks))
    if notes and not oks:
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — " + "; ".join(notes))
    summary = "OK — " + ", ".join(oks)
    if notes:
        return Check(name, ok=True, note=True, summary=summary, detail="; ".join(notes))
    return Check(name, ok=True, summary=summary)


# GitHub Pages builds ``docs/`` from ``main`` in a workflow GitHub creates
# itself ("pages build and deployment"). It has no file in the repository and
# the Actions API takes no path as its id, so the id is looked up by path.
PAGES_WORKFLOW_PATH = "dynamic/pages/pages-build-deployment"
# Runs read per page: some 260 KB of JSON, well under GITHUB_API_MAX_BYTES
# (50 runs were 644 KB on 2026-11-01). 20 runs reached back about eight
# hours at the median in October 2026, but at 10:02 UTC on 2026-11-01,
# with many merges coming in, not to the start of the streak at 07:43 UTC;
# so until every run from the newest down to the newest success has been
# read (cancelled runs take places on a page too), the next page follows,
# up to PAGES_MAX_PAGES, and the alarm can still name the commit that broke
# the build.
PAGES_RUNS_PER_PAGE = 20
PAGES_MAX_PAGES = 3
# The Jekyll step of the build job ("Build with Jekyll"); the step before it,
# "Pull ghcr.io/actions/jekyll-build-pages:…", is GitHub's side. Should GitHub
# rename the step, a content failure counts as GitHub's side and turns red
# with the next run, which fails the same way.
_JEKYLL_STEP_RE = re.compile(r"build\b.*\bjekyll\b", re.IGNORECASE)
# The Jekyll step also fails when GitHub's API fails it: jekyll-github-metadata
# 2.16.1 asks it for every build and lets any error but 403 rate limit, 404
# and a lost connection end the build. Pages runs #20334 (2026-05-23 10:01
# UTC, "The GitHub API credentials you provided aren't valid.") and
# #23181-#23183 (2026-07-16 22:31-23:31 UTC, "GET
# https://api.github.com/repos/…: 503") failed so, and the run after them
# built again. The step's error annotation (the build's output, cut at 4096
# characters; both calls come in its first 2 KB) names it.
_JEKYLL_GITHUB_ERROR_RE = re.compile(
    r"\|\s*Error:\s+(?:[A-Z]+\s+https://api\.github\.com/\S*:\s*\d{3}\b"
    r"|The GitHub API credentials you provided aren't valid)"
)


def _pages_workflow_id() -> int | None:
    """The id of the Pages workflow; ``None`` without Pages."""
    data = _github_api_json("/actions/workflows?per_page=100")
    workflows = data.get("workflows") if isinstance(data, dict) else None
    if not isinstance(workflows, list):
        workflows = []
    ids = [
        wf.get("id")
        for wf in workflows
        if isinstance(wf, dict) and wf.get("path") == PAGES_WORKFLOW_PATH
    ]
    return ids[0] if ids and isinstance(ids[0], int) else None


def _pages_finished_runs() -> list[dict[str, Any]] | None:
    """The finished Pages runs, newest first; ``None`` without Pages.

    Runs GitHub cancelled because a newer commit came, or skipped, do not
    count. While the page was full and the runs read do not hold every run
    number from the newest down to the newest success
    (:func:`_read_down_to_success`), the next page is read, up to
    :data:`PAGES_MAX_PAGES`; a later page that does not answer leaves the
    streak at what was read. A run that two pages both list (it moved down
    when a run finished between the requests) is kept once.
    """
    workflow_id = _pages_workflow_id()
    if workflow_id is None:
        return None
    listed: list[object] = []
    seen: set[int] = set()
    for page in range(1, PAGES_MAX_PAGES + 1):
        path = (
            f"/actions/workflows/{workflow_id}/runs?status=completed"
            f"&per_page={PAGES_RUNS_PER_PAGE}&page={page}"
        )
        try:
            data = _github_api_json(path)
        except Exception:
            if page == 1:
                raise
            break
        batch = data.get("workflow_runs") if isinstance(data, dict) else None
        batch = batch if isinstance(batch, list) else []
        for run in batch:
            run_id = run.get("id") if isinstance(run, dict) else None
            if isinstance(run_id, int):
                if run_id in seen:
                    continue
                seen.add(run_id)
            listed.append(run)
        if len(batch) < PAGES_RUNS_PER_PAGE or _read_down_to_success(_newest_first(listed)):
            break
    return [run for run in _newest_first(listed) if _conclusion(run) not in _NOT_RUN]


def _read_down_to_success(runs: list[dict[str, Any]]) -> bool:
    """Whether *runs* (newest first) hold every run from the newest one down
    to the newest success.

    The API lists by ``created_at``, which can be an hour late (see
    :func:`_newest_first`): on 2026-11-01 a run created at 08:56 UTC stood
    ahead of the runs of 09:00-09:55 UTC, so a success on page 1 does not
    mean that the newer runs were read. Run numbers count up by one, so a
    gap is a run not read yet (or one still running, or deleted, which
    costs a page more and changes nothing).
    """
    for index, run in enumerate(runs):
        if _conclusion(run) == "success":
            numbers = [r.get("run_number") for r in runs[: index + 1]]
            return all(
                isinstance(newer, int) and isinstance(older, int) and newer - older == 1
                for newer, older in zip(numbers, numbers[1:], strict=False)
            )
    return False


def _jekyll_failed_at_github(job: dict[str, Any]) -> bool:
    """Whether the failed Jekyll step of *job* names an error of GitHub's API.

    Reads the job's check-run annotations (``checks: read``); an answer that
    does not come, or no such error, leaves the failure on the content.
    """
    try:
        data = _github_api_json(f"/check-runs/{int(job.get('id') or 0)}/annotations?per_page=50")
    except Exception:
        return False
    return isinstance(data, list) and any(
        isinstance(note, dict)
        and note.get("annotation_level") == "failure"
        and _JEKYLL_GITHUB_ERROR_RE.search(str(note.get("message") or "")) is not None
        for note in data
    )


def _pages_failure_kind(run: dict[str, Any]) -> str:
    """Which part of a failed Pages run failed.

    ``"jekyll"``: the Jekyll step, so the content under ``docs/`` does not
    build, and every later run fails the same way until it is fixed (the
    case of 2026-11-01). ``"api"``: the Jekyll step, but on an error of
    GitHub's API (:data:`_JEKYLL_GITHUB_ERROR_RE`), gone after one to three
    runs so far.
    ``"github"``: another step or job (image pull, upload, deploy), GitHub's
    side; in October 2026 four such failures, each gone with the next run.
    ``""``: the jobs could not be read.
    """
    try:
        data = _github_api_json(f"/actions/runs/{int(run.get('id') or 0)}/jobs?per_page=20")
    except Exception:
        return ""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return ""
    jekyll_jobs = [
        job for job in jobs if isinstance(job, dict)
        and any(
            isinstance(step, dict) and step.get("conclusion") == "failure"
            and _JEKYLL_STEP_RE.match(str(step.get("name") or "")) is not None
            for step in (job.get("steps") or [])
        )
    ]
    if not jekyll_jobs:
        return "github"
    return "api" if all(_jekyll_failed_at_github(job) for job in jekyll_jobs) else "jekyll"


def check_pages_build(now: datetime) -> Check:
    """Red as soon as the latest Pages run failed in its Jekyll build.

    Operator decision (Michael, 2026-11-01, card "Soll ein gescheiterter
    Pages-Build künftig sofort auffallen?", answer "Beides bauen"): on
    2026-11-01 a bare Liquid tag in ``docs/reference/ki_waechter.md`` stopped
    every Pages build from 08:43 Vienna time (22 failed, 4 cancelled until
    11:13 Vienna time); the live feed the TV reads stayed at 08:30 Vienna time
    while the Update runs stayed green, and ``Veröffentlichung`` would have
    turned red only at about 13:00 Vienna time (4 h lag). This check reads the
    latest completed run of "pages build and deployment" from the Actions API
    (``actions: read``), by run number (:func:`_newest_first`), skipping runs
    GitHub cancelled because a newer commit came:

    * ``success``: green.
    * failed in the Jekyll step: red at once. The content does not build,
      and it stays so until someone fixes ``docs/``.
    * failed in the Jekyll step on an error of GitHub's API (its error
      annotation says so, read with ``checks: read``; 2026-05-23 and
      2026-07-16 UTC): GitHub's side, as below. An annotation that cannot be
      read leaves it red at once.
    * failed elsewhere (image pull, upload, deploy): GitHub's side. Red when
      the run before failed too, a single failure is a note (alarm line of
      2026-10-28: a single hiccup sends no mail; the next tick deploys
      again). In October 2026 there were four, each gone with the next run.
    * jobs that cannot be read: red, the failure is real.

    An Actions API that does not answer, no Pages workflow, or no finished
    run is a note. ``Veröffentlichung`` stays as the backstop for an outage
    that shows no failed run.
    """
    name = "Pages-Build"
    try:
        finished = _pages_finished_runs()
    except Exception as exc:
        reason = str(sanitize_log_arg(str(exc)))[:120]
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — Actions-API nicht abgefragt ({type(exc).__name__}: {reason})")
    if finished is None:
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — kein Workflow {PAGES_WORKFLOW_PATH} gefunden")
    if not finished:
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — kein abgeschlossener Pages-Lauf gefunden")
    latest = finished[0]
    ended = _run_time(latest.get("updated_at"), now)
    when = f" am {ended}" if ended else ""
    if _conclusion(latest) == "success":
        return Check(name, ok=True, summary=f"OK — letzter Lauf sauber{when}")

    streak = 0
    for run in finished:
        if _conclusion(run) == "success":
            break
        streak += 1
    kind = _pages_failure_kind(latest)
    what = {
        "jekyll": "Jekyll-Build gescheitert",
        "api": "Jekyll-Build an GitHubs API gescheitert",
        "github": "Auslieferung durch GitHub gescheitert",
    }.get(kind, "Pages-Lauf gescheitert")
    text = f"{what}{when}"
    # The first failed run of the streak names the commit that broke the
    # build (on 2026-11-01 the merge of #2092); later ones are only ticks.
    # When every run read failed, the start lies before them.
    first_failed: dict[str, Any] | None = finished[streak - 1]
    if streak > 1 and streak == len(finished):
        text += f", mindestens {streak} Läufe in Folge"
        first_failed = None
    elif streak > 1:
        started = _run_time(finished[streak - 1].get("created_at"), now)
        since = f" seit {started}" if started else ""
        text += f", {streak} Läufe in Folge{since}"
    details: list[str] = []
    commit = first_failed.get("head_commit") if first_failed is not None else None
    message = commit.get("message") if isinstance(commit, dict) else None
    if isinstance(message, str) and message.strip():
        first_line = str(sanitize_log_arg(message.strip().splitlines()[0]))[:80]
        details.append(f"zuerst nach Commit „{first_line}“" if streak > 1 else f"Commit „{first_line}“")
    link = str(latest.get("html_url") or "")
    if link:
        details.append(link)
    detail = " — ".join(details)
    if kind == "github" and streak == 1:
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — {text}; der nächste Lauf liefert neu aus",
                     detail=detail)
    if kind == "api" and streak == 1:
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — {text}; der nächste Lauf baut neu",
                     detail=detail)
    if kind == "jekyll":
        text += " (Inhalt unter docs/ prüfen)"
    return Check(name, ok=False,
                 summary=f"FEHLER — {text}; GitHub Pages liefert den alten Stand aus",
                 detail=detail)
