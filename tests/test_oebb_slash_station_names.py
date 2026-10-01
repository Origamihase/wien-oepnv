"""ÖBB writes "an der"/"am" as a slash: Bruck/Leitha, Tulln/Donau.

The directory knows "Bruck an der Leitha" and "Tulln an der Donau", not
the slash forms. A route Wien ↔ Bruck/Leitha classified as Wien ↔ unknown,
and the strict route check dropped the message: a disruption on the line
to one of the busiest commuter stations never reached the feed. The cache
cannot show such a case, because a dropped message is never cached; the
REX 41 item of 2026-09-30 shows the shape ("zwischen Tulln/Donau Bahnhof
und Wien Franz-Josefs-Bahnhof"). Five commuter stations failed in the slash
form; the others resolve through their aliases.

``station_info`` now tries "X an der Y" and "X am Y" for "X/Y" after every
other variant, so a name that resolves as written keeps its station.

The REX 41 item names four routes from one hub. With Tulln resolving, the
``" / "`` renderer would have repeated the hub four times (about 180
characters); ``_try_star_routes`` names it once.

Mutations checked against this file (each one caught, by the test named):

* no slash variant → ``test_a_slash_name_resolves`` and
  ``test_a_route_to_bruck_leitha_reaches_the_feed``.
* no star rendering → ``test_a_star_names_its_hub_once``.

Trying the slash variant before the name as written is equivalent today:
none of the 378 slash names in the directory resolves differently either
way. The order is a safeguard for names added later.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb
from src.utils.stations import station_info

# Verbatim from cache/oebb_c40d21/events.json (guid …TRACKINFO&915701,
# 2026-09-30 14:07), trimmed before the alternatives.
_REX_41 = (
    "31.10.2026 - 04.11.2026<br/><br/>Wegen Bauarbeiten können<br>von <b>02.11.2026</b> bis "
    "<b>03.11.2026</b><br>zwischen <b>Wien Franz-Josefs-Bahnhof</b> und <b>St.Andrä-Wördern "
    "Bahnhof</b><br>keine R 40-Züge fahren.<br>Die REX 41-Züge 2153 und 2157 können<br>zwischen "
    "<b>Tulln/Donau Bahnhof</b> und <b>Wien Franz-Josefs-Bahnhof</b> nicht fahren.<br>Von "
    "<b>31.10.2026</b> (22:00 Uhr) bis <b>01.11.2026</b> (06:00 Uhr) können<br>zwischen <b>Wien "
    "Franz-Josefs-Bahnhof</b> und <b>Wien Heiligenstadt Bahnhof</b><br>keine REX 4-Züge fahren.<br>"
    "Von <b>31.10.2026</b> (22:00 Uhr) <b>bis 03.11.2026</b> können<br>zwischen <b>Wien "
    "Franz-Josefs-Bahnhof</b> und <b>Wien Nußdorf Bahnhst</b><br>keine S 40-Züge fahren."
)


@pytest.mark.parametrize(
    ("written", "station"),
    [
        ("Bruck/Leitha", "Bruck an der Leitha"),
        ("Bruck/Leitha Bahnhof", "Bruck an der Leitha"),
        ("Tulln/Donau", "Tulln an der Donau"),
        ("Brunn/Gebirge", "Brunn am Gebirge"),
        ("Neusiedl/See", "Neusiedl am See"),
        ("Hadersdorf/Kamp", "Hadersdorf am Kamp"),
    ],
)
def test_a_slash_name_resolves(written: str, station: str) -> None:
    info = station_info(written)
    assert info is not None and info.name == station


@pytest.mark.parametrize(
    ("written", "station"),
    [
        ("Linz/Donau", "Linz Hbf"),
        ("Krems/Donau", "Krems a.d.Donau"),
        ("Wien Nestroyplatz/Praterstraße", "Wien Nestroyplatz/Praterstraße (WL)"),
    ],
)
def test_a_name_that_resolves_as_written_keeps_its_station(written: str, station: str) -> None:
    info = station_info(written)
    assert info is not None and info.name == station


def test_two_station_names_do_not_become_one() -> None:
    assert station_info("Wien Spittelau Bahnhst/Wien Mitte-Landstraße") is None


def test_a_route_to_bruck_leitha_reaches_the_feed() -> None:
    description = (
        "01.10.2026<br/><br/>Wegen einer Weichenstörung sind zwischen <b>Wien Hbf (U) </b>und "
        "<b>Bruck/Leitha Bahnhof Zugfahrten</b> derzeit nur eingeschränkt möglich."
    )
    xml = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0"><channel><item>
<title><![CDATA[ Wien Hbf (U) &lt; ↔ &gt; Bruck/Leitha Bahnhof ]]></title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;1</guid>
<pubDate>Thu, 01 Oct 2026 07:00:00 +0200</pubDate>
<description><![CDATA[ {description} ]]></description>
</item></channel></rss>
"""
    with patch.object(oebb, "_fetch_xml", return_value=ET.fromstring(xml)):
        events = oebb.fetch_events()
    assert [event["title"] for event in events] == ["Wien Hauptbahnhof ↔ Bruck an der Leitha"]


def test_a_star_names_its_hub_once() -> None:
    # The message disrupts four lines; all of them lead (2026-10-01).
    assert oebb._apply_route_title("REX 41: Bauarbeiten", _REX_41) == (
        "R 40/REX 41/REX 4/S 40: Wien Franz-Josefs-Bahnhof ↔ St.Andrä-Wördern / Tulln an der Donau / "
        "Wien Heiligenstadt / Wien Nußdorf"
    )


def test_two_routes_from_one_hub_still_chain() -> None:
    assert oebb._format_route_title([("Wien Meidling", "Mödling"), ("Wien Meidling", "Baden")]) == (
        "Baden ↔ Wien Meidling ↔ Mödling"
    )
