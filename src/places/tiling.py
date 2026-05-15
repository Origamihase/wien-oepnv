"""Helpers for configuring search tiles for the Google Places API."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Iterator, Mapping

from ..utils.files import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
)

__all__ = ["Tile", "load_tiles_from_env", "load_tiles_from_file", "iter_tiles"]


@dataclass(frozen=True)
class Tile:
    """A circular search tile defined by latitude and longitude."""

    latitude: float
    longitude: float


_DEFAULT_TILE = Tile(latitude=48.208174, longitude=16.373819)
MAX_TILE_COUNT = 200

# Security: defense-in-depth byte-size cap on the on-disk tile config
# file. Tile configs are operator-supplied and bounded by
# ``MAX_TILE_COUNT`` (200 entries × ~50 bytes each ≈ 10 KiB), so a 1 MiB
# cap is ~100x the largest legitimate tile config and bounds the
# worst-case parse cost well below any cron runner's ulimit. The
# depth-bomb defence above catches the deeply-nested attack via
# ``RecursionError``, but a wide-but-flat attack (e.g. ``[{}]*1_000_000``
# or a 1 GiB pretty-printed dump) would slip past the depth check —
# ``json.loads`` on a flat list does NOT raise ``RecursionError`` and
# ``MemoryError`` is a ``BaseException`` that propagates past the
# ``json.JSONDecodeError`` handler. Threat model mirrors
# ``MAX_QUOTA_FILE_BYTES`` in ``src/places/quota.py``: compromised CI
# runner / corrupted previous write / partial flush + power loss.
MAX_TILE_FILE_BYTES = 1024 * 1024


def _validate_tile_count(count: int) -> None:
    # Security: prevent unbounded tile lists from triggering excessive API calls/DoS.
    if count > MAX_TILE_COUNT:
        raise ValueError(
            f"Tile configuration exceeds the limit of {MAX_TILE_COUNT} entries."
        )


def _coerce_coordinate(raw: Mapping[str, object], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, float | int):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise TypeError(f"Invalid {key!r} value in tile specification: {value!r}")


def _parse_tiles(raw_tiles: Iterable[object]) -> list[Tile]:
    tiles: list[Tile] = []
    for raw in raw_tiles:
        # Zero-Trust: env- and file-supplied JSON may contain non-object
        # entries (scalars, lists, null). Reject them with a clean ValueError
        # before invoking ``raw.get`` would raise AttributeError.
        if not isinstance(raw, Mapping):
            raise ValueError(f"Invalid tile specification: {raw!r}")
        try:
            lat = _coerce_coordinate(raw, "lat")
            lng = _coerce_coordinate(raw, "lng")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid tile specification: {raw!r}") from exc
        tiles.append(Tile(latitude=lat, longitude=lng))
    return tiles


def load_tiles_from_env(raw_value: str | None) -> list[Tile]:
    """Parse ``raw_value`` from ``PLACES_TILES`` into :class:`Tile` objects."""

    if not raw_value:
        return [_DEFAULT_TILE]

    # Security: ``RecursionError`` covers JSON depth-bomb attacks via
    # operator-controlled env / leaked CI env. ``json.loads`` raises
    # ``RecursionError`` (NOT a subclass of ``json.JSONDecodeError`` and
    # NOT caught by ``except ValueError``) on a deeply-nested but
    # well-formed payload. Without this catch the unhandled
    # ``RecursionError`` propagates out of ``_load_tiles_configuration``
    # in ``update_station_directory.py`` (caller's
    # ``except (OSError, ValueError)`` does NOT catch ``RecursionError``)
    # and crashes the cron pipeline. Same canonical defence as the
    # network-sourced parsers in ``src/places/client.py``.
    try:
        # Security (reader-side non-finite literal defence): tile
        # configs carry float-typed ``latitude`` / ``longitude`` fields
        # which are downstream-validated by :class:`Tile` (a frozen
        # dataclass). A planted ``NaN`` / ``Infinity`` / ``-Infinity`` /
        # ``1e1000`` in the ``PLACES_TILES`` env value (operator-
        # controlled env / leaked CI env / compromised secret store) is
        # rejected at the parse boundary so the in-memory tile list
        # never contains a non-finite coordinate that would crash the
        # bounding-box / haversine calculations downstream.
        data = json.loads(
            raw_value,
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("PLACES_TILES is not valid JSON") from exc
    if not isinstance(data, list):
        raise ValueError("PLACES_TILES must encode a list of objects")
    _validate_tile_count(len(data))
    return _parse_tiles(data)


def load_tiles_from_file(path: Path) -> list[Tile]:
    """Load tile configuration from ``path``."""

    # Security: same depth-bomb defence as ``load_tiles_from_env`` above.
    # The on-disk path mirrors the env-source threat model — a depth-bomb
    # in an operator-supplied tiles file (or a corrupted previous output)
    # would otherwise propagate ``RecursionError`` past the caller's
    # ``except (OSError, ValueError)`` and crash the surrounding cron.
    # Security: byte-size cap (see MAX_TILE_FILE_BYTES) defeats the
    # wide-but-flat size-bomb attack that the depth-bomb catch does NOT
    # cover. Open first, then ``os.fstat`` — closes the TOCTOU between
    # ``stat`` and ``read_text`` that lets an attacker swap the inode
    # between the two syscalls. The ``read(MAX_TILE_FILE_BYTES + 1)``
    # cap defends against zero-st_size special files.
    try:
        with path.open("rb") as handle:
            file_size = os.fstat(handle.fileno()).st_size
            if file_size > MAX_TILE_FILE_BYTES:
                raise ValueError(
                    f"Tile file too large (> {MAX_TILE_FILE_BYTES} bytes)"
                )
            raw_bytes = handle.read(MAX_TILE_FILE_BYTES + 1)
            if len(raw_bytes) > MAX_TILE_FILE_BYTES:
                raise ValueError(
                    f"Tile file too large (> {MAX_TILE_FILE_BYTES} bytes)"
                )
    except OSError as exc:
        raise ValueError("Tile file is not readable") from exc
    try:
        # Security (reader-side non-finite literal defence): mirrors
        # the env-source defence in :func:`load_tiles_from_env`. The
        # on-disk path inherits the same operator-supplied-config threat
        # model — a non-finite ``latitude`` / ``longitude`` in the tile
        # file would otherwise propagate as ``float('nan')`` /
        # ``float('inf')`` into the bounding-box / haversine math.
        data = json.loads(
            raw_bytes,
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
        raise ValueError("Tile file is not valid JSON") from exc
    if not isinstance(data, list):
        raise ValueError("Tile file must contain a list of tile objects")
    _validate_tile_count(len(data))
    return _parse_tiles(data)


def iter_tiles(tiles: Iterable[Tile]) -> Iterator[Tile]:
    """Yield tiles from ``tiles`` ensuring there is at least one tile."""

    materialised = list(tiles)
    if not materialised:
        yield _DEFAULT_TILE
        return
    yield from materialised
