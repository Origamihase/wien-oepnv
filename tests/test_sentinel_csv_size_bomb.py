"""Sentinel PoC: Memory-exhaustion via unbounded ``csv.DictReader`` reads.

Threat model
------------
The JSON Size-Bomb rounds 1-7 closed the unbounded ``json.load`` and
``Path.read_text`` axes across ``src/`` and ``scripts/``. The auto-
discoverable closing grep ``git grep -nE 'csv\\.\\(DictReader\\|reader\\)'``
returned **ten open sites in five modules** that consume operator-
controlled CSV files via ``path.open("r", ...)`` -> ``csv.DictReader(handle)``
with NO byte-size cap whatsoever. ``csv.DictReader`` iterates the underlying
text file via ``iter(handle)`` -> ``handle.readline()`` which buffers the
input *up to the next newline or EOF* — a planted CSV with **no newlines**
and N MiB of payload allocates O(N) bytes BEFORE ``csv.reader`` ever
inspects a field, propagating ``MemoryError`` (a ``BaseException`` subclass
that is NOT caught by ``except (OSError, csv.Error, ValueError)``) past
the loader and crashing the cron pipeline.

Sites covered (cron-pipeline blast radius via
``scripts/update_all_stations.py:run_script(check=True)`` — a single
``MemoryError`` aborts the entire batch):

 1. ``src/utils/stations_validation.py:_load_gtfs_stop_ids`` —
    **HIGH**: CI gate. The validator runs as part of ``validate_stations.py``
    on every change to ``data/stations.json``. A planted huge GTFS
    stops.txt crashes the validator before any inconsistency is flagged.
 2. ``scripts/update_station_directory.py:_load_gtfs_locations`` —
    **HIGH**: cron pipeline. The orchestrator reads the GTFS path
    operator-controlled via CLI arg / config.
 3. ``scripts/update_station_directory.py:_load_wienerlinien_locations`` —
    **HIGH**: same blast radius as (2) for the WL haltepunkte CSV.
 4. ``scripts/update_station_directory.py:_load_vor_locations`` —
    **HIGH**: same blast radius for the VOR haltestellen CSV.
 5. ``scripts/update_station_directory.py:_iter_vor_rows`` —
    **HIGH**: separate VOR loader path used by ``load_vor_stops``.
 6. ``scripts/update_vor_stations.py:_dict_reader`` —
    **HIGH**: cron pipeline. Yields normalized VOR CSV rows.
 7. ``scripts/update_wl_stations.py:_dict_reader`` —
    **HIGH**: cron pipeline. Reads WL haltestellen / haltepunkte CSVs.
 8. ``scripts/enrich_station_aliases.py:_load_vor_names``
    (line 300 pre-fix) — **HIGH**: cron pipeline. Reads the
    semicolon-delimited VOR haltestellen CSV.
 9. ``scripts/enrich_station_aliases.py:_load_gtfs_index``
    (line 367 pre-fix) — **HIGH**: cron pipeline. Indexes GTFS names
    for alias merging.
10. ``scripts/gtfs.py:read_gtfs_stops`` — **MEDIUM**: test/utility module
    but exported via ``__all__`` so accessible to any import path.

Fix shape
---------
Identical to JSON Size-Bomb Round 7: replace ``path.open("r", ...)`` ->
``csv.DictReader(handle)`` with ``read_capped_text(path, MAX_*_BYTES,
encoding=..., label=..., logger=log)`` -> ``csv.DictReader(io.StringIO(
content), ...)``. ``read_capped_text`` is TOCTOU-safe (open-then-fstat)
and special-file-safe (``read(max_bytes + 1)`` guard for FIFOs /
``/dev/zero``). Each call site exposes its own per-loader cap constant
at module level so the auto-discoverable inventory test catches any
future loader added without the cap.

Closing grep (drift defence): re-run after every CSV-reader addition::

    git grep -nE 'csv\\.(DictReader|reader)' src/ scripts/ \\
      | grep -v 'StringIO\\|test_'

This grep MUST return zero hits other than tests. Any new hit is a
sibling site that needs the same canonical defence.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest


def _write_oversized_csv(path: Path, size_bytes: int) -> None:
    """Plant a CSV file with a payload exceeding the loader's byte cap.

    The pathological shape is "no newlines after header" — the data line
    consumes every byte until EOF in one ``readline()`` call, exhausting
    memory before any field is yielded. We include a header so the file
    is structurally a valid CSV from the loader's perspective; the
    planted attack only kicks in when the first data line is consumed.
    """
    prefix = "stop_id\n"
    path.write_text(prefix + "x" * size_bytes, encoding="utf-8")


# ============================================================================
# Precondition: per-loader cap constants are exposed at module level
# ============================================================================


def test_precondition_stations_validation_cap_exists() -> None:
    from src.utils import stations_validation

    assert isinstance(stations_validation.MAX_GTFS_STOPS_BYTES, int)
    assert stations_validation.MAX_GTFS_STOPS_BYTES > 0
    assert stations_validation.MAX_GTFS_STOPS_BYTES >= 1_000_000


def test_precondition_update_station_directory_cap_exists() -> None:
    import scripts.update_station_directory as usd

    assert isinstance(usd.MAX_CSV_LOCATIONS_BYTES, int)
    assert usd.MAX_CSV_LOCATIONS_BYTES > 0
    assert usd.MAX_CSV_LOCATIONS_BYTES >= 1_000_000


def test_precondition_update_wl_stations_cap_exists() -> None:
    import scripts.update_wl_stations as uws

    assert isinstance(uws.MAX_WL_CSV_BYTES, int)
    assert uws.MAX_WL_CSV_BYTES > 0
    assert uws.MAX_WL_CSV_BYTES >= 1_000_000


def test_precondition_enrich_station_aliases_cap_exists() -> None:
    import scripts.enrich_station_aliases as esa

    assert isinstance(esa.MAX_ALIAS_CSV_BYTES, int)
    assert esa.MAX_ALIAS_CSV_BYTES > 0
    assert esa.MAX_ALIAS_CSV_BYTES >= 1_000_000


def test_precondition_gtfs_cap_exists() -> None:
    import scripts.gtfs as gtfs_mod

    assert isinstance(gtfs_mod.MAX_GTFS_STOPS_BYTES, int)
    assert gtfs_mod.MAX_GTFS_STOPS_BYTES > 0
    assert gtfs_mod.MAX_GTFS_STOPS_BYTES >= 1_000_000


# ============================================================================
# Auto-discoverable closing grep — every csv.DictReader / csv.reader site
# must route through ``io.StringIO`` (i.e. text loaded via
# ``read_capped_text``) NOT a raw file handle.
# ============================================================================


def test_no_unbounded_csv_dictreader_in_src_or_scripts() -> None:
    """Drift defence: every csv reader must consume capped text.

    Walk ``src/`` and ``scripts/`` and assert every ``csv.DictReader``
    or ``csv.reader`` callsite is constructed from an in-memory
    ``StringIO`` wrapper (i.e. text loaded via ``read_capped_text``)
    rather than a raw file handle from ``path.open(...)``.
    """
    repo_root = Path(__file__).resolve().parents[1]
    offenders: list[tuple[Path, int, str]] = []
    for sub in ("src", "scripts"):
        for py_file in (repo_root / sub).rglob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            try:
                lines = py_file.read_text(encoding="utf-8").splitlines()
            except OSError:  # pragma: no cover - defensive
                continue
            for idx, line in enumerate(lines, start=1):
                stripped = line.lstrip()
                # Skip comment / docstring-style narration lines so the
                # grep only flags actual constructor calls.
                if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                    continue
                if "csv.DictReader(" not in line and "csv.reader(" not in line:
                    continue
                # The constructor MUST receive a StringIO buffer (loaded
                # via read_capped_text). Raw ``handle`` from
                # ``path.open(...)`` is the unbounded shape.
                if "StringIO" not in line:
                    offenders.append((py_file.relative_to(repo_root), idx, line.strip()))
    assert not offenders, (
        "Unbounded csv reader sites detected — wrap text via read_capped_text "
        "+ io.StringIO before constructing csv.DictReader / csv.reader:\n"
        + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
    )


# ============================================================================
# src/utils/stations_validation.py — _load_gtfs_stop_ids
# ============================================================================


def test_load_gtfs_stop_ids_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``path.open("r")`` -> ``csv.DictReader(handle)`` reads via
    ``iter(handle)`` -> ``readline()`` which buffers until newline/EOF.
    A planted huge file with no newlines exhausts memory.

    Post-fix: ``read_capped_text`` returns ``None`` and the loader
    returns an empty (set, 0) tuple.
    """
    from src.utils import stations_validation

    csv_path = tmp_path / "stops.txt"
    monkeypatch.setattr(stations_validation, "MAX_GTFS_STOPS_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    stop_ids, count = stations_validation._load_gtfs_stop_ids(csv_path)
    assert stop_ids == set()
    assert count == 0


def test_load_gtfs_stop_ids_accepts_within_cap(tmp_path: Path) -> None:
    """Within-cap reads still succeed and yield expected rows."""
    from src.utils import stations_validation

    csv_path = tmp_path / "stops.txt"
    csv_path.write_text(
        "stop_id,stop_name\n"
        "S1,Wien Westbahnhof\n"
        "S2,Wien Mitte\n",
        encoding="utf-8-sig",
    )

    stop_ids, count = stations_validation._load_gtfs_stop_ids(csv_path)
    assert stop_ids == {"S1", "S2"}
    assert count == 2


# ============================================================================
# scripts/gtfs.py — read_gtfs_stops
# ============================================================================


def test_read_gtfs_stops_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Module-exported helper must also enforce the cap."""
    from scripts import gtfs as gtfs_mod

    csv_path = tmp_path / "stops.txt"
    monkeypatch.setattr(gtfs_mod, "MAX_GTFS_STOPS_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    with pytest.raises(ValueError):
        gtfs_mod.read_gtfs_stops(csv_path)


# ============================================================================
# scripts/update_station_directory.py — four CSV loader sites
# ============================================================================


def test_load_gtfs_locations_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.update_station_directory as usd

    csv_path = tmp_path / "stops.txt"
    monkeypatch.setattr(usd, "MAX_CSV_LOCATIONS_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    locations = usd._load_gtfs_locations(csv_path)
    assert locations == {}


def test_load_wienerlinien_locations_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.update_station_directory as usd

    csv_path = tmp_path / "haltepunkte.csv"
    monkeypatch.setattr(usd, "MAX_CSV_LOCATIONS_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    locations = usd._load_wienerlinien_locations(csv_path)
    assert locations == {}


def test_load_vor_locations_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.update_station_directory as usd

    csv_path = tmp_path / "vor.csv"
    monkeypatch.setattr(usd, "MAX_CSV_LOCATIONS_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    locations = usd._load_vor_locations(csv_path)
    assert locations == {}


def test_load_vor_stops_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``load_vor_stops`` consumes ``_iter_vor_rows`` — both must be
    capped end-to-end."""
    import scripts.update_station_directory as usd

    csv_path = tmp_path / "vor-stops.csv"
    monkeypatch.setattr(usd, "MAX_CSV_LOCATIONS_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    stops = usd.load_vor_stops(csv_path)
    assert stops == []


# ============================================================================
# scripts/update_wl_stations.py — _dict_reader
# ============================================================================


def test_update_wl_stations_dict_reader_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.update_wl_stations as uws

    csv_path = tmp_path / "wl.csv"
    monkeypatch.setattr(uws, "MAX_WL_CSV_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    rows = list(uws._dict_reader(csv_path))
    assert rows == []


# ============================================================================
# scripts/enrich_station_aliases.py — two CSV loader sites
# ============================================================================


def test_enrich_load_vor_names_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.enrich_station_aliases as esa

    csv_path = tmp_path / "vor.csv"
    monkeypatch.setattr(esa, "MAX_ALIAS_CSV_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    mapping = esa._load_vor_names(csv_path)
    assert mapping == {}


def test_enrich_load_gtfs_index_rejects_oversized_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.enrich_station_aliases as esa

    csv_path = tmp_path / "stops.txt"
    monkeypatch.setattr(esa, "MAX_ALIAS_CSV_BYTES", 1024)
    _write_oversized_csv(csv_path, 4096)

    index = esa._load_gtfs_index(csv_path)
    assert index == {}


# ============================================================================
# Sanity test — io.StringIO + csv.DictReader yields rows correctly
# ============================================================================


def test_stringio_csv_dictreader_basic() -> None:
    """Sanity check: ``csv.DictReader(io.StringIO(text))`` is the
    canonical fix shape used everywhere."""
    import csv

    text = "stop_id,stop_name\nA,Foo\nB,Bar\n"
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    assert rows == [
        {"stop_id": "A", "stop_name": "Foo"},
        {"stop_id": "B", "stop_name": "Bar"},
    ]
