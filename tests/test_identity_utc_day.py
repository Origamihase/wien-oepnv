"""The fallback identity keys an item's start to its UTC day (code audit 2026-10-31, finding E).

``_identity_for_item`` returns the provider ``_identity`` when there is one;
only an item without it gets the fallback ``source|category|L=…|D=<day>|…``,
whose day ``_ymd_or_none`` takes as the UTC calendar day, while the feed
displays Vienna days and the Wiener Linien identity (``_wl_identity``)
carries the Vienna day. Every current provider also sets a ``guid``, which
the dedupe key and the ``first_seen`` key prefer, so no running item depends
on this fallback. The tests pin the current rule so that a change is a
decision, not an accident.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import src.build_feed as bf
from src.feed_types import FeedItem

VIENNA = ZoneInfo("Europe/Vienna")


def _item(starts_at: datetime, **extra: Any) -> FeedItem:
    """A Wiener-Linien-shaped item without provider ``_identity`` and ``guid``."""
    return cast(
        FeedItem,
        {
            "source": "Wiener Linien",
            "category": "Störung",
            "title": "U4: Test",
            "starts_at": starts_at,
            **extra,
        },
    )


def test_fallback_identity_uses_the_utc_day_of_the_start() -> None:
    # 00:30 Vienna time on 1 November (winter) is 23:30 UTC on 31 October.
    identity = bf._identity_for_item(_item(datetime(2026, 11, 1, 0, 30, tzinfo=VIENNA)))
    assert "|D=2026-10-31|" in identity
    assert "D=2026-11-01" not in identity


def test_fallback_identity_keeps_the_day_from_one_hour_vienna_time() -> None:
    identity = bf._identity_for_item(_item(datetime(2026, 11, 1, 1, 0, tzinfo=VIENNA)))
    assert "|D=2026-11-01|" in identity


def test_provider_identity_and_guid_win_over_the_fallback() -> None:
    start = datetime(2026, 11, 1, 0, 30, tzinfo=VIENNA)
    provider_identity = "wl|störung|L=U4|D=2026-11-01|TK=test"
    assert bf._identity_for_item(_item(start, _identity=provider_identity)) == provider_identity
    assert bf._state_key_for_item(_item(start, guid="wl-guid")) == "wl-guid"
    assert bf._dedupe_key_for_item(_item(start, guid="wl-guid")) == ("wl-guid", False)


def test_ymd_or_none_takes_the_utc_day() -> None:
    # 01:30 Vienna summer time is 23:30 UTC of the day before.
    assert bf._ymd_or_none(datetime(2026, 7, 1, 1, 30, tzinfo=VIENNA)) == "2026-06-30"
    assert bf._ymd_or_none(datetime(2026, 7, 1, 2, 0, tzinfo=VIENNA)) == "2026-07-01"
    assert bf._ymd_or_none(None) == ""
