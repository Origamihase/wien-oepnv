"""Which disruptions go to Gemini, and what its answer changes (``src/ki_entscheidung``).

Owner's decision of 2026-11-12 (Michael, card "Soll ich die
Gemini-Entscheidung für unklare Fälle so bauen?": "Ja, so bauen"). The
rules tell a sudden disruption from a planned measure by its words; where
they cannot (a works word beside an incident, no cause at all, ÖBB works
that overrun) Gemini's stored answer decides. The shapes below are the
messages of the replay of 20.09. to 12.11.2026 that showed the three
classes. No test here asks anyone: the answers are set on the items or
written to the store.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem
from src.ki_entscheidung import apply_stored
from src.ki_entscheidung import candidates as cand
from src.ki_entscheidung import store

VIENNA = ZoneInfo("Europe/Vienna")
NBSP = "\u00a0"
NOW = datetime(2026, 9, 21, 9, 1, tzinfo=VIENNA)
# The moment of a stored answer: at NOW, after the begin of every message below.
DECIDED = "2026-09-21T07:01:00+00:00"
GAS_TEXT = (
    "Betrieb nur zwischen Parlament, U Volkstheater und Joachimsthalerplatz. Zwischen Joachimsthalerplatz und "
    "Klinik Penzing wurde ein Pendelbusverkehr eingerichtet. Aufgrund der Bauarbeiten werden die Busse über die "
    "Breitenseer Straße umgeleitet."
)
OVERRUN = (
    "01.11.2026<br/><br/>Wegen Verlängerung der Bauarbeiten sind zwischen <b>Wien Franz-Josefs-Bahnhof</b> und "
    "<b>Wien Heiligenstadt Bahnhof (U)</b> bis voraussichtlich 09:00 Uhr keine Fahrten möglich."
)


def _wl(title: str, description: str, start: datetime, **extra: Any) -> FeedItem:
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": f"guid-{title}",
        "pubDate": start,
        "starts_at": start,
        "ends_at": None,
    }
    item.update(extra)
    return cast(FeedItem, item)


def _oebb(title: str, description: str, start: datetime, published: datetime, end: datetime | None) -> FeedItem:
    return cast(FeedItem, {
        "source": "ÖBB",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://fahrplan.oebb.at/",
        "guid": f"https://fahrplan.oebb.at/?{title}",
        "pubDate": published,
        "starts_at": start,
        "ends_at": end,
    })


def _tokio(start: datetime = datetime(2026, 9, 21, 8, 48, 2, tzinfo=VIENNA)) -> FeedItem:
    return _wl("26: Züge halten in Tokiostraße", "Züge halten in\nTokiostraße", start)


def _gas() -> FeedItem:
    start = datetime(2026, 9, 21, 8, 47, tzinfo=VIENNA)
    return _wl("48A: Gasrohrgebrechen", GAS_TEXT, start, _wl_ids=["I20260921-0031"])


def _overrun() -> FeedItem:
    day = datetime(2026, 9, 21, tzinfo=VIENNA)
    return _oebb("Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt", OVERRUN, day,
                 day.replace(hour=5, minute=12), day.replace(hour=23, minute=59, second=59))


def _class(item: FeedItem, now: datetime = NOW) -> str | None:
    return cand.unclear_class(item, None, now.astimezone(ZoneInfo("UTC")))


def _line(item: FeedItem, now: datetime = NOW) -> str:
    start, end = item.get("starts_at"), item.get("ends_at")
    since = bf._incident_since(item, start, bf._first_published(item, None))
    return bf.format_local_times(start, end, now, since=since).replace(NBSP, " ")


# ---- the three classes ------------------------------------------------------------


def test_a_wl_disruption_without_a_cause_is_unclear() -> None:
    assert _class(_tokio()) == cand.CLASS_NO_CAUSE
    midnight = _wl("71E: Ersatzverkehr Busse halten Simmeringer Hauptstraße ggü. 197",
                   "Ersatzverkehr\nBusse halten Simmeringer Hauptstraße ggü. 197",
                   datetime(2026, 9, 21, 0, 0, 19, tzinfo=VIENNA))
    assert _class(midnight) == cand.CLASS_NO_CAUSE


def test_an_incident_beside_a_works_word_is_unclear() -> None:
    assert _class(_gas()) == cand.CLASS_WORKS_WORD


def test_oebb_works_published_on_their_only_day_are_unclear() -> None:
    assert _class(_overrun()) == cand.CLASS_OEBB_DAY


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("25: Fahrtbehinderung wegen Rettungseinsatz", "Linie 25: Fahrtbehinderung. Grund: Rettungseinsatz."),
        ("15A: Verspätungen", "Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsüberlastung."),
        ("1: Kein Betrieb", "Gleisbauarbeiten\nKein Betrieb"),
        ("D: Fahrtbehinderung Gleisbauarbeiten", "Gleisbauarbeiten\nFahrtbehinderung"),
        ("62A: Straßenbau Haltestelle verlegt zur Wienerbergbrücke", "Straßenbau\nHaltestelle verlegt"),
    ],
)
def test_a_clear_cause_is_decided_by_the_rules(title: str, text: str) -> None:
    assert _class(_wl(title, text, datetime(2026, 9, 21, 8, 48, tzinfo=VIENNA))) is None


@pytest.mark.parametrize(
    "words",
    [
        "einer Demonstration", "einer Kundgebung", "einer Veranstaltung", "einer Haltestellenverlegung", "eines Staatsbesuchs",
        "einer Übung", "der Auflassung der Haltestelle", "Bauarbeiten und einer Veranstaltung",
    ],
)
def test_what_the_owner_decided_is_a_notice_stays_with_the_rules(words: str) -> None:
    # 2026-11-03: a moved stop, a demonstration, an event are notices, even
    # in a text whose cause names an incident and beside a works word.
    start = datetime(2026, 9, 21, 8, 48, tzinfo=VIENNA)
    assert _class(_wl("2: Polizeieinsatz", f"Fahrtbehinderung. Wegen {words} am Ring.", start)) is None
    overrun = _overrun()
    overrun["description"] = f"{OVERRUN} Wegen {words}."
    assert _class(overrun) is None


def test_only_a_disruption_that_has_begun_is_unclear() -> None:
    later = _tokio(datetime(2026, 9, 21, 12, 30, tzinfo=VIENNA))
    assert _class(later) is None
    notice = _tokio()
    notice["category"] = "Hinweis"
    assert _class(notice) is None
    measured = _tokio()
    measured["source"] = "VOR/VAO"
    assert _class(measured) is None


def test_oebb_works_announced_ahead_or_over_days_are_planned() -> None:
    day = datetime(2026, 9, 21, tzinfo=VIENNA)
    text = "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof keine Züge fahren."
    ahead = _oebb("Wien Hauptbahnhof ↔ Gramatneusiedl", text, day, day - timedelta(days=5),
                  day.replace(hour=23, minute=59))
    assert _class(ahead) is None
    weeks = _oebb("Wien Hauptbahnhof ↔ Gramatneusiedl", text, day, day.replace(hour=6), day + timedelta(days=30))
    assert _class(weeks) is None
    # The state knows an earlier publication than today's update.
    entry = {"first_published": (day - timedelta(days=2)).isoformat(), "first_seen": (day - timedelta(days=2)).isoformat()}
    assert cand.unclear_class(_overrun(), entry, NOW.astimezone(ZoneInfo("UTC"))) is None
    assert "first_published" in entry and len(entry) == 2


def test_an_oebb_report_of_a_disruption_that_is_over_is_no_candidate() -> None:
    day = datetime(2026, 9, 21, tzinfo=VIENNA)
    over = _oebb(
        "Wien Hauptbahnhof ↔ Gramatneusiedl",
        "Wegen Verlängerung der Bauarbeiten waren zwischen Gramatneusiedl Bahnhof und Wien Hbf (U) bis 06:10 Uhr "
        "keine Fahrten möglich.",
        day, day.replace(hour=6, minute=12), day.replace(hour=23, minute=59),
    )
    assert _class(over) is None


# ---- the print and the description --------------------------------------------------


def test_wls_changing_forecast_is_no_new_message() -> None:
    first = _wl("U6: Stellwerksstörung", "Kein Betrieb. Voraussichtliche Dauer: 19:30 Uhr. Grund: Stellwerksstörung.", NOW)
    later = _wl("U6: Stellwerksstörung", "Kein Betrieb. Voraussichtliche Dauer: Bis Betriebsschluss. Grund: Stellwerksstörung.", NOW)
    other = _wl("U6: Stellwerksstörung", "Betrieb nur bis Alterlaa. Grund: Stellwerksstörung.", NOW)
    assert cand.fingerprint(first) == cand.fingerprint(later) != cand.fingerprint(other)


def test_the_model_reads_the_other_messages_of_the_line() -> None:
    works = _wl("26: Gleisbauarbeiten", "<h2>Gleisbauarbeiten</h2><p>Wegen Gleisbauarbeiten in der Tokiostraße …</p>",
                datetime(2026, 9, 14, 4, 0, tzinfo=VIENNA), category="Hinweis", guid="notice")
    other_line = _wl("9: Rettungseinsatz", "Fahrtbehinderung.", NOW, guid="nine")
    item = _tokio()
    found = cand.candidates_of([works, item, other_line], {}, NOW.astimezone(ZoneInfo("UTC")))
    assert [candidate.key for candidate in found] == ["guid-26: Züge halten in Tokiostraße"]
    text = cand.describe(1, found[0], [works, item, other_line])
    assert text.startswith("Meldung 1\nQuelle: Wiener Linien\nTitel: 26: Züge halten in Tokiostraße\n")
    assert "Beginn laut Quelle: Mo 21.09.2026 08:48" in text and "Ende laut Quelle: nicht genannt" in text
    assert "Andere Meldung derselben Linie (Hinweis, Beginn Mo 14.09.2026 04:00" in text
    assert "26: Gleisbauarbeiten — Gleisbauarbeiten Wegen Gleisbauarbeiten in der Tokiostraße" in text
    assert "Rettungseinsatz" not in text
    alone = cand.describe(2, found[0], [item])
    assert alone.endswith("Andere Meldungen derselben Linie: keine")


def test_oebb_neighbours_share_a_place_of_the_route() -> None:
    day = datetime(2026, 9, 21, tzinfo=VIENNA)
    planned = _oebb("S 40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau", "Wegen Bauarbeiten …", day - timedelta(days=3),
                    day - timedelta(days=20), day + timedelta(days=3))
    elsewhere = _oebb("Wien Meidling ↔ Wien Liesing", "Wegen eines Schadens am Gleis …", day, day, day)
    overrun = _overrun()
    found = cand.candidates_of([planned, overrun, elsewhere], {}, NOW.astimezone(ZoneInfo("UTC")))
    text = cand.describe(1, found[0], [planned, overrun, elsewhere])
    assert "S 40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau" in text and "Liesing" not in text


# ---- what the answer changes ----------------------------------------------------------


def test_the_rules_alone_read_the_clock_and_the_works_word() -> None:
    assert _line(_tokio()) == "Seit 08:48"
    assert _line(_gas()) == "Seit heute"
    assert _line(_overrun()) == "Heute"


def test_planned_takes_the_begin_from_a_measure_without_a_cause() -> None:
    item = _tokio()
    item["_ki_art"] = "geplant"
    assert _line(item) == "Seit heute"


def test_sudden_gives_the_begin_back_to_an_incident_beside_a_works_word() -> None:
    item = _gas()
    item["_ki_art"] = "ploetzlich"
    assert _line(item) == "Seit 08:47"
    overrun = _overrun()
    overrun["_ki_art"] = "ploetzlich"
    assert _line(overrun) == "Seit 05:12"


def test_an_incident_that_ends_after_midnight_keeps_its_line_and_moves_up() -> None:
    """The real "48A: Gasrohrgebrechen" of 30.09.2026 ended at 01:00: its line names the end under every answer."""
    item = _gas()
    item["ends_at"] = datetime(2026, 9, 22, 1, 0, tzinfo=VIENNA)
    now_utc = NOW.astimezone(ZoneInfo("UTC"))
    assert _line(item) == "Bis Di 22.09." and bf._recency_sort_key(item, {}, now_utc)[0] == 2
    item["_ki_art"] = "ploetzlich"
    assert _line(item) == "Bis Di 22.09." and bf._recency_sort_key(item, {}, now_utc)[0] == 0


def test_the_model_reads_when_oebb_first_published_the_message() -> None:
    """ÖBB's ``pubDate`` moves with every update; the state entry keeps the first one."""
    item = _overrun()
    item["pubDate"] = datetime(2026, 9, 21, 8, 30, tzinfo=VIENNA)
    now_utc = datetime(2026, 9, 21, 8, 31, tzinfo=VIENNA).astimezone(ZoneInfo("UTC"))
    key = cand.candidates_of([item], {}, now_utc)[0].key
    state = {key: {"first_seen": "2026-09-21T03:20:00+00:00", "first_published": "2026-09-21T03:12:00+00:00"}}
    (candidate,) = cand.candidates_of([item], state, now_utc)
    assert "Erstmals veröffentlicht: Mo 21.09.2026 05:12" in cand.describe(1, candidate, [item])
    assert state[key] == {"first_seen": "2026-09-21T03:20:00+00:00", "first_published": "2026-09-21T03:12:00+00:00"}
    (tokio,) = cand.candidates_of([_tokio()], {}, NOW.astimezone(ZoneInfo("UTC")))
    assert "Erstmals veröffentlicht: Mo 21.09.2026 08:48" in cand.describe(1, tokio, [])


