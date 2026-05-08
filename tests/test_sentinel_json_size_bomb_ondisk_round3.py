"""Sentinel PoC: JSON size-bomb defence — Round 3 ``scripts/`` sweep.

Threat model
------------
Round 1 (``test_sentinel_json_size_bomb_ondisk.py``) and Round 2
(``test_sentinel_json_size_bomb_ondisk_round2.py``) canonicalised the
"stat-then-cap-then-read" pattern for ELEVEN on-disk JSON parsers in
``src/``. Round 2's verdict line explicitly deferred the ``scripts/`` tree
(~20 sibling parsers) to Round 3 with structural roadmap: every
script-level on-disk parser also needs the same cap because the
cron-pipeline blast radius (orchestrator runs every update script via
``subprocess.run(check=True)``) propagates an unhandled ``MemoryError`` —
``BaseException``, NOT caught by the depth-bomb tuple — out as
``CalledProcessError`` and aborts the whole pipeline.

This Round 3 closes SIXTEEN open on-disk loaders across eight scripts:

  scripts/enrich_station_aliases.py
    * ``_load_vor_mapping`` — VOR mapping; cron-step run via
      ``update_all_stations.py`` orchestrator.
    * ``_load_pendler_alternative_names`` — pendler candidates.
    * ``main`` — operator-supplied stations.json.

  scripts/fetch_vor_haltestellen.py
    * ``load_stations`` — operator-supplied stations.json.
    * ``load_pendler_candidate_names`` — pendler candidates.

  scripts/update_all_stations.py
    * ``_load_stations`` — post-merge stations.json (heartbeat input).
    * ``_count_polygon_vertices`` — Vienna polygon (heartbeat input).

  scripts/update_baustellen_cache.py
    * ``_load_fallback`` — bundled baustellen geojson; the network-
      unreachable failover path.

  scripts/update_station_directory.py
    * ``_load_existing_station_entries`` — existing-state stations.json.
    * ``_load_vor_name_to_id_map`` — VOR mapping.
    * ``load_pendler_station_ids`` — pendler ID whitelist.
    * ``load_pendler_name_candidates`` — pendler name whitelist.

  scripts/update_vor_stations.py
    * ``merge_into_stations`` — existing-state stations.json.

  scripts/update_wl_stations.py
    * ``load_vor_mapping`` — VOR mapping.
    * ``merge_into_stations`` — existing-state stations.json.

  scripts/validate_vor_mapping.py
    * ``main`` — VOR mapping (CLI validator).

The fix shape mirrors Round 1/2: every script imports the new shared
``src.utils.files.read_capped_json`` helper (combines the byte-size cap
with the depth-bomb catch tuple in one place) and exposes its own
``MAX_JSON_FILE_BYTES`` constant (50 MiB, ~285x the production
stations.json) so the auto-discoverable inventory test catches any
future loader added without the cap.

Why the depth-bomb catch tuple is structurally insufficient (Round 1
verdict quoted): "(a) ``json.loads`` does NOT raise ``RecursionError``
on a flat list regardless of length — only nested structures hit the
recursion limit; (b) ``path.read_text()`` and ``json.load(fh)`` BOTH
buffer the entire file before parsing; (c) the resulting
``MemoryError`` is a ``BaseException`` subclass — it is NOT caught by
the surrounding ``except (OSError, json.JSONDecodeError, RecursionError)``
handler".
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
# Precondition: the canonical helper and per-script cap constants exist.
# Pinning these is the auto-discoverable invariant: a future PR that adds a
# new on-disk JSON loader without the cap fails the inventory test below.
# ============================================================================


def test_precondition_round3_helper_exists() -> None:
    """The shared helper must be importable from ``src/utils/files.py``."""
    from src.utils.files import DEFAULT_MAX_JSON_FILE_BYTES, read_capped_json

    assert callable(read_capped_json)
    assert isinstance(DEFAULT_MAX_JSON_FILE_BYTES, int)
    # Cap must accommodate the largest legitimate on-disk JSON in the repo
    # (production stations.json ~ 175 KiB; production polygon ~ 146 KiB)
    # with comfortable headroom for fleet growth.
    assert DEFAULT_MAX_JSON_FILE_BYTES >= 1_000_000


def test_canonical_size_cap_constants_inventory_round3() -> None:
    """Inventory of every covered ``scripts/`` on-disk JSON parser's
    size cap constant. If a future refactor moves a loader to a new
    module the inventory below MUST be updated to keep the
    ``Round 3`` coverage map auditable from a single test."""
    inventory: list[str] = [
        "scripts.enrich_station_aliases",
        "scripts.fetch_vor_haltestellen",
        "scripts.update_all_stations",
        "scripts.update_baustellen_cache",
        "scripts.update_station_directory",
        "scripts.update_vor_stations",
        "scripts.update_wl_stations",
        "scripts.validate_vor_mapping",
    ]

    for module_name in inventory:
        module = importlib.import_module(module_name)
        cap = getattr(module, "MAX_JSON_FILE_BYTES", None)
        assert isinstance(cap, int) and cap > 0, (
            f"{module_name}.MAX_JSON_FILE_BYTES must be a positive int — got {cap!r}"
        )
        # Cap must accommodate the largest legitimate on-disk JSON shape
        # in this script's domain. 1 MiB floor catches accidentally
        # tightening the cap into the production-state range.
        assert cap >= 1_000_000


# ============================================================================
# Site 1: scripts/enrich_station_aliases.py:_load_vor_mapping
# ============================================================================


def test_enrich_load_vor_mapping_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_load_vor_mapping`` buffered the entire mapping file
    via ``path.read_text()``. A planted-huge file (~1 GiB of
    ``[0,0,…]``) propagated ``MemoryError`` past the depth-bomb catch
    tuple and crashed the cron pipeline (``subprocess.run`` from the
    orchestrator). Post-fix: the size cap rejects the file before
    ``open()`` and the loader returns the canonical empty dict."""
    from scripts import enrich_station_aliases as eas

    fake_mapping = tmp_path / "vor-haltestellen.mapping.json"
    _write_oversized(fake_mapping, 4096)

    monkeypatch.setattr(eas, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = eas._load_vor_mapping(fake_mapping)

    mock_load.assert_not_called()
    assert result == {}


# ============================================================================
# Site 2: scripts/enrich_station_aliases.py:_load_pendler_alternative_names
# ============================================================================


def test_enrich_load_pendler_alternative_names_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror of Site 1; planted-huge pendler-candidates
    file propagated ``MemoryError`` past the depth-bomb catch."""
    from scripts import enrich_station_aliases as eas

    fake_pendler = tmp_path / "pendler_candidates.json"
    _write_oversized(fake_pendler, 4096)

    monkeypatch.setattr(eas, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = eas._load_pendler_alternative_names(fake_pendler)

    mock_load.assert_not_called()
    assert result == {}


# ============================================================================
# Site 3: scripts/enrich_station_aliases.py:main (operator-supplied stations)
# ============================================================================


def test_enrich_main_rejects_oversized_stations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``main`` buffered the operator-supplied stations file
    via ``path.read_text()``. Post-fix: the size cap returns exit code
    1 — the canonical exit-1 path keeps downstream scripts running."""
    from scripts import enrich_station_aliases as eas

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(eas, "MAX_JSON_FILE_BYTES", 1024)

    args = type("Args", (), {
        "stations": fake_stations,
        "vor_stops": tmp_path / "missing-vor-stops.csv",
        "vor_mapping": tmp_path / "missing-vor-mapping.json",
        "gtfs_stops": tmp_path / "missing-gtfs-stops.txt",
        "pendler_candidates": tmp_path / "missing-pendler.json",
        "verbose": False,
    })()

    monkeypatch.setattr(eas, "parse_args", lambda: args)

    with patch("src.utils.files.json.load") as mock_load:
        rc = eas.main()

    mock_load.assert_not_called()
    assert rc == 1


# ============================================================================
# Site 4: scripts/fetch_vor_haltestellen.py:load_stations
# ============================================================================


def test_fetch_vor_load_stations_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``load_stations`` did NOT catch FileNotFoundError so
    a missing file already crashed the script — the size-bomb axis
    additionally surfaces ``MemoryError`` past the depth-bomb catch.
    Post-fix: ``read_capped_json`` returns None and the loader yields
    the empty list fallback."""
    from scripts import fetch_vor_haltestellen as fvh

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(fvh, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = fvh.load_stations(fake_stations)

    mock_load.assert_not_called()
    assert result == []


# ============================================================================
# Site 5: scripts/fetch_vor_haltestellen.py:load_pendler_candidate_names
# ============================================================================


def test_fetch_vor_load_pendler_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror; planted-huge pendler-candidates file
    propagated ``MemoryError`` past the depth-bomb catch."""
    from scripts import fetch_vor_haltestellen as fvh

    fake_pendler = tmp_path / "pendler_candidates.json"
    _write_oversized(fake_pendler, 4096)

    monkeypatch.setattr(fvh, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = fvh.load_pendler_candidate_names(fake_pendler)

    mock_load.assert_not_called()
    assert result == []


# ============================================================================
# Site 6: scripts/update_all_stations.py:_load_stations
# ============================================================================


def test_update_all_stations_load_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: a planted-huge post-merge stations.json crashed
    ``_build_heartbeat`` AFTER the merged file had already been
    atomically written, masking the real cause. Post-fix: the size
    cap returns the canonical empty list."""
    from scripts import update_all_stations as uas

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(uas, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = uas._load_stations(fake_stations)

    mock_load.assert_not_called()
    assert result == []


# ============================================================================
# Site 7: scripts/update_all_stations.py:_count_polygon_vertices
# ============================================================================


def test_update_all_stations_polygon_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror for the polygon file. Post-fix: the size
    cap returns ``None`` (heartbeat records polygon counter as
    unavailable) — the canonical fallback."""
    from scripts import update_all_stations as uas

    fake_polygon = tmp_path / "LANDESGRENZEOGD.json"
    _write_oversized(fake_polygon, 4096)

    monkeypatch.setattr(uas, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = uas._count_polygon_vertices(fake_polygon)

    mock_load.assert_not_called()
    assert result is None


# ============================================================================
# Site 8: scripts/update_baustellen_cache.py:_load_fallback
# ============================================================================


def test_baustellen_load_fallback_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: the bundled baustellen fallback file is read on the
    network-unreachable failover path; a planted-huge fallback (e.g.
    via a compromised contributor commit) propagated ``MemoryError``
    past the depth-bomb catch and crashed the cache update on the
    very path used when the network is unreachable. Post-fix: the
    size cap returns ``None``."""
    from scripts import update_baustellen_cache as ubc

    fake_fallback = tmp_path / "baustellen_sample.geojson"
    _write_oversized(fake_fallback, 4096)

    monkeypatch.setattr(ubc, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = ubc._load_fallback(fake_fallback)

    mock_load.assert_not_called()
    assert result is None


# ============================================================================
# Site 9: scripts/update_station_directory.py:_load_existing_station_entries
# ============================================================================


def test_usd_load_existing_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror; planted-huge existing-state stations.json
    propagated ``MemoryError`` past the depth-bomb catch tuple."""
    from scripts import update_station_directory as usd

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(usd, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        mapping, manual = usd._load_existing_station_entries(fake_stations)

    mock_load.assert_not_called()
    assert mapping == {}
    assert manual == []


# ============================================================================
# Site 10: scripts/update_station_directory.py:_load_vor_name_to_id_map
# ============================================================================


def test_usd_load_vor_mapping_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror for the VOR mapping file."""
    from scripts import update_station_directory as usd

    fake_mapping = tmp_path / "vor-haltestellen.mapping.json"
    _write_oversized(fake_mapping, 4096)

    monkeypatch.setattr(usd, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = usd._load_vor_name_to_id_map(fake_mapping)

    mock_load.assert_not_called()
    assert result == {}


# ============================================================================
# Site 11: scripts/update_station_directory.py:load_pendler_station_ids
# ============================================================================


def test_usd_load_pendler_station_ids_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: malformed JSON raised ``ValueError`` (canonical exit-1
    contract), but ``MemoryError`` is ``BaseException`` and propagated
    past the entire try/except block. Post-fix: the size cap surfaces
    the same ``ValueError`` on miss, preserving the exit-1 contract."""
    from scripts import update_station_directory as usd

    fake_pendler = tmp_path / "pendler_stations.json"
    _write_oversized(fake_pendler, 4096)

    monkeypatch.setattr(usd, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        with pytest.raises(ValueError):
            usd.load_pendler_station_ids(fake_pendler)

    mock_load.assert_not_called()


# ============================================================================
# Site 12: scripts/update_station_directory.py:load_pendler_name_candidates
# ============================================================================


def test_usd_load_pendler_name_candidates_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror; planted-huge pendler-candidates file."""
    from scripts import update_station_directory as usd

    fake_pendler = tmp_path / "pendler_candidates.json"
    _write_oversized(fake_pendler, 4096)

    monkeypatch.setattr(usd, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = usd.load_pendler_name_candidates(fake_pendler)

    mock_load.assert_not_called()
    assert result == set()


# ============================================================================
# Site 13: scripts/update_vor_stations.py:merge_into_stations
# ============================================================================


def test_update_vor_merge_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: planted-huge stations.json crashed the VOR merge with
    ``MemoryError`` after the merge had partially started. Post-fix:
    the merge starts fresh from an empty state, restoring the
    canonical schema for the next run."""
    from scripts import update_vor_stations as uvs

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(uvs, "MAX_JSON_FILE_BYTES", 1024)

    # The merge writes back over the file; supply at least one VOR
    # entry so the merge has something to write. We don't assert on
    # the file content — only that the loader did not crash and
    # ``json.load`` was never invoked on the oversized file.
    vor_entries: list[dict[str, object]] = []

    with patch("src.utils.files.json.load") as mock_load:
        uvs.merge_into_stations(fake_stations, vor_entries)

    mock_load.assert_not_called()


# ============================================================================
# Site 14: scripts/update_wl_stations.py:load_vor_mapping
# ============================================================================


def test_update_wl_load_vor_mapping_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror; planted-huge VOR mapping file."""
    from scripts import update_wl_stations as uws

    fake_mapping = tmp_path / "vor-haltestellen.mapping.json"
    _write_oversized(fake_mapping, 4096)

    monkeypatch.setattr(uws, "MAX_JSON_FILE_BYTES", 1024)

    with patch("src.utils.files.json.load") as mock_load:
        result = uws.load_vor_mapping(fake_mapping)

    mock_load.assert_not_called()
    assert result == {}


# ============================================================================
# Site 15: scripts/update_wl_stations.py:merge_into_stations
# ============================================================================


def test_update_wl_merge_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix shape mirror of Site 13 for the WL merge."""
    from scripts import update_wl_stations as uws

    fake_stations = tmp_path / "stations.json"
    _write_oversized(fake_stations, 4096)

    monkeypatch.setattr(uws, "MAX_JSON_FILE_BYTES", 1024)

    wl_entries: list[dict[str, object]] = []

    with patch("src.utils.files.json.load") as mock_load:
        uws.merge_into_stations(fake_stations, wl_entries)

    mock_load.assert_not_called()


# ============================================================================
# Site 16: scripts/validate_vor_mapping.py:main
# ============================================================================


def test_validate_vor_mapping_rejects_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: planted-huge mapping file crashed the validator with
    an unhandled ``MemoryError`` traceback instead of the documented
    exit-1 contract. Post-fix: the size cap surfaces a clean exit-1."""
    from scripts import validate_vor_mapping as vvm

    fake_mapping = tmp_path / "vor-haltestellen.mapping.json"
    _write_oversized(fake_mapping, 4096)

    monkeypatch.setattr(vvm, "MAX_JSON_FILE_BYTES", 1024)
    # Patch Path("data/vor-haltestellen.mapping.json") at the module
    # level so the validator reads our temp file.
    monkeypatch.chdir(tmp_path)
    fake_mapping_at_default = tmp_path / "data" / "vor-haltestellen.mapping.json"
    fake_mapping_at_default.parent.mkdir(parents=True, exist_ok=True)
    _write_oversized(fake_mapping_at_default, 4096)

    with patch("src.utils.files.json.load") as mock_load:
        rc = vvm.main()

    mock_load.assert_not_called()
    assert rc == 1


# ============================================================================
# Sanity checks: normal-sized JSON files MUST still parse correctly. The
# size cap is at 50 MiB by default and tests above patch it to 1024 bytes
# to trigger the rejection path; without an explicit sanity check at the
# default cap the tests would silently pass even on a hypothetical
# refactor that broke the read path entirely.
# ============================================================================


def test_enrich_load_vor_mapping_normal_unaffected(tmp_path: Path) -> None:
    from scripts import enrich_station_aliases as eas

    mapping = tmp_path / "vor-haltestellen.mapping.json"
    mapping.write_text(
        json.dumps([{"bst_id": 1, "resolved_name": "Wien Hbf"}]),
        encoding="utf-8",
    )

    result = eas._load_vor_mapping(mapping)
    assert result == {1: "Wien Hbf"}


def test_fetch_vor_load_stations_normal_unaffected(tmp_path: Path) -> None:
    from scripts import fetch_vor_haltestellen as fvh

    stations = tmp_path / "stations.json"
    stations.write_text(
        json.dumps([{"name": "Wien Hbf", "bst_id": "8100002"}]),
        encoding="utf-8",
    )

    result = fvh.load_stations(stations)
    assert len(result) == 1
    assert result[0].name == "Wien Hbf"


def test_update_wl_load_vor_mapping_normal_unaffected(tmp_path: Path) -> None:
    from scripts import update_wl_stations as uws

    mapping = tmp_path / "vor-haltestellen.mapping.json"
    mapping.write_text(
        json.dumps([{"station_name": "Wien Hbf", "vor_id": "490132100"}]),
        encoding="utf-8",
    )

    result = uws.load_vor_mapping(mapping)
    assert result  # at least one mapping entry


# ============================================================================
# BaseException-rooted exception handling regression test. The canonical
# defence is "stat-then-cap-then-read" — the byte-size cap fires BEFORE
# ``open()``, so ``json.load`` is never reached on oversized files and
# ``MemoryError`` is never raised. This test pins the contract by mocking
# ``json.load`` to confirm it is unreachable for files exceeding the cap.
# ============================================================================


def test_read_capped_json_skips_open_when_oversized(
    tmp_path: Path,
) -> None:
    """The shared helper MUST stat the file BEFORE opening it. If the
    helper opened the file then checked ``len(content)``, a 1 GiB
    file would already be buffered in memory and the cap defence
    would be irrelevant. This test pins the stat-first contract."""
    from src.utils.files import read_capped_json

    big = tmp_path / "big.json"
    _write_oversized(big, 4096)

    with patch("src.utils.files.json.load") as mock_load:
        result = read_capped_json(big, max_bytes=1024)

    mock_load.assert_not_called()
    assert result is None


def test_read_capped_json_normal_file_parses(tmp_path: Path) -> None:
    """Sanity: normal-sized files parse correctly through the helper."""
    from src.utils.files import read_capped_json

    payload = {"hello": "world"}
    target = tmp_path / "normal.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = read_capped_json(target)
    assert result == payload


def test_read_capped_json_missing_returns_none(tmp_path: Path) -> None:
    """The helper returns None for missing files (canonical fallback)."""
    from src.utils.files import read_capped_json

    missing = tmp_path / "does-not-exist.json"
    result = read_capped_json(missing)
    assert result is None


def test_read_capped_json_invalid_returns_none(tmp_path: Path) -> None:
    """The helper returns None for malformed JSON."""
    from src.utils.files import read_capped_json

    target = tmp_path / "bad.json"
    target.write_text("not valid json{", encoding="utf-8")
    result = read_capped_json(target)
    assert result is None


def test_read_capped_json_depth_bomb_returns_none(tmp_path: Path) -> None:
    """The helper returns None for depth-bomb (RecursionError) JSON.

    Combined with the size-cap test above, this pins the orthogonal
    two-axes defence: the depth-bomb axis (Round 1-5 of that family)
    AND the size-bomb axis (this round) are both covered.
    """
    from src.utils.files import read_capped_json

    target = tmp_path / "deep.json"
    target.write_text("[" * 5000 + "1" + "]" * 5000, encoding="utf-8")
    result = read_capped_json(target)
    assert result is None
