import src.providers.vor as vor


def test_fetch_events_passes_local_timezone(monkeypatch):
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    vor.VOR_ACCESS_ID = "test"
    vor.VOR_STATION_IDS = ["123"]

    recorded = {}

    def fake_fetch_traffic_info(station_id, now_local):
        recorded["tz"] = now_local.tzinfo
        return {}

    monkeypatch.setattr(vor, "_fetch_traffic_info", fake_fetch_traffic_info)

    result = vor.fetch_events()
    assert result == []
    assert recorded["tz"].key == "Europe/Vienna"
