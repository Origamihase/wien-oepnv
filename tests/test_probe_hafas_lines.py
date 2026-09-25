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
    lines, lid = probe.summarise_loc_match(payload)
    assert lid == LID
    assert lines[0] == "LocMatch err=OK"
    assert "  pRefL: 3 product references" in lines
    assert any("name=S 80" in line and "ctx.catOutL=S-Bahn" in line and "cls=16" in line for line in lines)
    assert any("name=U4" in line for line in lines)  # index 7 is out of range and skipped
    assert sum(1 for line in lines if line.startswith("    ")) == 2


def test_loc_match_without_product_references() -> None:
    lines, lid = probe.summarise_loc_match(_loc_match({"lid": LID, "name": "X", "extId": "1"}))
    assert "  pRefL: absent" in lines
    assert lid == LID
    assert any(line.startswith("  location keys: extId, lid, name") for line in lines)


@pytest.mark.parametrize("payload", [None, {}, {"svcResL": []}, _loc_match(None)])
def test_loc_match_without_location(payload: object) -> None:
    lines, lid = probe.summarise_loc_match(payload)
    assert lid is None
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
    lines, _ = probe.summarise_loc_match(payload)
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


def test_probe_asks_loc_match_then_the_board(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[dict[str, object]], int | None]] = []
    board = {"svcResL": [{"err": "OK", "res": {"jnyL": [], "common": {"prodL": [S80]}}}]}

    def fake_post(requests: list[dict[str, object]], *, max_bytes: int | None = None) -> object:
        calls.append((requests, max_bytes))
        if requests[0]["meth"] == "LocMatch":
            return _loc_match({"lid": LID, "name": "Wien Hütteldorf", "extId": "1"})
        return board

    monkeypatch.setattr(probe, "post_mgate", fake_post)
    lines = probe.probe("Wien Hütteldorf", date(2026, 9, 29))
    assert [c[0][0]["meth"] for c in calls] == ["LocMatch", "StationBoard"]
    board_request: Any = calls[1][0][0]["req"]
    assert (board_request["stbLoc"]["lid"], board_request["date"], board_request["type"]) == (
        LID,
        "20260929",
        "DEP",
    )
    assert calls[1][1] == probe.BOARD_MAX_BYTES
    assert lines[0] == "=== Wien Hütteldorf"
    assert any(line.startswith("StationBoard err=OK") for line in lines)


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
