"""The manual HAFAS probe for stage 2 of the line check (A.14).

``scripts/probe_hafas_lines.py`` runs only in a manually dispatched
workflow; these tests pin what it asks and what it prints, with the HTTP
layer (:func:`src.places.hafas_client.post_mgate`) replaced.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from scripts import probe_hafas_lines as probe

LID = "A=1@O=Wien Hütteldorf@X=16261073@Y=48197391@L=1290401@"
S80 = {
    "name": "S 80",
    "nameS": "80",
    "number": "80",
    "cls": 16,
    "prodCtx": {"line": "80", "catOut": "S", "catOutL": "S-Bahn", "admin": "81____"},
}
U4 = {"name": "U4", "cls": 128, "prodCtx": {"catOut": "U"}}


def _loc_match(location: dict[str, Any] | None, products: list[Any] | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {"match": {"locL": [location] if location else []}}
    if products is not None:
        res["common"] = {"prodL": products}
    return {"svcResL": [{"meth": "LocMatch", "err": "OK", "res": res}]}


def test_loc_match_with_product_references() -> None:
    payload = _loc_match(
        {"lid": LID, "name": "Wien Hütteldorf", "extId": "1290401", "pRefL": [0, 1, 7]},
        [S80, U4],
    )
    lines, lid, ext_id = probe.summarise_loc_match(payload)
    assert (lid, ext_id) == (LID, "1290401")
    assert lines[0] == "LocMatch err=OK"
    assert "  pRefL: 3 product references" in lines
    assert any("name=S 80" in line and "ctx.catOutL=S-Bahn" in line and "cls=16" in line for line in lines)
    assert any("name=U4" in line for line in lines)  # index 7 is out of range and skipped
    assert sum(1 for line in lines if line.startswith("    ")) == 2


def test_loc_match_without_product_references() -> None:
    lines, lid, _ = probe.summarise_loc_match(_loc_match({"lid": LID, "name": "X", "extId": "1"}))
    assert "  pRefL: absent" in lines
    assert lid == LID
    assert any(line.startswith("  location keys: extId, lid, name") for line in lines)


@pytest.mark.parametrize("payload", [None, {}, {"svcResL": []}, _loc_match(None)])
def test_loc_match_without_location(payload: object) -> None:
    lines, lid, ext_id = probe.summarise_loc_match(payload)
    assert (lid, ext_id) == (None, None)
    assert lines[-1] == "  no location"


def test_board_lists_distinct_products() -> None:
    payload = {
        "svcResL": [
            {
                "meth": "StationBoard",
                "err": "OK",
                "res": {"jnyL": [{}, {}, {}], "common": {"prodL": [S80, U4, dict(S80)]}},
            }
        ]
    }
    lines = probe.summarise_board(payload, 1234)
    assert lines[0] == "StationBoard err=OK bytes=1234 journeys=3 products=3"
    assert len(lines) == 3  # the duplicate S 80 is listed once


def test_board_error_is_reported() -> None:
    payload = {"svcResL": [{"meth": "StationBoard", "err": "LOCATION", "res": {}}]}
    assert probe.summarise_board(payload, 10) == [
        "StationBoard err=LOCATION bytes=10 journeys=absent products=absent"
    ]


def test_output_is_single_line_and_sanitised() -> None:
    payload = _loc_match({"lid": LID, "name": "Evil\nName\x1b[31m", "extId": "1"})
    lines, _, _ = probe.summarise_loc_match(payload)
    assert all("\n" not in line and "\x1b" not in line for line in lines)


@pytest.mark.parametrize(
    ("today", "tuesday"),
    [
        (date(2026, 9, 25), date(2026, 9, 29)),  # Friday
        (date(2026, 9, 28), date(2026, 9, 29)),  # Monday
        (date(2026, 9, 29), date(2026, 10, 6)),  # Tuesday → next week
    ],
)
def test_the_board_window_is_the_next_tuesday(today: date, tuesday: date) -> None:
    assert probe._next_tuesday(today) == tuesday


def _fake_post(
    calls: list[tuple[list[dict[str, object]], int | None]], boards: list[object]
) -> Any:
    def fake_post(requests: list[dict[str, object]], *, max_bytes: int | None = None) -> object:
        calls.append((requests, max_bytes))
        if requests[0]["meth"] == "LocMatch":
            return _loc_match({"lid": LID, "name": "Hütteldorf (Wien)", "extId": "1191401"})
        return boards.pop(0)

    return fake_post


BOARD_OK = {"svcResL": [{"err": "OK", "res": {"jnyL": [], "common": {"prodL": [S80]}}}]}
BOARD_PARSE = {"svcResL": [{"err": "PARSE", "errTxt": "unknown field", "res": {}}]}


def test_the_board_request_has_the_hafas_client_form() -> None:
    request: Any = probe.board_request("1191401", date(2026, 9, 29))
    assert request["meth"] == "StationBoard"
    assert request["req"] == {
        "type": "DEP",
        "date": "20260929",
        "time": "000000",
        "stbLoc": {"type": "S", "lid": "A=1@L=1191401@"},
        "jnyFltrL": [{"type": "PROD", "mode": "INC", "value": "4159"}],
        "dur": 1439,
    }  # no getPasslist: the first run's board with it came back PARSE


def test_probe_asks_loc_match_then_the_days_board(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[dict[str, object]], int | None]] = []
    monkeypatch.setattr(probe, "post_mgate", _fake_post(calls, [BOARD_OK]))
    lines = probe.probe("Wien Hütteldorf", date(2026, 9, 29))
    assert [c[0][0]["meth"] for c in calls] == ["LocMatch", "StationBoard"]
    board_request: Any = calls[1][0][0]["req"]
    assert board_request["stbLoc"]["lid"] == "A=1@L=1191401@"
    assert calls[1][1] == probe.BOARD_MAX_BYTES
    assert lines[0] == "=== Wien Hütteldorf"
    assert any(line.startswith("day, rail: StationBoard err=OK") for line in lines)


def test_a_failed_board_triggers_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[dict[str, object]], int | None]] = []
    monkeypatch.setattr(probe, "post_mgate", _fake_post(calls, [BOARD_PARSE, BOARD_OK]))
    lines = probe.probe("Wien Hütteldorf", date(2026, 9, 29))
    assert len(calls) == 3
    fallback: Any = calls[2][0][0]["req"]
    assert fallback["stbLoc"]["lid"] == LID
    assert "getPasslist" not in fallback and "jnyFltrL" not in fallback
    assert any("day, rail: StationBoard err=PARSE (unknown field)" in line for line in lines)
    assert any(line.startswith("fallback: StationBoard err=OK") for line in lines)


@pytest.mark.parametrize(
    ("bits", "names"),
    [
        (4159, "ICE/RJ, IC/EC, D/EN, R/REX, S-Bahn"),
        (32, "S-Bahn"),
        (256 | 512, "U-Bahn, Straßenbahn"),
        (0, "-"),
        ("32", "?"),
    ],
)
def test_class_names(bits: object, names: str) -> None:
    assert probe._class_names(bits) == names


def test_loc_match_reports_the_product_classes() -> None:
    payload = _loc_match({"lid": LID, "name": "Hütteldorf (Wien)", "extId": "1191401", "pCls": 4479})
    lines, _, _ = probe.summarise_loc_match(payload)
    assert "  pCls=4479 (ICE/RJ, IC/EC, D/EN, R/REX, S-Bahn, Bus, U-Bahn)" in lines


def test_probe_stops_without_a_location(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_post(requests: list[dict[str, object]], **_: object) -> object:
        calls.append(requests)
        return _loc_match(None)

    monkeypatch.setattr(probe, "post_mgate", fake_post)
    probe.probe("Nirgendwo", date(2026, 9, 29))
    assert len(calls) == 1


def test_main_fails_only_when_every_station_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_probe(station: str, _day: date) -> list[str]:
        if station == "Kaputt":
            raise OSError("boom")
        return [f"=== {station}"]

    monkeypatch.setattr(probe, "probe", fake_probe)
    assert probe.main(["Kaputt", "Wien Meidling"]) == 0
    assert probe.main(["Kaputt"]) == 1
    assert "=== Kaputt: FAILED OSError: boom" in capsys.readouterr().out
