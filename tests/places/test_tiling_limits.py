"""Tests for tile configuration limits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.places import tiling


def test_load_tiles_from_env_limits_entries() -> None:
    too_many = [{"lat": 48.2, "lng": 16.3}] * (tiling.MAX_TILE_COUNT + 1)
    payload = json.dumps(too_many)

    with pytest.raises(ValueError, match="Tile configuration exceeds the limit"):
        tiling.load_tiles_from_env(payload)


def test_load_tiles_from_file_limits_entries() -> None:
    data_path = Path("data/test_tiles_limit.json")
    too_many = [{"lat": 48.2, "lng": 16.3}] * (tiling.MAX_TILE_COUNT + 1)
    try:
        data_path.write_text(json.dumps(too_many), encoding="utf-8")
        with pytest.raises(ValueError, match="Tile configuration exceeds the limit"):
            tiling.load_tiles_from_file(data_path)
    finally:
        data_path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "payload",
    [
        "[1, 2, 3]",
        "[null]",
        '["just a string"]',
        "[[48.2, 16.3]]",
        "[true]",
    ],
)
def test_load_tiles_from_env_rejects_non_object_entries(payload: str) -> None:
    # Zero-Trust: env-supplied JSON must not crash with AttributeError when
    # entries are scalars/lists/null. The loader must surface a clean
    # ValueError instead so callers can handle the misconfiguration.
    with pytest.raises(ValueError, match="Invalid tile specification"):
        tiling.load_tiles_from_env(payload)
