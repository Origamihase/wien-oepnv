"""Enrich station aliases in :mod:`data/stations.json`.

The repository already aggregates station metadata from several sources
(``stations.json``).  This helper script augments the ``aliases`` field for
each station by collecting alternative names from all locally available
datasets:

* VOR stop exports (``data/vor-haltestellen.csv``)
* The VAO resolution mapping (``data/vor-haltestellen.mapping.json``)
* GTFS stops provided with the repository (``data/gtfs/stops.txt``)

External network access is not required which makes the script deterministic
inside the CI sandbox.  The collected aliases are deduplicated, sanitized and
sorted to keep the JSON payload stable.

Usage::

    python scripts/enrich_station_aliases.py --stations data/stations.json

"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from collections.abc import Iterable, Mapping

# Ensure the project root is in sys.path to allow imports from src
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from src.utils.files import atomic_write, read_capped_json, read_capped_text
    from src.utils.serialize import scrub_trojan_source_primitives
except ModuleNotFoundError:
    from utils.files import atomic_write, read_capped_json, read_capped_text  # type: ignore[no-redef]
    from utils.serialize import scrub_trojan_source_primitives  # type: ignore[no-redef]

DEFAULT_STATIONS = BASE_DIR / "data" / "stations.json"
DEFAULT_VOR_STOPS = BASE_DIR / "data" / "vor-haltestellen.csv"
DEFAULT_VOR_MAPPING = BASE_DIR / "data" / "vor-haltestellen.mapping.json"
DEFAULT_GTFS_STOPS = BASE_DIR / "data" / "gtfs" / "stops.txt"
DEFAULT_PENDLER_CANDIDATES = BASE_DIR / "data" / "pendler_candidates.json"

# Security cap against wide-but-flat JSON size-bomb attacks. The
# depth-bomb catch tuple does NOT cover ``MemoryError`` (a
# ``BaseException`` subclass), so a planted-huge file (~1 GiB of
# ``[0,0,…]``) buffered via ``path.read_text()`` propagates past the
# loader and crashes the cron pipeline. Sized at ~285x the production
# stations.json so no legitimate state is ever rejected. Mirrors the
# canonical ``MAX_*_FILE_BYTES`` contract from
# ``src/utils/cache.py`` / ``src/utils/stations.py``.
MAX_JSON_FILE_BYTES = 50 * 1024 * 1024
# CSV size-bomb axis: ``_load_vor_names`` and ``_load_gtfs_index``
# previously fed operator-supplied CSVs into ``csv.DictReader(handle)``
# directly, letting ``handle.readline()`` buffer GiB-sized single-line
# payloads. Routes through ``read_capped_text`` -> ``io.StringIO`` to
# bound the allocation. 50 MiB matches ``MAX_JSON_FILE_BYTES``.
MAX_ALIAS_CSV_BYTES = 50 * 1024 * 1024

log = logging.getLogger("enrich_station_aliases")


def _path_fingerprint(path: Path) -> str:
    """Return a one-way SHA-256 fingerprint of ``str(path)`` (12 hex chars).

    Security (Path-Log Sibling Drift Round 2, ``scripts/`` closure):
    mirrors the canonical sanitisation shape pinned in
    :func:`src.utils.env._path_fingerprint` and the inline
    :func:`src.utils.files.read_capped_json` fingerprint. The path
    arguments at every caller-side WARNING / INFO log line in this
    script come from operator-controlled CLI flags (``--vor-stops``,
    ``--vor-mapping``, ``--gtfs-stops``, ``--pendler-candidates``,
    ``--stations``). Interpolating the raw path bytes lets a hostile
    path carrying Trojan-Source primitives (BiDi RLO, zero-width,
    8-bit C1 CSI/OSC, Tag block, Variation Selectors, newline
    log-forgery, ANSI ESC) flow verbatim into stderr / aggregated
    cron logs / SIEM splitters. The hex-only fingerprint is
    Trojan-Source-clean and a CodeQL-recognised barrier for the
    ``py/clear-text-logging-sensitive-data`` taint. Operators
    correlate by re-hashing the candidate path locally.
    """
    return hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge additional aliases from local data sources into stations.json",
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=DEFAULT_STATIONS,
        help="stations.json file to update",
    )
    parser.add_argument(
        "--vor-stops",
        type=Path,
        default=DEFAULT_VOR_STOPS,
        help="CSV file with VOR stop information",
    )
    parser.add_argument(
        "--vor-mapping",
        type=Path,
        default=DEFAULT_VOR_MAPPING,
        help="JSON mapping produced by fetch_vor_haltestellen.py",
    )
    parser.add_argument(
        "--gtfs-stops",
        type=Path,
        default=DEFAULT_GTFS_STOPS,
        help="GTFS stops.txt source for platform level names",
    )
    parser.add_argument(
        "--pendler-candidates",
        type=Path,
        default=DEFAULT_PENDLER_CANDIDATES,
        help=(
            "pendler_candidates.json source — alternative_names entries get "
            "added to the matching station's aliases"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned changes without writing stations.json",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    # Lazy import: this script doesn't have a top-level `from src.X`
    # so we delay the import until after sys.path is bootstrapped at
    # module top.
    from src.feed.logging_safe import setup_script_logging
    setup_script_logging(level)


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
})


def _strip_accents(value: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def _normalize_key(text: str) -> str:
    cleaned = _strip_accents(text)
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
    cleaned = _strip_accents(value.translate(_UMLAUT_TRANSLATION))
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


def _load_vor_names(path: Path) -> dict[str, str]:
    if not path.exists():
        log.warning(
            "VOR stops file [path-sha256=%s] not found",
            _path_fingerprint(path),
        )
        return {}
    # Security: see ``MAX_ALIAS_CSV_BYTES`` for the canonical CSV
    # size-bomb defence shape (``read_capped_text`` -> ``io.StringIO``
    # -> ``csv.DictReader``). Pre-fix a planted unbounded VOR CSV would
    # propagate ``MemoryError`` past the caller and crash the cron
    # pipeline.
    import io
    content = read_capped_text(
        path, MAX_ALIAS_CSV_BYTES,
        encoding="utf-8", label="VOR stops", logger=log,
    )
    if content is None:
        return {}
    names: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    for row in reader:
        vor_id = (row.get("StopPointId") or "").strip()
        name = (row.get("StopPointName") or "").strip()
        if vor_id and name:
            names[vor_id] = name
    log.info("Loaded %d VOR stop names", len(names))
    return names


def _load_vor_mapping(path: Path) -> dict[int, str]:
    if not path.exists():
        log.warning(
            "VOR mapping file [path-sha256=%s] not found",
            _path_fingerprint(path),
        )
        return {}
    # Security: ``read_capped_json`` enforces the byte-size cap (see
    # MAX_JSON_FILE_BYTES) BEFORE opening the file plus the depth-bomb
    # catch tuple. The cron-pipeline blast radius (subprocess.run via
    # update_all_stations.py) makes the wide-but-flat MemoryError
    # propagation a defense-in-depth gap that the depth-bomb catch
    # alone does NOT cover.
    payload = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="VOR mapping", logger=log,
    )
    if payload is None:
        log.warning(
            "Could not parse VOR mapping [path-sha256=%s] (missing/invalid/oversized)",
            _path_fingerprint(path),
        )
        return {}
    # Zero-trust: a successfully-decoded payload from disk may still be the wrong shape
    # (corrupted file, hand-edited mapping, or upstream contract change). Without this
    # guard, `for item in payload` raises TypeError on non-iterable JSON values (null,
    # int, bool) and bypasses the documented `return {}` fallback, taking down the cron
    # pipeline (run via subprocess.run check=True from update_all_stations.py). The
    # sibling `_load_vor_name_to_id_map` in scripts/update_station_directory.py and
    # `_load_pendler_alternative_names` above both apply the same shape guard.
    if not isinstance(payload, list):
        log.warning(
            "VOR mapping [path-sha256=%s] must contain a JSON array; got %s",
            _path_fingerprint(path),
            type(payload).__name__,
        )
        return {}
    mapping: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            bst_id_raw = item.get("bst_id")
            if bst_id_raw is None:
                continue
            bst_id = int(bst_id_raw)
        except (TypeError, ValueError):
            continue
        resolved_name_raw = item.get("resolved_name")
        if not isinstance(resolved_name_raw, str):
            continue
        resolved_name = resolved_name_raw.strip()
        if resolved_name:
            mapping[bst_id] = resolved_name
    log.info("Loaded %d VOR resolved names", len(mapping))
    return mapping


def _load_gtfs_index(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        log.warning(
            "GTFS stops file [path-sha256=%s] not found",
            _path_fingerprint(path),
        )
        return {}
    # Security: see ``_load_vor_names`` / ``MAX_ALIAS_CSV_BYTES`` for
    # the canonical CSV size-bomb defence shape.
    import io
    content = read_capped_text(
        path, MAX_ALIAS_CSV_BYTES,
        encoding="utf-8", label="GTFS stops", logger=log,
    )
    if content is None:
        return {}
    index: dict[str, set[str]] = defaultdict(set)
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        name = (row.get("stop_name") or "").strip()
        if not name:
            continue
        key = _normalize_key(name)
        if key:
            index[key].add(name)
    log.info("Indexed %d GTFS stop name variants", len(index))
    return index


def _load_pendler_alternative_names(path: Path) -> dict[str, list[str]]:
    """Map normalized canonical / alternative-name keys → list of all
    name variants from one ``pendler_candidates.json`` entry.

    The fetcher already uses these alternatives to resolve VOR ids; the
    alias enrichment uses them so feed text containing the colloquial
    spelling ("Angern (March)" or "Trautmannsdorf/Leitha") matches the
    canonical station entry. Each candidate's normalized canonical
    name and its normalized alternative_names all key into the same
    list so the lookup hits regardless of which form the station was
    stored under.
    """
    if not path.exists():
        log.info(
            "Pendler candidates file [path-sha256=%s] not found",
            _path_fingerprint(path),
        )
        return {}
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). Same
    # cron-pipeline blast radius as ``_load_vor_mapping`` above.
    data = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="Pendler candidates", logger=log,
    )
    if data is None:
        log.warning(
            "Invalid JSON in pendler candidates [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return {}
    if not isinstance(data, Mapping):
        return {}
    raw = data.get("candidates")
    if not isinstance(raw, list):
        return {}
    name_map: dict[str, list[str]] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        canonical = entry.get("name")
        alternatives = entry.get("alternative_names")
        names: list[str] = []
        if isinstance(canonical, str) and canonical.strip():
            names.append(canonical.strip())
        if isinstance(alternatives, list):
            for alt in alternatives:
                if isinstance(alt, str) and alt.strip():
                    names.append(alt.strip())
        if not names:
            continue
        for variant in names:
            key = _normalize_key(variant)
            if key:
                name_map.setdefault(key, []).extend(
                    n for n in names if n not in name_map.get(key, [])
                )
    log.info("Loaded %d pendler-candidate name keys", len(name_map))
    return name_map


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
            # Word boundary check (for normalized keys, words are separated by space)
            # But wait, _normalize_key returns space separated?
            # yes: cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
            # So "wien hauptbahnhof" has space.
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


def _write_stations_payload(path: Path, stations: list[dict[str, object]]) -> None:
    """Atomically rewrite *path* with the enriched stations wrapped in
    the canonical ``{"stations": [...]}`` envelope.

    Security (Trojan-Source / BiDi-Mark Drift Round 14, ingestion-boundary
    defence): strip the canonical CVE-2021-42574 attack-byte union BEFORE
    ``json.dump`` so a poisoned VOR / GTFS / pendler-alternative-names
    source cannot leak raw BiDi marks into ``data/stations.json`` via
    a planted alias. The file is committed to ``main`` by the weekly
    ``update-stations.yml`` cron (when the orchestrator invokes this
    script). Mirrors ``src/places/merge.py:write_stations`` (Round 13).

    Security (Coordinate finite/range drift, companion-writer
    defence-in-depth): ``allow_nan=False`` mirrors the canonical
    writer-side pin established in Round 1485 at
    ``src/places/merge.py:write_stations``. The alias-enrichment step
    re-reads ``data/stations.json`` from the previous cron tick; a
    poisoned ``NaN`` / ``Infinity`` coordinate value planted by an
    upstream step (compromised OEBB Excel / Google Places / HAFAS /
    WL OGD response) survives Python's ``json.loads`` (default
    lenient mode) and flows verbatim through this rewrite. The pin
    surfaces such a bypass as a loud ``ValueError`` rather than a
    silent non-standard JSON literal in the committed artefact.
    """
    scrubbed = scrub_trojan_source_primitives(stations)
    serialisable = scrubbed if isinstance(scrubbed, list) else stations
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(
            {"stations": serialisable},
            handle,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")


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


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    if not args.stations.exists():
        log.error(
            "Stations file [path-sha256=%s] not found",
            _path_fingerprint(args.stations),
        )
        return 1

    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). The
    # canonical exit-1 path keeps downstream scripts running.
    raw_data = read_capped_json(
        args.stations, MAX_JSON_FILE_BYTES, label="Stations", logger=log,
    )
    if raw_data is None:
        log.error(
            "Could not parse Stations file [path-sha256=%s] (missing/invalid/oversized)",
            _path_fingerprint(args.stations),
        )
        return 1

    if isinstance(raw_data, list):
        stations = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("stations"), list):
        stations = raw_data["stations"]
    else:
        log.error(
            "Stations file [path-sha256=%s] must contain a JSON array or wrapped object",
            _path_fingerprint(args.stations),
        )
        return 1

    vor_names = _load_vor_names(args.vor_stops)
    vor_mapping = _load_vor_mapping(args.vor_mapping)
    gtfs_index = _load_gtfs_index(args.gtfs_stops)
    pendler_alt_names = _load_pendler_alternative_names(args.pendler_candidates)

    canonical_keys: set[str] = set()
    for entry in stations:
        if isinstance(entry, dict):
            key = _normalize_key(str(entry.get("name") or ""))
            if key:
                canonical_keys.add(key)
    other_canonical_keys = frozenset(canonical_keys)

    updated = 0
    for entry in stations:
        if not isinstance(entry, dict):
            continue
        aliases = _alias_candidates(
            entry,
            vor_names,
            vor_mapping,
            gtfs_index,
            other_canonical_keys=other_canonical_keys,
            pendler_alt_names=pendler_alt_names,
        )
        ordered = _order_aliases(entry, aliases)
        if ordered != entry.get("aliases"):
            updated += 1
            entry["aliases"] = ordered

    if not updated:
        log.info("No station aliases changed")
        return 0

    log.info("Updated aliases for %d stations", updated)
    if args.dry_run:
        log.info(
            "Dry run – not writing [path-sha256=%s]",
            _path_fingerprint(args.stations),
        )
        return 0

    _write_stations_payload(args.stations, stations)
    log.info(
        "Wrote enriched aliases to [path-sha256=%s]",
        _path_fingerprint(args.stations),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
