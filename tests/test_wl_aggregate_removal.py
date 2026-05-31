from datetime import datetime, timedelta, UTC
from typing import Any

import pytest

from src.providers import wl_fetch


def _make_event(title: str, lines: list[str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=1)).isoformat()
    return {
        "title": title,
        "description": "",
        "time": {"start": start, "end": end},
        "relatedLines": lines,
        "relatedStops": [],
        "attributes": {},
    }


def test_aggregate_removed_when_all_singles_present(monkeypatch: pytest.MonkeyPatch) -> None:
    aggregate = _make_event("Aggregate", ["U1", "U2"])
    single1 = _make_event("Single1", ["U1"])
    single2 = _make_event("Single2", ["U2"])

    monkeypatch.setattr(
        wl_fetch,
        "_fetch_traffic_infos",
        lambda timeout=20, session=None: [aggregate, single1, single2],
    )
    monkeypatch.setattr(
        wl_fetch, "_fetch_news", lambda timeout=20, session=None: []
    )

    items = wl_fetch.fetch_events()
    titles = [it["title"] for it in items]

    assert "U1: Single1" in titles
    assert "U2: Single2" in titles
    assert "U1/U2: Aggregate" not in titles


def test_aggregate_retained_when_single_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # This test checks behavior when NOT all singles are present.
    # Previously, it expected both Aggregate and Single1 to be present.
    # With the new subset removal logic, Single1 (subset of Aggregate) is considered redundant and removed.
    # The Aggregate remains because Single2 is missing, so Aggregate is "better" than just Single1.

    aggregate = _make_event("Aggregate", ["U1", "U2"])
    single1 = _make_event("Single1", ["U1"])

    monkeypatch.setattr(
        wl_fetch,
        "_fetch_traffic_infos",
        lambda timeout=20, session=None: [aggregate, single1],
    )
    monkeypatch.setattr(
        wl_fetch, "_fetch_news", lambda timeout=20, session=None: []
    )

    items = wl_fetch.fetch_events()
    titles = [it["title"] for it in items]

    assert "U1/U2: Aggregate" in titles
    # Single1 is removed because it is a subset of Aggregate
    assert "U1: Single1" not in titles
    assert len(items) == 1


def _make_news(title: str, lines: list[str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=1)).isoformat()
    return {
        "title": title,
        "description": "",
        "time": {"start": start, "end": end},
        "relatedLines": lines,
        "relatedStops": [],
        "attributes": {},
    }


def test_aggregate_retained_when_only_other_category_singles_cover_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-line Störung aggregate must NOT be dropped just because
    single-line items of a DIFFERENT category (Hinweis) cover its lines:
    no single-line *Störung* actually covers them, so the disruption alert
    must survive. Section E is category-aware, mirroring section F."""
    aggregate = _make_event("Signalstörung Innenstadt", ["U1", "U2"])
    hinweis1 = _make_news("U1: Umleitung wegen Veranstaltung", ["U1"])
    hinweis2 = _make_news("U2: Umleitung wegen Veranstaltung", ["U2"])

    monkeypatch.setattr(
        wl_fetch,
        "_fetch_traffic_infos",
        lambda timeout=20, session=None: [aggregate],
    )
    monkeypatch.setattr(
        wl_fetch,
        "_fetch_news",
        lambda timeout=20, session=None: [hinweis1, hinweis2],
    )

    items = wl_fetch.fetch_events()
    categories = [it["category"] for it in items]

    # The Störung aggregate survives (cross-category singles do not cover it).
    assert "Störung" in categories, categories
    assert categories.count("Hinweis") == 2
    assert len(items) == 3
