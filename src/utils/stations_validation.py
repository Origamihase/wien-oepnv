"""Validation helpers for ``data/stations.json``.

The module powers automated quality reports that flag inconsistencies in the
station directory.  It is intentionally light on external dependencies so
that it can be reused in scripts, tests and CI jobs.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import csv
import json
import math
from typing import Iterable, Iterator, Mapping, Sequence


@dataclass(frozen=True)
class DuplicateGroup:
    """Group of stations that share the same coordinates."""

    latitude: float
    longitude: float
    identifiers: tuple[str, ...]
    names: tuple[str, ...]


@dataclass(frozen=True)
class AliasIssue:
    """Station entries with missing or incomplete alias metadata."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class GTFSIssue:
    """Stations whose ``vor_id`` is not contained in the GTFS stops file."""

    identifier: str
    name: str
    vor_id: str


@dataclass(frozen=True)
class CoordinateIssue:
    """Stations whose geographic metadata appears to be malformed."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class ValidationReport:
    """Summary returned by :func:`validate_stations`."""

    total_stations: int
    duplicates: tuple[DuplicateGroup, ...]
    alias_issues: tuple[AliasIssue, ...]
    coordinate_issues: tuple[CoordinateIssue, ...]
    gtfs_issues: tuple[GTFSIssue, ...]
    gtfs_stop_count: int

    @property
    def has_issues(self) -> bool:
        return bool(
            self.duplicates
            or self.alias_issues
            or self.coordinate_issues
            or self.gtfs_issues
        )

    def to_markdown(self) -> str:
        lines = ["# Stations Validation Report", ""]
        lines.append(f"*Total stations analysed*: {self.total_stations}")
        lines.append(f"*GTFS stops loaded*: {self.gtfs_stop_count}")
        lines.append(f"*Geographic duplicates*: {len(self.duplicates)}")
        lines.append(f"*Alias issues*: {len(self.alias_issues)}")
        lines.append(f"*Coordinate anomalies*: {len(self.coordinate_issues)}")
        lines.append(f"*GTFS mismatches*: {len(self.gtfs_issues)}")
        lines.append("")

        if self.duplicates:
            lines.append("## Geographic duplicates")
            for group in self.duplicates:
                lines.append(
                    f"- ({group.latitude:.5f}, {group.longitude:.5f}) → "
                    + ", ".join(group.identifiers)
                )
            lines.append("")

        if self.alias_issues:
            lines.append("## Alias issues")
            for alias_issue in self.alias_issues:
                lines.append(
                    f"- {alias_issue.identifier} ({alias_issue.name}): {alias_issue.reason}"
                )
            lines.append("")

        if self.coordinate_issues:
            lines.append("## Coordinate anomalies")
            for coordinate_issue in self.coordinate_issues:
                lines.append(
                    f"- {coordinate_issue.identifier} ({coordinate_issue.name}): {coordinate_issue.reason}"
                )
            lines.append("")

        if self.gtfs_issues:
            lines.append("## GTFS mismatches")
            for gtfs_issue in self.gtfs_issues:
                lines.append(
                    f"- {gtfs_issue.identifier} ({gtfs_issue.name}) → missing stop_id {gtfs_issue.vor_id}"
                )
            lines.append("")

        if not self.has_issues:
            lines.append("No issues detected.")

        return "\n".join(lines).rstrip() + "\n"


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

    return ValidationReport(
        total_stations=len(stations),
        duplicates=duplicates,
        alias_issues=alias_issues,
        coordinate_issues=coordinate_issues,
        gtfs_issues=gtfs_issues,
        gtfs_stop_count=gtfs_count,
    )


def _load_stations(path: Path) -> list[Mapping[str, object]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise StationValidationError(f"Stations file not found: {path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise StationValidationError(f"Invalid JSON in {path}") from exc

    if not isinstance(data, list):
        raise StationValidationError("Stations payload must be a list")

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

    stop_ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            stop_id = row.get("stop_id")
            if isinstance(stop_id, str):
                token = stop_id.strip()
                if token:
                    stop_ids.add(token)
    return stop_ids, len(stop_ids)


def _find_duplicate_coordinate_groups(
    stations: Sequence[Mapping[str, object]],
    decimal_places: int,
) -> Iterator[tuple[tuple[float, float], list[Mapping[str, object]]]]:
    buckets: dict[tuple[float, float], list[Mapping[str, object]]] = defaultdict(list)
    for station in stations:
        lat = _extract_float(station.get("latitude"))
        lon = _extract_float(station.get("longitude"))
        if lat is None or lon is None:
            continue
        key = (round(lat, decimal_places), round(lon, decimal_places))
        buckets[key].append(station)

    for key, entries in buckets.items():
        if len(entries) > 1:
            yield key, entries


def _extract_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        try:
            f = float(token)
            return f if math.isfinite(f) else None
        except ValueError:
            return None
    return None


def _format_duplicate_group(
    key: tuple[float, float],
    entries: Sequence[Mapping[str, object]],
) -> DuplicateGroup:
    identifiers = tuple(_format_identifier(entry) for entry in entries)
    names = tuple(str(entry.get("name", "")) for entry in entries)
    return DuplicateGroup(
        latitude=key[0],
        longitude=key[1],
        identifiers=identifiers,
        names=names,
    )


def _format_identifier(entry: Mapping[str, object]) -> str:
    parts: list[str] = []
    bst_id = entry.get("bst_id")
    if isinstance(bst_id, int):
        parts.append(f"bst:{bst_id}")
    bst_code = entry.get("bst_code")
    if isinstance(bst_code, str) and bst_code.strip():
        parts.append(f"code:{bst_code.strip()}")
    source = entry.get("source")
    if isinstance(source, str) and source.strip():
        parts.append(f"source:{source.strip()}")
    if not parts:
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            parts.append(name.strip())
        else:
            parts.append("<unknown>")
    return " / ".join(parts)


def _find_alias_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[AliasIssue]:
    for entry in stations:
        aliases_obj = entry.get("aliases")
        name = str(entry.get("name", "")).strip()
        identifier = _format_identifier(entry)

        if not isinstance(aliases_obj, Sequence) or isinstance(aliases_obj, (str, bytes)):
            yield AliasIssue(identifier=identifier, name=name or "<unknown>", reason="missing aliases list")
            continue

        aliases: list[str] = []
        for item in aliases_obj:
            if isinstance(item, str):
                token = item.strip()
                if token:
                    aliases.append(token)
        if not aliases:
            yield AliasIssue(identifier=identifier, name=name or "<unknown>", reason="aliases list is empty")
            continue

        required: list[str] = []
        if name:
            required.append(name)
        bst_code = entry.get("bst_code")
        if isinstance(bst_code, str) and bst_code.strip():
            required.append(bst_code.strip())
        vor_id = entry.get("vor_id")
        if isinstance(vor_id, str) and vor_id.strip():
            required.append(vor_id.strip())

        alias_set = {alias.lower() for alias in aliases}
        missing_required = [value for value in required if value.lower() not in alias_set]
        if missing_required:
            missing_text = ", ".join(missing_required)
            yield AliasIssue(
                identifier=identifier,
                name=name or "<unknown>",
                reason=f"missing required aliases: {missing_text}",
            )


def _find_coordinate_issues(
    stations: Sequence[Mapping[str, object]],
    *,
    bounds: tuple[float, float, float, float] | None,
) -> Iterator[CoordinateIssue]:
    if bounds is None:
        min_lat, max_lat, min_lon, max_lon = (47.0, 48.8, 15.4, 17.2)
    else:
        min_lat, max_lat, min_lon, max_lon = bounds

    for entry in stations:
        identifier = _format_identifier(entry)
        name = str(entry.get("name", "")).strip() or "<unknown>"

        latitude_value = entry.get("latitude")
        longitude_value = entry.get("longitude")
        latitude = _extract_float(latitude_value)
        longitude = _extract_float(longitude_value)

        missing_components: list[str] = []
        if latitude is None:
            missing_components.append("missing latitude")
        if longitude is None:
            missing_components.append("missing longitude")

        if missing_components:
            reason = ", ".join(missing_components)
            yield CoordinateIssue(identifier=identifier, name=name, reason=reason)
            continue

        # Mypy guard
        if latitude is None or longitude is None:
            continue

        if not (min_lat <= latitude <= max_lat) or not (min_lon <= longitude <= max_lon):
            swapped_hint = min_lat <= longitude <= max_lat and min_lon <= latitude <= max_lon
            if swapped_hint:
                reason = f"coordinates look swapped (lat={latitude}, lon={longitude})"
            else:
                reason = f"coordinates out of bounds (lat={latitude}, lon={longitude})"
            yield CoordinateIssue(identifier=identifier, name=name, reason=reason)


def _find_gtfs_issues(
    stations: Sequence[Mapping[str, object]],
    gtfs_stop_ids: Iterable[str],
) -> Iterator[GTFSIssue]:
    stops = set(gtfs_stop_ids)
    if not stops:
        return

    for entry in stations:
        vor_id_obj = entry.get("vor_id")
        if not isinstance(vor_id_obj, str):
            continue
        vor_id = vor_id_obj.strip()
        if not vor_id:
            continue
        if vor_id not in stops:
            name = str(entry.get("name", "")).strip() or "<unknown>"
            identifier = _format_identifier(entry)
            yield GTFSIssue(identifier=identifier, name=name, vor_id=vor_id)
