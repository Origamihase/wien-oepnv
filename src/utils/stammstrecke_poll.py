"""Does the Stammstrecke monitor's fetch work — independent of whether trains run?

The monitor (``scripts/update_stammstrecke_hbf.py``) polls the VAO
departure board at Wien Hbf once per update tick. Its ledgers only record
trains, so a silent ledger cannot tell two very different situations apart:

* **The fetch is broken** — the request fails, the answer is unreadable or
  incomplete, the poll is skipped (quota, circuit breaker) or no longer runs.
* **The fetch works, but no train runs** — a valid answer whose board holds
  no Stammstrecke train (night pause, closure, a real stop of service).

Operator decision (Michael, 2026-10-10): "Der Check soll nur auf rot, wenn
der Abruf nicht funktioniert. Wenn die Technik funktioniert gehört er auch
auf grün. Wenn wirklich keine Züge fahren, gehört dies auf der Homepage
gemeldet, aber der Check bleibt grün." Before, a completely dead monitor
turned nothing red: the direction check never fails (decision 2026-10-03,
#1939), and a corridor without ledger rows looked exactly like a night.

So every poll writes its outcome to :data:`POLL_STATUS_PATH`, whether it
worked or not, and this module holds the one rule all readers share:

* ``scripts/health_check.py`` turns red only when :func:`assess_poll` says
  the fetch is not working;
* the website and README name a train-less corridor only while the fetch
  demonstrably works — otherwise "keine Fahrten" would be a claim the
  monitor cannot make.

What counts as an incomplete answer is structural, not a word list: the
board lists departures, but not one of them carries the fields the monitor
reads (a line name, a scheduled date and time, a platform). A valid answer
with departures on other platforms only, or with no departures at all, is a
working fetch.
"""

from __future__ import annotations

import json as _json_lib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from .files import atomic_write, read_capped_json

LOGGER = logging.getLogger(__name__)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

#: Written by every monitor poll, committed by the update cycle.
POLL_STATUS_PATH: Final = REPO_ROOT / "cache" / "stammstrecke" / "poll_status.json"

#: Hours without a working poll before the fetch counts as broken. Ticks
#: arrive every 30 minutes; the longest gap between two polls from
#: 2026-09-09 to 2026-10-10 (1 493 polls, reconstructed from the VAO quota
#: counter in git history) was 2:00 h. Three hours is the first whole hour
#: clear of that, i.e. at least five missed polls in a row.
POLL_STALE_HOURS: Final = 3.0

RESULT_OK: Final = "ok"
RESULT_ERROR: Final = "error"
RESULT_INCOMPLETE: Final = "incomplete"
RESULT_QUOTA: Final = "quota_exceeded"
RESULT_BREAKER: Final = "breaker_open"

_RESULT_TEXT: Final = {
    RESULT_OK: "erfolgreich",
    RESULT_ERROR: "fehlgeschlagen",
    RESULT_INCOMPLETE: "Antwort unvollständig",
    RESULT_QUOTA: "übersprungen, Tageslimit erreicht",
    RESULT_BREAKER: "übersprungen, Circuit Breaker offen",
}


@dataclass(frozen=True)
class PollStatus:
    """Outcome of the latest poll plus the last one that worked."""

    last_attempt: datetime
    last_result: str
    last_error: str = ""
    last_success: datetime | None = None
    #: Departures on the board at the last working poll (all platforms).
    last_success_departures: int = 0
    #: Stammstrecke trains (S-Bahn on platforms 1/2, both directions, with
    #: or without realtime) at that poll.
    last_success_trains: int = 0
    #: Last working poll whose board held at least one Stammstrecke train.
    last_train_seen: datetime | None = None


@dataclass(frozen=True)
class PollVerdict:
    working: bool
    summary: str
    detail: str = ""


def answer_is_incomplete(*, departures: int, readable: int) -> bool:
    """A board that lists departures none of which can be read is broken."""
    return departures > 0 and readable == 0


