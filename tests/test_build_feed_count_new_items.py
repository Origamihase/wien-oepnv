"""Regression tests for :func:`src.build_feed._count_new_items`.

The persisted ``first_seen`` state is keyed by the guid-preferring scheme
(:func:`_state_key_for_item`). ``_count_new_items`` previously compared the raw
content identity (:func:`_identity_for_item`) against those keys, so every
guid-bearing item was miscounted as "new" on every run. These tests pin the
corrected behaviour: an item already tracked in the state is not counted.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import src.build_feed as bf
from src.feed_types import FeedItem


def _wl_item(guid: str) -> FeedItem:
    return {
        "title": "U6: Störung im Bereich Längenfeldgasse",
        "link": "https://example.invalid/u6",
        "description": "Test disruption",
        "source": "Wiener Linien",
        "category": "Störung",
        "guid": guid,
        "starts_at": datetime(2026, 5, 1, tzinfo=UTC),
    }


def test_count_new_items_recognises_guid_keyed_state() -> None:
    item = _wl_item("WL-123")
    key = bf._state_key_for_item(item)
    # Sanity: the state key is the guid, not the content identity.
    assert key == "WL-123"
    assert bf._identity_for_item(item) != key

    state: dict[str, dict[str, Any]] = {key: {"first_seen": "2026-05-01T00:00:00+00:00"}}
    assert bf._count_new_items([item], state) == 0


def test_count_new_items_counts_genuinely_new_items() -> None:
    seen = _wl_item("WL-123")
    state: dict[str, dict[str, Any]] = {
        bf._state_key_for_item(seen): {"first_seen": "2026-05-01T00:00:00+00:00"}
    }

    fresh = _wl_item("WL-999")
    assert bf._count_new_items([seen, fresh], state) == 1


def test_count_new_items_empty_state_counts_all() -> None:
    items = [_wl_item("WL-1"), _wl_item("WL-2")]
    assert bf._count_new_items(items, {}) == 2
