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
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence


def _path_fingerprint(path: Path) -> str:
    """Return a one-way SHA-256 fingerprint of ``str(path)`` (12 hex chars).

    Security (Path-Log Sibling Drift Round 2, ``scripts/`` closure):
    mirrors :func:`src.utils.env._path_fingerprint`. The path arguments
    at every caller-side WARNING / INFO log line in this script come
    from operator-controlled CLI flags (``--haltepunkte``,
    ``--haltestellen``, ``--stations``, ``--vor-mapping``).
    Interpolating the raw path bytes lets a hostile path carrying
    Trojan-Source primitives (BiDi RLO, zero-width, 8-bit C1 CSI/OSC,
    Tag block, Variation Selectors, newline log-forgery, ANSI ESC)
    flow verbatim into stderr / aggregated cron logs / SIEM splitters.
    The hex-only fingerprint is Trojan-Source-clean and a
    CodeQL-recognised barrier for the
    ``py/clear-text-logging-sensitive-data`` taint.
    """
    return hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]


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


def _load_get_bool_env() -> Callable[..., bool]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.env")
    return cast(Callable[..., bool], module.get_bool_env)


def _load_sanitize_log_arg() -> Callable[..., Any]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.logging")
    return cast(Callable[..., Any], module.sanitize_log_arg)


def _load_resolve_at_coordinate() -> Callable[..., Any]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.places.coordinate_consensus")
    return cast(Callable[..., Any], module.resolve_at_coordinate)


def _load_enrich_station_with_hafas() -> Callable[..., Any]:
    """Lazy HAFAS import — pulls ``requests`` and the HAFAS profile loader,
    so it is only resolved when the reconciliation pass actually runs.
    """

    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.places.hafas_client")
    return cast(Callable[..., Any], module.enrich_station_with_hafas)


