"""Raw-data recording: what each source delivered and what its fetch dropped.

The fixtures follow the real response shapes: the Wiener Linien OGD
realtime envelope (``data`` + ``message`` with ``serverTime``), the ÖBB
RSS items and the Stadt-Wien WFS FeatureCollection (``OBJECTID``,
``timeStamp``, feature ``id`` as in ``data/samples/baustellen_sample.geojson``).
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb_provider
from scripts import update_baustellen_cache
from src.providers import wl_fetch
from src.utils import raw_capture
from src.utils.files import atomic_write


@pytest.fixture
def raw_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "raw"
    monkeypatch.setattr(raw_capture, "RAW_ROOT", root)
    # The Baustellen script imports the module as ``utils.raw_capture``.
    monkeypatch.setattr(sys.modules["utils.raw_capture"], "RAW_ROOT", root)
    monkeypatch.setenv(raw_capture.RAW_CAPTURE_ENV, "1")
    return root


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------- module ----------------


def test_capture_is_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_capture, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.delenv(raw_capture.RAW_CAPTURE_ENV, raising=False)
    raw_capture.write_snapshot("wl", "newsList", {"a": 1})
    assert not (tmp_path / "raw").exists()


def test_render_is_stable_and_one_value_per_line() -> None:
    a = raw_capture.render({"b": [1, 2], "a": "Störung‮"})
    b = raw_capture.render({"a": "Störung‮", "b": [1, 2]})
    assert a == b
    assert a == '{\n "a": "Störung",\n "b": [\n  1,\n  2\n ]\n}\n'


def test_unchanged_snapshot_is_not_rewritten(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[Path] = []
    real = atomic_write

    def counting(path: Path, *args: Any, **kwargs: Any) -> Any:
        writes.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(raw_capture, "atomic_write", counting)
    raw_capture.write_snapshot("wl", "newsList", {"a": 1})
    raw_capture.write_snapshot("wl", "newsList", {"a": 1})
    raw_capture.write_snapshot("wl", "newsList", {"a": 2})
    assert len(writes) == 2
    assert _read(raw_root / "wl" / "newsList.json") == {"a": 2}


def test_oversized_snapshot_is_skipped(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_capture, "MAX_SNAPSHOT_BYTES", 50)
    raw_capture.write_snapshot("wl", "newsList", {"text": "x" * 100})
    assert not (raw_root / "wl" / "newsList.json").exists()


@pytest.mark.parametrize("source,name", [("../x", "a"), ("wl", "../../etc"), ("wl", "a/b"), ("", "a")])
def test_bad_names_are_refused_without_raising(raw_root: Path, source: str, name: str) -> None:
    raw_capture.write_snapshot(source, name, {"a": 1})
    assert not any(raw_root.parent.rglob("*.json"))


def test_drops_are_sorted_and_unique(raw_root: Path) -> None:
    raw_capture.reset_drops("oebb")
    raw_capture.note_drop("oebb", "nicht Wien-relevant", "  Linz  Hbf ")
    raw_capture.note_drop("oebb", "nicht Wien-relevant", "Linz Hbf")
    raw_capture.note_drop("oebb", "anderer Grund", "Graz")
    raw_capture.write_drops("oebb")
    assert _read(raw_root / "oebb" / "verworfen.json") == {
        "verworfen": [
            {"grund": "anderer Grund", "titel": "Graz"},
            {"grund": "nicht Wien-relevant", "titel": "Linz Hbf"},
        ]
    }


def test_write_errors_never_raise(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(raw_capture, "atomic_write", boom)
    raw_capture.write_snapshot("wl", "newsList", {"a": 1})


# ---------------- Wiener Linien ----------------


def _wl_info(name: str, title: str, *, hours: tuple[int, int] = (-1, 1), status: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    info: dict[str, Any] = {
        "refTrafficInfoCategoryId": 2,
        "name": name,
        "priority": "1",
        "owner": "WL",
        "title": title,
        "description": f"{title}. Unregelmäßige Intervalle.",
        "time": {
            "start": (now + timedelta(hours=hours[0])).strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
            "end": (now + timedelta(hours=hours[1])).strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
        },
        "relatedLines": ["13A"],
        "relatedStops": [],
        "attributes": {},
    }
    if status:
        info["attributes"]["status"] = status
    return info


def _wl_response(key: str, items: list[dict[str, Any]], server_time: str) -> dict[str, Any]:
    return {
        "data": {key: items, "trafficInfoCategories": [{"id": 2, "name": "stoerunglang"}]},
        "message": {"value": "OK", "messageCode": 1, "serverTime": server_time},
    }


def test_wl_snapshot_and_drops(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    traffic = [
        _wl_info("ma_stoerung_2", "Rettungseinsatz"),
        _wl_info("ma_stoerung_1", "Falschparker"),
        _wl_info("ma_stoerung_3", "Aufzug außer Betrieb"),
        _wl_info("ma_stoerung_4", "Verkehrsunfall", hours=(-5, -2)),
        _wl_info("ma_stoerung_5", "Polizeieinsatz", status="beendet"),
    ]
    responses = {
        "trafficInfoList": _wl_response("trafficInfos", traffic, "2026-10-04T10:00:00.000+0200"),
        "newsList": _wl_response("pois", [], "2026-10-04T10:00:01.000+0200"),
    }
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: responses[path])

    items = wl_fetch.fetch_events()

    assert {it["title"] for it in items} == {"13A: Rettungseinsatz", "13A: Falschparker"}
    snapshot = _read(raw_root / "wl" / "trafficInfoList.json")
    assert snapshot["message"] == {"messageCode": 1, "value": "OK"}
    assert [i["name"] for i in snapshot["data"]["trafficInfos"]] == [
        "ma_stoerung_1", "ma_stoerung_2", "ma_stoerung_3", "ma_stoerung_4", "ma_stoerung_5",
    ]
    assert snapshot["data"]["trafficInfoCategories"] == [{"id": 2, "name": "stoerunglang"}]
    assert _read(raw_root / "wl" / "verworfen.json")["verworfen"] == [
        {"grund": "Status inaktiv", "titel": "Polizeieinsatz"},
        {"grund": "außerhalb des Zeitraums", "titel": "Verkehrsunfall"},
        {"grund": "nur Aufzug/Fahrtreppe", "titel": "Aufzug außer Betrieb"},
    ]

    # The next call differs only in serverTime and order: no rewrite.
    before = (raw_root / "wl" / "trafficInfoList.json").read_bytes()
    responses["trafficInfoList"] = _wl_response(
        "trafficInfos", list(reversed(traffic)), "2026-10-04T10:30:00.000+0200"
    )
    wl_fetch.fetch_events()
    assert (raw_root / "wl" / "trafficInfoList.json").read_bytes() == before


def test_wl_failed_call_keeps_the_last_snapshot(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_capture.write_snapshot("wl", "newsList", {"kept": True})
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: {})
    wl_fetch.fetch_events()
    assert _read(raw_root / "wl" / "newsList.json") == {"kept": True}


# ---------------- ÖBB ----------------

OEBB_RSS = """<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0">
<channel>
<title>ÖBB Störungen</title>
<lastBuildDate>Sun, 04 Oct 2026 10:00:00 +0200</lastBuildDate>
<item>
<title><![CDATA[ Linz Hbf ↔ Wels Hbf ]]></title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?tpl=showmap_external&amp;2</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;900002</guid>
<pubDate>Sun, 04 Oct 2026 09:00:00 +0200</pubDate>
<description><![CDATA[ Wegen Bauarbeiten zwischen Linz Hbf und Wels Hbf … ]]></description>
</item>
<item>
<title><![CDATA[ Wien Meidling ↔ Wien Floridsdorf ]]></title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?tpl=showmap_external&amp;1</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;900001</guid>
<pubDate>Sun, 04 Oct 2026 08:00:00 +0200</pubDate>
<description><![CDATA[ Wegen einer Fahrzeugstörung zwischen Wien Meidling und Wien Floridsdorf … ]]></description>
</item>
</channel>
</rss>
"""


def test_oebb_snapshot_and_drops(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oebb_provider, "_fetch_xml", lambda url, timeout=25: ET.fromstring(OEBB_RSS))
    monkeypatch.setattr(
        oebb_provider, "_is_relevant", lambda title, desc: "Wien" in title or "Wien" in desc
    )

    items = oebb_provider.fetch_events()

    assert len(items) == 1
    snapshot = _read(raw_root / "oebb" / "rss.json")
    assert "lastBuildDate" not in json.dumps(snapshot)
    assert [i["guid"][-6:] for i in snapshot["items"]] == ["900001", "900002"]
    assert set(snapshot["items"][0]) == {"title", "link", "guid", "pubDate", "description"}
    drops = _read(raw_root / "oebb" / "verworfen.json")["verworfen"]
    assert drops == [{"grund": "nicht Wien-relevant", "titel": "Linz Hbf ↔ Wels Hbf"}]


# ---------------- Stadt Wien Baustellen ----------------


def _wfs(features: list[dict[str, Any]], stamp: str) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "totalFeatures": len(features),
        "numberMatched": len(features),
        "numberReturned": len(features),
        "timeStamp": stamp,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
        "features": features,
    }


def _feature(object_id: int, title: str, text: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": f"BAUSTELLENLINOGD.{object_id}",
        "geometry": {"type": "LineString", "coordinates": [[16.3255, 48.2103], [16.3262, 48.2107]]},
        "geometry_name": "SHAPE",
        "properties": {
            "OBJECTID": object_id,
            "BEZIRK": 16,
            "BEZEICHNUNG": title,
            "BEHINDERUNGSART": "Gleisbau",
            "PRESSETEXT": text,
            "OBJEKT_BEGINN": "2026-10-01Z",
            "OBJEKT_ENDE": "2026-12-20Z",
        },
    }


def test_baustellen_snapshot_and_drops(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    features = [
        _feature(7, "Thaliastraße", "Die Haltestelle der Straßenbahnlinie 2 wird verlegt."),
        _feature(3, "Hasnerstraße", "Fahrbahnsanierung, eine Spur gesperrt."),
    ]
    calls = {"n": 0}

    def fake_fetch_remote(url: str, timeout: int) -> dict[str, Any]:
        calls["n"] += 1
        return _wfs(features if "LIN" in url else [], f"2026-10-04T10:00:0{calls['n']}Z")

    cached: list[Any] = []
    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", lambda p, items: cached.append(items))

    assert update_baustellen_cache.main() == 0

    snapshot = _read(raw_root / "baustellen" / "BAUSTELLENLINOGD.json")
    assert [f["properties"]["BEZEICHNUNG"] for f in snapshot["features"]] == ["Hasnerstraße", "Thaliastraße"]
    assert snapshot["features"][0]["geometry"] == {"first_position": [16.3255, 48.2103], "type": "LineString"}
    assert "OBJECTID" not in json.dumps(snapshot)
    assert "timeStamp" not in json.dumps(snapshot)
    assert "BAUSTELLENLINOGD.3" not in json.dumps(snapshot)
    assert _read(raw_root / "baustellen" / "verworfen.json")["verworfen"] == [
        {"grund": "ohne ÖPNV-Bezug", "titel": "Hasnerstraße"}
    ]

    # Re-indexed row numbers and a new response time change nothing.
    before = (raw_root / "baustellen" / "BAUSTELLENLINOGD.json").read_bytes()
    features[0]["properties"]["OBJECTID"] = 70
    features[0]["id"] = "BAUSTELLENLINOGD.70"
    assert update_baustellen_cache.main() == 0
    assert (raw_root / "baustellen" / "BAUSTELLENLINOGD.json").read_bytes() == before


def test_baustellen_fallback_writes_no_drops(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", lambda url, timeout: None)
    monkeypatch.setattr(update_baustellen_cache, "_log_endpoint_diagnostic", lambda url, timeout: None)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", lambda p, items: None)
    update_baustellen_cache.main()
    assert not (raw_root / "baustellen").exists()
