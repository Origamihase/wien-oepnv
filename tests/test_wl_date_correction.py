from types import TracebackType
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest

from src.providers.wl_fetch import fetch_events

class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "DummySession":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False

def _setup_fetch(
    monkeypatch: pytest.MonkeyPatch,
    traffic_infos: list[dict[str, Any]] | None = None,
    news: list[dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setattr(
        "src.providers.wl_fetch._fetch_traffic_infos",
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

# Any: overrides accepts arbitrary kwargs; values vary per test
def _base_event(**overrides: Any) -> dict[str, Any]:
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

def test_reproduction_linie_4a_date_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
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

def test_reproduction_linie_n62_date_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
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

def test_monthname_advance_notice_sets_starts_at(monkeypatch: pytest.MonkeyPatch) -> None:
    # Spelled-out month form (real WL shape, e.g. "ab 07. April 2026").
    # The legacy regex ignored it, so starts_at silently fell back to the
    # API publication date; it must now win as the effective start.
    traffic_info = _base_event(
        title="56A: Bauarbeiten Maxingstraße ab 07. April 2026",
        time={"start": "2026-01-10T00:00:00.000+01:00"},
        attributes={"relatedLines": ["56A"]},
    )

    _setup_fetch(monkeypatch, traffic_infos=[traffic_info])

    events = fetch_events()
    assert len(events) == 1
    ev = events[0]

    start_dt = ev["starts_at"]
    assert start_dt.year == 2026
    assert start_dt.month == 4
    assert start_dt.day == 7

    # PubDate should remain the API publication date.
    assert ev["pubDate"].year == 2026
    assert ev["pubDate"].month == 1
    assert ev["pubDate"].day == 10


@pytest.mark.parametrize("kind", ["traffic_infos", "news"])
def test_missing_start_does_not_take_the_end(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # Without ``time.start`` the end used to become the start: the item
    # looked like it had not begun and stayed out of the feed until it ended.
    end = datetime.now(UTC) + timedelta(days=60)
    updated = datetime.now(UTC) - timedelta(days=1)
    event = _base_event(
        title="Gleisschaden",
        description="Umleitung: Kein Betrieb zwischen A und B.",
        time={"end": end.isoformat()},
        updated=updated.isoformat(),
        relatedLines=["5"],
    )
    _setup_fetch(monkeypatch, **{kind: [event]})

    events = fetch_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["starts_at"] == updated
    assert ev["pubDate"] == updated
    assert ev["ends_at"] == end


def test_missing_start_without_publication_stays_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    end = datetime.now(UTC) + timedelta(days=60)
    event = _base_event(
        title="Gleisschaden",
        description="Kein Betrieb zwischen A und B.",
        time={"end": end.isoformat()},
        relatedLines=["5"],
    )
    _setup_fetch(monkeypatch, traffic_infos=[event])

    events = fetch_events()
    assert len(events) == 1
    assert events[0]["starts_at"] is None
    assert events[0]["ends_at"] == end
