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


def test_load_tiles_from_file_limits_entries(tmp_path: Path) -> None:
    # Pre-fix this test wrote to the production-adjacent path
    # ``data/test_tiles_limit.json`` with a ``finally``-based cleanup —
    # not robust against pytest signal termination or a write-time
    # exception that fires before the ``try`` block. ``tmp_path`` is
    # the canonical fixture for test-owned files: pytest cleans it up
    # automatically and the path lives outside the repository root,
    # so a failure cannot leak into ``git status`` either way.
    data_path = tmp_path / "test_tiles_limit.json"
    too_many = [{"lat": 48.2, "lng": 16.3}] * (tiling.MAX_TILE_COUNT + 1)
    data_path.write_text(json.dumps(too_many), encoding="utf-8")
    with pytest.raises(ValueError, match="Tile configuration exceeds the limit"):
        tiling.load_tiles_from_file(data_path)


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
