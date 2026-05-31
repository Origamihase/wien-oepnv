"""Regression tests for the round-2 line-by-line audit (2026-05).

Each test pins a behavioural fix so a future refactor that reintroduces the
defect fails loudly. The companion fixes for the midnight-rollover heuristic,
the Places circuit-breaker HALF_OPEN probe and the ``_make_rss`` mock contract
live next to their existing siblings (``tests/scripts/test_update_stammstrecke_*``,
``tests/places/test_client_circuit_breaker.py``, ``tests/test_build_feed_mutation.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import requests

from src.places.client import (
    MAX_REQUEST_RETRIES,
    MAX_TIMEOUT_S,
    GooglePlacesClient,
    GooglePlacesConfig,
    Place,
)
from src.places.merge import MergeConfig, StationEntry, merge_places
from src.places.quota import MonthlyQuota


def _cfg(**overrides: Any) -> GooglePlacesConfig:
    base: dict[str, Any] = dict(
        api_key="k",
        included_types=["bus_station"],
        language="de",
        region="AT",
        radius_m=1000,
        timeout_s=5.0,
        max_retries=2,
        max_result_count=20,
    )
    base.update(overrides)
    return GooglePlacesConfig(**base)


# --- #1: GooglePlacesConfig floor/range clamps -----------------------------


def test_config_floors_negative_retries_to_zero() -> None:
    # A negative ``max_retries`` made ``while attempt <= max_retries`` never
    # run → every tile hard-failed and the whole Places tier silently died.
    assert _cfg(max_retries=-1).max_retries == 0
    assert _cfg(max_retries=-99).max_retries == 0


def test_config_clamps_nonpositive_timeout_to_default() -> None:
    # A non-positive timeout would make ``requests`` raise a bare ValueError
    # mid-request and abort the enrichment pass; fall back to the safe default.
    assert _cfg(timeout_s=0).timeout_s == MAX_TIMEOUT_S
    assert _cfg(timeout_s=-3.0).timeout_s == MAX_TIMEOUT_S


def test_config_still_caps_oversized_timeout_and_retries() -> None:
    assert _cfg(timeout_s=99999.0).timeout_s == MAX_TIMEOUT_S
    assert _cfg(max_retries=99999).max_retries == MAX_REQUEST_RETRIES


def test_config_clamps_result_count_and_radius_to_api_range() -> None:
    assert _cfg(max_result_count=0).max_result_count == 1
    assert _cfg(max_result_count=99).max_result_count == 20
    assert _cfg(radius_m=0).radius_m == 1
    assert _cfg(radius_m=10**9).radius_m == 50000


# --- #8: client consumes the config geometry, not the module globals -------


def test_client_uses_config_geometry_not_module_globals() -> None:
    cfg = _cfg(radius_m=1234, max_result_count=7)
    client = GooglePlacesClient(cfg, session=MagicMock(spec=requests.Session))
    assert client._radius_m == 1234
    assert client._max_result_count == 7


# --- #2: distance match must not relocate a station bound to another id ----


def test_distance_match_preserves_station_bound_to_other_place_id() -> None:
    """A distance-only match against a station that already carries a DIFFERENT
    valid Google place_id must keep that id AND its coordinates/types/address —
    it must not be silently relocated onto a neighbouring, distinct place."""
    existing: list[StationEntry] = [
        {
            "name": "Established Stop",
            "source": "oebb,google_places",
            "_google_place_id": "place-X",
            "aliases": [],
            "latitude": 48.2060,
            "longitude": 16.3840,
            "_types": ["train_station"],
            "_formatted_address": "Addr X",
            "in_vienna": True,
        }
    ]
    # A distinct place (different id, non-matching name) ~66 m away — within
    # the 150 m radius, so it matches by distance, not by name.
    places = [
        Place(
            place_id="place-Y",
            name="Totally Different Name",
            latitude=48.2065,
            longitude=16.3845,
            types=["bus_station"],
            formatted_address="Addr Y",
        )
    ]
    outcome = merge_places(
        existing, places, MergeConfig(max_distance_m=150.0, bounding_box=None)
    )
    station = next(s for s in outcome.stations if s["name"] == "Established Stop")
    assert station["_google_place_id"] == "place-X"
    assert station["latitude"] == 48.2060
    assert station["longitude"] == 16.3840
    assert station["_types"] == ["train_station"]
    assert station["_formatted_address"] == "Addr X"


# --- #29: monthly quota key resets on the Vienna boundary, like the day key -


def test_month_key_uses_vienna_not_utc_across_boundary() -> None:
    # UTC 2026-01-31 23:30 == Vienna 2026-02-01 00:30 (CET, +1 h): the month
    # and day keys must advance TOGETHER (both Europe/Vienna).
    instant = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
    assert MonthlyQuota.current_month_key(instant) == "2026-02"
    assert MonthlyQuota.current_daily_key(instant) == "2026-02-01"


def test_month_key_midday_utc_matches_vienna_month() -> None:
    instant = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    assert MonthlyQuota.current_month_key(instant) == "2026-03"
