"""Verify that ``FRESH_PUBDATE_WINDOW_MIN`` env overrides cannot exceed ``MAX_FRESH_PUBDATE_WINDOW_MIN``.

``feed_config.FRESH_PUBDATE_WINDOW_MIN`` is consumed in
``src/build_feed.py:_emit_item`` as
``if age <= timedelta(minutes=FRESH_PUBDATE_WINDOW_MIN): pubDate = now`` (line
1620) to gate the "newly arrived item gets now() as pubDate" rule. Without an
upper bound an env override such as ``FRESH_PUBDATE_WINDOW_MIN=999999999999``
(intentional misconfig, leaked CI env, compromised secret store) raises
``OverflowError: Python int too large to convert to C int`` from the
``timedelta`` constructor — the C-level normalisation packs days into a signed
32-bit int and ~10**11 minutes overflows that bound — crashing the RSS
rendering loop in ``_make_rss`` and halting the feed-build pipeline. Even at
non-overflow values the missing cap disables the freshness gate so every item
without a pubDate gets ``now()`` regardless of its actual ``first_seen``
timestamp, breaking RSS subscriber dedup. Same TIGHTEN-only contract as
``CACHE_MAX_AGE_HOURS`` (Round 10), and member of the same env-cap drift family
— env-derived integer feeding ``timedelta(unit=N)`` whose constructor overflows
at large magnitudes.
"""

from __future__ import annotations

import pytest

from src.config.defaults import DEFAULT_FRESH_PUBDATE_WINDOW_MIN
from src.feed import config as feed_config


def test_max_fresh_pubdate_window_min_is_generous_relative_to_default() -> None:
    # The cap is intentionally generous (~288x default) so operators can absorb
    # long-running batch backfills without raising the ceiling, but the
    # absolute upper bound stays well within ``timedelta``'s safe range and
    # still semantically means "recently arrived" (one day).
    assert feed_config.MAX_FRESH_PUBDATE_WINDOW_MIN >= DEFAULT_FRESH_PUBDATE_WINDOW_MIN
    assert feed_config.MAX_FRESH_PUBDATE_WINDOW_MIN == 1440


def test_fresh_pubdate_window_min_clamped_to_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRESH_PUBDATE_WINDOW_MIN", "999999999999")
    feed_config.refresh_from_env()
    assert (
        feed_config.FRESH_PUBDATE_WINDOW_MIN
        == feed_config.MAX_FRESH_PUBDATE_WINDOW_MIN
    )


def test_fresh_pubdate_window_min_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRESH_PUBDATE_WINDOW_MIN", "15")
    feed_config.refresh_from_env()
    assert feed_config.FRESH_PUBDATE_WINDOW_MIN == 15


def test_fresh_pubdate_window_min_at_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FRESH_PUBDATE_WINDOW_MIN", str(feed_config.MAX_FRESH_PUBDATE_WINDOW_MIN)
    )
    feed_config.refresh_from_env()
    assert (
        feed_config.FRESH_PUBDATE_WINDOW_MIN
        == feed_config.MAX_FRESH_PUBDATE_WINDOW_MIN
    )


def test_fresh_pubdate_window_min_zero_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Zero disables the freshness rule entirely (no item ever gets pubDate=now);
    # the existing ``max(..., 0)`` lower-bound contract must survive the new
    # upper-bound clamp.
    monkeypatch.setenv("FRESH_PUBDATE_WINDOW_MIN", "0")
    feed_config.refresh_from_env()
    assert feed_config.FRESH_PUBDATE_WINDOW_MIN == 0


def test_fresh_pubdate_window_min_negative_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` followed by ``max(..., 0)`` already coerces negatives to
    # zero. The new cap must not change that lower-bound contract.
    monkeypatch.setenv("FRESH_PUBDATE_WINDOW_MIN", "-5")
    feed_config.refresh_from_env()
    assert feed_config.FRESH_PUBDATE_WINDOW_MIN == 0


def test_fresh_pubdate_window_min_garbage_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` falls back to the default on parse failure; the default
    # is itself well within the cap.
    monkeypatch.setenv("FRESH_PUBDATE_WINDOW_MIN", "garbage")
    feed_config.refresh_from_env()
    assert feed_config.FRESH_PUBDATE_WINDOW_MIN == DEFAULT_FRESH_PUBDATE_WINDOW_MIN


def test_fresh_pubdate_window_min_unset_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRESH_PUBDATE_WINDOW_MIN", raising=False)
    feed_config.refresh_from_env()
    assert feed_config.FRESH_PUBDATE_WINDOW_MIN == DEFAULT_FRESH_PUBDATE_WINDOW_MIN
