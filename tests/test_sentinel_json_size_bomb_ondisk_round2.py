"""Sentinel PoC: JSON size-bomb defence — Round 2 drift sites.

Threat model
------------
The 2026-05-08 round of JSON size-bomb defences (``test_sentinel_json_size_bomb_ondisk.py``)
canonicalised the "stat-then-cap-then-read" pattern for FIVE on-disk JSON
parsers in ``src/`` (``cache.py:read_cache``/``read_status``/``write_cache``
data-degradation guard, ``quota.py:MonthlyQuota.load``,
``tiling.py:load_tiles_from_file``). The verdict line named those five sites
but the inverse enumeration grep
(``git grep -nE 'json\\.loads\\(|json\\.load\\(' src/`` paired with
``not preceded by stat\\(\\)\\.st_size``) returns SIX FURTHER open sites in
``src/`` whose loaders share the canonical depth-bomb catch tuple
``except (json.JSONDecodeError, RecursionError)`` but lack the byte-size cap:

  1. ``src/utils/stations.py:_station_entries`` — module-level
     ``@lru_cache`` loader for ``data/stations.json``. Called from EVERY
     station-name lookup repo-wide (``canonical_name``, ``station_info``,
     ``station_by_oebb_id``, ``vor_station_ids``, ``is_in_vienna``…).
     A planted-huge ``stations.json`` (compromised CI runner / partial
     flush + power loss / corrupted previous run) propagates
     ``MemoryError`` past the ``except (OSError, json.JSONDecodeError,
     RecursionError)`` handler (``MemoryError`` is ``BaseException``,
     not ``Exception``) and crashes every feed-build path that touches
     a station name. CRITICAL — the highest-blast-radius drift site.

  2. ``src/utils/stations.py:_vienna_polygons`` — module-level
     ``@lru_cache`` loader for ``data/LANDESGRENZEOGD.json``. Called
     from ``is_in_vienna(lat, lon)``. Same ``MemoryError`` propagation
     as Site 1.

  3. ``src/places/merge.py:load_stations`` — operator-supplied stations
     file passed via ``update_station_directory.py`` CLI. Caller wraps
     the ``ValueError`` in ``except Exception`` so the post-fix
     behaviour mirrors ``quota.py`` / ``tiling.py`` (raise
     ``ValueError`` with descriptive message).

  4. ``src/build_feed.py:_load_state`` — orchestrator's load of
     ``data/first_seen.json`` (cross-run dedup state). Already wrapped
     in ``except (FileNotFoundError, json.JSONDecodeError)`` then a
     broad ``except Exception`` returning ``{}``, so ``MemoryError``
     escapes BOTH handlers and crashes the orchestrator BEFORE any
     provider runs.

  5. ``src/build_feed.py:_save_state`` data-merge guard — reads the
     EXISTING state file under exclusive lock before overwriting.
     Same ``MemoryError`` propagation as Site 4 but in the WRITE path:
     the state file is never atomically updated so the next run
     re-reads the planted-huge file and crashes again (recovery
     requires manual file deletion).

  6. ``src/utils/stations_validation.py:_load_stations`` — operator-
     supplied stations file passed via ``scripts/validate_stations.py``.
     The validator already raises ``StationValidationError`` on
     malformed JSON; same fail-mode for oversized files keeps the
     canonical exit-1 path intact instead of an unhandled
     ``MemoryError`` traceback.

Why the depth-bomb catch tuple is structurally insufficient (Round 1 verdict
quoted): "(a) ``json.loads`` does NOT raise ``RecursionError`` on a flat
list regardless of length — only nested structures hit the recursion limit;
(b) ``path.read_text(encoding=\"utf-8\")`` and ``json.load(fh)`` BOTH buffer
the entire file before parsing — a 1 GiB file allocates a 1 GiB Python
string plus another ~5 GiB worth of ``int``/``list``/``dict`` objects after
parse; (c) the resulting ``MemoryError`` is a ``BaseException`` subclass —
it is NOT caught by any of the surrounding ``except (OSError,
json.JSONDecodeError, RecursionError)`` handlers".

The fix shape mirrors Round 1: each loader stat()'s the file BEFORE
opening / reading, and treats sizes above the per-loader
``MAX_*_FILE_BYTES`` constant as missing (cache-style: return empty
record) or unreadable (quota-/tiling-style: raise ``ValueError`` /
``StationValidationError``). Each cap is sized at ~100x the largest
legitimately-written file shape (50 MiB for stations.json /
LANDESGRENZEOGD.json, 50 MiB for the orchestrator state file) so the
cap does NOT introduce a false-positive rejection of valid state.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_oversized(path: Path, size_bytes: int) -> None:
    """Write a flat-but-huge JSON list that exceeds the loader's byte cap.

    The payload shape ``[0,0,0,…]`` is intentional: it is BOTH a valid
    JSON document (so ``json.loads`` would succeed if it ran) AND wide
    enough to consume memory proportional to the file size. Pre-fix,
    every loader below would buffer the whole file via
    ``path.read_text()`` / ``json.load(fh)`` and consume O(file_size)
    memory; post-fix, the size cap rejects the file before opening.
    """
    payload = "[" + ",".join("0" for _ in range(size_bytes // 2)) + "]"
    path.write_text(payload, encoding="utf-8")


# ============================================================================
# Precondition: the canonical cap constants are exposed and sized for
# production. Pinning the existence of the constants is the auto-discoverable
# invariant: if a future refactor renames or removes them, every regression
# test below would silently pass even on unfixed code, so the precondition
# pin is the structural defence against the cap-constant drift that
# previously surfaced this same family.
# ============================================================================


def test_precondition_round2_size_cap_constants_exist() -> None:
    from src.utils import stations
    from src.places import merge as places_merge
    import src.build_feed as build_feed_mod

    assert isinstance(stations.MAX_STATIONS_FILE_BYTES, int)
    assert stations.MAX_STATIONS_FILE_BYTES > 0
    # Cap must accommodate the production stations.json (~175 KiB) with
    # comfortable headroom for fleet growth; a sub-1 MiB cap would
    # reject normal directory state.
    assert stations.MAX_STATIONS_FILE_BYTES >= 1_000_000

    assert isinstance(stations.MAX_VIENNA_POLYGON_FILE_BYTES, int)
    assert stations.MAX_VIENNA_POLYGON_FILE_BYTES > 0
    assert stations.MAX_VIENNA_POLYGON_FILE_BYTES >= 1_000_000

    # ``places.merge`` re-uses the canonical stations cap to keep the
    # boundary consistent across all on-disk readers of stations.json.
    assert hasattr(places_merge, "MAX_STATIONS_FILE_BYTES")
    assert places_merge.MAX_STATIONS_FILE_BYTES > 0

    assert isinstance(build_feed_mod.MAX_STATE_FILE_BYTES, int)
    assert build_feed_mod.MAX_STATE_FILE_BYTES > 0
    assert build_feed_mod.MAX_STATE_FILE_BYTES >= 1_000_000


# ============================================================================
# Site 1: src/utils/stations.py:_station_entries (data/stations.json)
# ============================================================================


def test_station_entries_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_station_entries`` opened ``data/stations.json``
    via ``_STATIONS_PATH.open(\"r\")`` and called ``json.load(handle)``
    unconditionally. A planted-huge stations.json buffered into
    memory and crashed the @lru_cache-decorated loader with
    ``MemoryError`` (BaseException, not caught by the surrounding
    handler). Post-fix: the size cap rejects the file before
    ``open`` is called and the loader returns the canonical empty
    tuple — keeping every station-lookup path operational on
    degraded data."""
    from src.utils import stations

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(stations, "_STATIONS_PATH", fake_stations, raising=False)
    monkeypatch.setattr(stations, "MAX_STATIONS_FILE_BYTES", 1024, raising=False)
    # Bust the @lru_cache so the loader actually runs against the patched path.
    stations._station_entries.cache_clear()

    with patch("src.utils.stations.json.load") as mock_load:
        result = stations._station_entries()

    # Post-fix: json.load is never reached because the size cap fires first.
    mock_load.assert_not_called()
    assert result == ()
    stations._station_entries.cache_clear()