def record_poll(
    previous: PollStatus | None,
    *,
    when: datetime,
    result: str,
    error: str = "",
    departures: int = 0,
    trains: int = 0,
) -> PollStatus:
    """Fold one poll into the status; a failed poll keeps the last success."""
    if result == RESULT_OK:
        return PollStatus(
            last_attempt=when,
            last_result=result,
            last_success=when,
            last_success_departures=departures,
            last_success_trains=trains,
            last_train_seen=(
                when if trains > 0 else (previous.last_train_seen if previous else None)
            ),
        )
    return PollStatus(
        last_attempt=when,
        last_result=result,
        last_error=error,
        last_success=previous.last_success if previous else None,
        last_success_departures=previous.last_success_departures if previous else 0,
        last_success_trains=previous.last_success_trains if previous else 0,
        last_train_seen=previous.last_train_seen if previous else None,
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def status_to_json(status: PollStatus) -> dict[str, object]:
    return {
        "last_attempt": _iso(status.last_attempt),
        "last_result": status.last_result,
        "last_error": status.last_error,
        "last_success": _iso(status.last_success),
        "last_success_departures": status.last_success_departures,
        "last_success_trains": status.last_success_trains,
        "last_train_seen": _iso(status.last_train_seen),
    }


def load_poll_status(path: Path | None = None) -> PollStatus | None:
    """Read the status file; ``None`` when it is missing or unreadable."""
    payload = read_capped_json(
        path if path is not None else POLL_STATUS_PATH,
        label="Stammstrecke-Abrufstatus",
        logger=LOGGER,
    )
    if not isinstance(payload, dict):
        return None
    last_attempt = _parse(payload.get("last_attempt"))
    result = payload.get("last_result")
    if last_attempt is None or not isinstance(result, str) or not result:
        return None
    error = payload.get("last_error")
    return PollStatus(
        last_attempt=last_attempt,
        last_result=result,
        last_error=error if isinstance(error, str) else "",
        last_success=_parse(payload.get("last_success")),
        last_success_departures=_count(payload.get("last_success_departures")),
        last_success_trains=_count(payload.get("last_success_trains")),
        last_train_seen=_parse(payload.get("last_train_seen")),
    )


def save_poll_status(status: PollStatus, path: Path | None = None) -> bool:
    """Persist *status* atomically; best-effort like the sibling ledgers."""
    target = path if path is not None else POLL_STATUS_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(target, mode="w", encoding="utf-8", permissions=0o644) as fh:
            _json_lib.dump(
                status_to_json(status),
                fh,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            fh.write("\n")
        return True
    except OSError as exc:
        LOGGER.warning(
            "Stammstrecke-Abrufstatus konnte nicht geschrieben werden: %s",
            type(exc).__name__,
        )
        return False


def _fmt_age(delta: timedelta) -> str:
    """Same shape as ``_fmt_age`` in ``scripts/health_check.py``."""
    s = max(0, int(delta.total_seconds()))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def assess_poll(
    status: PollStatus | None,
    *,
    now: datetime,
    stale_hours: float = POLL_STALE_HOURS,
) -> PollVerdict:
    """Is the fetch working? Train counts never enter the verdict."""
    if status is None:
        return PollVerdict(
            False,
            "FEHLER — kein lesbarer Abrufstatus des Stammstrecken-Monitors",
            "cache/stammstrecke/poll_status.json fehlt oder ist unlesbar",
        )
    last = (
        f"letzter Versuch vor {_fmt_age(now - status.last_attempt)}: "
        f"{_RESULT_TEXT.get(status.last_result, status.last_result)}"
        + (f" ({status.last_error})" if status.last_error else "")
    )
    if status.last_success is None:
        return PollVerdict(False, "FEHLER — noch kein erfolgreicher Abruf", last)
    age = now - status.last_success
    if age > timedelta(hours=stale_hours):
        return PollVerdict(
            False,
            f"FEHLER — kein erfolgreicher Abruf seit {_fmt_age(age)}",
            last,
        )
    if status.last_success_trains > 0:
        board = f"{status.last_success_trains} Stammstrecken-Zug/Züge auf der Tafel"
    else:
        since = (
            f" seit {_fmt_age(now - status.last_train_seen)}"
            if status.last_train_seen is not None
            else ""
        )
        board = f"derzeit kein Stammstrecken-Zug auf der Tafel{since}"
    detail = "" if status.last_result == RESULT_OK else last
    return PollVerdict(
        True, f"OK — Abruf funktioniert (vor {_fmt_age(age)}), {board}", detail
    )


__all__ = [
    "POLL_STALE_HOURS",
    "POLL_STATUS_PATH",
    "PollStatus",
    "PollVerdict",
    "RESULT_BREAKER",
    "RESULT_ERROR",
    "RESULT_INCOMPLETE",
    "RESULT_OK",
    "RESULT_QUOTA",
    "answer_is_incomplete",
    "assess_poll",
    "load_poll_status",
    "record_poll",
    "save_poll_status",
    "status_to_json",
]
