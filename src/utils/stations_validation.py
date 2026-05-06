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
import re
from typing import Iterable, Iterator, Mapping, Sequence

from src.utils.stations import _normalize_token


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
class SecurityIssue:
    """Stations containing potentially unsafe characters (XSS risk)."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class CrossStationIDIssue:
    """Aliases that collide with identity fields of other stations."""

    identifier: str
    name: str
    alias: str
    colliding_identifier: str
    colliding_name: str
    colliding_field: str


@dataclass(frozen=True)
class ProviderIssue:
    """VOR/OEBB consistency issue (e.g. invalid VOR identifier, OEBB collision)."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class NamingIssue:
    """Station whose canonical ``name`` field violates the uniqueness or formatting policy."""

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
    security_issues: tuple[SecurityIssue, ...]
    cross_station_id_issues: tuple[CrossStationIDIssue, ...]
    provider_issues: tuple[ProviderIssue, ...]
    naming_issues: tuple[NamingIssue, ...]
    gtfs_stop_count: int

    @property
    def has_issues(self) -> bool:
        return bool(
            self.duplicates
            or self.alias_issues
            or self.coordinate_issues
            or self.gtfs_issues
            or self.security_issues
            or self.cross_station_id_issues
            or self.provider_issues
            or self.naming_issues
        )

    def to_markdown(self) -> str:
        lines = ["# Stations Validation Report", ""]
        lines.append(f"*Total stations analysed*: {self.total_stations}")
        lines.append(f"*GTFS stops loaded*: {self.gtfs_stop_count}")
        lines.append(f"*Geographic duplicates*: {len(self.duplicates)}")
        lines.append(f"*Alias issues*: {len(self.alias_issues)}")
        lines.append(f"*Coordinate anomalies*: {len(self.coordinate_issues)}")
        lines.append(f"*GTFS mismatches*: {len(self.gtfs_issues)}")
        lines.append(f"*Security warnings*: {len(self.security_issues)}")
        lines.append(f"*Provider issues*: {len(self.provider_issues)}")
        lines.append(f"*Cross station ID issues*: {len(self.cross_station_id_issues)}")
        lines.append(f"*Naming issues*: {len(self.naming_issues)}")
        lines.append("")

        if self.security_issues:
            lines.append("## Security warnings (potential XSS/Injection)")
            for sec_issue in self.security_issues:
                lines.append(
                    f"- {sec_issue.identifier} ({sec_issue.name}): {sec_issue.reason}"
                )
            lines.append("")

        if self.provider_issues:
            lines.append("## Provider issues (VOR/OEBB)")
            for provider_issue in self.provider_issues:
                lines.append(
                    f"- {provider_issue.identifier} ({provider_issue.name}): {provider_issue.reason}"
                )
            lines.append("")

        if self.cross_station_id_issues:
            lines.append("## Cross station ID issues")
            for cross_issue in self.cross_station_id_issues:
                lines.append(
                    f"- {cross_issue.identifier} ({cross_issue.name}): alias {cross_issue.alias!r} collides with "
                    f"{cross_issue.colliding_field} of {cross_issue.colliding_identifier} ({cross_issue.colliding_name})"
                )
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

        if self.naming_issues:
            lines.append("## Naming issues")
            for naming_issue in self.naming_issues:
                lines.append(
                    f"- {naming_issue.identifier} ({naming_issue.name}): {naming_issue.reason}"
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
    security_issues = tuple(_find_security_issues(stations))
    cross_station_id_issues = tuple(_find_cross_station_id_conflicts(stations))
    provider_issues = tuple(_find_provider_issues(stations))
    naming_issues = tuple(_find_naming_issues(stations))

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
    )


