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

A ticker belongs to a closed incident when all three hold:

1. its lines are among the incident's lines (from ``relatedLines``, else
   from its title: the U6 tickers name no line otherwise);
2. it started between ten minutes before the incident and its closing
   (``time.end`` of the resolved message), so a later incident with the
   same cause keeps its tickers;
3. every word it adds beyond lines and stock words ("Fahrtbehinderung",
   "Betrieb ab", "Kein Betrieb zwischen", …) appears in the incident's
   title or text: "Westbahnhof" and "Längenfeldgasse" in "Die Linie U6
   fährt derzeit nicht zwischen Westbahnhof und Längenfeldgasse". The
   works ticker "66A: Bauarbeiten / Busse halten Salvatorianerplatz" next to
   the closed "66A: Störung an einem Bahnübergang" fails here and stays.

A ticker that also fits a running incident of another WL number stays. The
resolved message shows up in one fetch only, so the tickers found then are
remembered by name, start and title (``data/wl_resolved_tickers.json``,
written by ``scripts/update_wl_cache.py``) and dropped until WL removes
them. A ticker WL reuses for a new incident gets a new start or title and
counts as new.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dateutil import parser as dtparser

from ..utils.files import atomic_write, read_capped_json, read_capped_text
from ..utils.serialize import scrub_trojan_source_primitives
from .wl_lines import _detect_line_pairs_from_text, _make_line_pairs_from_related

log = logging.getLogger(__name__)

TICKER_CATEGORY = 3
# How long before the incident's start one of its tickers may begin. Measured
# 2026-10-04/05: the earliest began 61 s before the incident's start
# (13A, 04.10.), the latest 9.5 minutes after it (U6, 05.10.).
TICKER_LEAD = timedelta(minutes=10)
MAX_MEMORY_BYTES = 256 * 1024

# Words a ticker uses for the kind of consequence, not for the incident.
# Everything else a ticker says (cause, stop, street) must be in the incident.
_STOCK_WORDS = frozenset(
    {
        "ab", "auf", "aus", "bei", "beiden", "betrieb", "bis", "bitte", "busse",
        "dem", "den", "der", "des", "die", "das", "derzeit", "einstieg",
        "ersatzbus", "ersatzverkehr", "fahrtbehinderung", "fahrtrichtung",
        "halte", "halten", "haltestelle", "haltestellen", "hält", "im", "in",
        "kein", "keine", "linie", "linien", "mit", "nach", "nur", "richtung",
        "richtungen", "störung", "über", "umleitung", "und", "unregelmäßige",
        "intervalle", "verspätungen", "von", "vor", "wegen", "zug", "züge",
        "zum", "zur", "zwischen",
    }
)
# Cause words WL writes differently on the display and in the long message,
# side by side within one incident (``build_feed._CAUSE_SYNONYMS``).
_WORD_ALIASES: dict[str, frozenset[str]] = {
    "fremdunfall": frozenset({"verkehrsunfall"}),
    "schadhafter": frozenset({"schadhaftes"}),
    "bus": frozenset({"fahrzeug"}),
    "beschädigte": frozenset({"oberleitungsgebrechen"}),
    "oberleitung": frozenset({"oberleitungsgebrechen"}),
    "oberleitungsgebr": frozenset({"oberleitungsgebrechen"}),
    "rettungseinatz": frozenset({"rettungseinsatz"}),
}
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_TAG_RE = re.compile(r"<[^>]+>")

# Tickers of closed incidents, remembered across fetches: key -> record.
_remembered: dict[str, dict[str, str]] = {}


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
    return moment if moment.tzinfo is not None else None


def _time(info: Mapping[str, Any], key: str) -> datetime | None:
    times = info.get("time")
    return _moment(times.get(key)) if isinstance(times, Mapping) else None


def _lines(info: Mapping[str, Any]) -> frozenset[str]:
    related = info.get("relatedLines")
    pairs = _make_line_pairs_from_related(related) if isinstance(related, list) else []
    if not pairs:
        pairs = _detect_line_pairs_from_text(str(info.get("title") or ""))
    return frozenset(token for token, _display in pairs)


def _words(*parts: object) -> set[str]:
    text = html.unescape(_TAG_RE.sub(" ", " ".join(str(part or "") for part in parts)))
    return set(_WORD_RE.findall(text.casefold()))


