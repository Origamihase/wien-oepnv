"""A source that fails in part or answers in the wrong shape.

Before 2026-10-04 three cases went through as a good answer (measured by
feeding altered copies of the real raw data through the cache updaters and
the feed build, see ``docs/architecture.md``, "Ausfall einer Quelle"):

* one of the two WL lists or one of the two Baustellen layers failed and the
  rest was written as the complete cache (all five running WL disruptions
  left the ten places of the German feed, with exit code 0);
* a field missing from every item (WL ``title`` → internal ids as titles,
  ÖBB ``description`` → construction notices from outside Vienna);
* a failed WFS wrote the two demo sites over a cache of ten or fewer.

Fixtures follow the real response shapes (``tests/test_raw_capture.py``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb_provider
from scripts import health_check, update_baustellen_cache, update_wl_cache
from src.providers import wl_fetch
from src.utils import raw_capture, source_shape
from tests.test_raw_capture import (
    OEBB_RSS,
    _feature,
    _wfs,
    _wl_info,
    _wl_response,
)

SERVER_TIME = "2026-10-04T16:01:38.000+0200"


@pytest.fixture
def raw_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Raw capture on, into *tmp_path* (as in ``tests/test_raw_capture.py``)."""
    root = tmp_path / "raw"
    monkeypatch.setattr(raw_capture, "RAW_ROOT", root)
    # The Baustellen script imports the module as ``utils.raw_capture``.
    monkeypatch.setattr(sys.modules["utils.raw_capture"], "RAW_ROOT", root)
    monkeypatch.setenv(raw_capture.RAW_CAPTURE_ENV, "1")
    return root


def _traffic(n: int = 4) -> list[dict[str, Any]]:
    return [_wl_info(f"ma_stoerung_{i}", f"Störung {i}") for i in range(n)]


def _news(n: int = 4) -> list[dict[str, Any]]:
    return [_wl_info(f"poi_{i}", f"Hinweis {i}") for i in range(n)]


def _answers(traffic: Any, news: Any) -> dict[str, Any]:
    return {
        "trafficInfoList": traffic
        if not isinstance(traffic, list)
        else _wl_response("trafficInfos", traffic, SERVER_TIME),
        "newsList": news if not isinstance(news, list) else _wl_response("pois", news, SERVER_TIME),
    }


def _serve(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> None:
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: answers[path])


def _titles(items: list[dict[str, Any]]) -> set[str]:
    return {it["title"] for it in items}


# ---------------- source_shape ----------------


def test_missing_fields_needs_every_record_without_the_field() -> None:
    checks = {"title": (source_shape.has("title"), 3)}
    assert source_shape.missing_fields([{"title": "a"}, {}, {}], checks) == []
    assert source_shape.missing_fields([{"x": 1}, {"title": ""}, {"title": None}], checks) == ["title"]


def test_missing_fields_skips_short_answers() -> None:
    checks = {"title": (source_shape.has("title"), 3)}
    assert source_shape.missing_fields([{}, {}], checks) == []


# ---------------- Wiener Linien ----------------


def test_wl_failed_list_comes_from_its_last_good_answer(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _answers(_traffic(), _news()))
    good = _titles(wl_fetch.fetch_events())
    assert wl_fetch.fallback_parts() == []

    _serve(monkeypatch, _answers({}, _news()))
    assert _titles(wl_fetch.fetch_events()) == good
    assert wl_fetch.fallback_parts() == ["trafficInfoList"]


def test_wl_failed_list_without_last_good_answer_fails_the_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Capture off (health check, local runs): nothing to fall back to.
    monkeypatch.delenv(raw_capture.RAW_CAPTURE_ENV, raising=False)
    _serve(monkeypatch, _answers(_traffic(), {}))
    with pytest.raises(wl_fetch.SourceIncompleteError):
        wl_fetch.fetch_events()


def test_wl_both_lists_failed_is_an_outage_even_with_last_good_answers(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _answers(_traffic(), _news()))
    wl_fetch.fetch_events()
    _serve(monkeypatch, _answers({}, {}))
    with pytest.raises(wl_fetch.SourceIncompleteError):
        wl_fetch.fetch_events()


