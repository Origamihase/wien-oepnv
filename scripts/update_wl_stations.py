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
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_is_in_vienna() -> Callable[..., bool]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.stations")
    return cast(Callable[..., bool], module.is_in_vienna)


def _load_atomic_write() -> Callable[..., Any]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.files")
    return cast(Callable[..., Any], module.atomic_write)


def _load_read_capped_json() -> Callable[..., Any]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.files")
    return cast(Callable[..., Any], module.read_capped_json)


def _load_read_capped_text() -> Callable[..., Any]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.files")
    return cast(Callable[..., Any], module.read_capped_text)


def _load_scrub_trojan_source_primitives() -> Callable[..., Any]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.serialize")
    return cast(Callable[..., Any], module.scrub_trojan_source_primitives)


BASE_DIR = _project_root()
is_in_vienna = _load_is_in_vienna()
atomic_write = _load_atomic_write()
read_capped_json = _load_read_capped_json()
read_capped_text = _load_read_capped_text()
scrub_trojan_source_primitives = _load_scrub_trojan_source_primitives()

# Security cap against wide-but-flat JSON size-bomb attacks. Mirrors the
# canonical ``MAX_*_FILE_BYTES`` contract from ``src/utils/cache.py`` /
# ``src/utils/stations.py``: depth-bomb catch alone misses ``MemoryError``
# (a ``BaseException`` subclass) so a planted-huge stations.json
# (~1 GiB of ``[0,0,…]``) buffered via ``json.load`` propagates past
# the loader and crashes the WL merge — running under
# ``subprocess.run(check=True)`` aborts the whole cron pipeline. 50 MiB
# is ~285x the production stations.json so legitimate state is never
# rejected.
MAX_JSON_FILE_BYTES = 50 * 1024 * 1024
# CSV size-bomb axis: ``_dict_reader`` previously fed the operator-
# supplied haltestellen / haltepunkte CSVs into ``csv.DictReader(handle)``
# directly, letting ``handle.readline()`` buffer GiB-sized single-line
# payloads. Routes through ``read_capped_text`` -> ``io.StringIO`` to
# bound the allocation.
MAX_WL_CSV_BYTES = 50 * 1024 * 1024
DEFAULT_HALTEPUNKTE = BASE_DIR / "data" / "wienerlinien-ogd-haltepunkte.csv"
DEFAULT_HALTESTELLEN = BASE_DIR / "data" / "wienerlinien-ogd-haltestellen.csv"
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
OGD_DOWNLOAD_TIMEOUT_SECONDS = 30
USER_AGENT = (
    "wien-oepnv station updater "
    "(https://github.com/Origamihase/wien-oepnv)"
)

log = logging.getLogger("update_wl_stations")


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
            "Download the latest WL OGD haltestellen/haltepunkte CSVs from "
            "data.wien.gv.at before merging (default: enabled). On failure, "
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


def _download_ogd_csv(url: str, target: Path) -> bool:
    """Download a Wiener Linien OGD CSV and write it to *target* atomically.

    Returns ``True`` on success, ``False`` on any error. Errors are logged
    but not raised, so callers can fall back to existing local files.
    """
    base_dir = _project_root()
    if str(base_dir) not in sys.path:  # pragma: no cover - defensive
        sys.path.insert(0, str(base_dir))
    try:
        from src.utils.http import fetch_content_safe, session_with_retries
    except ImportError:  # pragma: no cover - defensive
        log.warning("HTTP utilities unavailable; cannot download %s", url)
        return False

    log.info("Downloading WL OGD: %s", url)
    try:
        with session_with_retries(USER_AGENT) as session:
            content = fetch_content_safe(
                session, url, timeout=OGD_DOWNLOAD_TIMEOUT_SECONDS
            )
    except Exception as exc:  # pragma: no cover - network-dependent
        log.warning("Failed to download %s (%s); using local file if present", url, exc)
        return False

    if not content:
        log.warning("Empty response from %s; using local file if present", url)
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(target, mode="wb", permissions=0o644) as handle:
        handle.write(content)
    log.info("Saved %s (%d bytes)", target, len(content))
    return True


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


