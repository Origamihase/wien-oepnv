"""Regression: the default first_seen state-retention window must cover the
maximum age an item can live in the feed.

If ``DEFAULT_STATE_RETENTION_DAYS`` is shorter than the age caps, ``_load_state``
drops a still-produced long-running item's state once its ``first_seen`` crosses
the retention window; ``_update_item_state`` then resets ``first_seen`` to "now",
so the item re-publishes (fresh pubDate), jumps to the top of the recency sort,
can never reach the age cutoff, and is re-counted in the stats. The shipped
default therefore MUST satisfy retention >= absolute / max item age (the prior
default was 60 << 540).
"""

from __future__ import annotations

from src.config.defaults import (
    DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS,
    DEFAULT_MAX_ITEM_AGE_DAYS,
    DEFAULT_STATE_RETENTION_DAYS,
)


def test_default_state_retention_covers_item_lifetime() -> None:
    assert DEFAULT_STATE_RETENTION_DAYS >= DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS, (
        "State retention must outlast the absolute item-age cap, otherwise a "
        "long-running item's first_seen is pruned before it ages out."
    )
    assert DEFAULT_STATE_RETENTION_DAYS >= DEFAULT_MAX_ITEM_AGE_DAYS
