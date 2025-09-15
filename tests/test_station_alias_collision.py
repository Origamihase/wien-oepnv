import json
import logging

from src.utils import stations


def test_station_alias_collision_logs_warning(tmp_path, caplog, monkeypatch):
    data = [
        {
            "name": "First Station",
            "aliases": ["Collision"],
        },
        {
            "name": "Second Station",
            "aliases": ["Collision"],
        },
    ]
    temp_file = tmp_path / "stations.json"
    temp_file.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(stations, "_STATIONS_PATH", temp_file)
    stations._station_lookup.cache_clear()
    try:
        with caplog.at_level(logging.WARNING):
            stations._station_lookup()
    finally:
        stations._station_lookup.cache_clear()

    warnings = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert any("Duplicate station alias" in message for message in warnings)
    assert any("First Station" in message and "Second Station" in message for message in warnings)
