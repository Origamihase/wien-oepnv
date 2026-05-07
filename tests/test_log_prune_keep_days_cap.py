"""Verify that ``prune_log_file(keep_days=...)`` cannot exceed ``MAX_LOG_PRUNE_KEEP_DAYS``.

``src/feed/logging.py:prune_log_file`` consumes ``keep_days`` as
``cutoff = now - timedelta(days=keep_days)`` — direct ``datetime - timedelta``
arithmetic. The default callers in ``src/feed/reporting.py`` use the hardcoded
7-day default, but the function is exported as a public API and a future caller
passing an env-controlled or user-controlled value (e.g. a hypothetical
``LOG_RETENTION_DAYS`` env var) would otherwise inherit the unbounded shape — at
very large values the subtraction underflows past Python's year-1 datetime
boundary and raises ``OverflowError: date value out of range``, propagating out
of ``prune_log_file`` past the surrounding ``OSError`` handlers and crashing
the cron job that owns the call. Capping inside the function (defense-in-depth)
means every caller — current and future — inherits the ceiling. TIGHTEN-only
contract mirrors ``MAX_STATE_RETENTION_DAYS`` / ``MAX_ENDS_AT_GRACE_MINUTES`` /
``MAX_CACHE_MAX_AGE_HOURS`` / ``MAX_FRESH_PUBDATE_WINDOW_MIN`` in
``src/feed/config.py`` — same env-cap drift family.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.feed.logging import MAX_LOG_PRUNE_KEEP_DAYS, prune_log_file


def test_max_log_prune_keep_days_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (~52x default of 7 days) so operators
    # can extend forensic retention without raising the ceiling, but the
    # absolute upper bound stays well within Python's datetime safe range.
    assert MAX_LOG_PRUNE_KEEP_DAYS == 365
    assert MAX_LOG_PRUNE_KEEP_DAYS >= 7


def test_prune_log_file_does_not_overflow_at_huge_keep_days(tmp_path: Path) -> None:
    """A caller passing ``keep_days=99999999`` would underflow Python's
    datetime range (year-1 boundary) without the cap. Verify the function
    completes without raising ``OverflowError``."""
    log_file = tmp_path / "huge.log"

    new_date = datetime.now(UTC)
    # Use a record with a recent timestamp so the post-clamp cutoff still
    # keeps it; the assertion that matters here is that the function does
    # not raise during the ``now - timedelta(days=N)`` arithmetic.
    new_line = f"{new_date.strftime('%Y-%m-%d %H:%M:%S,000')} Recent Message\n"
    log_file.write_text(new_line, encoding="utf-8")

    prune_log_file(log_file, now=new_date, keep_days=99999999)

    content = log_file.read_text(encoding="utf-8")
    assert "Recent Message" in content


def test_prune_log_file_at_cap_keeps_records_within_one_year(tmp_path: Path) -> None:
    """At the cap, records younger than 365 days must be retained and older
    ones (above the cap) removed — verifies the cap actually clamps to its
    documented value, not silently to a tighter bound."""
    log_file = tmp_path / "at_cap.log"

    now = datetime.now(UTC)
    fresh = now - timedelta(days=10)  # well within 365 days
    ancient = now - timedelta(days=400)  # above the 365-day cap

    fresh_line = f"{fresh.strftime('%Y-%m-%d %H:%M:%S,000')} Fresh Message\n"
    ancient_line = f"{ancient.strftime('%Y-%m-%d %H:%M:%S,000')} Ancient Message\n"

    log_file.write_text(ancient_line + fresh_line, encoding="utf-8")

    prune_log_file(log_file, now=now, keep_days=MAX_LOG_PRUNE_KEEP_DAYS)

    content = log_file.read_text(encoding="utf-8")
    assert "Fresh Message" in content
    assert "Ancient Message" not in content


def test_prune_log_file_below_cap_passes_through(tmp_path: Path) -> None:
    """A small ``keep_days`` (e.g. 3) must clamp records older than 3 days."""
    log_file = tmp_path / "below.log"

    now = datetime.now(UTC)
    fresh = now - timedelta(days=1)
    stale = now - timedelta(days=5)

    fresh_line = f"{fresh.strftime('%Y-%m-%d %H:%M:%S,000')} Fresh Message\n"
    stale_line = f"{stale.strftime('%Y-%m-%d %H:%M:%S,000')} Stale Message\n"

    log_file.write_text(stale_line + fresh_line, encoding="utf-8")

    prune_log_file(log_file, now=now, keep_days=3)

    content = log_file.read_text(encoding="utf-8")
    assert "Fresh Message" in content
    assert "Stale Message" not in content


def test_prune_log_file_zero_keep_days_short_circuits(tmp_path: Path) -> None:
    """``keep_days <= 0`` exits early; the existing lower-bound contract
    must survive the new upper-bound clamp."""
    log_file = tmp_path / "zero.log"

    now = datetime.now(UTC)
    ancient = now - timedelta(days=400)
    ancient_line = f"{ancient.strftime('%Y-%m-%d %H:%M:%S,000')} Ancient Message\n"
    log_file.write_text(ancient_line, encoding="utf-8")

    prune_log_file(log_file, now=now, keep_days=0)

    content = log_file.read_text(encoding="utf-8")
    assert "Ancient Message" in content


def test_prune_log_file_negative_keep_days_short_circuits(tmp_path: Path) -> None:
    """Negative ``keep_days`` exits early without raising."""
    log_file = tmp_path / "neg.log"

    now = datetime.now(UTC)
    ancient = now - timedelta(days=400)
    ancient_line = f"{ancient.strftime('%Y-%m-%d %H:%M:%S,000')} Ancient Message\n"
    log_file.write_text(ancient_line, encoding="utf-8")

    prune_log_file(log_file, now=now, keep_days=-5)

    content = log_file.read_text(encoding="utf-8")
    assert "Ancient Message" in content
