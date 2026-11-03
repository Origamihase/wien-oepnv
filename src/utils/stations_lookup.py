"""Station lookup by name and ID, display names, names in free text, ÖBB lines.

Moved verbatim from ``src/utils/stations.py`` (700-line rule, 2026-11-02);
the public facade stays ``src.utils.stations``. Builds on
``stations_data`` and ``stations_normalize``; this module must not import
the facade.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from .stations_data import (
    MAX_STATION_LINES_FILE_BYTES,
    StationInfo,
    WLStop,
    _STATION_LINES_PATH,
    _read_capped_json,
    _station_entries,
)
from .stations_normalize import (
    _MatchStrength,
    _coerce_lat,
    _coerce_lon,
    _iter_aliases_with_strength,
    _name_owns_alias,
    _normalize_token,
    _source_token_set,
)

# The logger keeps the name of the facade ``src.utils.stations`` (log
# configuration and caplog filters in the tests match on it).
logger = logging.getLogger("src.utils.stations")


# Canonical station names that should appear under a different label in the
# user-facing feed output.  ÖBB lists ``Wien Mitte-Landstraße`` as the official
# station name, but the colloquial ``Wien Mitte`` reads cleaner in a 2-line TV
# layout — keep the directory entry authoritative and only override at render
# time.
_FEED_DISPLAY_OVERRIDES: dict[str, str] = {
    "Wien Mitte-Landstraße": "Wien Mitte",
}


def _log_alias_collision(
    reported: set[str],
    key: str,
    alias_text: str,
    loser: StationInfo,
    winner: StationInfo,
) -> None:
    """Log a contested alias key once, at DEBUG.

    A separate function rather than an inline guard so ``_station_lookup``
    does not gain a branch: it sits on the C901 baseline at 29 and that
    baseline is meant to ratchet down, never up.
    """
    if key in reported:
        return
    reported.add(key)
    logger.debug(
        "Duplicate station alias %r normalized to %r for %s conflicts with %s",
        alias_text,
        key,
        loser.name,
        winner.name,
    )


@lru_cache(maxsize=1)
def _station_lookup() -> dict[str, StationInfo]:
    """Return a mapping from normalized aliases to :class:`StationInfo` records.

    The internal mapping carries match-strength alongside each record so
    that a later entry whose alias matches via a stronger class
    (:attr:`_MatchStrength.IDENTITY`) can evict an earlier weaker match
    (:attr:`_MatchStrength.TEXT`). The strength annotation is dropped from
    the returned mapping; only :class:`StationInfo` is exposed.
    """

    mapping: dict[str, tuple[StationInfo, _MatchStrength]] = {}
    # Normalized keys already reported as contested, so one collision costs
    # one log line instead of one per cosmetic alias variant.
    reported_collisions: set[str] = set()

    for entry in _station_entries():
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        code_raw = entry.get("bst_code")
        code = str(code_raw).strip() if code_raw is not None else ""
        wl_diva_raw = entry.get("wl_diva")
        wl_diva = str(wl_diva_raw).strip() if wl_diva_raw is not None else ""
        vor_id_raw = entry.get("vor_id") or entry.get("id")
        vor_id = str(vor_id_raw).strip() if vor_id_raw is not None else ""

        # Identity-class aliases: authoritative IDs that uniquely identify
        # this station. A match against any of these outranks any text alias.
        identity_aliases: set[str] = set()
        if wl_diva:
            identity_aliases.add(wl_diva)
        if vor_id:
            identity_aliases.add(vor_id)

        # Text-class aliases: the explicit ``aliases`` list and stop names.
        # Conflicts between two text matches fall through to the historical
        # source-based tie-break below.
        text_aliases: set[str] = set()
        aliases_field = entry.get("aliases")
        if isinstance(aliases_field, list):
            for alias in aliases_field:
                if alias is None:
                    continue
                alias_text = str(alias).strip()
                if alias_text:
                    text_aliases.add(alias_text)

        stop_records: list[WLStop] = []
        stops_field = entry.get("wl_stops")
        if isinstance(stops_field, list):
            for stop in stops_field:
                if not isinstance(stop, dict):
                    continue
                stop_id_raw = stop.get("stop_id")
                stop_id = str(stop_id_raw).strip() if stop_id_raw is not None else ""
                name_raw = stop.get("name")
                stop_name = str(name_raw).strip() if name_raw is not None else ""
                latitude = _coerce_lat(stop.get("latitude"))
                longitude = _coerce_lon(stop.get("longitude"))
                if stop_id:
                    # Stop IDs are authoritative and rank as IDENTITY.
                    identity_aliases.add(stop_id)
                if stop_name:
                    text_aliases.add(stop_name)
                stop_records.append(
                    WLStop(
                        stop_id=stop_id,
                        name=stop_name or None,
                        latitude=latitude,
                        longitude=longitude,
                    )
                )
        station_latitude = _coerce_lat(entry.get("latitude") or entry.get("lat"))
        station_longitude = _coerce_lon(entry.get("longitude") or entry.get("lon"))
        source_text = str(entry.get("source") or "")
        base_record = StationInfo(
            name=name,
            in_vienna=bool(entry.get("in_vienna")),
            pendler=bool(entry.get("pendler")),
            wl_diva=wl_diva or None,
            wl_stops=tuple(stop_records),
            vor_id=vor_id or None,
            latitude=station_latitude,
            longitude=station_longitude,
            source=source_text or None,
        )
        vor_name_raw = entry.get("vor_name")
        vor_name = str(vor_name_raw).strip() if isinstance(vor_name_raw, str) else ""
        for alias_text, strength in _iter_aliases_with_strength(
            name,
            code or None,
            identity_extras=identity_aliases,
            text_extras=text_aliases,
        ):
            alias_lower = alias_text.casefold()
            alias_is_numeric = alias_text.isdigit()
            alias_mentions_vor = "vor" in alias_lower
            alias_matches_vor_id = bool(vor_id and alias_is_numeric and alias_text == vor_id)
            use_vor_label = bool(vor_name and (alias_matches_vor_id or alias_mentions_vor))

            alias_record = base_record
            if use_vor_label:
                alias_record = StationInfo(
                    name=vor_name,
                    in_vienna=base_record.in_vienna,
                    pendler=base_record.pendler,
                    wl_diva=base_record.wl_diva,
                    wl_stops=base_record.wl_stops,
                    vor_id=base_record.vor_id,
                    latitude=base_record.latitude,
                    longitude=base_record.longitude,
                    source="vor",
                )

            key = _normalize_token(alias_text)
            if not key:
                continue
            existing = mapping.get(key)
            if existing is None:
                mapping[key] = (alias_record, strength)
                continue
            existing_record, existing_strength = existing

            if existing_record == alias_record or existing_record.name == alias_record.name:
                continue

            # Match-strength precedence: an IDENTITY-class match by one
            # station outranks a TEXT-class match by another station for
            # the same token. This prevents a foreign station's aliases
            # entry from shadowing the rightful ID-bearing station (the
            # root cause of the '900100' bug, see #1082).
            if strength > existing_strength:
                mapping[key] = (alias_record, strength)
                continue
            if strength < existing_strength:
                continue

            # Equal strength: fall back to the historical source-based and
            # name-token-based tie-break. Source comparisons go through
            # _source_token_set so that combined values like "google_places,vor"
            # don't accidentally match the pure-"vor" branch — a record only
            # counts as "VOR" when VOR is its sole declared source.
            existing_tokens = _source_token_set(existing_record.source)
            record_tokens = _source_token_set(alias_record.source)
            existing_is_vor = existing_tokens == frozenset({"vor"})
            record_is_vor = record_tokens == frozenset({"vor"})
            existing_is_wl = existing_tokens == frozenset({"wl"})
            record_is_wl = record_tokens == frozenset({"wl"})

            alias_token = _normalize_token(alias_text)

            # Eponymous-name precedence: the station the alias is named after
            # wins it over a neighbour that merely cross-references the name.
            # ``_name_owns_alias`` also matches the bare name (``Wien`` prefix /
            # ``(WL)`` suffix stripped), so "Wien Taborstraße (WL)" claims
            # "taborstrasse" instead of the earlier-registered "Heinestraße"
            # (and "Wien Kagran" instead of "Betriebshof Kagran").
            record_named = _name_owns_alias(alias_record.name, alias_token)
            existing_named = _name_owns_alias(existing_record.name, alias_token)

            if record_named and not existing_named:
                mapping[key] = (alias_record, strength)
                continue
            if existing_named and not record_named:
                continue

            if existing_record.vor_id and alias_record.vor_id and existing_record.vor_id == alias_record.vor_id:
                if alias_is_numeric or alias_mentions_vor:
                    if record_is_vor and not existing_is_vor:
                        mapping[key] = (alias_record, strength)
                else:
                    if record_is_wl and not existing_is_wl:
                        mapping[key] = (alias_record, strength)
                continue

            if existing_is_vor and not record_is_vor:
                mapping[key] = (alias_record, strength)
                continue
            if record_is_vor and not existing_is_vor:
                continue
            # DEBUG, and once per normalized KEY rather than once per alias
            # surface. Measured on the live directory (audit 2026-09-12,
            # Befund 6): 111 WARNING lines per process for 22 distinct keys,
            # because the alias generator emits up to seven cosmetic variants
            # of one fact — "Bhf. Hütteldorf", "Bahnhof Bhf. Hütteldorf",
            # "Bhf. Hütteldorf Bahnhof", … — each colliding separately.
            #
            # Harmless is not the same as unguarded. What the losing alias
            # costs is a resolution: ``station_info`` feeds ``is_in_vienna``,
            # which decides whether an ÖBB disruption reaches the feed at all.
            # Across all 44 (key, loser, winner) triples in the live data the
            # two sides agree on ``in_vienna``, so today no collision can
            # change an answer — but nothing in this loop checks that. The
            # check lives in the validator instead
            # (``_find_alias_collision_issues``), where it can fail a run;
            # a log line never could.
            _log_alias_collision(
                reported_collisions, key, alias_text, alias_record, existing_record
            )

    # Drop the strength annotation from the public mapping.
    return {key: record for key, (record, _) in mapping.items()}


# A slash standing for "an der"/"am" between two words, as in "Bruck/Leitha"
# or "Neusiedl/See" (see ``_candidate_values``).
_SLASH_QUALIFIER_RE = re.compile(r"(?<=[A-Za-zÄÖÜäöüß])/(?=[A-ZÄÖÜ][a-zäöüß])")

# The river, region or state ÖBB appends to a place name the directory knows
# bare: "Mistelbach/Zaya", "Wolkersdorf im Weinviertel", "Traisen NÖ",
# "Wolfsberg in Ktn" (see ``station_info``). Only at the end of the name
# (before an optional "Bahnhof"), so a pair like "Wien Westbahnhof/Wien Hbf"
# keeps both parts.
_PLACE_QUALIFIER_RE = re.compile(
    r"(?:(?<=[A-Za-zäöüß])/[A-ZÄÖÜ][a-zäöüß]+"
    r"|\s+im\s+[A-ZÄÖÜ][a-zäöüß]+"
    r"|\s+(?:NÖ|OÖ|in\s+(?:Stmk|Ktn|Tirol|Osttirol|Vlbg|Bgld)))"
    r"(?=(?:\s+(?:Bahnhof|Bahnhst|Hbf|Bf))?\s*$)"
)


def _candidate_values(value: str) -> list[str]:
    """Generate possible textual variants for *value* supplied by the caller."""

    candidates: list[str] = []
    seen: set[str] = set()
    for variant in (
        value,
        value.strip(),
        re.sub(r"\s*\([^)]*\)\s*", " ", value),
        value.replace("-", " "),
        value.replace("/", " "),
    ):
        cleaned = re.sub(r"\s{2,}", " ", variant.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    extras: list[str] = []
    for variant in candidates:
        if re.search(r"\b(?:bei|b[./-]?)\s*wien\b", variant, re.IGNORECASE):
            stripped = re.sub(
                r"\b(?:bei|b[./-]?)\s*wien\b", "", variant, flags=re.IGNORECASE
            )
            extras.append(stripped)

        # Expand common station abbreviations like "Hbf"/"Bhf"/"Bf" to improve
        # canonical lookups for ÖBB titles that use shorthand spellings.
        if re.search(r"\bHbf\b", variant, re.IGNORECASE):
            extras.append(re.sub(r"\bHbf\b", "Hauptbahnhof", variant, flags=re.IGNORECASE))
        if re.search(r"\bBhf\b", variant, re.IGNORECASE):
            extras.append(re.sub(r"\bBhf\b", "Bahnhof", variant, flags=re.IGNORECASE))
        if re.search(r"\bBf\b", variant, re.IGNORECASE):
            extras.append(re.sub(r"\bBf\b", "Bahnhof", variant, flags=re.IGNORECASE))
        # ÖBB writes "an der"/"am" as a slash: "Bruck/Leitha", "Tulln/Donau".
        # The directory knows "Bruck an der Leitha", so a route Wien ↔
        # Bruck/Leitha classified as Wien ↔ unknown and the ÖBB message was
        # dropped. Tried after every other variant, so a name that resolves
        # as written keeps its station.
        if _SLASH_QUALIFIER_RE.search(variant):
            extras.append(_SLASH_QUALIFIER_RE.sub(" an der ", variant))
            extras.append(_SLASH_QUALIFIER_RE.sub(" am ", variant))

    for extra in extras:
        cleaned = re.sub(r"\s{2,}", " ", extra.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    return candidates


@lru_cache(maxsize=1024)
def station_by_oebb_id(bst_id: int | str) -> str | None:
    """Return the station name for a given ÖBB station ID (bst_id)."""
    target_id = str(bst_id).strip()

    for entry in _station_entries():
        current_id = entry.get("bst_id")
        if current_id is not None and str(current_id).strip() == target_id:
            return entry.get("name")
    return None


@lru_cache(maxsize=2048)
def canonical_name(name: str) -> str | None:
    """Return the canonical ÖBB station name for *name* or ``None`` if unknown."""

    info = station_info(name)
    return info.name if info else None


def display_name(name: str | None) -> str:
    """Return the user-facing display name for a station name.

    Strips the ``(VOR)`` provenance suffix and applies feed-display
    overrides (e.g. ``Wien Mitte-Landstraße`` → ``Wien Mitte``).  Falls back
    to the input — trimmed, with any trailing ``(VOR)`` removed — when the
    name is not in the override map.
    """

    if not name:
        return ""
    cleaned = re.sub(r"\s+\(VOR\)$", "", name).strip()
    return _main_station_form(_FEED_DISPLAY_OVERRIDES.get(cleaned, cleaned))


# A main station keeps "Hauptbahnhof" behind a short place name and "Hbf"
# behind a long one (operator decision 2026-11-02: "Wenn der Ort länger als
# sieben Zeichen lang ist, wird Hauptbahnhof abgekürzt"): "Wien
# Hauptbahnhof", "Linz Hauptbahnhof", "Graz Hauptbahnhof", but "St. Pölten
# Hbf", "Wiener Neustadt Hbf", "Salzburg Hbf". The directory writes either
# form ("Linz Hbf", "St. Pölten Hauptbahnhof"); only the display changes.
_MAIN_STATION_RE = re.compile(r"^(?P<place>.+?)\s+(?:Hauptbahnhof|Hbf\.?)$")
_MAIN_STATION_PLACE_MAX = 7


_MAIN_STATION_IN_TEXT_RE = re.compile(r"(?P<words>(?:[A-ZÄÖÜ][\w.\-]*\s+)+)(?P<kind>Hauptbahnhof|Hbf)\b\.?")


def main_station_names(text: str) -> str:
    """*text* with every main station of the directory in its display form (see above).

    For titles written before the rule (the ÖBB cache holds "St. Pölten
    Hauptbahnhof" until its next refresh). Only a name the directory knows
    changes, its longest form in front of "Hauptbahnhof"/"Hbf": "Bauarbeiten
    Graz Hbf" keeps "Bauarbeiten" and shows "Bauarbeiten Graz Hauptbahnhof".
    """

    def shown(match: re.Match[str]) -> str:
        words = match["words"].split()
        for start in range(len(words)):
            place = " ".join(words[start:])
            if station_info(f"{place} Hbf") is None and station_info(f"{place} Hauptbahnhof") is None:
                continue
            lead = " ".join(words[:start])
            return f"{lead} {_main_station_form(f'{place} Hbf')}".lstrip()
        return match[0]

    return _MAIN_STATION_IN_TEXT_RE.sub(shown, text)


def _main_station_form(name: str) -> str:
    """*name* with "Hauptbahnhof" or "Hbf" by the length of its place (see above)."""
    match = _MAIN_STATION_RE.match(name)
    if match is None:
        return name
    place = match["place"]
    return f"{place} {'Hbf' if len(place) > _MAIN_STATION_PLACE_MAX else 'Hauptbahnhof'}"


@lru_cache(maxsize=2048)
def station_info(name: str) -> StationInfo | None:
    """Return :class:`StationInfo` for *name* or ``None`` if the station is unknown."""

    lookup = _station_lookup()
    if not lookup:
        return None

    candidates = _candidate_values(name)
    for candidate in candidates:
        key = _normalize_token(candidate)
        if not key:
            continue
        info = lookup.get(key)
        if info:
            return info
    # Last resort: the place name without the qualifier ÖBB appends. On
    # 2026-10-04 "Wien Leopoldau => Mistelbach/Zaya" (S2 Schienenersatzverkehr)
    # was dropped as Wien ↔ unknown although Mistelbach is a Pendler station;
    # no ÖBB message naming Mistelbach or Wolkersdorf ("im Weinviertel") had
    # reached the feed since July. A qualified name lies outside Vienna, so a
    # bare name that lands on a Vienna station is no match: "Baumgarten im
    # Burgenland" must not become the Wiener-Linien stop Baumgarten.
    for candidate in candidates:
        bare = _PLACE_QUALIFIER_RE.sub("", candidate)
        if bare == candidate:
            continue
        info = lookup.get(_normalize_token(bare))
        if info and not info.in_vienna:
            return info
    return None


@lru_cache(maxsize=1)
def _name_keys() -> frozenset[str]:
    """Lookup keys some station carries as its name or a text alias.

    The keys of :func:`_station_lookup` minus the identity-only ones: the
    ``bst_code``, ``wl_diva``, ``vor_id`` and WL ``stop_id`` of a station
    (``"Gn"``, ``"1730"``) name it in a data field, never in a sentence.
    """
    keys: set[str] = set()
    for entry in _station_entries():
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        extras: list[str] = []
        aliases_field = entry.get("aliases")
        if isinstance(aliases_field, list):
            extras.extend(str(alias).strip() for alias in aliases_field if alias is not None)
        stops_field = entry.get("wl_stops")
        if isinstance(stops_field, list):
            extras.extend(
                str(stop.get("name")).strip()
                for stop in stops_field
                if isinstance(stop, dict) and stop.get("name") is not None
            )
        for alias, _ in _iter_aliases_with_strength(
            name, None, identity_extras=None, text_extras=[e for e in extras if e]
        ):
            key = _normalize_token(alias)
            if key:
                keys.add(key)
    return frozenset(keys)


# A key with fewer letters is a code or a fragment, not a name a sentence
# would use ("am" from the WL stop "Am Bahnhof", "Hbf am" after the
# normalisation drops "Hbf").
_MIN_NAME_KEY_LETTERS = 3


def _names_a_station(key: str) -> bool:
    return (
        key in _name_keys()
        and len(re.sub(r"[^a-z]", "", key)) >= _MIN_NAME_KEY_LETTERS
    )


@lru_cache(maxsize=4096)
def station_named_in_text(fragment: str) -> StationInfo | None:
    """:func:`station_info` for a fragment cut out of a sentence.

    A scan that slides a window over a message text hands every run of
    words to the directory, and the directory also knows stations by their
    codes and IDs and by names that shrink to a single short word once
    "Bahnhof" is dropped. ``station_info`` resolves such a fragment like a
    name: "Stellwerkstörung am Bahnhof" ("am" → the WL stop "Am Bahnhof"),
    "S4-Züge 4212 und 1730 (REX1)" (the WL stop ID 1730), "Update 2
    (12.09.2026 23:15)" ("2"). Until 2026-10-05 that made ÖBB disruptions
    in Wolfurt, Ebensee, Hinterstoder, Telfs and Lind-Rosegg count as
    Vienna and put them into the feed (six since July).

    Here a fragment only matches through a key some station carries as
    its name or a text alias, with at least three letters. Everything
    else is as in :func:`station_info`.
    """
    lookup = _station_lookup()
    if not lookup:
        return None
    candidates = _candidate_values(fragment)
    for candidate in candidates:
        key = _normalize_token(candidate)
        if key and _names_a_station(key):
            info = lookup.get(key)
            if info:
                return info
    for candidate in candidates:
        bare = _PLACE_QUALIFIER_RE.sub("", candidate)
        if bare == candidate:
            continue
        key = _normalize_token(bare)
        info = lookup.get(key) if _names_a_station(key) else None
        if info and not info.in_vienna:
            return info
    return None


@lru_cache(maxsize=1)
def _station_lines_by_name() -> dict[str, frozenset[str]]:
    """Lines per station from :mod:`data/oebb_station_lines.json`.

    Keyed on the normalised station name; the lines are the HAFAS line
    codes seen at the station (``REX41``, ``S40``, …). A missing or
    unreadable file yields an empty mapping.
    """

    payload = _read_capped_json(
        _STATION_LINES_PATH,
        MAX_STATION_LINES_FILE_BYTES,
        label="Station lines",
    )
    stations = payload.get("stations") if isinstance(payload, dict) else None
    if not isinstance(stations, dict):
        return {}
    result: dict[str, frozenset[str]] = {}
    for entry in stations.values():
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        lines = entry.get("lines")
        if not isinstance(name, str) or not isinstance(lines, dict):
            continue
        key = _normalize_token(name)
        if key:
            result[key] = result.get(key, frozenset()) | frozenset(
                line for line in lines if isinstance(line, str)
            )
    return result


def station_lines(name: str) -> frozenset[str]:
    """Return the ÖBB lines that stop at station *name* (empty if unknown)."""

    info = station_info(name)
    canonical = info.name if info else name
    return _station_lines_by_name().get(_normalize_token(canonical), frozenset())
