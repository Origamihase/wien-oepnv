"""Regression tests for the round-2 line-by-line audit (2026-05).

Each test pins a behavioural fix so a future refactor that reintroduces the
defect fails loudly. The companion fixes for the midnight-rollover heuristic
and the ``_make_rss`` mock contract live next to their existing siblings
(``tests/scripts/test_update_stammstrecke_*``, ``tests/test_build_feed_mutation.py``).
The Google-Places-specific regressions (config clamps, client geometry,
monthly quota key) were removed together with the Places client on
2026-10-16.
"""

from __future__ import annotations

from src.places.merge import MergeConfig, StationEntry, merge_places
from src.places.place import Place


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