def _load_osm_place_fetchers() -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Lazy OSM import — pulls the Overpass client; resolved only when a
    WL/HAFAS disagreement needs an arbiter.
    """

    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.places.osm_client")
    return (
        cast(Callable[..., Any], module.fetch_osm_places),
        cast(Callable[..., Any], module.filter_complete_places),
    )


BASE_DIR = _project_root()
is_in_vienna = _load_is_in_vienna()
atomic_write = _load_atomic_write()
read_capped_json = _load_read_capped_json()
read_capped_text = _load_read_capped_text()
scrub_trojan_source_primitives = _load_scrub_trojan_source_primitives()
get_bool_env = _load_get_bool_env()
sanitize_log_arg = _load_sanitize_log_arg()
resolve_at_coordinate = _load_resolve_at_coordinate()

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

# Bandwidth-saver: each WL OGD CSV download stores ``ETag`` and
# ``Last-Modified`` in a sibling ``<csv>.cache.json`` so the next run
# can send ``If-None-Match`` / ``If-Modified-Since`` and short-circuit
# on ``304 Not Modified``. The sidecar is tiny (<1 KiB) so the cap is
# generous; oversized files are silently treated as missing (which
# falls back to an unconditional GET, the safe direction).
MAX_OGD_CACHE_SIDECAR_BYTES = 64 * 1024
_OGD_CACHE_SIDECAR_SUFFIX = ".cache.json"

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


def _cache_sidecar_path(target: Path) -> Path:
    """Return the sidecar path that stores ``ETag`` / ``Last-Modified``.

    Convention: ``<target>.cache.json`` next to the CSV. The sidecar
    therefore moves/disappears with the file it describes — there is no
    risk of a stale sidecar describing a target that no longer exists.
    """
    return target.with_name(target.name + _OGD_CACHE_SIDECAR_SUFFIX)


def _read_cache_validators(target: Path) -> dict[str, str]:
    """Return ``If-None-Match`` / ``If-Modified-Since`` headers for *target*.

    Returns an empty dict when either the target CSV or its sidecar is
    missing, when the sidecar is corrupted/oversized, or when neither
    validator was captured by the previous fetch. The caller falls back
    to an unconditional GET in those cases — the safe direction.
    """
    sidecar = _cache_sidecar_path(target)
    if not target.exists() or not sidecar.exists():
        return {}
    payload = read_capped_json(
        sidecar,
        MAX_OGD_CACHE_SIDECAR_BYTES,
        label="WL OGD cache sidecar",
        logger=log,
    )
    if not isinstance(payload, dict):
        return {}
    validators: dict[str, str] = {}
    etag = payload.get("etag")
    if isinstance(etag, str) and etag:
        validators["If-None-Match"] = etag
    last_modified = payload.get("last_modified")
    if isinstance(last_modified, str) and last_modified:
        validators["If-Modified-Since"] = last_modified
    return validators


def _write_cache_validators(target: Path, headers: Mapping[str, str]) -> None:
    """Persist ``ETag`` / ``Last-Modified`` from *headers* into the sidecar.

    Best-effort: a write failure is logged but never raised so a
    successful CSV download is not cancelled by a sidecar I/O glitch.
    When the upstream returned neither validator we skip writing entirely
    — a sidecar without validators would force a full-body re-download
    on the next run anyway, so the empty file would be useless.
    """
    etag = headers.get("ETag") or headers.get("etag")
    last_modified = headers.get("Last-Modified") or headers.get("last-modified")
    if not etag and not last_modified:
        return
    payload = {
        "version": 1,
        "etag": etag or "",
        "last_modified": last_modified or "",
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    sidecar = _cache_sidecar_path(target)
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(
            sidecar, mode="w", encoding="utf-8", permissions=0o644
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
    except OSError as exc:
        log.warning(
            "Failed to write WL OGD cache sidecar [path-sha256=%s] (%s)",
            _path_fingerprint(sidecar),
            exc,
        )


def _download_ogd_csv(url: str, target: Path) -> bool:
    """Download a Wiener Linien OGD CSV and write it to *target* atomically.

    Uses HTTP conditional requests (``If-None-Match`` / ``If-Modified-Since``)
    when a previous fetch persisted validators in the sidecar — a ``304
    Not Modified`` response keeps the existing local file untouched and
    returns ``True`` (the file is up to date, which is a success state).
    A fresh ``200`` response writes the body atomically and refreshes the
    sidecar with the new validators.

    Returns ``True`` on success (200 written or 304 skipped),
    ``False`` on any error. Errors are logged but not raised, so callers
    can fall back to existing local files.
    """
    base_dir = _project_root()
    if str(base_dir) not in sys.path:  # pragma: no cover - defensive
        sys.path.insert(0, str(base_dir))
    try:
        from src.utils.http import request_safe, session_with_retries
    except ImportError:  # pragma: no cover - defensive
        log.warning("HTTP utilities unavailable; cannot download %s", url)
        return False

    validators = _read_cache_validators(target)
    headers: dict[str, str] = {"User-Agent": USER_AGENT, **validators}
    if validators:
        log.info(
            "Downloading WL OGD (conditional, validators=%s): %s",
            ",".join(sorted(validators)),
            url,
        )
    else:
        log.info("Downloading WL OGD: %s", url)

    try:
        with session_with_retries(USER_AGENT) as session:
            response = request_safe(
                session,
                url,
                method="GET",
                timeout=OGD_DOWNLOAD_TIMEOUT_SECONDS,
                headers=headers,
                raise_for_status=True,
            )
    except Exception as exc:  # pragma: no cover - network-dependent
        log.warning("Failed to download %s (%s); using local file if present", url, exc)
        return False

    if response.status_code == 304:
        if not target.exists():
            # Defensive: the server claims our copy is current but we
            # don't have one. This would only happen if the sidecar and
            # the CSV were independently moved/deleted between runs. Drop
            # the stale sidecar so the next invocation does a full GET.
            log.warning(
                "WL OGD returned 304 but local target [path-sha256=%s] is "
                "missing; clearing stale sidecar",
                _path_fingerprint(target),
            )
            sidecar = _cache_sidecar_path(target)
            try:
                sidecar.unlink()
            except FileNotFoundError:
                log.debug(
                    "Stale WL OGD cache sidecar already absent [path-sha256=%s]",
                    _path_fingerprint(sidecar),
                )
            except OSError as exc:
                log.warning(
                    "Failed to clear stale WL OGD cache sidecar (%s)", exc
                )
            return False
        log.info(
            "WL OGD not modified (304); keeping existing [path-sha256=%s]",
            _path_fingerprint(target),
        )
        return True

    content = cast(bytes, response.content)
    if not content:
        log.warning("Empty response from %s; using local file if present", url)
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(target, mode="wb", permissions=0o644) as handle:
        handle.write(content)
    log.info(
        "Saved [path-sha256=%s] (%d bytes)",
        _path_fingerprint(target),
        len(content),
    )
    _write_cache_validators(target, response.headers)
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


# Haltestelle PlatformText values that carry no location information
# on their own and are routinely shared across far-apart stops in the
# OGD-Echtzeit data (e.g. ``Lokalbahn`` × 4 across 5.6 km of greater
# Vienna, ``Bahnhof`` × 2 across 9.4 km). Whenever a station's
# PlatformText is in this set, ``_derive_station_label`` overrides
# it with the more specific haltepunkte ``StopText`` even when the
# StopText does not contain the PlatformText as a substring (e.g.
# PlatformText ``Bahnhof`` → StopText ``Tribuswinkel - Josefsthal``).
_GENERIC_PLATFORM_TEXTS: frozenset[str] = frozenset(
    {
        "bahnhof",
        "bahn",
        "hauptbahnhof",
        "lokalbahn",
        "station",
        "halt",
        "bf",
        "hbf",
        "u-bahn",
    }
)

# Bahnsteig-direction-/Bedarfshalt-Markers that should be stripped
# from haltepunkte StopText before deriving the canonical label.
# ``(Richtung X)`` describes which way the platform faces; ``(Bedarf)``
# marks request stops. Neither is part of the stop's identity.
_STOPTEXT_QUALIFIER_RE = re.compile(
    r"\s*(?:\([^)]*Richtung[^)]*\)|\([^)]*Bedarf[^)]*\)|→.*|←.*)$"
)


def _derive_station_label(platform_text: str, stops: Iterable[Haltepunkt]) -> str:
    """Override generic haltestelle ``PlatformText`` labels with the
    more informative haltepunkte ``StopText``.

    Wiener Linien's OGD-Echtzeit ``haltestellen.csv`` ``PlatformText``
    field is sometimes a generic transport-typed token
    (``Bahnhof``, ``Lokalbahn``, …) that gives the operator no useful
    location context. The corresponding haltepunkte carry a much more
    specific ``StopText`` value (e.g. ``Bahnhof`` →
    ``Tribuswinkel - Josefsthal``). This helper picks the more
    informative label **only** when the PlatformText is one of those
    generic tokens; the much-larger common case (named haltestellen
    like ``Karlsplatz``, ``Stephansplatz``, ``Bhf. Atzgersdorf``)
    keeps the PlatformText untouched so ÖBB / VOR name-based joins
    in ``merge_into_stations`` remain stable and the displayed label
    does not silently rotate to the first haltepunkte StopText (which
    may carry idiosyncratic suffixes like ``Bhf. Atzgersdorf S``).

    Strategy:

    * Strip Bahnsteig direction / Bedarfshalt qualifiers from each
      haltepunkte StopText (``Karlsplatz U (Richtung Reumannplatz)``
      → ``Karlsplatz U``).
    * If ``platform_text`` is in :data:`_GENERIC_PLATFORM_TEXTS` and
      the haltepunkte produce a single cleaned StopText, return the
      StopText (overrides ``Bahnhof`` / ``Lokalbahn`` / …).
    * Otherwise return ``platform_text`` unchanged.
    """
    if platform_text.casefold() not in _GENERIC_PLATFORM_TEXTS:
        return platform_text

    cleaned: set[str] = set()
    for stop in stops:
        normalized = _STOPTEXT_QUALIFIER_RE.sub("", stop.name).strip()
        if normalized:
            cleaned.add(normalized)

    if len(cleaned) == 1:
        return next(iter(cleaned))
    return platform_text


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

    # Skip the ``U`` / ``Bahnhof`` / ``Station`` augmentations when the
    # base name is too generic. Wiener Linien's OGD has stations like
    # ``Bahnhof`` (multiple DIVAs, disambiguated to ``Wien Bahnhof
    # (WL 60205022)`` post-PR #1448) whose ``_normalize_token``
    # already strips the ``bahnhof``/``hbf``/``bf`` stem and the
    # ``(wl …)`` parenthetical to a single token (``"wien"``).
    # Appending ``U`` to such a base yields ``Wien Bahnhof U`` which
    # normalises to ``"wien u"`` — a catch-all key that shadowed every
    # ``canonical_name("Wien X (U)")`` lookup, breaking the
    # ``test_clean_title_expands_wien_hbf_abbreviation`` regression
    # on ``main`` after the disambiguation landed.
    # ``_is_generic_base`` keeps the standard variants for the common
    # case (multi-token station names like ``Wien Karlsplatz``) and
    # drops them only for the degenerate ``Wien <stem>`` shape.
    is_generic = _is_generic_base(base)

    variants = {canonical, base, f"{base} (WL)"}
    if not is_generic:
        variants.update(
            {
                f"{base} U",
                f"{base} U (VOR)",
                f"{base} Bahnhof",
                f"Bahnhof {base}",
                f"{base} Station",
            }
        )
    english_base = base
    if base.lower().startswith("wien "):
        english_base = f"Vienna {base[5:]}".strip()
        variants.update({english_base, f"{english_base} (WL)"})
        if not is_generic:
            variants.update(
                {
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


def _is_generic_base(base: str) -> bool:
    """Return True when *base* normalises to a single token that would
    collapse the ``U``/``Bahnhof``/``Station`` augmentations into
    catch-all alias keys. The check imports the canonical normaliser
    lazily so this module stays importable when the orchestrator
    starts before ``src.utils.stations`` is on the path.
    """
    base_dir = _project_root()
    if str(base_dir) not in sys.path:  # pragma: no cover - defensive
        sys.path.insert(0, str(base_dir))
    from src.utils.stations import _normalize_token

    tokens = _normalize_token(base).split()
    # ``_normalize_token`` strips ``bahnhof``/``hbf``/``bf``/``bahnhst``
    # plus the parenthetical ``(WL <diva>)`` augmentations. If the
    # base reduces to a single token (typically just ``"wien"``), the
    # augmentations collide with every other Vienna lookup.
    return len(tokens) <= 1


def load_vor_mapping(path: Path) -> dict[str, Mapping[str, object]]:
    if not path.exists():
        log.info(
            "No VOR mapping found at [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return {}
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). Same
    # cron-pipeline blast radius as the sibling loader in
    # ``scripts/enrich_station_aliases.py``.
    raw = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="VOR mapping", logger=log,
    )
    if raw is None:
        log.warning(
            "Could not parse VOR mapping [path-sha256=%s] (missing/invalid/oversized)",
            _path_fingerprint(path),
        )
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
        # The haltestelle ``PlatformText`` is sometimes a generic
        # transport-typed label (``Bahnhof``, ``Lokalbahn``, …) that
        # gives the operator no useful context — multiple haltestellen
        # share it across all of Vienna's outskirts. The corresponding
        # haltepunkte often carry a much more specific ``StopText``
        # (e.g. ``Tribuswinkel - Josefsthal`` for one of the four
        # WLB ``Lokalbahn`` stops). ``_derive_station_label`` picks
        # the most informative label between PlatformText and the
        # haltepunkte StopText, falling back to PlatformText when the
        # StopText carries no additional information (Karlsplatz,
        # Stephansplatz, … stay untouched so ÖBB / VOR name-based
        # joins remain stable).
        display_label = _derive_station_label(station.name, stops)
        if display_label != station.name:
            aliases.add(f"Wien {display_label}")
        canonical = _canonical_name(display_label)
        aliases.add(canonical)
        latitude, longitude = _aggregate_coordinates(stops)
        # Compute ``in_vienna`` from the aggregate (mean) coordinates
        # rather than any-stop-wins, so the flag stays consistent with
        # the coordinates that will land in the stations.json entry.
        # Pre-fix variant flipped to True the moment any haltepunkt
        # fell inside Vienna's polygon, which produced false-positives
        # for boundary stations: e.g. ``Wien Lohnergasse (WL)`` has
        # one bahnsteig (8802) at (48.2821, 16.3692) just inside the
        # polygon and another (8803) at (48.2821, 16.3690) just
        # outside; the agg mean falls outside, so the entry's coords
        # would say "outside Vienna" while the flag said "inside" —
        # exactly the inconsistency that ``test_coordinates_match_in_
        # vienna_flag`` regression-checks.
        if latitude is not None and longitude is not None:
            in_vienna = is_in_vienna(latitude, longitude)
        else:
            log.warning(
                "WL station %s (%s) lacks coordinates; falling back to name lookup",
                station.name,
                station_identifier,
            )
            in_vienna = is_in_vienna(station.name)
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
    entries = _merge_colocated_duplicates(entries)
    # ``_disambiguate_duplicate_names`` retired 2026-05-12. The
    # stations validator no longer enforces canonical-name uniqueness
    # (PR after #1451) — structured identifiers (``wl_diva``,
    # ``bst_id``, ``vor_id``) carry the project's
    # eindeutigkeits-Garantie. The canonical display name stays
    # ``Wien <PlatformText> (WL)`` for every WL haltestelle, even when
    # the same PlatformText is shared by another DIVA, so the RSS
    # feed renders ``Wien Bahnhof (WL)`` cleanly instead of the
    # ``Wien Bahnhof (WL 60205022)`` DIVA suffix that pre-existed.
    return entries


_COLOCATED_MERGE_DISTANCE_M = 150.0


def _merge_colocated_duplicates(
    entries: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Merge entries that share a canonical name AND are within 150 m
    of each other.

    Wiener Linien's OGD-Echtzeit ``haltestellen.csv`` lists some
    physical stops twice: opposing-direction bahnsteige at the same
    intersection get distinct DIVAs despite sharing a name and lying
    a few dozen metres apart. Forensic analysis of the current CSV
    surfaces six such groups (Soldanellenweg 27 m, Bhf. Atzgersdorf
    57 m, Vorgartenstraße 71 m, Am Rosenhügel 88 m, Stock im Weg
    121 m, Lieblgasse 122 m). Without this pass each one shows up
    as two near-identical entries in ``data/stations.json``
    (disambiguated by DIVA suffix downstream — see
    ``_disambiguate_duplicate_names``).

    Merging rules (all additive, no schema break):

    * ``name``: unchanged base name (kept; the disambiguation pass
      that runs next is a no-op because the group collapses to one).
    * ``wl_diva``: the lexicographically lowest DIVA in the group
      (deterministic).
    * ``wl_stops``: union of ``wl_stops`` from every merged entry,
      sorted by ``stop_id``.
    * ``aliases``: union, sorted.
    * ``latitude`` / ``longitude``: arithmetic mean across the
      group's coordinates.
    * ``in_vienna`` / ``pendler``: re-derived from the new mean
      coordinates via the same polygon check
      ``build_wl_entries`` uses for single-entry groups
      (keeps the flag pair consistent with the persisted coords —
      same invariant pinned by ``test_coordinates_match_in_vienna_
      flag``).
    * Other identity-class fields (``bst_id``, ``bst_code``,
      ``vor_id`` if any picked up from the VOR mapping): primary's
      values win; absent on every WL-only entry by construction
      after PR #1446's redesign.

    Multi-modal stops at the same venue (150-500 m apart — e.g.
    tram + bus pair at one intersection) are NOT merged; they remain
    separate entries with the existing DIVA-suffix disambiguation,
    because operating-line-level disambiguation cannot be inferred
    from the OGD-Echtzeit columns alone.
    """
    from collections import defaultdict

    if not entries:
        return entries

    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        name = str(entry.get("name") or "")
        if name:
            by_name[name].append(entry)

    merged_total = 0
    out: list[dict[str, object]] = []
    consumed_ids: set[int] = set()
    for _name, group in by_name.items():
        if len(group) < 2:
            continue
        if all(id(e) not in consumed_ids for e in group):
            if _all_pairs_within(group, _COLOCATED_MERGE_DISTANCE_M):
                merged = _merge_entry_group(group)
                out.append(merged)
                merged_total += len(group) - 1
                for e in group:
                    consumed_ids.add(id(e))

    if not consumed_ids:
        return entries

    # Re-assemble: keep all entries that were NOT consumed by a merge,
    # plus the merged primaries we just produced. Preserve the original
    # sort order downstream by re-sorting at the end.
    for entry in entries:
        if id(entry) not in consumed_ids:
            out.append(entry)
    out.sort(key=lambda item: (str(item.get("name")), str(item.get("wl_diva"))))
    log.info(
        "Merged %d co-located WL haltestellen (≤%.0f m) into existing "
        "neighbour entries",
        merged_total,
        _COLOCATED_MERGE_DISTANCE_M,
    )
    return out


