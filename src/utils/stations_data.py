"""Station record types, data-file paths, size caps and the capped JSON reader.

Moved verbatim from ``src/utils/stations.py`` (700-line rule, 2026-11-02);
the public facade stays ``src.utils.stations``. Tests that redirect
``_STATIONS_PATH`` or ``MAX_STATIONS_FILE_BYTES`` patch this module, where
:func:`_station_entries` reads them. This module must not import the facade.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from .files import _reject_non_finite_constant, _reject_non_finite_float

# ``__all__`` lists every name this module defines. Some private names are
# read only by other modules and tests; CodeQL (py/unused-global-variable)
# counts such a read through an import only when the name is listed here.
__all__ = [
    "logger", "WLStop", "StationInfo", "_STATIONS_PATH", "_STATION_LINES_PATH",
    "_GEONETZ_STOPS_PATH", "_WL_OGD_STOPS_PATH", "_WL_GTFS_STOPS_PATH", "_VIENNA_POLYGON_PATH",
    "MAX_STATIONS_FILE_BYTES", "MAX_VIENNA_POLYGON_FILE_BYTES", "MAX_STATION_LINES_FILE_BYTES",
    "MAX_GEONETZ_STOPS_FILE_BYTES", "MAX_WL_OGD_STOPS_FILE_BYTES", "MAX_WL_GTFS_STOPS_FILE_BYTES",
    "WL_NETWORK_CENTRE", "MAX_WL_STOP_DISTANCE_M", "_WL_GTFS_STOP_ID_RE", "_read_capped_json",
    "_station_entries",
]


# The logger keeps the name of the facade ``src.utils.stations`` (log
# configuration and caplog filters in the tests match on it).
logger = logging.getLogger("src.utils.stations")


class WLStop(NamedTuple):
    """Coordinates for a Wiener Linien stop/platform."""

    stop_id: str
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class StationInfo(NamedTuple):
    """Normalized metadata for a single station entry."""

    name: str
    in_vienna: bool
    pendler: bool
    wl_diva: str | None = None
    wl_stops: tuple[WLStop, ...] = ()
    vor_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source: str | None = None

_STATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "stations.json"
_STATION_LINES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "oebb_station_lines.json"
)
_GEONETZ_STOPS_PATH = Path(__file__).resolve().parents[2] / "data" / "oebb_geonetz_stops.json"
_WL_OGD_STOPS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "wienerlinien-ogd-haltestellen.csv"
)
# The stops of the Wiener Linien GTFS timetable (``stops.txt``, same
# licensor and licence as the OGD stop list). It answers only where the OGD
# list carries a coordinate that cannot be a WL stop (see
# :func:`is_plausible_wl_coordinate`).
_WL_GTFS_STOPS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "wienerlinien-gtfs-stops.csv"
)
_VIENNA_POLYGON_PATH = Path(__file__).resolve().parents[2] / "data" / "LANDESGRENZEOGD.json"

# Security: defense-in-depth byte-size caps on the on-disk stations and
# Vienna-polygon files. The depth-bomb catch tuple
# ``except (OSError, json.JSONDecodeError, RecursionError)`` covers the
# deeply-nested attack via ``RecursionError``, but a wide-but-flat attack
# (e.g. ``[0]*50_000_000``) slips past that defence entirely:
#  * ``json.load`` does NOT raise ``RecursionError`` on a flat list
#    regardless of length — only nested structures hit the recursion limit.
#  * ``handle.read()`` (called internally by ``json.load(handle)``) buffers
#    the whole file before parsing — a 1 GiB file allocates a 1 GiB string
#    plus ~5 GiB worth of int/list/dict objects after parse.
#  * ``MemoryError`` is a ``BaseException`` subclass — it is NOT caught by
#    the surrounding handler, so the exception propagates out of the
#    ``@lru_cache``-decorated loader and crashes EVERY station-lookup path.
# Threat model mirrors ``MAX_CACHE_FILE_BYTES`` in ``src/utils/cache.py``:
# compromised CI runner / corrupted previous run / partial flush + power
# loss plants a multi-MiB-to-multi-GiB file under ``data/``. 50 MiB is
# ~285x the production stations.json (~175 KiB) and ~342x the polygon
# (~146 KiB), bounding the worst-case parse cost well below any cron
# runner's standard 1 GiB cgroup limit.
MAX_STATIONS_FILE_BYTES = 50 * 1024 * 1024
MAX_VIENNA_POLYGON_FILE_BYTES = 50 * 1024 * 1024
MAX_STATION_LINES_FILE_BYTES = 50 * 1024 * 1024
MAX_GEONETZ_STOPS_FILE_BYTES = 10 * 1024 * 1024
MAX_WL_OGD_STOPS_FILE_BYTES = 10 * 1024 * 1024
MAX_WL_GTFS_STOPS_FILE_BYTES = 10 * 1024 * 1024

# Plausibility gate for a Wiener-Linien stop coordinate. The WL OGD export
# ships well-formed coordinates that point at the wrong place: in the
# 2026-09-13 snapshot three consecutive DIVAs (60201954 Leopoldine-
# Padaurek-Straße, 60201955 Halblehenweg, 60201956 Wassermanngasse) sit
# 76-112 km away in the Waldviertel while declaring ``Municipality=Wien``,
# and they still do on 2026-10-25. The furthest genuine stop is the Badner-
# Bahn terminus at Leesdorf/Baden, 24.9 km from Stephansplatz; 50 km sits
# in the empty gap between the two populations.
WL_NETWORK_CENTRE: tuple[float, float] = (48.2082, 16.3738)
MAX_WL_STOP_DISTANCE_M: float = 50_000.0
# GTFS ``stop_id`` of a WL stop: ``at:<state>:<n>:<area>:<platform>``,
# where ``<n>`` is the DIVA number without its ``602`` prefix (DIVA
# 60201956 → ``at:49:1956:0:1``; Badner-Bahn stops in Lower Austria carry
# ``at:43``). Measured 2026-10-25: for the 1755 stations in both files the
# GTFS mean lies a median 14 m and at most 229 m from the OGD coordinate.
_WL_GTFS_STOP_ID_RE = re.compile(r"^at:\d{2}:(\d{1,5}):")


def _read_capped_json(
    path: Path,
    max_bytes: int,
    *,
    label: str,
) -> object | None:
    """Read JSON from *path*, returning ``None`` if missing/invalid/oversized.

    Security: combines the canonical size-bomb defence with the depth-bomb
    catch tuple ``(OSError, json.JSONDecodeError, RecursionError)``. The
    byte-size cap is enforced via ``os.fstat`` on the *open file
    descriptor* AND a defensive ``read(max_bytes + 1)`` budget — closing
    the TOCTOU window between ``Path.stat`` and ``Path.open`` that lets
    an attacker swap the inode (atomic ``os.replace`` of a symlink,
    parallel writer's ``atomic_write`` rename) under the loader's feet.
    Pre-fix the size check happened on a separate stat() syscall; an
    attacker who replaced the symlink target between stat and open made
    the cap pass on the small target while open() resolved to the big
    swapped file, propagating ``MemoryError`` past the catch tuple.
    Post-fix the fstat reports the size of the inode the open() call
    resolved, immune to any subsequent symlink swap, and the read cap
    catches the special-file case (``/dev/zero``, FIFOs) where st_size
    reports 0 but the read returns unbounded bytes. CRITICAL because
    this helper feeds the two ``@lru_cache`` import-time loaders
    (``_station_entries``, ``_vienna_polygons``) — a successful bypass
    crashes every feed-build path that touches a station name or a
    Vienna geo-fence check.

    Security (Path-Log Sibling Drift, sibling of PR #1456): the WARNING
    log lines below fingerprint ``path`` via SHA-256 (truncated to 12
    hex chars) rather than interpolating the raw path bytes. Pre-fix
    a hostile path name carrying Trojan-Source primitives (BiDi RLO,
    zero-width, 8-bit C1 CSI/OSC, Tag block, Variation Selectors,
    newline log-forgery, ANSI ESC) would flow into the operator-facing
    log + ``docs/feed_health.json`` + the GitHub-Issue auto-submission
    verbatim. Post-fix the hex fingerprint replaces the path bytes at
    the interpolation boundary — Trojan-Source-clean and CodeQL-
    recognised as a barrier for any clear-text-logging taint (the
    duplicate-code shape mirrors :func:`src.utils.files.read_capped_json`
    which closed the same drift at the canonical helper boundary).
    """
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                logger.warning(
                    "%s file [path-sha256=%s] is too large (> %d bytes); "
                    "treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                logger.warning(
                    "%s file [path-sha256=%s] exceeded %d bytes during read; "
                    "treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            # Security: ``parse_constant`` + ``parse_float`` hooks reject
            # the canonical non-finite literal family. Mirrors the
            # canonical defence pinned at :func:`src.utils.files.read_capped_json`
            # so a planted ``NaN`` / ``Infinity`` literal in the on-disk
            # stations or polygon file does NOT propagate as
            # ``float('nan')`` / ``float('inf')`` into the ``@lru_cache``
            # import-time loaders (``_station_entries`` /
            # ``_vienna_polygons``).
            payload: object = json.loads(
                raw,
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
            return payload
    except (OSError, json.JSONDecodeError, RecursionError, UnicodeDecodeError):
        return None


@lru_cache(maxsize=1)
def _station_entries() -> tuple[dict[str, Any], ...]:
    """Return the raw station entries from :mod:`data/stations.json`."""

    # Security: ``_read_capped_json`` enforces the byte-size cap (see
    # MAX_STATIONS_FILE_BYTES) BEFORE opening the file plus the
    # depth-bomb catch tuple. Module-import-time blast radius via
    # ``@lru_cache`` is critical — every station-name lookup
    # (``canonical_name``, ``station_info``, ``station_by_oebb_id``,
    # ``is_pendler``, …) routes through this loader.
    entries = _read_capped_json(
        _STATIONS_PATH,
        MAX_STATIONS_FILE_BYTES,
        label="Stations",
    )
    if entries is None:
        return ()

    if isinstance(entries, dict):
        entries = entries.get("stations", [])

    if not isinstance(entries, list):
        return ()

    result: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            result.append(entry)
    return tuple(result)
