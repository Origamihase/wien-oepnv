"""Coverage for ``_enrich_manual_stations`` and ``_merge_sources_into_entry``.

The manual block (``source=manual``, ``type=manual_distant_at``/``manual_foreign_city``)
bypasses ``_filter_relevant_stations`` and therefore the ÖBB-side OSM/HAFAS/Google
enrichment chain. ``_enrich_manual_stations`` closes that gap by re-using the
already-loaded ``location_index`` (GTFS + VOR coords, free) and falling back to
the unmetered HAFAS LocMatch tier.

These tests verify the three resolution branches (local hit, HAFAS hit, both
miss), the skip-if-already-has-coords idempotency contract, the source-token
merge rules, and the never-crash contract on HAFAS upstream failures.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts import update_station_directory as usd


def _location(lat: float, lon: float, *, source: str = "gtfs") -> usd.LocationInfo:
    return usd.LocationInfo(latitude=lat, longitude=lon, sources={source})


def _manual(name: str, **extras: Any) -> dict[str, object]:
    base: dict[str, object] = {
        "name": name,
        "in_vienna": False,
        "pendler": False,
        "source": "manual",
        "type": "manual_distant_at",
        "aliases": [name],
    }
    base.update(extras)
    return base


def test_skips_entries_that_already_have_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Idempotency: a subsequent cron tick must not re-resolve entries whose
    coords were filled in by a previous run."""
    entry = _manual("Melk", latitude=48.227778, longitude=15.332778)
    location_index = {"melk": _location(0.0, 0.0)}  # would clobber if branch ran

    def _explode_if_called(_name: str) -> object:
        raise AssertionError("HAFAS must not be called for entries with coords")

    monkeypatch.setattr(usd, "enrich_station_with_hafas", _explode_if_called)
    enriched = usd._enrich_manual_stations([entry], location_index)

    assert enriched == 0
    assert entry["latitude"] == 48.227778
    assert entry["longitude"] == 15.332778


def test_local_index_wins_before_hafas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local GTFS/VOR lookup is the cheap-first tier — HAFAS must NOT be
    called when the entry is already resolvable from the in-memory index."""
    entry = _manual("Wolkersdorf")
    location_index = {"wolkersdorf": _location(48.3784, 16.5127, source="gtfs")}

    def _explode_if_called(_name: str) -> object:
        raise AssertionError("HAFAS must not be called when local index hits")

    monkeypatch.setattr(usd, "enrich_station_with_hafas", _explode_if_called)
    enriched = usd._enrich_manual_stations([entry], location_index)

    assert enriched == 1
    assert entry["latitude"] == 48.3784
    assert entry["longitude"] == 16.5127
    # Source merge: 'manual' (existing) + 'gtfs' (location_index source), comma-sorted
    assert entry["source"] == "gtfs,manual"


def test_hafas_fallback_when_local_index_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local index miss → HAFAS LocMatch fallback. Coords + extId persist."""
    entry = _manual("Mariazell")
    location_index: dict[str, usd.LocationInfo] = {}  # no local coverage

    def _hafas_hit(name: str) -> dict[str, object] | None:
        assert name == "Mariazell"
        return {"name": "Mariazell Bahnhof", "lat": 47.7717, "lon": 15.3175, "extId": "8100098"}

    monkeypatch.setattr(usd, "enrich_station_with_hafas", _hafas_hit)
    enriched = usd._enrich_manual_stations([entry], location_index)

    assert enriched == 1
    assert entry["latitude"] == 47.7717
    assert entry["longitude"] == 15.3175
    assert entry["hafas_extId"] == "8100098"
    assert entry["source"] == "hafas,manual"


def test_no_resolution_leaves_entry_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both tiers miss the entry stays coordinate-less — no spurious
    keys appear, return count is 0."""
    entry = _manual("Mystery Halt")
    monkeypatch.setattr(usd, "enrich_station_with_hafas", lambda _name: None)

    enriched = usd._enrich_manual_stations([entry], {})

    assert enriched == 0
    assert "latitude" not in entry
    assert "longitude" not in entry
    assert "hafas_extId" not in entry
    assert entry["source"] == "manual"


def test_hafas_exception_does_not_crash_pipeline(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The HAFAS tier MUST be best-effort — an unexpected exception is
    swallowed (logged) so the cron pipeline never aborts on a transient
    upstream blip. Mirrors the same contract as ``_enrich_with_hafas``."""

    def _hafas_raises(_name: str) -> object:
        raise RuntimeError("simulated transient HAFAS failure")

    monkeypatch.setattr(usd, "enrich_station_with_hafas", _hafas_raises)
    entry = _manual("Sopron")

    # Must not raise
    enriched = usd._enrich_manual_stations([entry], {})

    assert enriched == 0
    assert "latitude" not in entry


def test_empty_input_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge case: no manual entries to enrich → zero work, no calls."""
    def _explode(_name: str) -> object:
        raise AssertionError("HAFAS must not be called for empty input")

    monkeypatch.setattr(usd, "enrich_station_with_hafas", _explode)
    assert usd._enrich_manual_stations([], {}) == 0


def test_merge_sources_preserves_existing_and_dedupes() -> None:
    """``_merge_sources_into_entry`` is the canonical source-field
    formatter for manual enrichment — must dedupe, sort, and accept
    both string and list inputs."""
    entry: dict[str, object] = {"source": "manual,gtfs"}
    usd._merge_sources_into_entry(entry, {"hafas", "gtfs"})  # gtfs already present
    assert entry["source"] == "gtfs,hafas,manual"


def test_merge_sources_handles_missing_source_field() -> None:
    """A manual entry without an existing source token still gets a clean
    canonical form."""
    entry: dict[str, object] = {}
    usd._merge_sources_into_entry(entry, {"hafas"})
    assert entry["source"] == "hafas"


def test_skips_entry_with_blank_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry with a blank/whitespace name must be skipped silently —
    no HAFAS call (which would 400 anyway), no crash."""
    def _explode(_name: str) -> object:
        raise AssertionError("HAFAS must not be called for blank names")

    monkeypatch.setattr(usd, "enrich_station_with_hafas", _explode)
    entry: dict[str, object] = {"name": "  ", "source": "manual"}

    assert usd._enrich_manual_stations([entry], {}) == 0
    assert "latitude" not in entry
