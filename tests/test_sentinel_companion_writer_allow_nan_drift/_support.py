"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_companion_writer_allow_nan_drift.py.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_COMPANION_WRITER_DRIFT`` finds the full call-graph at once.
SENTINEL_COMPANION_WRITER_DRIFT = "companion writer allow_nan=False drift"


_NAN = float("nan")

_POS_INF = float("inf")

_NEG_INF = float("-inf")


def _raise_on_nan_or_infinity(token: str) -> float:
    """Strict-JSON ``parse_constant`` hook: refuse any non-finite literal.

    Mirrors the reader-side defence pinned in
    ``tests/test_sentinel_coordinate_nan_inf_range_drift.py``: a
    downstream consumer that wires this hook into ``json.loads`` MUST
    succeed on every artefact produced by the writer-side fix.
    """
    raise ValueError(f"Non-finite JSON literal {token!r} in committed artefact")


def _poisoned_station(*, lat: float = 48.185222, lon: float = 16.377778) -> dict[str, Any]:
    """Construct a station entry carrying the requested coords."""
    return {
        "name": "Wien Hauptbahnhof",
        "bst_code": "Wn",
        "aliases": [],
        "latitude": lat,
        "longitude": lon,
        "in_vienna": True,
        "pendler": False,
        "source": "oebb",
    }


def _legit_station() -> dict[str, Any]:
    return _poisoned_station()


# ---------------------------------------------------------------------------
# Lazy script imports (each script uses ``sys.path`` manipulation in its
# own header; importing once and caching keeps each per-writer test cheap).
# ---------------------------------------------------------------------------


def _import_script(name: str) -> Any:
    """Import a ``scripts/<name>.py`` module and return it.

    ``importlib.import_module`` is preferred over ``importlib.util`` so a
    re-import inside the same pytest run reuses the cached module rather
    than re-executing its top-level side effects (provider-cache wiring,
    logger configuration, etc.).
    """
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# (B) ``src.utils.cache.write_cache`` — PoC + inventory.
# ---------------------------------------------------------------------------


def _baustelle_event(*, lat: float, lon: float) -> dict[str, Any]:
    """Construct a Baustellen cache event matching the canonical shape
    written by :func:`scripts.update_baustellen_cache._feature_to_event`.
    """
    return {
        "source": "Stadt Wien – Baustellen",
        "category": "Baustelle",
        "title": "Arbeiten Mariahilfer Straße",
        "description": "Fahrbahnverengung",
        "link": "https://www.data.gv.at/katalog/baustellen",
        "guid": "0000000000000000000000000000000000000000000000000000000000000000",
        "pubDate": "2025-10-01T06:00:00+02:00",
        "starts_at": "2025-10-01T06:00:00+02:00",
        "ends_at": "2025-10-15T22:00:00+02:00",
        "location": {
            "address": "Mariahilfer Straße",
            "coordinates": {"lat": lat, "lon": lon},
        },
    }


__all__ = [
    "REPO_ROOT",
    "SCRIPTS_DIR",
    "SENTINEL_COMPANION_WRITER_DRIFT",
    "_NAN",
    "_NEG_INF",
    "_POS_INF",
    "_baustelle_event",
    "_import_script",
    "_legit_station",
    "_poisoned_station",
    "_raise_on_nan_or_infinity",
]
