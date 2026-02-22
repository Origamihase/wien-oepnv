
import inspect
from typing import get_type_hints, List
from src.providers import vor, oebb
from src.feed_types import FeedItem

def test_vor_provider_interface():
    """Verify that vor module exposes fetch_events matching the Provider protocol."""
    assert hasattr(vor, "fetch_events")
    hints = get_type_hints(vor.fetch_events)
    # Check return type is List[FeedItem]
    # Note: Depending on how FeedItem is imported/defined, we check equality
    assert hints['return'] == List[FeedItem]

def test_oebb_provider_interface():
    """Verify that oebb module exposes fetch_events matching the Provider protocol."""
    assert hasattr(oebb, "fetch_events")
    hints = get_type_hints(oebb.fetch_events)
    assert hints['return'] == List[FeedItem]
