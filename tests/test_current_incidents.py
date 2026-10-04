"""Current incidents come before everything else in the feed order.

Operator decision 2026-10-04: "Aktuelle Störungen sollen oberste Priorität
haben. Vorangekündigte Baustellen sollen eine niedrigere Priorität haben."
Replayed feed of 2026-10-03 15:01 (Vienna): "43A: Veranstaltung" (new event
notice) stood above "9A: Rettungseinsatz"; 2026-10-04 16:30: "U6: Neue Donau,
kein Halt" above four running incidents.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import src.build_feed as bf
from src.feed_types import FeedItem

NOW = datetime(2026, 10, 3, 13, 1, tzinfo=UTC)  # 15:01 Vienna


def _wl(title: str, guid: str, start: datetime, *, category: str = "Störung", text: str = "") -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": category,
        "title": title,
        "description": text or f"{title}.",
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": start,
        "starts_at": start,
        "ends_at": start + timedelta(hours=3),
    }


def _order(items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime = NOW) -> list[str]:
    ranked = sorted(items, key=lambda it: bf._recency_sort_key(it, state, now))
    return [str(it["title"]) for it in ranked]


def test_incident_leads_a_newer_announcement() -> None:
    incident = _wl(
        "9A: Rettungseinsatz", "g9a", NOW - timedelta(minutes=4),
        text="Fahrtbehinderung wegen Rettungseinsatz. Unregelmäßige Intervalle.",
    )
    event = _wl(
        "43A: Veranstaltung", "g43a", NOW - timedelta(minutes=1),
        text="Wegen einer Veranstaltung wird die Linie 43A umgeleitet.",
    )
    state = {"g9a": {"first_seen": (NOW - timedelta(minutes=4)).isoformat()}}

    # "43A" is not in the state yet and counts as seen now.
    assert _order([event, incident], state) == ["9A: Rettungseinsatz", "43A: Veranstaltung"]


def test_planned_measure_is_no_incident() -> None:
    works = _wl(
        "26A: Bauarbeiten", "g26a", NOW - timedelta(minutes=10),
        text="Wegen Bauarbeiten halten die Busse der Linie 26A in der Erzherzog-Karl-Straße 78.",
    )
    entry = {"first_seen": (NOW - timedelta(minutes=10)).isoformat()}

    assert not bf._is_current_incident(works, entry, NOW - timedelta(minutes=10), NOW)


def test_old_incident_text_keeps_the_normal_order() -> None:
    # "18: Haltestelle Stadionbrücke … aufgelassen" reads like an incident
    # and stood in the data since 13.07.: no lead.
    old = _wl(
        "18: Haltestelle Stadionbrücke aufgelassen", "g18", datetime(2026, 7, 13, 12, 7, tzinfo=UTC),
        text="Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus der Linie 18 aufgelassen.",
    )
    fresh = _wl(
        "63A: Umleitung wegen Kranarbeiten", "g63a", NOW - timedelta(hours=2), category="Hinweis",
        text="Wegen Kranarbeiten wird die Linie 63A umgeleitet.",
    )
    state = {
        "g18": {"first_seen": datetime(2026, 7, 13, 12, 30, tzinfo=UTC).isoformat()},
        "g63a": {"first_seen": (NOW - timedelta(hours=2)).isoformat()},
    }

    assert _order([old, fresh], state) == ["63A: Umleitung wegen Kranarbeiten", "18: Haltestelle Stadionbrücke aufgelassen"]


def test_incident_older_than_the_window_falls_back() -> None:
    since = NOW - bf._CURRENT_INCIDENT_WINDOW - timedelta(minutes=1)
    incident = _wl("U1: Rettungseinsatz", "gu1", since, text="Fahrtbehinderung wegen Rettungseinsatz.")
    entry = {"first_seen": since.isoformat()}

    assert not bf._is_current_incident(incident, entry, since, NOW)


def test_oebb_construction_closure_is_no_incident() -> None:
    closure: FeedItem = {
        "source": "ÖBB",
        "category": "Störung",
        "title": "Wien Hauptbahnhof ↔ Gramatneusiedl",
        "description": "03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten können zwischen "
        "<b>Wien Hbf (U)</b> und <b>Gramatneusiedl Bahnhof</b> einzelne Züge nicht fahren.",
        "link": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&894473",
        "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&894473",
        "pubDate": datetime(2026, 9, 28, 12, 5, tzinfo=UTC),
        "starts_at": datetime(2026, 10, 2, 22, 0, tzinfo=UTC),
        "ends_at": datetime(2026, 10, 5, 21, 59, tzinfo=UTC),
    }
    first_seen = NOW - timedelta(hours=1)

    assert not bf._is_current_incident(closure, {"first_seen": first_seen.isoformat()}, first_seen, NOW)


def test_oebb_incident_leads() -> None:
    incident: FeedItem = {
        "source": "ÖBB",
        "category": "Störung",
        "title": "Wien Liesing ↔ Wien Meidling",
        "description": "Wegen eines Polizeieinsatzes sind zwischen Wien Liesing und Wien Meidling "
        "keine Fahrten möglich.",
        "link": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&916001",
        "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&916001",
        "pubDate": NOW - timedelta(minutes=30),
        "starts_at": datetime(2026, 10, 2, 22, 0, tzinfo=UTC),
        "ends_at": datetime(2026, 10, 3, 21, 59, tzinfo=UTC),
    }
    newer = _wl(
        "16A/N65: Grohnergasse", "g16a", NOW - timedelta(minutes=5), category="Hinweis",
        text="Haltestellenverlegung der Linie 16A. Haltestelle: Grohnergasse.",
    )
    state = {
        str(incident["guid"]): {"first_seen": (NOW - timedelta(minutes=30)).isoformat()},
        "g16a": {"first_seen": (NOW - timedelta(minutes=5)).isoformat()},
    }

    assert _order([newer, incident], state) == ["Wien Liesing ↔ Wien Meidling", "16A/N65: Grohnergasse"]
