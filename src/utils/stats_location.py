"""Location extraction for the disruption ledger (``stoerungen_*.csv``).

Moved verbatim from ``src/utils/stats.py`` (700-line limit, 2026-11-02);
``src.utils.stats`` re-exports every name.
"""
from __future__ import annotations

import re
from typing import Any, Final

from src.utils.stations import display_name, station_info, station_named_in_text

# Heuristic field-extraction patterns. All patterns operate on the
# already-sanitised plain-text title/description coming out of the
# providers — no HTML, no control bytes, no upstream-controlled
# unbounded length (the providers cap titles at ~200 chars and
# descriptions at a few hundred). The patterns are deliberately broad-
# but-bounded: ``{2,80}`` upper bounds keep ReDoS off the table even on
# pathological input.

# Upper bound for the *anchored* label scans in
# :func:`extract_location_name` (``| Haltestelle:`` / ``| Station:`` /
# ``| Location:``). Sized against live data: WL descriptions in
# ``cache/wl_*/events.json`` topped out at 4972 chars on 2026-09-13,
# with 69% above the 1024-char bound that the unanchored scans still
# use. 8192 leaves ~1.6x headroom over that observed maximum while
# keeping an explicit ceiling on adversarial input — the label regexes
# are anchored on a literal and cap their capture at 200 chars, so the
# cost here is a bounded literal search, not a backtracking scan.
MAX_LABELLED_SCAN_CHARS: Final = 8192

# Structured-signal patterns. Each captures a *labelled* location segment
# that the providers already serialise into ``description`` (or ``title``)
# in a deterministic shape — these are the cleanest signals because the
# field came out of a curated upstream API field (WL ``relatedStops`` /
# attrs.station / attrs.location) or out of our own VOR ``in Richtung``
# render. The capture group is then passed through
# :func:`_resolve_via_directory`, which only returns a value when
# :func:`station_info` recognises it — so a poisoned upstream string can
# never silently land as a "location" cell on the dashboard.
#
# All upper bounds are explicit (``{1,200}`` for the labelled-segment
# captures, ``{1,80}`` for the free-text scans) to keep the regex engine
# bounded on adversarial inputs (ReDoS hardening); the captured value is
# later clamped to 80 chars in :func:`_normalise_location`.
_HALTESTELLE_RE: Final = re.compile(
    r"\|\s*Haltestelle:\s*([^|]{1,200})",
    re.IGNORECASE,
)
_LABELED_LOCATION_RE: Final = re.compile(
    r"\|\s*(?:Station|Location)\s*:\s*([^|]{1,200})",
    re.IGNORECASE,
)
_RICHTUNG_RE: Final = re.compile(
    r"\bin\s+Richtung\s+([^,\.\[\|]{1,80})",
    re.IGNORECASE,
)
_BETWEEN_RE: Final = re.compile(
    r"\bzwischen\s+([A-ZÄÖÜ][\w\.\-’']{1,80}?(?:\s+[A-ZÄÖÜ][\w\.\-’']{1,80}){0,3})"
    r"\s+und\s+",
    re.IGNORECASE,
)
_WIEN_PREFIX_RE: Final = re.compile(
    r"\bWien\s+([A-ZÄÖÜ][\wäöüÄÖÜß\-’']{1,40}(?:\s+[A-ZÄÖÜ][\wäöüÄÖÜß\-’']{1,40})?)"
)

# Sliding-window scan parameters. ``_MAX_STATION_WINDOW`` mirrors the
# value used in :mod:`src.providers.oebb._find_stations_in_text` so the
# two scans behave consistently — a station name that resolves there
# also resolves here. The token-split character class strips arrow /
# slash / dash punctuation that appears in route titles
# (``"A ↔ B"`` / ``"A / B"``) and would otherwise corrupt window joins.
_MAX_STATION_WINDOW: Final = 4
_TOKEN_SPLIT_RE: Final = re.compile(r"[\s/]+")
_NOISE_TOKEN_RE: Final = re.compile(r"^[↔→←↗↘↙↖<>=–—\-«»‹›]+$")
# Single-token chunks that the directory's alias-expansion rules would
# silently collapse into flagship stations. ``Hbf`` aliases to
# ``Wien Hauptbahnhof`` even when the surrounding text is talking about
# a different station ("Verbindungen zum Hbf umgeleitet"); ``Bahnhof`` /
# ``Bf`` likewise; ``Wien`` alone aliases on its own (and would land
# every Vienna-related disruption under the same node). Mirrors the
# ``oebb._GENERIC_STATION_TOKENS`` filter to keep the two scans in sync.
_GENERIC_DIRECTORY_TOKENS: Final = frozenset(
    {
        "hbf",
        "bhf",
        "bf",
        "bahnhof",
        "bahnhst",
        "hauptbahnhof",
        "westbahnhof",
        "westbf",
        "ostbahnhof",
        "ostbf",
        "südbahnhof",
        "suedbahnhof",
        "südbf",
        "suedbf",
        "nordbahnhof",
        "nordbf",
        "station",
        "wien",
        "vor",
        # Audit 2026-09-13: four Badner-Bahn stops up to 5.6 km apart
        # ("Wien Guntramsdorf Lokalbahn (WL)", "Wien Neu Guntramsdorf
        # (WL)", "Wien Möllersdorf (WL)", "Wien Traiskirchen Lokalbahn
        # (WL)") all claim the bare "lokalbahn" alias, so a generic
        # mention collapsed every Badner-Bahn incident onto whichever
        # one won the tie-break (Guntramsdorf) — the same flagship-node
        # skew this set exists to prevent for "hbf".
        "lokalbahn",
    }
)


