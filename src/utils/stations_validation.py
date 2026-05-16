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
import io
import json
import logging
import math
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence

from src.utils.files import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
    read_capped_text,
)
from src.utils.stations import MAX_STATIONS_FILE_BYTES, _normalize_token
from src.utils.text import escape_markdown, normalise_markdown_text

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
class IdentityFieldConflict:
    """Two stations declaring the same value in a structural identifier field.

    Caught the 2026-05-16 Innenstadt-U-Bahn drift: ten ``wl_diva`` values
    (Herrengasse, Schwedenplatz, Volkstheater, …) were shared between a
    ``google_places``-sourced stub and the canonical ``source: wl`` entry
    because ``scripts/update_wl_stations.py:merge_into_stations``
    indexed existing entries by ``vor_id`` / ``bst_id`` / ``name`` but
    not by ``wl_diva``. ``_find_cross_station_id_conflicts`` did not
    fire — it only compares aliases against other stations' identity
    fields, not identity-vs-identity. ``_find_duplicate_coordinate_
    groups`` did not fire either — the stub and the master coordinates
    differed by ~7-50 m, below the rounding granularity but above the
    5-decimal-place bucket boundary.

    Distinct from :class:`CrossStationIDIssue`: this dataclass flags a
    raw identity-field collision (same ``wl_diva`` declared by two
    entries), whereas ``CrossStationIDIssue`` captures the more
    indirect case where one entry's *alias* shadows another entry's
    identity field.
    """

    field: str
    value: str
    identifiers: tuple[str, ...]
    names: tuple[str, ...]


# Per-cell length cap applied at every ``stations.json``-derived
# Markdown sink in :meth:`ValidationReport.to_markdown`. Mirrors the
# canonical ``_DASHBOARD_FIELD_MAX_LEN`` constant in
# ``scripts/generate_markdown_stats.py``: the report renders bullet
# lines that already include identifier + name + reason from the same
# row; an additional render-side cap keeps the layout legible even if
# a future writer persists multi-KiB blobs to the source field.
_REPORT_FIELD_MAX_LEN = 400


