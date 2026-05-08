"""Tests for the cache-driven S-Bahn Stammstrecke provider.

The provider is the *read* half of the canonical cache-driven
provider architecture: ``scripts/update_gtfs_cache.py`` polls the
ÖBB GTFS-Realtime feed and writes ``cache/gtfs_stammstrecke/events.json``;
this module deserialises that document into a single :class:`FeedItem`.
The suite drives the provider with hand-built JSON payloads and
asserts the title text, the ``[Seit DD.MM.YYYY]`` description prefix,
and the threshold + self-heal contracts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.providers import gtfs_stammstrecke as provider


# ------------------------------------------------------------------ helpers


def _write_cache(tmp_path: Path, document: dict[str, Any]) -> Path:
    cache_path = tmp_path / "cache" / "gtfs_stammstrecke" / "events.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return cache_path


def _active_state(
    *,
    minutes: int = 12,
    active_trips: int = 5,
    first_seen: str = "2026-05-08T08:30:00+02:00",
    updated: str | None = None,
) -> dict[str, Any]:
    return {
        "guid": "stammstrecke-fixture-guid",
        "first_seen": first_seen,
        "updated": updated or "2026-05-08T10:00:00+02:00",
        "average_delay_minutes": minutes,
        "active_trips": active_trips,
    }


# -------------------------------------------------------- threshold logic


def test_fetch_events_returns_empty_when_cache_missing(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache" / "gtfs_stammstrecke" / "events.json"
    assert provider.fetch_events(cache_path=cache_path) == []


def test_fetch_events_returns_empty_when_events_list_empty(tmp_path: Path) -> None:
    cache_path = _write_cache(tmp_path, {"events": [], "metadata": {}})
    assert provider.fetch_events(cache_path=cache_path) == []


def test_fetch_events_returns_empty_when_average_below_threshold(tmp_path: Path) -> None:
    document = {
        "events": [_active_state(minutes=provider.STAMMSTRECKE_THRESHOLD_MINUTES)],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    assert provider.fetch_events(cache_path=cache_path) == []


def test_fetch_events_returns_empty_when_payload_is_not_object(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache" / "gtfs_stammstrecke" / "events.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert provider.fetch_events(cache_path=cache_path) == []


def test_fetch_events_returns_empty_when_first_event_is_not_object(tmp_path: Path) -> None:
    cache_path = _write_cache(tmp_path, {"events": ["not-a-dict"], "metadata": {}})
    assert provider.fetch_events(cache_path=cache_path) == []


def test_fetch_events_returns_empty_when_first_seen_missing(tmp_path: Path) -> None:
    document = {
        "events": [{"average_delay_minutes": 12, "active_trips": 5}],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    assert provider.fetch_events(cache_path=cache_path) == []


# -------------------------------------------------------- title formatting


def test_fetch_events_renders_canonical_title(tmp_path: Path) -> None:
    document = {
        "events": [_active_state(minutes=12, active_trips=5)],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)

    items = provider.fetch_events(cache_path=cache_path)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == (
        "S-Bahn Stammstrecke: Derzeit durchschnittlich 12 Minuten Verspätung"
    )


def test_fetch_events_rounds_minutes_in_title(tmp_path: Path) -> None:
    document = {
        "events": [_active_state(minutes=11)],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    assert items[0]["title"].endswith("11 Minuten Verspätung")


# -------------------------------------------------------- description format


def test_fetch_events_description_starts_with_seit_prefix(tmp_path: Path) -> None:
    document = {
        "events": [_active_state(first_seen="2026-04-15T07:42:00+02:00")],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    description = items[0]["description"]
    assert description.startswith("[Seit 15.04.2026]"), description


def test_fetch_events_description_uses_vienna_local_date(tmp_path: Path) -> None:
    """A UTC ``first_seen`` near midnight Vienna time renders the local date."""
    document = {
        "events": [_active_state(first_seen="2026-05-08T22:30:00+00:00")],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    description = items[0]["description"]
    # 22:30 UTC on 2026-05-08 is 00:30 on 2026-05-09 in Vienna (CEST, UTC+2).
    assert description.startswith("[Seit 09.05.2026]"), description


def test_fetch_events_description_includes_corridor_summary(tmp_path: Path) -> None:
    document = {
        "events": [_active_state(minutes=14, active_trips=7)],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    description = items[0]["description"]
    assert "Wien Floridsdorf" in description
    assert "Wien Meidling" in description
    assert "<b>14 Minuten</b>" in description
    assert "<b>7 Züge</b>" in description
    assert "Datenquelle: ÖBB GTFS-Realtime." in description


def test_fetch_events_description_handles_missing_trip_count(tmp_path: Path) -> None:
    document = {
        "events": [
            {
                "first_seen": "2026-05-08T08:30:00+02:00",
                "updated": "2026-05-08T10:00:00+02:00",
                "average_delay_minutes": 11,
            }
        ],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    description = items[0]["description"]
    assert description.startswith("[Seit 08.05.2026]")
    assert "<b>11 Minuten</b>" in description


# -------------------------------------------------------- pubDate / metadata


def test_fetch_events_emits_pubDate_from_updated(tmp_path: Path) -> None:
    document = {
        "events": [_active_state(updated="2026-05-08T11:15:00+02:00")],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    pub_date = items[0]["pubDate"]
    assert pub_date is not None
    assert pub_date.isoformat() == "2026-05-08T11:15:00+02:00"


def test_fetch_events_falls_back_to_first_seen_when_updated_missing(
    tmp_path: Path,
) -> None:
    document = {
        "events": [
            {
                "first_seen": "2026-05-08T08:30:00+02:00",
                "average_delay_minutes": 12,
                "active_trips": 5,
            }
        ],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    pub_date = items[0]["pubDate"]
    assert pub_date is not None
    assert pub_date.isoformat() == "2026-05-08T08:30:00+02:00"


def test_fetch_events_uses_explicit_guid_when_present(tmp_path: Path) -> None:
    document = {
        "events": [_active_state()],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    assert items[0]["guid"] == "stammstrecke-fixture-guid"


def test_fetch_events_synthesises_guid_when_missing(tmp_path: Path) -> None:
    document = {
        "events": [
            {
                "first_seen": "2026-05-08T08:30:00+02:00",
                "average_delay_minutes": 12,
                "active_trips": 5,
            }
        ],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    assert isinstance(items[0]["guid"], str)
    assert len(items[0]["guid"]) == 64  # SHA256 hex


# -------------------------------------------------------- shape contracts


def test_fetch_events_emits_known_source_and_category(tmp_path: Path) -> None:
    document = {
        "events": [_active_state()],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    assert items[0]["source"] == provider.STAMMSTRECKE_SOURCE
    assert items[0]["category"] == provider.STAMMSTRECKE_CATEGORY
    assert items[0]["link"] == provider.STAMMSTRECKE_LINK


def test_fetch_events_yields_at_most_one_item(tmp_path: Path) -> None:
    """Even if the cache document accidentally lists multiple events, only
    the first active event is rendered (the contract that lets the merged
    feed self-heal)."""
    document = {
        "events": [
            _active_state(minutes=12),
            _active_state(minutes=20, first_seen="2026-05-08T07:00:00+02:00"),
        ],
        "metadata": {},
    }
    cache_path = _write_cache(tmp_path, document)
    items = provider.fetch_events(cache_path=cache_path)
    assert len(items) == 1
    assert "12 Minuten" in items[0]["title"]


def test_load_cache_document_returns_none_when_missing(tmp_path: Path) -> None:
    assert provider.load_cache_document(tmp_path / "missing.json") is None


def test_load_cache_document_returns_none_when_top_level_not_dict(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert provider.load_cache_document(cache_path) is None


# -------------------------------------------------------- builder helper


def test_build_event_from_state_returns_none_below_threshold() -> None:
    state = _active_state(minutes=provider.STAMMSTRECKE_THRESHOLD_MINUTES)
    assert provider.build_event_from_state(state) is None


def test_build_event_from_state_uses_rounded_minutes_when_average_missing() -> None:
    state = {
        "first_seen": "2026-05-08T08:30:00+02:00",
        "rounded_minutes": 11,
    }
    event = provider.build_event_from_state(state)
    assert event is not None
    assert "11 Minuten" in event["title"]


def test_default_cache_path_resolves_to_repo_relative_location() -> None:
    assert provider.DEFAULT_CACHE_PATH.parts[-3:] == (
        "cache",
        "gtfs_stammstrecke",
        "events.json",
    )


# Marker import to keep ruff happy with the typing-only ``pytest`` use:
_ = pytest
