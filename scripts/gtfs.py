"""Utilities for reading GTFS reference data used in tests."""
from __future__ import annotations

import io
import logging
import sys
from dataclasses import dataclass
import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_GTFS_STOP_PATH = BASE_DIR / "data" / "gtfs" / "stops.txt"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from src.utils.files import read_capped_text
except ModuleNotFoundError:  # pragma: no cover - fallback path
    from utils.files import read_capped_text  # type: ignore[no-redef]

# CSV size-bomb axis: ``read_gtfs_stops`` previously fed the operator-
# supplied ``stops.txt`` into ``csv.DictReader(handle)`` directly,
# letting ``handle.readline()`` buffer GiB-sized single-line payloads.
# Routes through ``read_capped_text`` -> ``io.StringIO`` to bound the
# allocation. 50 MiB matches the canonical ``MAX_*_FILE_BYTES``
# contract.
MAX_GTFS_STOPS_BYTES = 50 * 1024 * 1024

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GTFSStop:
    """Representation of a single row from ``stops.txt``."""

    stop_id: str
    stop_name: str
    stop_code: str | None
    stop_lat: float | None
    stop_lon: float | None
    location_type: int | None
    parent_station: str | None
    platform_code: str | None


def _strip(text: str | None) -> str:
    if text is None:
        return ""
    return text.strip()


def _optional(text: str | None) -> str | None:
    value = _strip(text)
    return value or None


def _coerce_float(text: str | None) -> float | None:
    value = _strip(text)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _coerce_int(text: str | None) -> int | None:
    value = _strip(text)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def read_gtfs_stops(path: Path | None = None) -> dict[str, GTFSStop]:
    """Read GTFS stop entries from ``stops.txt``.

    Parameters
    ----------
    path:
        Optional override for the ``stops.txt`` file that should be read.  If no
        path is provided the file placed at ``data/gtfs/stops.txt`` is used.

    Returns
    -------
    dict
        A mapping keyed by ``stop_id`` where each value contains a
        :class:`GTFSStop` with the parsed data.

    Raises
    ------
    ValueError
        If the file does not provide the mandatory ``stop_id`` column.
    """

    stop_path = Path(path) if path is not None else DEFAULT_GTFS_STOP_PATH

    # Security: see ``MAX_GTFS_STOPS_BYTES`` for the canonical CSV
    # size-bomb defence shape (``read_capped_text`` -> ``io.StringIO``
    # -> ``csv.DictReader``). Pre-fix a planted unbounded ``stops.txt``
    # would propagate ``MemoryError`` past ``csv.DictReader.fieldnames``
    # and crash any caller. ``ValueError`` is raised on oversized /
    # missing / decode-error so the existing ``with pytest.raises
    # (ValueError)`` contract from ``test_gtfs_read_stops_requires_
    # stop_id_column`` extends to size-bomb attacks.
    content = read_capped_text(
        stop_path, MAX_GTFS_STOPS_BYTES,
        encoding="utf-8-sig", label="GTFS stops", logger=_log,
    )
    if content is None:
        raise ValueError(
            f"GTFS stops.txt file is missing or too large at {stop_path}"
        )

    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None or "stop_id" not in reader.fieldnames:
        raise ValueError("GTFS stops.txt file is missing the 'stop_id' column")

    stops: dict[str, GTFSStop] = {}
    for row in reader:
        stop_id = _strip(row.get("stop_id"))
        if not stop_id:
            continue
        stop_name = _strip(row.get("stop_name"))
        stops[stop_id] = GTFSStop(
            stop_id=stop_id,
            stop_name=stop_name,
            stop_code=_optional(row.get("stop_code")),
            stop_lat=_coerce_float(row.get("stop_lat")),
            stop_lon=_coerce_float(row.get("stop_lon")),
            location_type=_coerce_int(row.get("location_type")),
            parent_station=_optional(row.get("parent_station")),
            platform_code=_optional(row.get("platform_code")),
        )
    return stops


__all__ = ["GTFSStop", "DEFAULT_GTFS_STOP_PATH", "read_gtfs_stops"]
