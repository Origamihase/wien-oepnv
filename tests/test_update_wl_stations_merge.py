"""Regression tests for :mod:`scripts.update_wl_stations`."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from scripts import update_wl_stations


class _DummyResponse:
    """Minimal stand-in for :class:`requests.Response` used by the helper.

    Only exposes the surface ``_download_ogd_csv`` actually reads:
    ``status_code``, ``content`` and ``headers``. Headers default to an
    empty dict so tests that don't care about validators stay terse.
    """

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = dict(headers or {})


def _install_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    request_callable: object,
) -> list[dict[str, object]]:
    """Patch ``session_with_retries`` and ``request_safe`` to deterministic doubles.

    Returns a mutable list that captures one entry per ``request_safe``
    invocation, recording the URL and the request kwargs. Tests inspect
    this to assert that conditional headers were (or weren't) sent.
    """

    captured: list[dict[str, object]] = []

    def fake_session_with_retries(_user_agent):  # type: ignore[no-untyped-def]
        class _DummySession:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *_args):  # type: ignore[no-untyped-def]
                return False

        return _DummySession()

    def fake_request_safe(_session, url, **kwargs):  # type: ignore[no-untyped-def]
        captured.append({"url": url, "kwargs": kwargs})
        if callable(request_callable):
            return request_callable(url, kwargs)
        return request_callable

    import src.utils.http as http_utils
    monkeypatch.setattr(http_utils, "session_with_retries", fake_session_with_retries)
    monkeypatch.setattr(http_utils, "request_safe", fake_request_safe)
    return captured


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
    # Sidecar is preserved as-is on 304
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["etag"] == '"abc123"'


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
