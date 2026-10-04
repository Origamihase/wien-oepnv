"""Every WL stop relocation and closure reaches the feed, once and dated by its text.

Operator decision 2026-10-04 ("Alle aufnehmen"): a "Haltestellenverlegung"
or "Haltestellenauflassung" notice belongs in the feed whatever its reason.
Before, the news gate let one through only when its reason word contained
"arbeiten": 13 of the 37 notices of the Update run of 2026-10-04 14:00 UTC
reached the cache (``tests/providers/data/wl_stop_notices_2026-10-04.json``,
copied verbatim from ``data/raw/wl/newsList.json``). Four defects stood
behind it:

* the gate (``KW_RESTRICTION``) knew neither "verlegt/Verlegung" nor
  "aufgelassen/Auflassung";
* rules E and F (``_drop_covered_aggregates``/``_drop_covered_subsets``)
  removed a notice whose stop another notice names as a direction: the
  relocation of Schellinggasse "in Richtung Oper, Karlsplatz" took "3A:
  Oper, Karlsplatz" with it (``_covers_stop_notice``);
* a stop notice names its period under "Dauer:", not "Zeitraum:", so
  ``time.start`` (the publication) and the 11:11 end stood: "26E/N20:
  Fultonstraße", published 28.09. for 05.10., read "[Bis 28.09.2027]";
* WL titles that write the line list without a colon or with a two-word
  name repeated it: "16A/N65: 16A, N65 Grohnergasse", "N25: N25, SEV U1:
  Kaisermühlen, V.I.C." (``_strip_written_line_list``; the same class
  showed "1/18/62/LB: 1, 18, 62, Badner Bahn: Signalstörung" on 10.09. and
  "3A: 3A Netzänderung Betrieb ab Riemergasse" on 04.10.).

So that the 24 notices new to the feed do not all lead it at once, a stop
notice seen for the first time sorts from when WL published it
(``build_feed._initial_first_seen``).

Mutations checked against this file (each one caught, by the test named):

* ``verleg``/``auflass``/``aufgelassen`` removed from ``KW_RESTRICTION`` →
  ``test_every_stop_notice_reaches_the_cache``.
* ``_covers_stop_notice`` always true → ``test_a_stop_named_as_a_direction_keeps_its_notice``.
* the "Dauer:" heading not read → ``test_the_period_comes_from_dauer``.
* "Dauer:" read in every notice → ``test_a_disruption_keeps_its_own_dauer``.
* ``_strip_written_line_list`` returning *body* unchanged →
  ``test_a_written_line_list_is_not_repeated`` and
  ``test_no_title_repeats_its_lines``.
* the known-line check of the colonless form removed →
  ``test_a_number_that_is_no_line_of_the_item_stays``.
* ``_initial_first_seen`` returning ``now`` → ``test_a_stop_notice_sorts_from_its_publication``.
* ``_initial_first_seen`` without the stop-notice check →
  ``test_any_other_new_item_is_new``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem
from src.providers import wl_fetch
from src.providers.wl_lines import _ensure_line_prefix
from src.providers.wl_text import (
    extract_duration_from_description,
    extract_end_from_description,
    extract_start_from_description,
)

VIENNA = ZoneInfo("Europe/Vienna")
_FIXTURE = Path(__file__).parent / "providers" / "data" / "wl_stop_notices_2026-10-04.json"


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


_FROZEN_NOW = _FrozenDatetime(2026, 10, 4, 14, 0, tzinfo=UTC)


def _pois() -> list[dict[str, Any]]:
    pois: list[dict[str, Any]] = json.loads(_FIXTURE.read_text(encoding="utf-8"))["pois"]
    return pois


def _poi(title: str) -> dict[str, Any]:
    return next(poi for poi in _pois() if poi["title"] == title)


def _fetch(monkeypatch: pytest.MonkeyPatch, news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    monkeypatch.setattr(wl_fetch, "datetime", _FrozenDatetime, raising=True)
    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda **_kwargs: [], raising=True)
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda **_kwargs: list(news), raising=True)
    return wl_fetch.fetch_events()


def test_every_stop_notice_reaches_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    pois = _pois()
    assert len(pois) == 37
    events = _fetch(monkeypatch, pois)
    assert len(events) == 37
    titles = {event["title"] for event in events}
    # Reasons without "arbeiten", dropped before.
    assert {
        "16A/N65: Grohnergasse",  # Rohrgebrechen
        "1A: Habsburgergasse",  # Haussanierung
        "74A: Rabengasse",  # Spontangebrechen
        "N25/SEVU1: Kaisermühlen, V.I.C.",  # Straßenbau
        "95B: Amongasse",  # an "Auflassung"
    } <= titles


def test_a_stop_named_as_a_direction_keeps_its_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _fetch(
        monkeypatch,
        [
            _poi("3A: Oper, Karlsplatz"),
            _poi("3A: Schellinggasse"),
            _poi("12A, N8: Längenfeldgasse U"),
            _poi("12A: Geibelgasse Richtung Eichenstraße, neuer Standort"),
        ],
    )
    assert sorted(event["title"] for event in events) == [
        "12A/N8: Längenfeldgasse U",
        "12A: Geibelgasse Richtung Eichenstraße, neuer Standort",
        "2A/3A: Schellinggasse",
        "3A: Oper, Karlsplatz",
    ]


def test_the_period_comes_from_dauer(monkeypatch: pytest.MonkeyPatch) -> None:
    description = _poi("26E, N20: Fultonstraße")["description"]
    reference = datetime(2026, 9, 28, 15, 45, tzinfo=VIENNA)
    assert extract_start_from_description(description, reference) == datetime(2026, 10, 5, tzinfo=VIENNA)
    assert extract_duration_from_description(description) == timedelta(days=14)
    (event,) = _fetch(monkeypatch, [_poi("26E, N20: Fultonstraße")])
    assert event["title"] == "26E/N20: Fultonstraße"
    assert event["starts_at"] == datetime(2026, 10, 5, tzinfo=VIENNA)
    # Two weeks and the buffer of the duration rule (#1919), not the 11:11 end of 28.09.2027.
    assert event["ends_at"] == datetime(2026, 10, 26, 23, 59, tzinfo=VIENNA)
    # A named end replaces the 11:11 end too.
    (panethgasse,) = _fetch(monkeypatch, [_poi("27A: Panethgasse, Sebaldgasse")])
    assert panethgasse["ends_at"] == datetime(2026, 10, 31, 23, 59, tzinfo=VIENNA)


def test_a_disruption_keeps_its_own_dauer() -> None:
    # Verbatim from data/raw/wl/trafficInfoList.json (2026-10-04, "46, 49, 52:
    # Gleisbauarbeiten"): "Bis 30.10." would pass for a begin on 30.10.
    description = (
        "Linie 46: Wird ab Joachimsthalerplatz über die Strecke der Linien 10 und 49 nach "
        "Hütteldorf, Bujattigasse verlängert. Linie 49: Kein Betrieb. Linie 52: Wird ab "
        "Westbahnhof über die Strecke der Linien 18 und 49 nach Parlament, U Volkstheater "
        "verlängert. Weitere Alternativen: U3, 9, 12A. Dauer: Bis 30.10.2026 Betriebsschluss. "
        "Grund: Gleisbauarbeiten im Bereich Märzstraße # Huglgasse."
    )
    reference = datetime(2026, 9, 18, 4, 30, tzinfo=VIENNA)
    assert extract_start_from_description(description, reference) is None
    assert extract_end_from_description(description, reference) is None


@pytest.mark.parametrize(
    ("written", "lines", "title"),
    [
        ("16A, N65 Grohnergasse", ["16A", "N65"], "16A/N65: Grohnergasse"),
        ("N25, SEV U1: Kaisermühlen, V.I.C.", ["N25"], "N25/SEVU1: Kaisermühlen, V.I.C."),
        ("1, 18, 62, Badner Bahn: Signalstörung", ["1", "18", "62", "LB"], "1/18/62/LB: Signalstörung"),
        (
            "62, Badner Bahn, 59A, 62A: Beschädigte Oberleitung",
            ["59A", "62", "62A", "LB"],
            "59A/62/62A/LB: Beschädigte Oberleitung",
        ),
        ("3A Netzänderung Betrieb ab Riemergasse", ["3A"], "3A: Netzänderung Betrieb ab Riemergasse"),
        ("U1 Klapprampensperre am 13.09.2026", ["U1"], "U1: Klapprampensperre am 13.09.2026"),
        ("86A, 87A Rufbus 86A: Veranstaltung", ["86A", "86AR", "87A"], "86A/86AR/87A: Veranstaltung"),
        ("U2 und U3 fahren wieder", ["U2", "U3"], "U2/U3: fahren wieder"),
    ],
)
def test_a_written_line_list_is_not_repeated(written: str, lines: list[str], title: str) -> None:
    assert _ensure_line_prefix(written, lines) == title


@pytest.mark.parametrize(
    ("written", "lines", "title"),
    [
        ("25 Jahre Linie 25", ["26"], "26: 25 Jahre Linie 25"),
        ("16A, N65 Grohnergasse", ["16A"], "16A: 16A, N65 Grohnergasse"),
        ("U2 und U3 fahren", ["U2"], "U2: U2 und U3 fahren"),
        ("U2 oder U3", ["U2"], "U2: U2 oder U3"),
        ("5 Minuten Verspätung", ["5"], "5: 5 Minuten Verspätung"),
        ("D, 1, 2, 71, 1A, 3A", ["D", "1", "2", "71", "1A", "3A"], "D/1/2/71/1A/3A: D, 1, 2, 71, 1A, 3A"),
        ("Achtung: Sperre", ["U1"], "U1: Achtung: Sperre"),
        ("17:30 Verspätung", ["17"], "17: 17:30 Verspätung"),
        ("Ersatzbus 26E hält Karl-Waldbrunner-Platz", ["25"], "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz"),
    ],
)
def test_a_number_that_is_no_line_of_the_item_stays(written: str, lines: list[str], title: str) -> None:
    assert _ensure_line_prefix(written, lines) == title


def test_no_title_repeats_its_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    for event in _fetch(monkeypatch, _pois()):
        prefix, _, body = event["title"].partition(": ")
        first = re.split(r"[\s,/:]", body, maxsplit=1)[0]
        assert first not in prefix.split("/"), event["title"]


def _item(description: str, *, published: datetime | None, source: str = "Wiener Linien") -> FeedItem:
    return cast(FeedItem, {
        "source": source,
        "category": "Hinweis",
        "title": "16A/N65: Grohnergasse",
        "description": description,
        "guid": "g",
        "pubDate": published,
    })


_NOW = datetime(2026, 10, 4, 14, 0, tzinfo=UTC)
_STOP_TEXT = "Haltestellenverlegung der Linie 16A in Richtung Hetzendorf S bzw. N65 in Richtung Liesing"


def test_a_stop_notice_sorts_from_its_publication() -> None:
    published = datetime(2026, 9, 28, 15, 45, tzinfo=VIENNA)
    assert bf._initial_first_seen(_item(_STOP_TEXT, published=published), _NOW) == published.astimezone(UTC)
    # Its place among items already in the state: after one first seen on 01.10.
    state = {"a": {"first_seen": "2026-10-01T08:00:00+00:00"}}
    older = cast(FeedItem, {**_item("Gleisbauarbeiten", published=None), "guid": "a"})
    ranked = sorted([_item(_STOP_TEXT, published=published), older], key=lambda it: bf._recency_sort_key(it, state, _NOW))
    assert [it["guid"] for it in ranked] == ["a", "g"]


def test_any_other_new_item_is_new() -> None:
    published = datetime(2026, 9, 28, 15, 45, tzinfo=VIENNA)
    assert bf._initial_first_seen(_item("Gleisbauarbeiten in der Donaufelder Straße", published=published), _NOW) == _NOW
    assert bf._initial_first_seen(_item(_STOP_TEXT, published=published, source="ÖBB"), _NOW) == _NOW
    assert bf._initial_first_seen(_item(_STOP_TEXT, published=None), _NOW) == _NOW
    assert bf._initial_first_seen(_item(_STOP_TEXT, published=_NOW + timedelta(hours=1)), _NOW) == _NOW
