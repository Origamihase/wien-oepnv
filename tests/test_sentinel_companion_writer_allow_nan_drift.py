"""Sentinel PoC: ``allow_nan=False`` writer-defence drift across the
five companion ``data/stations.json`` writers, the cache-sidecar writer,
and the Baustellen coordinate parser.

The 2026-05-14 coordinate-finite/range round (PR #1485, commit
``31570fe``) pinned ``allow_nan=False`` on
:func:`src.places.merge.write_stations` so a future bypass of the
parser-level finite floor at :class:`src.places.hafas_client` /
:class:`src.places.client.GooglePlacesClient` would surface as a loud
:class:`ValueError` at write time rather than silently corrupting the
committed ``data/stations.json`` artefact with non-standard
``NaN`` / ``Infinity`` / ``-Infinity`` JSON literals (invalid per
RFC 8259, rejected by every strict downstream consumer:
``JSON.parse`` in every modern browser, ``serde_json`` in Rust,
``encoding/json`` in Go).

The drift this round closes: **five additional writer sites all write to
the same on-disk artefact (``data/stations.json``) but only the
canonical** ``src.places.merge.write_stations`` **had the writer-defence
pin.** Plus the cache writer (``cache/<provider>/events.json``) which
the Baustellen feed lands coordinates into.

Sites enumerated
================

(A) ``data/stations.json`` writers — committed to ``main`` by the
    weekly ``update-stations.yml`` cron and the operator-only
    ``update-google-places-stations.yml`` workflow:

      * :func:`scripts.update_station_directory.write_json` — the
        primary OEBB ``Verzeichnis der Verkehrsstationen`` Excel
        writer.  The companion CSV / GTFS / VOR parsers in this
        script (:func:`_coerce_float_value` at line 455) accept
        ``float('nan')`` / ``float('inf')`` because Python's
        ``float()`` constructor accepts the literal strings ``"nan"``,
        ``"inf"``, ``"infinity"`` etc. and the ``isinstance(value,
        int | float)`` shape check on JSON-decoded numeric values
        accepts the non-finite literals that ``json.loads`` parses
        from a compromised upstream JSON.

      * :func:`scripts.enrich_station_aliases._write_stations_payload`
        — the alias-enrichment writer that re-reads ``data/stations.json``
        from the previous cron step and writes it back after appending
        aliases from VOR / GTFS / pendler-alternative-names sources.
        A poisoned coordinate from an upstream step propagates
        verbatim because the alias enricher never touches
        ``latitude`` / ``longitude``.

      * :func:`scripts.update_all_stations._write_stations_payload` —
        the orchestrator that copies ``data/stations.json`` into a
        temp directory, runs every sub-script in sequence against the
        temp file, and atomically copies the result back to
        ``data/stations.json``.  The temp file's writer is the last
        layer before the commit.

      * :func:`scripts.update_wl_stations.merge_into_stations` (line
        1226 pre-fix) — the Wien-Linien OGD CSV merger.  The local
        :func:`scripts.update_wl_stations._coerce_float` parser
        accepts non-finite floats via ``float(text)`` if the upstream
        CSV cell is the literal string ``"nan"`` / ``"inf"`` /
        ``"infinity"`` (per Python's :class:`float` constructor
        contract).

      * :func:`scripts.fetch_google_places_stations._write_if_changed`
        — the Google-Places-only manual escape hatch invoked by
        ``update-google-places-stations.yml`` (which lists
        ``git add data/stations.json`` in its commit step).

      * :func:`scripts.fetch_google_places_stations._dump_changes` —
        the companion diff-dump writer (writes a sibling JSON with
        only the ``new`` / ``updated`` entries; reviewed by operators
        and shared via the workflow logs).

(B) Cache writer (``cache/<provider>/events.json``) — committed to
    ``main`` by the IFTTT-triggered ``update-cycle.yml``:

      * :func:`src.utils.cache.write_cache` — the unified cache
        writer used by every provider.  The Baustellen feed
        (``scripts.update_baustellen_cache``) emits
        ``location.coordinates = {"lat": float, "lon": float}`` into
        each cached event; a compromised Stadt-Wien Baustellen
        upstream replying with ``{"coordinates": [NaN, Infinity]}``
        propagates through the parser and lands as non-standard
        literals in the committed cache file.

(C) Baustellen parser — defence-in-depth at the ingest boundary so
    a poisoned coordinate is *dropped* (rather than crashing the
    writer for the entire batch):

      * :func:`scripts.update_baustellen_cache._build_location`
        (line 509 pre-fix) — performs ``float(coordinates[0])`` /
        ``float(coordinates[1])`` without ``math.isfinite()`` or
        WGS84-range guards.  Mirrors the canonical floor pinned at
        :func:`src.places.hafas_client._extract_first_location` and
        :meth:`src.places.client.GooglePlacesClient._parse_place`.

Threat model
============

Three distinct attacker positions can plant non-finite or out-of-range
coordinates into the cron pipeline:

  1. **Compromised upstream**: ``data.wien.gv.at`` (Baustellen),
     OEBB ``Verzeichnis der Verkehrsstationen`` (Excel mirror),
     ``places.googleapis.com``, OGD Wien-Linien CSVs, the operator
     mirror of GTFS / VOR.  Any compromised JSON / CSV / XLSX
     response carrying the non-standard ``NaN`` / ``Infinity`` /
     ``-Infinity`` literal flows through the local ``float()`` /
     ``isinstance(..., int | float)`` shape checks without rejection.

  2. **MITM / DNS hijack** on any of the above network paths.

  3. **Poisoned on-disk cache / state file** — the alias-enrichment
     step and the orchestrator both re-read ``data/stations.json``
     from the previous cron run.  A planted non-finite coordinate
     value survives ``json.loads`` (Python's default lenient mode
     parses ``NaN`` / ``Infinity`` literals as ``float('nan')`` /
     ``float('inf')``) and propagates verbatim through the rewrite.

Public sinks impacted
=====================

  * ``data/stations.json`` — committed to ``main`` by
    ``update-stations.yml`` on every weekly cron tick.  Served from
    the GitHub web UI, the ``raw.githubusercontent.com`` mirror,
    the GitHub Pages mirror
    (``https://origamihase.github.io/wien-oepnv/stations.json``).
    Loaded by every downstream consumer that maps Vienna stations to
    their canonical coordinates.

  * ``cache/baustellen_*/events.json`` and the parallel
    ``cache/oebb_*/`` / ``cache/wl_*/`` / ``cache/vor_*/`` events
    files — committed to ``main`` by the IFTTT-triggered
    ``update-cycle.yml``.  Consumed by the feed builder which emits
    the public RSS at ``docs/feed.xml``.

Severity: **MEDIUM** — public-artefact data-integrity attack with
multi-vector attacker positions.  No code execution (the on-disk
values are plain floats), no credential leak, no SSRF.  Same shape
class as the historical Trojan-Source drift rounds: the canonical
floor (here, "finite, WGS84-range coordinate" + ``allow_nan=False``
defence pin) must apply uniformly across every writer site that
lands data into a committed JSON artefact.

The fix
=======

Eight coordinated edits, all pinned by this test file:

  1. ``scripts/update_station_directory.py:write_json`` —
     pass ``allow_nan=False`` to ``json.dump``.
  2. ``scripts/enrich_station_aliases.py:_write_stations_payload`` —
     same.
  3. ``scripts/update_all_stations.py:_write_stations_payload`` —
     same.
  4. ``scripts/update_wl_stations.py:merge_into_stations`` (the
     ``json.dump`` near line 1226) — same.
  5. ``scripts/fetch_google_places_stations.py:_write_if_changed`` —
     pass ``allow_nan=False`` to ``json.dumps``.
  6. ``scripts/fetch_google_places_stations.py:_dump_changes`` —
     same.
  7. ``src/utils/cache.py:write_cache`` — pass ``allow_nan=False``
     to ``json.dump``.
  8. ``scripts/update_baustellen_cache.py:_build_location`` —
     reject non-finite (``math.isfinite``) and out-of-WGS84-range
     coordinates at the parser boundary so the writer-side floor is
     pure defence-in-depth (the legitimate events in the same batch
     still serialise cleanly with their address and metadata).

Inventory invariant
===================

Every committed-to-main JSON writer that may carry float coordinates
must pin ``allow_nan=False`` (writer-side defence-in-depth) AND every
coordinate-emitting parser must apply the canonical finite + WGS84-
range floor at its boundary.  The ``test_inventory_*`` cases below
each grep the function source for the required pin so any future
edit that drops the contract fails the test.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_COMPANION_WRITER_DRIFT`` finds the full call-graph at once.
SENTINEL_COMPANION_WRITER_DRIFT = "companion writer allow_nan=False drift"


_NAN = float("nan")
_POS_INF = float("inf")
_NEG_INF = float("-inf")


def _raise_on_nan_or_infinity(token: str) -> float:
    """Strict-JSON ``parse_constant`` hook: refuse any non-finite literal.

    Mirrors the reader-side defence pinned in
    ``tests/test_sentinel_coordinate_nan_inf_range_drift.py``: a
    downstream consumer that wires this hook into ``json.loads`` MUST
    succeed on every artefact produced by the writer-side fix.
    """
    raise ValueError(f"Non-finite JSON literal {token!r} in committed artefact")


def _poisoned_station(*, lat: float = 48.185222, lon: float = 16.377778) -> dict[str, Any]:
    """Construct a station entry carrying the requested coords."""
    return {
        "name": "Wien Hauptbahnhof",
        "bst_code": "Wn",
        "aliases": [],
        "latitude": lat,
        "longitude": lon,
        "in_vienna": True,
        "pendler": False,
        "source": "oebb",
    }


def _legit_station() -> dict[str, Any]:
    return _poisoned_station()


# ---------------------------------------------------------------------------
# Lazy script imports (each script uses ``sys.path`` manipulation in its
# own header; importing once and caching keeps each per-writer test cheap).
# ---------------------------------------------------------------------------


def _import_script(name: str) -> Any:
    """Import a ``scripts/<name>.py`` module and return it.

    ``importlib.import_module`` is preferred over ``importlib.util`` so a
    re-import inside the same pytest run reuses the cached module rather
    than re-executing its top-level side effects (provider-cache wiring,
    logger configuration, etc.).
    """
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# (A1) ``update_station_directory.write_json`` — PoC + inventory.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (_NAN, 16.377778),
        (48.185222, _NAN),
        (_POS_INF, 16.377778),
        (48.185222, _POS_INF),
        (_NEG_INF, 16.377778),
        (48.185222, _NEG_INF),
        (_NAN, _NAN),
    ],
)
def test_update_station_directory_write_json_rejects_non_finite(
    tmp_path: Path,
    lat: float,
    lon: float,
) -> None:
    """PoC: ``scripts/update_station_directory.py:write_json`` must
    refuse to serialise a poisoned station whose ``latitude`` /
    ``longitude`` is ``NaN`` / ``Inf`` / ``-Inf``.

    Pre-fix ``json.dump`` writes the non-standard ``NaN`` / ``Infinity``
    literals into ``data/stations.json`` (invalid per RFC 8259) and
    every strict downstream JSON consumer breaks silently.  Post-fix
    ``json.dump(..., allow_nan=False)`` raises :class:`ValueError`.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("update_station_directory")
    out_path = tmp_path / "stations.json"
    poisoned = [_poisoned_station(lat=lat, lon=lon)]
    with pytest.raises(ValueError):
        module.write_json(poisoned, out_path)