def _haversine_m(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    import math

    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _all_pairs_within(
    group: list[dict[str, object]], max_distance_m: float
) -> bool:
    """Return True when every pair of entries in *group* sits within
    *max_distance_m* of each other. Entries missing coordinates are
    treated as "cannot merge" — return False so the group is left to
    the downstream DIVA-suffix disambiguation.
    """
    coords: list[tuple[float, float]] = []
    for entry in group:
        lat = entry.get("latitude")
        lon = entry.get("longitude")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            return False
        coords.append((float(lat), float(lon)))

    for i, (lat_i, lon_i) in enumerate(coords):
        for lat_j, lon_j in coords[i + 1:]:
            if _haversine_m(lat_i, lon_i, lat_j, lon_j) >= max_distance_m:
                return False
    return True


def _drop_distant_name_contamination(
    entries: list[dict[str, object]], *, threshold_m: float = 2000.0
) -> int:
    """Strip aliases / wl_stops whose name is a *different* station located
    more than *threshold_m* away.

    A Wiener-Linien DIVA legitimately groups physically nearby stops, and a
    grouped stop's StopText sometimes equals a neighbouring station's name
    (interchanges, depots) — those resolve to a station a few hundred metres
    away and stay. But a genuinely mislabelled stop pollutes the alias index:
    e.g. a stop sitting *at Grinzing* carrying the name ``Karlsplatz`` (6.4 km
    away) made ``station_info("Karlsplatz")`` resolve to Grinzing. Only the
    > threshold cases are dropped; the sweep that motivated this guard found
    exactly one such case across the whole directory, with no false positives
    among the legitimate sub-2-km interchange names. Returns the number of
    alias/stop tokens dropped. Mutates *entries* in place.
    """
    from src.utils.stations import _normalize_token

    def _bare(name: object) -> str:
        text = re.sub(r"\s*\((?:WL|VOR)\)\s*$", "", str(name or ""))
        return _normalize_token(re.sub(r"^Wien\s+", "", text))

    # bare canonical-name token -> coordinates of the owning station(s)
    name_coords: dict[str, list[tuple[float, float]]] = {}
    for entry in entries:
        lat, lon = entry.get("latitude"), entry.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            token = _bare(entry.get("name"))
            if token:
                name_coords.setdefault(token, []).append((float(lat), float(lon)))

    def _is_distant(label: object, own: str, elat: float, elon: float) -> bool:
        if not isinstance(label, str):
            return False
        token = _normalize_token(label)
        owners = name_coords.get(token)
        if not token or token == own or not owners:
            return False
        return all(
            _haversine_m(elat, elon, owner_lat, owner_lon) > threshold_m
            for owner_lat, owner_lon in owners
        )

    dropped = 0
    for entry in entries:
        lat, lon = entry.get("latitude"), entry.get("longitude")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            continue
        elat, elon, own = float(lat), float(lon), _bare(entry.get("name"))

        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            kept = [a for a in aliases if not _is_distant(a, own, elat, elon)]
            if len(kept) != len(aliases):
                dropped += len(aliases) - len(kept)
                entry["aliases"] = kept

        stops = entry.get("wl_stops")
        if isinstance(stops, list):
            kept_stops = [
                s
                for s in stops
                if not (isinstance(s, dict) and _is_distant(s.get("name"), own, elat, elon))
            ]
            if len(kept_stops) != len(stops):
                dropped += len(stops) - len(kept_stops)
                entry["wl_stops"] = kept_stops

    return dropped


def _merge_entry_group(group: list[dict[str, object]]) -> dict[str, object]:
    """Fold a list of co-located duplicate entries into one.

    See ``_merge_colocated_duplicates`` for the merge-rule rationale.
    """
    # Pick the primary by lexicographically lowest wl_diva so the
    # output is deterministic across cron ticks.
    primary = min(
        group,
        key=lambda e: str(e.get("wl_diva") or ""),
    )
    extras = [e for e in group if e is not primary]

    merged: dict[str, object] = dict(primary)

    # Union wl_stops, dedup by stop_id, sort
    seen_stop_ids: set[str] = set()
    combined_stops: list[dict[str, object]] = []
    for entry in (primary, *extras):
        stops = entry.get("wl_stops")
        if not isinstance(stops, list):
            continue
        for stop in stops:
            if not isinstance(stop, dict):
                continue
            sid = str(stop.get("stop_id") or "")
            if sid and sid not in seen_stop_ids:
                seen_stop_ids.add(sid)
                combined_stops.append(dict(stop))
    merged["wl_stops"] = sorted(
        combined_stops, key=lambda item: str(item.get("stop_id") or "")
    )

    # Union aliases
    combined_aliases: set[str] = set()
    for entry in (primary, *extras):
        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    combined_aliases.add(alias)
    merged["aliases"] = sorted(combined_aliases)

    # Mean coordinates (only over members that have them, all do by
    # _all_pairs_within precondition)
    lats: list[float] = []
    lons: list[float] = []
    for entry in (primary, *extras):
        lat = entry.get("latitude")
        lon = entry.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            lats.append(float(lat))
            lons.append(float(lon))
    if lats and lons:
        mean_lat = round(sum(lats) / len(lats), 6)
        mean_lon = round(sum(lons) / len(lons), 6)
        merged["latitude"] = mean_lat
        merged["longitude"] = mean_lon
        # Re-derive in_vienna against the persisted mean — keeps the
        # flag pair consistent with the coords.
        merged["in_vienna"] = is_in_vienna(mean_lat, mean_lon)
        merged["pendler"] = not bool(merged["in_vienna"])

    return merged


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


def _wl_coord_index(
    wl_entries: Iterable[Mapping[str, object]],
) -> dict[str, tuple[float, float]]:
    """Map ``wl_diva`` → authoritative WL ``(lat, lon)`` from the payloads.

    The first DIVA wins (payloads are already de-duplicated upstream).
    Non-finite / out-of-range coordinates are dropped so the consensus
    resolver never receives an invalid WL anchor.
    """

    index: dict[str, tuple[float, float]] = {}
    for payload in wl_entries:
        diva = str(payload.get("wl_diva") or "").strip()
        if not diva or diva in index:
            continue
        lat = payload.get("latitude")
        lon = payload.get("longitude")
        if (
            isinstance(lat, int | float)
            and isinstance(lon, int | float)
            and -90.0 <= lat <= 90.0
            and -180.0 <= lon <= 180.0
        ):
            index[diva] = (float(lat), float(lon))
    return index


def _coord_from_hafas_hit(hit: object) -> tuple[float, float] | None:
    if not isinstance(hit, Mapping):
        return None
    lat = hit.get("lat")
    lon = hit.get("lon")
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        return (float(lat), float(lon))
    return None


def _build_hafas_lookup() -> Callable[[str], tuple[float, float] | None]:
    """Return a name → ``(lat, lon)`` HAFAS lookup backed by ÖBB Scotty.

    Results are cached per name and every failure degrades to ``None`` so a
    HAFAS outage can never shift a coordinate or crash the WL merge.
    """

    enrich = _load_enrich_station_with_hafas()
    cache: dict[str, tuple[float, float] | None] = {}

    def lookup(name: str) -> tuple[float, float] | None:
        if name in cache:
            return cache[name]
        try:
            hit = enrich(name)
        except Exception:  # nosec B902 - HAFAS must never crash the merge
            log.warning(
                "HAFAS lookup raised during coordinate reconciliation for %s",
                sanitize_log_arg(name),
            )
            hit = None
        coord = _coord_from_hafas_hit(hit)
        cache[name] = coord
        return coord

    return lookup


def _build_osm_index_loader() -> Callable[[], Mapping[str, tuple[float, float]]]:
    """Return a lazy loader of a normalised-name → ``(lat, lon)`` OSM index.

    The Overpass round-trip happens on first call only; the reconciliation
    pass invokes it solely when a WL/HAFAS disagreement actually needs an
    arbiter, so an all-agree run never touches the network. Failures
    degrade to an empty index (every disagreement then stays unresolved
    and keeps WL).
    """

    def loader() -> Mapping[str, tuple[float, float]]:
        try:
            fetch_osm_places, filter_complete_places = _load_osm_place_fetchers()
            places = filter_complete_places(fetch_osm_places())
        except Exception as exc:  # nosec B902 - OSM must never crash the merge
            log.warning(
                "OSM fetch for coordinate arbitration failed: %s",
                sanitize_log_arg(type(exc).__name__),
            )
            return {}
        index: dict[str, tuple[float, float]] = {}
        for place in places:
            key = _normalize_key(getattr(place, "name", None))
            lat = getattr(place, "latitude", None)
            lon = getattr(place, "longitude", None)
            if (
                key
                and key not in index
                and isinstance(lat, int | float)
                and isinstance(lon, int | float)
            ):
                index[key] = (float(lat), float(lon))
        return index

    return loader


def _apply_coordinate_decision(entry: dict[str, object], decision: Any) -> None:
    """Write a :class:`CoordinateDecision` onto a merged station entry.

    Coordinates are rounded to 6 decimals to match the WL aggregation
    precision and keep the committed ``stations.json`` diff stable, and the
    decision's provider tokens are folded into the ``source`` field.
    """

    entry["latitude"] = round(float(decision.latitude), 6)
    entry["longitude"] = round(float(decision.longitude), 6)
    entry["source"] = _merge_sources(entry.get("source"), *decision.sources)


def _reconcile_at_overlap(
    entries: list[dict[str, object]],
    wl_coord_by_diva: Mapping[str, tuple[float, float]],
    *,
    hafas_lookup: Callable[[str], tuple[float, float] | None],
    osm_index_loader: Callable[[], Mapping[str, tuple[float, float]]] | None = None,
    agree_tolerance_m: float = 150.0,
    osm_sanity_radius_m: float = 500.0,
) -> None:
    """Apply the ``WL → HAFAS → OSM`` coordinate priority to overlap entries.

    Only entries carrying BOTH a ``wl_diva`` with a known WL coordinate and
    a HAFAS identity (``hafas_extId`` / ``eva_nr``) are reconciled — the
    multimodal hubs where a Wiener-Linien stop and an ÖBB station were
    unified. WL is authoritative; HAFAS is cross-checked; OSM (fetched
    lazily, only when a disagreement exists) arbitrates by endorsing the
    candidate closer to it. A missing HAFAS result is a deliberate no-op:
    a transient outage must never shift a coordinate. An unresolvable
    conflict keeps WL (highest priority) and is logged for review — Google
    is the gap-fill of last resort for coordinate-less stations, not an
    arbiter between two Austrian sources.
    """

    pending_osm: list[
        tuple[dict[str, object], tuple[float, float], tuple[float, float]]
    ] = []
    agreed = 0
    skipped_no_hafas = 0

    for entry in entries:
        diva = str(entry.get("wl_diva") or "").strip()
        if not diva:
            continue
        wl = wl_coord_by_diva.get(diva)
        if wl is None:
            continue
        if not (entry.get("hafas_extId") or entry.get("eva_nr")):
            # WL-only stop: no second Austrian source to reconcile against.
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        hafas = hafas_lookup(name.strip())
        if hafas is None:
            skipped_no_hafas += 1
            continue
        decision = resolve_at_coordinate(
            wl=wl, hafas=hafas, agree_tolerance_m=agree_tolerance_m
        )
        if decision.decision == "wl_hafas_agree":
            _apply_coordinate_decision(entry, decision)
            agreed += 1
        else:
            # WL and HAFAS disagree → defer until OSM (the arbiter) is loaded.
            pending_osm.append((entry, wl, hafas))

    osm_index: Mapping[str, tuple[float, float]] = {}
    if pending_osm and osm_index_loader is not None:
        osm_index = osm_index_loader()

    picked_wl = picked_hafas = unresolved = 0
    for entry, wl, hafas in pending_osm:
        name = str(entry.get("name") or "")
        osm = osm_index.get(_normalize_key(name))
        decision = resolve_at_coordinate(
            wl=wl,
            hafas=hafas,
            osm=osm,
            agree_tolerance_m=agree_tolerance_m,
            osm_sanity_radius_m=osm_sanity_radius_m,
        )
        _apply_coordinate_decision(entry, decision)
        if decision.decision == "osm_picked_hafas":
            picked_hafas += 1
            log.warning(
                "WL/HAFAS coordinate conflict for %s: OSM endorsed HAFAS",
                sanitize_log_arg(name),
            )
        elif decision.decision == "osm_picked_wl":
            picked_wl += 1
            log.warning(
                "WL/HAFAS coordinate conflict for %s: OSM endorsed WL",
                sanitize_log_arg(name),
            )
        else:
            unresolved += 1
            log.warning(
                "WL/HAFAS coordinate conflict for %s unresolved (OSM "
                "unavailable or implausible); kept WL",
                sanitize_log_arg(name),
            )

    if agreed or pending_osm or skipped_no_hafas:
        log.info(
            "AT coordinate reconciliation: %d agreed, %d OSM->WL, %d OSM->HAFAS, "
            "%d unresolved, %d not cross-checkable",
            agreed,
            picked_wl,
            picked_hafas,
            unresolved,
            skipped_no_hafas,
        )


def merge_into_stations(
    stations_path: Path,
    wl_entries: list[dict[str, Any]],
    *,
    reconcile: Callable[[list[dict[str, object]]], None] | None = None,
) -> None:
    # Data-loss floor: with no WL entries the merge below strips every
    # existing ``source == "wl"`` station and writes the remainder back,
    # silently deleting the entire Wiener-Linien layer from stations.json.
    # An empty set only happens when the OGD CSVs failed to load/parse
    # (oversized → ``read_capped_text`` returned ``None``, download error,
    # format change). Refuse and keep the committed file untouched.
    if not wl_entries:
        log.error(
            "merge_into_stations called with no WL entries — refusing to "
            "overwrite stations [path-sha256=%s] (would delete every WL station).",
            _path_fingerprint(stations_path),
        )
        return
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
    # Added 2026-05-16 (PR #1539): index by ``wl_diva`` so a WL payload
    # whose name doesn't normalise to the existing ``google_places`` /
    # ``oebb`` entry's name (the Innenstadt-U-Bahn pattern — payload
    # ``Wien Herrengasse (WL)`` vs existing ``Herrengasse``) is still
    # merged into the existing record instead of duplicated. Pre-fix,
    # ten Innenstadt-U-Bahn DIVAs (Herrengasse, Schwedenplatz,
    # Volkstheater, …) were silently duplicated each cron tick because
    # ``_normalize_key`` rendered the two names as distinct keys.
    wl_diva_index: dict[str, dict[str, object]] = {}

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

        wl_diva = entry.get("wl_diva")
        if wl_diva is not None:
            key = str(wl_diva).strip()
            if key and key not in wl_diva_index:
                wl_diva_index[key] = entry

    log.info("Keeping %d existing non-WL stations", len(filtered))

    unmatched: list[dict[str, object]] = []
    for payload in wl_entries:
        merged_into: dict[str, object] | None = None

        # Match precedence: ``wl_diva`` first — strongest WL-domain
        # identifier and the failure mode the new index was added for.
        # Then ``vor_id`` / ``bst_id`` / ``name`` as before so cross-
        # provider stubs (VOR-only / ÖBB-only) continue to consolidate
        # the same way.
        wl_diva = payload.get("wl_diva")
        merged_into = _lookup_candidates(wl_diva_index, wl_diva)

        if merged_into is None:
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

    # Coordinate consensus (WL → HAFAS → OSM → Google): runs on the fully
    # merged list so overlap entries already carry both ``wl_diva`` and the
    # ÖBB-side HAFAS identity. Optional so direct callers / unit tests keep
    # the historical no-reconcile behaviour; ``main()`` injects the real,
    # network-backed pass behind the ``WIEN_OEPNV_AT_RECONCILE`` gate.
    if reconcile is not None:
        reconcile(filtered)

    # Security (Trojan-Source / BiDi-Mark Drift Round 14, ingestion-boundary
    # defence): strip the canonical CVE-2021-42574 attack-byte union from
    # the merged stations BEFORE ``json.dump``. A planted Wien OGD CSV
    # (or hijacked ``data.wien.gv.at`` response) could plant U+202E in a
    # WL station ``name`` / ``aliases[]`` field — the file is committed
    # to ``main`` by the weekly cron via the orchestrator. Mirrors
    # ``src/places/merge.py:write_stations`` (Round 13). ``ensure_ascii=
    # False`` is preserved so legitimate German station names stay
    # compact in the commit diff.
    #
    # Security (Coordinate finite/range drift, companion-writer
    # defence-in-depth): ``allow_nan=False`` mirrors the canonical
    # writer-side pin established in Round 1485 at
    # ``src/places/merge.py:write_stations``. The local
    # ``_coerce_float`` parser (line 267 pre-fix) accepts the
    # literal strings ``"nan"`` / ``"inf"`` / ``"infinity"`` via
    # ``float(text)`` from a compromised Wien OGD CSV cell — the
    # per-stop ``latitude`` / ``longitude`` fields flow into
    # ``data/stations.json`` verbatim and Python's default
    # ``json.dump`` emits non-standard JSON literals (invalid per
    # RFC 8259). The pin surfaces such a bypass as a loud
    # ``ValueError`` rather than silently corrupting the committed
    # artefact.
    scrubbed = scrub_trojan_source_primitives(filtered)
    serialisable = scrubbed if isinstance(scrubbed, list) else filtered
    with atomic_write(stations_path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(
            {"stations": serialisable},
            handle,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
    log.info("Wrote %d total stations", len(filtered))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    if args.download:
        _download_ogd_csv(OGD_HALTESTELLEN_URL, args.haltestellen)
        _download_ogd_csv(OGD_HALTEPUNKTE_URL, args.haltepunkte)

    log.info(
        "Reading haltestellen: [path-sha256=%s]",
        _path_fingerprint(args.haltestellen),
    )
    haltestellen = load_haltestellen(args.haltestellen)
    log.info("Found %d haltestellen", len(haltestellen))

    log.info(
        "Reading haltepunkte: [path-sha256=%s]",
        _path_fingerprint(args.haltepunkte),
    )
    haltepunkte = load_haltepunkte(args.haltepunkte)
    log.info("Found %d haltepunkte", len(haltepunkte))

    vor_mapping = load_vor_mapping(args.vor_mapping)
    if vor_mapping:
        log.info("Loaded %d VOR mapping entries", len(vor_mapping))

    wl_entries = build_wl_entries(haltestellen, haltepunkte, vor_mapping)
    log.info("Prepared %d WL station entries", len(wl_entries))

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

    # Austrian-source coordinate consensus (WL → HAFAS → OSM → Google).
    # Env-disabled via ``WIEN_OEPNV_AT_RECONCILE=0`` — mirrors the
    # ``WIEN_OEPNV_OSM_ENRICH`` gate so the orchestrator wrapper test can
    # skip the ~40 real HAFAS round-trips (and a possible Overpass call)
    # that would otherwise tip it over its pytest timeout. Production cron
    # runs leave the env unset so reconciliation remains active.
    reconcile: Callable[[list[dict[str, object]]], None] | None = None
    if get_bool_env("WIEN_OEPNV_AT_RECONCILE", True):
        wl_coord_by_diva = _wl_coord_index(wl_entries)
        hafas_lookup = _build_hafas_lookup()
        osm_index_loader = _build_osm_index_loader()

        def _run_reconcile(merged: list[dict[str, object]]) -> None:
            _reconcile_at_overlap(
                merged,
                wl_coord_by_diva,
                hafas_lookup=hafas_lookup,
                osm_index_loader=osm_index_loader,
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
