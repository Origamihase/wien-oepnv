"""WL stations carry the lines that stop there (``wl_lines``).

Groundwork for a plausibility check of disruption messages (audit
2026-09-25, A.14): WL published "Bhf. Hütteldorf / ÖBB-Ersatzbus für <80"
under tram line 1, which does not stop at Hütteldorf. With ``wl_lines`` per
station, "does line X stop at Y?" becomes a lookup.

Source: the Wiener Linien OGD ``linien`` (LineID → LineText) and
``fahrwegverlaeufe`` (LineID, StopID per route stop) CSVs, joined on the
StopIDs ``wl_stops`` already carries. The line data in these fixtures is
made up; only the CSV layout and the StopIDs follow the OGD export.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import update_wl_stations as wl

LINIEN = (
    "LineID;LineText;SortingHelper;Realtime;MeansOfTransport\n"
    "301;U4;4;1;ptMetro\n"
    "402;47A;47;1;ptBusCity\n"
    "503;N49;149;1;ptBusNight\n"
    "604;1;1;1;ptTram\n"
)
FAHRWEGE = (
    "LineID;PatternID;StopSeqCount;StopID;Direction\n"
    "301;1;1;2811;1\n"
    "402;1;1;3242;1\n"
    "402;2;5;3242;2\n"  # a second pattern of the same line at the same stop
    "503;1;7;3242;1\n"
    "604;1;3;9999;1\n"  # tram 1 stops elsewhere
    "777;1;1;2811;1\n"  # a LineID the linien CSV does not know
)
HALTESTELLEN = (
    "DIVA;PlatformText;Municipality;MunicipalityID;Longitude;Latitude\n"
    "60200560;Bhf. Hütteldorf;Wien;49000001;16.2613392;48.1963808\n"
)
HALTEPUNKTE = (
    "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
    "2811;60200560;Bhf. Hütteldorf;Wien;49000001;16.2613392;48.1963808\n"
    "3242;60200560;Bhf. Hütteldorf S U;Wien;49000001;16.2612314;48.1979256\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _entries(tmp_path: Path, lines_by_stop: dict[str, set[str]] | None) -> list[dict[str, object]]:
    haltestellen = wl.load_haltestellen(_write(tmp_path, "haltestellen.csv", HALTESTELLEN))
    haltepunkte = wl.load_haltepunkte(_write(tmp_path, "haltepunkte.csv", HALTEPUNKTE))
    return wl.build_wl_entries(haltestellen, haltepunkte, None, lines_by_stop)


def test_line_texts_are_read_and_normalised(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "linien.csv",
        LINIEN + "705; 13a ;13;1;ptBusCity\n806;Linie <b>;0;0;ptBusCity\n907;;0;0;ptTram\n",
    )
    assert wl.load_line_texts(path) == {
        "301": "U4",
        "402": "47A",
        "503": "N49",
        "604": "1",
        "705": "13A",
    }


def test_lines_by_stop_joins_the_routes(tmp_path: Path) -> None:
    line_texts = wl.load_line_texts(_write(tmp_path, "linien.csv", LINIEN))
    lines = wl.load_lines_by_stop(_write(tmp_path, "fahrwege.csv", FAHRWEGE), line_texts)
    assert lines == {"2811": {"U4"}, "3242": {"47A", "N49"}, "9999": {"1"}}


def test_missing_line_files_are_no_error(tmp_path: Path) -> None:
    assert wl.load_line_texts(tmp_path / "missing.csv") == {}
    assert wl.load_lines_by_stop(tmp_path / "missing.csv", {"301": "U4"}) == {}
    # Without line texts the routes file is not even read.
    assert wl.load_lines_by_stop(tmp_path / "missing.csv", {}) == {}


def test_the_station_carries_the_lines_of_all_its_stops(tmp_path: Path) -> None:
    lines = {"2811": {"U4", "10"}, "3242": {"47A", "N49", "5"}, "9999": {"1"}}
    (entry,) = _entries(tmp_path, lines)
    assert entry["wl_lines"] == ["5", "10", "47A", "N49", "U4"]  # tram 1 stops elsewhere


def test_without_line_data_there_is_no_wl_lines(tmp_path: Path) -> None:
    (entry,) = _entries(tmp_path, None)
    assert "wl_lines" not in entry
    (entry,) = _entries(tmp_path, {"9999": {"1"}})  # no line at these stops
    assert "wl_lines" not in entry


def test_natural_line_order() -> None:
    lines = ["U4", "49", "5", "13A", "N49", "D", "WLB", "U1", "10"]
    assert sorted(lines, key=wl._line_sort_key) == [
        "5", "10", "13A", "49", "D", "N49", "U1", "U4", "WLB",
    ]


def test_merging_colocated_entries_unions_their_lines() -> None:
    group: list[dict[str, object]] = [
        {"name": "Wien X (WL)", "wl_diva": "60200002", "wl_stops": [], "wl_lines": ["47A", "U4"]},
        {"name": "Wien X (WL)", "wl_diva": "60200001", "wl_stops": [], "wl_lines": ["U4"]},
        {"name": "Wien X (WL)", "wl_diva": "60200003", "wl_stops": []},
    ]
    assert wl._merge_entry_group(group)["wl_lines"] == ["47A", "U4"]


def test_a_merged_non_wl_entry_carries_this_runs_lines() -> None:
    target: dict[str, object] = {"name": "Wien Hütteldorf", "aliases": [], "wl_lines": ["OLD"]}
    wl._merge_wl_payload(target, {"wl_diva": "60200560", "aliases": [], "wl_lines": ["U4"]})
    assert target["wl_lines"] == ["U4"]
    wl._merge_wl_payload(target, {"wl_diva": "60200560", "aliases": []})
    assert "wl_lines" not in target


def test_main_writes_wl_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIEN_OEPNV_AT_RECONCILE", "0")
    stations = tmp_path / "stations.json"
    stations.write_text('{"stations": []}\n', encoding="utf-8")
    code = wl.main(
        [
            "--no-download",
            "--haltestellen", str(_write(tmp_path, "haltestellen.csv", HALTESTELLEN)),
            "--haltepunkte", str(_write(tmp_path, "haltepunkte.csv", HALTEPUNKTE)),
            "--linien", str(_write(tmp_path, "linien.csv", LINIEN)),
            "--fahrwegverlaeufe", str(_write(tmp_path, "fahrwege.csv", FAHRWEGE)),
            "--vor-mapping", str(tmp_path / "no-vor-mapping.json"),
            "--stations", str(stations),
        ]
    )
    assert code == 0
    (entry,) = json.loads(stations.read_text(encoding="utf-8"))["stations"]
    assert (entry["wl_diva"], entry["wl_lines"]) == ("60200560", ["47A", "N49", "U4"])


def test_main_without_line_files_still_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIEN_OEPNV_AT_RECONCILE", "0")
    stations = tmp_path / "stations.json"
    stations.write_text('{"stations": []}\n', encoding="utf-8")
    code = wl.main(
        [
            "--no-download",
            "--haltestellen", str(_write(tmp_path, "haltestellen.csv", HALTESTELLEN)),
            "--haltepunkte", str(_write(tmp_path, "haltepunkte.csv", HALTEPUNKTE)),
            "--linien", str(tmp_path / "missing-linien.csv"),
            "--fahrwegverlaeufe", str(tmp_path / "missing-fahrwege.csv"),
            "--vor-mapping", str(tmp_path / "no-vor-mapping.json"),
            "--stations", str(stations),
        ]
    )
    assert code == 0
    (entry,) = json.loads(stations.read_text(encoding="utf-8"))["stations"]
    assert entry["wl_diva"] == "60200560"
    assert "wl_lines" not in entry


@pytest.mark.parametrize(
    ("wl_lines", "valid"),
    [
        (["47A", "N49", "U4"], True),
        (["u4"], False),
        (["U4", "U4"], False),
        ([], False),
        (["Linie 1"], False),
    ],
)
def test_the_schema_accepts_wl_lines(wl_lines: list[str], valid: bool) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(__file__).resolve().parents[1] / "docs" / "schema" / "stations.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    station: dict[str, Any] = {
        "name": "Wien Bhf. Hütteldorf (WL)",
        "in_vienna": True,
        "pendler": False,
        "aliases": ["Bhf. Hütteldorf"],
        "latitude": 48.197,
        "longitude": 16.261,
        "source": "wl",
        "wl_diva": "60200560",
        "wl_lines": wl_lines,
    }
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors({"stations": [station]}))
    assert (not errors) is valid


def test_the_pinned_ogd_files_answer_the_hutteldorf_question() -> None:
    """The committed OGD snapshots (2026-09-25) in their real layout
    (``SortingHelp`` header, CRLF): Bhf. Hütteldorf is served by the U4
    and buses, and tram 1 — the line WL filed the S80 replacement bus
    under (A.14) — stops elsewhere.
    """
    data = Path(__file__).resolve().parents[1] / "data"
    line_texts = wl.load_line_texts(data / "wienerlinien-ogd-linien.csv")
    lines_by_stop = wl.load_lines_by_stop(
        data / "wienerlinien-ogd-fahrwegverlaeufe.csv", line_texts
    )
    stations = json.loads((data / "stations.json").read_text(encoding="utf-8"))["stations"]
    (huetteldorf,) = (s for s in stations if s.get("wl_diva") == "60200560")
    stop_ids = [str(stop["stop_id"]) for stop in huetteldorf["wl_stops"]]
    lines = wl._station_lines(stop_ids, lines_by_stop)
    assert "U4" in lines
    assert "1" not in lines
    assert any("1" in served for served in lines_by_stop.values())  # tram 1 exists