def _normalize_key(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


class NormalizedRow:
    """Wrapper around a CSV row that allows fuzzy column access."""

    def __init__(self, row: dict[str, str | None]):
        self._row = row
        self._map = {_normalize_key(key): key for key in row if key}

    def get(self, *candidates: str) -> str:
        for candidate in candidates:
            key = self._map.get(_normalize_key(candidate))
            if key is None:
                continue
            value = self._row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""


def _coerce_float(value: str) -> float | None:
    if not value:
        return None
    text = value.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


@dataclass
class Haltestelle:
    station_id: str
    name: str
    diva: str | None


@dataclass
class Haltepunkt:
    station_id: str
    stop_id: str
    name: str
    latitude: float | None
    longitude: float | None


def _dict_reader(path: Path) -> Iterator[NormalizedRow]:
    # Security: see ``MAX_WL_CSV_BYTES`` for the canonical CSV size-
    # bomb defence shape (``read_capped_text`` -> ``io.StringIO`` ->
    # ``csv.DictReader``). FileNotFoundError is raised explicitly so
    # downstream callers can keep their existing ``except
    # FileNotFoundError`` branches; oversized files are silently
    # treated as missing (read_capped_text logs a warning).
    import io
    if not path.exists():
        raise FileNotFoundError(str(path))
    content = read_capped_text(
        path, MAX_WL_CSV_BYTES,
        encoding="utf-8-sig", label="WL CSV", logger=log,
    )
    if content is None:
        return
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    for row in reader:
        yield NormalizedRow({key or "": value for key, value in row.items()})


def load_haltestellen(path: Path) -> dict[str, Haltestelle]:
    mapping: dict[str, Haltestelle] = {}
    for row in _dict_reader(path):
        # The legacy data.wien.gv.at proxy CSV used HALTESTELLEN_ID /
        # NAME; the canonical wienerlinien.at OGD-Echtzeit CSV that
        # replaced it (post PR #1442) collapses station_id and diva
        # onto a single DIVA column and renames NAME → PlatformText.
        # Accept both via the fuzzy-key lookup so the loader survives
        # either upstream shape.
        station_id = row.get("HALTESTELLEN_ID", "ID", "DIVA")
        name = row.get("NAME", "PlatformText")
        diva = row.get("DIVA", "DIVANR") or None
        if not station_id or not name:
            continue
        mapping[station_id] = Haltestelle(
            station_id=station_id,
            name=name,
            diva=diva,
        )
    return mapping


def load_haltepunkte(path: Path) -> list[Haltepunkt]:
    haltepunkt_records: list[Haltepunkt] = []
    for row in _dict_reader(path):
        # Legacy proxy CSV joined on HALTESTELLEN_ID with a separate
        # STOP_ID / RBL_NUMMER per platform. The canonical
        # wienerlinien.at OGD-Echtzeit CSV exposes a StopID primary
        # key, joins haltestellen via DIVA, names the platform via
        # StopText, and uses Longitude / Latitude column headers.
        # Accept both shapes via fuzzy-key fallback.
        station_id = row.get("HALTESTELLEN_ID", "ID", "DIVA")
        stop_id = row.get("STOP_ID", "STOPID", "RBL_NUMMER", "RBLNR", "StopID")
        name = row.get("NAME", "HALTEPUNKTNAME", "StopText")
        lat = _coerce_float(row.get("WGS84_LAT", "LAT", "GEO_LAT", "Latitude"))
        lon = _coerce_float(row.get("WGS84_LON", "LON", "GEO_LON", "LONG", "Longitude"))
        if not station_id or not stop_id:
            continue
        haltepunkt_records.append(
            Haltepunkt(
                station_id=station_id,
                stop_id=stop_id,
                name=name,
                latitude=lat,
                longitude=lon,
            )
        )
    return haltepunkt_records


def _canonical_name(raw: str) -> str:
    cleaned = re.sub(r"\s+\([^)]*\)", "", raw).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if not cleaned:
        cleaned = raw.strip()
    if cleaned.casefold().startswith("wien"):
        base = cleaned
    else:
        base = f"Wien {cleaned}".strip()
    if "(WL)" not in base:
        base = f"{base} (WL)"
    return base


def _derive_bst_id(identifier: str | None) -> int | None:
    if not identifier:
        return None
    digits = re.sub(r"\D", "", identifier)
    if not digits:
        return None
    trimmed = digits[-8:]
    return int(f"9{trimmed.zfill(8)}")


def _derive_bst_code(name: str, identifier: str | None) -> str | None:
    cleaned = re.sub(r"\(WL\)", "", name).strip()
    cleaned = re.sub(r"(?i)^wien\s+", "", cleaned).strip()
    tokens = [token for token in re.split(r"[^A-Za-z0-9ÄÖÜäöüß]+", cleaned) if token]
    if tokens:
        primary = tokens[0][:3]
        if primary:
            return f"WL-{primary.upper()}"
    if identifier:
        digits = re.sub(r"\D", "", identifier)
        if digits:
            return f"WL-{digits[-3:]}"
    return None


def _aggregate_coordinates(stops: Iterable[Haltepunkt]) -> tuple[float | None, float | None]:
    latitudes: list[float] = []
    longitudes: list[float] = []
    for stop in stops:
        if stop.latitude is None or stop.longitude is None:
            continue
        latitudes.append(stop.latitude)
        longitudes.append(stop.longitude)
    if not latitudes or not longitudes:
        return None, None
    avg_lat = round(sum(latitudes) / len(latitudes), 6)
    avg_lon = round(sum(longitudes) / len(longitudes), 6)
    return avg_lat, avg_lon


def _alias_variants(
    station_name: str, canonical: str, resolved: str | None
) -> set[str]:
    base = f"Wien {station_name}".strip()
    variants = {
        canonical,
        base,
        f"{base} (WL)",
        f"{base} U",
        f"{base} U (VOR)",
        f"{base} Bahnhof",
        f"Bahnhof {base}",
        f"{base} Station",
    }
    english_base = base
    if base.lower().startswith("wien "):
        english_base = f"Vienna {base[5:]}".strip()
        variants.update(
            {
                english_base,
                f"{english_base} (WL)",
                f"{english_base} U",
                f"{english_base} U (VOR)",
                f"{english_base} Station",
            }
        )
    variants.add(base.replace(" ", "-"))
    variants.add(canonical.replace(" ", "-"))
    if resolved:
        variants.add(resolved)
        variants.add(f"{resolved} (VOR)")
    return {variant for variant in variants if variant.strip()}


def load_vor_mapping(path: Path) -> dict[str, Mapping[str, object]]:
    if not path.exists():
        log.info("No VOR mapping found at %s", path)
        return {}
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). Same
    # cron-pipeline blast radius as the sibling loader in
    # ``scripts/enrich_station_aliases.py``.
    raw = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="VOR mapping", logger=log,
    )
    if raw is None:
        log.warning("Could not parse VOR mapping %s (missing/invalid/oversized)", path)
        return {}
    mapping: dict[str, Mapping[str, object]] = {}
    if not isinstance(raw, list):
        return mapping
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        candidates = set()
        for key in ("station_name", "resolved_name"):
            text = str(entry.get(key) or "").strip()
            if text:
                candidates.add(_normalize_key(text))
        vor_id = str(entry.get("vor_id") or "").strip()
        if vor_id:
            candidates.add(_normalize_key(vor_id))
        for candidate in candidates:
            if candidate:
                mapping[candidate] = entry
    return mapping


