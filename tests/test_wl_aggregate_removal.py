from datetime import datetime, timedelta, timezone

from src.providers import wl_fetch


def _make_event(title: str, lines: list[str]) -> dict:
    now = datetime.now(timezone.utc)
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


def test_aggregate_removed_when_all_singles_present(monkeypatch):
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


def test_aggregate_retained_when_single_missing(monkeypatch):
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
