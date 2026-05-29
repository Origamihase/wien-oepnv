"""Regression tests for the round-12 places-module bundle.

Pins three coordinate / field-mask correctness fixes:

1. ``src/places/tiling.py`` ``_coerce_coordinate`` rejects non-finite
   coordinates supplied as JSON *strings* (``"NaN"`` / ``"Infinity"`` /
   ``"1e999"``). The ``json.loads`` ``parse_float`` / ``parse_constant``
   hooks only fire for JSON *number* literals, so a stringified coordinate
   previously bypassed the module's documented non-finite defence and
   landed ``float('inf')`` / ``float('nan')`` in a :class:`Tile`. It also
   rejects ``bool`` coordinates (an ``int`` subclass) and converts the
   huge-integer ``OverflowError`` into a clean ``ValueError``.

2. ``src/places/client.py`` ``_parse_place`` rejects ``bool`` latitude /
   longitude. ``bool`` is an ``int`` subclass, so a smuggled ``true`` /
   ``false`` previously satisfied ``isinstance(..., float | int)`` and
   coerced to a bogus ``1.0`` / ``0.0`` that sailed past the finite +
   WGS84-range guards.

3. ``src/places/client.py`` ``FIELD_MASK_NEARBY`` includes
   ``places.formattedAddress`` so the production Google path actually
   populates ``Place.formatted_address`` — ``merge._infer_in_vienna`` keys
   the Vienna classification off the address text and the merge layer
   persists ``_formatted_address`` to ``data/stations.json``. Pre-fix the
   New Places API omitted the unmasked field and the whole address-based
   signal silently degraded to bounding-box-only.
"""
from __future__ import annotations

import pytest

from src.places import tiling
from src.places.client import (
    FIELD_MASK_NEARBY,
    GooglePlacesClient,
    GooglePlacesConfig,
)


# ---------------------------------------------------------------------------
# Fix 1 — tiling._coerce_coordinate: stringified non-finite / bool / overflow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_value",
    [
        '[{"lat": "NaN", "lng": "16.3"}]',
        '[{"lat": "Infinity", "lng": "16.3"}]',
        '[{"lat": "16.3", "lng": "-Infinity"}]',
        '[{"lat": "1e999", "lng": "16.3"}]',
        '[{"lat": "16.3", "lng": "1e1000"}]',
    ],
)
def test_load_tiles_from_env_rejects_stringified_non_finite(raw_value: str) -> None:
    """A coordinate supplied as a JSON *string* bypasses the parse_float /
    parse_constant hooks (which only fire for number literals);
    ``_coerce_coordinate`` must re-validate finiteness and reject it."""
    with pytest.raises(ValueError):
        tiling.load_tiles_from_env(raw_value)


def test_load_tiles_from_env_rejects_huge_int_coordinate() -> None:
    """A several-hundred-digit JSON integer parses to an arbitrary-precision
    Python ``int`` (the parse_float non-finite hook never sees an integer
    literal) and ``float()`` overflows IEEE-754. The fix surfaces this as a
    clean ``ValueError`` instead of an uncaught ``OverflowError`` that would
    escape the caller's ``except (OSError, ValueError)`` cron guard."""
    huge = "1" + "0" * 400
    with pytest.raises(ValueError):
        tiling.load_tiles_from_env(f'[{{"lat": {huge}, "lng": 16.3}}]')


def test_load_tiles_from_env_rejects_bool_coordinate() -> None:
    """``true`` / ``false`` (``bool`` is an ``int`` subclass) must not coerce
    to ``1.0`` / ``0.0`` tile coordinates."""
    with pytest.raises(ValueError):
        tiling.load_tiles_from_env('[{"lat": true, "lng": 16.3}]')


def test_load_tiles_from_env_accepts_stringified_finite_coordinate() -> None:
    """Regression guard: a legitimate stringified *finite* coordinate still
    parses to the expected :class:`Tile` — the fix must not over-reject."""
    tiles = tiling.load_tiles_from_env('[{"lat": "48.2", "lng": "16.37"}]')
    assert len(tiles) == 1
    assert tiles[0].latitude == 48.2
    assert tiles[0].longitude == 16.37


# ---------------------------------------------------------------------------
# Fix 2 — client._parse_place: reject bool coordinates
# ---------------------------------------------------------------------------


def _make_client() -> GooglePlacesClient:
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


def _place_payload(lat: object, lng: object) -> dict[str, object]:
    return {
        "id": "places/ChIJtest",
        "displayName": {"text": "Wien Hbf"},
        "location": {"latitude": lat, "longitude": lng},
        "types": ["train_station"],
        "formattedAddress": "Am Hauptbahnhof, 1100 Wien",
    }


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(True, 16.37), (48.2, False), (True, False)],
)
def test_parse_place_rejects_bool_coordinate(lat: object, lng: object) -> None:
    """``bool`` is an ``int`` subclass; a smuggled ``true`` / ``false`` must
    NOT become a bogus ``1.0`` / ``0.0`` coordinate (pre-fix it passed the
    ``isinstance(..., float | int)`` guard and both finite + range checks)."""
    client = _make_client()
    assert client._parse_place(_place_payload(lat, lng)) is None


def test_parse_place_still_accepts_legitimate_coordinate() -> None:
    """Regression guard: a normal float coordinate still produces a Place."""
    client = _make_client()
    place = client._parse_place(_place_payload(48.185222, 16.377778))
    assert place is not None
    assert place.latitude == 48.185222


# ---------------------------------------------------------------------------
# Fix 3 — FIELD_MASK_NEARBY requests formattedAddress
# ---------------------------------------------------------------------------


def test_field_mask_includes_formatted_address() -> None:
    """``places.formattedAddress`` must be in the mask, otherwise the New
    Places API omits the field and merge._infer_in_vienna /
    ``_formatted_address`` persistence silently lose the address signal."""
    assert "places.formattedAddress" in FIELD_MASK_NEARBY
    # Sibling invariant (pinned in test_fieldmask_and_types.py too):
    # searchNearby does not accept nextPageToken in the field mask.
    assert "nextPageToken" not in FIELD_MASK_NEARBY


def test_parse_place_populates_formatted_address() -> None:
    """With ``formattedAddress`` present, the parser surfaces it on
    ``Place.formatted_address`` — the field the mask fix makes reachable in
    production (the merge layer keys the Vienna classification off it)."""
    client = _make_client()
    place = client._parse_place(_place_payload(48.2, 16.37))
    assert place is not None
    assert place.formatted_address == "Am Hauptbahnhof, 1100 Wien"
