"""Display tickers of a finished Wiener-Linien incident.

WL marks a finished incident ``"status": "resolved"`` and keeps it in
``trafficInfoList`` for one more fetch; ``wl_fetch`` drops it from then on
(2026-10-05). Its display-board tickers (``stoerungkurz``,
``refTrafficInfoCategoryId`` 3) carry no status at all and often run on
until their own ``time.end``. Left alone, they kept the finished incident in
the feed: in the raw data of 2026-10-04/05, 6 of 12 resolved incidents had
such tickers, and they stood for between 15 minutes and four and a half
hours after WL had closed the incident:

* ``15A: Feuerwehreinsatz`` closed at 18:28, its ticker "Feuerwehreinsatz /
  Betrieb ab Eibesbrunnergasse" stood until 23:00 (Vienna time, 04.10.).
* ``U6: Rettungseinsatz`` closed at 10:59, its 59 tickers "U6 Störung / Kein
  Betrieb zwischen Westbahnhof und Längenfeldgasse" stood until 11:33 and
  were merged into the next U6 incident (05.10.).
* ``36B: Fremder Verkehrsunfall`` closed at 12:59; without the closed
  incident to merge into, its tickers became a new item on place 1 (05.10.).

A ticker belongs to the newest WL message of its kind on its lines, by
times alone, never by its words:

1. the message's lines include the ticker's lines (from ``relatedLines``,
   else from the title: the U6 tickers name no line otherwise);
2. the message began at most ten minutes after the ticker, and of all such
   messages it began last: tickers come with their incident, from four minutes
   before its start (65A, 06.10.) to 24 minutes after it (71, 05.10.), so an older
   incident on the same line does not claim them;
3. both are planned measures or both are not (``PLANNED_DISRUPTION_RE`` on
   the title, the rule of the feed's time line): the works ticker
   "Gleisbauarbeiten / Betrieb ab Johnstraße U" switched on for the N49 at
   01:00, 22 minutes into "N49: Verspätungen" (06.10.), belongs to the
   night works, not to the delay.

The ticker leaves when that message is closed and the ticker began before
the disruption ended, so a later incident with the same cause keeps its
tickers. For a resolved message the end is its ``time.end``. A follow-up
(``…-F01``) is WL's aftermath notice "Nach einer Fahrtbehinderung kommt es
zu unterschiedlichen Intervallen": WL creates it when the disruption ends
(``time.created``) and resolves it 20 to 90 minutes later, so its window
closes two minutes after ``time.created``. A running message of another WL
number that began in the same minute keeps the ticker; a running follow-up
of the same incident does not.

What a ticker says does not count (2026-10-08). Until then each of its
words beyond lines and stock words had to appear in the closed message, and
WL's display wording differs from the message too often: the ticker
"Rettungseinsatz / züge halten Favoritenstraße 113" stood on place 2 of the
feed for four hours after WL had closed "6: Rettungseinsatz" at 10:09,
because the aftermath notice names no street; "Schadhafter Zug" stayed
beside "25, 26A: Betriebsstörung" (07.10.). In the raw data of 04. to
08.10. the word rule kept 40 of the 339 tickers of 36 closed incidents.

A ticker the closed message's window held, but whose lines a running
incident of another WL number claimed by rule 2, stays: "Fremdunfall /
Betrieb ab Quellenplatz" began at 19:04, four minutes into "11, O: Fremder
Verkehrsunfall", while "74A, O: Verkehrsüberlastung" on the O closed
(06.10.). The resolved message shows up in one fetch only, so the tickers
found then are remembered by name, start and title
(``data/wl_resolved_tickers.json``, written by
``scripts/update_wl_cache.py``) and dropped until WL removes them. A ticker
WL reuses for a new incident gets a new start or title and counts as new.

A reused ticker keeps the start of its old incident; :func:`rewritten_starts`
gives it the start of the incident it now shows, so it does not date that
one back (operator decision 2026-11-14). The same file remembers which
incident each ticker's start dates.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dateutil import parser as dtparser

from ..utils.files import atomic_write, read_capped_json, read_capped_text
from ..utils.serialize import scrub_trojan_source_primitives
from ..utils.text import names_planned_measure
from ..utils.vienna_clock import plausible_moment, vienna_wall_clock
from .wl_lines import _detect_line_pairs_from_text, _make_line_pairs_from_related

log = logging.getLogger(__name__)

TICKER_CATEGORY = 3
# How long before the incident's start one of its tickers may begin.
# Measured 2026-10-04 to 08.10.: at most 3.7 minutes ("65A: Fremder
# Verkehrsunfall", 06.10.).
TICKER_LEAD = timedelta(minutes=10)
# How long after a follow-up's ``time.created`` (the end of the disruption)
# one of the incident's tickers may still begin. Measured 2026-10-04/05: at
# most 52 s ("42: Falschparker", 05.10. 17:49). A ticker begun later belongs
# to a new incident, even with the same line and cause: the aftermath of
# "38A: Falschparker" ran from 11:30 until 12:59 (05.10.).
AFTERMATH_GRACE = timedelta(minutes=2)
MAX_MEMORY_BYTES = 256 * 1024

# Tickers of closed incidents, remembered across fetches: key -> record.
_remembered: dict[str, dict[str, str]] = {}
# Name and start of each ticker -> the incident that start dates and the
# ticker's title when it came with it, remembered across fetches
# (``rewritten_starts``).
_owners: dict[str, dict[str, str]] = {}


def _moment(value: object) -> datetime | None:
    """WL's ``2026-10-05T18:57:59.000+0200`` as an aware datetime, else ``None``."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        moment = dtparser.isoparse(text)
    except (ValueError, OverflowError):
        return None
    if plausible_moment(moment) is None:
        return None
    # The offset WL gives a time of the other season (``wl_fetch._iso``).
    return vienna_wall_clock(moment) if moment.tzinfo is not None else None


