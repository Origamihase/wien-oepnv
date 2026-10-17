"""Every WL item links to the public Betriebsinfo page, not to the API root.

``https://www.wienerlinien.at/ogd_realtime`` is the API base: a browser gets
404 there (403 with a trailing slash), checked live on 17.10.2026. A reader
who clicked a WL notice in a feed reader or on the dashboard landed on an
error page. The GUID does not depend on the link, so first_seen, dedupe and
the order of the feed stay as they were.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.test_wl_bucket_start import _fetch, _notice

VIENNA = ZoneInfo("Europe/Vienna")


def test_wl_items_link_to_the_betriebsinfo_page(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.providers import wl_fetch

    now = datetime.now(VIENNA).replace(microsecond=0)
    news = [_notice("Gleisbauarbeiten", now - timedelta(days=1), now + timedelta(days=5))]
    disruption = [_notice("Schadhafter Zug", now - timedelta(minutes=30), now + timedelta(hours=2))]
    events = _fetch(monkeypatch, news, traffic_infos=disruption)
    assert len(events) == 2
    for event in events:
        assert event["link"] == wl_fetch.WL_INFO_URL == "https://www.wienerlinien.at/betriebsinfo"
        assert event["link"] != wl_fetch.WL_BASE