@pytest.mark.parametrize("field", ["title", "description", "time"])
def test_wl_list_without_a_field_is_no_answer(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    _serve(monkeypatch, _answers(_traffic(), _news()))
    good = _titles(wl_fetch.fetch_events())
    snapshot = (raw_root / "wl" / "trafficInfoList.json").read_bytes()

    broken = _traffic()
    for item in broken:
        item.pop(field)
    _serve(monkeypatch, _answers(broken, _news()))

    assert _titles(wl_fetch.fetch_events()) == good
    assert wl_fetch.fallback_parts() == ["trafficInfoList"]
    # The broken answer never becomes the last good one.
    assert (raw_root / "wl" / "trafficInfoList.json").read_bytes() == snapshot


def test_wl_lines_are_checked_only_on_long_lists(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    few = _traffic(4)
    for item in few:
        item.pop("relatedLines")
    _serve(monkeypatch, _answers(few, _news()))
    wl_fetch.fetch_events()
    assert wl_fetch.fallback_parts() == []

    _serve(monkeypatch, _answers(_traffic(12), _news()))
    wl_fetch.fetch_events()
    many = _traffic(12)
    for item in many:
        item.pop("relatedLines")
    _serve(monkeypatch, _answers(many, _news()))
    wl_fetch.fetch_events()
    assert wl_fetch.fallback_parts() == ["trafficInfoList"]


def test_wl_empty_list_after_a_full_one_is_no_answer(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _answers(_traffic(), _news()))
    good = _titles(wl_fetch.fetch_events())
    _serve(monkeypatch, _answers(_traffic(), []))
    assert _titles(wl_fetch.fetch_events()) == good
    assert wl_fetch.fallback_parts() == ["newsList"]


def test_wl_empty_list_after_a_nearly_empty_one_counts(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _answers(_traffic(), _news(2)))
    wl_fetch.fetch_events()
    _serve(monkeypatch, _answers(_traffic(), []))
    items = wl_fetch.fetch_events()
    assert wl_fetch.fallback_parts() == []
    assert not any(t.startswith("13A: Hinweis") for t in _titles(items))
    assert json.loads((raw_root / "wl" / "newsList.json").read_text())["data"]["pois"] == []


def test_wl_cache_updater_exit_codes(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[Any] = []
    monkeypatch.setattr(update_wl_cache, "write_cache", lambda provider, items: written.append(items))
    monkeypatch.setattr(update_wl_cache, "record_plausibility_anomalies", lambda: None)

    _serve(monkeypatch, _answers(_traffic(), _news()))
    assert update_wl_cache.main() == 0
    _serve(monkeypatch, _answers(_traffic(), {}))
    assert update_wl_cache.main() == 3
    assert len(written) == 2 and len(written[1]) == len(written[0])
    _serve(monkeypatch, _answers({}, {}))
    assert update_wl_cache.main() == 1
    assert len(written) == 2


# ---------------- ÖBB ----------------


def test_oebb_answer_without_descriptions_keeps_the_cache(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rss = ET.fromstring(OEBB_RSS)
    channel = rss.find("channel")
    assert channel is not None
    template = channel.findall("item")[1]
    for _ in range(2):
        channel.append(ET.fromstring(ET.tostring(template)))
    monkeypatch.setattr(oebb_provider, "_fetch_xml", lambda url, timeout=25: rss)
    assert oebb_provider.fetch_events()
    snapshot = (raw_root / "oebb" / "rss.json").read_bytes()

    for item in channel.findall("item"):
        description = item.find("description")
        assert description is not None
        item.remove(description)
    assert oebb_provider.fetch_events() == []
    assert (raw_root / "oebb" / "rss.json").read_bytes() == snapshot


def test_oebb_empty_answer_keeps_the_last_snapshot(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oebb_provider, "_fetch_xml", lambda url, timeout=25: ET.fromstring(OEBB_RSS))
    oebb_provider.fetch_events()
    snapshot = (raw_root / "oebb" / "rss.json").read_bytes()
    empty = ET.fromstring("<rss><channel><title>ÖBB</title></channel></rss>")
    monkeypatch.setattr(oebb_provider, "_fetch_xml", lambda url, timeout=25: empty)
    assert oebb_provider.fetch_events() == []
    assert (raw_root / "oebb" / "rss.json").read_bytes() == snapshot


# ---------------- Stadt Wien Baustellen ----------------

_LIN = [
    _feature(1, "Thaliastraße", "Die Haltestelle der Straßenbahnlinie 2 wird verlegt."),
    _feature(2, "Hernalser Hauptstraße", "Die Haltestelle der Straßenbahnlinie 43 wird verlegt."),
    _feature(3, "Gablenzgasse", "Die Haltestelle der Buslinie 48A wird verlegt."),
]
_PKT = [
    _feature(4, "Ottakringer Straße", "Die Haltestelle der Straßenbahnlinie 2 wird verlegt."),
    _feature(5, "Wattgasse", "Die Haltestelle der Buslinie 10A wird verlegt."),
    _feature(6, "Koppstraße", "Die Haltestelle der Buslinie 48A wird verlegt."),
]


def _serve_layers(
    monkeypatch: pytest.MonkeyPatch, lin: Any, pkt: Any, written: list[list[Any]] | None = None
) -> list[list[Any]]:
    """Serve the two layers (a list of features, or ``None`` for a failed fetch)."""

    def fake_fetch_remote(url: str, timeout: int) -> Any:
        layer = lin if "LIN" in url else pkt
        return _wfs(layer, "2026-10-04T16:01:38Z") if isinstance(layer, list) else layer

    written = [] if written is None else written
    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", lambda p, items: written.append(items))
    monkeypatch.setattr(update_baustellen_cache, "_log_endpoint_diagnostic", lambda *a, **k: None)
    monkeypatch.setattr(update_baustellen_cache, "_cache_exists", lambda: True)
    return written


def _guids(items: list[Any]) -> set[str]:
    return {it["guid"] for it in items}


def test_baustellen_failed_layer_comes_from_its_last_good_answer(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = _serve_layers(monkeypatch, _LIN, _PKT)
    assert update_baustellen_cache.main() == 0
    _serve_layers(monkeypatch, None, _PKT, written)
    assert update_baustellen_cache.main() == 3
    assert _guids(written[1]) == _guids(written[0])


def test_baustellen_failed_layer_without_last_good_answer_keeps_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(raw_capture.RAW_CAPTURE_ENV, raising=False)
    written = _serve_layers(monkeypatch, None, _PKT)
    assert update_baustellen_cache.main() == 1
    assert written == []


@pytest.mark.parametrize("field", ["OBJEKT_BEGINN", "OBJEKT_ENDE", "PRESSETEXT", "geometry"])
def test_baustellen_layer_without_a_field_is_no_answer(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    written = _serve_layers(monkeypatch, _LIN, _PKT)
    assert update_baustellen_cache.main() == 0
    broken = json.loads(json.dumps(_LIN))
    for feature in broken:
        (feature if field == "geometry" else feature["properties"]).pop(field)
    _serve_layers(monkeypatch, broken, _PKT, written)
    assert update_baustellen_cache.main() == 3
    assert _guids(written[1]) == _guids(written[0])


def test_baustellen_empty_layer_after_a_full_one_is_no_answer(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = _serve_layers(monkeypatch, _LIN, _PKT)
    assert update_baustellen_cache.main() == 0
    _serve_layers(monkeypatch, [], _PKT, written)
    assert update_baustellen_cache.main() == 3
    assert _guids(written[1]) == _guids(written[0])


def test_baustellen_all_layers_failed_keeps_the_cache_even_with_last_good_answers(
    raw_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_layers(monkeypatch, _LIN, _PKT)
    assert update_baustellen_cache.main() == 0
    written = _serve_layers(monkeypatch, None, None)
    assert update_baustellen_cache.main() == 1
    assert written == []


# ---------------- health check ----------------


def test_health_check_names_a_partial_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    class Done:
        returncode = 3
        stdout = ""
        stderr = "WARNING Baustellen: Layer BAUSTELLENLINOGD nicht abrufbar – letzte gute Antwort verwendet."

    monkeypatch.setattr("scripts.health_check.subprocess.run", lambda *a, **k: Done())
    check = health_check.check_source("Baustellen", "update_baustellen_cache.py")
    assert not check.ok
    assert "Teil der Quelle" in check.summary
