"""An announced measure counts as new on the day it begins.

Live case 2026-10-04: the ÖBB closure "Wien Hauptbahnhof ↔ Gramatneusiedl"
(03.10.–05.10.2026) had been in the data since 08.07. and stood on place 49
of the sorted feed on its second day; the S-Bahn-Stammstrecke closure
(Phase 2, seen since 10.06.) held none of the ten slots on 07.09., its first
day.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import src.build_feed as bf
from src.feed_types import FeedItem

NOW = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)  # Fri 22:00 Vienna
START = datetime(2026, 10, 2, 22, 0, tzinfo=UTC)  # Sat 03.10. 00:00 Vienna


def _oebb(guid: str, starts_at: datetime) -> FeedItem:
    return {
        "source": "ÖBB",
        "category": "Störung",
        "title": "Wien Hauptbahnhof ↔ Gramatneusiedl",
        "description": "03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten können zwischen "
        "<b>Wien Hbf (U)</b> und <b>Gramatneusiedl Bahnhof</b> einzelne Züge nicht fahren.",
        "link": f"https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&{guid}",
        "guid": f"https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&{guid}",
        "pubDate": datetime(2026, 9, 28, 12, 5, tzinfo=UTC),
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(days=3) - timedelta(minutes=1),
    }


def _wl(title: str, guid: str, start: datetime, *, category: str = "Störung") -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": category,
        "title": title,
        "description": f"{title}.",
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": start,
        "starts_at": start,
        "ends_at": start + timedelta(hours=20),
    }


def _entry(first_seen: datetime) -> dict[str, Any]:
    return {"first_seen": first_seen.isoformat()}


def test_future_start_is_noted() -> None:
    item = _oebb("894473", START)
    state = {str(item["guid"]): _entry(datetime(2026, 7, 8, 12, 1, tzinfo=UTC))}

    assert bf._note_announced_starts([item], state, NOW) == 1
    assert state[str(item["guid"])]["announced_start"] == START.isoformat()
    # Noted once; the same start does not count again.
    assert bf._note_announced_starts([item], state, NOW) == 0


def test_started_item_is_not_noted() -> None:
    # A measure seen from its start on (WL re-issues "66A: Busse halten
    # Salvatorianerplatz" every morning at 04:40) gets no announced start.
    item = _wl("66A: Busse halten Salvatorianerplatz", "g66a", NOW - timedelta(hours=1))
    state = {"g66a": _entry(NOW - timedelta(days=1))}

    assert bf._note_announced_starts([item], state, NOW) == 0
    assert "announced_start" not in state["g66a"]


def test_item_without_entry_is_not_noted() -> None:
    assert bf._note_announced_starts([_oebb("894473", START)], {}, NOW) == 0


def test_postponed_start_overwrites() -> None:
    item = _oebb("894473", START)
    state = {str(item["guid"]): _entry(datetime(2026, 7, 8, 12, 1, tzinfo=UTC))}
    bf._note_announced_starts([item], state, NOW)

    later = START + timedelta(days=7)
    bf._note_announced_starts([_oebb("894473", later)], state, NOW)

    assert state[str(item["guid"])]["announced_start"] == later.isoformat()


def test_announced_item_leads_from_its_start() -> None:
    closure = _oebb("894473", START)
    relocation = _wl("16A/N65: Grohnergasse", "g16a", NOW - timedelta(days=2), category="Hinweis")
    state = {
        str(closure["guid"]): _entry(datetime(2026, 7, 8, 12, 1, tzinfo=UTC)),
        "g16a": _entry(NOW - timedelta(days=2)),
    }
    bf._note_announced_starts([closure, relocation], state, NOW)

    def order(now: datetime) -> list[str]:
        items = [relocation, closure]
        items.sort(key=lambda it: bf._recency_sort_key(it, state, now))
        return [str(it["title"]) for it in items]

    # Before its start the announcement keeps its announcement date.
    assert order(NOW) == ["16A/N65: Grohnergasse", "Wien Hauptbahnhof ↔ Gramatneusiedl"]
    # From its start it counts as new …
    assert order(START + timedelta(minutes=1)) == [
        "Wien Hauptbahnhof ↔ Gramatneusiedl",
        "16A/N65: Grohnergasse",
    ]
    # … and anything newer than its start still comes first.
    incident = _wl("U4: Rettungseinsatz", "gu4", START + timedelta(hours=2))
    later = START + timedelta(hours=3)
    items = [closure, incident]
    items.sort(key=lambda it: bf._recency_sort_key(it, state, later))
    assert [it["title"] for it in items] == ["U4: Rettungseinsatz", "Wien Hauptbahnhof ↔ Gramatneusiedl"]


def test_new_occurrence_after_the_start_ignores_it() -> None:
    # A WL restart (_restart_recurring_occurrences) moved first_seen past the
    # old announced start: the newer first_seen counts.
    entry = _entry(START + timedelta(days=5))
    entry["announced_start"] = START.isoformat()
    now = START + timedelta(days=6)

    assert bf._sort_moment(entry, START + timedelta(days=5), now) == START + timedelta(days=5)


def test_first_emission_notes_a_future_start() -> None:
    item = _oebb("915701", START + timedelta(days=28))
    state: dict[str, dict[str, Any]] = {}

    bf._update_item_state(item, NOW, state)

    entry = state[str(item["guid"])]
    assert entry["announced_start"] == (START + timedelta(days=28)).isoformat()