def test_sudden_also_holds_on_a_full_hour() -> None:
    item = _wl("9: Züge halten bei Linie 52", "Züge halten bei\nLinie 52", datetime(2026, 9, 21, 8, 0, 4, tzinfo=VIENNA))
    assert _line(item) == "Seit heute"
    item["_ki_art"] = "ploetzlich"
    assert _line(item) == "Seit 08:00"


def test_an_answer_never_revives_a_disruption_that_is_over() -> None:
    day = datetime(2026, 9, 21, tzinfo=VIENNA)
    over = _oebb("Wien Meidling ↔ Wien Liesing",
                 "Wegen Bauarbeiten waren zwischen Wien Meidling und Wien Liesing bis 08:10 Uhr keine Fahrten möglich.",
                 day, day.replace(hour=8, minute=12), day.replace(hour=23, minute=59))
    over["_ki_art"] = "ploetzlich"
    assert _line(over) == "Heute"
    notice = _tokio()
    notice["category"] = "Hinweis"
    notice["_ki_art"] = "ploetzlich"
    assert bf._incident_since(notice, notice.get("starts_at")) is None


# ---- the chain reads the stored answers -------------------------------------------------


def _write_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: dict[str, dict[str, Any]]) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data" / "ki_entscheidungen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "entscheidungen": records}), encoding="utf-8")
    return path


