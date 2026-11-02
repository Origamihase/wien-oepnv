"""Pure alias generation for ``data/stations.json``.

Moved verbatim from ``scripts/enrich_station_aliases.py`` (no logic
changes).  The script re-exports the names it and the tests use.  Note:
patching ``scripts.enrich_station_aliases._normalize_key`` (or another
helper defined here) does not affect ``_alias_candidates``; patch this
module instead.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping

from src.utils.text import strip_accents


# Generic single-token aliases that match too broadly in feed text and
# must never be added as aliases for any station. The keys are the
# normalized form (lowercase, accent-free, ß→ss). "mitte" matches a
# Bezirk in Berlin/Frankfurt and the noun "in der Mitte"; "flughafen"
# is the generic word for any airport; "stadt"/"zentrum"/cardinal
# directions are equally ambiguous; bare rail vocabulary (hbf, bf,
# bahnhof) and city names (wien, vienna) are too broad to disambiguate
# specific stations from feed text. "munchen"/"muenchen" alone is also
# a substring of "Münchendorf" (a separate NÖ pendler station) — only
# the disambiguated forms ("München Hbf", "München Hauptbahnhof") are
# safe.
_GENERIC_ALIAS_BLOCKLIST = frozenset({
    # Cities (already enforced; kept for completeness)
    "wien", "vienna",
    # Generic city quarters / cardinal directions
    "mitte", "nord", "sud", "ost", "west", "zentrum", "stadt",
    # Generic transport vocabulary (when alone)
    "flughafen", "bahnhof", "hauptbahnhof", "hbf", "bf", "bhf", "bahnhst",
    # Generic place words
    "markt", "ort", "platz",
    # Bare city names that overlap with NÖ pendler stops by substring.
    # "München" is a prefix of "Münchendorf"; the disambiguated forms
    # ("München Hbf", "München Hauptbahnhof") are kept as aliases.
    "munchen", "muenchen",
    # Synthetic short forms that collide with everyday German. "Aug." (the
    # Gasse→G. shortening of "Wien Augasse") matches the month abbreviation
    # "Aug."; "Am Bf"/"Am Bahnhof" (from the WL stop "Wien Am Bahnhof") match
    # the ubiquitous phrase "am Bf". Both normalise to a bare
    # preposition/abbreviation via _normalize_key (which strips the
    # bahnhof/bf/station suffix), so every compound variant is covered. The
    # stations stay resolvable via "Wien Augasse" / "Wien Am Bahnhof" and the
    # "wien" key (bugs b8/b9).
    "aug", "am",
})


def _normalize_key(text: str) -> str:
    cleaned = strip_accents(text)
    cleaned = cleaned.replace("ß", "ss")
    cleaned = cleaned.casefold()
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", cleaned)
    cleaned = cleaned.replace("-", " ").replace("/", " ")
    cleaned = re.sub(r"\b(?:bahnhof|bf|bahnhst|station)\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s{2,}", " ", value.strip())


_UMLAUT_TRANSLATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
)


def _accent_free(value: str) -> str:
    cleaned = strip_accents(value.translate(_UMLAUT_TRANSLATION))
    cleaned = cleaned.replace("ß", "ss").replace("ẞ", "SS")
    return cleaned


def _is_textual_alias(value: str, station_keys: set[str]) -> bool:
    if not re.search(r"[a-zäöüß]", value):
        return False
    normalized = _normalize_key(value)
    if not normalized:
        return False
    return normalized in station_keys


_ST_ABBREV_RE = re.compile(r"\bSt\.?\s*(?=[A-ZÄÖÜ])")


def _sankt_variants(alias: str) -> set[str]:
    variant = _ST_ABBREV_RE.sub("Sankt ", alias)
    variant = _normalize_spaces(variant)
    if variant and variant != alias:
        return {variant}
    return set()


_BAHNHOF_RE = re.compile(r"bahnhof|hbf|\bbf\b", re.IGNORECASE)


def _bahnhof_variants(alias: str) -> set[str]:
    base = _normalize_spaces(alias)
    if not base:
        return set()

    # Avoid adding "Bahnhof" if it's already present as a word or suffix
    if _BAHNHOF_RE.search(base):
        return set()

    variants: set[str] = {f"{base} Bahnhof"}

    prefix_target = base
    if base.lower().startswith("wien "):
        prefix_target = base[5:].strip()

    if prefix_target:
        variants.add(f"Bahnhof {prefix_target}")

    return {_normalize_spaces(variant) for variant in variants if variant.strip()}


_ABBREV_PAIRS = [
    (r"(?<!\w)Str\.", "Straße"),
    (r"(?<=\w)str\.", "straße"),
    (r"(?<!\w)Pl\.", "Platz"),
    (r"(?<=\w)pl\.", "platz"),
    (r"\bHbf\b", "Hauptbahnhof"),
    (r"\bBf\b", "Bahnhof"),
    (r"(?<=\w)bf\b", "bahnhof"),
    (r"\bDr\.", "Doktor"),
    (r"(?<!\w)G\.", "Gasse"),
    (r"(?<=\w)g\.", "gasse"),
    (r"(?<!\w)Br\.", "Brücke"),
    (r"(?<=\w)br\.", "brücke"),
    (r"\bOb\.", "Ober"),
    (r"\bUnt\.", "Unter"),
]

_SHORTEN_PAIRS = [
    (r"\bStraße\b", "Str."),
    (r"straße\b", "str."),
    (r"\bStrasse\b", "Str."),
    (r"strasse\b", "str."),
    (r"\bPlatz\b", "Pl."),
    (r"platz\b", "pl."),
    (r"\bHauptbahnhof\b", "Hbf"),
    (r"\bBahnhof\b", "Bf"),
    (r"bahnhof\b", "bf"),
    (r"\bDoktor\b", "Dr."),
    (r"\bGasse\b", "G."),
    (r"gasse\b", "g."),
    (r"\bBrücke\b", "Br."),
    (r"brücke\b", "br."),
    (r"\bOber\b", "Ob."),
    (r"\bUnter\b", "Unt."),
]


def _replace_variants(alias: str, pairs: list[tuple[str, str]]) -> set[str]:
    variants = set()
    for pattern, replacement in pairs:
        # Use simple regex substitution
        try:
            new_val = re.sub(pattern, replacement, alias, flags=re.IGNORECASE)
            if new_val != alias:
                variants.add(_normalize_spaces(new_val))
        except re.error:
            continue
    return variants


def _textual_variants(alias: str) -> set[str]:
    alias = _normalize_spaces(alias)
    variants: set[str] = set()

    accent_free = _accent_free(alias)
    if accent_free and accent_free != alias:
        variants.add(accent_free)

    variants.update(_sankt_variants(alias))
    variants.update(_bahnhof_variants(alias))

    # New abbreviation variants
    variants.update(_replace_variants(alias, _ABBREV_PAIRS))
    variants.update(_replace_variants(alias, _SHORTEN_PAIRS))

    lowered = alias.casefold()
    for prefix in ("wien ", "vienna "):
        if lowered.startswith(prefix):
            without_city = _normalize_spaces(alias[len(prefix) :])
            if without_city:
                variants.add(without_city)
            break

    return {variant for variant in variants if variant}


def _alias_candidates(
    station: Mapping[str, object],
    vor_names: Mapping[str, str],
    vor_mapping: Mapping[int, str],
    gtfs_index: Mapping[str, set[str]],
    other_canonical_keys: frozenset[str] = frozenset(),
    pendler_alt_names: Mapping[str, list[str]] | None = None,
) -> list[str]:
    aliases: set[str] = set()

    def push(value: str | None) -> None:
        if value:
            text = str(value).strip()
            if text:
                aliases.add(text)

    existing_aliases = station.get("aliases")
    if isinstance(existing_aliases, list):
        for alias in existing_aliases:
            push(str(alias) if alias is not None else None)

    name_value = station.get("name")
    push(str(name_value) if isinstance(name_value, str) else None)
    vor_id_value = station.get("vor_id")
    push(str(vor_id_value) if vor_id_value is not None else None)
    # Push bst_code so the JSON aliases array carries the ÖBB Stellencode
    # explicitly. The runtime lookup in src/utils/stations.py already
    # treats bst_code as IDENTITY-class via _iter_aliases_with_strength,
    # but the validator's "missing required aliases" rule expects the
    # code to also appear in the persisted aliases list. Closes the
    # 155-entry alias_issues backlog inherited from the legacy ÖBB-Excel
    # import flow that wrote bst_code to its own field but never to
    # aliases. STATIC_VOR_ENTRIES already include their bst_code in
    # aliases (via PR #1214's _new_entry_from_static helper).
    bst_code_value = station.get("bst_code")
    push(str(bst_code_value) if bst_code_value is not None else None)

    name = str(name_value if isinstance(name_value, str) else "").strip()
    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    push(no_paren)
    push(re.sub(r"\s{2,}", " ", no_paren.replace("-", " ")))
    push(re.sub(r"\s{2,}", " ", no_paren.replace("/", " ")))

    vor_id = str(vor_id_value or "").strip()
    if vor_id:
        push(vor_names.get(vor_id))

    try:
        bst_id_raw = station.get("bst_id")
        bst_id: int | None
        if bst_id_raw is None:
            bst_id = None
        elif isinstance(bst_id_raw, int | str):
            bst_id = int(bst_id_raw)
        else:
            bst_id = None
    except (TypeError, ValueError):
        bst_id = None
    if bst_id is not None:
        push(vor_mapping.get(bst_id))

    station_keys: set[str] = set()

    def add_station_key(raw: str) -> None:
        if raw:
            key = _normalize_key(raw)
            if key:
                station_keys.add(key)

    add_station_key(name)
    add_station_key(re.sub(r"^(?:wien|vienna)\s+", "", name, flags=re.IGNORECASE))

    # Expand station keys to allow matching of abbreviations
    # E.g. allow "thaliastr" if key is "thaliastrasse"
    key_expansions = [
        ("strasse", "str"),
        ("str", "strasse"),
        ("platz", "pl"),
        ("pl", "platz"),
        ("hauptbahnhof", "hbf"),
        ("hbf", "hauptbahnhof"),
        ("bahnhof", "bf"),
        ("bf", "bahnhof"),
        ("doktor", "dr"),
        ("dr", "doktor"),
        ("gasse", "g"),
        ("bruecke", "br"),
        ("br", "bruecke"),
    ]

    for key in list(station_keys):
        sankt_key = re.sub(r"\bst\b", "sankt", key)
        if sankt_key and sankt_key != key:
            station_keys.add(sankt_key)
        st_key = re.sub(r"\bsankt\b", "st", key)
        if st_key and st_key != key:
            station_keys.add(st_key)

        # New key expansions
        for src, dst in key_expansions:
            # Suffix check
            if key.endswith(src):
                # Replace suffix
                new_key = key[:-len(src)] + dst
                station_keys.add(new_key)
            # _normalize_key collapses non-alphanumerics to single spaces, so
            # words are space-separated — pad with spaces for a word-boundary match.
            if f" {src} " in f" {key} ":
                 # Replace word
                 # Use regex to be safe
                 new_key = re.sub(rf"\b{src}\b", dst, key)
                 if new_key != key:
                     station_keys.add(new_key)


    queue: deque[str] = deque(sorted(aliases))
    processed: set[str] = set()
    while queue:
        alias = queue.popleft()
        if alias in processed:
            continue
        processed.add(alias)
        if not _is_textual_alias(alias, station_keys):
            continue
        for variant in sorted(_textual_variants(alias)):
            if variant not in aliases:
                aliases.add(variant)
                queue.append(variant)

    norm_keys: set[str] = set()
    for candidate in aliases:
        normalized = _normalize_key(candidate)
        if normalized:
            norm_keys.add(normalized)
            norm_keys.add(re.sub(r"\b(?:wien|vienna)\b", "", normalized).strip())

    for key in {key for key in norm_keys if key}:
        for gtfs_alias in gtfs_index.get(key, set()):
            push(gtfs_alias)

    # -------------------------------------------------------------------------
    # OPTIMIZATION: Manually add specific missing colloquial aliases
    # -------------------------------------------------------------------------
    missing_map = {
        r"^Flughafen Wien$": ["Vienna Airport", "Schwechat Flughafen Wien Bahnhof"],
        r"Wien Meidling$": ["Meidling"],
        r"Wien Westbahnhof$": ["Westbahnhof"],
        r"Wien Mitte-Landstraße$": ["Wien Mitte"],
        r"Wien Praterstern$": ["Praterstern"],
        r"Wien Floridsdorf$": ["Floridsdorf"],
        r"Wien Handelskai$": ["Handelskai"],
        r"Wien Hütteldorf$": ["Hütteldorf"],
        r"Wien Heiligenstadt$": ["Heiligenstadt"],
        r"Wien Spittelau$": ["Spittelau"],
        r"Wien Simmering$": ["Simmering"],
        r"Wien Ottakring$": ["Ottakring"],
        r"Wien Liesing$": ["Liesing"],
        r"Wien Penzing$": ["Penzing"],
        r"St. Pölten Hbf$": ["St. Pölten", "Sankt Pölten"],
        r"Wiener Neustadt Hbf$": ["Wr. Neustadt", "Wiener Neustadt"],
        r"Bratislava hl\.st\.$": ["Bratislava"],
        # Wien U-Bahn stations from Google Places that lack the
        # canonical "Wien " prefix. Adding the Wien-prefixed form as
        # an alias (the bare form remains the canonical) makes them
        # discoverable when feed text uses the full "Wien <Name>" form.
        # "Rennweg" is intentionally omitted: a "Wien Rennweg" alias
        # would collide with the separate S-Bahn station "Wien
        # Rennweg" — the cross-station-collision filter blocks it
        # anyway, so the omission keeps the missing_map intent clean.
        r"^Herrengasse$": ["Wien Herrengasse"],
        r"^Kettenbrückengasse$": ["Wien Kettenbrückengasse"],
        r"^Messe - Prater$": ["Wien Messe-Prater", "Wien Messe Prater"],
        r"^Neubaugasse$": ["Wien Neubaugasse"],
        r"^Pilgramgasse$": ["Wien Pilgramgasse"],
        r"^Schottenring$": ["Wien Schottenring"],
        r"^Schwedenplatz$": ["Wien Schwedenplatz"],
        r"^Stadtpark$": ["Wien Stadtpark"],
        r"^Stubentor$": ["Wien Stubentor"],
        r"^Südtiroler Platz$": ["Wien Südtiroler Platz"],
        r"^Volkstheater$": ["Wien Volkstheater"],
    }

    for pattern, add_list in missing_map.items():
        if re.match(pattern, name):
            for new_a in add_list:
                push(new_a)

    # -------------------------------------------------------------------------
    # OPTIMIZATION: Inject pendler_candidates.json alternative_names
    # -------------------------------------------------------------------------
    # If the station's canonical name (or any alias collected so far)
    # matches a pendler-candidate by normalized key, add ALL of that
    # candidate's name variants as aliases. Solves the "Angern" station
    # not having "Angern an der March" / "Angern (March)" / "Angern
    # March" as aliases — those alternatives only existed in
    # pendler_candidates.json for the resolver, and never made it into
    # stations.json itself. The cross-station-collision and generic-
    # blocklist filters below still apply, so dangerous additions are
    # rejected.
    if pendler_alt_names:
        match_keys: set[str] = set()
        own_key = _normalize_key(name)
        if own_key:
            match_keys.add(own_key)
        for alias in list(aliases):
            key = _normalize_key(alias)
            if key:
                match_keys.add(key)
        added_from_pendler: set[str] = set()
        for key in match_keys:
            for variant in pendler_alt_names.get(key, ()):
                if variant in added_from_pendler:
                    continue
                added_from_pendler.add(variant)
                push(variant)

    # -------------------------------------------------------------------------
    # OPTIMIZATION: Filter out dangerous aliases
    # -------------------------------------------------------------------------
    final_aliases = sorted(alias for alias in aliases if alias)

    own_canonical_key = _normalize_key(name)

    safe_aliases = []
    for a in final_aliases:
        norm = _normalize_key(a)
        if not norm:
            # Skip if it normalizes to nothing (e.g. just "Hbf" alone)
            continue

        # Explicit blacklist of generic single-word aliases that match
        # too broadly in feed text. "Mitte" is a Bezirk in Berlin /
        # Frankfurt / many cities and a common German noun ("in der
        # Mitte"); "Flughafen" is the generic word for any airport.
        # Direction tokens and bare rail-vocabulary are equally
        # ambiguous.
        if norm in _GENERIC_ALIAS_BLOCKLIST:
            continue

        # Cross-station-collision: an alias that normalizes to the
        # exact canonical name of *another* station in the directory
        # is almost always a leftover from a wrong VOR resolve or an
        # over-aggressive prefix-strip ("Wien Rennweg" → "Rennweg"
        # collides with the U3 station "Rennweg"; "Mistelbach Stadt"
        # → "Mistelbach" collides with the separate Mistelbach Hbf).
        if norm in other_canonical_keys and norm != own_canonical_key:
            continue

        # Filter out platform-specific aliases to prevent bloat
        if re.search(r"\b(?:bahnsteige?|gleise?)\b", a, re.IGNORECASE):
            continue

        safe_aliases.append(a)

    # The station's own bst_code is its operational identity (ÖBB
    # Stellencode like "Aw", "Ken H1") and must not be filtered by
    # the generic-blocklist or any other rule. Pfaffstätten happens
    # to have bst_code "Bf" — the blocklist correctly suppresses
    # "Bf" coming from arbitrary alias generation, but the validator
    # and runtime lookup expect the *own* bst_code to be present.
    # Append unconditionally (deduplicated).
    own_bst_code = str(station.get("bst_code") or "").strip()
    if own_bst_code and own_bst_code not in safe_aliases:
        safe_aliases.append(own_bst_code)

    return safe_aliases


def _order_aliases(station: Mapping[str, object], aliases: Iterable[str]) -> list[str]:
    ordered: list[str] = []

    def add(value: str | None) -> None:
        if value:
            text = str(value).strip()
            if text and text not in ordered:
                ordered.append(text)

    add(str(station.get("vor_id") or "").strip() or None)
    add(str(station.get("name") or "").strip() or None)

    remaining = sorted(
        {alias for alias in aliases if alias not in ordered},
        key=lambda item: (item.casefold(), item),
    )
    for alias in remaining:
        add(alias)
    return ordered
