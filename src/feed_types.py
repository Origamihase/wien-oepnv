"""Common Type Definitions for wien-oepnv."""

from __future__ import annotations

from datetime import datetime
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable


class FeedItem(TypedDict):
    """
    Standardized Feed Item structure.
    Used across providers and the feed builder.
    """
    title: str
    link: str
    description: str

    # Optional metadata
    guid: NotRequired[str]
    pubDate: NotRequired[datetime | None]
    starts_at: NotRequired[datetime | None]
    ends_at: NotRequired[datetime | None]

    source: NotRequired[str]
    category: NotRequired[str]

    # Internal processing fields
    _identity: NotRequired[str]
    _calculated_identity: NotRequired[str]
    _calculated_dedupe_key: NotRequired[str]
    _calculated_recency: NotRequired[datetime]
    _calculated_end: NotRequired[datetime]


@runtime_checkable
class Provider(Protocol):
    """Protocol for disruption providers."""
    def fetch_events(self, *args: Any, **kwargs: Any) -> list[FeedItem]:
        ...
