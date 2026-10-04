"""A Pendler station that ÖBB names with its river or region resolves.

ÖBB writes "Mistelbach/Zaya" and "Wolkersdorf im Weinviertel"; the
directory knows "Mistelbach" and "Wolkersdorf" (both Pendler stations of
the S2). ``station_info`` found neither, the route counted as Wien ↔
unknown and the strict route check dropped it. Seen in the raw data of the
Update run on 2026-10-04 (``data/raw/oebb/verworfen.json``): "Wien
Leopoldau Bahnhst (U) => Mistelbach/Zaya Bahnhof", an S2 Schienenersatz-
verkehr. No ÖBB message naming Mistelbach or Wolkersdorf had reached the
cache since July.

``station_info`` now tries the bare place name last, and only accepts a
station outside Vienna: a qualified name never lies in Vienna, and "Baumgarten
im Burgenland" must not become the Wiener-Linien stop (see
``test_oebb_place_qualifier_endpoint.py``). Over every name in the eight
raw ÖBB snapshots and every alias of the directory (243,399 names), the
only names that change are the five qualified ones below plus
"Bad Fischau-Brunn/Schneebergbahn"; the 80 ÖBB items in the cache since July
keep their decision.

Mutations checked against this file (each one caught, by the test named):

* no bare-name fallback → ``test_a_qualified_pendler_name_resolves`` and
  ``test_the_captured_s2_item_reaches_the_feed``.
* the ``in_vienna`` guard dropped → ``test_a_bare_name_never_lands_in_vienna``.
"""

from __future__ import annotations

from unittest.mock import patch
from xml.sax.saxutils import escape

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb
from src.utils.stations import station_info


@pytest.mark.parametrize(
    ("written", "station"),
    [
        ("Mistelbach/Zaya", "Mistelbach"),
        ("Mistelbach/Zaya Bahnhof", "Mistelbach"),
        ("Wolkersdorf im Weinviertel", "Wolkersdorf"),
        ("Wolkersdorf im Weinviertel Bahnhof", "Wolkersdorf"),
        ("Traisen NÖ", "Traisen"),
        ("Amstetten NÖ", "Amstetten"),
    ],
)
def test_a_qualified_pendler_name_resolves(written: str, station: str) -> None:
    info = station_info(written)
    assert info is not None and info.name == station


@pytest.mark.parametrize(
    "written",
    ["Baumgarten im Burgenland", "Wien Westbahnhof/Wien", "Hütteldorf im Wald"],
)
def test_a_bare_name_never_lands_in_vienna(written: str) -> None:
    assert station_info(written) is None


def test_names_known_as_written_keep_their_station() -> None:
    for written, station in (
        ("Bruck/Leitha", "Bruck an der Leitha"),
        ("Laa/Thaya Bahnhof", "Laa a.d.Thaya"),
        ("Neusiedl am See", "Neusiedl am See"),
    ):
        info = station_info(written)
        assert info is not None and info.name == station


# Verbatim from data/raw/oebb/rss.json (2026-10-04 13:01 UTC), the
# description trimmed after the replacement-bus sentence.
_S2_TITLE = (
    "Bauarbeiten - Schienenersatzverkehr/geänderte Fahrzeiten: Wien Leopoldau Bahnhst (U) "
    "=&#62; Mistelbach/Zaya Bahnhof"
)
_S2_DESCRIPTION = (
    "25.10.2026 - 01.11.2026<br/><br/>Wegen Bauarbeiten kann<br>zwischen <b>Wien Leopoldau "
    "Bahnhst (U)</b> und <b>Mistelbach/Zaya Bahnhof </b><br>am <b>25</b><b>.10.2026 </b>"
    "<span>und</span> <b>01</b><span><b>.11.2026<br></b></span>der S2-Zug 23360 nicht "
    "fahren.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)


def test_the_captured_s2_item_reaches_the_feed() -> None:
    rss = ET.fromstring(
        "<rss><channel><item>"
        f"<title>{escape(_S2_TITLE)}</title>"
        "<link>https://fahrplan.oebb.at/bin/help.exe/dn</link>"
        "<guid>https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;907728</guid>"
        "<pubDate>Thu, 27 Aug 2026 07:46:32 +0200</pubDate>"
        f"<description>{escape(_S2_DESCRIPTION)}</description>"
        "</item></channel></rss>"
    )
    with patch.object(oebb, "_fetch_xml", return_value=rss):
        events = oebb.fetch_events()
    assert [e["title"] for e in events] == ["S 2: Wien Leopoldau ↔ Mistelbach"]