def _time(info: Mapping[str, Any], key: str) -> datetime | None:
    times = info.get("time")
    return _moment(times.get(key)) if isinstance(times, Mapping) else None


def _lines(info: Mapping[str, Any]) -> frozenset[str]:
    related = info.get("relatedLines")
    pairs = _make_line_pairs_from_related(related) if isinstance(related, list) else []
    if not pairs:
        pairs = _detect_line_pairs_from_text(str(info.get("title") or ""))
    return frozenset(token for token, _display in pairs)


def _planned(info: Mapping[str, Any]) -> bool:
    """True when the title names a planned measure (rule 3 above)."""
    return names_planned_measure(str(info.get("title") or ""))


def ticker_key(info: Mapping[str, Any]) -> str:
    """Name, start and title of a ticker: a reused ticker gets a new key.

    Scrubbed like the memory file, so a key read back from it still matches
    a ticker whose title carries, say, a soft hyphen.
    """
    start = info.get("time", {}).get("start") if isinstance(info.get("time"), Mapping) else ""
    key = "|".join((str(info.get("name") or ""), str(start or ""), str(info.get("title") or "")))
    return str(scrub_trojan_source_primitives(key))


def is_ticker(info: Mapping[str, Any]) -> bool:
    """A display-board ticker (``stoerungkurz``), which WL sends without a status."""
    return str(info.get("refTrafficInfoCategoryId") or "").strip() == str(TICKER_CATEGORY)


def _incident_number(info: Mapping[str, Any]) -> str:
    """``I20261005-0035-F01`` → ``I20261005-0035``: a follow-up shares its number."""
    return str(info.get("name") or "").split("-F")[0]


def _disruption_end(incident: Mapping[str, Any]) -> datetime | None:
    """When the disruption of a resolved message ended (see above)."""
    closing = _time(incident, "end")
    created = _time(incident, "created")
    if closing is None or created is None or _incident_number(incident) == str(incident.get("name") or ""):
        return closing
    return min(closing, created + AFTERMATH_GRACE)


