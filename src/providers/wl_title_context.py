"""Titles, stop names and the context suffix of a WL item.

Moved verbatim from ``src/providers/wl_fetch.py`` (700-line rule,
2026-11-02).
"""

from __future__ import annotations

import html
import re
from typing import Any
from collections.abc import Iterable, Mapping, Sequence

from ..utils.stations import canonical_name, display_name
from ..utils.text import DROP_ACCESS, DROP_LIFT, facility_drop_label, is_station_access_only

from .wl_lines import LINE_CODE_RE
from .wl_text import (
    FACILITY_ONLY,
    _is_facility_only,
    _tidy_title_wl,
    is_stop_notice,
)

# WL's display boards cut a ticker where its text runs out: "Ersatzbus 41E
# hält gegenüber" (no place), "Demonstration Züge halten gegenüber"
# (R45-101, 24.10.2026 20:31 Vienna time). A title that ends in a preposition still
# waiting for its object says nothing the reader can use. Such a title never
# names a bucket while a complete one is there (``_title_quality_key``), and
# an item that has nothing else is dropped (``_post_filter_wl`` in
# ``src/feed_build/cache_filters.py``, which reads the pattern from here).
INCOMPLETE_TITLE_TAIL_RE = re.compile(
    r"\b(?:bei|gegen[üu]ber|an|in|vor|nach|zu|über|ueber|am|im|zur|zum)\s*$",
    re.IGNORECASE,
)


def _is_lift_record(record: Mapping[str, Any] | None) -> bool:
    """Whether the WL record is one of WL's lift or escalator entries.

    WL's ``newsList`` carries the planned maintenance of every lift as its
    own record: ``elevatorId`` ("MP 06"), status "geplante Wartung", the
    station as title ("Praterstern") and the lift's place as text ("U1
    Bahnsteig … Zwischengeschoss Zugang U2 - Ausgang Lassallestraße"). The
    title names no facility word, so the title rule below never saw them;
    they stayed out only because their texts happened to carry no
    restriction word (115 such titles in the raw data of 04.10.–07.11.2026,
    none passed the keyword gate). The field is the structural sign.
    """
    if not isinstance(record, Mapping):
        return False
    attrs = record.get("attributes")
    if record.get("elevatorId"):
        return True
    return isinstance(attrs, Mapping) and bool(attrs.get("elevatorId"))


def _facility_drop_reason(
    title: str, description: str = "", record: Mapping[str, Any] | None = None
) -> str | None:
    """Drop reason for a message about station facilities only, else ``None``.

    A lift or escalator (``_is_facility_only``, since 2026-11-07 also every
    record with WL's ``elevatorId``, :func:`_is_lift_record`), since
    2026-10-09 the folding ramp of a train (own label since 2026-10-10), and
    a closed station access (``is_station_access_only``: "Aufgangssperre",
    "Sperre Ausgang") have no place in the feed: the trains still stop.

    The rule reads the title as the subject of the message. The title of a
    stop relocation or closure is the stop's name (:func:`is_stop_notice`,
    *description*), and a stop may be named after an entrance: "68A: Laaer
    Berg, Kurpark Nordeingang" (relocation from 05.11.2026 for about two
    weeks, cause "Straßenbau") fell as "nur Aufgang/Ausgang" from 04.11.
    14:01 Vienna time in every fetch. A stop notice is never about a
    facility, so the title rule does not apply to it.
    """
    if _is_lift_record(record):
        return DROP_LIFT
    if is_stop_notice(description):
        return None
    if _is_facility_only(title):
        return facility_drop_label(FACILITY_ONLY.findall(title))
    if is_station_access_only(title):
        return DROP_ACCESS
    return None


# Precompiled regex patterns
_ALPHA_RE = re.compile(r"[A-Za-zÄÖÜäöüß]")


# A title that only lists lines says nothing the line prefix does not. The
# demonstration notice of 2026-10-01 was titled "D, 1, 2, 71, 1A, 3A" and
# stood in the feed as "D/1/2/71/1A/3A: D, 1, 2, 71, 1A, 3A"; its
# description opened with "<h2>Demonstration</h2>".
_LINE_LIST_SEPARATORS_RE = re.compile(r"[\s,;/&+]+|\bund\b", re.IGNORECASE)
_HEADING_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)


def _title_or_heading(title_raw: str, desc: str) -> str:
    """WL's tidied title, or the description's heading when the title only lists lines.

    ``_tidy_title_wl`` strips leading/trailing dashes/colons, so a source
    title of only punctuation ("---") tidies to "" — then
    ``_ensure_line_prefix`` would render just the line codes ("U1/U2") with
    no description. Such a title falls back to the heading, else to a
    generic label. The heading keeps its first word: ``_tidy_title_wl``
    would strip "Gleisbauarbeiten" from "Gleisbauarbeiten Märzstraße" as a
    label, and there it is the cause.
    """
    title = _tidy_title_wl(title_raw)
    if title and _LINE_LIST_SEPARATORS_RE.sub("", LINE_CODE_RE.sub("", title)):
        return title
    match = _HEADING_RE.search(desc)
    # Unescape before the tag strip, so entity-encoded tags go too.
    text = re.sub(r"<[^>]+>", " ", html.unescape(match.group(1))) if match else ""
    heading = re.sub(r"[<>«»‹›]+", "", " ".join(text.split())).strip(" -–—:/")
    return heading or title or "Meldung"


# ---------------- Stop-Namen extrahieren ----------------

