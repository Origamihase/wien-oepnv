"""Station validation: duplicates, aliases, coordinates, GTFS and the identifier label.

Moved verbatim from ``src/utils/stations_validation.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence

from src.utils.station_checks.issues import (
    AliasIssue,
    CoordinateIssue,
    DuplicateGroup,
    GTFSIssue,
)


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
    if isinstance(value, int | float):
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
    # Canonical writer serialises ``bst_id`` as ``str`` (see
    # ``scripts/update_station_directory.py`` — ``"bst_id": str(self.bst_id)``),
    # so the previous ``isinstance(bst_id, int)``-only branch was dead
    # code on every real entry — silently omitting ``bst_id`` from the
    # identifier the auto-quarantine path uses for distinctness.
    # Sibling readers in this same file already use ``str | int``
    # (``_find_cross_station_id_conflicts``, ``_find_identity_field_conflicts``).
    # Latent hazard: a future merge yielding two ``bst_code``-less entries
    # distinguished only by ``bst_id`` would collapse to one identifier
    # and re-trigger the mass-quarantine failure the comment below
    # documents for ``source="wl"``.
    if isinstance(bst_id, int):
        parts.append(f"bst:{bst_id}")
    elif isinstance(bst_id, str) and bst_id.strip():
        parts.append(f"bst:{bst_id.strip()}")
    bst_code = entry.get("bst_code")
    if isinstance(bst_code, str) and bst_code.strip():
        parts.append(f"code:{bst_code.strip()}")
    # WL-only entries (no ÖBB bst_id / bst_code) need a distinct
    # identifier — otherwise ``_partition_stations`` collapses every
    # source="wl" entry to the same ``"source:wl"`` key, and the
    # naming-issue auto-quarantine path matches and removes the entire
    # WL set instead of just the entries with a real naming collision.
    # Post-PR #1446 cron tick a23a2a7 confirmed this exact failure mode:
    # 30 genuine "canonical name not unique" issues fanned out into 1759
    # quarantined WL entries because all of them shared the identifier
    # ``source:wl``.
    wl_diva = entry.get("wl_diva")
    if isinstance(wl_diva, str) and wl_diva.strip():
        parts.append(f"wl_diva:{wl_diva.strip()}")
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

        if not isinstance(aliases_obj, Sequence) or isinstance(aliases_obj, str | bytes):
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
        # Accept ``str | int`` for the identity fields (bst_code / vor_id),
        # matching the sibling validators _find_cross_station_id_conflicts and
        # _find_identity_field_conflicts. A directory entry whose bst_code is a
        # JSON integer would otherwise be silently skipped here, so a genuinely
        # missing required alias for that station went unreported (bug #8).
        bst_code = entry.get("bst_code")
        if isinstance(bst_code, str | int) and str(bst_code).strip():
            required.append(str(bst_code).strip())
        vor_id = entry.get("vor_id")
        if isinstance(vor_id, str | int) and str(vor_id).strip():
            required.append(str(vor_id).strip())

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
            entry_type_raw = entry.get("type")
            entry_type = entry_type_raw.strip() if isinstance(entry_type_raw, str) else None
            if entry_type in ("manual_foreign_city", "manual_distant_at"):
                # Manual cross-country entries (München, Roma, Berlin Hbf,
                # Salzburg Hbf etc.) carry coordinates that are by design
                # outside the Wien-Region bounding box. The schema docstring
                # explicitly tolerates this — skip the bounds check and
                # don't pollute the report with 21 false positives.
                continue
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
