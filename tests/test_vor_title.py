from collections import namedtuple

import pytest
from src.providers import vor

StationInfo = namedtuple("StationInfo", ["name", "in_vienna"])

def test_collect_from_board_adds_station_context(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock station_info
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Mödling", in_vienna=False))

    root = {
        "DepartureBoard": {
            "Message": {
                "head": "Zugausfall",
                "text": "Technischer Defekt (Wien)",
                "id": "1",
            }
        }
    }

    items = vor._collect_from_board("12345", root)

    assert len(items) == 1
    title = items[0]["title"]
    # Expect: "Mödling: Zugausfall" (since no lines)
    assert title == "Mödling: Zugausfall"

def test_collect_from_board_adds_station_context_with_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Baden", in_vienna=False))

    root = {
        "DepartureBoard": {
            "Message": {
                "head": "Verspätung",
                "text": "Wegen ... (Wien)",
                "products": {"Product": [{"catOutL": "S3"}]},
                "id": "2",
            }
        }
    }

    items = vor._collect_from_board("54321", root)
    title = items[0]["title"]
    # Expect: "S3: Verspätung (Baden)"
    assert title == "S3: Verspätung (Baden)"

def test_collect_from_board_skips_context_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Wien Mitte", in_vienna=True))

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


def test_collect_from_board_skips_message_without_head_or_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message with neither ``head`` nor ``text`` would surface as a
    silent "Hinweis" item with an empty description. Such messages must
    be dropped, not emitted (regression test for diagnostic §4.4)."""
    monkeypatch.setattr(
        "src.providers.vor.station_info",
        lambda x: StationInfo(name="Wien Mitte", in_vienna=True),
    )

    root = {
        "DepartureBoard": {
            "Message": [
                # Empty: should be skipped
                {"id": "empty-1"},
                # head/text both whitespace-only: should also be skipped
                {"head": "   ", "text": "", "id": "empty-2"},
                # Valid: should be kept
                {"head": "Aufzug defekt", "id": "valid-1"},
            ]
        }
    }

    items = vor._collect_from_board("111", root)
    assert len(items) == 1
    assert items[0]["title"] == "Wien Mitte: Aufzug defekt"