def _incident() -> FeedItem:
    return _wl("13A: Rettungseinsatz", "Fahrtbehinderung. Grund: Rettungseinsatz.",
               datetime(2026, 9, 21, 8, 30, tzinfo=VIENNA), guid="rescue", _wl_ids=["I20260921-0030"])


def _order(items: list[FeedItem], state: dict[str, Any]) -> list[tuple[str, int]]:
    now_utc = NOW.astimezone(ZoneInfo("UTC"))
    return [(str(item["title"]), bf._recency_sort_key(item, state, now_utc)[0]) for item in items]


def test_without_stored_answers_the_chain_is_the_rules() -> None:
    state: dict[str, Any] = {}
    items = bf._assemble_feed_items([_tokio(), _gas(), _incident()], state, NOW)
    assert all("_ki_art" not in item for item in items)
    assert dict(_order(items, state)) == {"26: Züge halten in Tokiostraße": 0, "48A: Gasrohrgebrechen": 2, "13A: Rettungseinsatz": 0}


def test_the_chain_orders_by_the_stored_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_store(tmp_path, monkeypatch, {
        "guid-26: Züge halten in Tokiostraße": {"art": "geplant", "abdruck": "x", "gefragt": [], "entschieden": DECIDED},
        "guid-48A: Gasrohrgebrechen": {"art": "ploetzlich", "abdruck": "x", "gefragt": [], "entschieden": DECIDED},
        "rescue": {"art": "geplant", "abdruck": "x", "gefragt": [], "entschieden": DECIDED},
    })
    before = path.read_text(encoding="utf-8")
    state: dict[str, Any] = {}
    items = bf._assemble_feed_items([_tokio(), _gas(), _incident()], state, NOW)
    order = _order(items, state)
    # The gas pipe is a current incident now, the measure is not; a clear
    # incident takes no answer, whatever the file says about its key.
    assert dict(order) == {"26: Züge halten in Tokiostraße": 2, "48A: Gasrohrgebrechen": 0, "13A: Rettungseinsatz": 0}
    assert [title for title, _tier in order][-1] == "26: Züge halten in Tokiostraße"
    assert {str(item["title"]): item.get("_ki_art") for item in items} == {
        "26: Züge halten in Tokiostraße": "geplant", "48A: Gasrohrgebrechen": "ploetzlich", "13A: Rettungseinsatz": None,
    }
    assert path.read_text(encoding="utf-8") == before


