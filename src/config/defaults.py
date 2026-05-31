"""Shared default values for configuration and tooling."""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "DEFAULT_OUT_PATH",
    "DEFAULT_FEED_HEALTH_PATH",
    "DEFAULT_FEED_HEALTH_JSON_PATH",
    "DEFAULT_FEED_TITLE",
    "DEFAULT_FEED_DESCRIPTION",
    "DEFAULT_FEED_LINK",
    "DEFAULT_PAGES_BASE_URL",
    "DEFAULT_FEED_TTL_MINUTES",
    "DEFAULT_TITLE_CHAR_LIMIT",
    "DEFAULT_DESCRIPTION_CHAR_LIMIT",
    "DEFAULT_FRESH_PUBDATE_WINDOW_MIN",
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_MAX_ITEM_AGE_DAYS",
    "DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS",
    "DEFAULT_ENDS_AT_GRACE_MINUTES",
    "DEFAULT_CACHE_MAX_AGE_HOURS",
    "DEFAULT_PROVIDER_TIMEOUT",
    "DEFAULT_PROVIDER_MAX_WORKERS",
    "DEFAULT_STATE_PATH",
    "DEFAULT_STATE_RETENTION_DAYS",
    "DEFAULT_PROVIDER_FLAGS",
]

DEFAULT_OUT_PATH = Path("docs/feed.xml")
DEFAULT_FEED_HEALTH_PATH = Path("docs/feed-health.md")
DEFAULT_FEED_HEALTH_JSON_PATH = Path("docs/feed-health.json")
DEFAULT_FEED_TITLE = "ÖPNV Störungen Wien & Pendler"
DEFAULT_FEED_DESCRIPTION = "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen"
DEFAULT_FEED_LINK = "https://github.com/Origamihase/wien-oepnv"
DEFAULT_PAGES_BASE_URL = "https://origamihase.github.io/wien-oepnv"
DEFAULT_FEED_TTL_MINUTES = 15
DEFAULT_TITLE_CHAR_LIMIT = 256
DEFAULT_DESCRIPTION_CHAR_LIMIT = 4000
DEFAULT_FRESH_PUBDATE_WINDOW_MIN = 5
DEFAULT_MAX_ITEMS = 10
DEFAULT_MAX_ITEM_AGE_DAYS = 365
DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS = 540
DEFAULT_ENDS_AT_GRACE_MINUTES = 10
DEFAULT_CACHE_MAX_AGE_HOURS = 24
DEFAULT_PROVIDER_TIMEOUT = 25
DEFAULT_PROVIDER_MAX_WORKERS = 0
DEFAULT_STATE_PATH = Path("data/first_seen.json")
# Must stay >= DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS so a long-running disruption's
# first_seen is retained for the item's whole feed lifetime. With a shorter
# window (the prior default was 60 << 540), _load_state dropped a still-produced
# item's state once its first_seen crossed the window; _update_item_state then
# reset first_seen to "now", so the item re-published (fresh pubDate), jumped to
# the top of the recency sort, could never reach the age cutoff, and was
# re-counted in the stats. 600 = 540 (the absolute age cap) + 60 days margin so
# the age machinery retires the item before its state is pruned.
DEFAULT_STATE_RETENTION_DAYS = 600
DEFAULT_PROVIDER_FLAGS = {
    "WL_ENABLE": True,
    "OEBB_ENABLE": True,
    "BAUSTELLEN_ENABLE": True,
    "STAMMSTRECKE_ENABLE": True,
    # VOR_ENABLE intentionally absent — VOR API access scoped to the
    # Stammstrecke delay monitor only (operator policy 2026-05-11).
}
