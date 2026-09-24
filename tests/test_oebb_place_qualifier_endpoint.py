"""A second route endpoint keeps its ``im``/``am`` place qualifier.

Published 2026-09-22 05:31 as item 4 of ``docs/feed.xml``::

    REX 6: Wien Baumgarten (WL) ↔ Ebenfurth

The ÖBB description reads "zwischen Ebenfurth Bahnhof und Baumgarten im
Bgld-Schattendorf Bahnhof" — Baumgarten in Burgenland. ``im`` and ``am``
sit in the boundary alternations of ``_ZWISCHEN_PLAIN_RE`` /
``_VON_NACH_PLAIN_RE`` as time prepositions, so the second endpoint was cut
to "Baumgarten", which resolves to the Wiener-Linien stop "Wien Baumgarten
(WL)". A Burgenland route (Ebenfurth ↔ Baumgarten/Schattendorf, neither end
in Vienna) passed as Wien ↔ Pendler, got its title rewritten to the Vienna
stop and took one of the ten slots on the displays.

The fix re-attaches a single capitalised qualifier word when the station
suffix (``Bahnhof``/``Bf``/``Hbf``) follows it directly — the shape ÖBB
writes for a full station name. A date ("Felixdorf Bahnhof am 10.02.2026"),
a weekday or "im Bereich Wien Hbf" never has that shape.

Measured over the 32 distinct ÖBB items in the cache history: the REX 6
item is the only endpoint the boundary cut at ``im``/``am``; the other four
cuts at those words are genuine dates and stay as they are.

Mutations checked against this file (each one caught, by the test named):

* ``_with_place_qualifier`` returns the bare group again →
  ``test_the_captured_burgenland_route_is_not_wien_relevant`` and the
  ``Brunn am Gebirge``/``Neusiedl am See`` cases.
* the station-suffix lookahead is dropped →
  ``test_a_time_qualifier_is_not_attached``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb
from src.utils.stations import station_info

# Verbatim from cache/oebb_c40d21/events.json (guid …TRACKINFO&913609),
# trimmed after the route sentences.
_CAPTURED_DESCRIPTION = (
    "27.10.2026 - 30.10.2026<br/><br/>Wegen Bauarbeiten können<br>von "
    "<b>27.10.2026</b> (08:30 Uhr) bis <b>30.10.2026</b> (23:59 Uhr)<br>"
    "zwischen <b>Ebenfurth Bahnhof</b> und <b>Baumgarten im Bgld-Schattendorf "
    "Bahnhof</b><br><span>keine <span>REX 6-Züge </span>fahren.</span><br>"
    "Zwischen <span><b>Eisenstadt Bahnhof </b>und<b> Wulkaprodersdorf Bahnhof</b> "
    "</span><span>können</span><br><span>keine <span>REX 65-Züge </span>fahren"
    "</span>.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)


def test_the_captured_second_endpoint_keeps_its_qualifier() -> None:
    routes = oebb._extract_zwischen_routes(_CAPTURED_DESCRIPTION)
    assert ("Ebenfurth", "Baumgarten im Bgld-Schattendorf") in routes
    assert all(b != "Baumgarten" for _, b in routes)


def test_the_captured_burgenland_route_is_not_wien_relevant() -> None:
    assert oebb._is_relevant("REX 6: Bauarbeiten", _CAPTURED_DESCRIPTION) is False


@pytest.mark.parametrize(
    "raw_title",
    [
        # The ÖBB feed writes routes as "<A> < ↔ > <B>" with display names.
        "Baumgarten im Bgld-Schattendorf &lt; ↔ &gt; Ebenfurth",
        "Ebenfurth &lt; ↔ &gt; Baumgarten im Bgld-Schattendorf",
        # No route in the title at all: the description alone decides.
        "Bauarbeiten",
    ],
)
def test_the_captured_item_does_not_reach_the_feed(raw_title: str) -> None:
    xml = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0"><channel><item>
<title><![CDATA[ {raw_title} ]]></title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;913609</guid>
<pubDate>Tue, 22 Sep 2026 07:00:00 +0200</pubDate>
<description><![CDATA[ {_CAPTURED_DESCRIPTION} ]]></description>
</item></channel></rss>
"""
    with patch.object(oebb, "_fetch_xml", return_value=ET.fromstring(xml)):
        events = oebb.fetch_events()
    assert events == []
    assert all("Wien Baumgarten" not in str(ev.get("title")) for ev in events)


@pytest.mark.parametrize(
    ("place", "canonical"),
    [
        # Pendler stations that were cut to an unknown first word before.
        ("Brunn am Gebirge", "Brunn am Gebirge"),
        ("Neusiedl am See", "Neusiedl am See"),
    ],
)
def test_a_pendler_station_with_a_qualifier_resolves_in_full(place: str, canonical: str) -> None:
    desc = f"Wegen Bauarbeiten können zwischen Wien Meidling Bahnhof und {place} Bahnhof keine Züge fahren."
    routes = oebb._extract_zwischen_routes(desc)
    assert routes == [("Wien Meidling", place)]
    info = station_info(routes[0][1])
    assert info is not None and info.name == canonical
    assert oebb._route_is_wien_relevant(*routes[0]) is True


def test_von_nach_phrasing_keeps_the_qualifier_too() -> None:
    desc = "Wegen Bauarbeiten fahren von Wien Meidling nach Brunn am Gebirge Bahnhof keine Züge."
    assert ("Wien Meidling", "Brunn am Gebirge") in oebb._extract_zwischen_routes(desc)


@pytest.mark.parametrize(
    ("description", "expected_b"),
    [
        # Verbatim shape from the cache history: the suffix precedes "am".
        ("zwischen Wien Meidling Bahnhof und Felixdorf Bahnhof am 10.02.2026 am 10.03.2026 keine Züge", "Felixdorf"),
        ("zwischen Wien Meidling und Mödling am Wochenende keine Züge", "Mödling"),
        ("zwischen Wien Meidling und Mödling im Bereich Wien Hbf gesperrt", "Mödling"),
        ("zwischen Wien Meidling und Mödling am 03.10.2026 Bahnhof gesperrt", "Mödling"),
    ],
)
def test_a_time_qualifier_is_not_attached(description: str, expected_b: str) -> None:
    routes = oebb._extract_zwischen_routes(description)
    assert routes[0] == ("Wien Meidling", expected_b)