def test_update_station_directory_write_json_accepts_legitimate(
    tmp_path: Path,
) -> None:
    """Regression: legitimate Vienna coordinates serialise cleanly AND
    the on-disk artefact is RFC-8259-strict (a strict ``json.loads``
    with ``parse_constant`` raising on any non-finite literal MUST
    round-trip the file).
    """
    module = _import_script("update_station_directory")
    out_path = tmp_path / "stations.json"
    module.write_json([_legit_station()], out_path)
    raw = out_path.read_text(encoding="utf-8")
    parsed = json.loads(raw, parse_constant=_raise_on_nan_or_infinity)
    assert parsed["stations"][0]["name"] == "Wien Hauptbahnhof"


def test_inventory_update_station_directory_write_json_pins_allow_nan_false() -> None:
    """Source-grep inventory: ``write_json`` must include the
    ``allow_nan=False`` literal so a future edit that drops the
    contract fails this test.
    """
    module = _import_script("update_station_directory")
    source = inspect.getsource(module.write_json)
    assert "allow_nan=False" in source, (
        "scripts/update_station_directory.py:write_json must serialise "
        "the stations payload with allow_nan=False; otherwise a future "
        "bypass of the parser-level finite floor silently corrupts "
        "data/stations.json with non-standard JSON literals."
    )


