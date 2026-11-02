#!/usr/bin/env python3
"""Merge Wiener Linien CSV exports into the station directory.

The script reads the OGD CSV files ``wienerlinien-ogd-haltepunkte`` and
``wienerlinien-ogd-haltestellen`` (expected to live in ``data/`` by default)
combines the StopIDs with the station level metadata and appends the
resulting entries to ``data/stations.json``.

The JSON entries are tagged with ``"source": "wl"`` so they can easily be
replaced on subsequent runs.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from collections.abc import Callable, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# The station build lives in ``src/wl_stations/`` (moved verbatim on
# 2026-11-02 for the file line limit). The names below are re-exported for
# tests and callers (``__all__``: mypy --strict ``no_implicit_reexport``).
# A patch on this module reaches ``main`` only; the moved functions resolve
# their globals in their own ``src.wl_stations`` module, so patch there.
from src.wl_stations.base import (  # noqa: E402
    BASE_DIR,
    _path_fingerprint,
    _project_root,
    get_bool_env,
    load_wl_gtfs_coordinates,
    log,
    scrub_trojan_source_primitives,
)
from src.wl_stations.colocated import (  # noqa: E402
    _drop_distant_name_contamination,
    _merge_entry_group,
)
from src.wl_stations.entries import (  # noqa: E402
    _aggregate_coordinates,
    _derive_station_label,
    _is_plausible_wl_stop,
    build_wl_entries,
)
from src.wl_stations.merge import (  # noqa: E402
    MAX_JSON_FILE_BYTES,
    _merge_sources,
    _merge_wl_payload,
    load_vor_mapping,
    merge_into_stations,
)
from src.wl_stations.ogd_csv import (  # noqa: E402
    MAX_WL_CSV_BYTES,
    Haltepunkt,
    Haltestelle,
    NormalizedRow,
    _coerce_float,
    _dict_reader,
    _line_sort_key,
    _normalize_key,
    _station_lines,
    load_haltepunkte,
    load_haltestellen,
    load_line_texts,
    load_lines_by_stop,
)
from src.wl_stations.ogd_download import (  # noqa: E402
    _cache_sidecar_path,
    _download_ogd_csv,
    _read_cache_validators,
)
from src.wl_stations.reconcile import (  # noqa: E402
    _build_hafas_lookup,
    _reconcile_at_overlap,
    _wl_coord_index,
)

__all__ = [
    "BASE_DIR",
    "MAX_JSON_FILE_BYTES",
    "MAX_WL_CSV_BYTES",
    "Haltepunkt",
    "Haltestelle",
    "NormalizedRow",
    "_aggregate_coordinates",
    "_build_hafas_lookup",
    "_cache_sidecar_path",
    "_coerce_float",
    "_derive_station_label",
    "_dict_reader",
    "_download_ogd_csv",
    "_drop_distant_name_contamination",
    "_is_plausible_wl_stop",
    "_line_sort_key",
    "_merge_entry_group",
    "_merge_sources",
    "_merge_wl_payload",
    "_normalize_key",
    "_path_fingerprint",
    "_read_cache_validators",
    "_reconcile_at_overlap",
    "_station_lines",
    "_wl_coord_index",
    "build_wl_entries",
    "get_bool_env",
    "load_haltepunkte",
    "load_haltestellen",
    "load_line_texts",
    "load_lines_by_stop",
    "load_vor_mapping",
    "load_wl_gtfs_coordinates",
    "log",
    "main",
    "merge_into_stations",
    "parse_args",
    "scrub_trojan_source_primitives",
]


DEFAULT_HALTEPUNKTE = BASE_DIR / "data" / "wienerlinien-ogd-haltepunkte.csv"
DEFAULT_HALTESTELLEN = BASE_DIR / "data" / "wienerlinien-ogd-haltestellen.csv"
DEFAULT_LINIEN = BASE_DIR / "data" / "wienerlinien-ogd-linien.csv"
DEFAULT_FAHRWEGVERLAEUFE = BASE_DIR / "data" / "wienerlinien-ogd-fahrwegverlaeufe.csv"
DEFAULT_GTFS_STOPS = BASE_DIR / "data" / "wienerlinien-gtfs-stops.csv"
DEFAULT_STATIONS = BASE_DIR / "data" / "stations.json"
DEFAULT_VOR_MAPPING = BASE_DIR / "data" / "vor-haltestellen.mapping.json"

# Canonical OGD endpoint hosted by Wiener Linien themselves (documented in
# ``wienerlinien_ogd_Beschreibung.pdf``). The ``data.wien.gv.at/csv/...``
# proxy that this project used previously was retired during the 60th OGD
# phase (September 2025): haltepunkte.csv started returning HTTP 404 and
# haltestellen.csv was the last surviving proxy CSV. The wienerlinien.at
# host has served the same files under a URL pattern stable since 2022.
OGD_HALTESTELLEN_URL = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltestellen.csv"
OGD_HALTEPUNKTE_URL = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltepunkte.csv"
# Lines and their routes (``Fahrwegverläufe``: which StopIDs each line
# serves), same host and licence. They give every WL station its
# ``wl_lines`` — the lines that actually stop there — so a disruption can
# be checked for plausibility ("tram 1 does not stop at Bhf. Hütteldorf",
# audit 2026-09-25, A.14). Optional: without them a station simply carries
# no ``wl_lines``.
OGD_LINIEN_URL = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-linien.csv"
OGD_FAHRWEGVERLAEUFE_URL = (
    "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-fahrwegverlaeufe.csv"
)
# The stops of the Wiener Linien GTFS timetable (data.gv.at "Wiener Linien –
# Fahrplandaten GTFS Wien", CC BY 4.0). Used only for a station whose OGD
# haltepunkte all fail ``_is_plausible_wl_stop``; until 2026-10-25 such
# stations got their coordinate from a hand-written override instead.
OGD_GTFS_STOPS_URL = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/gtfs/stops.txt"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge Wiener Linien stop metadata into stations.json",
    )
    parser.add_argument(
        "--haltepunkte",
        type=Path,
        default=DEFAULT_HALTEPUNKTE,
        help="Path to the haltepunkte CSV export",
    )
    parser.add_argument(
        "--haltestellen",
        type=Path,
        default=DEFAULT_HALTESTELLEN,
        help="Path to the haltestellen CSV export",
    )
    parser.add_argument(
        "--linien",
        type=Path,
        default=DEFAULT_LINIEN,
        help="Path to the linien CSV export (optional, for wl_lines)",
    )
    parser.add_argument(
        "--fahrwegverlaeufe",
        type=Path,
        default=DEFAULT_FAHRWEGVERLAEUFE,
        help="Path to the fahrwegverlaeufe CSV export (optional, for wl_lines)",
    )
    parser.add_argument(
        "--gtfs-stops",
        type=Path,
        default=DEFAULT_GTFS_STOPS,
        help="Path to the WL GTFS stops.txt (fallback for implausible OGD coordinates)",
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=DEFAULT_STATIONS,
        help="stations.json that should be updated",
    )
    parser.add_argument(
        "--vor-mapping",
        type=Path,
        default=DEFAULT_VOR_MAPPING,
        help="Optional vor-haltestellen mapping to enrich WL stations with VOR identifiers",
    )
    parser.add_argument(
        "--download",
        dest="download",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Download the latest WL OGD haltestellen/haltepunkte/linien/"
            "fahrwegverlaeufe CSVs from "
            "www.wienerlinien.at before merging (default: enabled). On failure, "
            "the existing local files are used as a fallback."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging output",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    # Dynamic import matches the script's existing lazy-loading style
    # (``_load_is_in_vienna`` etc.) so sys.path bootstrapping in
    # ``_project_root()`` is honoured before resolving ``src.feed.*``.
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    from src.feed.logging_safe import setup_script_logging
    setup_script_logging(level)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    if args.download:
        _download_ogd_csv(OGD_HALTESTELLEN_URL, args.haltestellen)
        _download_ogd_csv(OGD_HALTEPUNKTE_URL, args.haltepunkte)
        _download_ogd_csv(OGD_LINIEN_URL, args.linien)
        _download_ogd_csv(OGD_FAHRWEGVERLAEUFE_URL, args.fahrwegverlaeufe)
        _download_ogd_csv(OGD_GTFS_STOPS_URL, args.gtfs_stops)

    log.info(
        "Reading haltestellen: [path-sha256=%s]",
        _path_fingerprint(args.haltestellen),
    )
    try:
        haltestellen = load_haltestellen(args.haltestellen)
        log.info("Found %d haltestellen", len(haltestellen))

        log.info(
            "Reading haltepunkte: [path-sha256=%s]",
            _path_fingerprint(args.haltepunkte),
        )
        haltepunkte = load_haltepunkte(args.haltepunkte)
        log.info("Found %d haltepunkte", len(haltepunkte))
    except FileNotFoundError:
        # A required CSV is missing (e.g. --download enabled but the fetch
        # failed and no local fallback file exists): _dict_reader raises
        # FileNotFoundError. Abort cleanly with return 1 — matching the
        # empty-wl_entries path below — instead of an unhandled traceback
        # that aborts the cron pipeline (subprocess check=True). Path is not
        # logged raw to avoid leaking it.
        log.error(
            "A required Wiener-Linien OGD CSV is missing; aborting (return 1)."
        )
        return 1

    vor_mapping = load_vor_mapping(args.vor_mapping)
    if vor_mapping:
        log.info("Loaded %d VOR mapping entries", len(vor_mapping))

    line_texts = load_line_texts(args.linien)
    lines_by_stop = load_lines_by_stop(args.fahrwegverlaeufe, line_texts)
    log.info(
        "Found %d WL lines serving %d haltepunkte", len(line_texts), len(lines_by_stop)
    )
    if line_texts and not lines_by_stop:
        log.warning(
            "WL lines loaded but no route rows matched them; "
            "check the fahrwegverlaeufe CSV columns (LineID, StopID)"
        )

    gtfs_coordinates = load_wl_gtfs_coordinates(args.gtfs_stops)
    log.info("Loaded WL GTFS stop coordinates for %d DIVAs", len(gtfs_coordinates))
    wl_entries = build_wl_entries(
        haltestellen, haltepunkte, vor_mapping, lines_by_stop, gtfs_coordinates
    )
    log.info(
        "Prepared %d WL station entries (%d with wl_lines)",
        len(wl_entries),
        sum(1 for entry in wl_entries if entry.get("wl_lines")),
    )

    # Abort before the costly reconcile + merge if the OGD load produced
    # nothing — merge_into_stations would otherwise wipe the WL layer.
    if not wl_entries:
        log.error(
            "No WL station entries were built (haltestellen=%d, haltepunkte=%d) — "
            "aborting to protect data/stations.json from WL-layer deletion.",
            len(haltestellen),
            len(haltepunkte),
        )
        return 1

    # Drop mislabelled stop names that resolve to a far-away station (an
    # upstream WL DIVA-grouping artefact). Without this a stop sitting at
    # one station but carrying another's name (observed: a Grinzing stop
    # named "Karlsplatz", 6.4 km off) pollutes the alias index and resolves
    # lookups to the wrong station.
    contaminated = _drop_distant_name_contamination(wl_entries)
    if contaminated:
        log.info("Dropped %d distant-name-contaminating alias/stop token(s)", contaminated)

    # Austrian-source coordinate consensus (WL → HAFAS). Since 2026-10-17
    # no OpenStreetMap arbiter (Michael: no OSM data in the published
    # directory, ODbL share-alike): a WL/HAFAS disagreement keeps the WL
    # coordinate. Env-disabled via ``WIEN_OEPNV_AT_RECONCILE=0`` so the
    # orchestrator wrapper test can skip the ~40 real HAFAS round-trips
    # that would otherwise tip it over its pytest timeout. Production cron
    # runs leave the env unset so reconciliation remains active.
    reconcile: Callable[[list[dict[str, object]]], None] | None = None
    if get_bool_env("WIEN_OEPNV_AT_RECONCILE", True):
        wl_coord_by_diva = _wl_coord_index(wl_entries)
        hafas_lookup = _build_hafas_lookup()

        def _run_reconcile(merged: list[dict[str, object]]) -> None:
            _reconcile_at_overlap(
                merged,
                wl_coord_by_diva,
                hafas_lookup=hafas_lookup,
            )

        reconcile = _run_reconcile
    else:
        log.info(
            "Skipping AT coordinate reconciliation (WIEN_OEPNV_AT_RECONCILE=0)"
        )

    merge_into_stations(args.stations, wl_entries, reconcile=reconcile)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
