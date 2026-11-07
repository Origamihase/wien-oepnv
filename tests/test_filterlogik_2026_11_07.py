"""Filter rules found wrong in the check of the filter logic (2026-11-07).

Replays of the real chain over the raw WL data of 04.10.–07.11.2026 (1 591
fetch states) and over 1 818 feed builds found four classes:

* a stop relocation whose stop is named after an entrance fell as a closed
  station access ("68A: Laaer Berg, Kurpark Nordeingang", every fetch since
  04.11. 13:01 Vienna time);
* WL's lift records (``elevatorId``) stayed out only because their texts
  carried no restriction word;
* the fuzzy merge joined a live disruption with an announcement of the same
  line ("7A: Bauarbeiten" 09.10., "18: Veranstaltung am 08.11.26" 26.10.) and
  a running incident with a stop relocation naming the same stop ("12:
  Feuerwehreinsatz Betrieb ab Albertgasse" into "5/12: Albertgasse", 30.10.;
  the notice always survives such a merge, so a running incident never
  merges with a notice, a stop notice with no disruption at all);
* a ticker cut by the display board named the bucket of a complete long
  message, and the cut title took the whole incident out of the feed ("1:
  Demonstration Züge halten gegenüber", 24.10. 18:31).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from src.build_feed import _assemble_feed_items, _post_filter_wl
from src.feed.merge import deduplicate_fuzzy
from src.feed.merge_items import _may_merge
from src.feed_types import FeedItem
from src.providers import wl_fetch
from src.providers.wl_text import is_stop_notice
from src.providers.wl_title_context import _facility_drop_reason, _title_quality_key
from src.utils import raw_capture

_FMT = "%Y-%m-%dT%H:%M:%S.000+0100"

_RELOCATION_68A = (
    "<p><strong><u>Haltestellenverlegung der Linie 68A in Richtung Laaer Berg, "
    "Nordosteingang</u></strong></p><p><strong><u>Haltestelle:</u></strong> Laaer Berg, "
    "Kurpark Nordeingang</p><p><strong><u>Von:</u></strong> Filmteichstraße vor Nordeingang "
    "des Kurparks</p><p><strong><u>Nach:</u></strong> Etwa 30 Meter Richtung "
    "Laaer-Berg-Straße</p><p><strong><u>Dauer:</u></strong> 05. November 2026, 07:30 Uhr "
    "für etwa zwei Wochen</p><p><strong><u>Grund:</u></strong> Straßenbau</p>"
)


def _news(name: str, title: str, description: str, now: datetime, **extra: Any) -> dict[str, Any]:
    # Real WL newsList shape (data/raw/wl/newsList.json, N20261104-0003).
    poi: dict[str, Any] = {
        "alertCause": "unknown",
        "alertCauseId": 0,
        "description": description,
        "id": name,
        "location": {"properties": {"attributes": {}, "coordName": "WGS84", "type": "news"}, "type": "Feature"},
        "name": name,
        "refPoiCategoryId": 13296,
        "relatedLines": ["68A"],
        "relatedStops": [3461],
        "time": {
            "end": (now + timedelta(days=12)).strftime(_FMT),
            "start": (now - timedelta(days=2)).strftime(_FMT),
            "validFrom": (now - timedelta(days=2)).strftime(_FMT),
        },
        "title": title,
    }
    poi.update(extra)
    return poi


def _traffic_info(name: str, title: str, description: str, now: datetime, lines: list[str]) -> dict[str, Any]:
    # Real WL trafficInfoList shape (data/raw/wl/trafficInfoList.json).
    return {
        "alertCause": "undefined alert cause",
        "alertCauseId": 255,
        "description": description,
        "name": name,
        "owner": "WL",
        "parentName": name,
        "priority": "1",
        "reasonId": 0,
        "reasonName": "undefined",
        "refTrafficInfoCategoryId": 3 if name.startswith("R") else 1,
        "relatedLines": lines,
        "relatedStops": [45],
        "time": {
            "end": (now + timedelta(hours=3)).strftime(_FMT),
            "start": (now - timedelta(minutes=50)).strftime(_FMT),
        },
        "title": title,
    }


def _fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, infos: list[dict[str, Any]], pois: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    monkeypatch.setattr(raw_capture, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setenv(raw_capture.RAW_CAPTURE_ENV, "1")
    responses = {
        "trafficInfoList": {"data": {"trafficInfos": infos}, "message": {"value": "OK"}},
        "newsList": {"data": {"pois": pois}, "message": {"value": "OK"}},
    }
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: responses[path])
    return wl_fetch.fetch_events()


# --- A stop named after an entrance is not a closed station access ------------


def test_stop_notice_is_recognised_from_its_lead() -> None:
    assert is_stop_notice(_RELOCATION_68A) is True
    assert is_stop_notice("<h2>Aufgangssperre</h2><p>Wegen Sanierungsarbeiten …</p>") is False
    assert is_stop_notice("") is False


def test_relocation_with_entrance_in_the_stop_name_is_no_facility() -> None:
    title = "68A: Laaer Berg, Kurpark Nordeingang"
    # The title alone still reads as an access closure …
    assert _facility_drop_reason(title) == "nur Aufgang/Ausgang"
    # … but the text says it is a stop relocation: the title names the stop.
    assert _facility_drop_reason(title, _RELOCATION_68A) is None
    # A real access closure keeps its drop reason, with or without the text.
    closure = "<h2>Aufgangssperre</h2><p>Wegen Sanierungsarbeiten wird in der Station Nestroyplatz ein Aufgang gesperrt.</p>"
    assert _facility_drop_reason("U1: Nestroyplatz, Aufgangssperre ab 12.10.2026", closure) == "nur Aufgang/Ausgang"


def test_wl_fetch_keeps_the_relocation_of_kurpark_nordeingang(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    items = _fetch(
        monkeypatch,
        tmp_path,
        [],
        [_news("N20261104-0003", "68A: Laaer Berg, Kurpark Nordeingang", _RELOCATION_68A, now)],
    )
    assert [it["title"] for it in items] == ["68A: Laaer Berg, Kurpark Nordeingang"]
    assert items[0]["category"] == "Hinweis"
    assert raw_capture.collected_drops("wl") == []


# --- WL's lift records drop by their field ------------------------------------


def test_lift_record_drops_by_its_elevator_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    lift = _news(
        "ftazW_1016568",
        "Matzleinsdorfer Platz",
        # A restriction word in the text would have passed the keyword gate.
        "Passage - Durchgang zu 6 Bahnsteig Richtung Kaiserebersdorf - Ausgang Matzleinsdorfer Platz gesperrt",
        now,
        elevatorId="MP 06",
        attributes={
            "ausBis": "09.11.2026 00:45",
            "ausVon": "08.11.2026 20:45",
            "relatedLines": ["18", "62", "6", "LB"],
            "station": "Matzleinsdorfer Platz",
            "status": "geplante Wartung",
        },
    )
    lift["relatedLines"] = ["18", "62", "6", "LB"]
    items = _fetch(monkeypatch, tmp_path, [], [lift])

    assert items == []
    assert {"grund": "nur Aufzug/Fahrtreppe", "titel": "Matzleinsdorfer Platz"} in raw_capture.collected_drops("wl")


def test_elevator_id_in_the_attributes_counts_too() -> None:
    assert _facility_drop_reason("Praterstern", "", {"attributes": {"elevatorId": "PS 02"}}) == "nur Aufzug/Fahrtreppe"
    assert _facility_drop_reason("Praterstern", "", {"attributes": {"station": "Praterstern"}}) is None
    assert _facility_drop_reason("Praterstern", "", None) is None


# --- A ticker cut by the board never names the bucket ------------------------


def test_cut_title_ranks_below_every_complete_title() -> None:
    cut = _title_quality_key("Demonstration Züge halten gegenüber", "Demonstration Züge halten gegenüber")
    whole = _title_quality_key("Demonstration", "Demonstration")
    assert cut < whole
    # Among complete titles the informative one still wins.
    assert _title_quality_key("Demonstration Betrieb ab Karlsplatz", "Demonstration Betrieb ab Karlsplatz") > whole


def test_long_message_keeps_its_title_beside_a_cut_ticker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    long_message = _traffic_info(
        "I20261024-0027-F01",
        "Demonstration",
        "Umleitung in beiden Richtungen zwischen Karlsplatz U und Schwedenplatz U über Ring. "
        "Voraussichtliche Dauer: 21:00 Uhr. Grund: Demonstration.",
        now,
        ["1"],
    )
    ticker = _traffic_info("R45-101", "Demonstration\nZüge halten gegenüber", "Demonstration\nZüge halten gegenüber", now, ["1"])

    items = _fetch(monkeypatch, tmp_path, [long_message, ticker], [])

    titles = [it["title"] for it in items]
    assert titles == ["1: Demonstration"], titles
    # The cache filter of the build keeps what the fetch now produces …
    assert [it["title"] for it in _post_filter_wl([dict(items[0])])] == ["1: Demonstration"]
    # … and still drops a ticker that stands alone with its cut title.
    assert _post_filter_wl([{"title": "1: Demonstration Züge halten gegenüber", "category": "Störung"}]) == []


# --- The fuzzy merge: announced stays announced, a stop is not an event ------


def _wl(category: str, title: str, description: str, start: str, end: str | None) -> dict[str, Any]:
    guid = f"wl|{category.casefold()}|{title}"
    return {
        "source": "Wiener Linien",
        "category": category,
        "title": title,
        "description": description,
        "guid": guid,
        "_identity": guid,
        "pubDate": datetime.fromisoformat(start),
        "starts_at": datetime.fromisoformat(start),
        "ends_at": datetime.fromisoformat(end) if end else None,
    }


def _announced_after(day: str) -> Callable[[Mapping[str, Any]], bool]:
    cutoff = datetime.fromisoformat(day)
    return lambda it: bool(it["starts_at"] > cutoff)


def test_announced_notice_does_not_merge_with_the_live_disruption() -> None:
    # 09.10.2026 11:31 UTC (a Friday, preview to Monday 12.10.): the live
    # disruption of line 7A and the notice of the diversion from 14.10.
    live = _wl(
        "Störung", "7A: Bauarbeiten", "Unregelmäßige Intervalle in beiden Richtungen. Grund: Bauarbeiten.",
        "2026-10-09T13:16:00+02:00", "2026-10-09T23:55:00+02:00",
    )
    planned = _wl(
        "Hinweis", "7A: Bauarbeiten",
        "<h2>Bauarbeiten</h2><p>Wegen Bauarbeiten im Bereich Neilreichgasse # Davidgasse wird die Linie 7A umgeleitet.</p>",
        "2026-10-14T00:00:00+02:00", "2026-11-14T23:59:00+01:00",
    )
    merged = deduplicate_fuzzy([live, planned], announced=_announced_after("2026-10-12T23:59:59+02:00"))
    assert sorted(it["category"] for it in merged) == ["Hinweis", "Störung"]
    # Without the predicate (the tests of the merge itself) the old behaviour stands.
    assert len(deduplicate_fuzzy([live, planned])) == 1
    # Two announcements of the same works still merge.
    second = dict(
        planned, title="7A/N62: Bauarbeiten", guid="x", _identity="x",
        starts_at=datetime.fromisoformat("2026-10-15T00:00:00+02:00"),
    )
    assert len(deduplicate_fuzzy([planned, second], announced=_announced_after("2026-10-12T23:59:59+02:00"))) == 1


def test_veranstaltung_of_next_month_keeps_the_live_ticker_apart() -> None:
    live = _wl(
        "Störung", "18: Fahrtbehinderung Veranstaltung", "Unregelmäßige Intervalle in beiden Richtungen. Grund: Veranstaltung.",
        "2026-10-26T17:07:00+01:00", None,
    )
    planned = _wl(
        "Hinweis", "18: Veranstaltung am 08.11.26",
        "<h2>Veranstaltung</h2><p>Wegen einer Veranstaltung im Bereich der Prater Hauptallee wird die Linie 18 kurzgeführt.</p>",
        "2026-11-08T00:00:00+01:00", "2026-11-08T12:30:00+01:00",
    )
    merged = deduplicate_fuzzy([live, planned], announced=_announced_after("2026-10-27T23:59:59+01:00"))
    assert [it["title"] for it in merged] == ["18: Veranstaltung am 08.11.26", "18: Fahrtbehinderung Veranstaltung"]


def test_stop_relocation_never_merges_with_a_disruption() -> None:
    relocation = _wl(
        "Hinweis", "5/12: Albertgasse",
        "<p><strong><u>Haltestellenverlegung der Linien 5 und 12 in Richtung Westbahnhof U bzw. "
        "Josefstädter Straße U</u></strong></p><p>Haltestelle: Albertgasse</p><p>Von: Albertgasse 1</p><p>Nach: Albertgasse 5</p>",
        "2026-10-13T00:00:00+02:00", "2026-11-03T23:59:00+01:00",
    )
    fire = _wl(
        "Störung", "12: Feuerwehreinsatz Betrieb ab Albertgasse", "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        "2026-10-30T10:41:00+01:00", None,
    )
    merged = deduplicate_fuzzy([relocation, fire])
    assert sorted(it["title"] for it in merged) == ["12: Feuerwehreinsatz Betrieb ab Albertgasse", "5/12: Albertgasse"]


def test_running_incident_never_merges_with_a_notice_but_the_works_ticker_does() -> None:
    works = _wl(
        "Hinweis", "12: Gleisbauarbeiten Albertgasse",
        "<h2>Gleisbauarbeiten</h2><p>Wegen Gleisbauarbeiten im Bereich Albertgasse wird die Linie 12 umgeleitet.</p>",
        "2026-10-13T00:00:00+02:00", "2026-11-03T23:59:00+01:00",
    )
    fire = _wl(
        "Störung", "12: Feuerwehreinsatz Betrieb ab Albertgasse", "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        "2026-10-30T10:41:00+01:00", None,
    )
    ticker = _wl(
        "Störung", "12: Gleisbauarbeiten", "Betrieb nur zwischen Albertgasse und Westbahnhof U. Grund: Gleisbauarbeiten.",
        "2026-10-13T04:30:00+02:00", "2026-11-03T01:00:00+01:00",
    )
    # A running incident (no planned measure in title or text) stays apart from the notice, whichever side it is on …
    assert _may_merge(1.0, works, fire) is False
    assert _may_merge(1.0, fire, works) is False
    # … the works' own ticker is a planned measure and still goes up in the works' notice, which survives.
    assert _may_merge(1.0, works, ticker) is True
    assert [(it["category"], it["title"]) for it in deduplicate_fuzzy([works, ticker])] == [
        ("Hinweis", "12: Gleisbauarbeiten Albertgasse")
    ]
    # Two notices, or a notice and a disruption of other lines, are not the rule's business.
    assert _may_merge(1.0, works, dict(works, title="12/N49: Gleisbauarbeiten Albertgasse")) is True
    assert _may_merge(0.2, works, fire) is False


def test_two_relocations_of_one_stop_still_merge() -> None:
    first = _wl(
        "Hinweis", "5A/5B/N31: Obere Augartenstraße",
        "<p><strong><u>Haltestellenverlegung der Linien 5A in Richtung Griegstraße, 5B in Richtung "
        "Heiligenstadt S U</u></strong></p><p>Haltestelle: Obere Augartenstraße</p>",
        "2025-09-30T00:00:00+02:00", "2026-11-01T11:11:00+01:00",
    )
    second = _wl(
        "Hinweis", "5A/N31: Obere Augartenstraße",
        "<p><strong><u>Haltestellenverlegung der Linien 5A und N31 in Richtung Griegstraße</u></strong></p>"
        "<p>Haltestelle: Obere Augartenstraße</p>",
        "2026-11-07T00:00:00+01:00", "2026-11-09T07:00:00+01:00",
    )
    # Both running (or both announced): one item, as before.
    assert len(deduplicate_fuzzy([first, second])) == 1
    # One running, one announced: two items.
    assert len(deduplicate_fuzzy([first, second], announced=_announced_after("2026-10-30T23:59:59+01:00"))) == 2


def test_assemble_feed_items_keeps_the_announcement_apart_until_its_preview_day() -> None:
    live = _wl(
        "Störung", "7A: Bauarbeiten", "Unregelmäßige Intervalle in beiden Richtungen. Grund: Bauarbeiten.",
        "2026-10-09T13:16:00+02:00", "2026-10-09T23:55:00+02:00",
    )
    planned = _wl(
        "Hinweis", "7A: Bauarbeiten",
        "<h2>Bauarbeiten</h2><p>Wegen Bauarbeiten im Bereich Neilreichgasse # Davidgasse wird die Linie 7A umgeleitet. "
        "Zeitraum: Ab Mittwoch, 14. Oktober 2026</p>",
        "2026-10-14T00:00:00+02:00", "2026-11-14T23:59:00+01:00",
    )
    # Friday 09.10. 13:30 Vienna time: the preview reaches Monday 12.10., the notice begins 14.10.
    friday = datetime.fromisoformat("2026-10-09T13:30:00+02:00")
    items = _assemble_feed_items(cast(list[FeedItem], [dict(live), dict(planned)]), {}, friday)
    assert [(it["category"], it["title"]) for it in items] == [("Störung", "7A: Bauarbeiten"), ("Hinweis", "7A: Bauarbeiten")]
    # Tuesday 13.10. 13:30: the notice is in its preview and the two are one works item again.
    tuesday = datetime.fromisoformat("2026-10-13T13:30:00+02:00")
    live_tuesday = dict(
        live,
        starts_at=datetime.fromisoformat("2026-10-13T13:16:00+02:00"),
        pubDate=datetime.fromisoformat("2026-10-13T13:16:00+02:00"),
        ends_at=datetime.fromisoformat("2026-10-13T23:55:00+02:00"),
    )
    assert len(_assemble_feed_items(cast(list[FeedItem], [live_tuesday, dict(planned)]), {}, tuesday)) == 1
