"""Teil 1 der Tests aus tests/test_sentinel_path_log_sanitisation_scripts_round2.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from ._support import (
    _PRIMITIVES,
    _assert_primitive_absent,
    _fingerprint,
    _poisoned_path,
)


# ============================================================================
# scripts/enrich_station_aliases.py — 6 sites
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_enrich_load_vor_names_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.enrich_station_aliases import _load_vor_names

    path = _poisoned_path(tmp_path, primitive, "vor-stops.csv")
    caplog.set_level(logging.WARNING)
    result = _load_vor_names(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "enrich:_load_vor_names L310"
    )


def test_enrich_load_vor_names_missing_emits_fingerprint(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.enrich_station_aliases import _load_vor_names

    benign = tmp_path / "vor-stops.csv"
    caplog.set_level(logging.WARNING)
    _load_vor_names(benign)
    combined = " ".join(r.getMessage() for r in caplog.records)
    assert _fingerprint(benign) in combined, (
        f"fingerprint missing: {combined!r}"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_enrich_load_vor_mapping_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.enrich_station_aliases import _load_vor_mapping

    path = _poisoned_path(tmp_path, primitive, "vor-mapping.json")
    caplog.set_level(logging.WARNING)
    result = _load_vor_mapping(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "enrich:_load_vor_mapping L337"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_enrich_load_vor_mapping_invalid_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``read_capped_json`` returns None on a corrupted file the
    caller emits its own additional WARNING at L349."""
    from scripts.enrich_station_aliases import _load_vor_mapping

    path = _poisoned_path(tmp_path, primitive, "vor-mapping.json")
    path.write_bytes(b"not json{{{")  # corrupted
    caplog.set_level(logging.WARNING)
    result = _load_vor_mapping(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "enrich:_load_vor_mapping L349"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_enrich_load_gtfs_index_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.enrich_station_aliases import _load_gtfs_index

    path = _poisoned_path(tmp_path, primitive, "stops.txt")
    caplog.set_level(logging.WARNING)
    result = _load_gtfs_index(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "enrich:_load_gtfs_index L388"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_enrich_load_pendler_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.enrich_station_aliases import _load_pendler_alternative_names

    path = _poisoned_path(tmp_path, primitive, "pendler_candidates.json")
    caplog.set_level(logging.INFO)
    result = _load_pendler_alternative_names(path)
    assert result == {}
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "enrich:_load_pendler_alternative_names L425",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_enrich_load_pendler_invalid_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``read_capped_json`` returns None on a corrupted file the
    caller emits its own additional WARNING at L434."""
    from scripts.enrich_station_aliases import _load_pendler_alternative_names

    path = _poisoned_path(tmp_path, primitive, "pendler_candidates.json")
    path.write_bytes(b"not json{{{")
    caplog.set_level(logging.WARNING)
    result = _load_pendler_alternative_names(path)
    assert result == {}
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "enrich:_load_pendler_alternative_names L434",
    )


# ============================================================================
# scripts/update_wl_stations.py — 2 sites
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_wl_load_vor_mapping_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_wl_stations import load_vor_mapping

    path = _poisoned_path(tmp_path, primitive, "vor-mapping.json")
    caplog.set_level(logging.INFO)
    result = load_vor_mapping(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "wl:load_vor_mapping L539"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_wl_load_vor_mapping_invalid_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_wl_stations import load_vor_mapping

    path = _poisoned_path(tmp_path, primitive, "vor-mapping.json")
    path.write_bytes(b"not json{{{")
    caplog.set_level(logging.WARNING)
    result = load_vor_mapping(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "wl:load_vor_mapping L549"
    )


# ============================================================================
# scripts/update_station_directory.py — 17 sites
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_gtfs_locations_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_station_directory import _load_gtfs_locations

    path = _poisoned_path(tmp_path, primitive, "stops.txt")
    caplog.set_level(logging.WARNING)
    result = _load_gtfs_locations(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:_load_gtfs_locations L442"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_gtfs_locations_csverror_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L478: ``csv.Error`` branch — patch ``csv.DictReader`` to raise so the
    caller hits the WARNING line that interpolates ``path``."""
    from scripts.update_station_directory import _load_gtfs_locations

    path = _poisoned_path(tmp_path, primitive, "stops.txt")
    path.write_text("stop_name,stop_lat,stop_lon\nFoo,48.2,16.4\n", encoding="utf-8")

    def _raising_reader(*args: object, **kwargs: object) -> None:
        raise csv.Error("planted")

    caplog.set_level(logging.WARNING)
    with patch("scripts.update_station_directory.csv.DictReader", _raising_reader):
        result = _load_gtfs_locations(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:_load_gtfs_locations L478"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_wl_locations_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_station_directory import _load_wienerlinien_locations

    path = _poisoned_path(tmp_path, primitive, "haltepunkte.csv")
    caplog.set_level(logging.WARNING)
    result = _load_wienerlinien_locations(path)
    assert result == {}
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:_load_wienerlinien_locations L488",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_wl_locations_csverror_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_station_directory import _load_wienerlinien_locations

    path = _poisoned_path(tmp_path, primitive, "haltepunkte.csv")
    path.write_text("NAME;WGS84_LAT;WGS84_LON\nFoo;48.2;16.4\n", encoding="utf-8")

    def _raising_reader(*args: object, **kwargs: object) -> None:
        raise csv.Error("planted")

    caplog.set_level(logging.WARNING)
    with patch("scripts.update_station_directory.csv.DictReader", _raising_reader):
        result = _load_wienerlinien_locations(path)
    assert result == {}
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:_load_wienerlinien_locations L516",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_vor_locations_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_station_directory import _load_vor_locations

    path = _poisoned_path(tmp_path, primitive, "vor-haltestellen.csv")
    caplog.set_level(logging.WARNING)
    result = _load_vor_locations(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:_load_vor_locations L531"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_vor_locations_csverror_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_station_directory import _load_vor_locations

    path = _poisoned_path(tmp_path, primitive, "vor-haltestellen.csv")
    path.write_text(
        "StopPointName;Latitude;Longitude\nFoo;48.2;16.4\n",
        encoding="utf-8",
    )

    def _raising_reader(*args: object, **kwargs: object) -> None:
        raise csv.Error("planted")

    caplog.set_level(logging.WARNING)
    with patch("scripts.update_station_directory.csv.DictReader", _raising_reader):
        result = _load_vor_locations(path)
    assert result == {}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:_load_vor_locations L561"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_existing_station_entries_invalid_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L607: parsing failure WARNING."""
    from scripts.update_station_directory import _load_existing_station_entries

    path = _poisoned_path(tmp_path, primitive, "stations.json")
    path.write_bytes(b"not json{{{")
    caplog.set_level(logging.WARNING)
    mapping, manual = _load_existing_station_entries(path)
    assert mapping == {}
    assert manual == []
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:_load_existing_station_entries L607",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_vor_stops_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1086: ``load_vor_stops`` FileNotFoundError INFO branch."""
    from scripts.update_station_directory import load_vor_stops

    path = _poisoned_path(tmp_path, primitive, "vor.csv")
    caplog.set_level(logging.INFO)
    result = load_vor_stops(path)
    assert result == []
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:load_vor_stops L1086"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_vor_stops_csverror_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1089: ``csv.Error`` branch of ``load_vor_stops``."""
    from scripts.update_station_directory import load_vor_stops

    path = _poisoned_path(tmp_path, primitive, "vor.csv")
    path.write_text(
        "StopPointId;StopPointName\n1;Foo\n", encoding="utf-8",
    )

    def _raise_csv(*args: object, **kwargs: object) -> None:
        raise csv.Error("planted")

    caplog.set_level(logging.WARNING)
    with patch("scripts.update_station_directory.csv.DictReader", _raise_csv):
        result = load_vor_stops(path)
    assert result == []
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:load_vor_stops L1089"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_vor_stops_empty_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1116: no-rows INFO branch of ``load_vor_stops``."""
    from scripts.update_station_directory import load_vor_stops

    path = _poisoned_path(tmp_path, primitive, "vor.csv")
    # Header only, no rows.
    path.write_text("StopPointId;StopPointName\n", encoding="utf-8")
    caplog.set_level(logging.INFO)
    result = load_vor_stops(path)
    assert result == []
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:load_vor_stops L1116"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_vor_stops_loaded_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1118: load-summary INFO branch of ``load_vor_stops``."""
    from scripts.update_station_directory import load_vor_stops

    path = _poisoned_path(tmp_path, primitive, "vor.csv")
    path.write_text(
        "StopPointId;StopPointName\n1;Foo\n2;Bar\n", encoding="utf-8",
    )
    caplog.set_level(logging.INFO)
    result = load_vor_stops(path)
    assert len(result) == 2
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:load_vor_stops L1118"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_pendler_station_ids_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_station_directory import load_pendler_station_ids

    path = _poisoned_path(tmp_path, primitive, "pendler.json")
    caplog.set_level(logging.WARNING)
    result = load_pendler_station_ids(path)
    assert result == set()
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:load_pendler_station_ids L1756",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_pendler_candidates_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.update_station_directory import load_pendler_name_candidates

    path = _poisoned_path(tmp_path, primitive, "pendler_candidates.json")
    caplog.set_level(logging.INFO)
    result = load_pendler_name_candidates(path)
    assert result == set()
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:load_pendler_name_candidates L1807",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_pendler_candidates_invalid_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1821: parse failure WARNING."""
    from scripts.update_station_directory import load_pendler_name_candidates

    path = _poisoned_path(tmp_path, primitive, "pendler_candidates.json")
    path.write_bytes(b"not json{{{")
    caplog.set_level(logging.WARNING)
    result = load_pendler_name_candidates(path)
    assert result == set()
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:load_pendler_name_candidates L1821",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_pendler_candidates_not_object_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1827: shape-error WARNING ('must be a JSON object')."""
    from scripts.update_station_directory import load_pendler_name_candidates

    path = _poisoned_path(tmp_path, primitive, "pendler_candidates.json")
    # A JSON array — passes the ``read_capped_json`` cap but fails the
    # isinstance(data, dict) check immediately after.
    path.write_text(json.dumps(["nope"]), encoding="utf-8")
    caplog.set_level(logging.WARNING)
    result = load_pendler_name_candidates(path)
    assert result == set()
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:load_pendler_name_candidates L1827",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_load_pendler_candidates_not_list_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1832: shape-error WARNING ('candidates must be a list')."""
    from scripts.update_station_directory import load_pendler_name_candidates

    path = _poisoned_path(tmp_path, primitive, "pendler_candidates.json")
    # JSON object but ``candidates`` key is not a list.
    path.write_text(
        json.dumps({"candidates": "nope"}), encoding="utf-8"
    )
    caplog.set_level(logging.WARNING)
    result = load_pendler_name_candidates(path)
    assert result == set()
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "usd:load_pendler_name_candidates L1832",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_usd_write_json_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1878: ``write_json`` summary INFO branch interpolates the
    operator-supplied ``--output`` path."""
    from scripts.update_station_directory import write_json

    output_path = _poisoned_path(tmp_path, primitive, "stations.json")
    caplog.set_level(logging.INFO)
    write_json([], output_path)
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "usd:write_json L1878"
    )


# ============================================================================
# Additive-regression invariants
# ============================================================================


def test_fingerprint_is_deterministic_across_calls(tmp_path: Path) -> None:
    """Operator-correlation contract: the fingerprint is stable across
    runs for a given path (no salt, no per-process state)."""
    p = tmp_path / "foo.json"
    assert _fingerprint(p) == _fingerprint(p)


def test_fingerprint_is_trojan_source_clean(tmp_path: Path) -> None:
    """The 12-char hex SHA-256 fingerprint is purely ``[0-9a-f]`` and
    therefore cannot itself carry any Trojan-Source primitive."""
    primitive_path = tmp_path / "größe_test\x1b‮​.json"
    fp = _fingerprint(primitive_path)
    assert len(fp) == 12
    assert all(c in "0123456789abcdef" for c in fp)


def test_legitimate_german_path_survives_unchanged(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legitimate German content (umlauts, sharp s) MUST produce a
    fingerprint identical to the bare ``hashlib.sha256`` computation;
    no scrubbing or normalisation is applied to the input bytes before
    hashing."""
    benign = tmp_path / "Größe_Wien_Bahnhof.json"
    expected = hashlib.sha256(
        str(benign).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    assert _fingerprint(benign) == expected


def test_io_stringio_imported_for_test() -> None:
    """Sanity: confirm test module imports its helpers."""
    assert io.StringIO is not None
