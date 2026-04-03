import builtins
import json
import multiprocessing
import os
import threading
from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

import src.providers.vor as vor


def _save_request_count_in_process(count_file: str, iso_timestamp: str, iterations: int, start_event) -> None:
    from datetime import datetime
    from pathlib import Path

    import src.providers.vor as vor_module

    vor_module.REQUEST_COUNT_FILE = Path(count_file)
    # Ensure memory cache is cleared so each process reads from file
    vor_module._QUOTA_CACHE["count"] = 0
    vor_module._QUOTA_CACHE["date"] = None
    vor_module._QUOTA_CACHE["unsaved_delta"] = 0

    moment = datetime.fromisoformat(iso_timestamp)
    start_event.wait()
    for _ in range(iterations):
        vor_module.save_request_count(moment)
        vor_module._QUOTA_CACHE["count"] = 0
        vor_module._QUOTA_CACHE["unsaved_delta"] = 0
        vor_module._QUOTA_CACHE["date"] = None


def test_fetch_events_respects_daily_limit(monkeypatch, caplog):
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["1"])
    monkeypatch.setattr(vor, "MAX_STATIONS_PER_RUN", 1)

    # Die neuen Docstrings von ``load_request_count`` und
    # ``save_request_count`` dokumentieren das zugrunde liegende Limit und die
    # Persistenz, auf die sich dieser Test stützt.
    monkeypatch.setattr(
        vor,
        "_select_stations_round_robin",
        lambda ids, chunk: ids[:chunk],
    )
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    def fail_fetch(*args, **kwargs):
        raise AssertionError("StationBoard request should not be triggered when limit reached")

    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", fail_fetch)

    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    vor.REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    vor.REQUEST_COUNT_FILE.write_text(
        json.dumps({"date": today, "requests": vor.MAX_REQUESTS_PER_DAY}),
        encoding="utf-8",
    )

    from requests import RequestException

    with caplog.at_level("INFO"):
        with pytest.raises(RequestException) as excinfo:
            vor.fetch_events()

    assert "Tageslimit" in str(excinfo.value)
    assert any("Tageslimit" in record.getMessage() for record in caplog.records)


def test_save_request_count_flushes_and_fsyncs(monkeypatch, tmp_path):
    # Reset cache to ensure we hit the write path
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)

    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    flush_called = False
    fsync_called = False

    original_open = builtins.open
    original_fsync = os.fsync

    def tracking_open(*args, **kwargs):
        file_obj = original_open(*args, **kwargs)

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

    monkeypatch.setattr("builtins.open", tracking_open)
    monkeypatch.setattr(vor.os, "fsync", tracking_fsync)

    vor.save_request_count(datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna")))

    assert flush_called
    assert fsync_called


def test_save_request_count_returns_previous_on_lock_failure(monkeypatch, tmp_path):
    # Reset cache to ensure we try to acquire lock
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)

    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    # Note: save_request_count now ignores "old" dates in load if they don't match today_utc.
    # To test logic, we should probably mock today or ensure the test date matches today.
    # However, if we write a file with a past date, load_request_count returns (None, 0).
    # Then save_request_count will see None != today, reset to 0, and try to write 1.
    # If we want to test "returns previous count", we need the date to match today.

    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    target_file.write_text(
        json.dumps({"date": today, "requests": 7}),
        encoding="utf-8",
    )

    from contextlib import contextmanager
    @contextmanager
    def failing_lock(*args, **kwargs):
        raise OSError("boom")
        yield

    monkeypatch.setattr(vor, "file_lock", failing_lock)

    # Arguments to save_request_count are ignored now, but we pass something.
    result = vor.save_request_count(datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna")))

    assert result == vor.MAX_REQUESTS_PER_DAY + 1
    stored = json.loads(target_file.read_text(encoding="utf-8"))
    assert stored["requests"] == 7


def test_save_request_count_returns_previous_on_replace_failure(monkeypatch, tmp_path):
    # Reset cache to ensure we try to replace file
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)

    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    target_file.write_text(
        json.dumps({"date": today, "requests": 3}),
        encoding="utf-8",
    )

    def failing_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(vor.os, "replace", failing_replace)

    result = vor.save_request_count()

    assert result == vor.MAX_REQUESTS_PER_DAY + 1
    # We poisoned the cache and returned the poison pill, the file wasn't replaced
    stored = json.loads(target_file.read_text(encoding="utf-8"))
    assert stored["requests"] == 3


