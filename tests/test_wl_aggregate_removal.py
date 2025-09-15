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
        wl_fetch, "_fetch_traffic_infos", lambda timeout=20: [aggregate, single1, single2]
    )
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda timeout=20: [])

    items = wl_fetch.fetch_events()
    titles = [it["title"] for it in items]

    assert "U1: Single1" in titles
    assert "U2: Single2" in titles
    assert "U1/U2: Aggregate" not in titles


def test_aggregate_retained_when_single_missing(monkeypatch):
    aggregate = _make_event("Aggregate", ["U1", "U2"])
    single1 = _make_event("Single1", ["U1"])

    monkeypatch.setattr(
        wl_fetch, "_fetch_traffic_infos", lambda timeout=20: [aggregate, single1]
    )
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda timeout=20: [])

    items = wl_fetch.fetch_events()
    titles = [it["title"] for it in items]

    assert "U1/U2: Aggregate" in titles
    assert "U1: Single1" in titles
    assert len(items) == 2
