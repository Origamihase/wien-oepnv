"""WL marks a finished incident ``"status": "resolved"`` and keeps it one fetch.

Real case (raw data 2026-10-05 19:01): "13A: Polizeieinsatz" came twice, the
original ``I20261005-0035`` already resolved and its follow-up
``I20261005-0035-F01`` active. Both share one identity, and the feed showed
the resolved text ("Fahrtbehinderung … Voraussichtliche Dauer: 19:15 Uhr")
instead of the follow-up's.

The incident's display tickers (``stoerungkurz``) carry no status and ran on
after the incident closed, from 15 minutes (36B) to four and a half hours
(15A), so they leave with it (``src/providers/wl_resolved.py``). The shapes
below are WL's, copied from ``data/raw/wl/trafficInfoList.json``; only the
times are moved next to now, in UTC so no clock change can skew them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

import pytest

from src.providers import wl_resolved
from src.utils import raw_capture

# Minutes are counted from the incident's start, which lies 60 minutes back.
_START = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=60)


def _at(minutes: float) -> str:
    return (_START + timedelta(minutes=minutes)).isoformat()


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


def _message(
    name: str, status: str, line: str, title: str, description: str, *, closed: float, html: str | None = None
) -> dict[str, Any]:
    """A long message (``stoerunglang``); a resolved one ends when WL closed it."""
    return {
        "attributes": {"relatedLineTypes": {line: "ptBusCity"}},
        "description": description,
        "descriptionHTML": html or description,
        "name": name,
        "owner": "WL",
        "priority": "1",
        "refTrafficInfoCategoryId": 2,
        "relatedLines": [line],
        "status": status,
        "time": {
            "created": _at(1),
            "end": _at(closed) if status == "resolved" else _at(240),
            "lastUpdate": _at(closed),
            "resume": _at(closed),
            "start": _at(0),
        },
        "title": title,
    }


def _ticker(name: str, title: str, *, began: float, line: str | None) -> dict[str, Any]:
    """A display-board ticker (``stoerungkurz``): no status, title and text alike."""
    info: dict[str, Any] = {
        "description": title,
        "name": name,
        "owner": "WL",
        "priority": "1",
        "refTrafficInfoCategoryId": 3,
        "relatedStops": [int(name[1:].split("-")[0])],
        "time": {"end": _at(began + 120), "start": _at(began)},
        "title": title,
    }
    if line:
        info["relatedLines"] = [line]
    return info


_ORIGINAL = (
    "Linie 13A: Fahrtbehinderung in Richtung Alser Straße, Skodagasse. "
    "Voraussichtliche Dauer: 19:15 Uhr. Grund: Polizeieinsatz im Bereich Kolschitzkygasse."
)
_FOLLOW_UP = "Linie 13A: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."


def _13a(status: str) -> dict[str, Any]:
    return _message("I20261005-0035", status, "13A", "13A: Polizeieinsatz", _ORIGINAL, closed=6)


def _13a_follow_up() -> dict[str, Any]:
    return _message("I20261005-0035-F01", "active", "13A", "13A: Polizeieinsatz", _FOLLOW_UP, closed=6)


def test_a_resolved_incident_is_not_in_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _fetch(monkeypatch, [_13a("resolved")]) == []
    assert raw_capture.collected_drops("wl") == [{"grund": "Status inaktiv", "titel": "13A: Polizeieinsatz"}]


def test_the_active_follow_up_shows_its_own_text(monkeypatch: pytest.MonkeyPatch) -> None:
    (event,) = _fetch(monkeypatch, [_13a("resolved"), _13a_follow_up()])
    assert "unterschiedlichen Intervallen" in event["description"]
    assert "Voraussichtliche Dauer" not in event["description"]


def test_an_active_incident_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    (event,) = _fetch(monkeypatch, [_13a("active")])
    assert "Voraussichtliche Dauer" in event["description"]


# --- display tickers -------------------------------------------------------


def _36b_tickers(began: float = 2.5) -> list[dict[str, Any]]:
    return [
        _ticker(f"R{stop}-236", "Fahrtbehinderung\nFremder Verkehrsunfall", began=began, line="36B")
        for stop in (1091, 8485, 8486, 8487)
    ]


def _36b_closed() -> dict[str, Any]:
    return _message(
        "I20261005-0023-F03",
        "resolved",
        "36B",
        "36B: Fremder Verkehrsunfall",
        "Linie 36B: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        closed=46,
    )


def test_the_tickers_of_a_closed_incident_leave_with_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """05.10. 13:01: the four 36B tickers had become a new item on place 1."""
    assert _fetch(monkeypatch, [_36b_closed(), *_36b_tickers()]) == []
    reasons = {drop["grund"] for drop in raw_capture.collected_drops("wl")}
    assert reasons == {"Status inaktiv", "Kurzmeldung einer erledigten Störung"}


_U6_TEXT = (
    "Die Linie U6 fährt derzeit nicht zwischen Westbahnhof und Längenfeldgasse. Weichen Sie ersatzweise "
    "auf die Linien U4, 6 und 18 aus. Voraussichtliche Dauer: 11:30 Uhr. Grund: Rettungseinsatz im "
    "Haltestellenbereich Gumpendorfer Straße."
)
_U6_TICKER = "U6 Störung\nKein Betrieb zwischen Westbahnhof und Längenfeldgasse"


def _u6(status: str) -> dict[str, Any]:
    return _message("I20261005-0017", status, "U6", "U6: Rettungseinsatz", _U6_TEXT, closed=35)


def _u6_follow_up() -> dict[str, Any]:
    return _message(
        "I20261005-0017-F01",
        "active",
        "U6",
        "U6: Rettungseinsatz",
        "Linie U6: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        closed=35,
    )


def _u6_tickers() -> list[dict[str, Any]]:
    # WL sends these without relatedLines (name "R…-0"); the line is in the title.
    return [_ticker(f"R{stop}-0", _U6_TICKER, began=10, line=None) for stop in (1199, 1200)]


def test_tickers_naming_their_line_only_in_the_title_leave_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """05.10. 11:01: the feed said the U6 did not run, after WL had closed the incident."""
    (event,) = _fetch(monkeypatch, [_u6("resolved"), _u6_follow_up(), *_u6_tickers()])
    assert "Kein Betrieb" not in event["description"]
    assert "unterschiedlichen Intervallen" in event["description"]


def test_the_tickers_stay_away_after_the_closed_message_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """WL sends the resolved message once; its tickers ran on for up to 4.5 hours (15A, 04.10.)."""
    _fetch(monkeypatch, [_u6("resolved"), _u6_follow_up(), *_u6_tickers()])
    (event,) = _fetch(monkeypatch, [_u6_follow_up(), *_u6_tickers()])
    assert "Kein Betrieb" not in event["description"]
    assert _fetch(monkeypatch, _u6_tickers()) == []


def test_tickers_of_a_running_incident_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _fetch(monkeypatch, [_u6("active"), *_u6_tickers()])
    assert any("Kein Betrieb" in event["title"] + event["description"] for event in events)
    assert wl_resolved.remembered() == {}


def test_a_ticker_saying_something_the_closed_incident_does_not_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    """04.10. 23:31: "66A: Störung an einem Bahnübergang" closed beside the works ticker of 66A."""
    closed = _message(
        "I20261004-0025",
        "resolved",
        "66A",
        "66A: Störung an einem Bahnübergang",
        "Die Linie 66A wird in beiden Richtungen zwischen Klingerstraße und Schwarzenhaidestraße über die "
        "Gutheil-Schoder-Straße umgeleitet. Grund: Störung an einem Bahnübergang.",
        closed=10,
    )
    works = _ticker("R1974-466", "Bauarbeiten\nBusse halten Salvatorianerplatz", began=1, line="66A")
    (event,) = _fetch(monkeypatch, [closed, works])
    assert "Salvatorianerplatz" in event["title"] + event["description"]


def test_a_ticker_begun_after_the_closing_belongs_to_a_new_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _fetch(monkeypatch, [_36b_closed(), *_36b_tickers(began=50)])
    assert events and "Fremder Verkehrsunfall" in events[0]["title"] + events[0]["description"]


def test_a_ticker_fitting_a_running_incident_of_another_number_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    other = _message(
        "I20261005-0024",
        "active",
        "36B",
        "36B: Fremder Verkehrsunfall",
        "Linie 36B: Fahrtbehinderung in Richtung Ruthnergasse. Grund: Fremder Verkehrsunfall.",
        closed=46,
    )
    events = _fetch(monkeypatch, [_36b_closed(), other, *_36b_tickers()])
    assert len(events) == 1
    assert wl_resolved.remembered() == {}


def test_a_reused_ticker_counts_as_new(monkeypatch: pytest.MonkeyPatch) -> None:
    """WL may reuse a ticker's name for a later incident; its start or title then differ."""
    _fetch(monkeypatch, [_36b_closed(), *_36b_tickers()])
    assert len(wl_resolved.remembered()) == 4
    events = _fetch(monkeypatch, _36b_tickers(began=55))
    assert events and wl_resolved.remembered() == {}