def _safe_md(text: object) -> str:
    """Render ``text`` for inclusion as bullet body in the validation report.

    Composes :func:`normalise_markdown_text` (strips C0/C1 controls +
    Trojan-Source / line-terminator union + ZWSP family + BiDi marks,
    collapses whitespace, caps length) with :func:`escape_markdown`
    (HTML-escapes + backslash-escapes the CommonMark structural set
    ``[]()*_`@<>``). Mirrors the canonical defence pattern applied at
    every other Markdown-renderer boundary in the project — see the
    module-level threat model in :meth:`ValidationReport.to_markdown`.
    """
    return escape_markdown(
        normalise_markdown_text(str(text), max_len=_REPORT_FIELD_MAX_LEN)
    )


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
    # Defaulted last so existing call sites that built ``ValidationReport``
    # positionally before the 2026-05-16 PR #1539 addition keep working
    # — adopted by the test fixtures + ``scripts/update_all_stations.py``
    # auto-quarantine path.
    identity_field_conflicts: tuple[IdentityFieldConflict, ...] = ()

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
            or self.identity_field_conflicts
        )

    def to_markdown(self) -> str:
        # Security (Markdown injection at the public-artefact renderer
        # boundary): every text field interpolated below carries
        # operator-controlled data sourced from ``stations.json`` — which
        # is in turn populated by cron-driven scripts that fan out to
        # external API surfaces (VOR/OEBB/Wiener Linien/Google Places/
        # OSM Overpass). A compromised upstream / DNS-hijack / MITM (or
        # any future fetch path that does not pin the host) injects
        # arbitrary ``name`` / ``bst_code`` / ``vor_id`` / ``alias``
        # bytes that flow VERBATIM into ``docs/stations_validation_
        # report.md`` — auto-committed by the ``update-stations.yml``
        # cron workflow and the ``manual-full-refresh.yml`` workflow,
        # then rendered on github.com (and every operator's IDE /
        # Markdown viewer / GitHub Pages mirror). The canonical
        # ``escape_markdown`` + ``normalise_markdown_text`` defence pair
        # mirrors the renderer-boundary pattern applied in
        # ``scripts/generate_markdown_stats.py`` and
        # ``src/feed/reporting.py`` (2026-05-09 Markdown Injection Drift
        # rounds). ``_safe_md`` (module-level helper) strips C0/C1 controls
        # + Trojan-Source / line-terminator union + ZWSP family + BiDi
        # marks (via ``normalise_markdown_text``), then backslash-
        # escapes the CommonMark structural set ``[]()*_\`@<>`` and
        # HTML-escapes ``&<>"'`` (via ``escape_markdown``). Layered
        # defence: the upstream ``_collect_blocking_issues`` gate aborts
        # the ``update_all_stations.py`` commit when ``_UNSAFE_CHARS_RE``
        # fires on ``<>`` / ASCII C0 / BiDi, but that gate is ONLY
        # active in the orchestrator script — the standalone CLI
        # invocation (``python -m src.cli stations validate``), the
        # ``manual-full-refresh.yml`` workflow's regenerate-step, and
        # any future direct ``to_markdown()`` caller bypass it
        # entirely. Sanitising at THIS boundary closes every code path
        # in one cut.
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
        lines.append(f"*Identity field conflicts*: {len(self.identity_field_conflicts)}")
        lines.append(f"*Naming issues*: {len(self.naming_issues)}")
        lines.append("")

        if self.security_issues:
            lines.append("## Security warnings (potential XSS/Injection)")
            for sec in self.security_issues:
                lines.append(
                    f"- {_safe_md(sec.identifier)} ({_safe_md(sec.name)}): {_safe_md(sec.reason)}"
                )
            lines.append("")

        if self.provider_issues:
            lines.append("## Provider issues (VOR/OEBB)")
            for prov in self.provider_issues:
                lines.append(
                    f"- {_safe_md(prov.identifier)} ({_safe_md(prov.name)}): {_safe_md(prov.reason)}"
                )
            lines.append("")

        if self.cross_station_id_issues:
            lines.append("## Cross station ID issues")
            for cross in self.cross_station_id_issues:
                lines.append(
                    f"- {_safe_md(cross.identifier)} ({_safe_md(cross.name)}): "
                    f"alias '{_safe_md(cross.alias)}' collides with "
                    f"{_safe_md(cross.colliding_field)} of "
                    f"{_safe_md(cross.colliding_identifier)} ({_safe_md(cross.colliding_name)})"
                )
            lines.append("")

        if self.identity_field_conflicts:
            lines.append("## Identity field conflicts")
            for conflict in self.identity_field_conflicts:
                joined_ids = ", ".join(_safe_md(ident) for ident in conflict.identifiers)
                joined_names = ", ".join(_safe_md(name) for name in conflict.names)
                lines.append(
                    f"- {_safe_md(conflict.field)}={_safe_md(conflict.value)} "
                    f"shared by [{joined_ids}] ({joined_names})"
                )
            lines.append("")

        if self.duplicates:
            lines.append("## Geographic duplicates")
            for group in self.duplicates:
                joined_ids = ", ".join(_safe_md(ident) for ident in group.identifiers)
                lines.append(
                    f"- ({group.latitude:.5f}, {group.longitude:.5f}) → {joined_ids}"
                )
            lines.append("")

        if self.alias_issues:
            lines.append("## Alias issues")
            for ali in self.alias_issues:
                lines.append(
                    f"- {_safe_md(ali.identifier)} ({_safe_md(ali.name)}): {_safe_md(ali.reason)}"
                )
            lines.append("")

        if self.coordinate_issues:
            lines.append("## Coordinate anomalies")
            for coord in self.coordinate_issues:
                lines.append(
                    f"- {_safe_md(coord.identifier)} ({_safe_md(coord.name)}): {_safe_md(coord.reason)}"
                )
            lines.append("")

        if self.gtfs_issues:
            lines.append("## GTFS mismatches")
            for gtfs in self.gtfs_issues:
                lines.append(
                    f"- {_safe_md(gtfs.identifier)} ({_safe_md(gtfs.name)}) → "
                    f"missing stop_id {_safe_md(gtfs.vor_id)}"
                )
            lines.append("")

        if self.naming_issues:
            lines.append("## Naming issues")
            for naming in self.naming_issues:
                lines.append(
                    f"- {_safe_md(naming.identifier)} ({_safe_md(naming.name)}): {_safe_md(naming.reason)}"
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
    identity_field_conflicts = tuple(_find_identity_field_conflicts(stations))

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
    if isinstance(bst_id, int):
        parts.append(f"bst:{bst_id}")
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


# Unsafe character class for ``stations.json`` validation. Sized as the
# UNION of (a) the legacy structural-injection set (``<``/``>`` for HTML,
# ASCII C0 controls excluding ``\t``/``\n``/``\r``) and (b) every code
# point covered by the canonical log sanitiser
# ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``. The 2026-05-09
# BiDi-Mark Drift Round 2 entry in ``.jules/sentinel.md`` flagged the
# divergence between the two regexes as the next drift candidate: the
# canonical sanitiser already strips ``\u061c`` (ALM), ``\u200b-\u200f``
# (ZWSP/ZWNJ/ZWJ/LRM/RLM), and ``\ufeff`` (BOM), but the validator did
# not, so a planted ``stations.json`` carrying any of those characters
# in ``name``/``bst_code``/``vor_id``/``aliases`` slipped past
# ``_find_security_issues`` and flowed verbatim into the published feed
# and operator-facing log lines (Trojan-Source / log-forging primitive
# per CVE-2021-42574). The widened set keeps the two regexes in sync;
# any future widening of ``_INVISIBLE_DANGEROUS_RE`` MUST be reflected
# here \u2014 pinned by ``test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set``.
#
# 2026-05-10 "8-bit C1 / DEL Drift": ``_INVISIBLE_DANGEROUS_RE`` was
# widened to ``\x7f-\x9f`` (DEL + 32 ECMA-48 C1 controls, including
# the 8-bit terminal-escape primitives ``\x9b`` CSI / ``\x9d`` OSC /
# ``\x90`` DCS) so the ``strip_control_chars=False`` sibling sinks
# inherit the defence. The stations validator is widened in the same
# PR to mirror the new canonical floor \u2014 a planted ``stations.json``
# carrying ``\x9b...m`` in a ``name`` / ``bst_code`` / ``vor_id`` /
# ``aliases`` field would otherwise flow through to the GitHub Issue
# body the directory validator emits and trigger SGR colour
# interpretation in any 8-bit-C1-honouring terminal that views it.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with the canonical _INVISIBLE_DANGEROUS_RE union to cover
# the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). Tag bytes smuggled into a station
# name / aliases / bst_code / vor_id field by a compromised upstream
# slip past the validator and reach the published feed item that
# keys off the station name.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Pre-fix a planted stations.json carrying any zero-width Format
# primitive in a name / aliases / bst_code / vor_id field would slip
# past the validator and reach the published feed item that keys off
# the station name. Aliases like "Wien Hbf<U+2060>" and "Wien Hbf"
# would aggregate as DISTINCT entries downstream because the dedup
# key is byte-equality. The U+2060..U+2069 range folds in the
# existing BiDi-isolate band; reserved U+2065 has no defined meaning.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# _INVISIBLE_DANGEROUS_RE union to cover the remaining 13 Unicode
# Cf-class bands (44 code points): U+00AD SOFT HYPHEN (the most
# impactful omission - rendered zero-width unconditionally in every
# Markdown / RSS / terminal renderer), U+0600..U+0605 Arabic prefix
# marks, U+06DD, U+070F, U+0890..U+0891, U+08E2, U+206A..U+206F
# deprecated BiDi controls (folds the existing U+2060..U+2069 band
# into U+2060..U+206F), U+FFF9..U+FFFB INTERLINEAR ANNOTATION,
# U+110BD/U+110CD KAITHI, U+13430..U+13438 EGYPTIAN HIEROGLYPH,
# U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and U+1D173..U+1D17A MUSICAL
# SYMBOL formatting. Pre-fix aliases like "Wien Hbf<U+00AD>" and
# "Wien Hbf" aggregated as DISTINCT entries downstream - the SOFT
# HYPHEN renders zero-width but the dedup key is byte-equality.
_UNSAFE_CHARS_RE = re.compile(
    r"[<>\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)

# Pattern for the synthetic ``bst_id``/``bst_code`` values assigned to
# VOR-sourced station entries (e.g. ``900100``–``900112`` for the Wien
# departure-board stops). Real VOR/HAFAS stop IDs are 9 digits and live in the
# ``vor_id`` field — they are not validated here. The 5-digit form is kept as
# a tolerated fallback for legacy synthetic ids such as ``93010``.
_VOR_ID_PATTERN = re.compile(r"9\d{4,5}")


def _find_cross_station_id_conflicts(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[CrossStationIDIssue]:
    id_map: dict[str, list[tuple[Mapping[str, object], str]]] = defaultdict(list)

    for entry in stations:
        for field in ("bst_id", "bst_code", "vor_id", "wl_diva"):
            val = entry.get(field)
            if isinstance(val, str | int):
                norm_val = _normalize_token(str(val))
                if norm_val:
                    id_map[norm_val].append((entry, field))

    for entry in stations:
        aliases_obj = entry.get("aliases")
        if not isinstance(aliases_obj, Sequence) or isinstance(aliases_obj, str | bytes):
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


def _find_identity_field_conflicts(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[IdentityFieldConflict]:
    """Yield one issue per value that appears on more than one station.

    Checks each of the four structural identifier fields
    (``wl_diva`` / ``vor_id`` / ``bst_id`` / ``bst_code``). The
    project's eindeutigkeits-Garantie pins these as primary keys; any
    duplicate means two stations claim the same physical asset (post
    PR #1538 a ``google_places`` stub and a ``source: wl`` master
    shared the same DIVA for ten Innenstadt-U-Bahn stops because the
    WL merge step indexed by name and ``_normalize_key`` rendered
    ``Herrengasse`` and ``Wien Herrengasse (WL)`` as distinct keys).

    Whitespace-stripped + lower-cased ``int`` / ``str`` values are
    compared; ``None`` and empty strings are ignored.  Distinct from
    :func:`_find_cross_station_id_conflicts`, which fires only when an
    *alias* on one station shadows an *identity* field on a different
    station — this function fires on raw identity collisions.
    """
    for field in ("wl_diva", "vor_id", "bst_id", "bst_code"):
        seen: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for entry in stations:
            val = entry.get(field)
            if not isinstance(val, str | int):
                continue
            key = str(val).strip()
            if not key:
                continue
            seen[key].append(entry)
        for value, entries in seen.items():
            if len(entries) <= 1:
                continue
            identifiers = tuple(_format_identifier(e) for e in entries)
            names = tuple(
                str(e.get("name", "")).strip() or "<unknown>" for e in entries
            )
            yield IdentityFieldConflict(
                field=field,
                value=value,
                identifiers=identifiers,
                names=names,
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
    """Validate flag-consistency invariants.

    Two checks (was three pre-2026-05-12):

    1. **Source-field formatting** – the comma-separated provider source
       string must not contain whitespace inside tokens (e.g.
       ``"google_places, vor"``). Whitespace breaks naive ``==``-based
       lookup branches and signals an unnormalized write path.
    2. **Vienna/pendler mutual exclusivity** – ``in_vienna`` and ``pendler``
       partition the directory: every entry is *either* inside the city
       limits *or* a commuter-belt station outside, never both. The
       exceptions are ``type: manual_foreign_city`` (München, Roma) and
       ``type: manual_distant_at`` (Salzburg, Graz, Linz etc.) where
       both flags may legitimately be ``false``.

    The pre-2026-05-12 canonical-name uniqueness check has been
    removed. ``name`` is the operator-facing display label, not a
    primary key; structured identifiers (``wl_diva``, ``bst_id``,
    ``vor_id``, ``bst_code``) carry the project's eindeutigkeits-
    Garantie. Wiener Linien's OGD-Echtzeit ``PlatformText`` is
    legitimately duplicated for the ten remaining non-mergeable
    multi-DIVA groups (Lokalbahn × 4, Bahnhof × 2, etc.); blocking
    them on name-uniqueness produced exactly the RSS feed clutter
    (``Wien Bahnhof (WL 60205022)``) that the disambiguation work
    in PR #1448 had to introduce. Removing the gate lets the
    upstream ``_disambiguate_duplicate_names`` step retire too, so
    the published feed shows ``Wien Bahnhof (WL)`` without a DIVA
    suffix.
    """

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
        if isinstance(aliases_obj, Sequence) and not isinstance(aliases_obj, str | bytes):
            for alias in aliases_obj:
                if isinstance(alias, str) and _UNSAFE_CHARS_RE.search(alias):
                    yield SecurityIssue(
                        identifier=identifier,
                        name=name,
                        reason=f"Unsafe characters in alias: {alias!r}"
                    )
