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
    name: str,
    status: str,
    line: str,
    title: str,
    description: str,
    *,
    closed: float,
    created: float = 1,
    start: float = 0,
    html: str | None = None,
) -> dict[str, Any]:
    """A long message (``stoerunglang``); a resolved one ends when WL closed it.

    WL creates the original a minute after its start and a follow-up
    (``…-F01``) when the disruption ends; a follow-up keeps the start.
    """
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
            "created": _at(created),
            "end": _at(closed) if status == "resolved" else _at(240),
            "lastUpdate": _at(closed),
            "resume": _at(closed),
            "start": _at(start),
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
    return _message("I20261005-0035-F01", "active", "13A", "13A: Polizeieinsatz", _FOLLOW_UP, closed=6, created=6)


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
        created=25,
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
        created=35,
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


def test_a_works_ticker_beside_a_closed_incident_stays(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_a_running_follow_up_of_the_same_incident_does_not_keep_its_tickers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """05.10. 19:01: the 13A tickers fit the active follow-up's title as well."""
    tickers = [
        _ticker(f"R{stop}-413", "Fahrtbehinderung\nwegen Polizeieinsatz", began=5, line="13A")
        for stop in (693, 694)
    ]
    (event,) = _fetch(monkeypatch, [_13a("resolved"), _13a_follow_up(), *tickers])
    assert "unterschiedlichen Intervallen" in event["description"]
    assert sorted(record["name"] for record in wl_resolved.remembered().values()) == ["R693-413", "R694-413"]


def test_a_ticker_of_another_line_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    other_line = _ticker("R1091-213", "Fahrtbehinderung\nFremder Verkehrsunfall", began=2.5, line="13A")
    events = _fetch(monkeypatch, [_36b_closed(), other_line])
    assert events and wl_resolved.remembered() == {}


def test_a_ticker_may_begin_at_most_ten_minutes_before_the_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    """The earliest real one began 62 s before (13A, 04.10.)."""
    assert _fetch(monkeypatch, [_36b_closed(), *_36b_tickers(began=-9)]) == []
    wl_resolved.forget()
    events = _fetch(monkeypatch, [_36b_closed(), *_36b_tickers(began=-15)])
    assert events and wl_resolved.remembered() == {}


def test_a_ticker_leaves_whatever_cause_it_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """07.10.: the display said "Schadhafter Zug", the message "25, 26A: Betriebsstörung"."""
    closed = _message(
        "I20261007-0003",
        "resolved",
        "25",
        "25, 26A: Betriebsstörung",
        "Linie 25: Fahrtbehinderung in Richtung Aspern, Oberdorfstraße. Grund: Betriebsstörung.",
        closed=40,
    )
    tickers = [_ticker(f"R{stop}-125", "Fahrtbehinderung\nSchadhafter Zug", began=3, line="25") for stop in (2373, 2374)]
    assert _fetch(monkeypatch, [closed, *tickers]) == []


def test_a_ticker_leaves_whatever_street_it_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """08.10.: "6: Rettungseinsatz züge halten Favoritenstraße 113" stood on place 2 after 10:09.

    WL had closed the incident's aftermath notice, which names no street;
    the ticker ran on until 14:00.
    """
    closed = _message(
        "I20261008-0002-F01",
        "resolved",
        "6",
        "6: Rettungseinsatz",
        "Linie 6: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        closed=167,
        created=27,
    )
    ticker = _ticker("R404-106", "Rettungseinsatz\nzüge halten Favoritenstraße 113", began=1, line="6")
    assert _fetch(monkeypatch, [closed, ticker]) == []
    assert [record["name"] for record in wl_resolved.remembered().values()] == ["R404-106"]


def test_a_ticker_of_a_newer_running_incident_on_the_line_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    """06.10. 19:04: tickers of "11, O: Fremder Verkehrsunfall" (19:00) as "74A, O" closed on the O."""
    closed = _message(
        "I20261006-0034",
        "resolved",
        "O",
        "74A, O: Verkehrsüberlastung",
        "Linie O: Fahrtbehinderung in Richtung Raxstraße. Grund: Verkehrsüberlastung.",
        closed=70,
    )
    running = _message(
        "I20261006-0037",
        "active",
        "O",
        "11, O: Fremder Verkehrsunfall",
        "Linie O: Fahrtbehinderung in Richtung Raxstraße. Grund: Fremder Verkehrsunfall.",
        closed=70,
        start=40,
    )
    ticker = _ticker("R1500-171", "Fremdunfall\nBetrieb ab Quellenplatz", began=44, line="O")
    events = _fetch(monkeypatch, [closed, running, ticker])
    assert events and wl_resolved.remembered() == {}


def test_a_ticker_of_the_closed_incident_leaves_beside_a_newer_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ticker begun before the newer incident on its line belongs to the older one."""
    closed = _message(
        "I20261006-0034", "resolved", "O", "O: Verkehrsüberlastung", "Linie O: Fahrtbehinderung.", closed=70
    )
    running = _message(
        "I20261006-0037", "active", "O", "O: Fremder Verkehrsunfall", "Linie O: Fahrtbehinderung.", closed=70, start=40
    )
    ticker = _ticker("R1500-171", "Fahrtbehinderung\nVerkehrsüberlastung", began=2, line="O")
    _fetch(monkeypatch, [closed, running, ticker])
    assert [record["name"] for record in wl_resolved.remembered().values()] == ["R1500-171"]


def test_a_works_ticker_switched_on_during_an_incident_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    """06.10. 01:00: the night works of the N49 went up 22 minutes into "N49: Verspätungen"."""
    closed = _message(
        "I20261005-0044", "resolved", "N49", "N49: Verspätungen", "Linie N49: Verspätungen.", closed=60
    )
    works = _ticker("R1448-549", "Gleisbauarbeiten\nBetrieb ab Johnstraße U", began=22, line="N49")
    events = _fetch(monkeypatch, [closed, works])
    assert events and wl_resolved.remembered() == {}


def test_the_works_tickers_of_closed_works_leave(monkeypatch: pytest.MonkeyPatch) -> None:
    """A planned measure WL closes takes its own tickers along (rule 3 holds both ways)."""
    closed = _message(
        "I20261007-0027",
        "resolved",
        "N8",
        "N8: Gleisbauarbeiten",
        "Linie N8: Umleitung wegen Gleisbauarbeiten.",
        closed=200,
    )
    works = _ticker("R1612-508", "Gleisbauarbeiten\nBusse halten Aßmayergasse", began=35, line="N8")
    assert _fetch(monkeypatch, [closed, works]) == []


def _38a_aftermath() -> dict[str, Any]:
    """05.10.: start 11:13, aftermath from 11:30, resolved at 12:59 (here after 58 minutes, not yet)."""
    return _message(
        "I20261005-0021-F01",
        "resolved",
        "38A",
        "38A: Falschparker",
        "Linie 38A: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        closed=58,
        created=17,
    )


def test_a_new_incident_during_the_aftermath_keeps_its_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The aftermath closed 89 minutes after the disruption; a new one in between is news."""
    old = _ticker("R774-438", "Fahrtbehinderung\nFalschparker", began=16.7, line="38A")
    new = _ticker("R805-438", "Fahrtbehinderung\nFalschparker", began=50, line="38A")
    events = _fetch(monkeypatch, [_38a_aftermath(), old, new])
    assert events and [record["name"] for record in wl_resolved.remembered().values()] == ["R774-438"]


def test_a_ticker_begun_with_the_aftermath_still_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    """05.10. 17:49: WL created the aftermath of "42: Falschparker" 52 s before a ticker of it."""
    late = _ticker("R1336-438", "Fahrtbehinderung\nFalschparker", began=17 + 52 / 60, line="38A")
    assert _fetch(monkeypatch, [_38a_aftermath(), late]) == []


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


def test_the_memory_matches_a_title_the_file_scrubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The file drops soft hyphens and the like; the key read back must still match."""
    path = tmp_path / "wl_resolved_tickers.json"
    tickers = [_ticker("R1091-236", "Fahrtbehinderung\nFremder Verkehrsunfall\u00ad", began=2.5, line="36B")]
    assert _fetch(monkeypatch, [_36b_closed(), *tickers]) == []
    wl_resolved.save_memory(path)
    wl_resolved.forget()
    wl_resolved.load_memory(path)
    assert _fetch(monkeypatch, tickers) == []


def test_the_update_script_carries_the_memory_from_run_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` reads the memory before the fetch and writes it after, run by run."""
    from scripts import update_wl_cache
    from src.providers import wl_fetch

    written: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(update_wl_cache, "write_cache", lambda provider, items: written.append(items))
    monkeypatch.setattr(update_wl_cache, "record_plausibility_anomalies", lambda: None)
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda *a, **kw: [])
    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *a, **kw: _Session())

    def run(infos: list[dict[str, Any]]) -> str:
        monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda *a, **kw: infos)
        wl_resolved.forget()  # every run is a new process
        assert update_wl_cache.main() == 0
        return json.dumps(written[-1], ensure_ascii=False)

    assert "Kein Betrieb" not in run([_u6("resolved"), _u6_follow_up(), *_u6_tickers()])
    stored = json.loads(update_wl_cache.RESOLVED_TICKERS.read_text(encoding="utf-8"))
    assert [record["name"] for record in stored["tickers"]] == ["R1199-0", "R1200-0"]
    assert "Kein Betrieb" not in run([_u6_follow_up(), *_u6_tickers()])


def test_no_file_without_anything_to_remember(tmp_path: Path) -> None:
    path = tmp_path / "wl_resolved_tickers.json"
    wl_resolved.save_memory(path)
    assert not path.exists()


def test_a_broken_memory_file_is_no_memory(tmp_path: Path) -> None:
    path = tmp_path / "wl_resolved_tickers.json"
    path.write_text("{not json", encoding="utf-8")
    wl_resolved.load_memory(path)
    assert wl_resolved.remembered() == {}
