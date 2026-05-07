"""Verify that ``ENDS_AT_GRACE_MINUTES`` env overrides cannot exceed ``MAX_ENDS_AT_GRACE_MINUTES``.

``feed_config.ENDS_AT_GRACE_MINUTES`` is consumed in
``src/build_feed.py:_drop_old_items`` as
``now_utc - timedelta(minutes=ENDS_AT_GRACE_MINUTES)`` (line 1057) and in
``src/providers/wl_fetch.py:_is_active`` as
``now - timedelta(minutes=ENDS_AT_GRACE_MINUTES)`` (line 140) to gate the
"drop already-expired item" filter. Without an upper bound an env override
such as ``ENDS_AT_GRACE_MINUTES=99999999999`` (intentional misconfig, leaked
CI env, compromised secret store) raises ``OverflowError: date value out of
range`` from the ``datetime - timedelta`` arithmetic — Python's datetime is
bounded at year 1, and subtracting ~190,000 years of minutes underflows —
crashing the feed-build pipeline. Even at non-overflow values the missing
cap disables the grace-window cutoff so every already-expired item stays in
the feed forever. Same TIGHTEN-only contract as ``STATE_RETENTION_DAYS``
(Round 8), and member of the same env-cap drift family — env-derived integer
feeding ``timedelta(unit=N)`` into ``datetime - timedelta`` arithmetic.
"""

from __future__ import annotations

import pytest

from src.config.defaults import DEFAULT_ENDS_AT_GRACE_MINUTES
from src.feed import config as feed_config


def test_max_ends_at_grace_minutes_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (~1000x default) so operators can
    # keep recently-expired items visible for weekly-poll RSS subscribers
    # without raising the ceiling, but the absolute upper bound stays well
    # within Python's ``datetime`` range.
    assert feed_config.MAX_ENDS_AT_GRACE_MINUTES >= DEFAULT_ENDS_AT_GRACE_MINUTES
    assert feed_config.MAX_ENDS_AT_GRACE_MINUTES == 10080


def test_ends_at_grace_minutes_clamped_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENDS_AT_GRACE_MINUTES", "99999999999")
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == feed_config.MAX_ENDS_AT_GRACE_MINUTES


def test_ends_at_grace_minutes_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENDS_AT_GRACE_MINUTES", "30")
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == 30


def test_ends_at_grace_minutes_at_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ENDS_AT_GRACE_MINUTES", str(feed_config.MAX_ENDS_AT_GRACE_MINUTES)
    )
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == feed_config.MAX_ENDS_AT_GRACE_MINUTES


def test_ends_at_grace_minutes_zero_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero disables the grace window entirely (items dropped at the exact
    # ends_at moment); the existing ``max(..., 0)`` lower-bound contract
    # must survive the new upper-bound clamp.
    monkeypatch.setenv("ENDS_AT_GRACE_MINUTES", "0")
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == 0


def test_ends_at_grace_minutes_negative_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` followed by ``max(..., 0)`` already coerces negatives
    # to zero. The new cap must not change that lower-bound contract.
    monkeypatch.setenv("ENDS_AT_GRACE_MINUTES", "-5")
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == 0


def test_ends_at_grace_minutes_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` falls back to the default on parse failure; the default
    # is itself well within the cap.
    monkeypatch.setenv("ENDS_AT_GRACE_MINUTES", "garbage")
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == DEFAULT_ENDS_AT_GRACE_MINUTES


def test_ends_at_grace_minutes_unset_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENDS_AT_GRACE_MINUTES", raising=False)
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == DEFAULT_ENDS_AT_GRACE_MINUTES