def build_wl_entries(
    haltestellen: dict[str, Haltestelle],
    haltepunkte: Iterable[Haltepunkt],
    vor_mapping: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    grouped: dict[str, list[Haltepunkt]] = {}
    for halt in haltepunkte:
        station = haltestellen.get(halt.station_id)
        if station is None:
            continue
        key = station.diva or station.station_id
        grouped.setdefault(key, []).append(halt)

    entries: list[dict[str, object]] = []
    for stops in grouped.values():
        if not stops:
            continue
        station = haltestellen.get(stops[0].station_id)
        if station is None:
            continue
        station_identifier = station.diva or station.station_id
        aliases = {station.name}
        stops_payload = []
        for stop in stops:
            # Sanitise both WL ``StopText`` direction markers — '>' and
            # '<' are in the stations validator's ``_UNSAFE_CHARS_RE``
            # (XSS / HTML metacharacters). Replace '>' with U+2192 (→)
            # and '<' with U+2190 (←); both arrows are typographically
            # correct for "Richtung X" / "Aus Richtung X", outside the
            # unsafe-char regex, and preserve direction information for
            # the operator-facing permutations built downstream by
            # ``_alias_variants``.
            stop_name = stop.name.replace(">", "→").replace("<", "←")
            aliases.add(stop_name)
            # Do NOT add ``stop.stop_id`` to ``aliases``. In the legacy
            # ``data.wien.gv.at`` proxy CSV ``STOP_ID`` was an 8-digit
            # RBL-Nummer (semantic Echtzeit-Anker). In the canonical
            # ``wienerlinien.at`` OGD-Echtzeit CSV ``StopID`` collapsed
            # to a small in-CSV row counter (1, 2, 3, …) with no
            # cross-system meaning. Either way the per-platform RBL is
            # reachable via the structured ``wl_stops[].stop_id`` field
            # — adding it to ``aliases`` only invites cross-station-id
            # collisions with other entries' ``bst_id`` / ``wl_diva``.
            stops_payload.append(
                {
                    "stop_id": stop.stop_id,
                    "name": stop_name,
                    "latitude": stop.latitude,
                    "longitude": stop.longitude,
                }
            )
        # Do NOT add ``station.diva`` to ``aliases``. The DIVA is the
        # canonical WL identifier — it is exposed via the structured
        # ``wl_diva`` field on the entry and recognised as an
        # identity-class alias by ``src/utils/stations._station_lookup``
        # at lookup time. Duplicating it into ``aliases`` is both
        # redundant and dangerous: WL has renumbered DIVAs at least
        # once between the data.wien.gv.at proxy era and the current
        # wienerlinien.at OGD-Echtzeit era (e.g. ``60201076`` was
        # Karlsplatz pre-PR #1442 and is Ratzenhofergasse today). A
        # stale alias copy from a prior run trivially collides with
        # another entry's current ``wl_diva``.
        aliases.add(f"Wien {station.name}")
        canonical = _canonical_name(station.name)
        aliases.add(canonical)
        coords_checked = False
        in_vienna = False
        for stop in stops:
            if stop.latitude is None or stop.longitude is None:
                continue
            coords_checked = True
            if is_in_vienna(stop.latitude, stop.longitude):
                in_vienna = True
                break
        if not coords_checked:
            log.warning(
                "WL station %s (%s) lacks coordinates; falling back to name lookup",
                station.name,
                station_identifier,
            )
            in_vienna = is_in_vienna(station.name)
        latitude, longitude = _aggregate_coordinates(stops)
        vor_entry: Mapping[str, object] | None = None
        if vor_mapping:
            for candidate in (
                canonical,
                station.name,
                f"Wien {station.name}",
                station_identifier,
            ):
                key = _normalize_key(str(candidate))
                if key and key in vor_mapping:
                    vor_entry = vor_mapping[key]
                    break
        resolved_name = ""
        if vor_entry:
            vor_id = str(vor_entry.get("vor_id") or "").strip()
            if vor_id:
                aliases.add(vor_id)
            resolved_name = str(vor_entry.get("resolved_name") or "").strip()
            if resolved_name:
                aliases.add(resolved_name)
            if latitude is None or longitude is None:
                lat_val = vor_entry.get("latitude")
                lon_val = vor_entry.get("longitude")
                if isinstance(lat_val, int | float) and isinstance(lon_val, int | float):
                    latitude = round(float(lat_val), 6)
                    longitude = round(float(lon_val), 6)
        aliases.update(_alias_variants(station.name, canonical, resolved_name or None))
        # Mirror the legacy WL-auto-promote heuristic from
        # ``update_station_directory._annotate_station_flags`` (a WL-sourced
        # station outside Vienna becomes pendler=True; see the regression
        # test ``test_wl_outside_station_becomes_pendler``). Without this,
        # unmatched WL entries from ``build_wl_entries`` reach
        # ``merge_into_stations`` with ``in_vienna=False, pendler=False``
        # and trip ``_find_naming_issues`` → auto-quarantine. ÖBB-mirrored
        # WL stations are unaffected because ``_merge_wl_payload`` does
        # not overwrite the existing entry's flag pair.
        entry = {
            "name": canonical,
            "in_vienna": in_vienna,
            "pendler": not in_vienna,
            "wl_diva": station_identifier,
            "wl_stops": sorted(
                stops_payload,
                key=lambda item: str(item["stop_id"]),
            ),
            "aliases": sorted(
                {alias for alias in aliases if isinstance(alias, str) and alias.strip()}
            ),
            "source": "wl",
        }
        # Do NOT synthesise ``bst_id`` / ``bst_code`` for WL-only
        # entries. ``bst_id`` / ``bst_code`` are ÖBB's namespace
        # ("Betriebsstellennummer" / Stellencode); reusing them for
        # WL-only entries via the prior ``9{DIVA}`` / ``WL-{tok[:3]}``
        # heuristics produced two failure modes at production scale:
        #
        #   (a) ``_find_cross_station_id_conflicts`` flagged
        #       ``alias DIVA`` collisions against synthetic
        #       ``bst_id = 9{DIVA}`` on other entries.
        #   (b) ``_derive_bst_code`` truncated names to the first
        #       three letters, producing hundreds of duplicates
        #       (``WL-ABS`` for both Absbergbrücke and Absberggasse,
        #       ``WL-ADA`` for Ada-Christen-Gasse and four others, …).
        #
        # The canonical WL identifier is ``wl_diva`` (already set
        # above), which lives in its own namespace and never collides.
        # Existing source="vor"/"google_places"/"manual" entries also
        # carry no ``bst_id`` for similar reasons; downstream lookup
        # is via the structured per-source fields, not bst_id-as-alias.
        if latitude is not None and longitude is not None:
            entry["latitude"] = latitude
            entry["longitude"] = longitude
        if vor_entry:
            vor_id = str(vor_entry.get("vor_id") or "").strip()
            if vor_id:
                entry["vor_id"] = vor_id
        entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("name")), str(item.get("wl_diva"))))
    return entries


