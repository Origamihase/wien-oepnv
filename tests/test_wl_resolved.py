"""WL marks a finished incident ``"status": "resolved"`` and keeps it one fetch.

Real case (raw data 2026-10-05 19:01): "13A: Polizeieinsatz" came twice, the
original ``I20261005-0035`` already resolved and its follow-up
``I20261005-0035-F01`` active. Both share one identity, and the feed showed
the resolved text ("Fahrtbehinderung … Voraussichtliche Dauer: 19:15 Uhr")
instead of the follow-up's.
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


def _fetch(monkeypatch: pytest.MonkeyPatch, infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from src.providers import wl_fetch

    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda *a, **kw: infos)
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda *a, **kw: [])
    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *a, **kw: _Session())
    return wl_fetch.fetch_events()


def _info(name: str, status: str, description: str, html: str, end: datetime) -> dict[str, Any]:
    start = datetime.now(VIENNA).replace(second=0, microsecond=0) - timedelta(minutes=20)
    return {
        "attributes": {"relatedLineTypes": {"13A": "ptBusCity"}},
        "description": description,
        "descriptionHTML": html,
        "location": "Kolschitzkygasse",
        "name": name,
        "owner": "WL",
        "priority": "1",
        "refTrafficInfoCategoryId": 2,
        "relatedLines": ["13A"],
        "relatedLinesDetails": [[{"lineId": "413", "lineName": "13A", "vehicleType": "ptBusCity"}]],
        "status": status,
        "time": {
            "created": start.isoformat(),
            "end": end.isoformat(),
            "lastUpdate": start.isoformat(),
            "resume": start.isoformat(),
            "start": start.isoformat(),
        },
        "title": "13A: Polizeieinsatz",
    }


_ORIGINAL = (
    "Linie 13A: Fahrtbehinderung in Richtung Alser Straße, Skodagasse. "
    "Voraussichtliche Dauer: 19:15 Uhr. Grund: Polizeieinsatz im Bereich Kolschitzkygasse."
)
_ORIGINAL_HTML = (
    "Linie 13A:<br>Fahrtbehinderung in Richtung Alser Straße, Skodagasse.<br> <br>"
    "Voraussichtliche Dauer: 19:15 Uhr.<br><br>Grund: Polizeieinsatz im Bereich Kolschitzkygasse."
)
_FOLLOW_UP = "Linie 13A: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."


def _soon() -> datetime:
    return datetime.now(VIENNA) + timedelta(hours=4)


def test_a_resolved_incident_is_not_in_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    infos = [_info("I20261005-0035", "resolved", _ORIGINAL, _ORIGINAL_HTML, _soon())]
    assert _fetch(monkeypatch, infos) == []


def test_the_active_follow_up_shows_its_own_text(monkeypatch: pytest.MonkeyPatch) -> None:
    infos = [
        _info("I20261005-0035", "resolved", _ORIGINAL, _ORIGINAL_HTML, _soon()),
        _info("I20261005-0035-F01", "active", _FOLLOW_UP, _FOLLOW_UP, _soon()),
    ]
    (event,) = _fetch(monkeypatch, infos)
    assert "unterschiedlichen Intervallen" in event["description"]
    assert "Voraussichtliche Dauer" not in event["description"]


def test_an_active_incident_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    infos = [_info("I20261005-0035", "active", _ORIGINAL, _ORIGINAL_HTML, _soon())]
    (event,) = _fetch(monkeypatch, infos)
    assert "Voraussichtliche Dauer" in event["description"]
