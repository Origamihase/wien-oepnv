"""Strict-subset contract for the Google Places fallback.

The OSM-first / Google-second hierarchy requires
``_enrich_with_google_places`` to operate exclusively on the subset of
stations that still lack coordinates after the OSM run. Stations OSM
already resolved must never be re-keyed by the fallback, even if a
Google Place happens to share their name. These tests pin the contract
end-to-end without any real network IO.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from collections.abc import Sequence

import pytest

from scripts import update_station_directory as usd


def _station(name: str, *, lat: float | None = None, lng: float | None = None) -> usd.Station:
    extras: dict[str, object] = {}
    if lat is not None:
        extras["latitude"] = lat
    if lng is not None:
        extras["longitude"] = lng
    return usd.Station(
        bst_id=name.replace(" ", "_"),
        bst_code="X",
        name=name,
        in_vienna=False,
        pendler=False,
        extras=extras,
    )


def test_skips_google_call_when_subset_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty missing_subset must short-circuit BEFORE any Google call.

    Even when GOOGLE_ACCESS_ID is set, if every station already has
    coordinates the fallback cannot legitimately do anything — it must
    leave the stations untouched and not consume any quota.
    """
    monkeypatch.setenv("GOOGLE_ACCESS_ID", "fake-key-not-used")

    def _must_not_be_called(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Google Places must not be invoked when subset is empty")

    monkeypatch.setattr(usd, "_load_tiles_configuration", _must_not_be_called)
    monkeypatch.setattr(usd, "_fetch_google_places", _must_not_be_called)
    monkeypatch.setattr(usd, "_merge_google_metadata", _must_not_be_called)

    stations = [_station("Wien Mitte", lat=48.21, lng=16.39)]
    with caplog.at_level("INFO", logger="update_station_directory"):
        usd._enrich_with_google_places(stations, tiles_file=None, missing_subset=[])

    assert any("no stations are missing coordinates" in record.getMessage() for record in caplog.records)


def test_only_subset_is_passed_to_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """When missing_subset is non-empty, the merge step must operate on
    that subset only — the resolved stations (full list) must be filtered
    out before reaching ``_merge_google_metadata``."""
    monkeypatch.setenv("GOOGLE_ACCESS_ID", "fake-key-not-used")

    resolved = _station("Wien Mitte", lat=48.21, lng=16.39)
    missing = _station("Wien Praterstern")
    stations = [resolved, missing]

    monkeypatch.setattr(
        usd,
        "_load_tiles_configuration",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        usd,
        "_fetch_google_places",
        lambda *_a, **_k: [],
    )

    received: dict[str, Sequence[usd.Station]] = {}

    def _capture_merge(targets: Sequence[usd.Station], *_args: Any, **_kwargs: Any) -> None:
        received["targets"] = list(targets)

    monkeypatch.setattr(usd, "_merge_google_metadata", _capture_merge)

    usd._enrich_with_google_places(
        stations,
        tiles_file=None,
        missing_subset=[missing],
    )

    targets = received.get("targets")
    assert targets is not None, "_merge_google_metadata was never invoked"
    assert [s.name for s in targets] == ["Wien Praterstern"]
    # The resolved station (already had coordinates from OSM) must NOT
    # have been forwarded into the Google merge step.
    assert not any(s is resolved for s in targets)


def test_legacy_full_list_path_when_subset_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Passing missing_subset=None preserves the legacy whole-list flow
    used by historical no-OSM cron paths. This guards against an over-
    eager refactor that would silently break callers that haven't been
    updated to the new strict contract yet."""
    monkeypatch.setenv("GOOGLE_ACCESS_ID", "fake-key-not-used")

    monkeypatch.setattr(usd, "_load_tiles_configuration", lambda *_a, **_k: [])
    monkeypatch.setattr(usd, "_fetch_google_places", lambda *_a, **_k: [])

    received: dict[str, Sequence[usd.Station]] = {}

    def _capture_merge(targets: Sequence[usd.Station], *_args: Any, **_kwargs: Any) -> None:
        received["targets"] = list(targets)

    monkeypatch.setattr(usd, "_merge_google_metadata", _capture_merge)

    a = _station("Wien Mitte", lat=48.21, lng=16.39)
    b = _station("Wien Praterstern")
    usd._enrich_with_google_places([a, b], tiles_file=None)

    assert {s.name for s in received["targets"]} == {"Wien Mitte", "Wien Praterstern"}
