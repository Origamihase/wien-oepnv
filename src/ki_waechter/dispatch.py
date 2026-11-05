"""Start the daily KI-Wächter from the Update cycle when it is due.

Until 2026-11-05 ``ki-waechter.yml`` hung off ``workflow_run`` of the Update
cycle: on every tick, 48 times a day, a runner started for its job ``gate``,
read the clock and the record ``data/side_steps/ki-waechter.json`` through
the API and ended with "no audit" on all but one to four ticks (6 to 30 s
each). Now the last step of a successful Update cycle on ``main`` decides
(``scripts/ki_waechter.py dispatch``) and sends ``repository_dispatch`` with
the type :data:`EVENT_TYPE` only when an audit is due; the watcher's gate
runs only then. Michael 2026-11-04: "Effizienz ist mir wichtig. Das Projekt
soll ressourcenschonend arbeiten."

The decision (:func:`not_due`; the former gate's, with the later hour):

* from :data:`START_HOUR` Vienna time (15:00 since 2026-11-05, the reasons
  at the constant; until then 9:00, right after the free-tier day of the
  Gemini API begins at 0:00 Pacific time);
* not when today's audit succeeded (``last_success`` of the record);
* not within :data:`RETRY_HOURS` of a failed try of today (``at``);
* not while a run of the watcher on ``main`` is queued, waiting or running:
  the run of the previous tick may still be publishing its record. Before,
  the second run waited in the concurrency group and read the record again.

Until later on 2026-11-05 a fifth rule held the rest of the day after a run
that the watcher's gate had ended without an audit because the day's budget
of Gemini requests was spent. Nothing counts a budget any more: Google does
(``scripts/ki_waechter.py``, Michael 2026-11-05: "Zähle es bitte so wie
Google. Wir brauchen keine eigene Zähltechnik."), and the jobs of a run are
not read.

The record is read from the checkout first; only when that says "due" the
copy on ``main`` is read through the API, because the audit of the previous
tick may have published since this tick's checkout. A tick before
:data:`START_HOUR` or after today's success therefore costs no request; a
tick that dispatches costs three (record, runs, dispatch).
"""

from __future__ import annotations

import argparse
import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.ki_waechter.inputs import VIENNA
from src.utils.files import read_capped_json

WORKFLOW = "ki-waechter.yml"
# The ``repository_dispatch`` type ``ki-waechter.yml`` listens to.
EVENT_TYPE = "ki-waechter"
STEP = "KI-Wächter"
RECORD = Path("data/side_steps/ki-waechter.json")
# First Vienna hour of the day in which the audit runs (env
# ``KI_WAECHTER_START_HOUR`` in update-cycle.yml). From 31.10. to 05.11.2026
# it was 9, right after the free-tier day of the Gemini API begins (0:00
# Pacific time, 9:00 Vienna; 8:00 in the weeks between the European and the
# US clock change), so that the audit ran first thing in a fresh quota day
# (Michael 2026-10-31, after Gemini 3.8 Flash answered 503 all afternoon).
# At that hour everyone's quota returns, and on five mornings in a row both
# Flash models answered 503 while the try at 11:00 succeeded on three of
# four days (Michael 2026-11-05: "Wenn jeder wieder darf, ist es klar, dass
# die API eine höhere Last hat ... wir sollten später abfragen. Wie wärs mit
# 15:00 Uhr?"). Vienna time: 15:00 is 6:00 on the US west coast.
START_HOUR = 15
# Hours between a failed audit and the next try on the same day
# (``KI_WAECHTER_RETRY_HOURS``; ki-waechter.yml names the same number in its
# warning).
RETRY_HOURS = 2
ACTIVE_STATES = frozenset({"queued", "waiting", "pending", "requested", "in_progress"})
# Runs of the watcher on main read per decision: a day has one to four
# audits, a few refusals and the odd manual run.
RUNS_PER_PAGE = 20
# The record is a few hundred bytes; the contents API answers with base64.
MAX_RECORD_BYTES = 64 * 1024


@dataclass(frozen=True)
class Record:
    """The two times of the step ``KI-Wächter`` in ``data/side_steps/ki-waechter.json``."""

    at: datetime | None = None
    last_success: datetime | None = None


def _parse_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def parse_record(payload: object) -> Record:
    """The record from the JSON of ``data/side_steps/ki-waechter.json`` (missing parts are None)."""
    steps = payload.get("steps") if isinstance(payload, Mapping) else None
    entry = steps.get(STEP) if isinstance(steps, Mapping) else None
    if not isinstance(entry, Mapping):
        return Record()
    return Record(_parse_time(entry.get("at")), _parse_time(entry.get("last_success")))


def read_record(path: Path) -> Record:
    """The record in the checkout; an unreadable or missing file counts as no record."""
    try:
        return parse_record(read_capped_json(path, label="ki_waechter"))
    except (OSError, ValueError):
        return Record()


def _vienna(moment: datetime) -> str:
    return moment.astimezone(VIENNA).strftime("%d.%m.%Y %H:%M")


def not_due(record: Record, now: datetime, *, start_hour: int = START_HOUR,
            retry_hours: float = RETRY_HOURS) -> str:
    """Why no audit is due at *now* (Vienna time decides the day), or "" when one is."""
    local = now.astimezone(VIENNA)
    if local.hour < start_hour:
        return f"{local:%H:%M} Uhr Wien, die Prüfung beginnt um {start_hour}:00 Uhr"
    today = local.date()
    if record.last_success is not None and record.last_success.astimezone(VIENNA).date() == today:
        return f"die heutige Prüfung ist gelungen ({_vienna(record.last_success)} Wien)"
    if (record.at is not None and record.at.astimezone(VIENNA).date() == today
            and now - record.at < timedelta(hours=retry_hours)):
        return (f"die heutige Prüfung scheiterte um {_vienna(record.at)} Wien, "
                f"nächster Versuch {retry_hours:g} Stunden später")
    return ""


