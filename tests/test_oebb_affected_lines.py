"""ÖBB: every disrupted line in front of the route (audit 2026-10-01, open point 3).

Live on 2026-10-01, ``docs/feed.xml``: "REX 41: Wien Franz-Josefs-Bahnhof ↔
St.Andrä-Wördern / Tulln an der Donau / Wien Heiligenstadt / Wien Nußdorf"
over "… keine R 40-Züge fahren". The message disrupts R 40, REX 41, REX 4
and S 40; ``_LINE_TOKEN_RE`` knew no ``R``, so the second line named became
the prefix of all four. In the ÖBB cache since September 3 of 45 messages
named several lines, and one more named only an R line (R 95, no prefix).

Mutations checked against this file (each one caught, by the test named):

* ``R`` is dropped from ``_LINE_TOKEN_RE`` → ``test_the_four_lines_of_the_franz_josefs_bahn_lead``.
* only the first line is kept → ``test_the_four_lines_of_the_franz_josefs_bahn_lead``.
* a line named twice is listed twice → ``test_the_four_lines_of_the_franz_josefs_bahn_lead``.
* the cached prefix is not recognised → ``test_re_reading_the_cache_keeps_one_prefix``.
"""

from __future__ import annotations

from typing import Any

from src.build_feed import _post_filter_oebb
from src.providers import oebb

# The cache entry of 2026-10-01 (``cache/oebb_c40d21/events.json``), shortened.
FRANZ_JOSEFS_BAHN = (
    "31.10.2026 - 04.11.2026<br/><br/>Wegen Bauarbeiten können<br>von <b>02.11.2026</b> bis "
    "<b>03.11.2026</b><br>zwischen <b>Wien Franz-Josefs-Bahnhof</b> und <b>St.Andrä-Wördern "
    "Bahnhof</b><br>keine R 40-Züge fahren.<br>Die REX 41-Züge 2153 und 2157 können<br>zwischen "
    "<b>Tulln/Donau Bahnhof</b> und <b>Wien Franz-Josefs-Bahnhof</b> nicht fahren.<br>Von "
    "<b>01.11.2026</b> bis <b>03.11.2026</b> kann<br>der REX 41-Zug 2155<br>zwischen "
    "<b>Tulln/Donau Bahnhof</b> und <b>Wien Franz-Josefs-Bahnhof</b> nicht fahren.<br>Von "
    "<b>31.10.2026</b> (22:00 Uhr) bis <b>01.11.2026</b> (06:00 Uhr) können<br>zwischen <b>Wien "
    "Franz-Josefs-Bahnhof</b> und <b>Wien Heiligenstadt Bahnhof</b><br>keine REX 4-Züge fahren.<br>"
    "Von <b>31.10.2026</b> (22:00 Uhr) <b>bis 03.11.2026</b> können<br>zwischen <b>Wien "
    "Franz-Josefs-Bahnhof</b> und <b>Wien Nußdorf Bahnhst</b><br>keine S 40-Züge fahren.<br><br>"
    "Reisende mit gültigem Ticket haben die Möglichkeit folgende Alternativverbindungen der "
    "<b>Wiener Linien</b> zu nutzen:<br><b>Linie Tram D</b> zwischen <b>Wien Franz-Josefs-Bahnhof</b> "
    "und <b>Wien Nußdorf Bahnhst</b><br><b>Linie U4</b> zwischen <b>Wien Heiligenstadt</b>"
)
# Every stretch of the four lines lies on the way to Tulln an der Donau
# (``_drop_contained_routes``, 2026-10-02).
FJB_TITLE = "R 40/REX 41/REX 4/S 40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau"


def test_the_four_lines_of_the_franz_josefs_bahn_lead() -> None:
    # In the order the message names them; REX 41 twice is listed once,
    # and the alternatives of the Wiener Linien ("Linie U4") are none.
    assert oebb._affected_lines(FRANZ_JOSEFS_BAHN) == "R 40/REX 41/REX 4/S 40"
    assert oebb._apply_route_title("Bauarbeiten: Wien Franz-Josefs-Bahnhof", FRANZ_JOSEFS_BAHN) == FJB_TITLE


def test_an_r_line_alone_leads_too() -> None:
    # Cache, September: "Wien Hauptbahnhof ↔ Felixdorf" without a prefix.
    desc = (
        "Wegen Bauarbeiten können am 10.10.2026 und 11.10.2026 (jeweils 14:00 Uhr - 22:00 Uhr) "
        "zwischen Wien Hauptbahnhof und Felixdorf Bahnhof keine R95-Züge fahren."
    )
    assert oebb._apply_route_title("Wien Hauptbahnhof ↔ Felixdorf", desc) == "R 95: Wien Hauptbahnhof ↔ Felixdorf"


def test_a_prefix_of_several_lines_is_one_prefix() -> None:
    assert oebb._extract_line_prefix("R 40/REX 41: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau") == (
        "R 40/REX 41",
        "Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau",
    )
    # A colonless title starting with a line code stays unprefixed.
    assert oebb._extract_line_prefix("R 5 Wien Hbf") == ("", "R 5 Wien Hbf")


def test_re_reading_the_cache_keeps_one_prefix() -> None:
    # ``_post_filter_oebb`` re-derives a cached title on every build.
    items: list[Any] = [{"title": FJB_TITLE, "description": FRANZ_JOSEFS_BAHN}]
    (item,) = _post_filter_oebb(items)
    assert item["title"] == FJB_TITLE
    # The same without a route the filter keeps: the prefix is not put in front twice.
    single = [{"title": "R 40/REX 41: Bauarbeiten", "description": "Keine R 40-Züge und REX 41-Züge."}]
    assert oebb._apply_route_title(single[0]["title"], single[0]["description"]) == "R 40/REX 41: Bauarbeiten"
