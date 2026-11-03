"""Strict Wien filter for ÖBB notices; owns ``OEBB_ONLY_VIENNA``.

Moved verbatim from ``src/providers/oebb.py`` (700-line limit); the facade
``src.providers.oebb`` re-exports the names other code uses.
"""

from __future__ import annotations

import html
import re

from ..utils.env import get_bool_env
from ..utils.stations import (
    StationInfo,
    station_info,
    station_named_in_text,
    text_has_vienna_connection,
)
from .oebb_routes import (
    _NON_STATION_FIRST_WORDS,
    _extract_routes,
    _is_category,
    _strip_oebb_prefixes,
)
from .oebb_scope import DROP_NOT_VIENNA, _facility_or_weather_reason


# Optional strenger Filter: Nur Meldungen mit Endpunkten in Wien behalten.
# Aktiviert durch Umgebungsvariable ``OEBB_ONLY_VIENNA`` ("1"/"true" vs "0"/"false", case-insens).
OEBB_ONLY_VIENNA = get_bool_env("OEBB_ONLY_VIENNA", False)
# Tokens that can never be part of a station name (arrow/separator
# glyphs). Hoisted to module scope to match every other regex here and
# the identical constant in ``src/utils/stats.py``; previously this was
# recompiled on each ``_find_stations_in_text`` call.
_NOISE_TOKEN_RE = re.compile(r"^[↔→←↗↘↙↖<>=–—\-«»‹›]+$")


def _classify_endpoint(name: str) -> tuple[StationInfo | None, str]:
    """Look up *name* and return ``(info, category)``.

    Categories are one of ``vienna``, ``pendler``, ``distant`` (known but not
    relevant) or ``unknown``.
    """
    info = station_info(name)
    if info is None:
        return None, "unknown"
    if info.in_vienna:
        return info, "vienna"
    if info.pendler:
        return info, "pendler"
    return info, "distant"


def _route_is_wien_relevant(name_a: str, name_b: str) -> bool:
    """Strict-spec route check.

    Per project specification a route is relevant if both endpoints are known
    Vienna or Pendler stations and at least one of them is in Vienna. Pendler
    ↔ Pendler routes and routes with unknown/distant endpoints are excluded.

    When ``OEBB_ONLY_VIENNA`` is enabled, the rule is tightened further: both
    endpoints must lie inside Vienna.
    """
    _, cat_a = _classify_endpoint(name_a)
    _, cat_b = _classify_endpoint(name_b)
    if OEBB_ONLY_VIENNA:
        return cat_a == "vienna" and cat_b == "vienna"
    if cat_a not in ("vienna", "pendler") or cat_b not in ("vienna", "pendler"):
        return False
    return cat_a == "vienna" or cat_b == "vienna"


