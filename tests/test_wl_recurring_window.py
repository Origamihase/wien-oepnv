"""A WL notice limited to some days or hours says so in its text.

The time line under a notice holds a start and an end. "Dauer:" and
"Zeitraum:" blocks fall away because it names the dates, but the stop
closure for the Christkindlmarkt 2026 ("an Freitagen, Samstagen, Sonntagen
sowie am 08. Dezember jeweils von etwa 15:00 Uhr bis 22:30 Uhr") read
"aufgelassen … [Bis 20.12.]", a closure on every day, and "63A: Umleitung
wegen Kranarbeiten" ("täglich von 20:00 Uhr bis 05:00 Uhr") a diversion
around the clock. The window now closes the lead sentence.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import src.build_feed  # noqa: F401  (loads the feed modules in their order)
from src.feed_types import FeedItem
from src.feed_build.item_format import _format_item_content
from src.feed_build.summary_text import _recurring_window

_RATHAUSPLATZ = (
    "<p><strong><u>Haltestellenauflassung der Linien D, 1 und 71 in beiden Richtungen</u></strong></p>"
    "<p><strong><u>Haltestelle:</u></strong> Rathausplatz, Burgtheater</p>"
    "<p><strong><u>Dauer:</u></strong> <br>13. November 2026 bis 20. Dezember 2026<br>an Freitagen, "
    "Samstagen, Sonntagen sowie am 08. Dezember<br>jeweils von etwa 15:00 Uhr bis 22:30 Uhr</p>"
    "<p><strong><u>Grund:</u></strong> Christkindlmarkt</p><p><strong><u>Ersatz:</u></strong> </p>"
    "<ul><li><p>Schottentor U</p></li><li><p>Parlament, U Volkstheater</p></li></ul><p></p>"
)
_KRANARBEITEN = (
    "<h2>Kranarbeiten</h2> <p>Wegen Kranarbeiten im Bereich Kerschensteinergasse zwischen "
    "Kundratstra&szlig;e und L&auml;ngenfeldgasse wird die Linie 63A umgeleitet.</p> <p><strong>"
    "Zeitraum:</strong><br />Von Montag, 05. Oktober 2026, auf Dauer von etwa vier Wochen, "
    "t&auml;glich von 20:00 Uhr bis 05:00 Uhr.</p> <p><strong>Ma&szlig;nahmen:<br /></strong>"
    "<strong>Linie 63A in Richtung Am Rosenh&uuml;gel:<br /></strong>Umleitung ab "
    "Eibesbrunnergasse # Unter-Meidlinger-Stra&szlig;e &uuml;ber Eibesbrunnergasse &ndash; "
    "Kerschensteinergasse &ndash; L&auml;ngenfeldgasse zur Stammstrecke.</p>"
)


def _summary(title: str, description: str, start: str, end: str) -> str:
    item: FeedItem = {
        "source": "Wiener Linien",
        "category": "Hinweis",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": "g",
    }
    content = _format_item_content(
        item, "g", datetime.fromisoformat(start), datetime.fromisoformat(end)
    )
    return content.desc_text_truncated


def test_stop_closure_names_its_days_and_hours() -> None:
    text = _summary(
        "1/71/D: Rathausplatz, Burgtheater", _RATHAUSPLATZ,
        "2026-11-13T00:00:00+01:00", "2026-12-20T22:30:00+01:00",
    )
    assert text.startswith(
        "Haltestellenauflassung der Linien D, 1 und 71 in beiden Richtungen, an Freitagen, "
        "Samstagen, Sonntagen sowie am 08. Dezember jeweils von etwa 15:00 Uhr bis 22:30 Uhr."
    )
    assert "13. November" not in text


def test_nightly_diversion_names_its_hours() -> None:
    text = _summary(
        "63A: Umleitung wegen Kranarbeiten", _KRANARBEITEN,
        "2026-10-05T00:00:00+02:00", "2026-11-11T23:59:00+01:00",
    )
    assert text.startswith(
        "Wegen Kranarbeiten im Bereich Kerschensteinergasse zwischen Kundratstraße und "
        "Längenfeldgasse wird die Linie 63A umgeleitet, täglich von 20:00 Uhr bis 05:00 Uhr."
    )
    assert "Oktober" not in text


@pytest.mark.parametrize(
    ("field", "window"),
    [
        ("Dauer: Ab 16. November 2026 etwa 08:00 Uhr, Dauer unbekannt", ""),
        ("Zeitraum: Von Montag, 05. Oktober 2026 bis Freitag, 09. Oktober 2026", ""),
        ("Zeitraum: Ab Mittwoch, 2. September 2026, Betriebsbeginn, bis voraussichtlich Ende April 2027.", ""),
        ("Dauer: 07. Dezember 2026 bis 11. Dezember 2026, Montag bis Freitag, 09:00 bis 15:00 Uhr",
         "Montag bis Freitag, 09:00 bis 15:00 Uhr"),
        ("Zeitraum: Ab 01. Dezember 2026 an Wochenenden", "an Wochenenden"),
        ("Zeitraum: 10. bis 20. Dezember 2026 jeweils nachts", "jeweils nachts"),
    ],
)
def test_only_days_or_hours_count_as_window(field: str, window: str) -> None:
    assert _recurring_window(field) == window