# --- memory across runs (scripts/update_wl_cache.py) ------------------------


def test_the_memory_survives_a_run_and_forgets_what_wl_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "wl_resolved_tickers.json"
    _fetch(monkeypatch, [_u6("resolved"), _u6_follow_up(), *_u6_tickers()])
    wl_resolved.save_memory(path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert [record["name"] for record in stored["tickers"]] == ["R1199-0", "R1200-0"]
    assert {record["incident"] for record in stored["tickers"]} == {"I20261005-0017"}

    wl_resolved.forget()
    wl_resolved.load_memory(path)
    assert _fetch(monkeypatch, _u6_tickers()[:1]) == []
    wl_resolved.save_memory(path)
    assert [record["name"] for record in json.loads(path.read_text(encoding="utf-8"))["tickers"]] == ["R1199-0"]

    _fetch(monkeypatch, [_u6_follow_up()])
    wl_resolved.save_memory(path)
    assert json.loads(path.read_text(encoding="utf-8"))["tickers"] == []


def test_no_file_without_anything_to_remember(tmp_path: Path) -> None:
    path = tmp_path / "wl_resolved_tickers.json"
    wl_resolved.save_memory(path)
    assert not path.exists()


def test_a_broken_memory_file_is_no_memory(tmp_path: Path) -> None:
    path = tmp_path / "wl_resolved_tickers.json"
    path.write_text("{not json", encoding="utf-8")
    wl_resolved.load_memory(path)
    assert wl_resolved.remembered() == {}
