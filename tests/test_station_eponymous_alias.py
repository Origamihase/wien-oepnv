"""Regression tests for eponymous-station alias precedence in ``_station_lookup``.

When two stations contend for the same normalized alias at equal match
strength, the station the alias is *named after* must win it — even when a
neighbour that merely cross-references the name was registered earlier in
file order. Pre-fix the eponymous check compared the bare alias key against
``_normalize_token(full_name)``, which keeps the ``Wien`` prefix and ``(WL)``
suffix (``"Wien Taborstraße (WL)"`` → ``"wien taborstrasse wl"``), so it never
matched the bare ``"taborstrasse"`` key and the earlier neighbour kept it
(observed: ``Taborstraße`` → Heinestraße, ``Kagran`` → Betriebshof Kagran).
"""
import json
from pathlib import Path
from typing import Any

import pytest

from src.utils import stations
from src.utils.stations import StationInfo


def _lookup_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: list[dict[str, Any]]
) -> dict[str, StationInfo]:
    temp_file = tmp_path / "stations.json"
    temp_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(stations, "_STATIONS_PATH", temp_file)
    stations._station_entries.cache_clear()
    stations._station_lookup.cache_clear()
    try:
        return stations._station_lookup()
    finally:
        stations._station_entries.cache_clear()
        stations._station_lookup.cache_clear()


def test_eponymous_station_wins_over_earlier_neighbour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Heinestraße is registered FIRST and cross-references "Taborstraße";
    # the eponymous "Wien Taborstraße (WL)" comes later and must win.
    data = [
        {"name": "Wien Heinestraße (WL)", "wl_diva": "1", "source": "wl",
         "aliases": ["Heinestraße", "Taborstraße"]},
        {"name": "Wien Taborstraße (WL)", "wl_diva": "2", "source": "wl",
         "aliases": ["Taborstraße"]},
    ]
    lut = _lookup_with(tmp_path, monkeypatch, data)
    assert lut["taborstrasse"].name == "Wien Taborstraße (WL)"


def test_station_wins_bare_name_over_earlier_depot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The depot ("Bahnhof Kagran" → "kagran") is registered first; the public
    # "Wien Kagran (WL)" station must still own the bare "Kagran" lookup.
    data = [
        {"name": "Wien Betriebshof Kagran (WL)", "wl_diva": "1", "source": "wl",
         "aliases": ["Bahnhof Kagran"]},
        {"name": "Wien Kagran (WL)", "wl_diva": "2", "source": "wl",
         "aliases": ["Kagran"]},
    ]
    lut = _lookup_with(tmp_path, monkeypatch, data)
    assert lut["kagran"].name == "Wien Kagran (WL)"


def test_non_eponymous_collision_keeps_first_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Neither station is named "Collision" → the eponymous rule must NOT fire;
    # the historical first-registered-wins behaviour is preserved.
    data = [
        {"name": "First Station", "wl_diva": "1", "source": "wl", "aliases": ["Collision"]},
        {"name": "Second Station", "wl_diva": "2", "source": "wl", "aliases": ["Collision"]},
    ]
    lut = _lookup_with(tmp_path, monkeypatch, data)
    assert lut["collision"].name == "First Station"