# ---------------------------------------------------------------------------
# (A2) ``enrich_station_aliases._write_stations_payload`` — PoC + inventory.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (_NAN, 16.377778),
        (48.185222, _POS_INF),
        (_NEG_INF, _POS_INF),
    ],
)
def test_enrich_station_aliases_writer_rejects_non_finite(
    tmp_path: Path,
    lat: float,
    lon: float,
) -> None:
    """PoC: ``scripts/enrich_station_aliases.py:_write_stations_payload``
    must refuse to serialise a poisoned station.  The alias enricher
    re-reads ``data/stations.json`` from the previous cron step; a
    planted ``NaN`` / ``Inf`` flows verbatim through the rewrite.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("enrich_station_aliases")
    out_path = tmp_path / "stations.json"
    poisoned = [_poisoned_station(lat=lat, lon=lon)]
    with pytest.raises(ValueError):
        module._write_stations_payload(out_path, poisoned)


def test_enrich_station_aliases_writer_accepts_legitimate(tmp_path: Path) -> None:
    module = _import_script("enrich_station_aliases")
    out_path = tmp_path / "stations.json"
    module._write_stations_payload(out_path, [_legit_station()])
    raw = out_path.read_text(encoding="utf-8")
    parsed = json.loads(raw, parse_constant=_raise_on_nan_or_infinity)
    assert parsed["stations"][0]["name"] == "Wien Hauptbahnhof"


def test_inventory_enrich_station_aliases_writer_pins_allow_nan_false() -> None:
    module = _import_script("enrich_station_aliases")
    source = inspect.getsource(module._write_stations_payload)
    assert "allow_nan=False" in source


# ---------------------------------------------------------------------------
# (A3) ``update_all_stations._write_stations_payload`` — PoC + inventory.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (_NAN, 16.377778),
        (48.185222, _POS_INF),
        (_NEG_INF, _NEG_INF),
    ],
)
def test_update_all_stations_writer_rejects_non_finite(
    tmp_path: Path,
    lat: float,
    lon: float,
) -> None:
    """PoC: the orchestrator's temp-file writer must refuse to serialise
    a poisoned station.  The temp file is copied back to
    ``data/stations.json`` and committed to ``main`` — a silent NaN
    leak past this writer reaches the public artefact directly.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("update_all_stations")
    out_path = tmp_path / "stations.json"
    poisoned = [_poisoned_station(lat=lat, lon=lon)]
    with pytest.raises(ValueError):
        module._write_stations_payload(out_path, poisoned)


