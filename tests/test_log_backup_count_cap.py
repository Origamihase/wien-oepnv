"""Verify that ``LOG_BACKUP_COUNT`` env overrides cannot exceed ``MAX_LOG_BACKUP_COUNT``.

``feed_config.LOG_BACKUP_COUNT`` is consumed by both ``RotatingFileHandler``
instances configured in ``src/feed/logging.py`` (``errors.log`` and
``diagnostics.log``) as the number of rotated log files retained per handler.
Without an upper bound it is the *multiplier* in the worst-case disk-footprint
formula ``2 * MAX_LOG_BYTES * (LOG_BACKUP_COUNT + 1)`` — the previously-fixed
``MAX_LOG_BYTES`` ceiling is defeated by a single ``LOG_BACKUP_COUNT=999999``
override (intentional misconfig, leaked CI env, compromised secret store), so
the cap closes the disk-exhaustion vector at the same TIGHTEN-only contract
shape used for ``LOG_MAX_BYTES``, ``PROVIDER_TIMEOUT``, and ``REQUEST_TIMEOUT_S``.
"""

from __future__ import annotations

import pytest

from src.feed import config as feed_config


def test_max_log_backup_count_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (100x default) so operators can extend
    # retention for forensics without raising the ceiling, but the absolute
    # upper bound prevents the disk-fill scenario documented in the constant
    # block.
    assert feed_config.MAX_LOG_BACKUP_COUNT >= feed_config.DEFAULT_LOG_BACKUP_COUNT
    assert feed_config.MAX_LOG_BACKUP_COUNT == 500


def test_log_backup_count_clamped_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_BACKUP_COUNT", "999999")
    feed_config.refresh_from_env()
    assert feed_config.LOG_BACKUP_COUNT == feed_config.MAX_LOG_BACKUP_COUNT


def test_log_backup_count_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_BACKUP_COUNT", "12")
    feed_config.refresh_from_env()
    assert feed_config.LOG_BACKUP_COUNT == 12


def test_log_backup_count_at_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_BACKUP_COUNT", str(feed_config.MAX_LOG_BACKUP_COUNT))
    feed_config.refresh_from_env()
    assert feed_config.LOG_BACKUP_COUNT == feed_config.MAX_LOG_BACKUP_COUNT


def test_log_backup_count_zero_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero disables backup retention entirely; the existing ``max(..., 0)``
    # lower-bound contract must survive the new upper-bound clamp.
    monkeypatch.setenv("LOG_BACKUP_COUNT", "0")
    feed_config.refresh_from_env()
    assert feed_config.LOG_BACKUP_COUNT == 0


def test_log_backup_count_negative_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` followed by ``max(..., 0)`` already coerces negatives to
    # zero. The new cap must not change that lower-bound contract.
    monkeypatch.setenv("LOG_BACKUP_COUNT", "-5")
    feed_config.refresh_from_env()
    assert feed_config.LOG_BACKUP_COUNT == 0


def test_log_backup_count_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` falls back to the default on parse failure; the default
    # is itself well within the cap.
    monkeypatch.setenv("LOG_BACKUP_COUNT", "garbage")
    feed_config.refresh_from_env()
    assert feed_config.LOG_BACKUP_COUNT == feed_config.DEFAULT_LOG_BACKUP_COUNT


def test_log_backup_count_unset_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOG_BACKUP_COUNT", raising=False)
    feed_config.refresh_from_env()
    assert feed_config.LOG_BACKUP_COUNT == feed_config.DEFAULT_LOG_BACKUP_COUNT