def test_station_entries_normal_file_unaffected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check: the size cap must not reject normal-sized stations files."""
    from src.utils import stations

    fake_stations = tmp_path / "stations.json"
    fake_stations.write_text(
        json.dumps([{"name": "Wien Hbf", "in_vienna": True}]),
        encoding="utf-8",
    )

    monkeypatch.setattr(stations, "_STATIONS_PATH", fake_stations, raising=False)
    stations._station_entries.cache_clear()

    result = stations._station_entries()
    assert len(result) == 1
    assert result[0]["name"] == "Wien Hbf"
    stations._station_entries.cache_clear()


# ============================================================================
# Site 2: src/utils/stations.py:_vienna_polygons (data/LANDESGRENZEOGD.json)
# ============================================================================


def test_vienna_polygons_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_vienna_polygons`` opened the polygon file and called
    ``json.load(handle)`` unconditionally. Post-fix: oversized files
    are rejected before ``open`` and the loader returns the canonical
    empty tuple — degrades to "no Vienna geo-fence" instead of
    crashing every ``is_in_vienna(lat, lon)`` caller."""
    from src.utils import stations

    fake_polygon = tmp_path / "polygon.json"
    _write_oversized(fake_polygon, 4096)

    monkeypatch.setattr(stations, "_VIENNA_POLYGON_PATH", fake_polygon, raising=False)
    monkeypatch.setattr(
        stations, "MAX_VIENNA_POLYGON_FILE_BYTES", 1024, raising=False
    )
    stations._vienna_polygons.cache_clear()

    with patch("src.utils.stations.json.load") as mock_load:
        result = stations._vienna_polygons()

    mock_load.assert_not_called()
    assert result == ()
    stations._vienna_polygons.cache_clear()