def ticker_key(info: Mapping[str, Any]) -> str:
    """Name, start and title of a ticker: a reused ticker gets a new key."""
    start = info.get("time", {}).get("start") if isinstance(info.get("time"), Mapping) else ""
    return "|".join((str(info.get("name") or ""), str(start or ""), str(info.get("title") or "")))


def is_ticker(info: Mapping[str, Any]) -> bool:
    """A display-board ticker (``stoerungkurz``), which WL sends without a status."""
    return str(info.get("refTrafficInfoCategoryId") or "").strip() == str(TICKER_CATEGORY)


def _incident_number(info: Mapping[str, Any]) -> str:
    """``I20261005-0035-F01`` → ``I20261005-0035``: a follow-up shares its number."""
    return str(info.get("name") or "").split("-F")[0]


def _own_words(ticker: Mapping[str, Any], lines: frozenset[str]) -> set[str]:
    words = _words(ticker.get("title"), ticker.get("description"))
    return {word for word in words if word not in _STOCK_WORDS and word not in {line.casefold() for line in lines}}


def _said_by(words: set[str], incident: Mapping[str, Any]) -> bool:
    """True when *incident*'s title or text holds every one of *words*."""
    told = _words(incident.get("title"), incident.get("description"), incident.get("descriptionHTML"))
    return all(word in told or bool(_WORD_ALIASES.get(word, frozenset()) & told) for word in words)


def _belongs(ticker: Mapping[str, Any], incident: Mapping[str, Any], *, closed: bool) -> bool:
    """True when *ticker* is one of *incident*'s display-board tickers."""
    lines = _lines(ticker)
    if not lines or not lines <= _lines(incident):
        return False
    began, start = _time(ticker, "start"), _time(incident, "start")
    if began is None or start is None or began < start - TICKER_LEAD:
        return False
    closing = _time(incident, "end")
    if closed and (closing is None or began > closing):
        return False
    return _said_by(_own_words(ticker, lines), incident)


def stale_tickers(
    infos: Iterable[Mapping[str, Any]], finished: Callable[[Mapping[str, Any]], bool]
) -> set[str]:
    """Keys of the tickers in *infos* that belong to a closed incident.

    *finished* tells a closed long message (``wl_fetch``'s status filter).
    Tickers remembered from an earlier fetch count while WL still sends
    them; the memory then holds exactly the tickers returned here.
    """
    infos = [info for info in infos if isinstance(info, Mapping)]
    tickers = [info for info in infos if is_ticker(info)]
    messages = [info for info in infos if not is_ticker(info)]
    closed = [message for message in messages if finished(message)]
    running = [message for message in messages if not finished(message)]
    stale: dict[str, dict[str, str]] = {}
    for ticker in tickers:
        key = ticker_key(ticker)
        owner = next((message for message in closed if _belongs(ticker, message, closed=True)), None)
        if owner is not None and any(
            _incident_number(other) != _incident_number(owner) and _belongs(ticker, other, closed=False)
            for other in running
        ):
            owner = None
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


def load_memory(path: Path) -> None:
    """Remember the tickers stored at *path*; an absent or broken file is no memory."""
    _remembered.clear()
    if not path.exists():
        return
    payload = read_capped_json(path, MAX_MEMORY_BYTES, label="WL resolved tickers", logger=log)
    records = payload.get("tickers") if isinstance(payload, dict) else None
    for record in records if isinstance(records, list) else []:
        if isinstance(record, dict) and isinstance(record.get("key"), str):
            _remembered[record["key"]] = {k: str(v) for k, v in record.items() if isinstance(k, str)}


def save_memory(path: Path) -> None:
    """Write the remembered tickers to *path*, only when that changes the file."""
    if not _remembered and not path.exists():
        return
    document = {
        "version": 1,
        "description": (
            "Display-board tickers (stoerungkurz) of Wiener-Linien incidents WL marked resolved. "
            "src/providers/wl_resolved.py drops them while WL still sends them; key = name|start|title."
        ),
        "tickers": [_remembered[key] for key in sorted(_remembered)],
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


__all__ = [
    "TICKER_CATEGORY",
    "TICKER_LEAD",
    "forget",
    "is_ticker",
    "load_memory",
    "remembered",
    "save_memory",
    "stale_tickers",
    "ticker_key",
]
