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
import json
import logging
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

# Ensure the project root is in sys.path to allow imports from src
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from src.utils.files import atomic_write
except ModuleNotFoundError:
    from utils.files import atomic_write  # type: ignore

DEFAULT_STATIONS = BASE_DIR / "data" / "stations.json"
DEFAULT_VOR_STOPS = BASE_DIR / "data" / "vor-haltestellen.csv"
DEFAULT_VOR_MAPPING = BASE_DIR / "data" / "vor-haltestellen.mapping.json"
DEFAULT_GTFS_STOPS = BASE_DIR / "data" / "gtfs" / "stops.txt"

log = logging.getLogger("enrich_station_aliases")


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
        "--dry-run",
        action="store_true",
        help="Print the planned changes without writing stations.json",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


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


_BAHNHOF_RE = re.compile(r"\b(?:bahnhof|hbf|bf)\b", re.IGNORECASE)


def _bahnhof_variants(alias: str) -> set[str]:
    base = _normalize_spaces(alias)
    if not base:
        return set()

    # Avoid adding "Bahnhof" if it's already present as a word or suffix
    lowered = base.lower()
    if _BAHNHOF_RE.search(base):
        return set()
    if lowered.endswith("bahnhof") or lowered.endswith("hbf") or lowered.endswith(" bf"):
        return set()

    variants: set[str] = {f"{base} Bahnhof"}

    prefix_target = base
    if base.lower().startswith("wien "):
        prefix_target = base[5:].strip()

    if prefix_target:
        variants.add(f"Bahnhof {prefix_target}")

    return {_normalize_spaces(variant) for variant in variants if variant.strip()}


_ABBREV_PAIRS = [
    (r"\bStr\.?", "Straße"),
    (r"\bPl\.?", "Platz"),
    (r"\bHbf\b", "Hauptbahnhof"),
    (r"\bBf\b", "Bahnhof"),
    (r"\bDr\.?", "Doktor"),
    (r"\bG\.?", "Gasse"),
    (r"\bBr\.?", "Brücke"),
    (r"\bOb\.?", "Ober"),
    (r"\bUnt\.?", "Unter"),
]

_SHORTEN_PAIRS = [
    (r"Straße", "Str."),
    (r"Strasse", "Str."),
    (r"Platz", "Pl."),
    (r"Hauptbahnhof", "Hbf"),
    (r"Bahnhof", "Bf"),
    (r"Doktor", "Dr."),
    (r"Gasse", "G."),
    (r"Brücke", "Br."),
    (r"Ober", "Ob."),
    (r"Unter", "Unt."),
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
        log.warning("VOR stops file %s not found", path)
        return {}
    names: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            vor_id = (row.get("StopPointId") or "").strip()
            name = (row.get("StopPointName") or "").strip()
            if vor_id and name:
                names[vor_id] = name
    log.info("Loaded %d VOR stop names", len(names))
    return names


def _load_vor_mapping(path: Path) -> dict[int, str]:
    if not path.exists():
        log.warning("VOR mapping file %s not found", path)
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("Could not parse %s: %s", path, exc)
        return {}
    mapping: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            bst_id = int(item.get("bst_id"))
        except (TypeError, ValueError):
            continue
        resolved_name = (item.get("resolved_name") or "").strip()
        if resolved_name:
            mapping[bst_id] = resolved_name
    log.info("Loaded %d VOR resolved names", len(mapping))
    return mapping


def _load_gtfs_index(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        log.warning("GTFS stops file %s not found", path)
        return {}
    index: dict[str, set[str]] = defaultdict(set)
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = (row.get("stop_name") or "").strip()
            if not name:
                continue
            key = _normalize_key(name)
            if key:
                index[key].add(name)
    log.info("Indexed %d GTFS stop name variants", len(index))
    return index


def _add_alias(container: list[str], alias: str) -> None:
    if alias and alias not in container:
        container.append(alias)


def _alias_candidates(
    station: dict,
    vor_names: dict[str, str],
    vor_mapping: dict[int, str],
    gtfs_index: dict[str, set[str]],
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
            push(alias)

    push(station.get("name"))
    push(station.get("vor_id"))

    name = str(station.get("name", "")).strip()
    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    push(no_paren)
    push(re.sub(r"\s{2,}", " ", no_paren.replace("-", " ")))
    push(re.sub(r"\s{2,}", " ", no_paren.replace("/", " ")))

    vor_id = str(station.get("vor_id") or "").strip()
    if vor_id:
        push(vor_names.get(vor_id))

    try:
        bst_id = int(station.get("bst_id"))
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

    return sorted(alias for alias in aliases if alias)


def _order_aliases(station: dict, aliases: Iterable[str]) -> list[str]:
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
        log.error("Stations file %s not found", args.stations)
        return 1

    try:
        stations = json.loads(args.stations.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("Could not parse %s: %s", args.stations, exc)
        return 1

    if not isinstance(stations, list):
        log.error("Stations file %s does not contain a JSON array", args.stations)
        return 1

    vor_names = _load_vor_names(args.vor_stops)
    vor_mapping = _load_vor_mapping(args.vor_mapping)
    gtfs_index = _load_gtfs_index(args.gtfs_stops)

    updated = 0
    for entry in stations:
        if not isinstance(entry, dict):
            continue
        aliases = _alias_candidates(entry, vor_names, vor_mapping, gtfs_index)
        ordered = _order_aliases(entry, aliases)
        if ordered != entry.get("aliases"):
            updated += 1
            entry["aliases"] = ordered

    if not updated:
        log.info("No station aliases changed")
        return 0

    log.info("Updated aliases for %d stations", updated)
    if args.dry_run:
        log.info("Dry run – not writing %s", args.stations)
        return 0

    with atomic_write(args.stations, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(stations, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    log.info("Wrote enriched aliases to %s", args.stations)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
