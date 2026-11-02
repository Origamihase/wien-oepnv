"""Teil 2 der Tests aus tests/test_update_wl_stations_merge.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from scripts import update_wl_stations

from ._support import (
    _stop,
)


def test_merge_wl_payload_strips_stale_wl_diva_aliases() -> None:
    """``_merge_wl_payload`` must remove ``aliases`` entries that look
    like a Wiener Linien DIVA (8 digits, starting with ``60``) but no
    longer match the entry's current ``wl_diva``. Without this, a stale
    DIVA pinned by an earlier cron tick survives across runs and
    trivially collides with another entry's current ``wl_diva`` via
    the cross-station-id validator — exactly the post-PR #1445 failure
    mode where Karlsplatz carried the legacy alias ``60201076`` which
    Wiener Linien has since reassigned to Ratzenhofergasse.
    """
    target: dict[str, object] = {
        "name": "Wien Karlsplatz",
        "wl_diva": "60201076",  # stale, pre-renumbering
        "aliases": [
            "Wien Karlsplatz",
            "490065700",  # legitimate vor_id — must be preserved
            "60201076",   # stale WL DIVA — must be stripped (mismatch)
            "60201077",   # stale WL alias — must be stripped (mismatch)
            "Karlsplatz",
        ],
    }
    payload: Mapping[str, object] = {
        "wl_diva": "60200657",  # current, post-renumbering
        "wl_stops": [],
        "source": "wl",
        "aliases": ["Wien Karlsplatz (WL)"],
    }

    update_wl_stations._merge_wl_payload(target, payload)

    aliases = cast(list[str], target["aliases"])
    assert "60201076" not in aliases, "Stale legacy WL DIVA must be stripped."
    assert "60201077" not in aliases, "Stale legacy WL alias must be stripped."
    assert "490065700" in aliases, "Real vor_id alias must be preserved."
    assert "Wien Karlsplatz" in aliases, "Natural-name aliases must be preserved."
    assert "Karlsplatz" in aliases
    assert target["wl_diva"] == "60200657", "wl_diva must be updated to current."


def test_build_wl_entries_merges_colocated_haltestellen(tmp_path: Path) -> None:
    """Two haltestellen with the same canonical name AND haltepunkte
    within 150 m of each other (same physical stop, two DIVAs for
    opposing-direction bahnsteige) must fold into a single entry.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60201433;Vorgartenstraße;Wien;49000001;16.4019;48.2241\n"
        "60200752;Vorgartenstraße;Wien;49000001;16.4025;48.2236\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "1;60201433;Vorgartenstraße A;Wien;49000001;16.4019;48.2241\n"
        "2;60201433;Vorgartenstraße B;Wien;49000001;16.4018;48.2240\n"
        "3;60200752;Vorgartenstraße C;Wien;49000001;16.4025;48.2236\n"
        "4;60200752;Vorgartenstraße D;Wien;49000001;16.4023;48.2235\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1, "Two haltestellen <150m apart must merge"
    merged = entries[0]
    # Lexicographically-lowest wl_diva wins
    assert merged["wl_diva"] == "60200752"
    # All four stops folded in
    wl_stops = cast(list[dict[str, object]], merged["wl_stops"])
    assert {s["stop_id"] for s in wl_stops} == {"1", "2", "3", "4"}
    # Name stays unsuffixed (merge made it unique, disambiguation no-op)
    assert "(WL 6" not in str(merged["name"])


