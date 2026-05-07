"""Verify that ``CACHE_MAX_AGE_HOURS`` env overrides cannot exceed ``MAX_CACHE_MAX_AGE_HOURS``.

``feed_config.CACHE_MAX_AGE_HOURS`` is consumed in
``src/build_feed.py:_detect_stale_caches`` as
``threshold = timedelta(hours=CACHE_MAX_AGE_HOURS)`` (line 223) to gate the
per-provider cache staleness warning. Without an upper bound an env override
such as ``CACHE_MAX_AGE_HOURS=999999999999`` (intentional misconfig, leaked
CI env, compromised secret store) raises ``OverflowError: Python int too
large to convert to C int`` from the ``timedelta`` constructor — the C-level
normalisation packs days into a signed 32-bit int, and ~10**11 hours
overflows that bound. ``_detect_stale_caches`` is invoked at
``build_feed.py:1772`` BEFORE the main ``try`` block at line 1777, so the
exception escapes the orchestrator and crashes the feed-build pipeline
before a single item is written. Even at non-overflow but unreasonably large
values (e.g. ~10**8 hours) the staleness warning is suppressed forever,
defeating the cron's early-warning signal. Same TIGHTEN-only contract as
``ENDS_AT_GRACE_MINUTES`` (Round 9), and member of the same env-cap drift
family — env-derived integer feeding ``timedelta(unit=N)`` whose constructor
overflows at large magnitudes.
"""

from __future__ import annotations

import pytest

from src.config.defaults import DEFAULT_CACHE_MAX_AGE_HOURS
from src.feed import config as feed_config


def test_max_cache_max_age_hours_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (365x default) so operators can
    # extend the staleness threshold during long planned pauses (e.g. a
    # multi-month deployment freeze) without raising the ceiling, but the
    # absolute upper bound stays well within ``timedelta``'s safe range.
    assert feed_config.MAX_CACHE_MAX_AGE_HOURS >= DEFAULT_CACHE_MAX_AGE_HOURS
    assert feed_config.MAX_CACHE_MAX_AGE_HOURS == 8760


def test_cache_max_age_hours_clamped_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CACHE_MAX_AGE_HOURS", "999999999999")
    feed_config.refresh_from_env()
    assert feed_config.CACHE_MAX_AGE_HOURS == feed_config.MAX_CACHE_MAX_AGE_HOURS


def test_cache_max_age_hours_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CACHE_MAX_AGE_HOURS", "48")
    feed_config.refresh_from_env()
    assert feed_config.CACHE_MAX_AGE_HOURS == 48


def test_cache_max_age_hours_at_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CACHE_MAX_AGE_HOURS", str(feed_config.MAX_CACHE_MAX_AGE_HOURS)
    )
    feed_config.refresh_from_env()
    assert feed_config.CACHE_MAX_AGE_HOURS == feed_config.MAX_CACHE_MAX_AGE_HOURS


def test_cache_max_age_hours_zero_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero disables the staleness check entirely (``_detect_stale_caches``
    # short-circuits at the ``<= 0`` guard before constructing the timedelta);
    # the existing ``max(..., 0)`` lower-bound contract must survive the new
    # upper-bound clamp.
    monkeypatch.setenv("CACHE_MAX_AGE_HOURS", "0")
    feed_config.refresh_from_env()
    assert feed_config.CACHE_MAX_AGE_HOURS == 0


def test_cache_max_age_hours_negative_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` followed by ``max(..., 0)`` already coerces negatives
    # to zero. The new cap must not change that lower-bound contract.
    monkeypatch.setenv("CACHE_MAX_AGE_HOURS", "-5")
    feed_config.refresh_from_env()
    assert feed_config.CACHE_MAX_AGE_HOURS == 0


def test_cache_max_age_hours_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` falls back to the default on parse failure; the default
    # is itself well within the cap.
    monkeypatch.setenv("CACHE_MAX_AGE_HOURS", "garbage")
    feed_config.refresh_from_env()
    assert feed_config.CACHE_MAX_AGE_HOURS == DEFAULT_CACHE_MAX_AGE_HOURS


def test_cache_max_age_hours_unset_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CACHE_MAX_AGE_HOURS", raising=False)
    feed_config.refresh_from_env()
    assert feed_config.CACHE_MAX_AGE_HOURS == DEFAULT_CACHE_MAX_AGE_HOURS