def test_unclear_and_unknown_answers_leave_the_item_to_the_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_store(tmp_path, monkeypatch, {
        "guid-26: Züge halten in Tokiostraße": {"art": "unklar", "abdruck": "x", "gefragt": [], "entschieden": DECIDED},
        "guid-48A: Gasrohrgebrechen": {"art": "sofort löschen", "abdruck": "x", "gefragt": [], "entschieden": DECIDED},
    })
    items = [_tokio(), _gas()]
    apply_stored(items, {}, NOW)
    assert all("_ki_art" not in item for item in items)
    assert store.load()["guid-48A: Gasrohrgebrechen"] == {"abdruck": "x", "gefragt": [], "entschieden": DECIDED}


def test_an_answer_holds_for_the_event_it_was_given_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A WL GUID carries no date: the same key and text return with the next event of the ticker."""
    _write_store(tmp_path, monkeypatch, {
        "guid-26: Züge halten in Tokiostraße": {"art": "ploetzlich", "abdruck": "x", "gefragt": [], "entschieden": DECIDED},
    })
    same = _tokio()
    apply_stored([same], {}, NOW)
    assert same.get("_ki_art") == "ploetzlich"
    # Two days later WL enters the measure again at 05:00: the answer of the 21st says nothing about it.
    again = _tokio(start=datetime(2026, 9, 23, 5, 0, 14, tzinfo=VIENNA))
    apply_stored([again], {}, datetime(2026, 9, 23, 5, 6, tzinfo=VIENNA))
    assert "_ki_art" not in again and bf._incident_since(again, again.get("starts_at")) is None
    # A record without the moment of its answer (a hand-edited file) decides nothing.
    _write_store(tmp_path, monkeypatch, {"guid-26: Züge halten in Tokiostraße": {"art": "geplant", "gefragt": []}})
    undated = _tokio()
    apply_stored([undated], {}, NOW)
    assert "_ki_art" not in undated


def test_an_oebb_answer_keeps_applying_when_the_end_moves_to_the_next_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    morning = datetime(2026, 9, 21, 6, 1, tzinfo=VIENNA)
    moved = _overrun()
    moved["ends_at"] = datetime(2026, 9, 22, 4, 0, tzinfo=VIENNA)
    assert _class(moved, morning) is None
    key = cand.candidates_of([_overrun()], {}, morning.astimezone(ZoneInfo("UTC")))[0].key
    _write_store(tmp_path, monkeypatch, {key: {
        "art": "ploetzlich", "klasse": cand.CLASS_OEBB_DAY, "abdruck": "x", "gefragt": [],
        "entschieden": morning.astimezone(ZoneInfo("UTC")).isoformat(),
    }})
    apply_stored([moved], {}, morning + timedelta(hours=3))
    assert moved.get("_ki_art") == "ploetzlich"
    # The class alone is no answer: a text that names an event stays with the rules.
    event = _overrun()
    event["description"] = str(event["description"]) + " Grund: Veranstaltung."
    apply_stored([event], {}, morning)
    assert "_ki_art" not in event


def test_one_unreadable_message_does_not_take_the_others_answers() -> None:
    odd = _tokio()
    odd["title"] = "26: Züge halten in Tokiostraße \ud800"
    assert len(cand.fingerprint(odd)) == 16
    broken = _tokio()
    broken["starts_at"] = cast(Any, object())
    found = cand.candidates_of([broken, _tokio(), _gas()], {}, NOW.astimezone(ZoneInfo("UTC")))
    assert [candidate.kind for candidate in found] == [cand.CLASS_NO_CAUSE, cand.CLASS_WORKS_WORD]


def test_a_broken_store_never_stops_the_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_store(tmp_path, monkeypatch, {})
    path.write_text("{ kein JSON", encoding="utf-8")
    items = bf._assemble_feed_items([_tokio()], {}, NOW)
    assert [item.get("_ki_art") for item in items] == [None]
    path.write_text(json.dumps({"entscheidungen": ["a", 1], "version": "x"}), encoding="utf-8")
    assert store.load() == {}
    path.write_text(json.dumps({"entscheidungen": {"k": "geplant", "j": {"art": 7, "gefragt": "nie"}}}), encoding="utf-8")
    assert store.load() == {"j": {"gefragt": []}}


def test_the_messages_of_the_same_stretch_come_before_those_that_share_one_end() -> None:
    # 09.11.2026: "Wien Hauptbahnhof ↔ Gramatneusiedl" beside "R 95: Wien Hauptbahnhof ↔ Felixdorf".
    day = datetime(2026, 11, 9, tzinfo=VIENNA)
    item = _oebb("Wien Hauptbahnhof ↔ Gramatneusiedl", OVERRUN, day, day.replace(hour=4, minute=30), day.replace(hour=23, minute=59))
    others = [
        _oebb(f"R 9{number}: Wien Hauptbahnhof ↔ Ort {number}", "Wegen Bauarbeiten …", day, day, None)
        for number in range(cand.MAX_NEIGHBOURS)
    ]
    same = _oebb("Wien Hauptbahnhof ↔ Gramatneusiedl", "Wegen Bauarbeiten … (jeweils von 23:15 Uhr bis 04:40 Uhr)", day, day, None)
    same["guid"] = "planned"
    wl = _wl("13A: Wien Hauptbahnhof", "Busse halten …", day)
    chosen = cand.neighbours_of(item, [*others, wl, item, same])
    assert chosen[0] is same and len(chosen) == cand.MAX_NEIGHBOURS and wl not in chosen
