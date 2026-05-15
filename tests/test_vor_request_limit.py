import builtins
import json
import multiprocessing
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Iterator

import pytest
from zoneinfo import ZoneInfo

import src.providers.vor as vor


def _save_request_count_in_process(
    count_file: str,
    iso_timestamp: str,
    iterations: int,
    start_event: threading.Event,
) -> None:
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




def test_save_request_count_flushes_and_fsyncs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Reset cache to ensure we hit the write path
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)

    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    flush_called = False
    fsync_called = False

    original_open = builtins.open
    original_fsync = os.fsync

    def tracking_open(*args: Any, **kwargs: Any) -> Any:
        file_obj = original_open(*args, **kwargs)

        class TrackingFile:
            def __init__(self, wrapped: Any) -> None:
                self._wrapped = wrapped

            def flush(self) -> Any:
                nonlocal flush_called
                flush_called = True
                return self._wrapped.flush()

            def __getattr__(self, name: str) -> Any:
                return getattr(self._wrapped, name)

            def __enter__(self) -> "TrackingFile":
                self._wrapped.__enter__()
                return self

            def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
                return self._wrapped.__exit__(exc_type, exc, tb)

        return TrackingFile(file_obj)

    def tracking_fsync(fd: int) -> None:
        nonlocal fsync_called
        fsync_called = True
        return original_fsync(fd)

    monkeypatch.setattr("builtins.open", tracking_open)
    # ``vor.os`` is the same singleton as the local ``os``; patch the
    # canonical reference so mypy --strict (no implicit reexport) is
    # happy without re-exporting the module from ``providers/vor.py``.
    monkeypatch.setattr(os, "fsync", tracking_fsync)

    vor.save_request_count(datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna")))

    assert flush_called
    assert fsync_called


def test_save_request_count_returns_previous_on_lock_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    def failing_lock(*args: Any, **kwargs: Any) -> Iterator[None]:
        raise OSError("boom")
        yield  # type: ignore[unreachable]  # contextmanager decorator requires a yield even after raise

    monkeypatch.setattr(vor, "file_lock", failing_lock)

    # Arguments to save_request_count are ignored now, but we pass something.
    result = vor.save_request_count(datetime(2023, 1, 2, tzinfo=ZoneInfo("Europe/Vienna")))

    assert result == vor.MAX_REQUESTS_PER_DAY + 1
    stored = json.loads(target_file.read_text(encoding="utf-8"))
    assert stored["requests"] == 7


def test_save_request_count_returns_previous_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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

    def failing_replace(src: Any, dst: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)

    result = vor.save_request_count()

    assert result == vor.MAX_REQUESTS_PER_DAY + 1
    # We poisoned the cache and returned the poison pill, the file wasn't replaced
    stored = json.loads(target_file.read_text(encoding="utf-8"))
    assert stored["requests"] == 3


def test_save_request_count_is_safe_across_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
def reset_vor_quota_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure memory cache is reset before every test."""
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)








def test_flush_quota_cache_does_not_inflate_persisted_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression: ``_flush_quota_cache`` MUST persist without recording.

    Pre-fix, the atexit-registered flush invoked ``save_request_count``,
    which always increments ``unsaved_delta`` by ``1`` before flushing.
    Every script invocation that made any VOR call therefore booked one
    *phantom* request beyond the actual API traffic. With the
    Stammstrecke cron (2 ``/trip`` calls per tick × 48 ticks/day = 96
    real requests) the bug inflated the persisted counter to ``144``
    requests/day, breaching the contractual ``100``/day VAO Start cap
    after roughly 33 ticks and leaving the remaining ~15 hours of the
    day without Stammstrecke observations. The visible symptom on the
    README dashboard was a "Letzte 60 Minuten" snapshot that bottomed
    out at 1-3 observations whenever an affected hour was the most
    recent one.

    This test pins the invariant: after N real ``save_request_count``
    calls plus a final ``_flush_quota_cache``, the persisted count is
    exactly ``N`` — never ``N + 1``.
    """
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    # Simulate a single Stammstrecke cron tick: 2 ``/trip`` requests
    # (one per direction). The first call triggers an immediate flush
    # via the ``current_total == 1`` branch; the second call only
    # increments ``unsaved_delta`` because the batch limit is 10.
    real_calls = 2
    moment = datetime.now(ZoneInfo("Europe/Vienna"))
    for _ in range(real_calls):
        vor.save_request_count(moment)

    # End-of-process flush — must NOT add another phantom request.
    vor._flush_quota_cache()

    stored = json.loads(target_file.read_text(encoding="utf-8"))
    assert stored["requests"] == real_calls, (
        f"Expected exactly {real_calls} persisted requests after "
        f"{real_calls} actual API calls + flush, but found "
        f"{stored['requests']}. The flush helper is double-counting."
    )

    # A second flush must be a strict no-op — the in-memory delta is
    # zero so neither the cache nor the disk should change.
    vor._flush_quota_cache()
    stored_again = json.loads(target_file.read_text(encoding="utf-8"))
    assert stored_again["requests"] == real_calls


def test_flush_quota_cache_with_no_unsaved_delta_is_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Idempotent ``_flush_quota_cache`` — never writes when delta=0.

    Belt-and-braces alongside the regression test above: if a future
    refactor reintroduces an unconditional ``save_request_count`` call
    inside the flush helper, this test catches it on the *no-call* code
    path (where the prior test would still have recorded a real call to
    mask the regression).
    """
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    # Cache primed with date but zero unsaved delta — the canonical
    # "nothing to flush" state.
    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", today)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    vor._flush_quota_cache()

    # No write should have happened: the file must not exist (or, if
    # it does, must reflect zero requests rather than the phantom +1
    # the pre-fix code would have produced).
    assert not target_file.exists(), (
        "Expected no on-disk write when there is nothing to flush, but "
        f"found {target_file.read_text(encoding='utf-8')!r}."
    )


def test_load_request_count_resets_on_legacy_integer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    # Legacy integer format
    target_file.write_text("42", encoding="utf-8")

    date, count = vor.load_request_count()
    assert date is None
    assert count == 0


def test_load_request_count_resets_on_legacy_dict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", target_file)

    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    # Legacy dict format (using 'count' instead of 'requests')
    target_file.write_text(json.dumps({"date": today, "count": 42}), encoding="utf-8")

    date, count = vor.load_request_count()
    assert date is None
    assert count == 0
