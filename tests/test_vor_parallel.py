import threading

import src.providers.vor as vor


def test_fetch_events_parallel(monkeypatch):
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "")
    vor.VOR_ACCESS_ID = "test"
    vor.VOR_STATION_IDS = ["1", "2"]
    vor.MAX_STATIONS_PER_RUN = 2

    # deterministische Auswahl der Stationen
    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk, period: ids[:chunk])

    barrier = threading.Barrier(2)

    def blocking_fetch(station_id, now_local, counter=None):
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError as e:
            raise AssertionError("stationboards not fetched in parallel") from e
        return {}

    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", blocking_fetch)
    # Return distinct titles so deduplication doesn't merge them
    monkeypatch.setattr(
        vor,
        "_collect_from_board",
        lambda sid, root: [{"guid": sid, "title": f"Title {sid}", "pubDate": None}],
    )

    items = vor.fetch_events()
    assert {it["guid"] for it in items} == {"1", "2"}


def test_fetch_events_logs_and_continues(monkeypatch, caplog):
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "")
    vor.VOR_ACCESS_ID = "test"
    vor.VOR_STATION_IDS = ["1", "2"]
    vor.MAX_STATIONS_PER_RUN = 2

    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk, period: ids[:chunk])

    def failing_fetch(station_id, now_local, counter=None):
        if station_id == "1":
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", failing_fetch)
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [{"guid": sid, "title": f"Title {sid}", "pubDate": None}])

    with caplog.at_level("ERROR"):
        items = vor.fetch_events()
    # Es sollte eine Fehlermeldung im Log auftauchen
    assert any("boom" in r.getMessage() for r in caplog.records)
    # Und trotzdem Ergebnisse für die andere Station geben
    assert items == [{"guid": "2", "title": "Title 2", "pubDate": None}]
