"""A relocation with two destinations keeps both in the 180 characters.

Live 27.–29.10.2026 (Jules audits 28./29.10.): "40A: Bauernfeldplatz" read
"Richtung Döblinger Friedhof, Felix-Dahn-Straße: Liechtensteinstraße 42 →
Liechtensteinstraße 38. Richtung Schottentor U: Liechtensteinstraße 37a
gegenüber Bauernfeldplatz → …": the new stop towards Schottentor U was cut.
Operator decision 2026-10-29, "Von weglassen, wenn zu lang": when a
destination would fall behind the cut, the old places go.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")

START = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)


def _field(label: str, value: str) -> str:
    return f"<p><strong><u>{label}</u></strong> {value}</p>"


def _short_text(title: str, description: str) -> str:
    item: FeedItem = {
        "source": "Wiener Linien",
        "category": "Hinweis",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": "g",
        "pubDate": START,
        "starts_at": START,
        "ends_at": None,
    }
    return bf._format_item_content(item, "g", START, None, lang="de").desc_text_truncated


NOTICE_40A = (
    "<p><strong><u>Haltestellenverlegung der Linie 40A in beiden Richtungen</u></strong></p>"
    + _field("Haltestelle:", "Bauernfeldplatz")
    + _field("Fahrtrichtung:", "Döblinger Friedhof, Felix-Dahn-Straße")
    + _field("Von:", "Liechtensteinstraße 42")
    + _field("Nach:", "Liechtensteinstraße 38")
    + _field("Fahrtrichtung:", "Schottentor U")
    + _field("Von:", "Liechtensteinstraße 37a gegenüber Bauernfeldplatz")
    + _field("Nach:", "Liechtensteinstraße 39")
    + _field("Dauer:", "Ab 27. Oktober 2026, etwa 07:00 Uhr, Dauer unbekannt")
    + _field("Grund:", "Kabelarbeiten")
)
NOTICE_17A = (
    "<p><strong><u>Haltestellenverlegung der Linie 17A in Richtung Inzersdorf, Birostraße</u></strong></p>"
    + _field("Haltestelle:", "Josef-Enslein-Platz")
    + _field("Von:", "Per-Albin-Hansson-Straße vor Bullgasse")
    + _field("Nach:", "Per-Albin-Hansson-Straße 130")
    + "<p><strong><u>Haltestellenverlegung der Linie 17A in Richtung Oberlaa U</u></strong></p>"
    + _field("Haltestelle:", "Josef-Enslein-Platz")
    + _field("Von:", "Per-Albin-Hansson-Straße nach Bullgasse")
    + _field("Nach:", "Per-Albin-Hansson-Straße 24")
    + _field("Grund:", "Straßenbauarbeiten")
)


def test_both_directions_of_40a_show_their_new_stop() -> None:
    text = _short_text("40A: Bauernfeldplatz", NOTICE_40A)
    assert text.startswith(
        "Richtung Döblinger Friedhof, Felix-Dahn-Straße: → Liechtensteinstraße 38. "
        "Richtung Schottentor U: → Liechtensteinstraße 39."
    )


def test_each_destination_stays_in_front_of_its_own_direction() -> None:
    text = _short_text("17A: Josef-Enslein-Platz", NOTICE_17A)
    assert text.startswith(
        "→ Per-Albin-Hansson-Straße 130. Haltestellenverlegung der Linie 17A in Richtung "
        "Inzersdorf, Birostraße. → Per-Albin-Hansson-Straße 24."
    )


def test_a_relocation_that_fits_keeps_its_old_place() -> None:
    notice = (
        "<p><strong><u>Haltestellenverlegung der Linie 3A in Richtung Schottenring U</u></strong></p>"
        + _field("Haltestelle:", "Oper, Karlsplatz")
        + _field("Von:", "Kärntner Ring, Nebenfahrbahn vor Kärntner Straße")
        + _field("Nach:", "Kärntner Ring 5, Nebenfahrbahn")
    )
    text = _short_text("3A: Oper, Karlsplatz", notice)
    assert text.startswith("Kärntner Ring, Nebenfahrbahn vor Kärntner Straße → Kärntner Ring 5, Nebenfahrbahn.")