def _stop_names_from_related(rel_stops: list[Any]) -> list[str]:
    dedup: dict[str, str] = {}
    for s in rel_stops:
        raw: str | None = None
        if isinstance(s, dict):
            for key in ("name", "stopName", "title"):
                val = s.get(key)
                if val and _ALPHA_RE.search(str(val)):
                    raw = str(val).strip()
                    break
        elif isinstance(s, str):
            if _ALPHA_RE.search(s):
                raw = s.strip()
        if not raw:
            continue
        canonical = canonical_name(raw)
        if canonical:
            final = display_name(canonical)
        else:
            # ``\s+`` (NOT ``\s{2,}``): the prior shape only collapsed runs
            # of >=2 whitespace chars, so a single embedded ``\n`` or ``\t``
            # in a relatedStops name survived into the RSS ``Haltestelle:``
            # description line verbatim, breaking the single-line render
            # downstream. Matches the canonical shape of the sibling
            # ``_normalize_whitespace`` helper below.
            final = re.sub(r"\s+", " ", raw).strip()
        if not final:
            continue
        key = final.casefold()
        dedup.setdefault(key, final)
    return sorted(dedup.values(), key=lambda x: x.casefold())


# ---------------- Kontext für Titel ----------------

def _normalize_whitespace(value: str) -> str:
    # Collapse any whitespace run (incl. isolated newlines and tabs) to a
    # single space so titles and labels stay single-line for RSS/Atom.
    return re.sub(r"\s+", " ", value or "").strip()


def _split_extra(extra: str) -> tuple[str, str] | None:
    if not extra or ":" not in extra:
        return None
    head, tail = extra.split(":", 1)
    head = head.strip()
    tail = _normalize_whitespace(tail)
    if not head or not tail:
        return None
    return head, tail


def _context_values_from_stop_names(
    stop_names: Iterable[str], base_title: str
) -> list[str]:
    base_cf = base_title.casefold()
    seen: set[str] = set()
    values: list[str] = []
    for name in sorted(stop_names, key=lambda x: x.casefold()):
        clean = _normalize_whitespace(str(name))
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        if key in base_cf:
            continue
        values.append(clean)
    return values


def _context_values_from_extras(
    extras: Sequence[str], base_title: str
) -> tuple[list[str], list[str]]:
    base_cf = base_title.casefold()
    values: list[str] = []
    used: list[str] = []
    seen: set[str] = set()
    for extra in extras:
        parsed = _split_extra(extra)
        if not parsed:
            continue
        label, value = parsed
        if label.casefold() not in {"station", "location"}:
            continue
        key = value.casefold()
        if not value or key in seen or key in base_cf:
            continue
        seen.add(key)
        values.append(value)
        used.append(extra)
    return values, used


def _title_quality_key(title: str, title_core: str) -> tuple[int, int, int, int]:
    """Score titles so that informative variants win over short generics.

    A title cut by the display board — it ends in a preposition without its
    object (:data:`INCOMPLETE_TITLE_TAIL_RE`) — ranks below every complete
    one, however many words it has. On 24.10.2026 20:31 Vienna time the ticker
    "Demonstration Züge halten gegenüber" (R45-101) outscored the long
    message "Demonstration" of the same incident on line 1; the bucket took
    its title, and ``_post_filter_wl`` dropped the whole item for the
    dangling "gegenüber" — the demonstration on line 1 left the feed while
    WL's long message still described it (again "2: Verkehrsunfall Züge
    halten gegenüber", 31.10. 15:01).
    """

    normalized_title = _normalize_whitespace(title)
    core = _normalize_whitespace(title_core)
    tokens = [tok for tok in core.split() if tok]
    informative_tokens = [tok for tok in tokens if len(tok) >= 4]
    complete = 0 if INCOMPLETE_TITLE_TAIL_RE.search(normalized_title) else 1
    return (
        complete,
        len(informative_tokens),
        len(core),
        -len(normalized_title),
    )


def _description_info_score(
    desc: str,
    *,
    title: str,
    stop_names: Iterable[str],
    extras: Sequence[str],
) -> tuple[int, int, int, int]:
    """Return a tuple describing how informative a description is."""

    normalized = _normalize_whitespace(desc)
    if not normalized:
        return (0, 0, 0, 0)

    desc_cf = normalized.casefold()
    title_norm = _normalize_whitespace(title).casefold()
    non_title = 0 if desc_cf and desc_cf == title_norm else 1

    info_hits = 0
    seen: set[str] = set()

    for name in stop_names:
        clean = _normalize_whitespace(str(name))
        if len(clean) < 3:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        if key and key in desc_cf:
            info_hits += 1
            seen.add(key)

    for extra in extras:
        parsed = _split_extra(extra)
        value = parsed[1] if parsed else _normalize_whitespace(str(extra))
        if len(value) < 3:
            continue
        key = value.casefold()
        if key in seen:
            continue
        if key and key in desc_cf:
            info_hits += 1
            seen.add(key)

    length = len(normalized)
    word_count = len(re.findall(r"\w+", normalized, flags=re.UNICODE))
    return (non_title, info_hits, length, word_count)


def _format_context(values: Sequence[str], limit: int = 2) -> tuple[str, int]:
    if not values:
        return "", 0
    trimmed = list(values[:limit])
    if not trimmed:
        return "", 0
    context = ", ".join(trimmed)
    if len(values) > limit:
        context += " …"
    return context, len(trimmed)


def _build_context_suffix(
    bucket: dict[str, Any], base_title: str, lines_disp: Sequence[str]
) -> tuple[str | None, list[str]]:
    if lines_disp:
        return None, []

    stop_context = _context_values_from_stop_names(
        bucket.get("stop_names", []), base_title
    )
    if stop_context:
        context, _ = _format_context(stop_context)
        if context:
            return context, []

    extras_context, used_extras = _context_values_from_extras(
        bucket.get("extras", []), base_title
    )
    if extras_context:
        context, used_count = _format_context(extras_context)
        if context:
            return context, used_extras[:used_count]

    return None, []