def test_update_all_stations_writer_accepts_legitimate(tmp_path: Path) -> None:
    module = _import_script("update_all_stations")
    out_path = tmp_path / "stations.json"
    module._write_stations_payload(out_path, [_legit_station()])
    raw = out_path.read_text(encoding="utf-8")
    parsed = json.loads(raw, parse_constant=_raise_on_nan_or_infinity)
    assert parsed["stations"][0]["name"] == "Wien Hauptbahnhof"


def test_inventory_update_all_stations_writer_pins_allow_nan_false() -> None:
    module = _import_script("update_all_stations")
    source = inspect.getsource(module._write_stations_payload)
    assert "allow_nan=False" in source


# ---------------------------------------------------------------------------
# (A4) ``update_wl_stations.merge_into_stations`` — PoC + inventory.
# ---------------------------------------------------------------------------


def test_inventory_update_wl_stations_writer_pins_allow_nan_false() -> None:
    """Source-grep inventory: the ``json.dump`` call inside
    ``merge_into_stations`` (line 1226 pre-fix) must include
    ``allow_nan=False`` so the WL CSV merger cannot silently emit
    non-standard literals from a poisoned OGD response.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("update_wl_stations")
    source = inspect.getsource(module.merge_into_stations)
    assert "allow_nan=False" in source, (
        "scripts/update_wl_stations.py:merge_into_stations must "
        "serialise data/stations.json with allow_nan=False to mirror "
        "src/places/merge.py:write_stations (Round 1485)."
    )


# ---------------------------------------------------------------------------
# (A5/A6) ``fetch_google_places_stations`` — both writers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (_NAN, 16.377778),
        (48.185222, _POS_INF),
    ],
)
def test_fetch_google_places_write_if_changed_rejects_non_finite(
    tmp_path: Path,
    lat: float,
    lon: float,
) -> None:
    """PoC: ``_write_if_changed`` must refuse a poisoned station.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("fetch_google_places_stations")
    out_path = tmp_path / "stations.json"
    poisoned = [_poisoned_station(lat=lat, lon=lon)]
    with pytest.raises(ValueError):
        module._write_if_changed(out_path, poisoned)


