"""Regression tests for :mod:`scripts.update_wl_stations`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from scripts import update_wl_stations


def test_download_ogd_csv_returns_false_on_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the WL OGD endpoint is unreachable the helper degrades gracefully.

    Sandboxed environments may not have network access; the download must
    return ``False`` (without raising) so that the existing local CSV
    files keep the pipeline functional.
    """
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"

    def fake_session_with_retries(_user_agent):  # type: ignore[no-untyped-def]
        class _DummySession:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *_args):  # type: ignore[no-untyped-def]
                return False

        return _DummySession()

    def fake_fetch(_session, _url, *, timeout):  # type: ignore[no-untyped-def]
        raise OSError("simulated network failure")

    import src.utils.http as http_utils
    monkeypatch.setattr(http_utils, "session_with_retries", fake_session_with_retries)
    monkeypatch.setattr(http_utils, "fetch_content_safe", fake_fetch)

    ok = update_wl_stations._download_ogd_csv(
        "https://example.invalid/wl.csv", target
    )
    assert ok is False
    assert not target.exists()


def test_download_ogd_csv_writes_target_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"
    payload = b'"HALTESTELLEN_ID";"NAME";"DIVA"\n"1001";"Karlsplatz";"60201076"\n'

    def fake_session_with_retries(_user_agent):  # type: ignore[no-untyped-def]
        class _DummySession:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *_args):  # type: ignore[no-untyped-def]
                return False

        return _DummySession()

    def fake_fetch(_session, _url, *, timeout):  # type: ignore[no-untyped-def]
        return payload

    import src.utils.http as http_utils
    monkeypatch.setattr(http_utils, "session_with_retries", fake_session_with_retries)
    monkeypatch.setattr(http_utils, "fetch_content_safe", fake_fetch)

    ok = update_wl_stations._download_ogd_csv(
        "https://example.test/wl.csv", target
    )
    assert ok is True
    assert target.read_bytes() == payload


@pytest.fixture()
def stations_path(tmp_path: Path) -> Path:
    path = tmp_path / "stations.json"
    path.write_text("[]", encoding="utf-8")
    return path


