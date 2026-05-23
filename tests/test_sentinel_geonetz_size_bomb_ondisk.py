"""Sentinel PoC: JSON size-bomb / non-finite-literal defence for the
GeoNetz on-disk loaders.

Threat model
------------
The 2026-05-08 round of JSON size-bomb defences canonicalised the
``read_capped_json`` helper (open + ``os.fstat`` on the open fd, byte
cap, ``parse_constant`` + ``parse_float`` non-finite literal rejection)
across every on-disk loader in ``src/`` and ``scripts/`` — except for
the GeoNetz pair audited here. Closing-checklist drift: the canonical
``json.loads(path.read_text(encoding="utf-8"))`` shape survived in two
sibling sites because neither was named in the original round's
file-by-file inventory:

  * ``scripts/update_station_directory.py:_load_geonetz_stops`` reads
    ``data/oebb_geonetz_stops.json`` on every cron tick (the weekly
    station refresh) and feeds the result into ``_enrich_with_geonetz``.
    A planted huge file at this path (compromised CI runner / hostile
    PR / corrupted previous run / partial flush + power loss) is buffered
    via ``Path.read_text()`` (allocating O(file_size) bytes before any
    surrounding ``except Exception`` handler can run). ``MemoryError``
    is a ``BaseException`` subclass — it is NOT caught by the broad
    ``except Exception:`` clause already present — so the unhandled
    exception escapes the loader and crashes the entire weekly cron
    tick (the orchestrator runs every update script via
    ``subprocess.run(check=True)``).

  * ``scripts/extract_oebb_geonetz_stops.py`` parses a fresh GeoNetz
    snapshot via ``json.loads(raw_bytes)`` without the canonical
    ``parse_constant`` / ``parse_float`` hooks. A planted ``STP_LAT:
    NaN`` / ``STP_LON: 1e1000`` literal in the upstream raw payload
    (compromised CDN / DNS hijack / MITM on the ÖBB-Infrastruktur
    fetch) propagates through ``_coerce_float`` (which only checks
    ``isinstance(value, (int, float))`` — ``float('nan')`` IS a float)
    into ``round(NaN, 6)`` (returns NaN), then through ``json.dumps``
    (which defaults to ``allow_nan=True``, emitting non-standard ``NaN``
    /``Infinity`` literals invalid per RFC 8259) into the committed
    ``data/oebb_geonetz_stops.json`` sidecar. The next cron tick then
    reads back the poisoned file via the size-bomb path above.

Both sites are closed by mirroring the canonical loader pattern pinned
at the sibling :func:`_load_existing_station_entries` /
:func:`load_pendler_station_ids` callers in the same module
(``read_capped_json`` + ``MAX_JSON_FILE_BYTES`` cap + ``loads_finite``
non-finite rejection).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ============================================================================
# Precondition: the canonical cap constants exist
# ============================================================================


def test_precondition_geonetz_size_cap_constants_exist() -> None:
    """Pin the canonical cap constants. If a future refactor renames or
    removes them, every regression test below would silently pass even on
    unfixed code — so we pin the precondition first."""
    from scripts import update_station_directory as usd

    assert isinstance(usd.MAX_JSON_FILE_BYTES, int)
    assert usd.MAX_JSON_FILE_BYTES > 0
    # Cap must accommodate the largest legitimate on-disk file observed in
    # production. The committed ``data/oebb_geonetz_stops.json`` is ~234 KiB
    # today; the cap is 50 MiB which is ~218x headroom.
    assert usd.MAX_JSON_FILE_BYTES >= 1_000_000


# ============================================================================
# scripts/update_station_directory.py — _load_geonetz_stops
# ============================================================================


def _write_oversized_json(path: Path, size_bytes: int) -> None:
    """Write a flat-but-huge JSON list that exceeds the loader's byte cap.

    The payload shape ``[0,0,0,…]`` is intentional: it is BOTH a valid
    JSON document (so ``json.loads`` would succeed if it ran) AND wide
    enough to consume memory proportional to the file size. Pre-fix the
    loader buffers the whole file via ``path.read_text()`` and consumes
    O(file_size) memory; post-fix the size cap rejects the file before
    opening.
    """
    # Wrap the flat list inside an envelope that satisfies the loader's
    # shape contract — the loader expects a top-level dict with a ``stops``
    # array. Padding with a sequence of dict-shaped entries keeps the
    # payload >= ``size_bytes`` while remaining a syntactically valid
    # GeoNetz-shaped document.
    fill_count = max(1, size_bytes // 40)
    entries = ",".join('{"bsts_id":"x","name":"x"}' for _ in range(fill_count))
    path.write_text('{"stops":[' + entries + "]}", encoding="utf-8")


def test_load_geonetz_stops_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-fix: ``_load_geonetz_stops`` used ``path.read_text(encoding="utf-8")``
    + ``json.loads(...)`` with NO byte-size cap. A planted huge file at
    ``data/oebb_geonetz_stops.json`` (compromised CI runner / hostile PR /
    corrupted previous run / partial flush + power loss) buffered into
    memory via ``Path.read_text()`` allocates O(file_size) bytes and
    raises ``MemoryError`` (a ``BaseException`` subclass NOT caught by
    the surrounding ``except Exception:``) past the loader and crashes
    the weekly station refresh cron pipeline.

    Post-fix: ``read_capped_json`` enforces both the byte-size cap and
    the depth-bomb catch tuple, so the oversized file is treated as
    missing and the loader returns the canonical empty dict.
    """
    from scripts import update_station_directory as usd

    # Tighten the cap so we don't have to write a multi-MiB test fixture.
    # The fix shape uses the module-level ``MAX_JSON_FILE_BYTES`` binding,
    # so monkeypatching it is sufficient to exercise the cap path.
    monkeypatch.setattr(usd, "MAX_JSON_FILE_BYTES", 1024, raising=False)

    target = tmp_path / "oebb_geonetz_stops.json"
    _write_oversized_json(target, 4096)
    assert target.stat().st_size > 1024

    caplog.set_level(logging.WARNING, logger="scripts.update_station_directory")
    result = usd._load_geonetz_stops(target)

    # Post-fix contract: the loader returns the canonical empty dict
    # rather than buffering the file and crashing the cron pipeline.
    assert result == {}


