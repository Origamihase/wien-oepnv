import json
import os
import threading
from datetime import datetime

import pytest
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


def test_fetch_events_stops_submitting_when_limit_reached(monkeypatch, tmp_path):
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test")
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["1", "2", "3"])
    monkeypatch.setattr(vor, "MAX_STATIONS_PER_RUN", 3)
    monkeypatch.setattr(
        vor,
        "_select_stations_round_robin",
        lambda ids, chunk, period: ids[:chunk],
    )
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    count_file.parent.mkdir(parents=True, exist_ok=True)

    today = datetime.now().astimezone(ZoneInfo("Europe/Vienna")).date().isoformat()
    count_file.write_text(
        json.dumps({"date": today, "count": vor.MAX_REQUESTS_PER_DAY - 1}),
        encoding="utf-8",
    )

    call_count = 0
    call_lock = threading.Lock()

    def fake_fetch(station_id, now_local):
        nonlocal call_count
        with call_lock:
            call_count += 1
        vor.save_request_count(now_local)
        return None

    monkeypatch.setattr(vor, "_fetch_stationboard", fake_fetch)

    items = vor.fetch_events()

    assert items == []
    assert call_count == 1

    stored = json.loads(count_file.read_text(encoding="utf-8"))
    assert stored["count"] == vor.MAX_REQUESTS_PER_DAY


@pytest.mark.parametrize("status_code, headers", [(429, {"Retry-After": "0"}), (503, {})])
def test_fetch_stationboard_counts_unsuccessful_requests(monkeypatch, status_code, headers):
    called = 0

    def fake_save(now_local):
        nonlocal called
        called += 1
        return called

    monkeypatch.setattr(vor, "save_request_count", fake_save)

    if status_code == 429:
        monkeypatch.setattr(vor.time, "sleep", lambda *_args, **_kwargs: None)

    class DummyResponse:
        def __init__(self, status: int, hdrs: dict[str, str]):
            self.status_code = status
            self.headers = hdrs

        def json(self):  # pragma: no cover - defensive, should not be called for error codes
            return {}

    class DummySession:
        def __init__(self, response: DummyResponse):
            self._response = response
            self.headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, timeout=None):  # pragma: no cover - exercised in test
            return self._response

    def fake_session_with_retries(*args, **kwargs):
        return DummySession(DummyResponse(status_code, headers))

    monkeypatch.setattr(vor, "session_with_retries", fake_session_with_retries)

    now_local = datetime.now().astimezone(ZoneInfo("Europe/Vienna"))
    result = vor._fetch_stationboard("123", now_local)

    assert result is None
    assert called == 1
