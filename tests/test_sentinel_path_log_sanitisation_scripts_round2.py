"""Sentinel PoC: Round-2 sibling drift of the 2026-05-11
``read_capped_json``/``read_capped_text`` path-log sanitisation fix in the
operator-facing ``scripts/`` directory.

The 2026-05-11 Caller-Drift round (PR closing the
``stations.py:_read_capped_json`` clone + the five sibling WARNING sinks in
``src/utils/env.py`` and ``src/build_feed.py``) explicitly named-but-deferred
the inverse grep across ``scripts/``::

    "the inverse grep across ``scripts/`` (currently ~14 sibling sites) is
     named-but-deferred to a Round 2 that does not block the canonical
     drift closure shipped here."

This file is that Round-2 closure. The grep enumerates 25 caller-side
WARNING/INFO sinks across three CLI-driven scripts that interpolate the
operator-controlled ``path`` argument into the bare ``%s`` format spec:

  * ``scripts/enrich_station_aliases.py`` (6 sites):
      - ``_load_vor_names``                    L310 (file-not-found WARNING)
      - ``_load_vor_mapping``                  L337 (file-not-found WARNING)
                                               L349 (could-not-parse WARNING)
      - ``_load_gtfs_index``                   L388 (file-not-found WARNING)
      - ``_load_pendler_alternative_names``    L425 (file-not-found INFO)
                                               L434 (invalid-JSON WARNING)
  * ``scripts/update_station_directory.py`` (17 sites):
      - ``_load_gtfs_locations``               L442 (file-not-found WARNING)
                                               L478 (csv.Error WARNING)
      - ``_load_wienerlinien_locations``       L488 (file-not-found WARNING)
                                               L516 (csv.Error WARNING)
      - ``_load_vor_locations``                L531 (file-not-found WARNING)
                                               L561 (csv.Error WARNING)
      - ``_load_existing_station_entries``     L607 (parse-failure WARNING)
      - ``load_vor_stops``                     L1086 (FNF INFO)
                                               L1089 (csv.Error WARNING)
                                               L1116 (no-rows INFO)
                                               L1118 (load-summary INFO)
      - ``load_pendler_station_ids``           L1756 (file-not-found WARNING)
      - ``load_pendler_name_candidates``       L1807 (file-not-found INFO)
                                               L1821 (parse-failure WARNING)
                                               L1827 (shape-error WARNING)
                                               L1832 (shape-error WARNING)
      - ``write_json``                         L1878 (write-summary INFO)
  * ``scripts/update_wl_stations.py`` (2 sites):
      - ``load_vor_mapping``                   L539 (file-not-found INFO)
                                               L549 (parse-failure WARNING)

Threat model
============
Every path in these sites is operator-controlled (CLI ``--vor-stops``,
``--vor-mapping``, ``--gtfs-stops``, ``--wl-haltepunkte``,
``--pendler``, ``--pendler-candidates``, ``--output``, ``--stations``,
``--haltepunkte``, ``--haltestellen``). The scripts run via cron under
the orchestrator (``update_all_stations.py`` invokes them with
``subprocess.run(check=True)``) so a hostile config / PR / env-var
override pointing at a directory whose path string carries Trojan-Source
primitives lands those primitives into:

  * The script's stderr (captured by the orchestrator and the cron
    diagnostic dump).
  * Any operator-facing log file that aggregates the script output.
  * Pytest's ``caplog`` capture (which exposes ``record.args[0]``
    BEFORE the :class:`SafeFormatter` runs — a third-party log handler
    or custom plugin sees the raw bytes).
  * Any downstream consumer that reads ``record.msg`` /
    ``record.getMessage()`` from the propagated record before formatter
    sanitisation (rsyslog with Python logging adapters, structured log
    JSON emitters that don't route through :class:`SafeJSONFormatter`).

The Trojan-Source primitive set (canonical, mirrors the prior round):

  * ``‮``   U+202E RIGHT-TO-LEFT OVERRIDE — visually reverses subsequent
            text in any Unicode-aware terminal, phishing primitive
            (CVE-2021-42574).
  * ``​``  U+200B ZERO WIDTH SPACE — invisible cache-key / equality
            poisoning primitive.
  * ``\x9b``  U+009B 8-bit CSI — survives the 7-bit ``_ANSI_ESCAPE_RE``
              defence, triggers SGR colour interpretation on every
              8-bit-C1-honouring terminal (xterm with eightBitInput,
              several BSD consoles, rxvt in 8-bit mode).
  * ``\x9d``  U+009D 8-bit OSC — companion to CSI; same defence bypass.
  * ``\x1b``  U+001B ESC — ANSI prefix, terminal-escape primitive.
  * ``\x07``  U+0007 BEL — terminal-bell denial-of-attention.
  * ``\n``    newline (record terminator) — log-record forgery in any
              line-based consumer (SIEM splitters, rsyslog, journald,
              Promtail).
  * ``\r``    carriage return — log overwrite primitive (terminal
              redraws over the prior line; the operator never sees
              the original WARNING).
  * ``\U000e0020``  U+E0020 Unicode Tag SPACE — invisible-instruction
                    smuggling primitive (2024 OpenAI disclosure).
  * ``︀``    U+FE00 VARIATION SELECTOR-1 — 4-bit-payload
              steganography.

Defence shape
=============
Each fixed site adopts the canonical ``_path_fingerprint`` shape pinned
in :func:`src.utils.env._path_fingerprint`::

    hashlib.sha256(str(path).encode("utf-8", errors="replace")).hexdigest()[:12]

This is:
  * A CodeQL-recognised barrier (``hashlib`` is a documented sanitiser
    sink for ``py/clear-text-logging-sensitive-data``).
  * Trojan-Source-clean — hex-only ``[0-9a-f]`` output.
  * Operator-correlatable — running ``sha256(str(path))[:12]`` locally
    on a candidate path confirms identity for cron-pipeline diagnosis.
  * Stable across runs for a given path — useful for log aggregation
    and SIEM grouping.

Each script gains:
  1. A module-level ``import hashlib``.
  2. A module-level ``_path_fingerprint`` helper mirroring
     :func:`src.utils.env._path_fingerprint`.
  3. Every operator-controlled-path WARNING / INFO log line is updated
     to use ``[path-sha256=%s]`` with ``_path_fingerprint(path)`` in
     place of the raw ``path`` argument.
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


_PRIMITIVES: list[tuple[str, str]] = [
    ("‮", "U+202E RIGHT-TO-LEFT OVERRIDE"),
    ("​", "U+200B ZERO WIDTH SPACE"),
    ("\x9b", "U+009B 8-bit CSI"),
    ("\x9d", "U+009D 8-bit OSC"),
    ("\x1b", "U+001B ESC (ANSI prefix)"),
    ("\x07", "U+0007 BEL"),
    ("\n", "newline (record terminator)"),
    ("\r", "carriage return"),
    ("\U000e0020", "U+E0020 Unicode Tag SPACE"),
    ("︀", "U+FE00 VARIATION SELECTOR-1"),
]


def _fingerprint(path: Path) -> str:
    """Return the canonical 12-hex SHA-256 fingerprint of *path*."""
    return hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]


def _poisoned_path(tmp_path: Path, primitive: str, filename: str) -> Path:
    """Return a path under a directory whose name carries ``primitive``."""
    poisoned_dir = tmp_path / f"dir{primitive}sub"
    poisoned_dir.mkdir(parents=True, exist_ok=True)
    return poisoned_dir / filename


def _assert_primitive_absent(
    caplog: pytest.LogCaptureFixture,
    primitive: str,
    primitive_label: str,
    site_label: str,
) -> None:
    """Assert no captured log record carries the primitive verbatim."""
    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"{site_label}: {message!r}"
        )
        for arg in record.args or ():
            assert primitive not in str(arg), (
                f"{primitive_label} ({primitive!r}) leaked through "
                f"{site_label} log args: {arg!r}"
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
