"""Three text faults of the feed of 24.10.2026, 22:00, each fixed as a class.

* WL measure blocks whose line labels did not have the plain ``Linie D:``
  shape (``<u>Linie D</u><br>Umleitung …``, ``Linie 11A in Richtung
  Stadion U:``, ``Montag-Freitag, Linie 80A …:``) were not found, so the
  item showed only "… kommt es zu folgenden Verkehrsmaßnahmen." (19 WL
  notices since June 2026, two of them in the feed that evening).
* A stop moved in one direction and closed in the other read "Hermesstraße
  nach Strampfergasse → etwa 100 Meter … Hermesstraße nach Strampfergasse
  → aufgelassen." — both at once, without the directions.
* ÖBB date lists ran together and kept their past days ("am 10.02.2026 am
  10.03.2026 …"); the day still to come fell behind the cut.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

from src import build_feed
from src.build_feed import (
    _oebb_upcoming_dates,
    _truncate_summary_180,
    _wl_measure_labels_with_colon,
)
from src.feed_types import FeedItem


def _render(raw_title: str, raw_desc: str, *, source: str = "Wiener Linien") -> str:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": source,
            "category": "Hinweis" if source == "Wiener Linien" else "Störung",
            "guid": "test",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 10, 20, 0, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 28, 22, 0, tzinfo=UTC),
    )
    return formatted.desc_text_truncated


# The cache's HTML of 24.10.2026.
_SICHERHEITSFEST_HTML = (
    "<h2>Veranstaltung</h2><p>Wegen des Wiener Sicherheitsfestes im Bereich "
    "Rathausplatz kommt es zu folgenden Verkehrsmaßnahmen.</p><p><strong><u>"
    "Zeitraum:</u></strong><br>Sonntag, 25. Oktober 2026, ab etwa 06:30 Uhr bis "
    "etwa 18:00 Uhr.</p><p><strong><u>Maßnahmen:</u></strong></p><p><u>Linie D"
    "</u><br>Umleitung in beiden Richtungen<br>zwischen Schwarzenbergplatz und "
    "Börse<br>über Schwedenplatz U.</p><p><u>Linie 1</u><br>Umleitung in beiden "
    "Richtungen<br>zwischen Karlsplatz U und Julius-Raab-Platz<br>über "
    "Schubertring und Stubenring.</p><p><u>Linie 2</u><br>Örtliche Umleitung "
    "zwischen Parlament, U Volkstheater und Rathaus U.<br>Alle Haltestellen "
    "werden eingehalten.</p><p><u>Linie 71 (ganztägig)</u><br>Betrieb nur "
    "zwischen Kaiserebersdorf und Schwarzenbergplatz.</p>"
)

_80A_HTML = (
    "<h2>Bauarbeiten</h2> <p>Wegen der Neuerrichtung der &Ouml;BB-Bahnviadukte "
    "im Bereich Helenengasse und Stoffellagasse treten bei der Linie 80A "
    "Ma&szlig;nahmen in Kraft. Beachten Sie bitte die unterschiedlichen "
    "Ma&szlig;nahmen werktags bzw. am Wochenende.</p> <p><span><strong>"
    "Zeitraum:</strong></span><br />Von Dienstag, 20. Oktober 2026 bis etwa "
    "September 2027.</p> <p><span><strong>Ma&szlig;nahmen:</strong></span><br />"
    "<strong>Montag-Freitag, Linie 80A in Richtung Schlachthausgasse U:</strong>"
    "<br />Umleitung ab Praterstern # Franzensbr&uuml;ckenstra&szlig;e &uuml;ber "
    "Praterstern - Hauptallee - Sportklubstra&szlig;e.</p> <p><strong>Samstag, "
    "Sonntag und Feiertag, Linie 80A:</strong><br />Betrieb nur zwischen "
    "Schlachthausgasse U und Franzensbr&uuml;cke.</p>"
)

_STRAMPFERGASSE_HTML = (
    "<p><span><strong><u>Haltestellenverlegung bzw. -auflassung der Linien 56A, "
    "56B und N62</u></strong></span></p><p><span><strong><u>Haltestelle:</u>"
    "</strong> Strampfergasse</span></p><p><span><strong><u>Fahrtrichtung:</u>"
    "</strong> Bhf. Atzgersdorf S bzw. Lainzer Tor</span></p><p><span><strong>"
    "<u>Von:</u></strong> Hermesstraße nach Strampfergasse</span></p><p><span>"
    "<strong><u>Nach:</u></strong> etwa 100 Meter in Richtung "
    "Dr.-Schober-Straße<br><br></span></p><p><span><strong><u>Fahrtrichtung:"
    "</u></strong> Hietzing U bzw. Oper, Karlsplatz U</span></p><p><span>"
    "<strong><u>Von:</u></strong> Hermesstraße nach Strampfergasse</span></p>"
    "<p><span><strong><u>Nach:</u></strong> aufgelassen<br><br></span></p>"
    "<p>&nbsp;</p><p><span><strong><u>Dauer:</u></strong> Ab 20. Oktober 2026, "
    "etwa 07:00 Uhr, für etwa eine Woche</span></p><p><span><strong><u>Grund:"
    "</u></strong> Straßenbau</span></p>"
)


def test_line_set_as_heading_leads_with_its_measure() -> None:
    body = _render("1/2/D/71: Veranstaltung", _SICHERHEITSFEST_HTML)
    assert "folgenden" not in body
    assert body.startswith(
        "Linie D: Umleitung in beiden Richtungen zwischen Schwarzenbergplatz und "
        "Börse über Schwedenplatz U; Linie 1: Umleitung"
    )


def test_label_with_days_and_direction_leads_and_request_goes() -> None:
    body = _render("80A: ÖBB-Bauarbeiten", _80A_HTML)
    assert "Maßnahmen in Kraft" not in body
    assert "Beachten Sie" not in body
    assert body.startswith(
        "Montag-Freitag, Linie 80A in Richtung Schlachthausgasse U: Umleitung ab "
        "Praterstern / Franzensbrückenstraße"
    )


def test_direction_with_abbreviation_is_part_of_the_label() -> None:
    html = (
        "<p>Wegen einer Veranstaltung kommt es zu folgenden Verkehrsmaßnahmen für "
        "die Linie 74A.</p><p><strong>Maßnahmen:</strong><br /><strong>Linie 74A "
        "in Richtung St. Marx S:</strong><br />Umleitung ab Weiskirchnerstraße über "
        "Am Stadtpark.</p>"
    )
    body = _render("74A: Veranstaltung", html)
    assert body.startswith("Linie 74A in Richtung St. Marx S: Umleitung ab Weiskirchnerstraße")


def test_a_ticker_that_opens_with_its_line_keeps_its_shape() -> None:
    # Only a label behind a sentence end or "Maßnahmen:" takes the colon;
    # a ticker "Linie U4 • Kein Betrieb …" is compared with its title as before.
    text = "Linie U4 • Kein Betrieb zwischen Hütteldorf und Ober St. Veit"
    assert _wl_measure_labels_with_colon(text) == text
    assert (
        _wl_measure_labels_with_colon("Maßnahmen: Linie 71 (ganztägig) • Betrieb nur")
        == "Maßnahmen: Linie 71 (ganztägig): Betrieb nur"
    )


def test_cut_does_not_leave_a_bare_line_label() -> None:
    text = (
        "Linie N8 in Richtung Alterlaa: Umleitung zwischen Aßmayergasse und "
        "Sonnergasse über Aßmayergasse - Niederhofstraße - Meidling Hauptstraße - "
        "Ruckergasse; Linie N8 in Richtung Nußdorfer Straße U: Umleitung zwischen "
        "Sonnergasse und Aßmayergasse."
    )
    assert _truncate_summary_180(text).endswith("Ruckergasse …")


def test_stop_moved_one_way_and_closed_the_other_names_both_directions() -> None:
    body = _render("56B/56A/N62: Strampfergasse", _STRAMPFERGASSE_HTML)
    assert body.startswith(
        "Richtung Bhf. Atzgersdorf S: Hermesstraße nach Strampfergasse → etwa 100 "
        "Meter in Richtung Dr.-Schober-Straße. Richtung Hietzing U: aufgelassen."
    )


# The day of the feed this module checks.
_TODAY = date(2026, 10, 24)


def test_oebb_date_list_drops_past_days() -> None:
    text = (
        "Wegen Bauarbeiten können • zwischen Wien Hbf (U) und Felixdorf Bahnhof • "
        "am 10.02.2026 • am 10.03.2026 • am 13.10.2026 und • am 10.11.2026 "
        "(jeweils 14:00 Uhr - 22:00 Uhr) keine R95-Züge fahren."
    )
    assert _oebb_upcoming_dates(text, _TODAY) == (
        "Wegen Bauarbeiten können • zwischen Wien Hbf (U) und Felixdorf Bahnhof • "
        "am 10.11.2026 (jeweils 14:00 Uhr - 22:00 Uhr) keine R95-Züge fahren."
    )


def test_oebb_date_list_reads_as_a_sentence() -> None:
    text = (
        "zwischen Deutsch Wagram Bahnhof und Gänserndorf Bahnhof • von 24.10.2026 "
        "bis 26.10.2026 • von 31.10.2026 bis 01.11.2026 • am 28.11.2026 und • "
        "zwischen Wien Leopoldau Bahnhst und Deutsch Wagram Bahnhof • am 25.10.2026 "
        "und 01.11.2026 • der S1-Zug 23626 nicht fahren."
    )
    # A running range (24.-26.10.) stays; "und" behind the list: commas only.
    assert _oebb_upcoming_dates(text, _TODAY).startswith(
        "zwischen Deutsch Wagram Bahnhof und Gänserndorf Bahnhof • von 24.10.2026 "
        "bis 26.10.2026, von 31.10.2026 bis 01.11.2026, am 28.11.2026 und • zwischen"
    )
    assert _oebb_upcoming_dates("am 31.10.2026, • am 07.11.2026 und • am 14.11.2026 keine", _TODAY) == (
        "am 31.10.2026, am 07.11.2026 und am 14.11.2026 keine"
    )


def test_oebb_dates_outside_a_list_stay() -> None:
    single = "von 23.10.2026 (23:00 Uhr) bis 27.10.2026 (04:00 Uhr) keine Fernverkehrszüge"
    assert _oebb_upcoming_dates(single, _TODAY) == single
    over = "am 10.02.2026 • am 10.03.2026 keine Züge"
    assert _oebb_upcoming_dates(over, _TODAY) == over
