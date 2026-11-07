"""An ÖBB endpoint that names two stations counts for both of them.

ÖBB lists the alternatives of one side inside the endpoint: "Wien Hbf/Wien
St. Marx <=> Wien Praterstern" in the title, "zwischen Wien Hbf bzw. Wien
St.Marx und Wien Praterstern" in the description (the Stammstrecke closure
of 2026-09-07 to 2027-10-31, published 2026-09-04). The directory knows no
such compound name, so the one route the message had counted as Wien ↔
unknown, and because a route had been found the single-station path never
ran: a Wien ↔ Wien message was dropped as "nicht Wien-relevant" for two
months (found 2026-11-06). The cache since May 2026 shows the same shape in
"Wien Meidling Bahnhof bzw. Wien Hbf (U)" (Gramatneusiedl, dropped at build
time) and "Wien Hütteldorf Bahnhof (U) bzw. Wien Westbahnhof (U)".

Only an endpoint that does not resolve as written is split, and only when
every part is a railway station: the description regexes run on to the next
station name when a sentence does not follow "zwischen A und B", and such an
over-long endpoint may contain a slash from the alternatives ÖBB offers.

Two stretches that end at the same station chained by graph shape alone,
"Wien Hauptbahnhof ↔ Wien Praterstern ↔ Wien St. Marx", as if trains ran
through Wien Praterstern; a hub that does not lie on the way between the
other two ends is a star.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb
from src.providers.oebb_route_title import _chain_is_corridor
from src.providers.oebb_routes import _endpoint_alternatives, _extract_zwischen_routes
from src.utils.stations import station_info

# Verbatim from data/raw/oebb/rss.json (guid …TRACKINFO&909456, pubDate
# 2026-09-04 11:10), trimmed before the Wiener Linien alternatives.
_STAMMSTRECKE = (
    "07.09.2026 - 31.10.2027<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf </b>bzw.<b> "
    "Wien St.Marx</b> und <b>Wien Praterstern</b><br>von <b>07.09.2026</b> bis <b>Ende Oktober 2027 "
    "</b><br>keine Züge fahren.<br>Als Ersatz haben Sie die Möglichkeit das Angebot der Wiener Linien "
    "zu nutzen."
)
# From cache/oebb_c40d21/events.json (git history: in the cache since 2026-05-01,
# published 2026-04-01), cut to the first of its three night windows.
_GRAMATNEUSIEDL = (
    "23.05.2026 - 26.05.2026<br/><br/>Wegen Bauarbeiten können<br>zwischen <b>Wien Meidling Bahnhof</b> "
    "bzw. <b>Wien Hbf (U)</b> und <b>Gramatneusiedl Bahnhof</b><br>von <b>23.05.2026</b> (23:09 Uhr) "
    "bis <b>24.05.2026</b> (04:40 Uhr)<br>keine Nahverkehrszüge fahren."
)


def _stations(parts: list[str]) -> list[str]:
    return [info.name for info in map(station_info, parts) if info is not None]


@pytest.mark.parametrize(
    ("written", "stations"),
    [
        ("Wien Hbf/Wien St. Marx", ["Wien Hauptbahnhof", "Wien St. Marx"]),
        ("Wien Meidling Bahnhof bzw. Wien Hbf (U)", ["Wien Meidling", "Wien Hauptbahnhof"]),
        (
            "Wien Hütteldorf Bahnhof (U) bzw. Wien Westbahnhof (U)",
            ["Wien Hütteldorf", "Wien Westbahnhof"],
        ),
        ("Wien Hbf (U) bzw Wien Meidling", ["Wien Hauptbahnhof", "Wien Meidling"]),
        # The slash splits only a part that does not resolve as a whole.
        ("Tulln/Donau bzw. Krems/Donau", ["Tulln an der Donau", "Krems a.d.Donau"]),
    ],
)
def test_two_stations_in_one_endpoint_count_for_both(written: str, stations: list[str]) -> None:
    parts = _endpoint_alternatives(written)
    assert len(parts) == 2
    assert _stations(parts) == stations


@pytest.mark.parametrize(
    "written",
    [
        # Resolve as a whole ("an der"): one station, not two.
        "Krems/Donau",
        "Bruck/Leitha",
        # Unknown as a whole and in its parts.
        "Kammer/Attersee-Schörfling",
        # Over-long endpoints of the description regexes, from the Wiener
        # Linien alternatives of the Franz-Josefs-Bahn works (2026-10-28) and
        # the replacement buses of 2026-09-07. Split on their one resolvable
        # part they made routes of their own.
        "Wien Nußdorf Bahnhst Linie U4 zwischen Wien Heiligenstadt Bahnhof und Wien Spittelau "
        "Bahnhst/Wien Friedensbrücke",
        "Flughafen Wien Bahnhof bzw. Wien Hbf (U) angefordert . Planen Sie in diesem Bereich",
        # Two tram stops, two U-Bahn stations (known to Wiener Linien and VOR
        # only): no end of an ÖBB stretch.
        "Wien Augasse/Wien Friedensbrücke",
        "Wien Karlsplatz/Wien Schottentor",
    ],
)
def test_every_other_compound_stays_one_endpoint(written: str) -> None:
    assert _endpoint_alternatives(written) == [written]


def test_the_stammstrecke_closure_reaches_the_feed() -> None:
    xml = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0"><channel><item>
<title><![CDATA[ Bauarbeiten - Zugausfall: Wien Hbf/Wien St. Marx &#60;=&#62; Wien Praterstern ]]></title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&amp;tpl=showmap_external&amp;</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;909456</guid>
<pubDate>Fri, 04 Sep 2026 11:10:05 +0100</pubDate>
<description><![CDATA[ {_STAMMSTRECKE} ]]></description>
</item></channel></rss>
"""
    with patch.object(oebb, "_fetch_xml", return_value=ET.fromstring(xml)):
        events = oebb.fetch_events()
    assert [event["title"] for event in events] == [
        "Wien Praterstern ↔ Wien Hauptbahnhof / Wien St. Marx"
    ]
    (event,) = events
    starts_at, ends_at = event["starts_at"], event["ends_at"]
    assert starts_at is not None and ends_at is not None
    assert starts_at.date().isoformat() == "2026-09-07"
    assert ends_at.date().isoformat() == "2027-10-31"


