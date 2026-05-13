
from typing import get_type_hints
from src.providers import oebb
from src.feed_types import FeedItem


def test_oebb_provider_interface() -> None:
    """Verify that oebb module exposes fetch_events matching the Provider protocol."""
    assert hasattr(oebb, "fetch_events")
    hints = get_type_hints(oebb.fetch_events)
    assert hints['return'] == list[FeedItem]
