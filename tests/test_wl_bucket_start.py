"""WL notices bucketed into one item start at the earliest of their starts.

"11: Gleisbauarbeiten" (01.09.–15.10.) and "11: Gleisbauarbeiten ab
20.10.2026" share lines and topic and become one item. It took the latest
start, so the running first phase read as works that only begin on 20.10.
Disruptions keep the latest start: WL reuses old display tickers for a new
incident, and their stale start must not date it back.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

VIENNA = ZoneInfo("Europe/Vienna")


class _Session:
    headers: dict[str, str] = {}

    def __enter__(self) -> _Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False


def _fetch(
    monkeypatch: pytest.MonkeyPatch,
    news: list[dict[str, Any]],
    traffic_infos: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from src.providers import wl_fetch

    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda *a, **kw: list(traffic_infos or []))
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda *a, **kw: list(news))
    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *a, **kw: _Session())
    return wl_fetch.fetch_events()


def _day(offset: int) -> datetime:
    today = datetime.now(VIENNA).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=offset)


def _notice(title: str, start: datetime | None, end: datetime) -> dict[str, Any]:
    time: dict[str, str] = {"end": end.isoformat()}
    if start is not None:
        time["start"] = start.isoformat()
    return {
        "title": title,
        "description": "Wegen Gleisbauarbeiten Umleitung der Linie 11.",
        "time": time,
        "relatedLines": ["11"],
        "attributes": {},
    }


def _phases() -> list[dict[str, Any]]:
    later = _day(20)
    return [
        _notice("Gleisbauarbeiten", _day(-30), _day(14)),
        _notice(f"Gleisbauarbeiten ab {later:%d.%m.%Y}", _day(-5), _day(90)),
    ]


def test_running_phase_keeps_the_bucket_started(monkeypatch: pytest.MonkeyPatch) -> None:
    (event,) = _fetch(monkeypatch, _phases())
    assert event["starts_at"] == _day(-30)
    assert event["ends_at"] == _day(90)


def test_bucket_start_does_not_depend_on_order(monkeypatch: pytest.MonkeyPatch) -> None:
    (event,) = _fetch(monkeypatch, list(reversed(_phases())))
    assert event["starts_at"] == _day(-30)


def test_unknown_start_keeps_the_known_one(monkeypatch: pytest.MonkeyPatch) -> None:
    news = [
        _notice("Gleisbauarbeiten", _day(-30), _day(14)),
        _notice("Gleisbauarbeiten", None, _day(14)),
    ]
    (event,) = _fetch(monkeypatch, news)
    assert event["starts_at"] == _day(-30)


def test_disruption_keeps_the_latest_start(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(VIENNA).replace(microsecond=0)
    reused = _notice("Schadhafter Zug", now - timedelta(days=4), now + timedelta(hours=2))
    new = _notice("Schadhafter Zug", now - timedelta(hours=1), now + timedelta(hours=2))
    for order in ([reused, new], [new, reused]):
        (event,) = _fetch(monkeypatch, [], traffic_infos=order)
        assert event["category"] == "Störung"
        assert event["starts_at"] == now - timedelta(hours=1)
