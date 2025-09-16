import json
from datetime import datetime

from zoneinfo import ZoneInfo

import src.providers.vor as vor


def test_fetch_events_respects_daily_limit(monkeypatch, caplog):
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test")
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["1"])
    monkeypatch.setattr(vor, "MAX_STATIONS_PER_RUN", 1)

    # Die neuen Docstrings von ``load_request_count`` und
    # ``save_request_count`` dokumentieren das zugrunde liegende Limit und die
    # Persistenz, auf die sich dieser Test stützt.
    monkeypatch.setattr(
        vor,
        "_select_stations_round_robin",
        lambda ids, chunk, period: ids[:chunk],
    )
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    def fail_fetch(*args, **kwargs):
        raise AssertionError("StationBoard request should not be triggered when limit reached")

    monkeypatch.setattr(vor, "_fetch_stationboard", fail_fetch)

    today = datetime.now().astimezone(ZoneInfo("Europe/Vienna")).date().isoformat()
    vor.REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    vor.REQUEST_COUNT_FILE.write_text(
        json.dumps({"date": today, "count": vor.MAX_REQUESTS_PER_DAY}),
        encoding="utf-8",
    )

    with caplog.at_level("INFO"):
        items = vor.fetch_events()

    assert items == []
    assert any("Tageslimit" in record.getMessage() for record in caplog.records)
