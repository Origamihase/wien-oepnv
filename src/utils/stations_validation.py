"""Validation helpers for ``data/stations.json``.

The module powers automated quality reports that flag inconsistencies in the
station directory.  It is intentionally light on external dependencies so
that it can be reused in scripts, tests and CI jobs.

The checks and the report live in ``src/utils/station_checks/`` (700-line
rule, 2026-11-02); every name of the former single module stays importable
from here.
"""
from __future__ import annotations

from pathlib import Path
import csv
import io
import json
import logging
import os
from collections.abc import Mapping

from src.utils.files import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
    read_capped_text,
)
from src.utils.stations import MAX_STATIONS_FILE_BYTES, _normalize_token

# The checks and the report live in src/utils/station_checks/; the names are
# re-exported here (see ``__all__``) so importers and tests keep this path.
from src.utils.station_checks.issues import (
    DuplicateGroup,
    AliasIssue,
    GTFSIssue,
    CoordinateIssue,
    SecurityIssue,
    CrossStationIDIssue,
    ProviderIssue,
    NamingIssue,
    IdentityFieldConflict,
    AliasCollisionIssue,
    CrossNameAliasIssue,
    NameOwnershipIssue,
)
from src.utils.station_checks.report import (
    _REPORT_FIELD_MAX_LEN,
    _safe_md,
    ValidationReport,
)
from src.utils.station_checks.basic import (
    _find_duplicate_coordinate_groups,
    _extract_float,
    _format_duplicate_group,
    _format_identifier,
    _find_alias_issues,
    _find_coordinate_issues,
    _find_gtfs_issues,
)
from src.utils.station_checks.security import (
    _UNSAFE_CHARS_RE,
    _find_security_issues,
)
from src.utils.station_checks.identity import (
    _VOR_ID_PATTERN,
    is_synthetic_vor_id,
    _find_cross_station_id_conflicts,
    _find_identity_field_conflicts,
    _extract_source_tokens,
    _find_provider_issues,
    _find_naming_issues,
)
from src.utils.station_checks.names import (
    _CROSS_NAME_DISTANCE_THRESHOLD_M,
    _ALIAS_COLLISION_DISTANCE_THRESHOLD_M,
    _find_alias_collision_issues,
    _entry_stop_names,
    _bears_name,
    _find_name_ownership_issues,
    _describe_claimant_disagreement,
    _WL_VOR_SUFFIX_RE,
    _WIEN_PREFIX_RE,
    _bare_station_name,
    _iter_entry_labels,
    _build_cross_name_index,
    _find_cross_name_alias_issues,
)

# Security: per-loader byte cap for the GTFS ``stops.txt`` CSV consumed
# by ``_load_gtfs_stop_ids``. Sized at 50 MiB to comfortably cover
# multi-region GTFS dumps (Vienna is ~6 KiB; an Austrian-wide bundle
# remains well under 50 MiB) while bounding the wide-but-flat
# ``readline()`` allocation that ``csv.DictReader`` performs on
# attacker-planted unbounded files. Mirror of the canonical
# ``MAX_*_FILE_BYTES`` contract from ``src/utils/cache.py`` /
# ``src/utils/stations.py``. Module-level so tests can monkeypatch it.
MAX_GTFS_STOPS_BYTES = 50 * 1024 * 1024

log = logging.getLogger(__name__)


class StationValidationError(RuntimeError):
    """Raised when input data cannot be processed."""


def validate_stations(
    stations_path: Path,
    *,
    gtfs_stops_path: Path | None = None,
    decimal_places: int = 5,
    coordinate_bounds: tuple[float, float, float, float] | None = None,
) -> ValidationReport:
    stations = _load_stations(stations_path)
    gtfs_stop_ids, gtfs_count = _load_gtfs_stop_ids(gtfs_stops_path)

    duplicates = tuple(
        _format_duplicate_group(key, entries)
        for key, entries in _find_duplicate_coordinate_groups(stations, decimal_places)
    )

    alias_issues = tuple(_find_alias_issues(stations))
    coordinate_issues = tuple(
        _find_coordinate_issues(stations, bounds=coordinate_bounds)
    )
    gtfs_issues = tuple(_find_gtfs_issues(stations, gtfs_stop_ids))
    security_issues = tuple(_find_security_issues(stations))
    cross_station_id_issues = tuple(_find_cross_station_id_conflicts(stations))
    provider_issues = tuple(_find_provider_issues(stations))
    naming_issues = tuple(_find_naming_issues(stations))
    identity_field_conflicts = tuple(_find_identity_field_conflicts(stations))
    cross_name_alias_issues = tuple(_find_cross_name_alias_issues(stations))
    alias_collision_issues = tuple(_find_alias_collision_issues(stations))
    name_ownership_issues = tuple(_find_name_ownership_issues(stations))

    return ValidationReport(
        total_stations=len(stations),
        duplicates=duplicates,
        alias_issues=alias_issues,
        coordinate_issues=coordinate_issues,
        gtfs_issues=gtfs_issues,
        security_issues=security_issues,
        cross_station_id_issues=cross_station_id_issues,
        provider_issues=provider_issues,
        naming_issues=naming_issues,
        gtfs_stop_count=gtfs_count,
        identity_field_conflicts=identity_field_conflicts,
        cross_name_alias_issues=cross_name_alias_issues,
        alias_collision_issues=alias_collision_issues,
        name_ownership_issues=name_ownership_issues,
    )