def test_fetch_google_places_write_if_changed_accepts_legitimate(
    tmp_path: Path,
) -> None:
    module = _import_script("fetch_google_places_stations")
    out_path = tmp_path / "stations.json"
    module._write_if_changed(out_path, [_legit_station()])
    raw = out_path.read_text(encoding="utf-8")
    parsed = json.loads(raw, parse_constant=_raise_on_nan_or_infinity)
    assert parsed["stations"][0]["name"] == "Wien Hauptbahnhof"


def test_fetch_google_places_dump_changes_rejects_non_finite(tmp_path: Path) -> None:
    """PoC: ``_dump_changes`` must refuse a poisoned ``new`` entry.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("fetch_google_places_stations")
    out_path = tmp_path / "changes.json"
    poisoned = [_poisoned_station(lat=_NAN, lon=16.377778)]
    with pytest.raises(ValueError):
        module._dump_changes(out_path, poisoned, [])

    out_path2 = tmp_path / "changes2.json"
    poisoned_updated = [_poisoned_station(lat=48.185222, lon=_POS_INF)]
    with pytest.raises(ValueError):
        module._dump_changes(out_path2, [], poisoned_updated)


def test_fetch_google_places_dump_changes_accepts_legitimate(tmp_path: Path) -> None:
    module = _import_script("fetch_google_places_stations")
    out_path = tmp_path / "changes.json"
    module._dump_changes(out_path, [_legit_station()], [])
    raw = out_path.read_text(encoding="utf-8")
    parsed = json.loads(raw, parse_constant=_raise_on_nan_or_infinity)
    assert parsed["new"][0]["name"] == "Wien Hauptbahnhof"


def test_inventory_fetch_google_places_writers_pin_allow_nan_false() -> None:
    module = _import_script("fetch_google_places_stations")
    write_source = inspect.getsource(module._write_if_changed)
    dump_source = inspect.getsource(module._dump_changes)
    assert "allow_nan=False" in write_source, (
        "scripts/fetch_google_places_stations.py:_write_if_changed "
        "must serialise data/stations.json with allow_nan=False."
    )
    assert "allow_nan=False" in dump_source, (
        "scripts/fetch_google_places_stations.py:_dump_changes must "
        "serialise the change-dump JSON with allow_nan=False."
    )


# ---------------------------------------------------------------------------
# (B) ``src.utils.cache.write_cache`` — PoC + inventory.
# ---------------------------------------------------------------------------


def _baustelle_event(*, lat: float, lon: float) -> dict[str, Any]:
    """Construct a Baustellen cache event matching the canonical shape
    written by :func:`scripts.update_baustellen_cache._feature_to_event`.
    """
    return {
        "source": "Stadt Wien – Baustellen",
        "category": "Baustelle",
        "title": "Arbeiten Mariahilfer Straße",
        "description": "Fahrbahnverengung",
        "link": "https://www.data.gv.at/katalog/baustellen",
        "guid": "0000000000000000000000000000000000000000000000000000000000000000",
        "pubDate": "2025-10-01T06:00:00+02:00",
        "starts_at": "2025-10-01T06:00:00+02:00",
        "ends_at": "2025-10-15T22:00:00+02:00",
        "location": {
            "address": "Mariahilfer Straße",
            "coordinates": {"lat": lat, "lon": lon},
        },
    }


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (_NAN, 16.3505),
        (48.1981, _POS_INF),
        (_NEG_INF, _POS_INF),
    ],
)
def test_write_cache_rejects_non_finite_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lat: float,
    lon: float,
) -> None:
    """PoC: ``src/utils/cache.py:write_cache`` must refuse to serialise
    a cache event whose embedded ``coordinates.lat`` / ``coordinates.lon``
    is ``NaN`` / ``Inf`` / ``-Inf``.  The Baustellen feed lands these
    fields in the cache, and the file is committed to ``main`` by
    ``update-cycle.yml``.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    cache_module = importlib.import_module("src.utils.cache")
    monkeypatch.setattr(cache_module, "_CACHE_DIR", tmp_path)

    poisoned = [_baustelle_event(lat=lat, lon=lon)]
    with pytest.raises(ValueError):
        cache_module.write_cache("baustellen", poisoned)