def _normalize_sources(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, Iterable):  # pragma: no cover - defensive guard
        candidates = list(value)
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = str(item).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _merge_sources(*values: object | None) -> str:
    """Merge multiple source-token lists into a single comma-separated string.

    Output is alphabetically sorted so that two callers that emit the same
    set of providers in different orders produce identical strings —
    e.g. "google_places,oebb" instead of "oebb,google_places". The sort
    matches the convention already used by ``src/places/merge.py:182``.
    """
    merged: set[str] = set()
    for value in values:
        for item in _normalize_sources(value):
            merged.add(item)
    return ",".join(sorted(merged))


def _ensure_sorted_aliases(entry: dict[str, object]) -> None:
    aliases = entry.get("aliases")
    if not isinstance(aliases, list):
        return
    unique: set[str] = set()
    cleaned: list[str] = []
    for alias in aliases:
        if not isinstance(alias, str):
            continue
        text = alias.strip()
        if not text or text in unique:
            continue
        unique.add(text)
        cleaned.append(text)
    cleaned.sort()
    entry["aliases"] = cleaned


_WL_DIVA_ALIAS_RE = re.compile(r"60\d{6}")


def _is_stale_wl_diva_alias(alias: object, current_wl_diva: str) -> bool:
    """Return True for aliases that look like a Wiener Linien DIVA value
    but do not match the current ``wl_diva`` of the entry.

    Wiener Linien renumbered DIVAs at least once between the
    ``data.wien.gv.at`` proxy era and the current ``wienerlinien.at``
    OGD-Echtzeit schema (e.g. ``60201076`` was Karlsplatz pre-PR #1442
    and is Ratzenhofergasse today). Because ``merge_into_stations``
    preserves existing aliases across runs, stale DIVA copies pinned by
    an earlier cron tick survive and trivially collide with the current
    ``wl_diva`` of a *different* entry, tripping
    ``_find_cross_station_id_conflicts`` and the auto-quarantine path.

    Heuristic: any alias matching the WL DIVA shape (``60`` + 6 digits,
    yielding 8 digits total) that is *not* the entry's current
    ``wl_diva`` value is treated as stale and dropped at merge time.
    Real per-stop RBL identifiers remain available via the structured
    ``wl_stops[].stop_id`` field.
    """
    if not isinstance(alias, str):
        return False
    stripped = alias.strip()
    if not _WL_DIVA_ALIAS_RE.fullmatch(stripped):
        return False
    return stripped != current_wl_diva