# Word tokens that may legitimately appear in an ÖBB title alongside
# station names without indicating a second endpoint. Anything not in this
# set AND not part of a known station is treated as a suspect unknown
# station mention.
_TITLE_NOISE_WORDS = frozenset({
    # Categories / event types (also covered by NON_LOCATION_PREFIXES, kept
    # explicit for clarity)
    "bauarbeiten",
    "bauarbeit",
    "störung",
    "stoerung",
    "verspätung",
    "verspaetung",
    "verspätungen",
    "verspaetungen",
    "ausfall",
    "ausfälle",
    "ausfaelle",
    "zugausfall",
    "umleitung",
    "haltausfall",
    "schienenersatzverkehr",
    "ersatzverkehr",
    "sev",
    "polizeieinsatz",
    "rettungseinsatz",
    "personenschaden",
    "fahrzeugschaden",
    "weichenstörung",
    "weichenstoerung",
    "signalstörung",
    "signalstoerung",
    "info",
    "information",
    "hinweis",
    "achtung",
    # Bahnhof-Suffixe
    "hbf",
    "bf",
    "bhf",
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
    # German articles / prepositions / conjunctions
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einer",
    "einem",
    "eines",
    "und",
    "oder",
    "aber",
    "sowie",
    "in",
    "im",
    "an",
    "am",
    "auf",
    "aus",
    "bei",
    "mit",
    "von",
    "vom",
    "zu",
    "zum",
    "zur",
    "nach",
    "vor",
    "über",
    "ueber",
    "via",
    "ab",
    "bis",
    "wegen",
    "aufgrund",
    "während",
    "waehrend",
    "zwischen",
    "neue",
    "neuer",
    "neues",
    "alle",
    "alle.",
    "kein",
    "keine",
    "halt",
    # Time/calendar words that occasionally surface in titles
    "heute",
    "morgen",
    "wochenende",
    "wochenenden",
    "feiertag",
    "feiertage",
    # Weather words (Sturm/Wetter etc.) — not station names; mentioning
    # them after a known Vienna station shouldn't drag the message into
    # the implicit-route heuristic.
    "sturm",
    "sturmschaden",
    "sturmwarnung",
    "unwetter",
    "gewitter",
    "hochwasser",
    "wetter",
    "wetterlage",
    "wettersituation",
    "glatteis",
    "schnee",
    "schneefall",
    "schneefälle",
    "schneefaelle",
    "regen",
    "wind",
    "hagel",
    "frost",
    "murenabgang",
    "lawinengefahr",
    "lawine",
    # Facility / generic location words (already in _NON_STATION_FIRST_WORDS
    # but we list them here too for the title-residual check)
    "aufzug",
    "lift",
    "fahrtreppe",
    "rolltreppe",
    "bahnsteig",
    "gleis",
    "wagen",
    "ausgang",
    "eingang",
    "halle",
    "platz",
    "sektor",
    "zone",
    # German short forms
    "b",
    "u",
    "s",
    # Generic transit-meta words that shouldn't be misread as endpoints
    "linie",
    "linien",
    "strecke",
    "strecken",
    "verbindung",
    "verbindungen",
    "abschnitt",
    "abschnitte",
    "bereich",
    "bereiche",
    "richtung",
    "fahrtrichtung",
    "haltestelle",
    "haltestellen",
    "station",
    "stationen",
    "betrieb",
    "verkehr",
    "fahrgäste",
    "fahrgaeste",
    "fahrt",
    "fahrten",
    "zug",
    "zuege",
    "züge",
    "fernverkehr",
    "fernverkehrszüge",
    "fernverkehrszuege",
    "nahverkehr",
    "nahverkehrszüge",
    "nahverkehrszuege",
    # Infrastructure-work nouns that frequently TRAIL a single Wien/Pendler
    # station title ("Wien Meidling Stellwerk", "Wien Praterstern
    # Umbauarbeiten"). They are common German nouns, never station names, so
    # the title-residual heuristic must not misread them as an implicit
    # unknown second endpoint and drop an otherwise-relevant single-station
    # message (bug b2).
    "stellwerk",
    "stellwerke",
    "sanierung",
    "sanierungsarbeiten",
    "umbau",
    "umbauarbeiten",
    "ausbau",
    "ausbauarbeiten",
    "erneuerung",
    "erneuerungsarbeiten",
    "modernisierung",
    "reparatur",
    "reparaturarbeiten",
    "wartung",
    "wartungsarbeiten",
    "instandhaltung",
    "instandhaltungsarbeiten",
    "ertüchtigung",
    "ertuechtigung",
    "brückenarbeiten",
    "brueckenarbeiten",
    "gleisarbeiten",
    "weichenarbeiten",
    "oberleitungsarbeiten",
    "vorplatz",
    "bahnhofsvorplatz",
    "reisezentrum",
})