def _read_entries(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return cast(list[dict[str, object]], data.get("stations", []))
    return cast(list[dict[str, object]], data)


def test_merge_wl_data_into_existing_vor_entry(stations_path: Path) -> None:
    stations_path.write_text(
        json.dumps(
            [
                {
                    "name": "Wien Karlsplatz",
                    "vor_id": "490065700",
                    "bst_id": "900101",
                    "aliases": ["Wien Karlsplatz"],
                    "source": "vor",
                }
            ]
        ),
        encoding="utf-8",
    )

    wl_entries = [
        {
            "name": "Wien Karlsplatz (WL)",
            "vor_id": "490065700",
            "aliases": ["Karlsplatz", "Wien Karlsplatz"],
            "wl_diva": "60201076",
            "wl_stops": [
                {
                    "stop_id": "60201076",
                    "name": "Karlsplatz U (Richtung Reumannplatz)",
                }
            ],
            "source": "wl",
        }
    ]

    update_wl_stations.merge_into_stations(stations_path, wl_entries)

    merged = _read_entries(stations_path)
    assert len(merged) == 1
    entry = merged[0]
    assert entry["source"] == "vor,wl"
    assert entry["wl_diva"] == "60201076"
    assert entry["wl_stops"] == wl_entries[0]["wl_stops"]
    from typing import cast
    assert set(cast(list[str], entry["aliases"])) == {"Karlsplatz", "Wien Karlsplatz"}

    update_wl_stations.merge_into_stations(stations_path, wl_entries)
    rerun = _read_entries(stations_path)
    assert rerun == merged


def test_unmatched_wl_entry_is_appended(stations_path: Path) -> None:
    wl_entries = [
        {
            "name": "Wien Neue Station (WL)",
            "aliases": ["Neue Station"],
            "wl_diva": "60209999",
            "wl_stops": [],
            "source": "wl",
        }
    ]

    update_wl_stations.merge_into_stations(stations_path, wl_entries)

    merged = _read_entries(stations_path)
    assert len(merged) == 1
    entry = merged[0]
    assert entry["source"] == "wl"
    assert entry["wl_diva"] == "60209999"
    assert entry["aliases"] == ["Neue Station"]


def test_load_haltestellen_parses_legacy_proxy_schema(tmp_path: Path) -> None:
    """The pre-2026-05 ``data.wien.gv.at`` proxy CSV used the
    ``HALTESTELLEN_ID;TYP;DIVA;NAME;…WGS84_LAT;WGS84_LON`` column
    layout. The fuzzy-key loader must keep parsing it so a future
    operator can still feed a pinned legacy snapshot through the
    pipeline without modification.
    """
    csv_path = tmp_path / "haltestellen-legacy.csv"
    csv_path.write_text(
        "﻿\"HALTESTELLEN_ID\";\"TYP\";\"DIVA\";\"NAME\";"
        "\"GEMEINDE\";\"WGS84_LAT\";\"WGS84_LON\"\n"
        "1085613576;\"stop\";60200120;\"Belvederegasse\";\"Wien\";"
        "48.1901468;16.3719577\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(csv_path)

    assert len(haltestellen) == 1
    halt = haltestellen["1085613576"]
    assert halt.name == "Belvederegasse"
    assert halt.diva == "60200120"


def test_load_haltestellen_parses_realtime_ogd_schema(tmp_path: Path) -> None:
    """The canonical ``wienerlinien.at/ogd_realtime/doku/ogd/`` CSV
    (post PR #1442) collapses station_id and diva onto a single
    ``DIVA`` column and renames ``NAME`` → ``PlatformText``. Without
    this lookup path, the production cron run on 2026-05-11 logged
    ``Found 0 haltestellen`` and emitted zero WL entries despite a
    successful 126 KiB download.
    """
    csv_path = tmp_path / "haltestellen-realtime.csv"
    csv_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60200001;Schrankenberggasse;Wien;49000001;16.3898073;48.1738011\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(csv_path)

    assert len(haltestellen) == 1
    halt = haltestellen["60200001"]
    assert halt.name == "Schrankenberggasse"
    assert halt.diva == "60200001"


def test_load_haltepunkte_parses_realtime_ogd_schema(tmp_path: Path) -> None:
    """The canonical OGD-Echtzeit haltepunkte CSV exposes
    ``StopID;DIVA;StopText;…;Longitude;Latitude`` instead of the
    legacy ``HALTESTELLEN_ID;…;STOP_ID;NAME;…;WGS84_*`` layout. The
    fuzzy-key fallback wires ``StopText`` → ``name`` and ``DIVA`` →
    ``station_id`` so build_wl_entries can join against
    ``load_haltestellen`` on the new canonical key.
    """
    csv_path = tmp_path / "haltepunkte-realtime.csv"
    csv_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "2;60201421;Venediger Au;Wien;49000001;16.3965357;48.2179090\n",
        encoding="utf-8",
    )

    haltepunkte = update_wl_stations.load_haltepunkte(csv_path)

    assert len(haltepunkte) == 1
    halt = haltepunkte[0]
    assert halt.station_id == "60201421"
    assert halt.stop_id == "2"
    assert halt.name == "Venediger Au"
    assert halt.latitude == pytest.approx(48.2179090)
    assert halt.longitude == pytest.approx(16.3965357)


def test_build_wl_entries_joins_realtime_schema(tmp_path: Path) -> None:
    """End-to-end smoke: hand the realtime schema to load_haltestellen
    and load_haltepunkte, then call build_wl_entries with the resulting
    dataclasses. The DIVA-on-DIVA join must produce a real entry —
    pre-fix this returned 0 because the legacy
    ``HALTESTELLEN_ID``-only lookup failed silently on the new schema.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60201076;Karlsplatz;Wien;49000001;16.369450;48.198680\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "1;60201076;Karlsplatz U (Richtung Reumannplatz);Wien;49000001;"
        "16.369450;48.198680\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["wl_diva"] == "60201076"
    assert entry["in_vienna"] is True
    assert entry["pendler"] is False
    from typing import cast
    stops = cast(list[dict[str, object]], entry["wl_stops"])
    assert len(stops) == 1
    assert stops[0]["stop_id"] == "1"


def test_build_wl_entries_skips_short_stop_ids_to_avoid_oebb_collision(
    tmp_path: Path,
) -> None:
    """The canonical wienerlinien.at OGD-Echtzeit ``StopID`` column
    collapsed from an 8-digit RBL-Nummer (legacy proxy) to a small
    in-CSV row counter (1, 2, 3, …, 1660, …). Adding those tiny values
    as aliases collides with ÖBB ``bst_id`` values like 1660 (Parndorf)
    and trips ``_find_cross_station_id_conflicts`` → auto-quarantine.
    The 2026-05-11 cron tick after PR #1444 confirmed 1442 such
    collisions out of 1449 quarantined stations. The length filter
    (``≥6``) keeps the legacy 8-digit RBL alias contract intact while
    suppressing the new schema's row-counter values.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60200788;Liesing;Wien;49000001;16.290;48.137\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "1660;60200788;Liesing;Wien;49000001;16.290;48.137\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    aliases = cast(list[str], entries[0]["aliases"])
    assert "1660" not in aliases, (
        "Short StopID counter values must not leak into aliases — they "
        "would collide with ÖBB bst_id values."
    )


def test_build_wl_entries_keeps_legacy_rbl_alias(tmp_path: Path) -> None:
    """The legacy ``data.wien.gv.at`` proxy CSV used STOP_ID == RBL-Nummer
    (8-digit), which is a semantically valuable cross-system identifier.
    The length-based filter (``≥6 chars``) must keep these long IDs in
    the alias set so existing realtime-anchor lookups continue to work.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "\"HALTESTELLEN_ID\";\"NAME\";\"DIVA\"\n"
        "\"1001\";\"Karlsplatz\";\"60201076\"\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "\"HALTEPUNKT_ID\";\"HALTESTELLEN_ID\";\"STOP_ID\";\"NAME\";"
        "\"WGS84_LAT\";\"WGS84_LON\"\n"
        "\"1\";\"1001\";\"60201076\";\"Karlsplatz U\";\"48.198680\";\"16.369450\"\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    aliases = cast(list[str], entries[0]["aliases"])
    assert "60201076" in aliases, (
        "Long stop_id values (legacy 8-digit RBL-Nummer) must remain "
        "in the alias set — they are valuable cross-system identifiers."
    )


def test_build_wl_entries_replaces_unsafe_direction_marker(tmp_path: Path) -> None:
    """WL ``StopText`` direction markers like 'Karlsplatz U > Reumannplatz'
    contain '>', which is in the stations validator's
    ``_UNSAFE_CHARS_RE`` (XSS / HTML metacharacter set). Without
    sanitisation, ``_alias_variants`` propagates the '>' into every
    alias permutation ('Bf Seestadt >', 'Seestadt > Bahnhof', …) and
    each one trips ``_find_security_issues`` → auto-quarantine. The
    fix replaces '>' with U+2192 (→), which is typographically correct
    for "Richtung" and outside the unsafe-char regex.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60201076;Karlsplatz;Wien;49000001;16.369450;48.198680\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60201076;60201076;Karlsplatz U > Reumannplatz;Wien;49000001;"
        "16.369450;48.198680\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    entry = entries[0]
    aliases = cast(list[str], entry["aliases"])
    assert not any(">" in a for a in aliases), (
        "No alias may contain '>' — it is in the validator's unsafe-char regex."
    )
    assert any("→" in a for a in aliases), (
        "The direction marker must be replaced with U+2192 (→), not stripped."
    )


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
