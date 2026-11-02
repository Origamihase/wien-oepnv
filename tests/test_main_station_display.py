"""Main stations: "Hauptbahnhof" behind a short place, "Hbf" behind a long one.

Operator decision 2026-11-02: "Wenn der Ort länger als sieben Zeichen lang
ist, wird Hauptbahnhof abgekürzt" ("Wien Hauptbahnhof", "Linz
Hauptbahnhof", "St. Pölten Hbf"). The rule lives in
``src.utils.stations.display_name``, so it holds for every name the station
directory carries, whichever form a refresh of ``data/stations.json``
writes; the last test checks every main station of the directory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.utils.stations import display_name, main_station_names

_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "stations.json"


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("Wien Hauptbahnhof", "Wien Hauptbahnhof"),
        ("Linz Hbf", "Linz Hauptbahnhof"),
        ("Graz Hbf", "Graz Hauptbahnhof"),
        ("Villach Hbf", "Villach Hauptbahnhof"),  # seven characters: written out
        ("Salzburg Hbf", "Salzburg Hbf"),  # eight: abbreviated
        ("St. Pölten Hauptbahnhof", "St. Pölten Hbf"),
        ("Wiener Neustadt Hauptbahnhof", "Wiener Neustadt Hbf"),
        ("Wiener Neustadt Hauptbahnhof (VOR)", "Wiener Neustadt Hbf"),
        ("Wien Hauptbahnhof Ost (WL)", "Wien Hauptbahnhof Ost (WL)"),  # not a name ending in it
        ("Wien Meidling", "Wien Meidling"),
    ],
)
def test_the_place_length_decides(name: str, shown: str) -> None:
    assert display_name(name) == shown


def test_every_main_station_of_the_directory_follows_the_rule() -> None:
    data = json.loads(_DIRECTORY.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data["stations"]
    checked = 0
    for entry in entries:
        match = re.match(r"^(?P<place>.+?)\s+(?:Hauptbahnhof|Hbf\.?)$", str(entry.get("name") or ""))
        if match is None:
            continue
        place = match["place"]
        expected = f"{place} Hbf" if len(place) > 7 else f"{place} Hauptbahnhof"
        assert display_name(entry["name"]) == expected
        checked += 1
    assert checked >= 10  # the directory carries Wien, Linz, Graz, St. Pölten, …


@pytest.mark.parametrize(
    ("title", "shown"),
    [
        # Titles the ÖBB cache held before the rule, as the feed now shows them.
        ("REX50/REX51/S50: Wien Westbahnhof ↔ St. Pölten Hauptbahnhof", "REX50/REX51/S50: Wien Westbahnhof ↔ St. Pölten Hbf"),
        ("Wien Hauptbahnhof ↔ Wiener Neustadt Hauptbahnhof", "Wien Hauptbahnhof ↔ Wiener Neustadt Hbf"),
        ("Aufhebung Verkehrseinschränkung: St. Pölten Hauptbahnhof", "Aufhebung Verkehrseinschränkung: St. Pölten Hbf"),
        ("Wien Hauptbahnhof ↔ Linz Hbf", "Wien Hauptbahnhof ↔ Linz Hauptbahnhof"),
        ("Wien Hütteldorf ↔ Wien Hauptbahnhof", "Wien Hütteldorf ↔ Wien Hauptbahnhof"),
        # Words in front of the place stay; an unknown place is left alone.
        ("Bauarbeiten Graz Hbf", "Bauarbeiten Graz Hauptbahnhof"),
        ("Musterort Hauptbahnhof", "Musterort Hauptbahnhof"),
    ],
)
def test_titles_show_the_display_form(title: str, shown: str) -> None:
    assert main_station_names(title) == shown


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        # Ledger rows written before the rule read like the new ones.
        ("St. Pölten Hauptbahnhof", "St. Pölten Hbf"),
        ("Wiener Neustadt Hauptbahnhof", "Wiener Neustadt Hbf"),
        ("St. Pölten Hbf", "St. Pölten Hbf"),
        ("Wien Hauptbahnhof", "Wien Hauptbahnhof"),
        ("unbekannt", "unbekannt"),
        ("Musterort Hauptbahnhof", "Musterort Hauptbahnhof"),
    ],
)
def test_statistics_show_older_rows_in_the_display_form(stored: str, shown: str) -> None:
    from src.utils.stats import current_location_label

    assert current_location_label(stored) == shown


def test_the_dashboard_reads_ledger_rows_in_the_display_form() -> None:
    import scripts.generate_markdown_stats as stats

    rows = stats._parse_stoerung_rows(
        [
            {"timestamp": "2026-07-14T04:30:46+02:00", "provider": "ÖBB", "location_name": "St. Pölten Hauptbahnhof"},
            {"timestamp": "2026-11-02T15:00:00+01:00", "provider": "ÖBB", "location_name": "St. Pölten Hbf"},
        ]
    )
    assert {row.location_name for row in rows} == {"St. Pölten Hbf"}
