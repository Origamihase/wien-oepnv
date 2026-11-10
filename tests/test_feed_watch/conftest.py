"""Macht die Fixtures aus _support.py für die Tests in diesem Paket sichtbar."""

from __future__ import annotations

import uuid

import pytest

from src.feed import config as feed_config

from ._support import (  # noqa: F401
    REPO_ROOT,
    data_file,
)


@pytest.fixture(autouse=True)
def _no_local_health_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``main()`` away from a ``docs/feed-health.json`` of a local build.

    The watcher reads the items the build left out from that file
    (``src/feed_watch/skipped_items.py``). A test that runs ``main()`` without
    ``--health-json`` would otherwise depend on what the last local build
    wrote there.
    """
    absent = REPO_ROOT / "data" / f"test_feed_watch_no_health_{uuid.uuid4().hex}.json"
    monkeypatch.setattr(feed_config, "FEED_HEALTH_JSON_PATH", absent)
