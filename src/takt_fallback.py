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
aktualisiert werden kann, mach den Health Check rot"):

* Every Update cycle run starts one waiter (``takt-fallback.yml``,
  ``workflow_run`` ``requested``). The waiter polls the Actions API once a
  minute. A newer Update cycle run that the fallback did not start (IFTTT,
  backup cron, a manual run) ends it at once: that run's own waiter takes
  over, so the fallback never acts while IFTTT ticks.
* Only when no Update cycle run at all was created for
  :data:`FALLBACK_AFTER` (40 minutes: IFTTT's 30 minutes plus 10 minutes
  drift) does it start one (``workflow_dispatch`` of ``update-cycle.yml``),
  and again every 40 minutes while the silence lasts.
* With its first start it opens one issue (label :data:`ISSUE_LABEL`); the
  waiter of the next IFTTT run closes it.
* A fallback run that fails or never appears starts the Health check, whose
  check ``Takt`` (``src/health_check/takt.py``) turns red when IFTTT is
  silent and the feed has had no new build for 80 minutes.
"""

from __future__ import annotations

import logging
import os
import time
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
# A started fallback run that has not shown up in the API after this long
# counts as failed (the next try comes 40 minutes after the start).
DISPATCH_GRACE = timedelta(minutes=10)
POLL_SECONDS = 60
# A waiter ends well before GitHub's 6-hour job limit and starts its
# successor, so the watch never lapses during a long outage.
WAITER_LIFETIME = timedelta(minutes=330)
# GitHub listed runs created on 2026-11-01 between 08:00 and 08:59 UTC with
# times one hour late; a creation time after "now" is read as "now".
FUTURE_SLACK = timedelta(minutes=5)

ISSUE_LABEL = "ifttt-ausfall"
ISSUE_TITLE = "IFTTT-Takt ausgefallen: Fallback hält den Feed aktuell"
RUNS_PER_PAGE = 30

try:
    _VIENNA: ZoneInfo | None = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover - tzdata missing
    _VIENNA = None


def _vienna(moment: datetime) -> str:
    local = moment.astimezone(_VIENNA) if _VIENNA is not None else moment
    return local.strftime("%d.%m.%Y %H:%M")


@dataclass(frozen=True)
class Run:
    """The parts of an Update cycle run the waiter decides on."""

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
    :data:`FALLBACK_AFTER` and the waiter has not itself started one within
    that time. A run the API does not list yet cannot hide a start: the
    waiter remembers its own last start (*last_dispatch*).
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
    """The few GitHub REST calls of the waiter, through the project's safe HTTP stack."""

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

    def dispatch(self, workflow: str) -> None:
        self._request("POST", f"/actions/workflows/{workflow}/dispatches", {"ref": self.ref})

    def open_issues(self) -> list[int]:
        data = self._request("GET", f"/issues?state=open&labels={ISSUE_LABEL}&per_page=10")
        return [entry["number"] for entry in data if isinstance(entry, Mapping)
                and isinstance(entry.get("number"), int) and "pull_request" not in entry] \
            if isinstance(data, list) else []

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


@dataclass
class _Waiter:
    """State of one waiter between two polls."""

    client: ActionsClient
    baseline: int
    last_dispatch: datetime | None = None
    watched: int | None = None  # number of the fallback run already judged, -1: none appeared

    def _follow_own_run(self, runs: list[Run], now: datetime) -> None:
        """Start the Health check when the started fallback run failed or never appeared."""
        if self.last_dispatch is None:
            return
        since = self.last_dispatch - timedelta(minutes=1)
        own = next((run for run in runs if run.is_fallback and run.created >= since), None)
        if own is not None and own.number != self.watched and own.status == "completed":
            self.watched = own.number
            if own.conclusion != "success":
                print(f"::warning title=Takt-Fallback::Fallback-Lauf #{own.number}: {own.conclusion}")
                _safe("Health check starten", lambda: self.client.dispatch(HEALTH_WORKFLOW))
        elif own is None and self.watched != -1 and now - self.last_dispatch >= DISPATCH_GRACE:
            self.watched = -1
            print("::warning title=Takt-Fallback::Der gestartete Update-Lauf erscheint nicht.")
            _safe("Health check starten", lambda: self.client.dispatch(HEALTH_WORKFLOW))

    def step(self, now: datetime) -> bool:
        """One poll; ``True`` when a newer run that the fallback did not start ends the waiter."""
        fresh = _safe("Läufe lesen", self.client.update_runs)
        if not isinstance(fresh, list):
            return False
        runs: list[Run] = fresh
        if newest_foreign_number(runs) > self.baseline:
            print("Neuer Update-Lauf ohne Fallback: sein Wächter übernimmt.")
            return True
        self._follow_own_run(runs, now)
        decision = decide(runs, now, self.last_dispatch)
        if decision.fire:
            print(f"Fallback: {decision.reason}; starte den Update-Lauf.")
            _safe("Update-Lauf starten", lambda: self.client.dispatch(UPDATE_WORKFLOW))
            self.last_dispatch, self.watched = now, None
            silent = decision.ifttt_silent
            _safe("Issue abgleichen", lambda: sync_issue(self.client, runs, now, open_new=silent))
        return False


def watch(client: ActionsClient, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
          sleep: Callable[[float], None] = time.sleep) -> int:
    """Wait for the next Update cycle tick; start one when IFTTT stays silent."""
    started = clock()
    runs = client.update_runs()
    waiter = _Waiter(client, newest_foreign_number(runs))
    _safe("Issue abgleichen", lambda: sync_issue(client, runs, started, open_new=False))
    print(f"Wartet auf den nächsten Tick nach Lauf #{waiter.baseline} (Fallback nach {FALLBACK_AFTER}).")
    while clock() - started < WAITER_LIFETIME:
        if waiter.step(clock()):
            return 0
        sleep(POLL_SECONDS)
    print("Lebensdauer erreicht: startet den nächsten Takt-Fallback.")
    _safe("Nachfolger starten", lambda: client.dispatch(SELF_WORKFLOW))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    return watch(client_from_env())