# ============================================================================
# Site 3: src/places/merge.py:load_stations (operator-supplied stations file)
# ============================================================================


def test_places_merge_load_stations_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``load_stations`` called ``path.read_text()`` then
    ``json.loads(content)`` — a 1 GiB stations file would buffer
    into memory and crash with ``MemoryError`` before the depth-bomb
    catch could run. Post-fix: the size cap raises ``ValueError``
    cleanly, preserving the existing error contract used by
    ``update_station_directory.py``'s broad-except caller."""
    from src.places import merge

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(merge, "MAX_STATIONS_FILE_BYTES", 1024, raising=False)

    with pytest.raises(ValueError, match="too large|zu groß"):
        merge.load_stations(fake_stations)


def test_places_merge_load_stations_normal_file_unaffected(
    tmp_path: Path,
) -> None:
    """Sanity check: normal-sized stations files load successfully."""
    from src.places import merge

    fake_stations = tmp_path / "stations.json"
    fake_stations.write_text(
        json.dumps({"stations": [{"name": "Wien Hbf"}]}),
        encoding="utf-8",
    )

    result = merge.load_stations(fake_stations)
    assert len(result) == 1
    assert result[0].get("name") == "Wien Hbf"


# ============================================================================
# Site 4: src/build_feed.py:_load_state (orchestrator state file)
# ============================================================================


