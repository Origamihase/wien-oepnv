"""ÖBB notices on several lines: every line in short (operator decision 2026-11-05).

"REX50/REX51/S50: Wien Westbahnhof ↔ St. Pölten Hbf" (02.11. to 11.11.2026)
read "… keine REX50-Züge fahren." on the TV: ÖBB's second sentence, about
REX51 and S50 trains between Wien Westbahnhof and Wien Hütteldorf, is a list
of about 60 train numbers and never fitted the 180 characters (Jules audits
of 31.10. and 02.11.2026). Like a WL disruption on several lines the text
now shows every line in short (src/feed_build/oebb_lines_short.py).
Trains ÖBB offers instead ("die REX6-Züge … zu nehmen") no longer count as
disrupted in the title (_affected_lines).

Mutations checked against this file (each one caught, by the test named):

* the short form is not called → test_rex50_rex51_s50_shows_every_line.
* sections are not joined → test_rex50_rex51_s50_shows_every_line.
* days are first/last instead of earliest/latest → test_rex50_rex51_s50_shows_every_line.
* nothing is dropped beyond 180 → test_five_sections_end_with_an_ellipsis.
* the short form also replaces a text that names every line → test_a_text_naming_every_line_stays.
* the offered-trains check is removed → test_offered_trains_are_not_disrupted.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from src.feed_build import translate_run
from src.feed_build.base import _parse_datetime
from src.feed_build.item_format import _format_item_content
from src.feed_build.oebb_lines_short import _oebb_every_line_in_short, _oebb_lines_in_short
from src.feed_watch.feed_rules import check_oebb_second_sentence
from src.feed_types import FeedItem
from src.feed_watch.model import Item
from src.providers.oebb_route_title import _affected_lines

# The cache entries (cache/oebb_c40d21/events.json) of 2026-10-27 and 2026-10-28.
REX50_DESCRIPTION = (
    "02.11.2026 - 11.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>St.Pölten Hbf<"
    "/b> und <b>Wien Westbahnhof (U)</b><br>von <b>03.11.2026</b> bis <b>10.11.2026 </b><br>k"
    "eine REX50-Züge fahren.<br>Zwischen <b> Wien Westbahnhof (U) </b>und <b>Wien Hütteldorf "
    "Bahnhof (U) </b>können<br>von <b>03.11.2026</b> bis <b>10.11.2026</b><br>die REX51-Züge "
    "1604, 1608, 1614, 1618, 1622, 1626, 1630, 1634, 1638, 1642, 1646, 1650, 1654, 1658, 1662"
    ", 1601, 1605, 1609, 1613, 1617, 1621, 1625, 1629, 1633, 1637, 1641, 1645, 1649, 1653 und"
    " 1657 <br>von <b>02.11.2026</b> bis <b>10.11.2026</b><br>die REX51-Züge 1670, 1674, 1661"
    ", 1665 und 1675<br>von <b>03.11.2026</b> bis <b>10.11.2026</b><br>keine S50-Züge<br>von "
    "<b>02.11.2026</b> bis <b>10.11.2026</b><br>die S50-Züge 20063, 20161, 20065, 20163, 2006"
    "7, 20165, 20069, 20071, 20073, 20075, 20077, 20081, 20062, 20064, 20066, 20068, 20070, 2"
    "0072, 20074, 20076 und 20078<br>von <b>03.11.2026</b> bis <b>11.11.2026</b><br>die S50-Z"
    "üge 20083 und 20080<br>nicht fahren.<br>Als Ersatz haben Sie die Möglichkeit den nächste"
    "n fahrplanmäßigen Zug zu nehmen. <br><br>Wir bitten um Entschuldigung. <br/><a href=\"htt"
    "ps://kundeninformation-pv.oebb.at/dam/kundeninformation/extern/OEBB/2026/2026_11/Serv102"
    "50_Gesamt.pdf\">Details finden Sie hier...</a>"
)
FJB_DESCRIPTION = (
    "31.10.2026 - 04.11.2026<br/><br/>Wegen Bauarbeiten können<br>von <b>02.11.2026</b> bis <"
    "b>03.11.2026</b><br>zwischen <b>Wien Franz-Josefs-Bahnhof</b> und <b>St.Andrä-Wördern Ba"
    "hnhof</b><br>keine R 40-Züge fahren.<br>Der REX 41-Zug 2157 kann<br>zwischen <b>Tulln/Do"
    "nau Bahnhof</b> und <b>Wien Franz-Josefs-Bahnhof</b> nicht fahren.<br>Als Ersatz haben S"
    "ie die Möglichkeit den nächsten fahrplanmäßigen Zug zu nehmen.<br><br>Von <b>31.10.2026<"
    "/b> (22:00 Uhr) bis <b>01.11.2026</b> (06:00 Uhr) können<br>zwischen <b>Wien Franz-Josef"
    "s-Bahnhof</b> und <b>Wien Heiligenstadt Bahnhof</b><br>keine REX 4-Züge fahren.<br>Von <"
    "b>31.10.2026</b> (22:00 Uhr) <b>bis 03.11.2026</b> können<br>zwischen <b>Wien Franz-Jose"
    "fs-Bahnhof</b> und <b>Wien Nußdorf Bahnhst</b><br>keine S 40-Züge fahren.<br>Am <b>01.11"
    ".2026</b> können<br>zwischen <b>Wien Franz-Josefs-Bahnhof</b> und <b>Wien Heiligenstadt "
    "Bahnhof</b><br>keine REX 41-Züge fahren.<br><br>Reisende mit gültigem Ticket haben die M"
    "öglichkeit folgende Alternativverbindungen der <b>Wiener Linien</b> zu nutzen:<br><b>Lin"
    "ie Tram D</b> zwischen <b>Wien Augasse</b> und <b>Wien Nußdorf Bahnhst</b><br><b>Linie U"
    "4</b> zwischen <b>Wien Heiligenstadt Bahnhof</b> und <b>Wien Spittelau Bahnhst/Wien Frie"
    "densbrücke</b><br><b>VOR-Linie Bus 400</b> zwischen <b>Wien Heiligenstadt Bahnhof</b> un"
    "d <b>Wien Nußdorf Bahnhst/Klosterneuburg-Kierling Bahnhof</b><br><br><b>SCHIENENERSATZVE"
    "RKEHR ab 02.11.2026:</b><br>Zusätzlich wird für Sie von <b>02.11.2026</b> bis <b>03.11.2"
    "026</b> ein Schienenersatzverkehr mit Autobussen zwischen <b>Wien Heiligenstadt Bahnhof<"
    "/b> und <b>Wien Nußdorf Bahnhst</b> eingerichtet.<br><b><br>ACHTUNG:</b><br>Anschlussver"
    "bindungen können nicht gewährleistet werden.<br><b>HINWEISE:</b><br>Bitte beachten Sie, "
    "dass der Zustieg in Stationen mit Ticketautomaten oder Ticketschalter ausschließlich mit"
    " einem gültigen Ticket gestattet ist. Der Ticketverkauf in den Bussen des Schienenersatz"
    "verkehres ist nur eingeschränkt möglich.<br>Fahrräder können nicht befördert werden.<b><"
    "br><br>Mobilitätseingeschränkten Fahrgästen,</b> die für ihre Fahrt Unterstützung benöti"
    "gen, wird dringend empfohlen, sich vor Reiseantritt mit dem ÖBB Kund:innenservice unter "
    "05-1717-5 in Verbindung zu setzen, um eine barrierefreie Reisekette zu gewährleisten.<br"
    "><br>Wir bitten um Entschuldigung. <br/><a href=\"https://kundeninformation-pv.oebb.at/da"
    "m/kundeninformation/extern/OEBB/2026/2026_10/Serv10284_Gesamt.pdf\">Details finden Sie hi"
    "er...</a>"
)


def _event(guid: str, title: str, description: str, starts: str, ends: str) -> dict[str, Any]:
    return {
        "source": "ÖBB",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://fahrplan.oebb.at/",
        "guid": guid,
        "starts_at": starts,
        "ends_at": ends,
    }


def _summary(event: dict[str, Any]) -> str:
    content = _format_item_content(
        cast(FeedItem, event),
        event["guid"],
        _parse_datetime(event["starts_at"]),
        _parse_datetime(event["ends_at"]),
        lang="de",
    )
    return content.desc_text_truncated


def test_rex50_rex51_s50_shows_every_line() -> None:
    event = _event(
        "oebb-905884",
        "REX 50/REX 51/S 50: Wien Westbahnhof ↔ St. Pölten Hauptbahnhof",
        REX50_DESCRIPTION,
        "2026-11-02T00:00:00+01:00",
        "2026-11-11T23:59:59+01:00",
    )
    assert _summary(event).startswith(
        "Wegen Bauarbeiten fallen Züge aus: REX50 St. Pölten Hbf ↔ Wien Westbahnhof 03.–10.11.; "
        "REX51, S50 Wien Westbahnhof ↔ Wien Hütteldorf 02.–11.11. ["
    )


def test_five_sections_end_with_an_ellipsis() -> None:
    event = _event(
        "oebb-915701",
        "R 40/REX 41/REX 4/S 40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau",
        FJB_DESCRIPTION,
        "2026-10-31T00:00:00+01:00",
        "2026-11-04T23:59:59+01:00",
    )
    summary = _summary(event)
    text = summary.rsplit(" [", 1)[0]
    assert text.startswith(
        "Wegen Bauarbeiten fallen Züge aus: R40 Wien Franz-Josefs-Bahnhof ↔ St. Andrä-Wördern 02.–03.11.; REX41 "
    )
    assert text.endswith(" …")
    assert len(text) <= 180


def test_a_text_naming_every_line_stays() -> None:
    shown = "Wegen Bauarbeiten können zwischen A und B keine REX50-Züge und keine S50-Züge fahren."
    event = cast(FeedItem, {"source": "ÖBB"})
    assert _oebb_every_line_in_short(event, shown + " Zwischen C und D …", shown, "REX50/S50: A ↔ B") is None


def test_a_line_without_its_section_keeps_the_old_text() -> None:
    summary = (
        "Wegen Bauarbeiten können zwischen A Bahnhof und B Bahnhof keine REX50-Züge fahren. "
        "Außerdem können keine S50-Züge fahren."
    )
    assert _oebb_lines_in_short(summary, ["REX50", "S50"]) is None


def test_wl_items_are_left_alone() -> None:
    shown = "Wegen Bauarbeiten können zwischen A und B keine REX50-Züge fahren."
    assert _oebb_every_line_in_short(cast(FeedItem, {"source": "Wiener Linien"}), shown, shown, "REX50/S50: A ↔ B") is None


def test_offered_trains_are_not_disrupted() -> None:
    description = (
        "Wegen Bauarbeiten können zwischen <b>Marchegg Bahnhof</b> und <b>Bratislava hl.st.</b> "
        "am 15.05.2026 keine REX8-Züge fahren.<br>Als Ersatzbeförderung haben Sie die Möglichkeit "
        "die REX6-Züge von Wien Hbf über Bruck/Leitha und Kittsee zu nehmen. Wir bitten um Entschuldigung."
    )
    assert _affected_lines(description) == "REX 8"


def test_english_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(translate_run, "_oebb_cause_en", lambda *_a, **_k: "Due to construction works")
    rendered = translate_run._render_oebb_sentence(
        "Wegen Bauarbeiten fallen Züge aus: REX50 St. Pölten Hbf ↔ Wien Westbahnhof 03.–10.11.; "
        "REX51, S50 Wien Westbahnhof ↔ Wien Hütteldorf 02.–11.11.",
        "x",
        "ÖBB",
        "Störung",
    )
    assert rendered == (
        "Due to construction works, trains are cancelled: REX50 St. Pölten Hbf ↔ Wien Westbahnhof "
        "03.–10.11.; REX51, S50 Wien Westbahnhof ↔ Wien Hütteldorf 02.–11.11."
    )


def test_feed_watch_accepts_the_short_form() -> None:
    item = Item(
        title="REX50/REX51/S50: Wien Westbahnhof ↔ St. Pölten Hauptbahnhof",
        guid="oebb-905884",
        description="Wegen Bauarbeiten fallen Züge aus: REX50 St. Pölten Hbf ↔ Wien Westbahnhof 03.–10.11.",
    )
    assert check_oebb_second_sentence([item], {"oebb-905884": REX50_DESCRIPTION}) == []
