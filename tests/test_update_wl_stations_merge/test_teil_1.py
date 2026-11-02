"""Teil 1 der Tests aus tests/test_update_wl_stations_merge.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from scripts import update_wl_stations

from ._support import (
    _DummyResponse,
    _install_fake_http,
    _read_entries,
)


def test_download_ogd_csv_returns_false_on_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the WL OGD endpoint is unreachable the helper degrades gracefully.

    Sandboxed environments may not have network access; the download must
    return ``False`` (without raising) so that the existing local CSV
    files keep the pipeline functional.
    """
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"

    def boom(_url, _kwargs):  # type: ignore[no-untyped-def]
        raise OSError("simulated network failure")

    _install_fake_http(monkeypatch, boom)

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

    response = _DummyResponse(
        status_code=200,
        content=payload,
        headers={"ETag": '"abc123"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
    )
    captured = _install_fake_http(monkeypatch, response)

    ok = update_wl_stations._download_ogd_csv(
        "https://example.test/wl.csv", target
    )
    assert ok is True
    assert target.read_bytes() == payload
    # First call: no validators on disk, so no conditional headers
    kwargs = cast(dict[str, object], captured[0]["kwargs"])
    first_headers = cast(dict[str, str], kwargs["headers"])
    assert "If-None-Match" not in first_headers
    assert "If-Modified-Since" not in first_headers
    # Sidecar is created with the returned validators
    sidecar = update_wl_stations._cache_sidecar_path(target)
    assert sidecar.exists()
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["etag"] == '"abc123"'
    assert sidecar_payload["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert sidecar_payload["checked_at"] == sidecar_payload["fetched_at"]


def test_download_ogd_csv_sends_conditional_headers_when_sidecar_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second invocation must send ``If-None-Match`` / ``If-Modified-Since``
    derived from the sidecar persisted by the previous run."""
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"
    target.write_bytes(b'"HALTESTELLEN_ID";"NAME"\n')
    sidecar = update_wl_stations._cache_sidecar_path(target)
    sidecar.write_text(
        json.dumps(
            {
                "version": 1,
                "etag": '"abc123"',
                "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
                "fetched_at": "2026-05-22T16:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    response = _DummyResponse(status_code=304)
    captured = _install_fake_http(monkeypatch, response)

    ok = update_wl_stations._download_ogd_csv(
        "https://example.test/wl.csv", target
    )

    assert ok is True
    kwargs = cast(dict[str, object], captured[0]["kwargs"])
    headers = cast(dict[str, str], kwargs["headers"])
    assert headers["If-None-Match"] == '"abc123"'
    assert headers["If-Modified-Since"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    # 304 leaves the existing target untouched (bytes-identical)
    assert target.read_bytes() == b'"HALTESTELLEN_ID";"NAME"\n'
    # The validators survive a 304; only ``checked_at`` records the good
    # answer, so the health check can tell a quiet source from a dead one.
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["etag"] == '"abc123"'
    assert sidecar_payload["fetched_at"] == "2026-05-22T16:00:00+00:00"
    assert sidecar_payload["checked_at"] > "2026-05-22T16:00:00+00:00"


def test_download_ogd_csv_304_with_missing_target_clears_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the sidecar lies about the local copy (file missing) the helper
    must drop the stale sidecar and report failure so the next run does a
    full GET."""
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"
    sidecar = update_wl_stations._cache_sidecar_path(target)
    # Create a sidecar but NOT the target. Because the helper checks for
    # target existence before considering the sidecar valid, no
    # conditional headers are sent — but the server may still respond
    # 304 (e.g. via an upstream cache). Simulate that hostile-shape edge
    # case so the recovery path is exercised.
    sidecar.write_text(
        json.dumps({"version": 1, "etag": '"x"', "last_modified": ""}),
        encoding="utf-8",
    )

    response = _DummyResponse(status_code=304)
    _install_fake_http(monkeypatch, response)

    ok = update_wl_stations._download_ogd_csv(
        "https://example.test/wl.csv", target
    )

    assert ok is False
    assert not target.exists()
    assert not sidecar.exists(), "Stale sidecar must be cleared on 304-without-target"


def test_download_ogd_csv_refreshes_sidecar_on_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 response with new validators must overwrite the existing sidecar."""
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"
    target.write_bytes(b"stale-payload\n")
    sidecar = update_wl_stations._cache_sidecar_path(target)
    sidecar.write_text(
        json.dumps({"version": 1, "etag": '"old"', "last_modified": ""}),
        encoding="utf-8",
    )

    fresh_payload = b'"DIVA";"PlatformText"\n"60201076";"Karlsplatz"\n'
    response = _DummyResponse(
        status_code=200,
        content=fresh_payload,
        headers={
            "ETag": '"new-etag"',
            "Last-Modified": "Thu, 02 Jan 2025 00:00:00 GMT",
        },
    )
    _install_fake_http(monkeypatch, response)

    ok = update_wl_stations._download_ogd_csv(
        "https://example.test/wl.csv", target
    )

    assert ok is True
    assert target.read_bytes() == fresh_payload
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["etag"] == '"new-etag"'
    assert sidecar_payload["last_modified"] == "Thu, 02 Jan 2025 00:00:00 GMT"


def test_download_ogd_csv_skips_sidecar_when_no_validators_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 with neither ETag nor Last-Modified must NOT create an empty
    sidecar (it would force a full GET next time anyway)."""
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"

    response = _DummyResponse(
        status_code=200, content=b"row\n", headers={}
    )
    _install_fake_http(monkeypatch, response)

    ok = update_wl_stations._download_ogd_csv(
        "https://example.test/wl.csv", target
    )
    assert ok is True
    assert target.read_bytes() == b"row\n"
    assert not update_wl_stations._cache_sidecar_path(target).exists()


def test_read_cache_validators_ignores_corrupt_sidecar(
    tmp_path: Path,
) -> None:
    """A corrupted JSON sidecar must be treated as missing (return ``{}``)
    so the next fetch falls back to an unconditional GET."""
    target = tmp_path / "wienerlinien-ogd-haltestellen.csv"
    target.write_bytes(b"")
    sidecar = update_wl_stations._cache_sidecar_path(target)
    sidecar.write_text("{not valid json", encoding="utf-8")

    assert update_wl_stations._read_cache_validators(target) == {}


def test_merge_into_stations_refuses_to_delete_wl_on_empty_entries(
    stations_path: Path,
) -> None:
    """Empty ``wl_entries`` must NOT strip existing WL stations (data-loss floor).

    An empty entry set only happens when the OGD CSV load failed; the merge
    must keep the committed file rather than silently wipe the WL layer.
    """
    stations_path.write_text(
        json.dumps(
            [
                {"name": "Karlsplatz", "wl_diva": "60201076", "source": "wl"},
                {"name": "Wien Hbf", "bst_id": "900100", "source": "oebb"},
            ]
        ),
        encoding="utf-8",
    )

    update_wl_stations.merge_into_stations(stations_path, [])

    merged = _read_entries(stations_path)
    # Both entries preserved — the WL station was not deleted.
    assert len(merged) == 2
    assert {e.get("source") for e in merged} == {"wl", "oebb"}


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


def test_merge_into_existing_google_places_stub_via_wl_diva_index(
    stations_path: Path,
) -> None:
    """The 2026-05-16 PR #1539 fix: a WL payload whose ``name`` doesn't
    normalise to the existing entry's ``name`` (Innenstadt-U-Bahn pattern
    — payload ``Wien Herrengasse (WL)`` vs existing ``Herrengasse``) must
    still merge via the new ``wl_diva`` index instead of producing a
    duplicate entry.

    Pre-fix, ``_normalize_key`` rendered the two names as ``herrengasse``
    vs ``wienherrengassewl`` so the name-index lookup missed, ``vor_id`` /
    ``bst_id`` were both absent, and the WL payload landed in
    ``unmatched`` — creating the ten Innenstadt-U-Bahn DIVA duplicates.
    """
    stations_path.write_text(
        json.dumps(
            [
                {
                    "name": "Herrengasse",
                    "wl_diva": "60200506",
                    "_google_place_id": "ChIJBa9sJZgHbUcRrekncb64g2M",
                    "_types": ["subway_station"],
                    "aliases": ["Herrengasse", "Bahnhof Herrengasse"],
                    "latitude": 48.2096482,
                    "longitude": 16.36638,
                    "in_vienna": True,
                    "pendler": False,
                    "source": "google_places",
                }
            ]
        ),
        encoding="utf-8",
    )

    wl_entries = [
        {
            "name": "Wien Herrengasse (WL)",
            "wl_diva": "60200506",
            "aliases": ["Wien Herrengasse (WL)", "Herrengasse U"],
            "wl_stops": [
                {"stop_id": "2938", "name": "Herrengasse U"},
                {"stop_id": "4907", "name": "Herrengasse"},
            ],
            "latitude": 48.20975,
            "longitude": 16.365304,
            "in_vienna": True,
            "pendler": False,
            "source": "wl",
        }
    ]

    update_wl_stations.merge_into_stations(stations_path, wl_entries)

    merged = _read_entries(stations_path)

    # The critical invariant: exactly ONE entry, not two.
    assert len(merged) == 1, (
        "WL payload must merge into the google_places stub via wl_diva, "
        f"got {len(merged)} entries"
    )
    entry = merged[0]
    assert entry["wl_diva"] == "60200506"
    assert entry["source"] == "google_places,wl"
    # Google Places identity bits survive the merge.
    assert entry["_google_place_id"] == "ChIJBa9sJZgHbUcRrekncb64g2M"
    assert entry["_types"] == ["subway_station"]
    # WL payload contributes wl_stops + new aliases.
    assert entry["wl_stops"] == wl_entries[0]["wl_stops"]
    from typing import cast
    aliases = set(cast(list[str], entry["aliases"]))
    assert {"Herrengasse", "Wien Herrengasse (WL)"} <= aliases


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


def test_build_wl_entries_does_not_emit_stop_ids_as_aliases(
    tmp_path: Path,
) -> None:
    """``stop_id`` (a small in-CSV row counter in the canonical
    OGD-Echtzeit schema, or an 8-digit RBL in the legacy proxy schema)
    is reachable via the structured ``wl_stops[].stop_id`` field. It is
    not added to ``aliases`` to avoid cross-station-id collisions with
    other entries' ``bst_id`` / ``wl_diva``.
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
    assert "1660" not in aliases
    from typing import cast as _cast
    wl_stops = _cast(list[dict[str, object]], entries[0]["wl_stops"])
    assert wl_stops[0]["stop_id"] == "1660", (
        "stop_id must still be exposed via the structured wl_stops field."
    )


def test_build_wl_entries_does_not_emit_synthetic_bst_id_or_code(
    tmp_path: Path,
) -> None:
    """WL-only entries no longer carry synthetic ``bst_id`` / ``bst_code``
    derived from the DIVA (the prior ``9{DIVA}`` and ``WL-{tok[:3]}``
    heuristics caused alias-collision and bst_code-uniqueness failures
    at production scale). The canonical WL identifier is the
    structured ``wl_diva`` field; downstream lookup via
    ``src/utils/stations._station_lookup`` already adds wl_diva as an
    identity-class alias on its own.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60200657;Karlsplatz;Wien;49000001;16.369;48.201\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "1;60200657;Karlsplatz;Wien;49000001;16.369;48.201\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    entry = entries[0]
    assert "bst_id" not in entry, (
        "WL-only entries must not carry a synthetic bst_id — it would "
        "collide with ÖBB bst_id values via cross-station-id checks."
    )
    assert "bst_code" not in entry, (
        "WL-only entries must not carry a synthetic bst_code — the "
        "first-3-letter truncation produced collisions at scale "
        "(e.g. WL-ABS for both Absbergbrücke and Absberggasse)."
    )
    assert entry["wl_diva"] == "60200657", (
        "The canonical WL identifier must still live in the wl_diva "
        "field — only the synthetic bst_id / bst_code mirrors are gone."
    )


def test_build_wl_entries_does_not_duplicate_wl_diva_into_aliases(
    tmp_path: Path,
) -> None:
    """The ``wl_diva`` value is the structured WL identifier and is
    indexed as an identity-class alias by
    ``src/utils/stations._station_lookup``. Duplicating it into the
    ``aliases`` list is both redundant and dangerous: Wiener Linien has
    renumbered DIVAs at least once (``60201076`` was Karlsplatz before
    PR #1442 and is Ratzenhofergasse today), so a stale ``aliases``
    copy from a prior cron tick collides with another entry's current
    ``wl_diva`` via ``_find_cross_station_id_conflicts``.
    """
    haltestellen_path = tmp_path / "haltestellen.csv"
    haltestellen_path.write_text(
        "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "60200657;Karlsplatz;Wien;49000001;16.369;48.201\n",
        encoding="utf-8",
    )
    haltepunkte_path = tmp_path / "haltepunkte.csv"
    haltepunkte_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "1;60200657;Karlsplatz;Wien;49000001;16.369;48.201\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    aliases = cast(list[str], entries[0]["aliases"])
    assert "60200657" not in aliases, (
        "The wl_diva value must not appear verbatim in aliases — "
        "the structured wl_diva field carries it instead."
    )


def test_build_wl_entries_replaces_both_direction_markers(tmp_path: Path) -> None:
    """WL ``StopText`` direction markers ('>' for "Richtung", '<' for
    "Aus Richtung") are both in the stations validator's
    ``_UNSAFE_CHARS_RE``. Without sanitisation, ``_alias_variants``
    propagates them into every prefix/suffix permutation ('Bf Brünner
    Str. <', 'Seestadt > Bahnhof', …) and each one trips
    ``_find_security_issues`` → auto-quarantine. The fix replaces '>'
    with U+2192 (→) and '<' with U+2190 (←); both are typographically
    correct and outside the unsafe-char regex.
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
        "1;60201076;Karlsplatz U > Reumannplatz;Wien;49000001;"
        "16.369450;48.198680\n"
        "2;60201076;Karlsplatz U < Leopoldau;Wien;49000001;"
        "16.369450;48.198680\n",
        encoding="utf-8",
    )

    haltestellen = update_wl_stations.load_haltestellen(haltestellen_path)
    haltepunkte = update_wl_stations.load_haltepunkte(haltepunkte_path)
    entries = update_wl_stations.build_wl_entries(haltestellen, haltepunkte)

    assert len(entries) == 1
    aliases = cast(list[str], entries[0]["aliases"])
    assert not any(">" in a for a in aliases), (
        "No alias may contain '>' — it is in the validator's unsafe-char regex."
    )
    assert not any("<" in a for a in aliases), (
        "No alias may contain '<' — it is in the validator's unsafe-char regex."
    )
    assert any("→" in a for a in aliases), "U+2192 (→) replaces '>'."
    assert any("←" in a for a in aliases), "U+2190 (←) replaces '<'."