def _load_stations(path: Path) -> list[Mapping[str, object]]:
    # Security: byte-size cap (see MAX_STATIONS_FILE_BYTES) defeats the
    # wide-but-flat size-bomb attack that the depth-bomb catch below
    # does NOT cover. ``path.read_text`` buffers the whole file before
    # ``json.loads`` runs, so a 1 GiB file allocates >1 GiB up-front
    # and crashes the validator with ``MemoryError`` (a
    # ``BaseException`` that escapes the surrounding handler).
    # Open first, then ``os.fstat`` the descriptor — closes the TOCTOU
    # between ``stat`` and ``read_text``/``open`` that lets an attacker
    # swap the inode between the two syscalls.
    # ``read(MAX_STATIONS_FILE_BYTES + 1)`` defends against zero-st_size
    # special files (FIFOs, ``/dev/zero``).
    try:
        with path.open("rb") as handle:
            file_size = os.fstat(handle.fileno()).st_size
            if file_size > MAX_STATIONS_FILE_BYTES:
                raise StationValidationError(
                    f"Stations file too large (> {MAX_STATIONS_FILE_BYTES} bytes): {path}"
                )
            raw_bytes = handle.read(MAX_STATIONS_FILE_BYTES + 1)
            if len(raw_bytes) > MAX_STATIONS_FILE_BYTES:
                raise StationValidationError(
                    f"Stations file too large (> {MAX_STATIONS_FILE_BYTES} bytes): {path}"
                )
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise StationValidationError(f"Stations file not found: {path}") from exc
    except OSError as exc:  # pragma: no cover - defensive
        raise StationValidationError(f"Stations file not readable: {path}") from exc

    try:
        # Security (reader-side non-finite literal defence): mirrors
        # the canonical :func:`src.places.merge.load_stations` defence
        # so the validator sees the same rejection shape — planted
        # ``NaN`` / ``Infinity`` literals in the stations file flow
        # through the ``except`` handler as ``StationValidationError``
        # (operator-actionable diagnostic), not as a silently-propagated
        # ``float('nan')`` that breaks every coordinate check downstream.
        raw_data = json.loads(
            raw_bytes,
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:  # pragma: no cover - defensive
        # Security: ``RecursionError`` covers JSON depth-bomb attacks in the
        # stations file (planted by a compromised CI runner or a corrupted
        # previous run). Without this catch the validator script would crash
        # with an unhandled traceback instead of the canonical exit-1
        # ``StationValidationError`` path.
        raise StationValidationError(f"Invalid JSON in {path}") from exc

    if isinstance(raw_data, list):
        data = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("stations"), list):
        data = raw_data["stations"]
    else:
        raise StationValidationError("Stations payload must be a list or a wrapped object")

    entries: list[Mapping[str, object]] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, Mapping):
            raise StationValidationError(
                f"Stations entry {index} is not an object: {entry!r}"
            )
        entries.append(entry)
    return entries


def _load_gtfs_stop_ids(path: Path | None) -> tuple[set[str], int]:
    if path is None or not path.exists():
        return set(), 0

    # Security: route through ``read_capped_text`` (TOCTOU-safe via
    # open-then-fstat, special-file-safe via ``read(max_bytes + 1)``)
    # to bound the ``csv.DictReader`` -> ``readline()`` allocation. A
    # planted unbounded ``stops.txt`` (single huge line, no newlines)
    # would otherwise propagate ``MemoryError`` past the validator and
    # crash the CI gate.
    content = read_capped_text(
        path, MAX_GTFS_STOPS_BYTES,
        encoding="utf-8-sig", label="GTFS stops", logger=log,
    )
    if content is None:
        return set(), 0

    stop_ids: set[str] = set()
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        stop_id = row.get("stop_id")
        if isinstance(stop_id, str):
            token = stop_id.strip()
            if token:
                stop_ids.add(token)
    return stop_ids, len(stop_ids)


# Names other files and tests read through this module; the code lives in
# src/utils/station_checks/ (700-line rule, 2026-11-02). Listing them marks
# the imports as deliberate re-exports.
__all__ = [
    "MAX_GTFS_STOPS_BYTES",
    "log",
    "StationValidationError",
    "validate_stations",
    "_load_stations",
    "_load_gtfs_stop_ids",
    "MAX_STATIONS_FILE_BYTES",
    "_normalize_token",
    "DuplicateGroup",
    "AliasIssue",
    "GTFSIssue",
    "CoordinateIssue",
    "SecurityIssue",
    "CrossStationIDIssue",
    "ProviderIssue",
    "NamingIssue",
    "IdentityFieldConflict",
    "AliasCollisionIssue",
    "CrossNameAliasIssue",
    "NameOwnershipIssue",
    "_REPORT_FIELD_MAX_LEN",
    "_safe_md",
    "ValidationReport",
    "_find_duplicate_coordinate_groups",
    "_extract_float",
    "_format_duplicate_group",
    "_format_identifier",
    "_find_alias_issues",
    "_find_coordinate_issues",
    "_find_gtfs_issues",
    "_UNSAFE_CHARS_RE",
    "_find_security_issues",
    "_VOR_ID_PATTERN",
    "is_synthetic_vor_id",
    "_find_cross_station_id_conflicts",
    "_find_identity_field_conflicts",
    "_extract_source_tokens",
    "_find_provider_issues",
    "_find_naming_issues",
    "_CROSS_NAME_DISTANCE_THRESHOLD_M",
    "_ALIAS_COLLISION_DISTANCE_THRESHOLD_M",
    "_find_alias_collision_issues",
    "_entry_stop_names",
    "_bears_name",
    "_find_name_ownership_issues",
    "_describe_claimant_disagreement",
    "_WL_VOR_SUFFIX_RE",
    "_WIEN_PREFIX_RE",
    "_bare_station_name",
    "_iter_entry_labels",
    "_build_cross_name_index",
    "_find_cross_name_alias_issues",
]