def _load_stations(path: Path) -> list[Mapping[str, object]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise StationValidationError(f"Stations file not found: {path}") from exc

    try:
        raw_data = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
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
    val: float
    if isinstance(value, (int, float)):
        val = float(value)
    elif isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        try:
            val = float(token)
        except ValueError:
            return None
    else:
        return None

    if not math.isfinite(val):
        return None
    return val


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


_UNSAFE_CHARS_RE = re.compile(r"[<>\x00-\x08\x0b\x0c\x0e-\x1f\u2028-\u202e\u2066-\u2069]")

# Historically VOR identifiers were six digits starting with "9". In practice some
# legitimate identifiers are five digits (e.g. "93010"), so we accept either.
_VOR_ID_PATTERN = re.compile(r"9\d{4,5}")


def _find_cross_station_id_conflicts(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[CrossStationIDIssue]:
    id_map: dict[str, list[tuple[Mapping[str, object], str]]] = defaultdict(list)

    for entry in stations:
        for field in ("bst_id", "bst_code", "vor_id", "wl_diva"):
            val = entry.get(field)
            if isinstance(val, (str, int)):
                norm_val = _normalize_token(str(val))
                if norm_val:
                    id_map[norm_val].append((entry, field))

    for entry in stations:
        aliases_obj = entry.get("aliases")
        if not isinstance(aliases_obj, Sequence) or isinstance(aliases_obj, (str, bytes)):
            continue

        for alias in aliases_obj:
            if not isinstance(alias, str):
                continue

            norm_alias = _normalize_token(alias)
            if not norm_alias:
                continue

            if norm_alias in id_map:
                for colliding_entry, field in id_map[norm_alias]:
                    if colliding_entry is not entry:
                        yield CrossStationIDIssue(
                            identifier=_format_identifier(entry),
                            name=str(entry.get("name", "")).strip() or "<unknown>",
                            alias=alias.strip(),
                            colliding_identifier=_format_identifier(colliding_entry),
                            colliding_name=str(colliding_entry.get("name", "")).strip() or "<unknown>",
                            colliding_field=field,
                        )


def _extract_source_tokens(value: object) -> set[str]:
    """Return the set of provider source tokens for a stations entry.

    Mirrors the behaviour of the inline logic that previously lived in
    ``scripts/validate_stations.py``: comma-separated strings are split and
    stripped, lists are taken as-is (filtered to strings).
    """
    if isinstance(value, str):
        return {token.strip() for token in value.split(",") if token.strip()}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _find_provider_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[ProviderIssue]:
    """Find VOR/OEBB consistency issues.

    Replicates the checks from the previous inline implementation in
    ``scripts/validate_stations.py``:

    1. At least two stations must declare VOR as a source.
    2. Each VOR station's ``bst_id`` and ``bst_code`` must match the VOR id
       pattern (``_VOR_ID_PATTERN``).
    3. No VOR station's ``bst_code`` may collide with the ``bst_code`` of any
       OEBB-sourced station.
    """
    vor_entries: list[Mapping[str, object]] = []
    oebb_codes: set[object] = set()

    for entry in stations:
        sources = _extract_source_tokens(entry.get("source"))
        if "vor" in sources:
            vor_entries.append(entry)
        if "oebb" in sources:
            bst_code = entry.get("bst_code")
            if bst_code:
                oebb_codes.add(bst_code)

    if len(vor_entries) < 2:
        yield ProviderIssue(
            identifier="<global>",
            name="<global>",
            reason="Need at least two VOR entries",
        )
        return

    for entry in vor_entries:
        identifier = _format_identifier(entry)
        name = str(entry.get("name", "")).strip() or "<unknown>"
        for key in ("bst_id", "bst_code"):
            value = entry.get(key)
            if not isinstance(value, str) or not _VOR_ID_PATTERN.fullmatch(value):
                yield ProviderIssue(
                    identifier=identifier,
                    name=name,
                    reason=f"Invalid {key} for VOR: {value}",
                )

    for entry in vor_entries:
        if entry.get("bst_code") in oebb_codes:
            yield ProviderIssue(
                identifier=_format_identifier(entry),
                name=str(entry.get("name", "")).strip() or "<unknown>",
                reason="VOR bst_code collides with OEBB",
            )


def _find_naming_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[NamingIssue]:
    """Validate canonical-name policy and flag-consistency invariants.

    Three checks:

    1. **Uniqueness** – the ``name`` field is the canonical display label
       for a station and must not be shared between two distinct entries.
    2. **Source-field formatting** – the comma-separated provider source
       string must not contain whitespace inside tokens (e.g.
       ``"google_places, vor"``). Whitespace breaks naive ``==``-based
       lookup branches and signals an unnormalized write path.
    3. **Vienna/pendler mutual exclusivity** – ``in_vienna`` and ``pendler``
       partition the directory: every entry is *either* inside the city
       limits *or* a commuter-belt station outside, never both. The
       exceptions are ``type: manual_foreign_city`` (München, Roma) and
       ``type: manual_distant_at`` (Salzburg, Graz, Linz etc.) where
       both flags may legitimately be ``false``.
    """
    name_to_identifiers: dict[str, list[str]] = defaultdict(list)
    for entry in stations:
        name_obj = entry.get("name")
        if not isinstance(name_obj, str):
            continue
        name = name_obj.strip()
        if not name:
            continue
        name_to_identifiers[name].append(_format_identifier(entry))

    for name, identifiers in name_to_identifiers.items():
        if len(identifiers) > 1:
            for identifier in identifiers:
                yield NamingIssue(
                    identifier=identifier,
                    name=name,
                    reason=(
                        f"canonical name {name!r} is not unique "
                        f"(also used by {', '.join(other for other in identifiers if other != identifier)})"
                    ),
                )

    for entry in stations:
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            continue
        # The format is comma-separated tokens; only the comma is a valid
        # delimiter. Any internal whitespace (including the leading or
        # trailing whitespace around a token) signals inconsistent
        # serialisation.
        if any(part.strip() != part for part in source.split(",")) or " " in source:
            identifier = _format_identifier(entry)
            name = str(entry.get("name", "")).strip() or "<unknown>"
            yield NamingIssue(
                identifier=identifier,
                name=name,
                reason=f"source field has whitespace: {source!r} (expected comma-separated, no spaces)",
            )

    for entry in stations:
        in_vienna = bool(entry.get("in_vienna"))
        pendler = bool(entry.get("pendler"))
        identifier = _format_identifier(entry)
        name = str(entry.get("name", "")).strip() or "<unknown>"
        entry_type = entry.get("type")

        if in_vienna and pendler:
            yield NamingIssue(
                identifier=identifier,
                name=name,
                reason=(
                    "in_vienna and pendler are both true — flags must be "
                    "mutually exclusive (a Vienna station cannot also be a "
                    "commuter-belt station)"
                ),
            )
        elif (
            not in_vienna
            and not pendler
            and entry_type not in ("manual_foreign_city", "manual_distant_at")
        ):
            yield NamingIssue(
                identifier=identifier,
                name=name,
                reason=(
                    "in_vienna and pendler are both false — entry should "
                    "either be classified as a Vienna station, a commuter "
                    "station, or marked with type='manual_foreign_city' "
                    "or type='manual_distant_at'"
                ),
            )


def _find_security_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[SecurityIssue]:
    for entry in stations:
        identifier = _format_identifier(entry)
        name = str(entry.get("name", "")).strip() or "<unknown>"

        # Check name
        if _UNSAFE_CHARS_RE.search(name):
            yield SecurityIssue(
                identifier=identifier,
                name=name,
                reason=f"Unsafe characters in name: {name!r}"
            )

        # Check bst_code
        bst_code = str(entry.get("bst_code") or "")
        if _UNSAFE_CHARS_RE.search(bst_code):
            yield SecurityIssue(
                identifier=identifier,
                name=name,
                reason=f"Unsafe characters in bst_code: {bst_code!r}"
            )

        # Check vor_id
        vor_id = str(entry.get("vor_id") or "")
        if _UNSAFE_CHARS_RE.search(vor_id):
            yield SecurityIssue(
                identifier=identifier,
                name=name,
                reason=f"Unsafe characters in vor_id: {vor_id!r}"
            )

        # Check aliases
        aliases_obj = entry.get("aliases")
        if isinstance(aliases_obj, Sequence) and not isinstance(aliases_obj, (str, bytes)):
            for alias in aliases_obj:
                if isinstance(alias, str) and _UNSAFE_CHARS_RE.search(alias):
                    yield SecurityIssue(
                        identifier=identifier,
                        name=name,
                        reason=f"Unsafe characters in alias: {alias!r}"
                    )
