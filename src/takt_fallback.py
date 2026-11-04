"""Takt-Fallback: start an Update cycle when IFTTT has not ticked for 40 minutes.

IFTTT carries the 30-minute cadence of the feed (``repository_dispatch``),
because GitHub's own cron starts late and skips most slots here (backup cron
2026-10-05 to 2026-11-04: 142 runs, median 5.1 h apart). The fallback needs no
personal access token. It uses the run's own ``GITHUB_TOKEN`` with ``actions:
write``, which may start ``workflow_dispatch`` runs.

Operator decision Michael 2026-11-04 ("Alle 40 Minuten klingt nicht
schlecht. Sorge aber bitte dafür, dass der Fallback wirklich nur läuft, wenn
IFTTT nicht funktioniert hat. Wenn IFTTT nicht funktioniert erstelle einen
Issue. Wenn der Fallback auch nicht funktioniert, und der Feed nicht mehr
aktualisiert werden kann, mach den Health Check rot"), and the same day:
"Effizienz ist mir wichtig. Das Projekt soll ressourcenschonend arbeiten."
So no runner waits:

* Every Update cycle run starts one check (``takt-fallback.yml``,
  ``workflow_run`` ``requested``). GitHub holds the check back for 40 minutes
  with the wait timer of the environment ``takt-wartezeit``; no runner runs
  and the wait is not billed. The check of the next run replaces the waiting
  one (``concurrency``, ``cancel-in-progress``), so while IFTTT ticks no check
  ever reaches a runner.
* A check that runs (:func:`run_check`) found no Update cycle run for 40
  minutes (:data:`FALLBACK_AFTER`: IFTTT's 30 minutes plus 10 minutes drift).
  It starts one (``workflow_dispatch`` of ``update-cycle.yml``), opens one
  issue (label :data:`ISSUE_LABEL`) and starts its successor, so the next
  check follows 40 minutes later while the silence lasts.
* The next IFTTT run closes the issue (:func:`close_issue_when_ticking`, a
  step of ``update-cycle.yml``).
* A fallback run that failed or never appeared starts the Health check, whose
  check ``Takt`` (``src/health_check/takt.py``) turns red when IFTTT is
  silent and the feed has had no new build for 80 minutes. Runs started by
  the bot mail nobody, so the check also comments on the issue, once when the
  fallback fails and once when it runs again (:func:`report_fallback_state`).
* A check that runs much earlier than 40 minutes means the wait timer is
  missing: it fails loudly (:data:`MIN_WAIT`).
* The backup schedule (own lane, so it never replaces a waiting check)
  restarts the watch when no check is waiting.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger("takt_fallback")

UPDATE_WORKFLOW = "update-cycle.yml"
HEALTH_WORKFLOW = "health-check.yml"
SELF_WORKFLOW = "takt-fallback.yml"
# Login of the ``GITHUB_TOKEN`` that starts a run: a ``workflow_dispatch``
# run of the Update cycle with this triggering actor is a fallback run (no
# other workflow starts the Update cycle).
BOT_LOGIN = "github-actions[bot]"
IFTTT_EVENT = "repository_dispatch"

FALLBACK_AFTER = timedelta(minutes=40)
# A check that runs this soon after its run was created has not waited: the
# environment ``takt-wartezeit`` lacks its 40-minute wait timer.
MIN_WAIT = timedelta(minutes=35)
# Two checks 40 minutes apart without a new run in between: the started
# fallback run never appeared.
LOST_START = timedelta(minutes=75)
WAITING_STATES = frozenset({"queued", "waiting", "pending", "requested", "in_progress"})
# GitHub listed runs created on 2026-11-01 between 08:00 and 08:59 UTC with
# times one hour late; a creation time after "now" is read as "now".
FUTURE_SLACK = timedelta(minutes=5)

ISSUE_LABEL = "ifttt-ausfall"
ISSUE_TITLE = "IFTTT-Takt ausgefallen: Fallback hält den Feed aktuell"
RUNS_PER_PAGE = 30
# Hidden marks of the two comments on the issue when the fallback fails and
# when it runs again: the newest mark tells the next check which state was
# last reported, so each change mails once (Michael 2026-11-04, card "Ja,
# Kommentar im Issue": runs started by the bot mail nobody when they fail).
FAIL_MARK = "<!-- takt-fallback: gescheitert -->"
OK_MARK = "<!-- takt-fallback: laeuft wieder -->"

try:
    _VIENNA: ZoneInfo | None = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover - tzdata missing
    _VIENNA = None


def _vienna(moment: datetime) -> str:
    local = moment.astimezone(_VIENNA) if _VIENNA is not None else moment
    return local.strftime("%d.%m.%Y %H:%M")


@dataclass(frozen=True)
class Run:
    """The parts of an Update cycle run the check decides on."""

    number: int
    run_id: int
    event: str
    actor: str
    created: datetime
    status: str
    conclusion: str

    @property
    def is_fallback(self) -> bool:
        return self.event == "workflow_dispatch" and self.actor == BOT_LOGIN

    @property
    def is_ifttt(self) -> bool:
        return self.event == IFTTT_EVENT


def _parse_time(raw: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def parse_runs(payload: object) -> list[Run]:
    """Runs from an Actions API answer, newest first by run number.

    The run number counts up with every run and depends on no clock (see
    ``src/health_check/github_runs.py``, ``_newest_first``).
    """
    raw = payload.get("workflow_runs") if isinstance(payload, Mapping) else None
    runs: list[Run] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        number, run_id = entry.get("run_number"), entry.get("id")
        created = _parse_time(entry.get("created_at"))
        actor = entry.get("triggering_actor")
        login = actor.get("login") if isinstance(actor, Mapping) else ""
        if not isinstance(number, int) or not isinstance(run_id, int) or created is None:
            continue
        runs.append(Run(number, run_id, str(entry.get("event") or ""), str(login or ""),
                        created, str(entry.get("status") or ""), str(entry.get("conclusion") or "")))
    return sorted(runs, key=lambda run: (run.number, run.run_id), reverse=True)


def tick_time(run: Run, now: datetime) -> datetime:
    """When *run* was created, never later than *now* (see :data:`FUTURE_SLACK`)."""
    return run.created if run.created <= now + FUTURE_SLACK else now


def newest_ifttt(runs: Iterable[Run]) -> Run | None:
    return next((run for run in runs if run.is_ifttt), None)


def newest_foreign_number(runs: Iterable[Run]) -> int:
    """The highest run number of a run the fallback did not start (0 if none)."""
    return max((run.number for run in runs if not run.is_fallback), default=0)


@dataclass(frozen=True)
class Decision:
    fire: bool
    reason: str
    ifttt_silent: bool


def decide(runs: list[Run], now: datetime, last_dispatch: datetime | None) -> Decision:
    """Whether the fallback starts an Update cycle now.

    Only when no Update cycle run of any kind was created within
    :data:`FALLBACK_AFTER` and no start of its own (*last_dispatch*, for a
    caller that remembers one the API does not list yet) lies within that time.
    """
    ifttt = newest_ifttt(runs)
    ifttt_silent = ifttt is None or now - tick_time(ifttt, now) >= FALLBACK_AFTER
    moments = [tick_time(run, now) for run in runs[:1]]
    if last_dispatch is not None:
        moments.append(last_dispatch)
    if not moments:
        return Decision(True, "kein Update-Lauf bekannt", ifttt_silent)
    last = max(moments)
    quiet = now - last
    if quiet < FALLBACK_AFTER:
        return Decision(False, f"letzter Update-Lauf {_vienna(last)} Wien", ifttt_silent)
    if not ifttt_silent:  # pragma: no cover - the newest run is at least as new as IFTTT's
        return Decision(False, "IFTTT tickt", ifttt_silent)
    minutes = int(quiet.total_seconds() // 60)
    return Decision(True, f"seit {minutes} Minuten kein Update-Lauf (letzter {_vienna(last)} Wien)",
                    ifttt_silent)


def issue_body(ifttt: Run | None, now: datetime) -> str:
    since = _vienna(ifttt.created) + " Wien" if ifttt is not None else "unbekannt"
    return (
        f"Seit {since} ist kein IFTTT-Tick (`repository_dispatch`) mehr angekommen; "
        f"festgestellt {_vienna(now)} Wien.\n\n"
        "Der Takt-Fallback (`.github/workflows/takt-fallback.yml`) startet den Update-Lauf "
        "jetzt alle 40 Minuten selbst, der Feed bleibt aktuell. Gelingt auch das nicht, "
        "wird der Health check rot (Prüfung „Takt“).\n\n"
        "Zu prüfen: Ist das IFTTT-Applet aktiv, und ist das GitHub-Token im Applet noch "
        "gültig (Ablaufdatum unter GitHub → Settings → Developer settings → Personal "
        "access tokens)?\n\n"
        "Dieses Issue schließt sich selbst, sobald IFTTT wieder tickt."
    )


class ActionsClient:
    """The few GitHub REST calls of the check, through the project's safe HTTP stack."""

    def __init__(self, repository: str, token: str, api_url: str, ref: str = "main") -> None:
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.ref = ref

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        from src.utils.files import _reject_non_finite_constant, _reject_non_finite_float
        from src.utils.http import request_safe, session_with_retries, validate_http_url

        url = f"{self.api_url}/repos/{self.repository}{path}"
        if not validate_http_url(url.split("?", 1)[0]):
            raise ValueError("unsafe GitHub API URL")
        with session_with_retries(
            user_agent="wien-oepnv-takt-fallback",
            # POST is not retried: a start GitHub carried out but answered
            # with a 5xx would start the Update cycle twice.
            allowed_methods=("GET", "PATCH"),
            raise_on_status=False,
        ) as session:
            session.headers.update(
                {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            kwargs: dict[str, Any] = {"json": dict(payload)} if payload is not None else {}
            response = request_safe(session, url, method=method, timeout=15, raise_for_status=False, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path.split('?', 1)[0]}: HTTP {response.status_code}")
        if not response.content:
            return None
        return response.json(parse_constant=_reject_non_finite_constant,
                             parse_float=_reject_non_finite_float)

    def update_runs(self) -> list[Run]:
        return parse_runs(self._request(
            "GET", f"/actions/workflows/{UPDATE_WORKFLOW}/runs?branch={self.ref}&per_page={RUNS_PER_PAGE}"))

    def run_created(self, run_id: int) -> datetime | None:
        data = self._request("GET", f"/actions/runs/{run_id}")
        return _parse_time(data.get("created_at")) if isinstance(data, Mapping) else None

    def other_check_waiting(self, run_id: int) -> bool:
        """Whether another run of the Takt-Fallback is queued, waiting or running."""
        data = self._request("GET", f"/actions/workflows/{SELF_WORKFLOW}/runs?per_page=10")
        raw = data.get("workflow_runs") if isinstance(data, Mapping) else None
        return any(isinstance(entry, Mapping) and entry.get("id") != run_id
                   and entry.get("status") in WAITING_STATES
                   for entry in (raw if isinstance(raw, list) else []))

    def dispatch(self, workflow: str) -> None:
        self._request("POST", f"/actions/workflows/{workflow}/dispatches", {"ref": self.ref})

    def open_issues(self) -> list[int]:
        data = self._request("GET", f"/issues?state=open&labels={ISSUE_LABEL}&per_page=10")
        return [entry["number"] for entry in data if isinstance(entry, Mapping)
                and isinstance(entry.get("number"), int) and "pull_request" not in entry] \
            if isinstance(data, list) else []

    def issue_comments(self, number: int) -> list[str]:
        """The bodies of the newest comments on issue *number*, oldest first."""
        data = self._request("GET", f"/issues/{number}/comments?per_page=100")
        return [str(entry.get("body") or "") for entry in data if isinstance(entry, Mapping)] \
            if isinstance(data, list) else []

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{number}/comments", {"body": body})

    def create_issue(self, title: str, body: str) -> None:
        self._request("POST", "/issues", {"title": title, "body": body, "labels": [ISSUE_LABEL]})

    def close_issue(self, number: int, comment: str) -> None:
        self._request("POST", f"/issues/{number}/comments", {"body": comment})
        self._request("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"})


def client_from_env() -> ActionsClient:
    from src.feed.reporting import _is_trusted_github_api, _is_valid_github_repo_slug
    from src.utils.env import read_secret

    repository = os.getenv("GITHUB_REPOSITORY") or ""
    token = read_secret("GITHUB_TOKEN") or ""
    api_url = os.getenv("GITHUB_API_URL") or "https://api.github.com"
    ref = os.getenv("TAKT_FALLBACK_REF") or "main"
    if not repository or not token:
        raise RuntimeError("GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")
    if not _is_valid_github_repo_slug(repository) or not _is_trusted_github_api(api_url):
        raise RuntimeError("Repository oder API-URL nicht vertrauenswürdig")
    return ActionsClient(repository, token, api_url, ref)


def sync_issue(client: ActionsClient, runs: list[Run], now: datetime, *, open_new: bool) -> None:
    """Close the issue once IFTTT ticks again; open one when *open_new* and none is open."""
    ifttt = newest_ifttt(runs)
    silent = ifttt is None or now - tick_time(ifttt, now) >= FALLBACK_AFTER
    numbers = client.open_issues()
    if not silent and numbers and ifttt is not None:
        fallbacks = sum(1 for run in runs if run.is_fallback)
        for number in numbers:
            client.close_issue(number, f"IFTTT tickt wieder (Tick {_vienna(ifttt.created)} Wien). "
                                       f"Fallback-Läufe in den letzten {len(runs)} Update-Läufen: {fallbacks}.")
            print(f"Issue #{number} geschlossen: IFTTT tickt wieder.")
    elif silent and open_new and not numbers:
        client.create_issue(ISSUE_TITLE, issue_body(ifttt, now))
        print("Issue geöffnet: IFTTT tickt nicht.")


def _safe(label: str, action: Callable[[], object]) -> object:
    try:
        return action()
    except Exception as exc:  # noqa: BLE001 - a GitHub hiccup must not end the watch
        print(f"::warning title=Takt-Fallback::{label}: {type(exc).__name__}: {str(exc)[:200]}")
        return None


def _fallback_failure(runs: list[Run], now: datetime, decision: Decision) -> str | None:
    """Why the fallback failed (its last run failed, or its start never appeared), or None."""
    newest = runs[0] if runs else None
    if newest is not None and newest.is_fallback and newest.status == "completed" \
            and newest.conclusion != "success":
        return f"Fallback-Lauf #{newest.number} endete mit {newest.conclusion or 'ohne Ergebnis'}"
    if decision.fire and newest is not None and now - tick_time(newest, now) >= LOST_START:
        return "seit zwei Prüfungen kein Update-Lauf, der gestartete Lauf erscheint nicht"
    return None


def _start_health_check_if_fallback_failed(client: ActionsClient, runs: list[Run], now: datetime,
                                           decision: Decision) -> str | None:
    failure = _fallback_failure(runs, now, decision)
    if failure is not None:
        print(f"::warning title=Takt-Fallback::{failure}")
        _safe("Health check starten", lambda: client.dispatch(HEALTH_WORKFLOW))
    return failure


def report_fallback_state(client: ActionsClient, runs: list[Run], now: datetime, failure: str | None) -> None:
    """Comment on the issue when the fallback fails and again when it runs, once per change.

    A Health check started by a run of the bot mails nobody when it turns red
    (GitHub mails the person who started a run), so the comment carries the
    news by the issue's mail (Michael 2026-11-04, card "Ja, Kommentar im Issue").
    """
    newest = runs[0] if runs else None
    recovered = newest is not None and newest.is_fallback and newest.status == "completed" \
        and newest.conclusion == "success"
    if failure is None and not recovered:
        return
    numbers = client.open_issues()
    if not numbers:
        if failure is None:
            return
        sync_issue(client, runs, now, open_new=True)
        numbers = client.open_issues()
        if not numbers:
            return
    number = numbers[0]
    marks = [body for body in client.issue_comments(number) if FAIL_MARK in body or OK_MARK in body]
    failing_reported = bool(marks) and FAIL_MARK in marks[-1]
    if failure is not None and not failing_reported:
        client.comment(number, f"{FAIL_MARK}\nAuch der Takt-Fallback scheitert ({failure}; "
                               f"festgestellt {_vienna(now)} Wien). Der Feed wird nicht mehr "
                               "aktualisiert, der Health check wird rot (Prüfung „Takt“).\n\n"
                               "Zu prüfen: die Läufe von `takt-fallback.yml` und `update-cycle.yml`.")
        print(f"Issue #{number}: Kommentar, der Fallback scheitert.")
    elif failure is None and failing_reported:
        client.comment(number, f"{OK_MARK}\nDer Takt-Fallback läuft wieder (Lauf #{newest.number if newest else '?'} "
                               f"sauber, festgestellt {_vienna(now)} Wien). Das Issue bleibt offen, "
                               "bis IFTTT wieder tickt.")
        print(f"Issue #{number}: Kommentar, der Fallback läuft wieder.")


def run_check(client: ActionsClient, *, run_id: int, now: datetime, scheduled: bool = False) -> int:
    """One check after the 40-minute wait; start the Update cycle when IFTTT is silent.

    *scheduled*: the backup schedule, which only acts when no other check is
    waiting (the watch has lapsed).
    """
    if scheduled:
        if client.other_check_waiting(run_id):
            print("Eine Prüfung wartet bereits: nichts zu tun.")
            return 0
    else:
        created = client.run_created(run_id)
        if created is not None and now - created < MIN_WAIT:
            print("::error title=Takt-Fallback::Die Prüfung hat nicht gewartet. Die Umgebung "
                  "takt-wartezeit braucht einen Wait timer von 40 Minuten "
                  "(Settings → Environments → takt-wartezeit).")
            return 1
    runs = client.update_runs()
    decision = decide(runs, now, None)
    failure = _start_health_check_if_fallback_failed(client, runs, now, decision)
    _safe("Issue-Kommentar", lambda: report_fallback_state(client, runs, now, failure))
    if decision.fire:
        print(f"Fallback: {decision.reason}; starte den Update-Lauf.")
        silent = decision.ifttt_silent
        # Issue first: the check of the started run may cancel this one at once.
        _safe("Issue abgleichen", lambda: sync_issue(client, runs, now, open_new=silent))
        _safe("Update-Lauf starten", lambda: client.dispatch(UPDATE_WORKFLOW))
    else:
        print(f"Kein Fallback: {decision.reason}.")
    # The next check 40 minutes from now, should no new Update cycle run start
    # one (its check replaces this one).
    _safe("Nächste Prüfung starten", lambda: client.dispatch(SELF_WORKFLOW))
    return 0


def close_issue_when_ticking(client: ActionsClient, now: datetime) -> int:
    """Called by an IFTTT run of the Update cycle: close the issue once IFTTT ticks again."""
    if client.open_issues():
        sync_issue(client, client.update_runs(), now, open_new=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schedule", action="store_true", help="Backup-Zeitplan: nur ohne wartende Prüfung")
    parser.add_argument("--close-issue", action="store_true", help="Issue schließen, wenn IFTTT tickt")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    client = client_from_env()
    now = datetime.now(UTC)
    if args.close_issue:
        _safe("Issue abgleichen", lambda: close_issue_when_ticking(client, now))
        return 0
    run_id = int(os.getenv("GITHUB_RUN_ID") or "0")
    return run_check(client, run_id=run_id, now=now, scheduled=args.schedule)
