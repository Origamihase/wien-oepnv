"""Verify that ``STATE_RETENTION_DAYS`` env overrides cannot exceed ``MAX_STATE_RETENTION_DAYS``.

``feed_config.STATE_RETENTION_DAYS`` is consumed in
``src/build_feed.py:_load_state`` as
``now_utc - timedelta(days=STATE_RETENTION_DAYS)`` to discard ``first_seen``
state entries older than the window. Without an upper bound an env override
such as ``STATE_RETENTION_DAYS=99999999`` (intentional misconfig, leaked CI
env, compromised secret store) raises ``OverflowError: date value out of
range`` from the ``datetime - timedelta`` arithmetic — Python's datetime is
bounded at year 1, so subtracting 99999999 days underflows — and propagates
out of ``_load_state`` past the ``except FileNotFoundError, JSONDecodeError``
/ generic ``except Exception`` handlers, crashing the entire feed-build
pipeline. Even at non-overflow values the missing cap disables the retention
cutoff so the on-disk state file grows unboundedly. Same TIGHTEN-only
contract shape as ``LOG_MAX_BYTES``, ``LOG_BACKUP_COUNT``, and
``PROVIDER_TIMEOUT``.
"""

from __future__ import annotations

import pytest

from src.config.defaults import DEFAULT_STATE_RETENTION_DAYS
from src.feed import config as feed_config


def test_max_state_retention_days_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (~60x default) so operators can
    # extend retention for long-running RSS subscribers without raising the
    # ceiling, but the absolute upper bound stays well within Python's
    # ``datetime`` range and bounds the on-disk state file size.
    assert feed_config.MAX_STATE_RETENTION_DAYS >= DEFAULT_STATE_RETENTION_DAYS
    assert feed_config.MAX_STATE_RETENTION_DAYS == 3650


def test_state_retention_days_clamped_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATE_RETENTION_DAYS", "99999999")
    feed_config.refresh_from_env()
    assert feed_config.STATE_RETENTION_DAYS == feed_config.MAX_STATE_RETENTION_DAYS


def test_state_retention_days_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATE_RETENTION_DAYS", "30")
    feed_config.refresh_from_env()
    assert feed_config.STATE_RETENTION_DAYS == 30


def test_state_retention_days_at_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "STATE_RETENTION_DAYS", str(feed_config.MAX_STATE_RETENTION_DAYS)
    )
    feed_config.refresh_from_env()
    assert feed_config.STATE_RETENTION_DAYS == feed_config.MAX_STATE_RETENTION_DAYS


def test_state_retention_days_zero_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero disables retention entirely (every entry is kept); the existing
    # ``max(..., 0)`` lower-bound contract must survive the new upper-bound
    # clamp so existing tests like ``test_state_repairs_malformed_entries``
    # keep working.
    monkeypatch.setenv("STATE_RETENTION_DAYS", "0")
    feed_config.refresh_from_env()
    assert feed_config.STATE_RETENTION_DAYS == 0


def test_state_retention_days_negative_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` followed by ``max(..., 0)`` already coerces negatives to
    # zero. The new cap must not change that lower-bound contract.
    monkeypatch.setenv("STATE_RETENTION_DAYS", "-5")
    feed_config.refresh_from_env()
    assert feed_config.STATE_RETENTION_DAYS == 0


def test_state_retention_days_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` falls back to the default on parse failure; the default
    # is itself well within the cap.
    monkeypatch.setenv("STATE_RETENTION_DAYS", "garbage")
    feed_config.refresh_from_env()
    assert feed_config.STATE_RETENTION_DAYS == DEFAULT_STATE_RETENTION_DAYS


def test_state_retention_days_unset_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STATE_RETENTION_DAYS", raising=False)
    feed_config.refresh_from_env()
    assert feed_config.STATE_RETENTION_DAYS == DEFAULT_STATE_RETENTION_DAYS
