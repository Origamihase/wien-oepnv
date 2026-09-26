"""Stage 3 of the line check: the lines of the German feed against the directory.

The places and distances mirror the real directory: "Wien Bhf. Hütteldorf
(WL)" lies 126 m from the ÖBB station, "Meidling Hauptstraße" 1 km from
Bahnhof Meidling, and Pasettistraße (a temporary stop in a real 37A item)
is no railway station at all.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import check_feed_lines as cl

REPO = Path(__file__).resolve().parents[1]

HUETTELDORF = {
    "bst_id": "804",
    "name": "Wien Hütteldorf",
    "aliases": ["Hütteldorf", "Bf Hütteldorf", "Hf", "490056000"],
    "latitude": 48.1964971,
    "longitude": 16.2619551,
}
HUETTELDORF_WL = {
    "name": "Wien Bhf. Hütteldorf (WL)",
    "aliases": ["Bhf. Hütteldorf"],
    "wl_lines": ["U4", "49A"],
    "latitude": 48.1976,
    "longitude": 16.2616,  # ~126 m
}
# A stop of the same area but 300 m away: not part of the station.
HUETTELDORF_FAR = {
    "name": "Wien Hütteldorf, Bujattigasse (WL)",
    "aliases": ["Hütteldorf, Bujattigasse"],
    "wl_lines": ["99A"],
    "latitude": 48.1991,
    "longitude": 16.2619,
}
MEIDLING = {
    "bst_id": "1410",
    "name": "Wien Meidling",
    "aliases": ["Meidling", "Bahnhof Meidling"],
    "latitude": 48.17475,
    "longitude": 16.33392,
}
MEIDLING_HAUPTSTRASSE = {
    "name": "Wien Meidling Hauptstraße (WL)",
    "aliases": ["Bahnhof Meidling Hauptstraße"],
    "wl_lines": ["U4", "7A"],
    "latitude": 48.1837,
    "longitude": 16.3289,  # ~1 km from Bahnhof Meidling
}
PASETTISTRASSE = {
    "name": "Wien Pasettistraße (WL)",
    "aliases": ["Pasettistraße"],
    "wl_lines": ["5A"],
    "latitude": 48.2361,
    "longitude": 16.3858,
}
ENTRIES = [HUETTELDORF, HUETTELDORF_WL, HUETTELDORF_FAR, MEIDLING, MEIDLING_HAUPTSTRASSE, PASETTISTRASSE]
OEBB_LINES = {
    "804": {"lines": {"S45": "2026-09-26", "S50": "2026-09-26"}},
    "1410": {"lines": {"S1": "2026-09-26"}},
}
PLANNED = {"stations": [{"bst_id": "804", "name": "Wien Hütteldorf", "lines": ["S80"], "until": None}]}
WL_NAMES = {"U4", "U6", "49A", "5A", "7A", "37A", "1", "99A", "S80"}
TODAY = date(2026, 9, 27)


def _directory(entries: list[dict[str, Any]] = ENTRIES, planned: object = PLANNED) -> cl.Directory:
    active, _ = cl.parse_planned(planned, TODAY)
    return cl.build_directory(entries, OEBB_LINES, active, WL_NAMES)


def _check(title: str, description: str = "", directory: cl.Directory | None = None) -> list[str]:
    return [f.text for f in cl.check_item(cl.FeedItem(title, description), directory or _directory())]


@pytest.mark.parametrize(
    ("title", "lines", "rest"),
    [
        ("7A/N65/N66: Arthaberplatz", ["7A", "N65", "N66"], "Arthaberplatz"),
        ("N68R: Quellenplatz", ["N68R"], "Quellenplatz"),
        ("Wien Hütteldorf ↔ Wien Hauptbahnhof", [], "Wien Hütteldorf ↔ Wien Hauptbahnhof"),
        ("S-Bahn Stammstrecke: Verspätungen", [], "S-Bahn Stammstrecke: Verspätungen"),
        ("7a: klein", [], "7a: klein"),
    ],
)
def test_split_title(title: str, lines: list[str], rest: str) -> None:
    assert cl.split_title(title) == (lines, rest)


def test_the_hutteldorf_item_is_reported() -> None:
    # The item that started it all (2026-09-25): WL's "ÖBB-Ersatzbus für
    # 80" surfaced under line 1, which does not serve Hütteldorf.
    findings = _check("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf [Am 26.09.2026]")
    assert findings == ["1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"]


def test_a_planned_line_confirms_a_station() -> None:
    # The S80 is replaced by buses at Hütteldorf; HAFAS does not show it.
    assert _check("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf [Am 26.09.2026]") == []


def test_an_expired_planned_line_no_longer_confirms() -> None:
    expired = {"stations": [{**PLANNED["stations"][0], "until": "2026-09-26"}]}
    directory = _directory(planned=expired)
    assert _check("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf", directory) == [
        "S80 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, U4)"
    ]
    assert cl.parse_planned(expired, TODAY) == ([], ["Wien Hütteldorf"])


def test_a_wl_stop_counts_only_within_200_m() -> None:
    assert _check("U4: Störung", "Bhf. Hütteldorf") == []  # 126 m
    assert _check("99A: Umleitung", "Bhf. Hütteldorf") == [
        "99A at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_temporary_stops_off_the_line_are_not_judged() -> None:
    # Real item, 2026-09-25: a temporary stop on another line's route.
    assert _check("37A: Busse halten Pasettistraße vor Hellwagstraße (bei Linie 5A)") == []


def test_the_longest_name_wins() -> None:
    # "Meidling Hauptstraße" is the U4 station 1 km away, not Bahnhof Meidling.
    assert _check("7A: Haltestellenverlegung", "ab Meidling Hauptstraße U") == []
    assert _check("7A: Haltestellenverlegung", "ab Bahnhof Meidling") == [
        "7A at Wien Meidling not confirmed (known there: S1)"
    ]


def test_case_and_punctuation_do_not_hide_a_station() -> None:
    assert _check("1: Umleitung", "Ab HÜTTELDORF, dann …") == [
        "1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_a_direction_is_not_a_stop() -> None:
    # Replay of 678 feed versions: "43: Stromstörung Züge halten bei Linie 9
    # Richtung Westbahnhof" named the destination, not a stop of the 43.
    assert _check("1: Umleitung", "Züge fahren Richtung Hütteldorf") == []
    assert _check("1: Umleitung", "in Ri. Bhf. Hütteldorf") == []
    assert _check("1: Umleitung", "Busse fahren (Richtung Hütteldorf) ab") == []
    assert _check("1: Umleitung", "Richtung Meidling, dann ab Hütteldorf") == [
        "1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_a_name_with_a_comma_is_matched_whole() -> None:
    # "Hütteldorf, Bujattigasse" is the WL stop 300 m away, not the station.
    assert _check("99A: Haltestellenverlegung", "Haltestelle Hütteldorf, Bujattigasse") == []


def test_a_name_at_the_end_of_a_sentence_is_found() -> None:
    assert _check("1: Umleitung", "Die Züge enden in Hütteldorf. Danach …") == [
        "1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_short_codes_and_numbers_are_no_names() -> None:
    # "Hf" is Hütteldorf's operating code, "490056000" its VOR id.
    assert _check("1: Umleitung", "Hf 490056000") == []


def test_one_line_of_several_is_enough() -> None:
    assert _check("1/U4: Umleitung", "Bhf. Hütteldorf") == []


def test_an_ambiguous_name_is_not_judged() -> None:
    other = {**MEIDLING, "bst_id": "999", "name": "Anderswo", "aliases": ["Hütteldorf"]}
    directory = cl.build_directory([*ENTRIES, other], {**OEBB_LINES, "999": {"lines": {"R9": "2026-09-26"}}}, [], WL_NAMES)
    assert _check("1: Umleitung", "Hütteldorf", directory) == []


@pytest.mark.parametrize(
    ("line", "known"),
    [("U4", True), ("S45", True), ("S80", True), ("U6E", True), ("26E", False), ("X9", False), ("E", False)],
)
def test_known_lines(line: str, known: bool) -> None:
    # U6E: replacement for the known U6. 26E is not in this test's WL list.
    assert _directory().is_known(line) is known


def test_an_unknown_line_is_reported() -> None:
    assert _check("X9: Störung") == ["line X9 is in no directory"]


def test_items_without_a_line_prefix_are_skipped() -> None:
    assert _check("Wien Hütteldorf ↔ Wien Hauptbahnhof", "Bhf. Hütteldorf, Linie X9") == []


@pytest.mark.parametrize(
    "entry",
    [
        {"bst_id": 804, "lines": ["S80"]},
        {"bst_id": "80a", "lines": ["S80"]},
        {"bst_id": "804", "lines": []},
        {"bst_id": "804", "lines": ["s80"]},
        {"bst_id": "804", "lines": ["S80"], "until": "Ende 2028"},
        {"bst_id": "804", "lines": ["S80"], "until": 2028},
        "junk",
    ],
)
def test_invalid_planned_entries_are_skipped(entry: object, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="feed_line_check"):
        assert cl.parse_planned({"stations": [entry]}, TODAY) == ([], [])
    assert "Skipping invalid planned-lines entry" in caplog.text


def test_planned_payload_shapes() -> None:
    assert cl.parse_planned(None, TODAY) == ([], [])
    assert cl.parse_planned({"stations": "x"}, TODAY) == ([], [])
    (entry,), _ = cl.parse_planned({"stations": [{"bst_id": "1", "lines": ["S1"], "until": "2026-09-27"}]}, TODAY)
    assert entry == cl.PlannedLines("1", frozenset({"S1"}), date(2026, 9, 27))  # in force on its last day


def test_load_wl_line_names(tmp_path: Path) -> None:
    csv_path = tmp_path / "linien.csv"
    csv_path.write_text('"LineID";"LineText"\n1;"U4"\n2;" 13A "\n3;""\n', encoding="utf-8")
    assert cl.load_wl_line_names(csv_path) == {"U4", "13A"}
    assert cl.load_wl_line_names(tmp_path / "missing.csv") == set()


def test_an_oversized_wl_line_list_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    csv_path = tmp_path / "linien.csv"
    csv_path.write_text('"LineID";"LineText"\n1;"U4"\n', encoding="utf-8")
    monkeypatch.setattr(cl, "MAX_CSV_BYTES", 10)
    assert cl.load_wl_line_names(csv_path) == set()


def _feed(tmp_path: Path, *items: tuple[str, str]) -> Path:
    body = "".join(f"<item><title>{t}</title><description>{d}</description></item>" for t, d in items)
    path = tmp_path / "feed.xml"
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?><rss><channel>{body}</channel></rss>', encoding="utf-8")
    return path


def _main_args(tmp_path: Path, feed: Path) -> list[str]:
    stations = tmp_path / "stations.json"
    stations.write_text(json.dumps({"stations": ENTRIES}), encoding="utf-8")
    oebb = tmp_path / "oebb.json"
    oebb.write_text(json.dumps({"stations": OEBB_LINES}), encoding="utf-8")
    planned = tmp_path / "planned.json"
    planned.write_text(json.dumps(PLANNED), encoding="utf-8")
    wl = tmp_path / "linien.csv"
    wl.write_text('"LineID";"LineText"\n' + "".join(f'{n};"{line}"\n' for n, line in enumerate(WL_NAMES)), encoding="utf-8")
    return [
        *("--feed", str(feed), "--stations", str(stations), "--oebb-lines", str(oebb)),
        *("--planned", str(planned), "--wl-lines", str(wl)),
    ]


def test_main_reports_and_leaves_everything_unchanged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    feed = _feed(
        tmp_path,
        ("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf"),
        ("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf"),
        ("Wien A ↔ Wien B", ""),
    )
    before = feed.read_bytes()
    with caplog.at_level("INFO", logger="feed_line_check"):
        assert cl.main(_main_args(tmp_path, feed)) == 0
    assert "Line check: 1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80: 1 at Wien Hütteldorf not confirmed" in caplog.text
    assert "Line check: 3 items, 2 with a line prefix, 1 findings" in caplog.text
    assert feed.read_bytes() == before


def test_main_without_a_feed_fails(tmp_path: Path) -> None:
    assert cl.main(_main_args(tmp_path, tmp_path / "missing.xml")) == 1


def test_main_rejects_an_oversized_or_broken_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    feed = _feed(tmp_path, ("1: Bhf. Hütteldorf", ""))
    args = _main_args(tmp_path, feed)
    monkeypatch.setattr(cl, "MAX_FEED_BYTES", 10)
    assert cl.main(args) == 1
    monkeypatch.undo()
    feed.write_text("<rss><channel><item>", encoding="utf-8")
    assert cl.main(args) == 1


def test_main_rejects_entity_declarations(tmp_path: Path) -> None:
    feed = tmp_path / "feed.xml"
    feed.write_text(
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]><rss><channel><item><title>&x;</title></item></channel></rss>',
        encoding="utf-8",
    )
    assert cl.main(_main_args(tmp_path, feed)) == 1


def test_the_committed_planned_lines_are_valid() -> None:
    payload = json.loads((REPO / "data" / "planned_station_lines.json").read_text(encoding="utf-8"))
    stations = json.loads((REPO / "data" / "stations.json").read_text(encoding="utf-8"))["stations"]
    directory = {e["bst_id"]: e["name"] for e in stations if e.get("bst_id")}
    entries = payload["stations"]
    active, expired = cl.parse_planned(payload, date(2026, 9, 26))
    assert len(active) + len(expired) == len(entries)  # no entry is invalid
    for entry in entries:
        assert directory.get(entry["bst_id"]) == entry["name"]


def test_the_real_directory_resolves_the_key_names() -> None:
    entries = json.loads((REPO / "data" / "stations.json").read_text(encoding="utf-8"))["stations"]
    oebb = json.loads((REPO / "data" / "oebb_station_lines.json").read_text(encoding="utf-8"))["stations"]
    planned_payload = json.loads((REPO / "data" / "planned_station_lines.json").read_text(encoding="utf-8"))
    planned, _ = cl.parse_planned(planned_payload, date(2026, 9, 26))
    directory = cl.build_directory(entries, oebb, planned, cl.load_wl_line_names(cl.DEFAULT_WL_LINES))
    texts = ("Bhf. Hütteldorf", "Meidling Hauptstraße U", "Hauptbahnhof S U", "Pasettistraße")
    names = {text: [s.name for s in directory.stations_in(text)] for text in texts}
    assert names == {
        "Bhf. Hütteldorf": ["Wien Hütteldorf"],
        "Meidling Hauptstraße U": [],
        "Hauptbahnhof S U": ["Wien Hauptbahnhof"],
        "Pasettistraße": [],
    }
    assert _check("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf", directory)
    assert not _check("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf", directory)
