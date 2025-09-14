import time
import xml.etree.ElementTree as ET

import src.providers.vor as vor


def test_fetch_events_parallel(monkeypatch):
    vor.VOR_ACCESS_ID = "test"
    vor.VOR_STATION_IDS = ["1", "2"]
    vor.MAX_STATIONS_PER_RUN = 2

    # deterministischer Auswahl der Stationen
    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk, period: ids[:chunk])

    def slow_fetch(station_id, now_local):
        time.sleep(0.1)
        return ET.Element("root")

    monkeypatch.setattr(vor, "_fetch_stationboard", slow_fetch)
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    start = time.perf_counter()
    vor.fetch_events()
    duration = time.perf_counter() - start
    assert duration < 0.18


def test_fetch_events_logs_and_continues(monkeypatch, caplog):
    vor.VOR_ACCESS_ID = "test"
    vor.VOR_STATION_IDS = ["1", "2"]
    vor.MAX_STATIONS_PER_RUN = 2

    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk, period: ids[:chunk])

    def failing_fetch(station_id, now_local):
        if station_id == "1":
            raise RuntimeError("boom")
        return ET.Element("root")

    monkeypatch.setattr(vor, "_fetch_stationboard", failing_fetch)
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [{"guid": sid, "pubDate": None}])

    with caplog.at_level("ERROR"):
        items = vor.fetch_events()
    # Es sollte eine Fehlermeldung im Log auftauchen
    assert any("boom" in r.getMessage() for r in caplog.records)
    # Und trotzdem Ergebnisse für die andere Station geben
    assert items == [{"guid": "2", "pubDate": None}]
