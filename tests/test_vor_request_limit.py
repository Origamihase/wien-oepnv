import json
import os
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


def test_save_request_count_flushes_and_fsyncs(monkeypatch, tmp_path):
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    flush_called = False
    fsync_called = False

    original_fdopen = os.fdopen
    original_fsync = os.fsync

    def tracking_fdopen(*args, **kwargs):
        file_obj = original_fdopen(*args, **kwargs)

        class TrackingFile:
            def __init__(self, wrapped):
                self._wrapped = wrapped

            def flush(self):
                nonlocal flush_called
                flush_called = True
                return self._wrapped.flush()

            def __getattr__(self, name):
                return getattr(self._wrapped, name)

            def __enter__(self):
                self._wrapped.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                return self._wrapped.__exit__(exc_type, exc, tb)

        return TrackingFile(file_obj)

    def tracking_fsync(fd):
        nonlocal fsync_called
        fsync_called = True
        return original_fsync(fd)

    monkeypatch.setattr(vor.os, "fdopen", tracking_fdopen)
    monkeypatch.setattr(vor.os, "fsync", tracking_fsync)

    vor.save_request_count(datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna")))

    assert flush_called
    assert fsync_called
