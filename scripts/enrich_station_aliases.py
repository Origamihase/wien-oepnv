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
import logging
import sys
from collections import defaultdict
from pathlib import Path
from collections.abc import Mapping

# Ensure the project root is in sys.path to allow imports from src
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.places.merge import write_stations_payload  # noqa: E402
from src.utils.env import _path_fingerprint  # noqa: E402
from src.utils.files import read_capped_json, read_capped_text  # noqa: E402
from src.places.station_aliases import (  # noqa: E402
    _GENERIC_ALIAS_BLOCKLIST as _GENERIC_ALIAS_BLOCKLIST,
    _alias_candidates as _alias_candidates,
    _normalize_key as _normalize_key,
    _order_aliases as _order_aliases,
)

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
    # ``utf-8-sig`` strips a UTF-8 BOM if upstream emits one. The VOR
    # CSV's first column is ``StopPointId`` — a BOM-prefixed file would
    # make ``row.get("StopPointId")`` return ``None`` for the first
    # row only (because the header reads as ``"﻿StopPointId"``),
    # silently dropping that stop's name. Sibling ``scripts/gtfs.py``
    # already uses ``utf-8-sig`` per the GTFS spec, which explicitly
    # permits a BOM.
    content = read_capped_text(
        path, MAX_ALIAS_CSV_BYTES,
        encoding="utf-8-sig", label="VOR stops", logger=log,
    )
    if content is None:
        return {}
    names: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    try:
        for row in reader:
            vor_id = (row.get("StopPointId") or "").strip()
            name = (row.get("StopPointName") or "").strip()
            if vor_id and name:
                names[vor_id] = name
    except csv.Error:
        # read_capped_text enforces only the byte cap, not CSV well-formedness.
        # A single field larger than csv.field_size_limit (default 131072) —
        # still well under MAX_ALIAS_CSV_BYTES — raises csv.Error (NOT an
        # OSError) during iteration. Degrade gracefully (skip enrichment)
        # instead of crashing the cron pipeline, mirroring the size/depth
        # fallbacks in the sibling loaders.
        log.warning(
            "Malformed VOR stops CSV [path-sha256=%s]; skipping alias enrichment.",
            _path_fingerprint(path),
        )
        return {}
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
        encoding="utf-8-sig", label="GTFS stops", logger=log,
    )
    if content is None:
        return {}
    index: dict[str, set[str]] = defaultdict(set)
    reader = csv.DictReader(io.StringIO(content))
    try:
        for row in reader:
            name = (row.get("stop_name") or "").strip()
            if not name:
                continue
            key = _normalize_key(name)
            if key:
                index[key].add(name)
    except csv.Error:
        # See _load_vor_names: a field larger than csv.field_size_limit
        # (default 131072, well under MAX_ALIAS_CSV_BYTES) raises csv.Error
        # (NOT an OSError) during iteration. Degrade gracefully with whatever
        # rows parsed cleanly instead of crashing the cron pipeline.
        log.warning(
            "Malformed GTFS stops CSV [path-sha256=%s]; using %d variants "
            "parsed before the error.",
            _path_fingerprint(path), len(index),
        )
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

    write_stations_payload(args.stations, stations)
    log.info(
        "Wrote enriched aliases to [path-sha256=%s]",
        _path_fingerprint(args.stations),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