def _incident_of(ticker: Mapping[str, Any], messages: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The messages of *ticker*'s incident: the newest of its kind on its lines (rules 1–3).

    A follow-up keeps the start of its incident, so the original and its
    follow-ups come together; empty when no message fits.
    """
    lines, began = _lines(ticker), _time(ticker, "start")
    if not lines or began is None:
        return []
    planned = _planned(ticker)
    fitting: list[tuple[datetime, Mapping[str, Any]]] = []
    for message in messages:
        start = _time(message, "start")
        if start is not None and start <= began + TICKER_LEAD and lines <= _lines(message) and _planned(message) == planned:
            fitting.append((start, message))
    if not fitting:
        return []
    newest = max(start for start, _message in fitting)
    return [message for start, message in fitting if start == newest]


def _closed_owner(
    ticker: Mapping[str, Any], messages: Iterable[Mapping[str, Any]], finished: Callable[[Mapping[str, Any]], bool]
) -> Mapping[str, Any] | None:
    """The closed message whose disruption *ticker* showed, if its incident is over."""
    incident = _incident_of(ticker, messages)
    began = _time(ticker, "start")
    running = {_incident_number(message) for message in incident if not finished(message)}
    for message in incident:
        if not finished(message) or not running <= {_incident_number(message)}:
            continue
        closing = _disruption_end(message)
        if began is not None and closing is not None and began <= closing:
            return message
    return None


def stale_tickers(
    infos: Iterable[Mapping[str, Any]], finished: Callable[[Mapping[str, Any]], bool]
) -> set[str]:
    """Keys of the tickers in *infos* that belong to a closed incident.

    *finished* tells a closed long message (``wl_fetch``'s status filter).
    Tickers remembered from an earlier fetch count while WL still sends
    them; the memory then holds exactly the tickers returned here.
    """
    infos = [info for info in infos if isinstance(info, Mapping)]
    messages = [info for info in infos if not is_ticker(info)]
    stale: dict[str, dict[str, str]] = {}
    for ticker in infos:
        if not is_ticker(ticker):
            continue
        key = ticker_key(ticker)
        owner = _closed_owner(ticker, messages, finished)
        if owner is not None:
            stale[key] = _record(key, ticker, str(owner.get("name") or ""))
        elif key in _remembered:
            stale[key] = _remembered[key]
    _remembered.clear()
    _remembered.update(stale)
    return set(stale)


def _record(key: str, ticker: Mapping[str, Any], incident: str) -> dict[str, str]:
    return {
        "key": key,
        "name": str(ticker.get("name") or ""),
        "title": str(ticker.get("title") or ""),
        "end": str(ticker.get("time", {}).get("end") or "") if isinstance(ticker.get("time"), Mapping) else "",
        "incident": incident,
    }


def _start_key(info: Mapping[str, Any]) -> str:
    """Name and start of a ticker: what WL keeps when it rewrites the title."""
    start = info.get("time", {}).get("start") if isinstance(info.get("time"), Mapping) else ""
    return str(scrub_trojan_source_primitives(f"{info.get('name') or ''}|{start or ''}"))


def _serves(ticker: Mapping[str, Any], message: Mapping[str, Any]) -> bool:
    """Whether *message* is one *ticker* could show: rules 1 and 3 above."""
    return _lines(ticker) <= _lines(message) and _planned(message) == _planned(ticker)


def rewritten_starts(
    infos: Iterable[Mapping[str, Any]], finished: Callable[[Mapping[str, Any]], bool]
) -> dict[str, datetime]:
    """Ticker key -> begin, for the tickers WL rewrote for a newer incident.

    WL rewrites the display tickers of an incident for the next incident on
    the same lines and keeps their start: "Feuerwehreinsatz / Züge halten
    bei Linie 52" (``R577-110``, start 12:17, incident ``I20261114-0018``
    from 12:16) read "Fahrtbehinderung / Falschparker" from 13:31 for
    ``I20261114-0020`` (from 13:12), and the feed showed "60: Falschparker
    [Seit 12:17]". A ticker's start dates the incident it came with: the
    newest message of its kind on its lines that began at most
    :data:`TICKER_LEAD` after it (rule 2), or, once that message has left
    the data, the incident remembered for its name and start. When the
    ticker no longer shows that incident (``_rewritten``), a running message
    of another number on its lines that began after the ticker is the
    incident it now shows, and that message's start is its begin (operator
    decision 2026-11-14, "Ja, als Klasse beheben"). A ticker
    without such an incident (it came before its message, up to 43 minutes
    on 07.11.2026) keeps its start, and so does a works ticker: the time
    line shows no "Seit" for a planned measure.
    """
    infos = [info for info in infos if isinstance(info, Mapping)]
    messages = [info for info in infos if not is_ticker(info)]
    owners: dict[str, dict[str, str]] = {}
    begins: dict[str, datetime] = {}
    for ticker in infos:
        began = _time(ticker, "start")
        if not is_ticker(ticker) or began is None or _planned(ticker):
            continue
        owner = _owner(ticker, messages)
        if owner is None:
            continue
        newer = [
            (start, _incident_number(message))
            for message in messages
            if not finished(message) and _incident_number(message) != owner["incident"]
            and _serves(ticker, message) and (start := _time(message, "start")) is not None
            and start > began + TICKER_LEAD
        ]
        if newer and _rewritten(ticker, owner, messages, finished):
            start, number = max(newer)
            owner = {"incident": number, "title": str(ticker.get("title") or ""), "begin": start.isoformat()}
        owners[_start_key(ticker)] = owner
        begin = _moment(owner.get("begin"))
        if begin is not None:
            begins[ticker_key(ticker)] = begin
    _owners.clear()
    _owners.update(owners)
    return begins


def _owner(ticker: Mapping[str, Any], messages: Iterable[Mapping[str, Any]]) -> dict[str, str] | None:
    """The incident *ticker*'s start dates, with the title the ticker had then.

    A ticker found rewritten keeps the newer incident and its begin
    (``begin``) for as long as WL sends it: when that incident is closed in
    turn, nothing else on its lines began after it.
    """
    remembered = _owners.get(_start_key(ticker))
    if remembered and remembered.get("begin"):
        return remembered  # rewritten before: it shows the incident noted then
    incident = _incident_of(ticker, messages)
    if incident:
        number = _incident_number(incident[0])
        if remembered and remembered.get("incident") == number:
            return remembered
        return {"incident": number, "title": str(ticker.get("title") or "")}
    return remembered


def _rewritten(
    ticker: Mapping[str, Any],
    owner: Mapping[str, str],
    messages: Iterable[Mapping[str, Any]],
    finished: Callable[[Mapping[str, Any]], bool],
) -> bool:
    """Whether *ticker* no longer shows *owner*'s incident.

    It does not once that incident is over, or when WL gave the ticker a new
    title while the incident still runs: on 21.10.2026 "Fahrtbehinderung /
    Fremdunfall" of "10, 46: Fremder Verkehrsunfall" (09:26) read "wegen
    Rettungseinsatz" for "46: Rettungseinsatz" (10:07). A ticker whose
    incident runs and whose title stayed shows that incident, although a
    newer one runs beside it ("11: Schadhafter Zug" beside "71, 11:
    Verkehrsunfall", 20.10.2026).
    """
    running = any(_incident_number(m) == owner.get("incident") and not finished(m) for m in messages)
    return not running or str(ticker.get("title") or "") != owner.get("title")


def load_memory(path: Path) -> None:
    """Remember the tickers stored at *path*; an absent or broken file is no memory."""
    _remembered.clear()
    _owners.clear()
    if not path.exists():
        return
    payload = read_capped_json(path, MAX_MEMORY_BYTES, label="WL resolved tickers", logger=log)
    records = payload.get("tickers") if isinstance(payload, dict) else None
    for record in records if isinstance(records, list) else []:
        if isinstance(record, dict) and isinstance(record.get("key"), str):
            _remembered[record["key"]] = {k: str(v) for k, v in record.items() if isinstance(k, str)}
    owners = payload.get("owners") if isinstance(payload, dict) else None
    for key, owner in owners.items() if isinstance(owners, dict) else ():
        if isinstance(owner, dict) and isinstance(owner.get("incident"), str):
            _owners[str(key)] = {k: str(v) for k, v in owner.items() if isinstance(k, str)}


def save_memory(path: Path) -> None:
    """Write the remembered tickers to *path*, only when that changes the file."""
    if not _remembered and not _owners and not path.exists():
        return
    document = {
        "version": 1,
        "description": (
            "Display-board tickers (stoerungkurz) of Wiener-Linien incidents WL marked resolved. "
            "src/providers/wl_resolved.py drops them while WL still sends them; key = name|start|title. "
            "owners: name|start of each ticker -> the incident its start dates, the ticker's title then and, "
            "for a ticker rewritten for a newer incident, that incident's begin (rewritten_starts)."
        ),
        "tickers": [_remembered[key] for key in sorted(_remembered)],
        "owners": {key: _owners[key] for key in sorted(_owners)},
    }
    text = json.dumps(scrub_trojan_source_primitives(document), ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    if path.exists() and read_capped_text(path, MAX_MEMORY_BYTES, label="WL resolved tickers", logger=log) == text:
        return
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(text)


def remembered() -> dict[str, dict[str, str]]:
    """A copy of the current memory (for tests and the update script's log)."""
    return {key: dict(record) for key, record in _remembered.items()}


def forget() -> None:
    """Clear the memory (tests)."""
    _remembered.clear()
    _owners.clear()


__all__ = [
    "AFTERMATH_GRACE",
    "TICKER_CATEGORY",
    "TICKER_LEAD",
    "forget",
    "is_ticker",
    "load_memory",
    "remembered",
    "rewritten_starts",
    "save_memory",
    "stale_tickers",
    "ticker_key",
]