class WatcherClient:
    """The three GitHub REST calls of the decision, through the project's safe HTTP stack."""

    def __init__(self, repository: str, token: str, api_url: str) -> None:
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        from src.utils.files import _reject_non_finite_constant, _reject_non_finite_float
        from src.utils.http import request_safe, session_with_retries, validate_http_url

        url = f"{self.api_url}/repos/{self.repository}{path}"
        if not validate_http_url(url.split("?", 1)[0]):
            raise ValueError("unsafe GitHub API URL")
        # POST is not retried: a dispatch GitHub carried out but answered
        # with a 5xx would start the audit twice.
        with session_with_retries(user_agent="wien-oepnv-ki-waechter", allowed_methods=("GET",),
                                  raise_on_status=False) as session:
            session.headers.update({
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            })
            kwargs: dict[str, Any] = {"json": dict(payload)} if payload is not None else {}
            response = request_safe(session, url, method=method, timeout=15, raise_for_status=False, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path.split('?', 1)[0]}: HTTP {response.status_code}")
        if not response.content:
            return None
        return response.json(parse_constant=_reject_non_finite_constant, parse_float=_reject_non_finite_float)

    def record_on_main(self) -> Record:
        """The record as ``main`` holds it now (the checkout may be a minute old)."""
        data = self._request("GET", f"/contents/{RECORD.as_posix()}?ref=main")
        content = data.get("content") if isinstance(data, Mapping) else None
        if not isinstance(content, str):
            raise RuntimeError("Aufzeichnung auf main nicht lesbar")
        raw = base64.b64decode(content, validate=False)
        if len(raw) > MAX_RECORD_BYTES:
            raise RuntimeError("Aufzeichnung auf main zu groß")
        from src.utils.files import loads_finite

        return parse_record(loads_finite(raw))

    def runs_on_main(self) -> list[Mapping[str, Any]]:
        """The newest runs of the watcher on ``main``, as the API lists them."""
        data = self._request("GET", f"/actions/workflows/{WORKFLOW}/runs?branch=main&per_page={RUNS_PER_PAGE}")
        raw = data.get("workflow_runs") if isinstance(data, Mapping) else None
        return [entry for entry in (raw if isinstance(raw, list) else []) if isinstance(entry, Mapping)]

    def dispatch(self, update_run: str) -> None:
        self._request("POST", "/dispatches", {"event_type": EVENT_TYPE, "client_payload": {"update_run": update_run}})


def client_from_env() -> WatcherClient:
    from src.feed.github_issue import _is_trusted_github_api, _is_valid_github_repo_slug
    from src.utils.env import read_secret

    repository = os.getenv("GITHUB_REPOSITORY") or ""
    token = read_secret("GITHUB_TOKEN") or ""
    api_url = os.getenv("GITHUB_API_URL") or "https://api.github.com"
    if not repository or not token:
        raise RuntimeError("GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")
    if not _is_valid_github_repo_slug(repository) or not _is_trusted_github_api(api_url):
        raise RuntimeError("Repository oder API-URL nicht vertrauenswürdig")
    return WatcherClient(repository, token, api_url)


def _hours_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if 0 <= value <= 48 else default


def _number(entry: Mapping[str, Any]) -> int:
    number = entry.get("run_number")
    return number if isinstance(number, int) else 0


def decide_and_dispatch(client: WatcherClient, local: Record, now: datetime, update_run: str, *,
                        start_hour: int = START_HOUR, retry_hours: float = RETRY_HOURS) -> str:
    """Dispatch the audit when it is due; returns the one line that says what happened."""
    reason = not_due(local, now, start_hour=start_hour, retry_hours=retry_hours)
    if reason:
        return f"Kein KI-Wächter: {reason}."
    fresh = client.record_on_main()
    reason = not_due(fresh, now, start_hour=start_hour, retry_hours=retry_hours)
    if reason:
        return f"Kein KI-Wächter: {reason} (Aufzeichnung auf main, neuer als der Checkout)."
    runs = client.runs_on_main()
    active = next((entry for entry in runs if entry.get("status") in ACTIVE_STATES), None)
    if active is not None:
        return f"Kein KI-Wächter: Lauf #{_number(active)} des Wächters läuft oder wartet noch."
    client.dispatch(update_run)
    return f"KI-Wächter gestartet (repository_dispatch {EVENT_TYPE}, {_vienna(now)} Wien)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", default=str(RECORD), help="Aufzeichnung im Checkout")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    start_hour = int(_hours_env("KI_WAECHTER_START_HOUR", START_HOUR))
    retry_hours = _hours_env("KI_WAECHTER_RETRY_HOURS", RETRY_HOURS)
    local = read_record(Path(args.record))
    server = os.getenv("GITHUB_SERVER_URL") or "https://github.com"
    update_run = f"{server}/{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}"
    try:
        # The checkout decides first, without a request; the client is built
        # only for a tick on which an audit may be due.
        reason = not_due(local, now, start_hour=start_hour, retry_hours=retry_hours)
        if reason:
            print(f"Kein KI-Wächter: {reason}.")
            return 0
        print(decide_and_dispatch(client_from_env(), local, now, update_run,
                                  start_hour=start_hour, retry_hours=retry_hours))
    except Exception as exc:  # noqa: BLE001 - the next tick tries again; the Health check sees a day without audit
        print(f"::warning title=KI-Wächter::Start nicht entschieden: {type(exc).__name__}: {str(exc)[:200]}")
        return 1
    return 0
