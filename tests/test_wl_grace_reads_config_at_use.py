"""``src.providers.wl_item_fields`` reads ``ENDS_AT_GRACE_MINUTES`` at use.

``refresh_from_env()`` rebinds ``ENDS_AT_GRACE_MINUTES`` in
``src.feed.config``. ``wl_item_fields`` used to import the name
(``from ..feed.config import ENDS_AT_GRACE_MINUTES``), which copies the value
of the first import; a later change did not reach ``_is_active``, the
function that keeps a WL record for that many minutes past its end. It now
reads ``feed_config.ENDS_AT_GRACE_MINUTES`` on every call, as
``feed_build.filters._drop_after`` does for the feed chain.

Every test sets a value after the import and looks at both sides of the
boundary that value gives. Several values are used, so whatever the value
was at the first import, most of them differ from it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.feed import config as feed_config
from src.providers import wl_item_fields
from tests.test_plausible_years_chain import _disruption, _fetch

# 13:00 in Vienna, the frozen clock of ``tests/test_plausible_years_chain.py``.
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
GRACE_MINUTES = [0, 7, 10, 45]


@pytest.mark.parametrize("minutes", GRACE_MINUTES)
def test_is_active_uses_the_grace_set_after_import(minutes: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feed_config, "ENDS_AT_GRACE_MINUTES", minutes)
    last_kept = NOW - timedelta(minutes=minutes)

    # An end exactly the grace ago is still kept; one second earlier is not.
    assert wl_item_fields._is_active(None, last_kept, NOW) is True
    assert wl_item_fields._is_active(None, last_kept - timedelta(seconds=1), NOW) is False


def test_is_active_follows_a_refresh_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``_feed_config_stays`` (tests/_conftest/zustand.py) restores the configuration afterwards.
    ended_20_minutes_ago = NOW - timedelta(minutes=20)

    monkeypatch.setenv("ENDS_AT_GRACE_MINUTES", "45")
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == 45
    assert wl_item_fields._is_active(None, ended_20_minutes_ago, NOW) is True

    monkeypatch.setenv("ENDS_AT_GRACE_MINUTES", "7")
    feed_config.refresh_from_env()
    assert feed_config.ENDS_AT_GRACE_MINUTES == 7
    assert wl_item_fields._is_active(None, ended_20_minutes_ago, NOW) is False


def test_the_grace_does_not_move_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # A record that has not begun is not active, whatever the grace.
    monkeypatch.setattr(feed_config, "ENDS_AT_GRACE_MINUTES", 45)

    assert wl_item_fields._is_active(NOW + timedelta(seconds=1), None, NOW) is False
    assert wl_item_fields._is_active(NOW, None, NOW) is True
    assert wl_item_fields._is_active(None, None, NOW) is True


@pytest.mark.parametrize(("minutes", "titles"), [(45, ["U1: Feuerwehreinsatz"]), (7, [])])
def test_fetch_keeps_an_ended_disruption_for_the_grace_set_after_import(
    minutes: int, titles: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The disruption ended at 12:40 Vienna, twenty minutes before the frozen clock.
    monkeypatch.setattr(feed_config, "ENDS_AT_GRACE_MINUTES", minutes)
    record = _disruption(
        "I20261110-0001", "U1", "Feuerwehreinsatz", start="2026-11-10T11:10:00.000+0100", end="2026-11-10T12:40:00.000+0100"
    )

    items = _fetch(monkeypatch, tmp_path, [record], [])

    assert sorted(items) == titles