def test_load_geonetz_stops_rejects_non_finite_literals(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-fix: ``json.loads`` accepted ``NaN`` / ``Infinity`` /
    ``-Infinity`` literal tokens (invalid per RFC 8259 §6) under Python's
    default lenient settings. A planted non-finite literal in the
    committed ``data/oebb_geonetz_stops.json`` (corrupted previous run /
    compromised CI / hostile PR) would propagate as ``float('nan')`` /
    ``float('inf')`` past the ``isinstance(value, str)`` shape guard on
    ``bsts_id`` and into downstream consumers that compare floats with
    ``==``/``!=`` (silent dedup invariant breakage: ``nan != nan`` is
    True).

    Post-fix: ``read_capped_json`` pins ``parse_constant`` +
    ``parse_float`` to reject the non-finite literal family at parse
    time so the loader returns the canonical empty dict.
    """
    from scripts import update_station_directory as usd

    target = tmp_path / "oebb_geonetz_stops.json"
    # Write a syntactically-valid GeoNetz-shaped document carrying a
    # ``NaN`` literal in a coordinate field. Python's default
    # ``json.loads`` accepts the bare ``NaN`` token (it is a
    # ``parse_constant`` callback target); the fix shape pins
    # ``parse_constant=_reject_non_finite_constant`` so the literal
    # raises ``json.JSONDecodeError`` and the loader returns ``{}``.
    target.write_text(
        '{"stops":[{"bsts_id":"X","name":"Test","lat":NaN,"lon":0.0}]}',
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING, logger="scripts.update_station_directory")
    result = usd._load_geonetz_stops(target)

    # Post-fix: the planted ``NaN`` literal is rejected at parse time;
    # the loader returns the canonical empty dict.
    assert result == {}


def test_load_geonetz_stops_normal_file_unaffected(
    tmp_path: Path,
) -> None:
    """Sanity check: the size cap and non-finite rejection must not affect
    legitimate GeoNetz files. Pinned alongside the rejection tests so a
    future tightening that accidentally rejects valid state fails loudly."""
    from scripts import update_station_directory as usd

    target = tmp_path / "oebb_geonetz_stops.json"
    payload = {
        "stops": [
            {
                "bsts_id": "1234",
                "name": "Test Station",
                "lat": 48.2082,
                "lon": 16.3738,
            }
        ]
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = usd._load_geonetz_stops(target)
    assert "1234" in result
    assert result["1234"]["name"] == "Test Station"


# ============================================================================
# scripts/extract_oebb_geonetz_stops.py — non-finite literal rejection
# ============================================================================


def test_extract_coerce_float_rejects_non_finite() -> None:
    """Pre-fix: ``_coerce_float`` returned ``float('nan')`` / ``float('inf')``
    unchanged because the ``isinstance(value, (int, float))`` guard
    matches every Python float (finite or not). The non-finite value
    then propagated through ``round(NaN, 6)`` (returns NaN) into the
    committed ``data/oebb_geonetz_stops.json`` as a non-standard ``NaN``
    literal (invalid per RFC 8259).

    Post-fix: ``math.isfinite`` filter rejects ``NaN`` / ``±Inf`` so the
    coordinate field is silently dropped rather than committed verbatim.
    """
    from scripts.extract_oebb_geonetz_stops import _coerce_float

    assert _coerce_float(float("nan")) is None
    assert _coerce_float(float("inf")) is None
    assert _coerce_float(float("-inf")) is None
    # Finite values pass through unchanged.
    assert _coerce_float(48.2082) == 48.2082
    assert _coerce_float(0) == 0.0
    assert _coerce_float(-90.0) == -90.0


def test_extract_writer_pins_allow_nan_false() -> None:
    """The writer must pin ``allow_nan=False`` so a future bypass of the
    parser-side / coercer-side defences cannot land non-standard
    ``NaN`` / ``Infinity`` literals into the committed sidecar. Mirrors
    the canonical writer-side pin established at
    :func:`src.places.merge.write_stations` (Round 1485) and
    :func:`src.utils.cache.write_cache` (Round 1487).
    """
    import scripts.extract_oebb_geonetz_stops as eog

    # Inspect the writer's source to verify the pin is in place — a
    # source-level assertion catches both the regression and any future
    # refactor that drops the writer pin.
    import inspect

    source = inspect.getsource(eog.main)
    assert "allow_nan=False" in source, (
        "Writer must pin allow_nan=False — pre-fix shape silently emits "
        "non-standard NaN / Infinity literals into committed JSON."
    )


def test_extract_loads_finite_rejects_planted_nan(tmp_path: Path) -> None:
    """Pre-fix: ``json.loads(raw_bytes)`` accepted ``NaN`` / ``Infinity``
    literal tokens. The fix routes through ``loads_finite`` (which pins
    both ``parse_constant`` and ``parse_float`` hooks) so a planted
    upstream payload carrying a non-finite literal raises
    ``json.JSONDecodeError`` and the script exits cleanly.
    """
    from scripts.extract_oebb_geonetz_stops import extract

    target = tmp_path / "raw.json"
    target.write_text(
        '{"features":[{"properties":{"STP_ID":"x","BSTS_ID":1,'
        '"STP_NAME":"Test","STP_LAT":NaN,"STP_LON":0.0}}]}',
        encoding="utf-8",
    )

    # The post-fix path raises ``ValueError`` ("unparseable as JSON"); we
    # verify that path rather than letting the NaN propagate silently.
    with pytest.raises(ValueError):
        extract(target, "https://example.com")


# ============================================================================
# Static-source invariants — catch a future refactor that re-introduces
# the unbounded ``path.read_text()`` shape
# ============================================================================


def test_load_geonetz_stops_routes_through_read_capped_json() -> None:
    """Pin the fix-shape invariant via the source itself. A future
    refactor that replaces ``read_capped_json`` with a bare
    ``json.loads(path.read_text(...))`` would silently regress the
    MemoryError defence; this test fails loudly on that drift.

    Uses :mod:`ast` to inspect the function body (excluding the
    docstring) so the test does not collide with the post-fix
    docstring's narrative reference to the pre-fix unbounded shape.
    """
    import ast
    import inspect

    from scripts.update_station_directory import _load_geonetz_stops

    source = inspect.getsource(_load_geonetz_stops)
    module = ast.parse(source)
    func_def = module.body[0]
    assert isinstance(func_def, ast.FunctionDef)

    # Walk the AST body (NOT the docstring) and collect every
    # function-call name. The canonical loader must invoke
    # ``read_capped_json`` and must NOT invoke a bare ``path.read_text``.
    call_names: set[str] = set()
    for node in ast.walk(func_def):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                call_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                call_names.add(func.attr)

    assert "read_capped_json" in call_names, (
        "_load_geonetz_stops must route through read_capped_json — "
        "the canonical defence helper enforces both the byte-size cap "
        "and the non-finite literal rejection."
    )
    assert "read_text" not in call_names, (
        "_load_geonetz_stops must NOT call .read_text() — the "
        "unbounded shape propagates MemoryError past the cron orchestrator."
    )
    assert "loads" not in call_names or "json" not in {
        getattr(node.func.value, "id", "")
        for node in ast.walk(func_def)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }, (
        "_load_geonetz_stops must NOT call json.loads directly — the "
        "canonical defence helper routes through read_capped_json which "
        "pins parse_constant / parse_float non-finite literal rejection."
    )


def test_extract_oebb_geonetz_stops_routes_through_loads_finite() -> None:
    """Pin the fix-shape invariant for the extractor script: the parser
    site must route through ``loads_finite`` (or its underlying
    ``parse_constant`` / ``parse_float`` hooks) so a future contributor
    cannot accidentally restore the lenient ``json.loads(...)`` shape."""
    import inspect

    import scripts.extract_oebb_geonetz_stops as eog

    source = inspect.getsource(eog.extract)
    assert "loads_finite" in source, (
        "extract() must route the parser through loads_finite so "
        "non-finite literal tokens are rejected at the parse boundary."
    )