def _merge_wl_payload(target: dict[str, object], payload: Mapping[str, object]) -> None:
    if payload.get("wl_diva"):
        target["wl_diva"] = payload["wl_diva"]

    wl_stops = payload.get("wl_stops")
    if isinstance(wl_stops, list):
        target["wl_stops"] = wl_stops

    target["source"] = _merge_sources(target.get("source"), payload.get("source"), "wl")

    from typing import cast
    current_wl_diva = str(target.get("wl_diva") or "").strip()
    existing_aliases: list[str] = []
    raw_target_aliases = target.get("aliases")
    if isinstance(raw_target_aliases, list):
        # Strip stale WL-DIVA aliases left over from prior runs against
        # an older Wiener Linien numbering scheme — see
        # ``_is_stale_wl_diva_alias`` for the renumbering rationale.
        existing_aliases = [
            cast(str, a)
            for a in raw_target_aliases
            if not _is_stale_wl_diva_alias(a, current_wl_diva)
        ]

    incoming_aliases: list[str] = []
    raw_payload_aliases = payload.get("aliases")
    if isinstance(raw_payload_aliases, list):
        incoming_aliases = cast(list[str], list(raw_payload_aliases))
    target["aliases"] = existing_aliases + incoming_aliases
    _ensure_sorted_aliases(target)

    if target.get("latitude") in (None, "") and payload.get("latitude") is not None:
        target["latitude"] = payload["latitude"]
    if target.get("longitude") in (None, "") and payload.get("longitude") is not None:
        target["longitude"] = payload["longitude"]