def _normalise_location(value: str) -> str:
    """Trim, collapse whitespace, and cap *value* at a sensible length."""
    cleaned = " ".join(value.split())
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip()
    return cleaned


def _resolve_via_directory(candidate: str, *, fragment: bool = False) -> str | None:
    """Return the *display* name when *candidate* resolves via the station
    directory, ``None`` otherwise.

    The directory in ``data/stations.json`` is the project's only
    curated source of truth for "is this a real Vienna-network
    station?". A regex-extracted candidate is only accepted as a
    location when :func:`station_info` recognises it (canonical or
    alias). The returned string is the user-facing
    :func:`display_name` (e.g. ``Wien Mitte`` rather than the
    canonical ``Wien Mitte-Landstraße``) so the dashboard renders the
    label operators expect to read.
    """
    cleaned = _normalise_location(candidate)
    if not cleaned:
        return None
    # A *fragment* is a run of words the sliding-window scan cut out of a
    # sentence; it only matches through a station name, never through a code
    # or ID (:func:`station_named_in_text`).
    info = station_named_in_text(cleaned) if fragment else station_info(cleaned)
    if info is None:
        return None
    return display_name(info.name)


def _scan_for_directory_station(haystack: str) -> str | None:
    """Sliding-window scan for a directory-known station in *haystack*.

    Matches longest first within each starting position so that a
    multi-token name (``Wien Mitte``) wins over the substring match
    (``Mitte``) when both resolve, and returns the *first* match in
    source order so the natural reading order of the title/description
    drives the choice. Mirrors
    :func:`src.providers.oebb._find_stations_in_text` semantically but
    is purposefully scoped to the stats-extraction surface (no HTML
    stripping — the callers already pass plain text).

    ``_GENERIC_DIRECTORY_TOKENS`` filters single-token chunks that the
    directory's alias rules silently collapse to flagship stations
    (``Hbf`` → ``Wien Hauptbahnhof`` etc.) — without that filter, every
    ÖBB description that mentions ``Hbf`` would land all incidents under
    a single node and skew the dashboard.
    """
    if not haystack:
        return None
    tokens = [t for t in _TOKEN_SPLIT_RE.split(haystack) if t]
    tokens = [t for t in tokens if not _NOISE_TOKEN_RE.match(t)]
    if not tokens:
        return None
    n = len(tokens)
    window = min(_MAX_STATION_WINDOW, n)
    for start in range(n):
        max_size = min(window, n - start)
        for size in range(max_size, 0, -1):
            chunk_tokens = tokens[start : start + size]
            chunk = " ".join(chunk_tokens)
            if size == 1:
                token_norm = chunk.casefold().rstrip(".:,;")
                if token_norm in _GENERIC_DIRECTORY_TOKENS:
                    continue
                # Drop ultra-short chunks ("S1") to avoid line-token
                # alias collisions; mirrors the 3-letter floor in the
                # ÖBB scan.
                alpha = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", chunk)
                if len(alpha) < 3:
                    continue
            resolved = _resolve_via_directory(chunk.rstrip(".:,;"), fragment=True)
            if resolved:
                return resolved
    return None


def current_location_label(label: str) -> str:
    """*label* of a stored ledger row as :func:`extract_location_name` writes it today.

    The ledger keeps the display name of the day a row was written; when the
    display layer changes (2026-11-02: "St. Pölten Hauptbahnhof" became
    "St. Pölten Hbf", ``display_name``), older rows would count as a second
    place beside the new ones in any analysis by place. The dashboard shows
    no places today, so its output does not change. A label the directory knows is shown through
    the same path as a new row (``display_name(station_info(...).name)``);
    any other label, ``unbekannt`` included, stays as stored.
    """
    info = station_info(label) if label and label != "unbekannt" else None
    return display_name(info.name) if info is not None else label