def _title_has_unknown_endpoint(title: str) -> bool:
    """Heuristic: detect titles like ``Wiener Neustadt Hauptbahnhof Semmering``
    that pair a known Wien/Pendler station with a station-name-shaped token
    that doesn't resolve against the directory.

    Real ÖBB titles list disrupted endpoints directly (``<Station1>
    <Station2>``); when one of those stations is missing from
    stations.json the strict route check never sees the connection and the
    single-station fall-through used to keep the message even though the
    real route is Pendler↔Distant or Wien↔Distant.

    Returns ``True`` when the title likely encodes such an implicit route
    so the caller can drop the message. Conservative: only fires when at
    least one Wien/Pendler station is present in the title AND the
    remaining content has at least one capitalized, non-stop-word token of
    three or more letters.
    """
    if not title:
        return False
    if "↔" in title:
        # Explicit-route titles are handled by _extract_routes; do not
        # second-guess them here.
        return False

    stripped = _strip_oebb_prefixes(title)
    # Remove any leading category prefix like "Bauarbeiten:" / "Hinweis:".
    while True:
        match = re.match(r"^\s*([^:]+):\s*", stripped)
        if not match:
            break
        prefix = match.group(1).strip()
        if not _is_category(prefix):
            break
        stripped = stripped[match.end():]

    if not stripped:
        return False

    # Collect every textual span that resolves to a known station so we
    # can subtract it from the title. We use a sliding-window approach
    # similar to _find_stations_in_text but record start/end offsets.
    tokens_re = re.finditer(r"\S+", stripped)
    tokens = [(m.start(), m.end(), m.group(0)) for m in tokens_re]
    if not tokens:
        return False

    used = [False] * len(tokens)
    has_relevant_station = False
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for idx in range(len(tokens) - size + 1):
            if any(used[idx : idx + size]):
                continue
            chunk = " ".join(t[2] for t in tokens[idx : idx + size])
            chunk_clean = chunk.strip(" ,.;:()[]")
            if size == 1:
                token_norm = chunk_clean.casefold().rstrip(".:,;")
                if token_norm in _GENERIC_STATION_TOKENS:
                    continue
            # A fragment of the title, not a name (``station_named_in_text``).
            info = station_named_in_text(chunk_clean)
            if info is None:
                continue
            if info.in_vienna or info.pendler:
                has_relevant_station = True
            for j in range(idx, idx + size):
                used[j] = True

    if not has_relevant_station:
        return False

    # The implicit second endpoint can only sit AFTER the last known
    # station match — otherwise we'd flag tokens that are clearly part of
    # the message preamble (e.g. "Sturm im Raum Wien" has "Sturm" before
    # the known "Wien" and is just a generic Vienna weather notice, not a
    # Sturm-↔-Wien route).
    last_known_idx = -1
    for idx, flag in enumerate(used):
        if flag:
            last_known_idx = idx

    for idx in range(last_known_idx + 1, len(tokens)):
        if used[idx]:
            continue
        raw = tokens[idx][2]
        clean = raw.strip(" .,;:()[]/")
        if not clean:
            continue
        norm = clean.casefold()
        if norm in _TITLE_NOISE_WORDS:
            continue
        if norm in _GENERIC_STATION_TOKENS:
            continue
        if norm in _NON_STATION_FIRST_WORDS:
            continue
        alpha = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", clean)
        if len(alpha) < 3:
            continue
        if not clean[0:1].isupper():
            continue
        # Found a station-name-shaped token after the last known match —
        # treat as an implicit second endpoint of an unknown station.
        return True

    return False


def _is_relevant(title: str, description: str) -> bool:
    """Decide whether an ÖBB message is relevant for Wien-Pendler.

    Strict rules:

    1. **Connection messages (A ↔ B / "zwischen X und Y")** – at least one
       extracted route must be Vienna ↔ Vienna or Vienna ↔ Pendler. If the
       message describes routes but none of them is Wien-relevant (e.g.
       Pendler ↔ Pendler, Wien ↔ Distant, or unknown endpoints), the message
       is dropped.
    2. **Single-station / general messages** – if no explicit route can be
       parsed:

       a. When at least one *known* distant station (in stations.json with
          ``in_vienna=False`` and ``pendler=False``) is mentioned alongside
          relevant stations, the message almost always describes a
          Wien↔Distant or Pendler↔Distant route — drop it. Standalone
          "Aufzug defekt am Wien Hbf" messages never drag in München names,
          so this rule is safe in practice.
       b. Otherwise, the message must mention at least one Vienna or
          Pendler station. If only distant stations are mentioned, drop.
       c. As a final fall-back, use the generic Vienna-text heuristic for
          U-Bahn references and the like.

    Before any of the above runs, messages whose primary topic is a
    broken facility (Aufzug, Lift, Fahrtreppe, …) or a standalone weather
    warning (Sturm, Wetterlage, …) are dropped: they are explicitly out
    of scope per project spec. Mixed transit messages that merely mention
    weather/facility as cause or side-effect still go through.
    """
    return _drop_reason(title, description) is None


def _drop_reason(title: str, description: str) -> str | None:
    """Why :func:`_is_relevant` drops the message, ``None`` when it stays.

    The reason is the label in ``data/raw/oebb/verworfen.json``.
    """
    reason = _facility_or_weather_reason(title, description)
    if reason is not None:
        return reason
    return None if _serves_vienna(title, description) else DROP_NOT_VIENNA


