import json
import multiprocessing
import os
import threading
from datetime import datetime

from zoneinfo import ZoneInfo

import src.providers.vor as vor


def _save_request_count_worker(path_str: str, iterations: int, start_event) -> None:
    """Helper for multiprocessing test to increment the counter."""

    from pathlib import Path

    import src.providers.vor as child_vor

    child_vor.REQUEST_COUNT_FILE = Path(path_str)
    child_vor.REQUEST_COUNT_LOCK = threading.Lock()

    now = datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna"))
    start_event.wait()
    for _ in range(iterations):
        child_vor.save_request_count(now)


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

    original_fsync = os.fsync
    original_open = vor.Path.open

    def tracking_open(self, *args, **kwargs):
        file_obj = original_open(self, *args, **kwargs)

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

    monkeypatch.setattr(vor.Path, "open", tracking_open)
    monkeypatch.setattr(vor.os, "fsync", tracking_fsync)

    vor.save_request_count(datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna")))

    assert flush_called
    assert fsync_called


def test_request_count_file_is_process_safe(monkeypatch, tmp_path):
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)
    monkeypatch.setattr(vor, "REQUEST_COUNT_LOCK", threading.Lock())

    ctx = multiprocessing.get_context("spawn")
    start_event = ctx.Event()
    iterations_per_process = 3
    processes = [
        ctx.Process(
            target=_save_request_count_worker,
            args=(str(target_file), iterations_per_process, start_event),
        )
        for _ in range(4)
    ]

    for proc in processes:
        proc.start()

    start_event.set()

    for proc in processes:
        proc.join(10)
        assert proc.exitcode == 0

    data = json.loads(target_file.read_text(encoding="utf-8"))
    expected_count = iterations_per_process * len(processes)
    assert data["count"] == expected_count
    assert data["date"] == datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna")).date().isoformat()

    loaded_date, loaded_count = vor.load_request_count()
    assert loaded_date == data["date"]
    assert loaded_count == expected_count