def _lookup_candidates(index: Mapping[str, dict[str, object]], key: object | None) -> dict[str, object] | None:
    if key is None:
        return None
    text = str(key).strip()
    if not text:
        return None
    return index.get(text)


def merge_into_stations(
    stations_path: Path,
    wl_entries: list[dict[str, Any]],
) -> None:
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). The
    # depth-bomb-only catch missed ``MemoryError`` (a ``BaseException``
    # subclass) — a planted-huge stations.json would propagate past
    # the loader and crash the WL merge. On miss we start fresh and
    # let the merge restore the canonical schema for the next run.
    raw_data: object
    if stations_path.exists():
        loaded = read_capped_json(
            stations_path, MAX_JSON_FILE_BYTES, label="Stations", logger=log,
        )
        if loaded is None:
            log.warning(
                "stations.json could not be parsed (missing/invalid/oversized)"
                " – starting WL merge from empty state",
            )
            raw_data = []
        else:
            raw_data = loaded
    else:
        raw_data = []

    existing: list[dict[str, object]] = []

    if isinstance(raw_data, list):
        existing = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("stations"), list):
        existing = raw_data["stations"]
    else:
        raise ValueError("stations.json must contain a JSON array or a dict with a 'stations' array")

    filtered: list[dict[str, object]] = []
    vor_index: dict[str, dict[str, object]] = {}
    bst_index: dict[str, dict[str, object]] = {}
    name_index: dict[str, dict[str, object]] = {}

    for entry in existing:
        source = entry.get("source")
        if isinstance(source, str) and source.strip() == "wl":
            continue
        filtered.append(entry)

        vor_id = entry.get("vor_id")
        if vor_id is not None:
            key = str(vor_id).strip()
            if key and key not in vor_index:
                vor_index[key] = entry

        bst_id = entry.get("bst_id")
        if bst_id is not None:
            key = str(bst_id).strip()
            if key and key not in bst_index:
                bst_index[key] = entry

        name = entry.get("name")
        if isinstance(name, str):
            key = _normalize_key(name)
            if key and key not in name_index:
                name_index[key] = entry

    log.info("Keeping %d existing non-WL stations", len(filtered))

    unmatched: list[dict[str, object]] = []
    for payload in wl_entries:
        merged_into: dict[str, object] | None = None

        vor_id = payload.get("vor_id")
        merged_into = _lookup_candidates(vor_index, vor_id)

        if merged_into is None:
            bst_id = payload.get("bst_id")
            merged_into = _lookup_candidates(bst_index, bst_id)

        if merged_into is None:
            name = payload.get("name")
            if isinstance(name, str):
                merged_into = _lookup_candidates(name_index, name)

        if merged_into is not None:
            _merge_wl_payload(merged_into, payload)
            continue

        entry = dict(payload)
        entry["source"] = _merge_sources(payload.get("source"), "wl") or "wl"
        _ensure_sorted_aliases(entry)
        unmatched.append(entry)

    filtered.extend(unmatched)

    # Security (Trojan-Source / BiDi-Mark Drift Round 14, ingestion-boundary
    # defence): strip the canonical CVE-2021-42574 attack-byte union from
    # the merged stations BEFORE ``json.dump``. A planted Wien OGD CSV
    # (or hijacked ``data.wien.gv.at`` response) could plant U+202E in a
    # WL station ``name`` / ``aliases[]`` field — the file is committed
    # to ``main`` by the weekly cron via the orchestrator. Mirrors
    # ``src/places/merge.py:write_stations`` (Round 13). ``ensure_ascii=
    # False`` is preserved so legitimate German station names stay
    # compact in the commit diff.
    scrubbed = scrub_trojan_source_primitives(filtered)
    serialisable = scrubbed if isinstance(scrubbed, list) else filtered
    with atomic_write(stations_path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump({"stations": serialisable}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    log.info("Wrote %d total stations", len(filtered))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    if args.download:
        _download_ogd_csv(OGD_HALTESTELLEN_URL, args.haltestellen)
        _download_ogd_csv(OGD_HALTEPUNKTE_URL, args.haltepunkte)

    log.info("Reading haltestellen: %s", args.haltestellen)
    haltestellen = load_haltestellen(args.haltestellen)
    log.info("Found %d haltestellen", len(haltestellen))

    log.info("Reading haltepunkte: %s", args.haltepunkte)
    haltepunkte = load_haltepunkte(args.haltepunkte)
    log.info("Found %d haltepunkte", len(haltepunkte))

    vor_mapping = load_vor_mapping(args.vor_mapping)
    if vor_mapping:
        log.info("Loaded %d VOR mapping entries", len(vor_mapping))

    wl_entries = build_wl_entries(haltestellen, haltepunkte, vor_mapping)
    log.info("Prepared %d WL station entries", len(wl_entries))

    merge_into_stations(args.stations, wl_entries)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
