"""Lazy ``src`` helper loaders and shared logger of ``scripts/update_wl_stations.py``.

Moved verbatim from the script. ``_project_root`` resolves ``parents[2]``
from here, the same repository root the script's ``parents[1]`` gave. The
logger keeps its name ``update_wl_stations``.
A test that patches a constant or function used here must patch it
on this module, not on the script.
"""
from __future__ import annotations

import logging
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from collections.abc import Callable


def _path_fingerprint(path: Path) -> str:
    """One-way SHA-256 fingerprint of ``str(path)`` for log lines (12 hex chars).

    :func:`src.utils.env._path_fingerprint`, imported lazily like the other
    ``src`` helpers of this module (``_load_is_in_vienna`` etc.) so the
    script stays importable before the repository root is on ``sys.path``.
    The path arguments logged below come from operator-controlled CLI
    options; the hex-only fingerprint keeps Trojan-Source primitives and
    ANSI escapes out of the log, and operators correlate by re-hashing
    the candidate path locally.
    """
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    from src.utils.env import _path_fingerprint as fingerprint

    return fingerprint(path)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_is_in_vienna() -> Callable[..., bool]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.stations")
    return cast(Callable[..., bool], module.is_in_vienna)


def _load_station_helper(name: str) -> Callable[..., Any]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.stations")
    return cast(Callable[..., Any], getattr(module, name))


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


def _load_calculate_distance_meters() -> Callable[..., float]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.geo")
    return cast(Callable[..., float], module.calculate_distance_meters)


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


BASE_DIR = _project_root()
is_in_vienna = _load_is_in_vienna()
is_plausible_wl_coordinate = _load_station_helper("is_plausible_wl_coordinate")
load_wl_gtfs_coordinates = _load_station_helper("load_wl_gtfs_coordinates")
calculate_distance_meters = _load_calculate_distance_meters()
atomic_write = _load_atomic_write()
read_capped_json = _load_read_capped_json()
read_capped_text = _load_read_capped_text()
scrub_trojan_source_primitives = _load_scrub_trojan_source_primitives()
get_bool_env = _load_get_bool_env()
sanitize_log_arg = _load_sanitize_log_arg()
resolve_at_coordinate = _load_resolve_at_coordinate()

log = logging.getLogger("update_wl_stations")
