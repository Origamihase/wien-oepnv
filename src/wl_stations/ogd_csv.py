"""CSV model and loaders (stops, lines) of ``scripts/update_wl_stations.py``.

Moved verbatim from the script; ``MAX_WL_CSV_BYTES`` lives here next to
``_dict_reader``, its only user.
A test that patches a constant or function used here must patch it
on this module, not on the script.
"""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Iterator, Mapping

from src.wl_stations.base import _path_fingerprint, log, read_capped_text


# CSV size-bomb axis: ``_dict_reader`` previously fed the operator-
# supplied haltestellen / haltepunkte CSVs into ``csv.DictReader(handle)``
# directly, letting ``handle.readline()`` buffer GiB-sized single-line
# payloads. Routes through ``read_capped_text`` -> ``io.StringIO`` to
# bound the allocation.
MAX_WL_CSV_BYTES = 50 * 1024 * 1024


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
        result = float(text)
    except ValueError:
        return None
    # Reject NaN/Inf at the parse boundary. ``float("nan")``/``float("1e1000")``
    # do not raise, and a non-finite coordinate would poison the coordinate
    # averaging (``_aggregate_coordinates``), make ``_all_pairs_within`` return
    # True for NaN pairs (spurious merges), and finally crash the whole merge at
    # ``json.dump(..., allow_nan=False)``. Drop the corrupt cell instead.
    if not math.isfinite(result):
        return None
    return result


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


# A WL line label as the OGD export spells it (``U4``, ``49``, ``13A``,
# ``N25``, ``D``, ``WLB``), uppercased like the WL provider's
# ``relatedLines`` tokens. Anything else is not a line and is dropped
# rather than persisted into stations.json.
_WL_LINE_TEXT_RE = re.compile(r"^[A-Z0-9]{1,6}$")
_WL_LINE_PARTS_RE = re.compile(r"^([A-Z]*)(\d*)(.*)$")


def _line_sort_key(line: str) -> tuple[str, int, str]:
    """Natural order: ``5`` < ``13A`` < ``49`` < ``D`` < ``N25`` < ``U4``."""
    match = _WL_LINE_PARTS_RE.match(line)
    if match is None:  # pragma: no cover - the pattern matches every string
        return (line, 0, "")
    prefix, number, rest = match.groups()
    return (prefix, int(number) if number else 0, rest)


def load_line_texts(path: Path) -> dict[str, str]:
    """Return ``LineID → LineText`` from ``wienerlinien-ogd-linien.csv``.

    The lines are optional enrichment: a missing file yields ``{}`` and the
    station merge carries on without ``wl_lines``.
    """
    mapping: dict[str, str] = {}
    try:
        for row in _dict_reader(path):
            line_id = row.get("LineID", "LINIEN_ID")
            text = row.get("LineText", "BEZEICHNUNG").strip().upper()
            if line_id and _WL_LINE_TEXT_RE.match(text):
                mapping[line_id] = text
    except FileNotFoundError:
        log.warning(
            "WL linien CSV missing [path-sha256=%s]; stations get no wl_lines",
            _path_fingerprint(path),
        )
        return {}
    return mapping


def load_lines_by_stop(path: Path, line_texts: Mapping[str, str]) -> dict[str, set[str]]:
    """Return ``StopID → {LineText, …}`` from the Fahrwegverläufe CSV.

    Every row of ``wienerlinien-ogd-fahrwegverlaeufe.csv`` is one stop of
    one route pattern of one line; the StopID is the one ``wl_stops``
    carries. A missing file, or no line texts, yields ``{}``.
    """
    if not line_texts:
        return {}
    by_stop: dict[str, set[str]] = {}
    try:
        for row in _dict_reader(path):
            text = line_texts.get(row.get("LineID", "LINIEN_ID"))
            stop_id = row.get("StopID", "STOP_ID")
            if text and stop_id:
                by_stop.setdefault(stop_id, set()).add(text)
    except FileNotFoundError:
        log.warning(
            "WL fahrwegverlaeufe CSV missing [path-sha256=%s]; stations get no wl_lines",
            _path_fingerprint(path),
        )
        return {}
    return by_stop


def _station_lines(
    stop_ids: Iterable[str], lines_by_stop: Mapping[str, Iterable[str]]
) -> list[str]:
    """Return the lines serving any of *stop_ids*, in natural order."""
    lines: set[str] = set()
    for stop_id in stop_ids:
        lines.update(lines_by_stop.get(stop_id, ()))
    return sorted(lines, key=_line_sort_key)