def test_write_cache_accepts_legitimate_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a legitimate Vienna Baustellen event must serialise
    cleanly AND the on-disk cache file must round-trip a strict
    ``json.loads`` with ``parse_constant=raise_on_nan_or_infinity``.
    """
    cache_module = importlib.import_module("src.utils.cache")
    monkeypatch.setattr(cache_module, "_CACHE_DIR", tmp_path)

    legit = [_baustelle_event(lat=48.1981, lon=16.3505)]
    cache_module.write_cache("baustellen", legit)

    cache_file = cache_module._cache_file("baustellen")
    raw = cache_file.read_text(encoding="utf-8")
    parsed = json.loads(raw, parse_constant=_raise_on_nan_or_infinity)
    assert parsed[0]["location"]["coordinates"]["lat"] == 48.1981


def test_inventory_write_cache_pins_allow_nan_false() -> None:
    cache_module = importlib.import_module("src.utils.cache")
    source = inspect.getsource(cache_module.write_cache)
    assert "allow_nan=False" in source, (
        "src/utils/cache.py:write_cache must serialise cache events "
        "with allow_nan=False so a poisoned float (e.g. Baustellen "
        "coordinate) surfaces as a loud ValueError rather than "
        "silently corrupting cache/<provider>/events.json."
    )


# ---------------------------------------------------------------------------
# (C) ``update_baustellen_cache._build_location`` — parser-level scrub.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lon", "lat"),
    [
        # GeoJSON convention: coordinates is [lon, lat]; mirror that here.
        (_NAN, 48.1981),
        (16.3505, _NAN),
        (_POS_INF, 48.1981),
        (16.3505, _POS_INF),
        (_NEG_INF, 48.1981),
        (16.3505, _NEG_INF),
        (_NAN, _NAN),
    ],
)
def test_baustellen_build_location_drops_non_finite_coordinates(
    lon: float,
    lat: float,
) -> None:
    """PoC: ``_build_location`` must drop the ``coordinates`` field
    entirely (rather than recording the poisoned pair) when the lon /
    lat pair is non-finite.  The rest of the event's address /
    metadata is still valid and should be preserved.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("update_baustellen_cache")
    geometry = {"type": "Point", "coordinates": [lon, lat]}
    properties = {"STRASSE": "Mariahilfer Straße"}
    location = module._build_location(properties, geometry)
    assert "coordinates" not in location, (
        f"Non-finite coordinate (lon={lon}, lat={lat}) leaked into the "
        "cache event payload — the parser did not enforce "
        "math.isfinite() at the ingest boundary."
    )
    # The address field carrying the rest of the metadata must survive.
    assert location.get("address") == "Mariahilfer Straße"