def extract_location_name(item: dict[str, Any]) -> str:
    """Pick a directory-validated location for *item* or ``"unbekannt"``.

    The strategy is **catalogue-first**: a candidate string is only
    returned when it resolves through :func:`station_info` (the curated
    directory in ``data/stations.json``). Free-form regex matches over
    capitalised tokens were dropped in 2026-05-09 because German nouns
    are all capitalised — the heuristic accepted disruption types like
    ``Demonstration Linie`` / ``Rettungseinsatz Linie`` /
    ``Polizeieinsatz Linie`` as if they were station names, polluting
    the README "Häufigste Störungsorte" table with non-locations.

    Pipeline (return on first hit):

    1. ``" | Haltestelle: ..."`` — WL provider serialises the
       ``relatedStops`` API field into the description with this
       prefix; the first comma-separated entry is the primary stop.
       When the WL stop name is not in the heavy-rail directory we
       still accept it verbatim (subject to the 80-char clamp) because
       the stop list comes from a curated upstream source.
    2. ``" | Station: ..."`` / ``" | Location: ..."`` — WL extras
       fallback (also curated upstream); only accepted when it
       resolves through the directory.
    3. ``"in Richtung X"`` — the Stammstrecke renderer in
       ``scripts/update_stammstrecke_status.py``; only accepted when
       it resolves through the directory.
    4. ``"zwischen X und Y"`` — canonical ÖBB phrasing; only accepted
       when X resolves through the directory.
    5. ``"Wien <Stadtteil>"`` — common in ÖBB / VOR descriptions; only
       accepted when ``"Wien <Stadtteil>"`` resolves through the
       directory.
    6. Sliding-window directory scan over the title + description.
       Filters single-token generic aliases (``Hbf`` etc.) so
       arbitrary mentions of those words do not auto-canonicalise to
       flagship stations.
    7. Final fallback: ``"unbekannt"``. We deliberately do NOT
       fall back to a free-form regex match here — the previous fix
       round demonstrated that any such fallback re-pollutes the
       statistics ledger.

    Steps 1-2 scan up to :data:`MAX_LABELLED_SCAN_CHARS` characters so a
    long provider description cannot push the label out of view; steps
    3-6 stay bounded to the first 1024 characters. See the inline
    comment below for why the two windows differ.

    The function never raises — a malformed item just returns the
    fallback string.
    """
    title = str(item.get("title") or "")
    description = str(item.get("description") or "")
    haystack = " ".join(part for part in (title, description) if part)
    if not haystack:
        return "unbekannt"

    # Two search windows, deliberately different in size.
    #
    # ``labelled_haystack`` feeds the *anchored* label regexes
    # (``_HALTESTELLE_RE`` / ``_LABELED_LOCATION_RE``). Those match on a
    # literal ``| <Label>:`` prefix and cap their capture at 200 chars,
    # so they stay linear-time on long input and need no tight bound.
    # They must see the whole description: the WL provider appends
    # ``" | Haltestelle: …"`` to the *end* of the built description
    # (``src/providers/wl_fetch.py`` — ``desc += f" | Haltestelle: …"``),
    # and 69% of live WL descriptions exceed 1024 chars (observed max
    # 4972 on 2026-09-13). Under the previous single 1024-char window
    # that marker fell outside the haystack and the most authoritative
    # signal the WL API gives us — the curated ``relatedStops`` list —
    # was silently dropped, landing the incident in the dashboard's
    # "Häufigste Störungsorte" table as ``unbekannt``.
    #
    # ``haystack`` keeps the historical 1024-char bound for the
    # *unanchored* heuristics and the sliding-window scan below. Those
    # are the ReDoS-sensitive ones, and widening them would also hurt
    # precision: the scan returns the first directory hit in reading
    # order, which deep inside a long description is typically a street
    # mentioned in passing rather than the affected stop.
    labelled_haystack = haystack[:MAX_LABELLED_SCAN_CHARS]
    haystack = haystack[:1024]

    halt = _HALTESTELLE_RE.search(labelled_haystack)
    if halt:
        first_stop = halt.group(1).split(",")[0].strip()
        resolved = _resolve_via_directory(first_stop)
        if resolved:
            return resolved
        normalised = _normalise_location(first_stop)
        if normalised:
            return normalised

    labeled = _LABELED_LOCATION_RE.search(labelled_haystack)
    if labeled:
        first_label = labeled.group(1).split(",")[0].strip()
        resolved = _resolve_via_directory(first_label)
        if resolved:
            return resolved

    richtung = _RICHTUNG_RE.search(haystack)
    if richtung:
        target = richtung.group(1).strip()
        resolved = _resolve_via_directory(target)
        if resolved:
            return resolved

    between = _BETWEEN_RE.search(haystack)
    if between:
        endpoint = _normalise_location(between.group(1).strip())
        resolved = _resolve_via_directory(endpoint)
        if resolved:
            return resolved

    wien = _WIEN_PREFIX_RE.search(haystack)
    if wien:
        suffix = wien.group(1).strip()
        if suffix:
            resolved = _resolve_via_directory(f"Wien {suffix}")
            if resolved:
                return resolved

    scan = _scan_for_directory_station(haystack)
    if scan:
        return scan

    return "unbekannt"


__all__ = [
    "MAX_LABELLED_SCAN_CHARS",
    "_BETWEEN_RE",
    "_GENERIC_DIRECTORY_TOKENS",
    "_HALTESTELLE_RE",
    "_LABELED_LOCATION_RE",
    "_MAX_STATION_WINDOW",
    "_NOISE_TOKEN_RE",
    "_RICHTUNG_RE",
    "_TOKEN_SPLIT_RE",
    "_WIEN_PREFIX_RE",
    "_normalise_location",
    "_resolve_via_directory",
    "_scan_for_directory_station",
    "current_location_label",
    "extract_location_name",
]