def test_the_second_station_of_a_description_route_counts() -> None:
    routes = _extract_zwischen_routes(_GRAMATNEUSIEDL)
    assert [(_stations([a])[0], _stations([b])[0]) for a, b in routes] == [
        ("Wien Meidling", "Gramatneusiedl"),
        ("Wien Hauptbahnhof", "Gramatneusiedl"),
    ]
    # Wien Hauptbahnhof lies on the way from Wien Meidling to Gramatneusiedl.
    assert oebb._format_route_title(routes) == "Wien Meidling ↔ Gramatneusiedl"


def test_a_hub_at_the_end_of_both_stretches_is_a_star() -> None:
    assert oebb._format_route_title(
        [("Wien Hbf", "Wien Praterstern"), ("Wien St. Marx", "Wien Praterstern")]
    ) == "Wien Praterstern ↔ Wien Hauptbahnhof / Wien St. Marx"
    # Polizeieinsatz of 2026-08-17: "Wien Hauptbahnhof ↔ Wien Westbahnhof ↔
    # Wien Meidling" read as if trains ran through Wien Westbahnhof.
    assert oebb._format_route_title(
        [("Wien Hbf", "Wien Westbahnhof"), ("Wien Westbahnhof", "Wien Meidling")]
    ) == "Wien Westbahnhof ↔ Wien Hauptbahnhof / Wien Meidling"


def test_a_station_on_the_way_still_chains() -> None:
    assert oebb._format_route_title(
        [("Wien Westbahnhof", "Wien Hütteldorf"), ("Wien Hütteldorf", "Tullnerbach-Pressbaum")]
    ) == "Wien Westbahnhof ↔ Wien Hütteldorf ↔ Tullnerbach-Pressbaum"


def test_unknown_coordinates_keep_the_chain() -> None:
    assert _chain_is_corridor(["A", "B", "C"], {}) is True
