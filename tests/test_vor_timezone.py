import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import src.providers.vor as vor


def test_fetch_events_passes_local_timezone(monkeypatch):
    vor.VOR_ACCESS_ID = "test"
    vor.VOR_STATION_IDS = ["123"]

    recorded = {}

    def fake_fetch_stationboard(station_id, now_local):
        recorded["tz"] = now_local.tzinfo
        return ET.Element("root")

    monkeypatch.setattr(vor, "_fetch_stationboard", fake_fetch_stationboard)

    result = vor.fetch_events()
    assert result == []
    assert recorded["tz"].key == "Europe/Vienna"
