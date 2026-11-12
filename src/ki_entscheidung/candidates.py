"""Which disruptions the rules leave unclear, and what the model is shown of them.

``feed_build.time_line._incident_since`` tells a sudden disruption from a
planned measure by its words: a planned word anywhere in title or text
(``PLANNED_DISRUPTION_RE``) makes it planned, a WL disruption without a
cause is planned when it began on a full hour. Replayed over the 2,169
cache states from 20.09. to 12.11.2026 these words leave 22 of 1,026
disruptions without a clear answer; they fall into the three classes below,
and only those are put to the model (:mod:`src.ki_entscheidung`):

``ohne-ursache``
    A WL disruption that names no cause and no planned word, so only the
    clock decides: "26: Züge halten in Tokiostraße" (a measure running for
    weeks) read "[Seit 08:48]" among the current incidents from 09:01 to
    23:01 on 21.09.2026; "9: Züge halten bei Linie 52" (18.10., an incident)
    is told apart from it by its minute alone.
``bauwort``
    A WL disruption whose cause names an incident while a works word
    stands elsewhere in its text: "48A: Gasrohrgebrechen" (30.09.,
    "Aufgrund der Bauarbeiten werden die Busse … umgeleitet") and "U6:
    Weichenstörung" (22.09., "Die Station Neue Donau kann … wegen
    Bauarbeiten nicht eingehalten werden") lost their "[Seit hh:mm]".
``oebb-tag``
    An ÖBB message with a works word that ÖBB first published on the day
    it begins and that ends on that day: "Wegen Verlängerung der
    Bauarbeiten sind zwischen Wien Franz-Josefs-Bahnhof und Wien
    Heiligenstadt Bahnhof bis voraussichtlich 09:00 Uhr keine Fahrten
    möglich" stood as "[Heute]" on place 7 on 01.11.2026 at 06:01.
    ÖBB's planned closures are published days ahead.

What the owner has decided stays with the rules and is no candidate: a
moved or closed stop, a demonstration, an event, a state visit, a drill
are notices (2026-11-03), so a text naming one of them is planned whatever
else it says; an ÖBB report of a disruption that is over is no incident.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, cast

from ..feed.merge_items import _is_wl_disruption
from ..feed_build.base import _OEBB_SOURCE, _VIENNA_TZ, _parse_datetime, _to_utc, log
from ..feed_build.state import _lookup_state
from ..feed_build.time_line import _first_published, _incident_since, _reports_past_disruption
from ..feed_build.wl_tickers import _incident_cause
from ..feed_types import FeedItem
from ..ki_waechter.inputs import line_keys
from ..utils.text import PLANNED_DISRUPTION_RE, html_to_text

__all__ = [
    "CLASS_NO_CAUSE", "CLASS_WORKS_WORD", "CLASS_OEBB_DAY", "Candidate", "unclear_class", "candidates_of",
    "fingerprint", "describe", "neighbours_of", "sudden_begin", "rule_begin",
]

CLASS_NO_CAUSE: Final = "ohne-ursache"
CLASS_WORKS_WORD: Final = "bauwort"
CLASS_OEBB_DAY: Final = "oebb-tag"

# The planned words that name works ("Bauarbeiten", "Gleisbauarbeiten",
# "Straßenbau"). Every other match of ``PLANNED_DISRUPTION_RE`` names
# something the owner decided is a notice.
_WORKS_WORD_RE: Final = re.compile(r"(?:arbeiten|bau)$", re.IGNORECASE)
# WL's forecast changes under one incident ("Voraussichtliche Dauer: 19:30
# Uhr", later "22:00 Uhr", "Bis Betriebsschluss": "U6: Stellwerksstörung",
# 11.11.2026); it is no new message.
_FORECAST_RE: Final = re.compile(r"\s*Voraussichtliche Dauer:[^.]*\.?", re.IGNORECASE)
MAX_TEXT_CHARS: Final = 700
MAX_NEIGHBOURS: Final = 4
MAX_NEIGHBOUR_CHARS: Final = 240
_ROUTE_SPLIT_RE: Final = re.compile(r"\s*(?:↔|/)\s*")
_WEEKDAYS: Final = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


@dataclass(frozen=True)
class Candidate:
    """One unclear disruption: the item, its key in the store, its class and the print of its text."""

    item: FeedItem
    key: str
    kind: str
    print: str
    entry: Mapping[str, Any] | None


def _plain(item: FeedItem) -> str:
    return " ".join(html_to_text(str(item.get("description") or "")).split())


def _text(item: FeedItem) -> str:
    return f"{item.get('title') or ''} {html_to_text(str(item.get('description') or ''))}"


def _only_works_words(text: str) -> bool:
    """Whether *text* names a planned measure and every such word names works."""
    words = [match.group(0) for match in PLANNED_DISRUPTION_RE.finditer(text)]
    return bool(words) and all(_WORKS_WORD_RE.search(word) for word in words)


def _vienna_day(moment: datetime) -> date:
    return _to_utc(moment).astimezone(_VIENNA_TZ).date()


def _oebb_same_day(item: FeedItem, start: datetime, entry: Mapping[str, Any] | None) -> bool:
    """Whether ÖBB first published *item* on the day it begins and it ends on that day."""
    # A copy: ``_first_published`` writes the entry, and choosing candidates must not.
    published = _first_published(item, dict(entry) if entry else None)
    end = _parse_datetime(item.get("ends_at"))
    return (
        published is not None
        and _vienna_day(published) == _vienna_day(start)
        and (not isinstance(end, datetime) or _vienna_day(end) == _vienna_day(start))
    )


def unclear_class(
    item: FeedItem, entry: Mapping[str, Any] | None, now_utc: datetime, known: str | None = None,
) -> str | None:
    """The class of *item* when the rules leave it unclear, else ``None`` (module docstring).

    Only a disruption that has begun and is not reported as over can show
    "[Seit hh:mm]" or stand in the first tier; everything else is clear for
    the feed whatever its words say. *known* is the class the store holds
    for the message: an ÖBB message that was asked about as ``oebb-tag``
    stays one when ÖBB later moves its end to the next day ("bis
    voraussichtlich morgen 04:00 Uhr"), so its answer keeps applying.
    """
    start = _parse_datetime(item.get("starts_at"))
    if item.get("category") != "Störung" or not isinstance(start, datetime) or _to_utc(start) > now_utc:
        return None
    text = _text(item)
    if _reports_past_disruption(item, text):
        return None
    planned = PLANNED_DISRUPTION_RE.search(text) is not None
    if _is_wl_disruption(item):
        if not planned:
            return None if _incident_cause(item) else CLASS_NO_CAUSE
        return CLASS_WORKS_WORD if _incident_cause(item) and _only_works_words(text) else None
    if item.get("source") != _OEBB_SOURCE or not _only_works_words(text):
        return None
    return CLASS_OEBB_DAY if known == CLASS_OEBB_DAY or _oebb_same_day(item, start, entry) else None


def _begin(candidate: Candidate, kind: str | None) -> datetime | None:
    """The begin ``_incident_since`` gives *candidate* under the answer *kind* (``None``: the rules alone), in UTC."""
    fields: dict[str, Any] = {name: value for name, value in candidate.item.items() if name != "_ki_art"}
    if kind is not None:
        fields["_ki_art"] = kind
    item = cast(FeedItem, fields)
    entry = dict(candidate.entry) if candidate.entry else None
    since = _incident_since(item, _parse_datetime(item.get("starts_at")), _first_published(item, entry))
    return _to_utc(since) if isinstance(since, datetime) else None


def sudden_begin(candidate: Candidate) -> datetime | None:
    """The begin the feed would show as "[Seit hh:mm]" if the answer were "ploetzlich"."""
    return _begin(candidate, "ploetzlich")


def rule_begin(candidate: Candidate) -> datetime | None:
    """The begin the rules alone show for *candidate*; ``None`` where they call it planned."""
    return _begin(candidate, None)


def fingerprint(item: FeedItem) -> str:
    """A print of what *item* says: source, title and text without WL's changing forecast."""
    said = "\n".join((str(item.get("source") or ""), str(item.get("title") or ""), _FORECAST_RE.sub("", _plain(item))))
    # A lone surrogate reaches a title through an escape in upstream JSON (``feed_build.state``).
    return hashlib.sha256(said.casefold().encode("utf-8", "surrogatepass")).hexdigest()[:16]


def candidates_of(
    items: Sequence[FeedItem],
    state: dict[str, dict[str, Any]],
    now_utc: datetime,
    records: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Candidate]:
    """The unclear disruptions among *items*, in their order, each under its state key.

    *records* are the stored answers (their class is the *known* of
    :func:`unclear_class`). A message that cannot be read is skipped
    alone, so the others keep their answers.
    """
    found: list[Candidate] = []
    for item in items:
        try:
            key, entry = _lookup_state(item, state)
            record = records.get(key) if records is not None and key else None
            kind = unclear_class(item, entry, now_utc, str(record.get("klasse") or "") if record else None)
            if kind is not None and key:
                found.append(Candidate(item, key, kind, fingerprint(item), entry))
        except Exception as exc:
            log.warning("KI-Entscheidung: eine Meldung übersprungen (%s).", type(exc).__name__)
    return found


def _when(value: object) -> str:
    moment = _parse_datetime(value)
    if not isinstance(moment, datetime):
        return "nicht genannt"
    local = _to_utc(moment).astimezone(_VIENNA_TZ)
    return f"{_WEEKDAYS[local.weekday()]} {local:%d.%m.%Y %H:%M}"


def _route_names(title: str) -> frozenset[str]:
    """The places an ÖBB title names ("S 40: Wien Franz-Josefs-Bahnhof ↔ Tulln" → both ends)."""
    route = title.split(": ", 1)[-1]
    return frozenset(name.casefold() for name in _ROUTE_SPLIT_RE.split(route) if name.strip())


def _shared(item: FeedItem, other: FeedItem) -> int:
    """How many lines (WL) or places of the route (ÖBB) the two messages of one source share."""
    if item.get("source") != other.get("source"):
        return 0
    title, other_title = str(item.get("title") or ""), str(other.get("title") or "")
    if item.get("source") == _OEBB_SOURCE:
        return len(_route_names(title) & _route_names(other_title))
    return len(line_keys(title) & line_keys(other_title))


def neighbours_of(item: FeedItem, items: Sequence[FeedItem]) -> list[FeedItem]:
    """The other messages of *item*'s lines, at most :data:`MAX_NEIGHBOURS`, those that share most first.

    "Wien Hauptbahnhof ↔ Gramatneusiedl" (09.11.2026) stood beside ÖBB
    messages that share only Wien Hauptbahnhof with it ("R 95: Wien
    Hauptbahnhof ↔ Felixdorf"); the two about the same stretch say what
    was planned there.
    """
    scored = [(_shared(item, other), index, other) for index, other in enumerate(items) if other is not item]
    scored = [entry for entry in scored if entry[0]]
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [other for _, _, other in scored[:MAX_NEIGHBOURS]]


def describe(number: int, candidate: Candidate, items: Sequence[FeedItem]) -> str:
    """What the model reads of one candidate: its own fields and the other messages of its lines.

    The neighbours carry what the short text lacks: a notice of works on
    the same line beside "26: Züge halten in Tokiostraße", a running
    incident on line 52 beside "9: Züge halten bei Linie 52".
    """
    item = candidate.item
    lines = [
        f"Meldung {number}",
        f"Quelle: {item.get('source') or ''}",
        f"Titel: {item.get('title') or ''}",
        f"Text: {_plain(item)[:MAX_TEXT_CHARS] or '(kein Text)'}",
        f"Beginn laut Quelle: {_when(item.get('starts_at'))}",
        f"Ende laut Quelle: {_when(item.get('ends_at'))}",
        f"Erstmals veröffentlicht: {_when(item.get('pubDate'))}",
    ]
    neighbours = neighbours_of(item, items)
    for other in neighbours:
        lines.append(
            f"Andere Meldung derselben Linie ({other.get('category') or ''}, "
            f"Beginn {_when(other.get('starts_at'))}, Ende {_when(other.get('ends_at'))}): "
            f"{other.get('title') or ''} — {_plain(other)[:MAX_NEIGHBOUR_CHARS]}"
        )
    if not neighbours:
        lines.append("Andere Meldungen derselben Linie: keine")
    return "\n".join(lines)
