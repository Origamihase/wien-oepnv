"""Regression tests for bugs found during the line-by-line audit (2026-05-30).

Each test pins one specific defect that was fixed in the same change set so a
future refactor cannot silently reintroduce it. The fixes span four modules:

* ``src/utils/stats.py``           — reader-side non-finite floor
* ``src/feed/logging_safe.py``     — datetime ``extra`` + cached ``exc_text``
* ``src/utils/logging.py``         — ``access_id`` / ``access-id`` masking
* ``src/providers/vor.py``         — quota cache must include ``unsaved_delta``
* ``scripts/update_baustellen_cache.py`` — degraded fallback must not crash
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

SAMPLE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "samples" / "baustellen_sample.geojson"
)


# ---------------------------------------------------------------------------
# src/utils/stats.py — CSV reader must reject non-finite delays, symmetric
# with the writer's ``math.isfinite`` floor in ``append_stammstrecke_row``.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("token", ["nan", "inf", "-inf", "1e400", "Infinity", "NaN"])
def test_parse_stammstrecke_row_rejects_non_finite(token: str) -> None:
    from src.utils import stats

    row = {
        "timestamp": "2026-05-30T12:00:00+02:00",
        "direction": "S1",
        "delay_minutes": token,
    }
    assert stats._parse_stammstrecke_row(row) is None


def test_parse_stammstrecke_row_accepts_finite() -> None:
    from src.utils import stats

    row = {
        "timestamp": "2026-05-30T12:00:00+02:00",
        "direction": "S1",
        "delay_minutes": "3.5",
    }
    obs = stats._parse_stammstrecke_row(row)
    assert obs is not None
    assert obs.delay_minutes == 3.5


# ---------------------------------------------------------------------------
# src/feed/logging_safe.py — a ``datetime`` (or any non-JSON-serialisable
# object) in ``extra`` must not raise / drop the record.
# ---------------------------------------------------------------------------
def test_safe_json_formatter_handles_datetime_extra() -> None:
    from src.feed import logging_safe

    formatter = logging_safe.SafeJSONFormatter()
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    # Inject a datetime ``extra`` the way the logging framework would; the
    # formatter reads it back from ``record.__dict__``.
    record.__dict__["when"] = datetime(2026, 1, 1, 12, 0, 0)
    out = formatter.format(record)
    payload = json.loads(out)
    assert payload["message"] == "hello"
    assert "2026-01-01" in payload["extra"]["when"]


# ---------------------------------------------------------------------------
# src/feed/logging_safe.py — a foreign handler that caches an unsanitised
# traceback in ``record.exc_text`` must not leak it through SafeFormatter.
# ---------------------------------------------------------------------------
def test_safe_formatter_does_not_leak_cached_exc_text() -> None:
    from src.feed import logging_safe

    secret = "ghp_ABCDEFGHIJKLMNOP1234567890abcdefXX"
    log = logging.getLogger("audit_regression_exc_text")
    log.setLevel(logging.ERROR)
    log.handlers.clear()
    log.propagate = False

    plain_buf = io.StringIO()
    plain = logging.StreamHandler(plain_buf)
    plain.setFormatter(logging.Formatter("%(message)s"))

    safe_buf = io.StringIO()
    safe = logging.StreamHandler(safe_buf)
    safe.setFormatter(logging_safe.SafeFormatter("%(message)s"))

    # Order matters: the plain handler formats first and caches the raw
    # traceback on the shared record; the Safe handler runs afterwards.
    log.addHandler(plain)
    log.addHandler(safe)
    try:
        try:
            raise ValueError(f"token={secret}")
        except ValueError:
            log.error("boom", exc_info=True)
        assert secret not in safe_buf.getvalue()
    finally:
        log.handlers.clear()


# ---------------------------------------------------------------------------
# src/utils/logging.py — the project's own VOR credential key name
# (``access_id`` / ``access-id``) must be masked, not just camelCase
# ``accessid`` / ``accessId``.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        "access_id=SUPERSECRETVALUE123",
        "access-id=SUPERSECRETVALUE123",
        "accessid=SUPERSECRETVALUE123",
        "access_id: SUPERSECRETVALUE123",
    ],
)
def test_sanitize_masks_access_id_variants(line: str) -> None:
    from src.utils import logging as ul

    assert "SUPERSECRETVALUE123" not in ul.sanitize_log_message(line)


# ---------------------------------------------------------------------------
# src/providers/vor.py — the cache-hit read path must add the live
# ``unsaved_delta`` to the flushed ``count`` (matches the disk path and
# ``save_request_count``), otherwise it under-reports usage by up to one
# flush batch and defeats the ``_charge_one_request`` pre-flight check.
# ---------------------------------------------------------------------------
def test_load_request_count_cache_includes_unsaved_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.providers import vor

    today = datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", today)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 5)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 3)

    stored_date, count = vor.load_request_count()

    assert stored_date == today
    assert count == 8  # 5 flushed + 3 pending — not 5


# ---------------------------------------------------------------------------
# scripts/update_baustellen_cache.py — when the fallback payload is
# drastically smaller than the existing cache, ``write_cache`` raises
# ``DataDegradationError``; ``main()`` must catch it (keep the pinned
# snapshot, exit non-zero) instead of crashing the cron step.
# ---------------------------------------------------------------------------
def test_baustellen_main_survives_data_degradation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import update_baustellen_cache

    # Import the original class definition from the SAME module the script
    # uses (``utils.cache`` once the script has put ``src`` on sys.path).
    # Under CI's ``PYTHONPATH=src`` ``utils.cache`` and ``src.utils.cache`` are
    # distinct module objects with distinct ``DataDegradationError`` classes,
    # so the raised type must match the one the script's ``except`` references.
    from utils.cache import DataDegradationError

    def fake_fetch_remote(url: str, timeout: int) -> None:
        return None

    def degrading_write_cache(provider: str, items: list[dict[str, Any]]) -> None:
        raise DataDegradationError("degraded payload rejected")

    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", degrading_write_cache)
    monkeypatch.setattr(
        update_baustellen_cache, "_log_endpoint_diagnostic", lambda *a, **k: None
    )
    monkeypatch.setenv("BAUSTELLEN_FALLBACK_PATH", str(SAMPLE_PATH))
    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")

    # Must return 1 (degraded, snapshot kept) rather than raising.
    exit_code = update_baustellen_cache.main()

    assert exit_code == 1
    assert any(
        "degrad" in record.getMessage().lower()
        for record in caplog.records
        if record.name == "update_baustellen_cache"
    )
