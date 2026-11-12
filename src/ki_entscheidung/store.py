"""The answers the model gave, one record per message, in ``data/ki_entscheidungen.json``.

The file rides the commit of the Update run like ``data/first_seen.json``,
so a later build, a rebuild after a merge and a replay of that commit read
the same answers and build the same feed without asking anyone. A build
without the key (``build-feed.yml``, ``manual-full-refresh.yml``, a local
run) reads the file and never writes it.

One record, under the message's state key (its GUID)::

    {"art": "ploetzlich", "abdruck": "…", "klasse": "bauwort", "titel": "48A: Gasrohrgebrechen",
     "modell": "gemini-3.5-flash-lite", "grund": "…", "entschieden": "2026-11-12T17:31:02+00:00",
     "gefragt": ["2026-11-12T17:31:02+00:00"], "zuletzt": "2026-11-12"}

``art`` is missing while no request for the message was answered.
``gefragt`` holds the moments of the last requests that carried the
message; the request count of a day is read from these moments, there is
no counter of its own (owner 2026-11-05: "Wir brauchen keine eigene
Zähltechnik."). ``entschieden`` says which event the answer is about: a WL
GUID carries no date, so the same key returns with the next event of that
ticker, and an answer older than the begin the message shows then is not
applied (``run._answer_holds``).

Beside the records the file holds ``schluessel_abgelehnt``, the moment
Google last refused the key, until a request is accepted again
(``run.Decider.outcome``).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from ..feed.config import validate_path
from ..feed_build.base import _VIENNA_TZ
from ..utils.files import atomic_write, read_capped_json
from ..utils.serialize import scrub_trojan_source_primitives

__all__ = [
    "PATH_ENV", "DEFAULT_PATH", "KINDS", "SUDDEN", "PLANNED", "UNCLEAR", "KEEP_DAYS", "REFUSED", "store_path", "read",
    "load", "save", "prune", "requests_on", "render",
]

LOGGER = logging.getLogger("build_feed.ki_entscheidung")

PATH_ENV: Final = "KI_ENTSCHEIDUNGEN_PATH"
DEFAULT_PATH: Final = "data/ki_entscheidungen.json"
MAX_BYTES: Final = 512 * 1024
SUDDEN: Final = "ploetzlich"
PLANNED: Final = "geplant"
UNCLEAR: Final = "unklar"
KINDS: Final = (SUDDEN, PLANNED, UNCLEAR)
# A record leaves the file a week after its message last stood in a build.
KEEP_DAYS: Final = 7
VERSION: Final = 1
REFUSED: Final = "schluessel_abgelehnt"


def store_path() -> Path:
    """Where the answers live: ``KI_ENTSCHEIDUNGEN_PATH`` or ``data/ki_entscheidungen.json``.

    Read on every call, so a test that redirects the variable also reaches
    a reloaded build module.
    """
    return validate_path(Path(os.getenv(PATH_ENV) or DEFAULT_PATH), PATH_ENV)


def _record(raw: object) -> dict[str, Any] | None:
    """*raw* as a record with the fields this package reads, or ``None``."""
    if not isinstance(raw, dict):
        return None
    record: dict[str, Any] = {
        name: raw[name]
        for name in ("abdruck", "klasse", "titel", "modell", "grund", "entschieden", "zuletzt")
        if isinstance(raw.get(name), str)
    }
    if raw.get("art") in KINDS:
        record["art"] = raw["art"]
    asked = raw.get("gefragt")
    record["gefragt"] = [moment for moment in asked if isinstance(moment, str)] if isinstance(asked, list) else []
    return record


def read() -> tuple[dict[str, dict[str, Any]], str]:
    """The stored records by key and the moment the key was last refused ("" when it was not).

    Both are empty when the file is missing or unreadable.
    """
    try:
        data = read_capped_json(store_path(), MAX_BYTES, label="ki_entscheidungen", logger=LOGGER)
    except Exception as exc:  # a broken path must not stop the build
        LOGGER.warning("KI-Entscheidung: Speicher nicht lesbar (%s).", type(exc).__name__)
        return {}, ""
    raw = data.get("entscheidungen") if isinstance(data, dict) else None
    records: dict[str, dict[str, Any]] = {}
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        record = _record(value)
        if isinstance(key, str) and record is not None:
            records[key] = record
    refused = data.get(REFUSED) if isinstance(data, dict) else None
    return records, refused if isinstance(refused, str) else ""


def load() -> dict[str, dict[str, Any]]:
    """The stored records by key; empty when the file is missing or unreadable."""
    return read()[0]


def render(records: Mapping[str, Mapping[str, Any]], refused: str = "") -> str:
    """The file's text for *records* (and a refused key's moment): sorted keys, one field a line."""
    document: dict[str, Any] = {"version": VERSION, "entscheidungen": {key: dict(records[key]) for key in sorted(records)}}
    if refused:
        document[REFUSED] = refused
    return json.dumps(scrub_trojan_source_primitives(document), ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n"


def save(records: Mapping[str, Mapping[str, Any]], before: str, refused: str = "") -> bool:
    """Write *records* unless their text equals *before*; whether the file was written."""
    text = render(records, refused)
    if text == before:
        return False
    with atomic_write(store_path(), mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(text)
    return True


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def prune(records: dict[str, dict[str, Any]], today: date) -> None:
    """Drop the records whose message has not stood in a build for :data:`KEEP_DAYS` days."""
    limit = today - timedelta(days=KEEP_DAYS)
    for key in [key for key, record in records.items() if (_day(record.get("zuletzt")) or today) < limit]:
        del records[key]


def _asked_moments(records: Iterable[Mapping[str, Any]]) -> set[str]:
    return {moment for record in records for moment in record.get("gefragt", ()) if isinstance(moment, str)}


def requests_on(records: Mapping[str, Mapping[str, Any]], day: date) -> int:
    """How many requests the records show for the Vienna day *day* (one moment, one request)."""
    count = 0
    for moment in _asked_moments(records.values()):
        try:
            asked = datetime.fromisoformat(moment)
        except ValueError:
            continue
        if asked.tzinfo is not None and asked.astimezone(_VIENNA_TZ).date() == day:
            count += 1
    return count
