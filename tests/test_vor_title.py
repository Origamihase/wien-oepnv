import pytest
from unittest.mock import MagicMock
from collections import namedtuple

from src.providers import vor

StationInfo = namedtuple("StationInfo", ["name"])

def test_collect_from_board_adds_station_context(monkeypatch):
    # Mock station_info
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Mödling"))

    root = {
        "DepartureBoard": {
            "Message": {
                "head": "Zugausfall",
                "text": "Technischer Defekt",
                "id": "1",
            }
        }
    }

    items = vor._collect_from_board("12345", root)

    assert len(items) == 1
    title = items[0]["title"]
    # Expect: "Mödling: Zugausfall" (since no lines)
    assert title == "Mödling: Zugausfall"

def test_collect_from_board_adds_station_context_with_lines(monkeypatch):
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Baden"))

    root = {
        "DepartureBoard": {
            "Message": {
                "head": "Verspätung",
                "text": "Wegen ...",
                "products": {"Product": [{"catOutL": "S3"}]},
                "id": "2",
            }
        }
    }

    items = vor._collect_from_board("54321", root)
    title = items[0]["title"]
    # Expect: "S3: Verspätung (Baden)"
    assert title == "S3: Verspätung (Baden)"

def test_collect_from_board_skips_context_if_present(monkeypatch):
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Wien Mitte"))

    root = {
        "DepartureBoard": {
            "Message": {
                "head": "Wien Mitte: Aufzug defekt",
                "text": "...",
                "id": "3",
            }
        }
    }

    items = vor._collect_from_board("111", root)
    title = items[0]["title"]
    # Expect no double addition
    assert title == "Wien Mitte: Aufzug defekt"
    assert title.count("Wien Mitte") == 1
