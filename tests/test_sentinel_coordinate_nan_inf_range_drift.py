"""Sentinel PoC: Coordinate-ingest drift — HAFAS / Google Places parsers
accept non-finite (``NaN`` / ``+Inf`` / ``-Inf``) and out-of-WGS84-range
``lat`` / ``lon`` values from a compromised upstream and persist them
verbatim into the public ``data/stations.json`` artefact.

The 2026-05-11 HAFAS coordinate-fallback round (PR #1482, commit
``45d899c``) added :mod:`src.places.hafas_client` as the third tier of
the station-directory enrichment cascade (OSM → HAFAS → Google Places).
The HAFAS parser :func:`src.places.hafas_client._extract_first_location`
validates that ``crd.x`` / ``crd.y`` are ``int | float`` (and not
``bool``) but performs no finite check (``math.isfinite``) and no
WGS84 range check.  The OSM sibling :func:`src.places.osm_client.
filter_complete_places` defends against ``NaN`` via ``math.isnan`` at
line 617 AND incidentally against ``+Inf`` / ``-Inf`` via
``BoundingBox.contains`` (lines 516-517) — every comparison with
``±inf`` falls outside the Vienna box and the place is dropped.

The Google Places sibling :func:`src.places.client.GooglePlacesClient.
_parse_place` (lines 323-327) ships an even thinner check: only the
``isinstance(latitude, float | int)`` shape, which accepts
``float('nan')`` / ``float('inf')`` / ``float('-inf')`` directly
because ``isinstance(NaN, float)`` returns ``True`` and the JSON decoder
parses the non-standard ``NaN`` / ``Infinity`` / ``-Infinity`` literals
into the corresponding Python float values by default.

Threat model
------------

Three distinct attacker positions can plant non-finite or out-of-range
coordinates into the cron pipeline:

  1. **Compromised HAFAS upstream** (``fahrplan.oebb.at``): a planted
     ``LocMatch`` response with ``crd: {x: NaN, y: Infinity}`` returns
     a :class:`HafasLocation` with ``lon=nan, lat=inf``.  The
     :func:`scripts.update_station_directory._enrich_with_hafas`
     writer commits the value straight onto ``station.extras
     ["latitude"]`` / ``["longitude"]`` without any post-validation,
     and :func:`src.places.merge.write_stations` then serialises the
     mutated stations list to ``data/stations.json`` with the default
     ``allow_nan=True`` flag.  The resulting on-disk JSON carries the
     non-standard ``NaN`` / ``Infinity`` literals — invalid per
     RFC 8259, broken in every strict consumer (``JSON.parse`` in
     every modern browser, ``serde_json`` in Rust, ``encoding/json``
     in Go), and silently visualised at impossible map locations by
     the lenient consumers that *do* accept them.

  2. **Compromised Google Places upstream** (``places.googleapis.com``):
     same shape via ``location: {latitude: NaN, longitude: Infinity}``
     in the ``searchText`` response.  The
     :class:`src.places.client.GooglePlacesClient._parse_place`
     surface passes the value through ``float()`` and constructs a
     :class:`Place` with the poisoned coordinates.  Downstream
     :func:`src.places.merge.merge_places` writes the same fields
     onto ``station["latitude"]`` / ``["longitude"]`` and the writer
     persists them.

  3. **MITM / DNS hijack** on either upstream: identical shape — the
     finite check is absent on the *parser* side of the network
     boundary, so any on-path attacker controls both the byte stream
     AND the cached on-disk state.

Sinks (public artefacts)
------------------------

  * ``data/stations.json`` — committed to ``main`` on every weekly
    ``update-stations.yml`` cron tick (see
    ``.github/workflows/update-stations.yml``).  Served from the
    GitHub web UI, the raw.githubusercontent.com mirror, the GitHub
    Pages mirror at ``https://origamihase.github.io/wien-oepnv/
    stations.json``.  Loaded by every downstream consumer that maps
    Vienna stations to their canonical coordinates.

  * Operator-facing logs (``log/diagnostics.log``) — the poisoned
    coordinates are written verbatim into stations.json AND every
    pre-write merge diagnostic that emits ``station["latitude"]`` /
    ``["longitude"]`` to the logger.  ``NaN`` and ``Infinity`` in
    log lines break every JSON-aware log shipper (Datadog, Splunk
    HEC, the GitHub Actions log viewer) that strictly parses the
    structured-log envelope.

Severity: **MEDIUM** — public-artefact data-integrity attack with
two attacker positions.  No code execution (the on-disk values are
plain floats), no credential leak, no SSRF.  Same shape class as
the historical Trojan-Source drift rounds: the canonical floor
(here, "finite WGS84 coordinate") must apply uniformly across every
ingest tier before the data lands in the committed artefact.

The fix
-------

Three coordinated boundary scrubs:

  1. :func:`src.places.hafas_client._extract_first_location` — after
     dividing by ``_HAFAS_COORD_SCALE``, reject any pair where
     ``not math.isfinite(lat)`` or ``not math.isfinite(lon)`` (catches
     ``NaN`` / ``+Inf`` / ``-Inf``) or where the coordinate falls
     outside the WGS84 valid range (``lat`` ∈ ``[-90, 90]``, ``lon``
     ∈ ``[-180, 180]``).

  2. :func:`src.places.client.GooglePlacesClient._parse_place` —
     mirror the same scrub on the post-``float()`` lat/lon pair so
     a hostile Google Places response (or MITM) cannot smuggle
     non-finite or out-of-range values into the :class:`Place`
     dataclass.

  3. :func:`src.places.merge.write_stations` — defence-in-depth:
     pass ``allow_nan=False`` to ``json.dumps`` so any future bypass
     (a fourth tier added without the canonical floor) surfaces as a
     loud :class:`ValueError` at write time rather than silently
     corrupting the committed artefact with non-standard JSON
     literals.

Inventory invariant
-------------------

Every ingest tier in :mod:`src.places` that emits coordinates into
``data/stations.json`` must apply the finite + WGS84 range floor
at its parser boundary, mirroring the prior Trojan-Source rounds
where every reachable string sink applied the canonical
attack-byte scrub.  The inventory test
:func:`test_inventory_every_places_parser_validates_finite_range`
names every current parser and asserts each rejects the canonical
inventory of non-finite / out-of-range pairs; a future tier whose
parser drops the check fails the inventory test.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from src.places import client as places_client
from src.places import hafas_client
from src.places.client import GooglePlacesClient, GooglePlacesConfig
from src.places.hafas_client import HafasLocation
from src.places.merge import write_stations


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_COORD_FINITE_DRIFT`` finds the full call-graph at once.
SENTINEL_COORD_FINITE_DRIFT = "coord-ingest finite/WGS84-range floor drift"


# Canonical inventory of non-finite and out-of-WGS84-range coordinate
# values an attacker can plant via ``NaN`` / ``Infinity`` JSON literals
# (Python's ``json.loads`` accepts them by default) or via integer
# overflow of the HAFAS 1e6-scaled wire format.
_NAN = float("nan")
_POS_INF = float("inf")
_NEG_INF = float("-inf")


def _hafas_payload(x: object, y: object) -> dict[str, Any]:
    """Return a minimal HAFAS LocMatch response carrying *x* and *y*."""
    return {
        "svcResL": [
            {
                "meth": "LocMatch",
                "err": "OK",
                "res": {
                    "match": {
                        "locL": [
                            {
                                "name": "Wien Hauptbahnhof",
                                "extId": "8100353",
                                "crd": {"x": x, "y": y},
                            }
                        ]
                    }
                },
            }
        ]
    }


def _google_place_payload(lat: object, lng: object) -> dict[str, Any]:
    """Return a minimal Google Places response with *lat* and *lng*."""
    return {
        "id": "places/ChIJtest123",
        "displayName": {"text": "Wien Hauptbahnhof"},
        "location": {"latitude": lat, "longitude": lng},
        "types": ["train_station"],
        "formattedAddress": "Am Hauptbahnhof, 1100 Wien",
    }


# ---------------------------------------------------------------------------
# (1) HAFAS parser — reject non-finite coordinates.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x_raw", "y_raw"),
    [
        # ``NaN`` planted via the standard JSON literal: ``json.loads``
        # accepts ``{"x": NaN}`` in Python's default lenient mode.
        (_NAN, 48_185_222),
        (16_377_778, _NAN),
        (_NAN, _NAN),
        # ``+Infinity`` / ``-Infinity`` likewise accepted by lenient
        # JSON parsing.
        (_POS_INF, 48_185_222),
        (16_377_778, _POS_INF),
        (_NEG_INF, 48_185_222),
        (16_377_778, _NEG_INF),
        (_POS_INF, _NEG_INF),
    ],
)
def test_hafas_parser_rejects_non_finite_coordinates(
    x_raw: float,
    y_raw: float,
) -> None:
    """PoC: a HAFAS upstream replying with ``NaN`` / ``Inf`` for x or y
    must NOT produce a :class:`HafasLocation`.  Pre-fix the parser
    returns a poisoned location with ``lon=nan`` / ``lat=inf``;
    post-fix the parser returns ``None``.

    SENTINEL_COORD_FINITE_DRIFT.
    """
    result = hafas_client._extract_first_location(_hafas_payload(x_raw, y_raw))
    assert result is None, (
        f"Non-finite HAFAS coordinate {x_raw=}/{y_raw=} reached the "
        "downstream writer — parser did not enforce math.isfinite()."
    )


# ---------------------------------------------------------------------------
# (2) HAFAS parser — reject out-of-WGS84-range coordinates.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x_raw", "y_raw"),
    [
        # Out-of-range latitude (post-scale > 90 or < -90).
        (16_377_778, 90_000_001),
        (16_377_778, -90_000_001),
        # Out-of-range longitude (post-scale > 180 or < -180).
        (180_000_001, 48_185_222),
        (-180_000_001, 48_185_222),
        # Pathological large integer overflow attempt.
        (999_999_999_999, 48_185_222),
        (16_377_778, -888_888_888_888),
    ],
)
def test_hafas_parser_rejects_out_of_range_coordinates(
    x_raw: int,
    y_raw: int,
) -> None:
    """PoC: HAFAS upstream replying with coordinates outside the WGS84
    valid range (``lat`` ∈ ``[-90, 90]``, ``lon`` ∈ ``[-180, 180]``)
    must NOT produce a :class:`HafasLocation`.

    SENTINEL_COORD_FINITE_DRIFT.
    """
    result = hafas_client._extract_first_location(_hafas_payload(x_raw, y_raw))
    assert result is None, (
        f"Out-of-WGS84-range HAFAS coordinate {x_raw=}/{y_raw=} "
        "reached the downstream writer — parser did not enforce "
        "the WGS84 valid-range floor."
    )


def test_hafas_parser_accepts_legitimate_vienna_coordinates() -> None:
    """Regression: the canonical Wien Hauptbahnhof coordinates
    (``16.377778`` / ``48.185222``) must still pass post-fix.
    """
    result = hafas_client._extract_first_location(_hafas_payload(16_377_778, 48_185_222))
    assert result == HafasLocation(
        name="Wien Hauptbahnhof",
        extId="8100353",
        lon=16.377778,
        lat=48.185222,
    )


def test_hafas_parser_accepts_wgs84_edge_coordinates() -> None:
    """Regression: exact WGS84-range boundary values must still pass
    post-fix (``±90`` lat, ``±180`` lon) — these are legal coordinates
    in the WGS84 datum (north / south pole, antimeridian).
    """
    edge_cases = [
        (180_000_000, 90_000_000),
        (-180_000_000, -90_000_000),
        (0, 0),
    ]
    for x, y in edge_cases:
        result = hafas_client._extract_first_location(_hafas_payload(x, y))
        assert result is not None, f"WGS84 boundary {x=}/{y=} was rejected"


# ---------------------------------------------------------------------------
# (3) Google Places parser — reject non-finite coordinates.
# ---------------------------------------------------------------------------


def _make_places_client() -> GooglePlacesClient:
    """Construct a minimal GooglePlacesClient suitable for parser tests."""
    config = GooglePlacesConfig(
        api_key="test-api-key",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=1000,
        timeout_s=5.0,
        max_retries=0,
    )
    return GooglePlacesClient(config)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (_NAN, 16.377778),
        (48.185222, _NAN),
        (_NAN, _NAN),
        (_POS_INF, 16.377778),
        (48.185222, _POS_INF),
        (_NEG_INF, 16.377778),
        (48.185222, _NEG_INF),
        (_POS_INF, _NEG_INF),
    ],
)
def test_google_places_parser_rejects_non_finite_coordinates(
    latitude: float,
    longitude: float,
) -> None:
    """PoC: Google Places upstream replying with ``NaN`` / ``Inf``
    for latitude or longitude must NOT produce a :class:`Place`.

    SENTINEL_COORD_FINITE_DRIFT.
    """
    client = _make_places_client()
    result = client._parse_place(_google_place_payload(latitude, longitude))
    assert result is None, (
        f"Non-finite Google Places coordinate {latitude=}/{longitude=} "
        "reached the downstream writer — parser did not enforce "
        "math.isfinite()."
    )


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (90.000_001, 16.377778),
        (-90.000_001, 16.377778),
        (48.185222, 180.000_001),
        (48.185222, -180.000_001),
        (1e10, 1e10),
        (-1e10, -1e10),
    ],
)
def test_google_places_parser_rejects_out_of_range_coordinates(
    latitude: float,
    longitude: float,
) -> None:
    """PoC: Google Places upstream replying with coordinates outside
    the WGS84 valid range must NOT produce a :class:`Place`.

    SENTINEL_COORD_FINITE_DRIFT.
    """
    client = _make_places_client()
    result = client._parse_place(_google_place_payload(latitude, longitude))
    assert result is None, (
        f"Out-of-WGS84-range Google Places coordinate {latitude=}/"
        f"{longitude=} reached the downstream writer — parser did "
        "not enforce the WGS84 valid-range floor."
    )


def test_google_places_parser_accepts_legitimate_vienna_coordinates() -> None:
    """Regression: a typical Vienna coordinate pair must still pass."""
    client = _make_places_client()
    result = client._parse_place(_google_place_payload(48.185222, 16.377778))
    assert result is not None
    assert math.isclose(result.latitude, 48.185222, abs_tol=1e-9)
    assert math.isclose(result.longitude, 16.377778, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# (4) Writer defence-in-depth — ``write_stations`` rejects non-finite floats.
# ---------------------------------------------------------------------------


def test_write_stations_rejects_nan_or_infinity_in_payload(
    tmp_path: Path,
) -> None:
    """PoC: ``write_stations`` must refuse to serialise a station whose
    ``latitude`` / ``longitude`` is ``NaN`` / ``Inf`` / ``-Inf``.  Pre-
    fix ``json.dumps`` writes the non-standard ``NaN`` / ``Infinity``
    literals (invalid per RFC 8259) into ``data/stations.json`` —
    silently breaking every strict downstream JSON consumer.  Post-fix
    ``json.dumps(allow_nan=False)`` raises :class:`ValueError` and
    the caller's failure path takes over.

    SENTINEL_COORD_FINITE_DRIFT.
    """
    stations_path = tmp_path / "stations.json"
    poisoned: list[dict[str, object]] = [
        {
            "name": "Wien Hbf",
            "aliases": [],
            "latitude": _NAN,
            "longitude": 16.377778,
        }
    ]
    with pytest.raises(ValueError):
        write_stations(stations_path, poisoned)

    poisoned_inf: list[dict[str, object]] = [
        {
            "name": "Wien Hbf",
            "aliases": [],
            "latitude": 48.185222,
            "longitude": _POS_INF,
        }
    ]
    with pytest.raises(ValueError):
        write_stations(stations_path, poisoned_inf)

    poisoned_neg_inf: list[dict[str, object]] = [
        {
            "name": "Wien Hbf",
            "aliases": [],
            "latitude": _NEG_INF,
            "longitude": 16.377778,
        }
    ]
    with pytest.raises(ValueError):
        write_stations(stations_path, poisoned_neg_inf)


def test_write_stations_accepts_legitimate_stations(tmp_path: Path) -> None:
    """Regression: legitimate finite coordinates must still serialise
    cleanly, and the resulting JSON must be RFC-8259-strict so a
    ``json.loads(..., parse_constant=...)`` round-trip with the
    NaN/Inf trap remains a safe reader posture for downstream code.
    """
    stations_path = tmp_path / "stations.json"
    legit: list[dict[str, object]] = [
        {
            "name": "Wien Hauptbahnhof",
            "aliases": ["Wien Hbf"],
            "latitude": 48.185222,
            "longitude": 16.377778,
        }
    ]
    write_stations(stations_path, legit)

    raw = stations_path.read_text(encoding="utf-8")
    # Strict round-trip: a parser that raises on NaN/Infinity must
    # still load the committed artefact.  Any future bypass that
    # writes a non-finite literal fails this check loudly.
    parsed = json.loads(
        raw,
        parse_constant=_raise_on_nan_or_infinity,
    )
    assert parsed["stations"][0]["name"] == "Wien Hauptbahnhof"


def _raise_on_nan_or_infinity(token: str) -> float:
    """Strict-JSON ``parse_constant`` hook: refuse any non-finite literal."""
    raise ValueError(f"Non-finite JSON literal {token!r} in committed artefact")


# ---------------------------------------------------------------------------
# (5) End-to-end integration — non-finite HAFAS coordinate never reaches
#     the on-disk artefact even if a future ingest tier forgets the check.
# ---------------------------------------------------------------------------


def test_end_to_end_nan_never_reaches_stations_file(tmp_path: Path) -> None:
    """A poisoned HAFAS response combined with the canonical writer
    contract MUST result in either:

      * a ``None`` return from the parser (parser-level floor), OR
      * a :class:`ValueError` at write time (writer-level floor).

    Either way, the on-disk ``stations.json`` MUST NOT contain the
    non-standard ``NaN`` / ``Infinity`` literal.
    """
    parser_out = hafas_client._extract_first_location(_hafas_payload(_NAN, _POS_INF))
    if parser_out is not None:  # pragma: no cover — must not happen post-fix
        # The parser floor leaked.  The writer floor must catch the
        # bypass.  Construct the station shape the
        # ``_enrich_with_hafas`` writer would have produced.
        stations_path = tmp_path / "stations.json"
        bypass_payload: list[dict[str, object]] = [
            {
                "name": parser_out["name"],
                "aliases": [],
                "latitude": parser_out["lat"],
                "longitude": parser_out["lon"],
            }
        ]
        with pytest.raises(ValueError):
            write_stations(stations_path, bypass_payload)


# ---------------------------------------------------------------------------
# (6) Inventory invariant — every parser tier applies the canonical floor.
# ---------------------------------------------------------------------------


def test_inventory_every_places_parser_validates_finite_range() -> None:
    """Inventory: every public coordinate-emitting parser in
    :mod:`src.places` must apply the finite + WGS84-range floor at
    its boundary.  A future fourth ingest tier added without the
    canonical floor will fail this test.

    SENTINEL_COORD_FINITE_DRIFT.
    """
    # HAFAS parser — non-finite rejection.
    assert hafas_client._extract_first_location(
        _hafas_payload(_NAN, 48_185_222)
    ) is None
    assert hafas_client._extract_first_location(
        _hafas_payload(_POS_INF, 48_185_222)
    ) is None
    # HAFAS parser — out-of-range rejection.
    assert hafas_client._extract_first_location(
        _hafas_payload(999_999_999, 48_185_222)
    ) is None

    # Google Places parser — non-finite rejection.
    client = _make_places_client()
    assert client._parse_place(_google_place_payload(_NAN, 16.377778)) is None
    assert client._parse_place(_google_place_payload(_POS_INF, 16.377778)) is None
    # Google Places parser — out-of-range rejection.
    assert client._parse_place(_google_place_payload(91.0, 16.377778)) is None
    assert client._parse_place(_google_place_payload(48.185222, 181.0)) is None


def test_inventory_writer_pins_allow_nan_false_contract() -> None:
    """Inventory: the :func:`src.places.merge.write_stations` writer
    must pin ``allow_nan=False`` so a future bypass of either parser
    floor cannot silently corrupt the committed artefact with
    non-standard JSON literals.
    """
    import inspect

    source = inspect.getsource(write_stations)
    assert "allow_nan=False" in source, (
        "write_stations must serialise the stations payload with "
        "allow_nan=False so a future bypass of the parser-level "
        "finite floor surfaces as a loud ValueError at write time "
        "rather than silently corrupting data/stations.json."
    )


# ---------------------------------------------------------------------------
# (7) Module-import smoke test — the fixes must not regress the module.
# ---------------------------------------------------------------------------


def test_module_imports_remain_clean() -> None:
    """The patched modules must still import without error and expose
    the entry points the surrounding pipeline relies on."""
    assert hasattr(hafas_client, "_extract_first_location")
    assert hasattr(hafas_client, "enrich_station_with_hafas")
    assert hasattr(places_client, "GooglePlacesClient")
    assert callable(write_stations)