def test_save_request_count_is_safe_across_processes(monkeypatch, tmp_path):
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)

    ctx = multiprocessing.get_context("spawn")
    start_event = ctx.Event()
    os.environ["WIEN_OEPNV_TEST_QUOTA_BATCH"] = "1"
    try:
        timestamp = datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna"))
        iterations = 5

        processes = [
            ctx.Process(
                target=_save_request_count_in_process,
                args=(str(count_file), timestamp.isoformat(), iterations, start_event),
            )
            for _ in range(2)
        ]

        for proc in processes:
            proc.start()

        start_event.set()

        for proc in processes:
            proc.join(10)
            assert not proc.is_alive()
            assert proc.exitcode == 0

        data = json.loads(count_file.read_text(encoding="utf-8"))
        assert data["requests"] == iterations * len(processes)
    finally:
        del os.environ["WIEN_OEPNV_TEST_QUOTA_BATCH"]


@pytest.fixture(autouse=True)
def reset_vor_quota_cache(monkeypatch):
    """Ensure memory cache is reset before every test."""
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)


def test_fetch_events_stops_submitting_when_limit_reached(monkeypatch, tmp_path):
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["1", "2", "3"])
    monkeypatch.setattr(vor, "MAX_STATIONS_PER_RUN", 3)
    monkeypatch.setattr(
        vor,
        "_select_stations_round_robin",
        lambda ids, chunk: ids[:chunk],
    )
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    count_file.parent.mkdir(parents=True, exist_ok=True)

    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    count_file.write_text(
        json.dumps({"date": today, "requests": vor.MAX_REQUESTS_PER_DAY - 1}),
        encoding="utf-8",
    )

    call_count = 0
    call_lock = threading.Lock()

    def fake_fetch(station_id, now_local, counter=None, session=None, timeout=None):
        nonlocal call_count
        with call_lock:
            call_count += 1
        vor.save_request_count(now_local)
        return {}

    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", fake_fetch)

    items = vor.fetch_events()

    assert items == []
    assert call_count == 1

    stored = json.loads(count_file.read_text(encoding="utf-8"))
    assert stored["requests"] == vor.MAX_REQUESTS_PER_DAY


@pytest.mark.parametrize("status_code, headers", [(429, {"Retry-After": "0"}), (503, {})])
def test_fetch_departure_board_for_station_counts_unsuccessful_requests(monkeypatch, status_code, headers):
    called = 0

    def fake_save(now_local):
        nonlocal called
        called += 1
        return called

    monkeypatch.setattr(vor, "save_request_count", fake_save)

    # Mock session to avoid real creation, but we will mock fetch_content_safe
    class DummySession:
        def __init__(self):
            self.headers: dict[str, str] = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def close(self): pass
        def prepare_request(self, request):
            from requests.models import PreparedRequest
            p = PreparedRequest()
            p.prepare(method=request.method, url=request.url, headers=request.headers)
            return p
        def merge_environment_settings(self, *args, **kwargs): return {}

    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: DummySession())

    def fake_fetch_content_safe(*args, **kwargs):
        import requests
        resp = requests.Response()
        resp.status_code = status_code
        for k, v in headers.items():
            resp.headers[k] = v
        # raise HTTPError as fetch_content_safe calls raise_for_status=True
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch_content_safe)

    now_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Vienna"))
    result = vor._fetch_departure_board_for_station("123", now_local)

    assert result is None
    # Requirement 4: Count only ONCE per station request (regardless of retries)
    assert called == 1


def test_fetch_departure_board_fails_gracefully_on_error(monkeypatch):
    from requests import ConnectionError

    call_count = 0

    def fake_save(now_local):
        nonlocal call_count
        call_count += 1
        return call_count

    monkeypatch.setattr(vor, "save_request_count", fake_save)

    # Mock session
    class DummySession:
        def __init__(self):
            self.headers: dict[str, str] = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def close(self): pass
        def prepare_request(self, request):
            from requests.models import PreparedRequest
            p = PreparedRequest()
            p.prepare(method=request.method, url=request.url, headers=request.headers)
            return p
        def merge_environment_settings(self, *args, **kwargs): return {}

    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: DummySession())

    # Mock fetch_content_safe to simulate failure
    def fake_fetch_content_safe(*args, **kwargs):
        raise ConnectionError("boom")

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch_content_safe)

    now_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Vienna"))
    payload = vor._fetch_departure_board_for_station("123", now_local)

    # Should return None on failure
    assert payload is None
    # Requirement: Count only ONCE per station request
    assert call_count == 1


def test_load_request_count_resets_on_legacy_integer(monkeypatch, tmp_path):
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    # Legacy integer format
    target_file.write_text("42", encoding="utf-8")

    date, count = vor.load_request_count()
    assert date is None
    assert count == 0


def test_load_request_count_resets_on_legacy_dict(monkeypatch, tmp_path):
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    # Legacy dict format (using 'count' instead of 'requests')
    target_file.write_text(json.dumps({"date": today, "count": 42}), encoding="utf-8")

    date, count = vor.load_request_count()
    assert date is None
    assert count == 0