def test_build_wl_entries_does_not_suffix_far_apart_duplicate_names(
    tmp_path: Path,
) -> None:
    """Two haltestellen with the same canonical name but haltepunkte
    >150 m apart stay as two SEPARATE entries — and (after 2026-05-12)
    keep the un-suffixed ``Wien <PlatformText> (WL)`` display label.

    The DIVA suffix disambiguation introduced by PR #1448 was retired
    together with the validator's canonical-name uniqueness check:
    structured identifiers (``wl_diva``) carry the eindeutigkeits-
    Garantie, so the operator-facing display label can stay clean for
    the RSS feed even when the same ``PlatformText`` legitimately
    appears at two physical locations.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60205022;Bahnhof;Wien;49000001;16.2697;48.0078\n"
        "60205201;Bahnhof;Wien;49000001;16.3141;48.0870\n"
        "60200077;Arbeitergasse, Gürtel;Wien;49000001;16.3460;48.1841\n"
        "60201880;Arbeitergasse, Gürtel;Wien;49000001;16.3449;48.1856\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        # Generic PlatformText 'Bahnhof' + informative StopText →
        # _derive_station_label overrides the generic label
        "1;60205022;Tribuswinkel - Josefsthal;Wien;49000001;16.2697;48.0078\n"
        "2;60205201;Wiener Neudorf;Wien;49000001;16.3141;48.0870\n"
        # Non-generic PlatformText with identical StopText → keep
        # PlatformText (multi-modal at one venue, no disambiguator)
        "3;60200077;Arbeitergasse, Gürtel;Wien;49000001;16.3460;48.1841\n"
        "4;60201880;Arbeitergasse, Gürtel;Wien;49000001;16.3449;48.1856\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 4
    by_diva = {str(e["wl_diva"]): str(e["name"]) for e in entries}

    # Generic PlatformText + informative StopText → derived label
    # replaces the generic "Wien Bahnhof (WL)" with a real toponym.
    assert by_diva["60205022"] == "Wien Tribuswinkel - Josefsthal (WL)", (
        "Generic PlatformText 'Bahnhof' must be overridden by the "
        "informative haltepunkte StopText so the RSS feed never "
        "renders the meaningless 'Wien Bahnhof' label."
    )
    # 'Wiener Neudorf' starts with 'Wien' so _canonical_name keeps it
    # as-is without a redundant 'Wien' prefix.
    # `Wiener Neudorf` starts with "Wien" so _canonical_name keeps it
    # as-is without prepending a redundant "Wien" prefix — the
    # generic `Bahnhof` PlatformText is replaced by the informative
    # StopText.
    assert by_diva["60205201"] in {"Wiener Neudorf (WL)", "Wien Wiener Neudorf (WL)"}

    # Multi-modal duplicate with identical StopText: PlatformText
    # carries the location info already, no override possible. Both
    # entries keep the same canonical label — structured wl_diva
    # disambiguates them at the data layer.
    arbeiter_names = sorted(
        n for d, n in by_diva.items() if d in {"60200077", "60201880"}
    )
    assert arbeiter_names == [
        "Wien Arbeitergasse, Gürtel (WL)",
        "Wien Arbeitergasse, Gürtel (WL)",
    ]


def test_derive_station_label_overrides_generic_platform_text() -> None:
    """``_derive_station_label`` swaps generic transport-typed
    ``PlatformText`` tokens for the informative haltepunkte StopText
    when one is available. Non-generic PlatformText values are kept
    untouched so ÖBB / VOR name-based joins remain stable.
    """
    def hp(name: str, stop_id: str = "1") -> update_wl_stations.Haltepunkt:
        return update_wl_stations.Haltepunkt(
            station_id="60200000",
            stop_id=stop_id,
            name=name,
            latitude=48.2,
            longitude=16.4,
        )

    # Generic PlatformText + single (orthogonal) StopText
    # → trust the StopText even when it shares no substring.
    assert update_wl_stations._derive_station_label(
        "Bahnhof", [hp("Tribuswinkel - Josefsthal")]
    ) == "Tribuswinkel - Josefsthal"
    assert update_wl_stations._derive_station_label(
        "Lokalbahn", [hp("Guntramsdorf Lokalbahn")]
    ) == "Guntramsdorf Lokalbahn"

    # Direction qualifiers are stripped before the cleaned-StopText
    # set is computed; if all qualified StopTexts collapse to one
    # cleaned value, that value wins.
    assert update_wl_stations._derive_station_label(
        "Bahnhof",
        [
            hp("Tribuswinkel - Josefsthal (Richtung A)", "1"),
            hp("Tribuswinkel - Josefsthal (Richtung B)", "2"),
        ],
    ) == "Tribuswinkel - Josefsthal"

    # Non-generic PlatformText keeps its label even when haltepunkte
    # carry richer text. Preserves ÖBB / VOR merge-by-name stability.
    assert update_wl_stations._derive_station_label(
        "Schottenring", [hp("Schottenring, Herminengasse")]
    ) == "Schottenring"
    assert update_wl_stations._derive_station_label(
        "Karlsplatz",
        [hp("Karlsplatz U (Richtung Reumannplatz)", "1"),
         hp("Karlsplatz U (Richtung Leopoldau)", "2")],
    ) == "Karlsplatz"

    # Generic PlatformText + multiple distinct StopTexts: nothing
    # specific to pick → keep PlatformText.
    assert update_wl_stations._derive_station_label(
        "Bahnhof", [hp("Tribuswinkel", "1"), hp("Wiener Neudorf", "2")]
    ) == "Bahnhof"

    # No haltepunkte → keep PlatformText.
    assert update_wl_stations._derive_station_label("Karlsplatz", []) == "Karlsplatz"


def test_build_wl_entries_auto_promotes_outside_station_to_pendler() -> None:
    """An unmatched WL station outside the Vienna polygon must reach the
    merge step with ``pendler=True`` so it does not trip
    ``_find_naming_issues`` → auto-quarantine. Mirrors the legacy
    ``test_wl_outside_station_becomes_pendler`` heuristic from
    ``test_update_station_directory_flags`` for the
    ``build_wl_entries`` boundary.
    """
    haltestellen = {
        "9999": update_wl_stations.Haltestelle(
            station_id="9999", name="Eisenstadt Domplatz", diva="60299999"
        )
    }
    haltepunkte = [
        update_wl_stations.Haltepunkt(
            station_id="9999",
            stop_id="60299999",
            name="Eisenstadt Domplatz",
            latitude=47.846,
            longitude=16.522,
        )
    ]

    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["in_vienna"] is False
    assert entry["pendler"] is True


def test_build_wl_entries_keeps_vienna_station_as_non_pendler() -> None:
    """Inside-Vienna WL stations stay ``pendler=False`` — the auto-promote
    only fires when the polygon check says ``in_vienna=False``."""
    haltestellen = {
        "1001": update_wl_stations.Haltestelle(
            station_id="1001", name="Karlsplatz", diva="60201076"
        )
    }
    haltepunkte = [
        update_wl_stations.Haltepunkt(
            station_id="1001",
            stop_id="60201076",
            name="Karlsplatz U (Richtung Reumannplatz)",
            latitude=48.200888,
            longitude=16.368907,
        )
    ]

    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["in_vienna"] is True
    assert entry["pendler"] is False


def test_merge_sources_emits_alphabetical_order() -> None:
    """``_merge_sources`` must produce a deterministic alphabetical
    ordering so two callers with the same set of providers (in any
    order) yield identical strings. Regression for the inconsistent
    "google_places,oebb" vs "oebb,google_places" duplication that
    snuck into stations.json across PRs."""
    assert update_wl_stations._merge_sources("oebb", "google_places") == "google_places,oebb"
    assert update_wl_stations._merge_sources("google_places", "oebb") == "google_places,oebb"
    assert (
        update_wl_stations._merge_sources("vor", "google_places", "wl")
        == "google_places,vor,wl"
    )
    # idempotent: pre-sorted input stays sorted, dedup wins
    assert (
        update_wl_stations._merge_sources("google_places,vor", "vor", "wl")
        == "google_places,vor,wl"
    )


def test_drop_distant_name_contamination() -> None:
    """A wl_stop/alias named like a >2km station is dropped; a nearby
    interchange name is kept. Mirrors the real Grinzing→Karlsplatz case."""
    entries: list[dict[str, object]] = [
        {
            "name": "Wien Grinzing (WL)",
            "latitude": 48.2554,
            "longitude": 16.3421,
            "aliases": ["Grinzing", "Karlsplatz", "Grinzinger Allee"],
            "wl_stops": [
                {"stop_id": "1", "name": "Grinzing", "latitude": 48.2554, "longitude": 16.3421},
                {"stop_id": "2", "name": "Karlsplatz", "latitude": 48.2554, "longitude": 16.3421},
            ],
        },
        {  # 6.4 km from Grinzing → its name on Grinzing is contamination
            "name": "Wien Karlsplatz",
            "latitude": 48.2008,
            "longitude": 16.3694,
            "aliases": ["Karlsplatz"],
            "wl_stops": [],
        },
        {  # ~250 m from Grinzing → legitimate nearby interchange name, kept
            "name": "Wien Grinzinger Allee (WL)",
            "latitude": 48.2540,
            "longitude": 16.3450,
            "aliases": ["Grinzinger Allee"],
            "wl_stops": [],
        },
    ]

    dropped = update_wl_stations._drop_distant_name_contamination(entries)

    assert dropped == 2  # the far "Karlsplatz" alias + the "Karlsplatz" wl_stop
    grinzing = cast(Mapping[str, object], entries[0])
    aliases = cast("list[str]", grinzing["aliases"])
    stops = cast("list[Mapping[str, object]]", grinzing["wl_stops"])
    assert "Karlsplatz" not in aliases
    assert all(s.get("name") != "Karlsplatz" for s in stops)
    # nearby interchange name and own name are preserved
    assert "Grinzinger Allee" in aliases
    assert "Grinzing" in aliases


def test_aggregate_coordinates_excludes_implausible_stop() -> None:
    """A Waldviertel outlier must not drag the station aggregate."""
    stops = [
        _stop("1", 48.2600, 16.4200),
        _stop("2", 48.2602, 16.4204),
        _stop("3", 48.6945799, 15.5787004),  # the corrupt upstream row
    ]
    assert update_wl_stations._aggregate_coordinates(stops) == (48.2601, 16.4202)


def test_aggregate_coordinates_returns_none_when_all_implausible() -> None:
    """Three corrupt rows yield no coordinate rather than a wrong one."""
    stops = [_stop("1", 48.6945799, 15.5787004), _stop("2", 48.7235788, 15.0678375)]
    assert update_wl_stations._aggregate_coordinates(stops) == (None, None)


def test_aggregate_coordinates_keeps_badner_bahn_terminus() -> None:
    """The furthest genuine WL stop stays inside the gate.

    Leesdorf/Baden is the Badner-Bahn terminus at ~24.9 km from the network
    centre — the real extent the threshold must not clip.
    """
    stops = [_stop("1", 47.9926, 16.2434)]
    latitude, longitude = update_wl_stations._aggregate_coordinates(stops)
    assert latitude == 47.9926
    assert longitude == 16.2434


def test_aggregate_coordinates_still_skips_missing_coordinates() -> None:
    """The pre-existing ``None`` skip is unchanged by the gate."""
    stops = [_stop("1", 48.2600, 16.4200), _stop("2", None, None)]
    assert update_wl_stations._aggregate_coordinates(stops) == (48.26, 16.42)


@pytest.mark.parametrize(
    "latitude, longitude, expected",
    [
        (48.2082, 16.3738, True),    # Stephansplatz — network centre
        (47.9926, 16.2434, True),    # Leesdorf/Baden — furthest genuine stop
        (48.6945799, 15.5787004, False),  # Wassermanngasse, corrupt upstream
        (48.7235788, 15.0678375, False),  # Halblehenweg, corrupt upstream
        (48.4036711, 15.3864340, False),  # Leopoldine-Padaurek-Str., corrupt
    ],
)
def test_is_plausible_wl_stop(latitude: float, longitude: float, expected: bool) -> None:
    assert update_wl_stations._is_plausible_wl_stop(latitude, longitude) is expected


def test_build_wl_entries_takes_gtfs_coordinate_when_every_stop_is_implausible() -> None:
    """DIVA 60201956 (Wassermanngasse) sits in the Waldviertel in both OGD
    files; the station gets the same stop's coordinate from the WL GTFS
    stops instead of a hand-written override (2026-10-25)."""
    haltestellen = {
        "60201956": update_wl_stations.Haltestelle(
            station_id="60201956", name="Wassermanngasse", diva="60201956"
        )
    }
    haltepunkte = [_stop("3953", 48.6945799, 15.5787004), _stop("3954", 48.6945799, 15.5787903)]
    gtfs = {"60201956": (48.268638, 16.452806)}

    entry = update_wl_stations.build_wl_entries(haltestellen, haltepunkte, None, None, gtfs)[0]

    assert (entry["latitude"], entry["longitude"]) == (48.268638, 16.452806)
    assert entry["in_vienna"] is True
    # ``wl_stops`` keeps mirroring the OGD export.
    stops = cast(list[dict[str, object]], entry["wl_stops"])
    assert {stop["latitude"] for stop in stops} == {48.6945799}


def test_build_wl_entries_prefers_plausible_ogd_stops_over_gtfs() -> None:
    haltestellen = {
        "60201956": update_wl_stations.Haltestelle(
            station_id="60201956", name="Wassermanngasse", diva="60201956"
        )
    }
    haltepunkte = [_stop("1", 48.2600, 16.4200)]
    gtfs = {"60201956": (48.268638, 16.452806)}

    entry = update_wl_stations.build_wl_entries(haltestellen, haltepunkte, None, None, gtfs)[0]

    assert (entry["latitude"], entry["longitude"]) == (48.26, 16.42)
