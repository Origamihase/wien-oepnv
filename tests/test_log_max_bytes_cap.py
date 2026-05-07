"""Verify that ``LOG_MAX_BYTES`` env overrides cannot exceed ``MAX_LOG_BYTES``.

``feed_config.LOG_MAX_BYTES`` is consumed by both ``RotatingFileHandler``
instances configured in ``src/feed/logging.py`` (``errors.log`` and
``diagnostics.log``) as the size threshold that triggers rotation. Without an
upper bound an env override such as ``LOG_MAX_BYTES=999999999999`` (intentional
misconfig, leaked CI env, compromised secret store) would prevent rotation
entirely and let the active log file grow until the volume fills, stalling the
cron pipeline — same TIGHTEN-only contract as the previously-capped
``PROVIDER_TIMEOUT`` and ``REQUEST_TIMEOUT_S``, just expressed via the
disk-exhaustion vector rather than the network/API ones.
"""

from __future__ import annotations

import pytest

from src.feed import config as feed_config


def test_max_log_bytes_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (100x default) so operators can absorb
    # verbose-debug runs without raising the ceiling, but the absolute upper
    # bound prevents the disk-fill scenario documented in the constant block.
    assert feed_config.MAX_LOG_BYTES >= feed_config.DEFAULT_LOG_MAX_BYTES
    assert feed_config.MAX_LOG_BYTES == 100 * 1024 * 1024


def test_log_max_bytes_clamped_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_MAX_BYTES", "999999999999")
    feed_config.refresh_from_env()
    assert feed_config.LOG_MAX_BYTES == feed_config.MAX_LOG_BYTES


def test_log_max_bytes_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_MAX_BYTES", "500000")
    feed_config.refresh_from_env()
    assert feed_config.LOG_MAX_BYTES == 500000


def test_log_max_bytes_at_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_MAX_BYTES", str(feed_config.MAX_LOG_BYTES))
    feed_config.refresh_from_env()
    assert feed_config.LOG_MAX_BYTES == feed_config.MAX_LOG_BYTES


def test_log_max_bytes_zero_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero disables rotation entirely; the existing ``max(..., 0)`` lower-bound
    # contract must survive the new upper-bound clamp.
    monkeypatch.setenv("LOG_MAX_BYTES", "0")
    feed_config.refresh_from_env()
    assert feed_config.LOG_MAX_BYTES == 0


def test_log_max_bytes_negative_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` followed by ``max(..., 0)`` already coerces negatives to
    # zero. The new cap must not change that lower-bound contract.
    monkeypatch.setenv("LOG_MAX_BYTES", "-5")
    feed_config.refresh_from_env()
    assert feed_config.LOG_MAX_BYTES == 0


def test_log_max_bytes_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` falls back to the default on parse failure; the default
    # is itself well within the cap.
    monkeypatch.setenv("LOG_MAX_BYTES", "garbage")
    feed_config.refresh_from_env()
    assert feed_config.LOG_MAX_BYTES == feed_config.DEFAULT_LOG_MAX_BYTES


def test_log_max_bytes_unset_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_MAX_BYTES", raising=False)
    feed_config.refresh_from_env()
    assert feed_config.LOG_MAX_BYTES == feed_config.DEFAULT_LOG_MAX_BYTES