@pytest.mark.parametrize(
    ("lon", "lat"),
    [
        # GeoJSON convention: [lon, lat].
        (181.0, 48.1981),
        (-181.0, 48.1981),
        (16.3505, 91.0),
        (16.3505, -91.0),
        # Pathological integer-overflow shapes.
        (1e10, 1e10),
        (-1e10, -1e10),
    ],
)
def test_baustellen_build_location_drops_out_of_range_coordinates(
    lon: float,
    lat: float,
) -> None:
    """PoC: ``_build_location`` must drop ``coordinates`` when either
    ``lat`` is outside ``[-90, 90]`` or ``lon`` is outside ``[-180, 180]``.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("update_baustellen_cache")
    geometry = {"type": "Point", "coordinates": [lon, lat]}
    location = module._build_location({}, geometry)
    assert "coordinates" not in location, (
        f"Out-of-WGS84-range coordinate (lon={lon}, lat={lat}) leaked "
        "into the cache event payload."
    )


def test_baustellen_build_location_accepts_vienna_coordinates() -> None:
    """Regression: a typical Vienna Baustellen coordinate must pass."""
    module = _import_script("update_baustellen_cache")
    geometry = {"type": "Point", "coordinates": [16.3505, 48.1981]}
    location = module._build_location({}, geometry)
    assert location["coordinates"]["lat"] == 48.1981
    assert location["coordinates"]["lon"] == 16.3505


def test_baustellen_build_location_accepts_wgs84_boundary_pairs() -> None:
    """Regression: WGS84 boundary corners (north / south pole,
    antimeridian) are legal coordinates in the WGS84 datum and MUST
    still pass post-fix.
    """
    module = _import_script("update_baustellen_cache")
    boundary_pairs: list[tuple[float, float]] = [
        (180.0, 90.0),
        (-180.0, -90.0),
        (0.0, 0.0),
        (180.0, 0.0),
        (0.0, 90.0),
    ]
    for lon, lat in boundary_pairs:
        geometry = {"type": "Point", "coordinates": [lon, lat]}
        location = module._build_location({}, geometry)
        assert "coordinates" in location, (
            f"WGS84 boundary pair ({lon}, {lat}) was rejected; only "
            "non-finite OR out-of-range pairs should be dropped."
        )


# ---------------------------------------------------------------------------
# (D) End-to-end integration — Baustellen NaN never reaches cache file.
# ---------------------------------------------------------------------------


def test_end_to_end_baustellen_nan_never_reaches_cache_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A poisoned Baustellen GeoJSON Feature combined with the
    canonical writer contract MUST result in either:

      * the parser dropping the ``coordinates`` field (so the event
        is still cached with its address and metadata, just without
        a coordinate pair), OR
      * a :class:`ValueError` at write time (writer-level
        defence-in-depth).

    Either way, the on-disk ``cache/baustellen/events.json`` MUST NOT
    contain the non-standard ``NaN`` / ``Infinity`` literal.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    cache_module = importlib.import_module("src.utils.cache")
    monkeypatch.setattr(cache_module, "_CACHE_DIR", tmp_path)

    parser_module = _import_script("update_baustellen_cache")
    geometry = {"type": "Point", "coordinates": [_NAN, _POS_INF]}
    location = parser_module._build_location({"STRASSE": "Test"}, geometry)
    event = {
        "source": "Baustellen",
        "title": "Test",
        "description": "Test event",
        "link": "https://example.invalid/",
        "guid": "0000000000000000000000000000000000000000000000000000000000000001",
        "pubDate": "2025-10-01T06:00:00+02:00",
        "starts_at": "2025-10-01T06:00:00+02:00",
        "ends_at": None,
        "location": location,
    }
    if "coordinates" in location:  # pragma: no cover - parser floor leaked
        # Parser floor failed — the writer must catch the bypass.
        with pytest.raises(ValueError):
            cache_module.write_cache("baustellen", [event])
        return
    # Parser floor caught the poison — the writer succeeds and emits a
    # strict-RFC-8259 artefact (no NaN / Infinity literals).
    cache_module.write_cache("baustellen", [event])
    cache_file = cache_module._cache_file("baustellen")
    raw = cache_file.read_text(encoding="utf-8")
    parsed = json.loads(raw, parse_constant=_raise_on_nan_or_infinity)
    assert parsed[0]["location"]["address"] == "Test"


# ---------------------------------------------------------------------------
# (E) Companion-writer inventory invariant: enumerate every committed-to-
#     main JSON writer that may carry float coordinates and assert each
#     pins allow_nan=False in lockstep with the canonical
#     ``src.places.merge.write_stations`` contract.
# ---------------------------------------------------------------------------


def test_inventory_every_committed_artefact_writer_pins_allow_nan_false() -> None:
    """Whole-family inventory: every writer that lands a JSON artefact
    in a path the cron pipeline commits to ``main`` MUST pin
    ``allow_nan=False``.  Pre-fix the canonical
    ``src/places/merge.py:write_stations`` had the pin; the five
    sibling ``data/stations.json`` writers AND the cache writer did
    not.

    A future writer added to the cron commit set without the pin
    fails this test on the first pytest run.
    """
    canonical = importlib.import_module("src.places.merge")
    canonical_source = inspect.getsource(canonical.write_stations)
    assert "allow_nan=False" in canonical_source, (
        "src/places/merge.py:write_stations lost the canonical "
        "allow_nan=False pin established in Round 1485."
    )

    inventory: list[tuple[str, str]] = [
        ("update_station_directory", "write_json"),
        ("enrich_station_aliases", "_write_stations_payload"),
        ("update_all_stations", "_write_stations_payload"),
        ("update_wl_stations", "merge_into_stations"),
        ("fetch_google_places_stations", "_write_if_changed"),
        ("fetch_google_places_stations", "_dump_changes"),
    ]
    missing: list[str] = []
    for module_name, func_name in inventory:
        module = _import_script(module_name)
        func = getattr(module, func_name)
        if "allow_nan=False" not in inspect.getsource(func):
            missing.append(f"scripts/{module_name}.py:{func_name}")

    cache_module = importlib.import_module("src.utils.cache")
    if "allow_nan=False" not in inspect.getsource(cache_module.write_cache):
        missing.append("src/utils/cache.py:write_cache")

    assert not missing, (
        "Companion JSON writers missing the allow_nan=False pin "
        "(committed-to-main artefacts): " + ", ".join(missing) + ". "
        "Mirror the canonical contract from "
        "src/places/merge.py:write_stations (Round 1485)."
    )


def test_inventory_baustellen_parser_pins_canonical_finite_floor() -> None:
    """Inventory: the Baustellen coordinate parser must apply the
    canonical finite + WGS84-range floor at its boundary, mirroring
    the HAFAS / Google Places / OSM parser-level checks pinned in
    Round 1485.

    SENTINEL_COMPANION_WRITER_DRIFT.
    """
    module = _import_script("update_baustellen_cache")
    source = inspect.getsource(module._build_location)
    assert "math.isfinite" in source, (
        "scripts/update_baustellen_cache.py:_build_location must "
        "apply math.isfinite() to lat/lon at the parser boundary."
    )
    # Range check: the function must reject pairs outside the WGS84 valid
    # range so a future bypass surfaces at parse time, not at write time.
    assert "-90" in source and "180" in source, (
        "scripts/update_baustellen_cache.py:_build_location must "
        "apply a WGS84-range check (-90 <= lat <= 90, -180 <= lon "
        "<= 180) at the parser boundary."
    )


# ---------------------------------------------------------------------------
# (F) Smoke test — every patched module imports cleanly.
# ---------------------------------------------------------------------------


def test_module_imports_remain_clean() -> None:
    """All patched modules must still import without error and expose
    the canonical entry points the surrounding pipeline relies on.
    """
    for module_name, func_name in [
        ("update_station_directory", "write_json"),
        ("enrich_station_aliases", "_write_stations_payload"),
        ("update_all_stations", "_write_stations_payload"),
        ("update_wl_stations", "merge_into_stations"),
        ("fetch_google_places_stations", "_write_if_changed"),
        ("fetch_google_places_stations", "_dump_changes"),
        ("update_baustellen_cache", "_build_location"),
    ]:
        module = _import_script(module_name)
        assert callable(getattr(module, func_name))

    cache_module = importlib.import_module("src.utils.cache")
    assert callable(cache_module.write_cache)
    canonical = importlib.import_module("src.places.merge")
    assert callable(canonical.write_stations)
    # Sanity: math.isfinite is the helper we rely on for the parser
    # floor, mirroring the documented contract in the round.
    assert math.isfinite(48.185222)
    assert not math.isfinite(_NAN)
    assert not math.isfinite(_POS_INF)