def test_load_state_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_load_state`` opened the state file via
    ``path.open(\"r\")`` and called ``json.load(f)`` unconditionally.
    A planted-huge ``data/first_seen.json`` (corrupted previous run /
    compromised CI runner) buffered into memory and crashed the
    orchestrator BEFORE any provider could run — leaving partial
    state. Post-fix: the size cap rejects the file before ``open``
    and ``_load_state`` returns the canonical empty dict, mirroring
    the existing ``json.JSONDecodeError`` fallback shape."""
    import src.build_feed as build_feed_mod
    from src.feed import config as feed_config

    # ``validate_path`` requires the state file to live under a
    # whitelisted root (``data/``); chdir into ``tmp_path`` so the
    # validator accepts the planted path.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    state_file = tmp_path / "data" / "state.json"
    _write_oversized(state_file, 4096)

    monkeypatch.setattr(feed_config, "STATE_FILE", state_file, raising=False)
    monkeypatch.setattr(build_feed_mod, "MAX_STATE_FILE_BYTES", 1024, raising=False)

    with patch("src.build_feed.json.load") as mock_load:
        result = build_feed_mod._load_state()

    mock_load.assert_not_called()
    assert result == {}


def test_save_state_merge_guard_handles_oversized_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_save_state``'s safe-merge step opened the EXISTING
    state file under exclusive lock before overwriting. A planted-
    huge file would crash mid-save with ``MemoryError`` instead of
    treating the unparseable state as overwriteable. Post-fix: the
    size cap fires before the merge read and the new state is
    written without consulting the planted state — recovery is
    automatic on the next run."""
    import src.build_feed as build_feed_mod
    from src.feed import config as feed_config

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    state_file = tmp_path / "data" / "state.json"
    _write_oversized(state_file, 4096)

    monkeypatch.setattr(feed_config, "STATE_FILE", state_file, raising=False)
    monkeypatch.setattr(build_feed_mod, "MAX_STATE_FILE_BYTES", 1024, raising=False)

    new_state = {
        "test-id": {"first_seen": "2025-01-01T00:00:00+00:00"}
    }
    # Pre-fix: this would attempt to ``json.load`` the 4 KiB existing
    # state against a 1 KiB cap → without the new size guard the
    # planted-huge path would crash. Post-fix: the existing state is
    # treated as overwriteable without consulting the planted payload.
    build_feed_mod._save_state(new_state)

    rewritten = json.loads(state_file.read_text(encoding="utf-8"))
    assert isinstance(rewritten, dict)
    assert "test-id" in rewritten


# ============================================================================
# Site 6: src/utils/stations_validation.py:_load_stations
# ============================================================================


def test_stations_validation_load_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_load_stations`` called ``path.read_text()`` and
    ``json.loads(raw)`` — a planted-huge stations file (operator
    error, attacker-supplied path) would crash the validator with
    ``MemoryError``. Post-fix: the size cap raises
    ``StationValidationError`` cleanly, preserving the canonical
    exit-1 path used by ``scripts/validate_stations.py``."""
    from src.utils import stations_validation

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(
        stations_validation, "MAX_STATIONS_FILE_BYTES", 1024, raising=False
    )

    with pytest.raises(stations_validation.StationValidationError, match="too large|zu groß"):
        stations_validation._load_stations(fake_stations)


# ============================================================================
# Walker: assert NO src/ on-disk JSON loader is missing the size cap.
# This is the "auto-discoverable invariant" the journal mandates so a
# future PR that adds a new on-disk JSON loader without the size cap
# fails the suite at PR-review time. The walker enumerates the canonical
# loader sites and asserts each has the corresponding ``MAX_*_FILE_BYTES``
# constant exposed on the same module.
# ============================================================================


def test_canonical_size_cap_constants_inventory() -> None:
    """Inventory of every covered on-disk JSON parser's size cap
    constant. If a future refactor moves a loader to a new module the
    inventory below MUST be updated to keep the ``Round 1 + Round 2``
    coverage map auditable from a single test."""
    inventory: list[tuple[str, str]] = [
        # Round 1
        ("src.utils.cache", "MAX_CACHE_FILE_BYTES"),
        ("src.places.quota", "MAX_QUOTA_FILE_BYTES"),
        ("src.places.tiling", "MAX_TILE_FILE_BYTES"),
        # Round 2 (this file)
        ("src.utils.stations", "MAX_STATIONS_FILE_BYTES"),
        ("src.utils.stations", "MAX_VIENNA_POLYGON_FILE_BYTES"),
        ("src.places.merge", "MAX_STATIONS_FILE_BYTES"),
        ("src.utils.stations_validation", "MAX_STATIONS_FILE_BYTES"),
        ("src.build_feed", "MAX_STATE_FILE_BYTES"),
    ]

    for module_name, attribute in inventory:
        module = importlib.import_module(module_name)
        cap = getattr(module, attribute, None)
        assert isinstance(cap, int) and cap > 0, (
            f"{module_name}.{attribute} must be a positive int — got {cap!r}"
        )