def _serves_vienna(title: str, description: str) -> bool:
    """Rules 1 and 2 of :func:`_is_relevant`: the Wien filter proper."""
    routes = _extract_routes(title, description)

    if routes:
        for raw_a, raw_b in routes:
            if _route_is_wien_relevant(raw_a, raw_b):
                return True
        return False

    # No identifiable connection — single-station / general announcement path.
    text = f"{title} {description}"
    found_stations = _find_stations_in_text(text)

    has_relevant = False
    has_distant = False
    for s in found_stations:
        info = station_info(s)
        if not info:
            continue
        if info.in_vienna or (info.pendler and not OEBB_ONLY_VIENNA):
            has_relevant = True
        else:
            has_distant = True

    # Step 2a: a known-distant station mention together with anything else
    # almost always implies a route into the distant station. Wien↔Distant,
    # Pendler↔Distant and Distant↔Distant routes are all not relevant per
    # spec, so drop the message even when a Wien station is co-mentioned.
    if has_distant:
        return False

    # Step 2b: same idea for stations that the directory doesn't carry yet —
    # if the title pairs a known Wien/Pendler station with a capitalized
    # token that isn't a stop word and isn't part of a known station name,
    # treat the pairing as an implicit Pendler↔Unknown / Wien↔Unknown
    # route. This catches Pendler↔Distant Fernverkehr items like
    # "Bauarbeiten: Wiener Neustadt Hauptbahnhof Semmering" until the
    # missing stations are added to data/stations.json (bug M).
    if has_relevant and _title_has_unknown_endpoint(title):
        return False

    if has_relevant:
        return True

    # OEBB_ONLY_VIENNA narrows the fallback to text-detected Vienna references.
    if OEBB_ONLY_VIENNA:
        return False

    return text_has_vienna_connection(text)

# ---------------- Region helpers ----------------
_MAX_STATION_WINDOW = 4

# Single-token chunks that should not be treated as stations on their own
# (they would otherwise alias-match high-profile stations such as "Wien
# Hauptbahnhof" via the directory's expansion rules).
_GENERIC_STATION_TOKENS = frozenset({
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
    # German preposition that aliases to "Wien Hauptbahnhof" via several
    # "(VOR)"-suffixed entries in stations.json. Without skipping it, words
    # like "vor Reiseantritt" inside an ÖBB description silently classify
    # the message as Wien-relevant.
    "vor",
})


def _find_stations_in_text(blob: str) -> list[str]:
    """
    Scans text for known station names using a sliding window.
    Returns a list of unique canonical station names found.
    """
    if not blob:
        return []
    # Strip HTML tags and unescape entities — otherwise tokens like
    # "Hbf<" (left over from "<b>Graz Hbf</b>") slip through the
    # _GENERIC_STATION_TOKENS filter and canonicalise to flagship
    # stations through their alias rules.
    # Order matters: unescape first so entity-encoded ``&lt;b&gt;`` becomes
    # ``<b>`` and the tag strip then catches it. See sibling sites for
    # the full rationale.
    cleaned = html.unescape(blob)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Use whitespace splitting to preserve punctuation like '.' in 'St. Pölten'
    tokens = [t for t in re.split(r"[\s/]+", cleaned) if t]
    if not tokens:
        return []

    # Drop tokens that can never be part of a station name. The arrow
    # characters appear in route titles ("A ↔ B"), and including them in
    # sliding-window chunks let canonical_name silently expand "Hbf ↔"
    # into "Wien Hauptbahnhof" via the directory's alias-expansion rules.
    tokens = [t for t in tokens if not _NOISE_TOKEN_RE.match(t)]
    if not tokens:
        return []

    found = set()
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for idx in range(len(tokens) - size + 1):
            chunk_tokens = tokens[idx : idx + size]
            chunk = " ".join(chunk_tokens)
            chunk_alpha = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", chunk)
            if size == 1:
                # Skip single-token chunks that are either generic
                # aliases ("Hbf", "Bahnhof", …) or two-letter
                # abbreviations like "SG"/"NÖ" — these falsely match
                # flagship stations through the directory's alias
                # expansions.
                token_norm = chunk.casefold().rstrip(".:,;")
                if token_norm in _GENERIC_STATION_TOKENS:
                    continue
                if OEBB_ONLY_VIENNA and token_norm in ("wien", "vienna"):
                    # The bare area word "Wien"/"Vienna" canonicalises to a
                    # flagship station ("Wien Hauptbahnhof") — a phantom
                    # mention. In strict Vienna-only mode that must not seed
                    # a relevant station from a generic "ab/bis Wien" notice
                    # (bug b10). Default mode (flag off) is unaffected.
                    continue
                if len(chunk_alpha) < 3:
                    continue
            # Only a name, never a code or a fragment, counts as a mention:
            # "Stellwerkstörung am Bahnhof" is no WL stop "Am Bahnhof"
            # (``station_named_in_text``).
            info = station_named_in_text(chunk)
            if info is not None:
                found.add(info.name)

    # Filter out shorter overlapping matches
    sorted_found = sorted(list(found), key=len, reverse=True)
    filtered: list[str] = []
    for station in sorted_found:
        if not any(station in longer_station for longer_station in filtered):
            filtered.append(station)

    return sorted(filtered)
