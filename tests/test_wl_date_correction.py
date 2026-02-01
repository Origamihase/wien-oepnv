
import pytest
from datetime import datetime, timezone
from src.providers.wl_fetch import fetch_events

class DummySession:
    def __init__(self):
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

def _setup_fetch(monkeypatch, traffic_infos=None, news=None):
    monkeypatch.setattr(
        "src.providers.wl_fetch._fetch_departure_board_for_stations",
        lambda *a, **kw: traffic_infos or [],
    )
    monkeypatch.setattr(
        "src.providers.wl_fetch._fetch_news",
        lambda *a, **kw: news or [],
    )
    monkeypatch.setattr(
        "src.providers.wl_fetch.session_with_retries",
        lambda *a, **kw: DummySession(),
    )

def _base_event(**overrides):
    # Default start date in past (simulating current active message)
    # We pretend "now" is somewhere in Dec 2025 for logic consistency if needed,
    # but actual tests run with real "now" unless mocked.
    # However, fetch_events uses datetime.now(timezone.utc).
    # If we want the event to be active, start must be <= now.
    # So we set start to 2020.
    base = {
        "title": "Meldung",
        "description": "Desc",
        "time": {"start": "2020-01-01T00:00:00.000+01:00"},
        "attributes": {},
    }
    base.update(overrides)
    return base

def test_reproduction_linie_4a_date_mismatch(monkeypatch):
    # Case 1: Linie 4A: Titel sagt "ab 12.01.2026", API "starts_at" is "2025-12-20"
    # The API start date is in the past/present (Dec 2025), so it's active.
    traffic_info = _base_event(
        title="Linie 4A: Verlegung ab 12.01.2026",
        time={"start": "2025-12-20T00:00:00.000+01:00"},
        attributes={"relatedLines": ["4A"]}
    )

    _setup_fetch(monkeypatch, traffic_infos=[traffic_info])

    events = fetch_events()
    assert len(events) == 1
    ev = events[0]

    start_dt = ev["starts_at"]
    # We expect corrected date
    assert start_dt.year == 2026
    assert start_dt.month == 1
    assert start_dt.day == 12

    # PubDate should remain original
    assert ev["pubDate"].year == 2025
    assert ev["pubDate"].month == 12
    assert ev["pubDate"].day == 20

def test_reproduction_linie_n62_date_mismatch(monkeypatch):
    # Case 2: Linie N62: Titel "ab 08.01.2026", API "start" "2025-12-12"
    traffic_info = _base_event(
        title="Linie N62: Umleitung ab 08.01.2026",
        time={"start": "2025-12-12T00:00:00.000+01:00"},
        attributes={"relatedLines": ["N62"]}
    )

    _setup_fetch(monkeypatch, traffic_infos=[traffic_info])

    events = fetch_events()
    assert len(events) == 1
    ev = events[0]

    start_dt = ev["starts_at"]
    # We expect corrected date
    assert start_dt.year == 2026
    assert start_dt.month == 1
    assert start_dt.day == 8

    # PubDate should remain original
    assert ev["pubDate"].year == 2025
    assert ev["pubDate"].month == 12
    assert ev["pubDate"].day == 12
