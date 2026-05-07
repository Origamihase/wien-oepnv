"""Verify that ``prune_cache(max_age_hours=...)`` cannot exceed ``MAX_PRUNE_CACHE_MAX_AGE_HOURS``.

``src/utils/cache.py:prune_cache`` consumes ``max_age_hours`` as
``cutoff = now - timedelta(hours=max_age_hours)`` — direct ``timedelta(unit=N)``
construction followed by ``datetime - timedelta`` arithmetic. The default caller
in ``write_cache`` uses the hardcoded 48-hour default, but the function is
exported as a public API and a future caller passing an env-controlled or
user-controlled value (e.g. a hypothetical ``CACHE_PRUNE_MAX_AGE_HOURS`` env
var) would otherwise inherit the unbounded shape — at very large values the
``timedelta`` constructor itself raises ``OverflowError: Python int too large
to convert to C int`` (the C-level normalisation packs days into a signed
32-bit int, ~10**11 hours overflows that bound), and at slightly smaller values
the subsequent ``now - timedelta(hours=N)`` subtraction underflows past
Python's year-1 datetime boundary and raises ``OverflowError: date value out
of range``. Both errors propagate out of ``prune_cache`` past the surrounding
``OSError`` handlers and crash the ``write_cache`` callers that wrap it.
Capping inside the function (defense-in-depth) means every caller — current
and future — inherits the ceiling. TIGHTEN-only contract mirrors
``MAX_LOG_PRUNE_KEEP_DAYS`` (``src/feed/logging.py``) and
``MAX_CACHE_MAX_AGE_HOURS`` / ``MAX_FRESH_PUBDATE_WINDOW_MIN`` /
``MAX_ENDS_AT_GRACE_MINUTES`` / ``MAX_STATE_RETENTION_DAYS``
(``src/feed/config.py``) — same env-cap drift family.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.utils import cache
from src.utils.cache import MAX_PRUNE_CACHE_MAX_AGE_HOURS, prune_cache


def test_max_prune_cache_max_age_hours_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (~182x the 48-hour default) so
    # operators can extend the eviction window for forensics without raising
    # the ceiling, but the absolute upper bound stays well within Python's
    # datetime safe range.
    assert MAX_PRUNE_CACHE_MAX_AGE_HOURS == 8760
    assert MAX_PRUNE_CACHE_MAX_AGE_HOURS >= 48


def test_prune_cache_does_not_overflow_at_huge_max_age_hours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller passing ``max_age_hours=99999999999`` would either trip the
    ``timedelta`` C-int overflow or the ``datetime - timedelta`` underflow
    without the cap. Verify the function completes without raising
    ``OverflowError`` and keeps recent cache files in place."""
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)
    provider_dir = base / "provider"
    provider_dir.mkdir(parents=True, exist_ok=True)
    cache_file = provider_dir / "events.json"
    cache_file.write_text("[]", encoding="utf-8")

    # Without the cap this would raise OverflowError from the
    # ``timedelta(hours=N)`` constructor or the subsequent subtraction.
    prune_cache(max_age_hours=99999999999)

    # A recently-written file must survive the post-clamp 1-year cutoff.
    assert cache_file.exists()


def test_prune_cache_at_cap_evicts_files_older_than_one_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At the cap, files older than 8760 hours (1 year) must be evicted —
    verifies the cap actually clamps to its documented value, not silently
    to a tighter or looser bound."""
    import os

    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    fresh_dir = base / "fresh"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    fresh_file = fresh_dir / "events.json"
    fresh_file.write_text("[]", encoding="utf-8")

    ancient_dir = base / "ancient"
    ancient_dir.mkdir(parents=True, exist_ok=True)
    ancient_file = ancient_dir / "events.json"
    ancient_file.write_text("[]", encoding="utf-8")
    # Backdate the ancient file to ~14 months ago (above the 1-year cap).
    ancient_ts = (datetime.now(UTC) - timedelta(days=420)).timestamp()
    os.utime(ancient_file, (ancient_ts, ancient_ts))

    prune_cache(max_age_hours=MAX_PRUNE_CACHE_MAX_AGE_HOURS)

    assert fresh_file.exists()
    assert not ancient_file.exists()


def test_prune_cache_below_cap_passes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small ``max_age_hours`` (e.g. 24) must still evict files older
    than 24 hours — the cap must not change the unclamped behaviour."""
    import os

    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    fresh_dir = base / "fresh"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    fresh_file = fresh_dir / "events.json"
    fresh_file.write_text("[]", encoding="utf-8")

    stale_dir = base / "stale"
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_file = stale_dir / "events.json"
    stale_file.write_text("[]", encoding="utf-8")
    stale_ts = (datetime.now(UTC) - timedelta(hours=48)).timestamp()
    os.utime(stale_file, (stale_ts, stale_ts))

    prune_cache(max_age_hours=24)

    assert fresh_file.exists()
    assert not stale_file.exists()


def test_prune_cache_zero_max_age_hours_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``max_age_hours <= 0`` exits early; the new lower-bound contract
    must keep all cache files in place rather than evicting everything."""
    import os

    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    provider_dir = base / "provider"
    provider_dir.mkdir(parents=True, exist_ok=True)
    cache_file = provider_dir / "events.json"
    cache_file.write_text("[]", encoding="utf-8")
    ancient_ts = (datetime.now(UTC) - timedelta(days=400)).timestamp()
    os.utime(cache_file, (ancient_ts, ancient_ts))

    prune_cache(max_age_hours=0)

    assert cache_file.exists()


def test_prune_cache_negative_max_age_hours_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative ``max_age_hours`` exits early without raising and without
    evicting anything (avoids treating a sign flip as ``evict everything``)."""
    import os

    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    provider_dir = base / "provider"
    provider_dir.mkdir(parents=True, exist_ok=True)
    cache_file = provider_dir / "events.json"
    cache_file.write_text("[]", encoding="utf-8")
    ancient_ts = (datetime.now(UTC) - timedelta(days=400)).timestamp()
    os.utime(cache_file, (ancient_ts, ancient_ts))

    prune_cache(max_age_hours=-5)

    assert cache_file.exists()
