"""A Stadt Wien Baustelle that WL already reports, and an extended one (audit 2026-10-09).

"Burggasse 67" stood on place 9 of the German feed: the same stop relocation
WL publishes as "48A: Neubaugasse, Burggasse" (with line and direction), and
it counted as brand-new because the city had moved its end from 02.10. to
30.10. Operator decision 2026-10-09 ("WL-Meldung genügt"): the WL notice is
enough. Real texts from the caches of 2026-10-09 and 2026-08-07.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem
from src.providers.baustellen import names_site_street, shares_address, site_street

_VIENNA = ZoneInfo("Europe/Vienna")
_NOW = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)

_BURGGASSE: FeedItem = {
    "source": "Stadt Wien – Baustellen",
    "category": "Baustelle",
    "title": "Burggasse 67",
    "description": (
        "DieHaltestelle des betroffenen öffentlichen Verkehrsmittels wird von Burggasse "
        "ONr.67 nach Burggasse ONr. 69 verlegt. Derrechte Fahrstreifen wird in "
        "Fahrtrichtung stadteinwärts gesperrt."
    ),
    "guid": "bau-burggasse",
    "starts_at": datetime(2026, 7, 28, tzinfo=_VIENNA),
    "ends_at": datetime(2026, 10, 30, tzinfo=_VIENNA),
}
_WL_48A: FeedItem = {
    "source": "Wiener Linien",
    "category": "Hinweis",
    "title": "48A: Neubaugasse, Burggasse",
    "description": (
        "<p><strong>Haltestellenverlegung der Linie 48A in Richtung Parlament, U Volkstheater"
        "</strong></p><p>Haltestelle: Neubaugasse, Burggasse</p><p>Von: Burggasse vor "
        "Neubaugasse</p><p>Nach: Burggasse 69</p><p>Dauer: 08. September 2026, etwa 06:00 "
        "Uhr für etwa sechs Wochen</p><p>Grund: Umbau der Haltestelle</p>"
    ),
    "guid": "wl-48a",
    "starts_at": datetime(2026, 9, 8, tzinfo=_VIENNA),
    "ends_at": datetime(2026, 11, 10, 23, 59, tzinfo=_VIENNA),
}


def _guids(items: list[FeedItem]) -> list[str]:
    return [str(it["guid"]) for it in items]


@pytest.mark.parametrize(
    ("title", "street"),
    [
        ("Burggasse 67", "Burggasse"),
        ("Neilreichgasse von Gudrunstraße bis Davidgasse", "Neilreichgasse"),
        ("Inzersdorfer Straße Kreuzung Leibnizgasse", "Inzersdorfer Straße"),
        ("Universitätsring und Schottenring von Rathausplatz bis Heßgasse", "Universitätsring"),
        ("Wien Hetzendorf: Altmannsdorfer Straße ONr.76 bis ONr.76A", "Altmannsdorfer Straße"),
        ("A4 (Ostautobahn) von Knoten Prater bis Landesgrenze", None),
    ],
)
def test_site_street(title: str, street: str | None) -> None:
    assert site_street(title) == street


@pytest.mark.parametrize(
    ("text", "street", "expected"),
    [
        ("Bauarbeiten Wegen Bauarbeiten im Bereich Neilreichgasse # Davidgasse wird die "
         "Linie 7A umgeleitet. Zeitraum: Ab Mittwoch", "Neilreichgasse", True),
        ("Aufgrund von Bauarbeiten in der Maxingstraße durch die Wiener Netze müssen die "
         "Linien 56A, 56B, 58A und 58B umgeleitet werden.", "Maxingstraße", True),
        ("Wegen der Sanierung der Floridsdorfer Brücke kommt es zu folgenden Maßnahmen "
         "auf der Linie 31.", "Floridsdorfer Brücke", True),
        # Only in the detour or the stop list, not where the works are.
        ("Wegen Straßenbauarbeiten im Bereich Inzersdorfer Straße # Leibnizgasse werden die "
         "Linien 7A, 65A und 66A umgeleitet. Umleitung ab Inzersdorfer Straße # "
         "Neilreichgasse über Neilreichgasse – Troststraße", "Neilreichgasse", False),
        ("62: Züge halten bei Linie 18, Richtung Burggasse", "Burggasse", False),
        ("Haltestelle: Neubaugasse, Burggasse Von: Burggasse vor Neubaugasse Nach: "
         "Burggasse 69", "Burggasse", False),
    ],
)
def test_names_site_street(text: str, street: str, expected: bool) -> None:
    assert names_site_street(text, street) is expected


@pytest.mark.parametrize(
    ("notice", "expected"),
    [
        ("Haltestelle: Neubaugasse, Burggasse Von: Burggasse vor Neubaugasse Nach: Burggasse 69", True),
        # 48A St.-Ulrichs-Platz, July 2026: another stop on the same street.
        ("Haltestelle: St.-Ulrichs-Platz Von: Burggasse 25 Nach: Burggasse 27", False),
        # 13A flea-market detour, 05.09.2026: a stop list along the street.
        ("- St.-Ulrichs-Platz (Burggasse 25, bei Linie 48A) - Neubaugasse, Burggasse "
         "(Neubaugasse 69)", False),
    ],
)
def test_shares_address(notice: str, expected: bool) -> None:
    assert shares_address(str(_BURGGASSE["description"]), notice) is expected


def test_a_site_with_a_running_wl_twin_leaves() -> None:
    out = bf._drop_baustellen_twins([_BURGGASSE, _WL_48A], _NOW)
    assert _guids(out) == ["wl-48a"]


def test_a_site_without_a_twin_stays() -> None:
    other = dict(_WL_48A, title="48A: St.-Ulrichs-Platz", guid="wl-other",
                 description="Haltestelle: St.-Ulrichs-Platz Von: Burggasse 25 Nach: Burggasse 27")
    out = bf._drop_baustellen_twins([_BURGGASSE, other], _NOW)
    assert _guids(out) == ["bau-burggasse", "wl-other"]


def test_an_announced_wl_notice_is_no_twin_yet() -> None:
    later = dict(_WL_48A, starts_at=datetime(2026, 10, 19, tzinfo=_VIENNA))
    out = bf._drop_baustellen_twins([_BURGGASSE, later], _NOW)
    assert "bau-burggasse" in _guids(out)


def test_a_live_wl_incident_is_no_twin() -> None:
    incident = dict(_WL_48A, category="Störung")
    out = bf._drop_baustellen_twins([_BURGGASSE, incident], _NOW)
    assert "bau-burggasse" in _guids(out)


def test_an_extended_site_counts_from_its_start() -> None:
    # first_seen 08.10. (new GUID after the extension), start 28.07.
    state = {"bau-burggasse": {"first_seen": "2026-10-08T12:30:45+00:00"}}
    key = bf._recency_sort_key(_BURGGASSE, state, _NOW)
    assert -key[1] == datetime(2026, 7, 28, tzinfo=_VIENNA).timestamp()


def test_a_site_seen_before_its_start_keeps_its_moment() -> None:
    site = dict(_BURGGASSE, starts_at=datetime(2026, 10, 12, tzinfo=_VIENNA))
    state = {"bau-burggasse": {"first_seen": "2026-10-08T12:30:45+00:00"}}
    key = bf._recency_sort_key(site, state, _NOW)
    assert -key[1] == datetime(2026, 10, 8, 12, 30, 45, tzinfo=UTC).timestamp()


def test_wl_items_keep_their_first_seen() -> None:
    state = {"wl-48a": {"first_seen": "2026-10-08T12:30:45+00:00"}}
    key = bf._recency_sort_key(_WL_48A, state, _NOW)
    assert -key[1] == datetime(2026, 10, 8, 12, 30, 45, tzinfo=UTC).timestamp()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("von Burggasse ONr.67 nach Burggasse ONr. 69", "von Burggasse Nr. 67 nach Burggasse Nr. 69"),
        ("Atzgersdorfer Straße in Höhe ONr.42", "Atzgersdorfer Straße in Höhe Nr. 42"),
        ("von der ON 125 bis zur ON 141", "von der Nr. 125 bis zur Nr. 141"),
        ("Busse halten Währinger Straße ONr. 200-202", "Busse halten Währinger Straße Nr. 200-202"),
        ("ONLINE 5 und DON 3", "ONLINE 5 und DON 3"),
    ],
)
def test_ordnungsnummer_reads_nr(text: str, expected: str) -> None:
    assert bf._ordnungsnummer_as_nr(text) == expected
