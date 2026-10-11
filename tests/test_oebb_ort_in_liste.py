"""ÖBB's first sentence keeps a place that a list continues or another route follows.

Since #2005 the first sentence leaves out the stations the title names when
ÖBB's second sentence would not fit otherwise (operator decision
2026-10-09). The cut must leave a sentence that still reads right. A replay
over all 563 ÖBB texts cached since June (Gesamtprüfung 11.10.2026,
reports/gesamtpruefung-2026-10-11-proben/) found four sentence shapes where
it did not; none reached the display, the closest came within ten
characters of it:

* "zwischen A und B bzw. C und D, bis …" → "sind bzw. Wien Hbf (U) und
  Flughafen Wien Bahnhof, bis …" (Geiselbergstraße ↔ Flughafen, 07.09.2026);
* "in A bzw B …" → "sind bzw Wien Meidling Zugfahrten …";
* "in A und B die S4-Züge …" → "bis 30.11.2026 (23:00 Uhr) und Föderlach
  Bahnhof die S4-Züge …";
* "zwischen A und B der Zug REX6 (7799) zwischen C und D der Zug S60 …" →
  the REX 6 got the S60's route.

Mutations checked against this file (each one caught):

* no list check behind the station → ``test_a_place_in_a_list_stays`` (bzw., und);
* no check for a second route → ``test_a_place_in_a_list_stays`` (REX6);
* both removed → ``test_the_display_never_reads_a_broken_list``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")

_VIENNA = ZoneInfo("Europe/Vienna")


@pytest.mark.parametrize(
    ("sentence", "title"),
    [
        (
            "Wegen Verlängerung der Bauarbeiten sind zwischen Wien Geiselbergstraße Bahnhst und "
            "Flughafen Wien Bahnhof bzw. Wien Hbf (U) und Flughafen Wien Bahnhof, bis "
            "voraussichtlich 07:00 Uhr keine Fahrten möglich.",
            "Wien Geiselbergstraße ↔ Flughafen Wien",
        ),
        (
            "Wegen einer Stellwerkstörung am Bahnhof sind in Wien Hbf (U) bzw Wien Meidling "
            "Zugfahrten erneut nur eingeschränkt möglich.",
            "Wien Hauptbahnhof",
        ),
        (
            "Wegen Bauarbeiten können von 23.11.2026 (08:00 Uhr) bis 30.11.2026 (23:00 Uhr) in "
            "Lind-Rosegg Bahnhst und Föderlach Bahnhof die S4-Züge 4205 und 4209 nicht halten.",
            "S4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach",
        ),
        (
            "Wegen Bauarbeiten kann am 02.08.2026 zwischen Wien Meidling Bahnhof (U) und "
            "Gramatneusiedl Bahnhof der Zug REX6 (7799) zwischen Wien Hbf und Kledering der Zug "
            "S60 (26093) nicht fahren.",
            "Wien Meidling ↔ Gramatneusiedl / Wien Hauptbahnhof ↔ Kledering",
        ),
    ],
)
def test_a_place_in_a_list_stays(sentence: str, title: str) -> None:
    assert bf._oebb_sentence_without_place(sentence, title) is None


def test_a_place_the_list_does_not_continue_still_leaves() -> None:
    # "bzw." between two other words is no list of places.
    sentence = (
        "Wegen Bauarbeiten fahren zwischen Wien Meidling Bahnhof (U) und Wien Hbf (U) von "
        "10.07.2026 bis 27.07.2026 die IC-Züge mit geänderten Fahrzeiten bzw. vorverlegten "
        "Abfahrtszeiten."
    )
    assert bf._oebb_sentence_without_place(sentence, "Wien Meidling ↔ Wien Hauptbahnhof") == (
        "Wegen Bauarbeiten fahren von 10.07.2026 bis 27.07.2026 die IC-Züge mit geänderten "
        "Fahrzeiten bzw. vorverlegten Abfahrtszeiten."
    )


# ÖBB, 07.09.2026 03:31 Vienna time (cache/oebb_c40d21/events.json), with a
# second sentence of 30 characters instead of ÖBB's 51: then the cut fits.
GEISELBERG: FeedItem = {
    "source": "ÖBB",
    "category": "Störung",
    "title": "Wien Geiselbergstraße ↔ Flughafen Wien",
    "description": (
        "07.09.2026<br/><br/>Wegen Verlängerung der Bauarbeiten sind zwischen <b>Wien "
        "Geiselbergstraße Bahnhst</b> und <b>Flughafen Wien Bahnhof</b> bzw. <b>Wien Hbf (U)</b> "
        "und <b>Flughafen Wien Bahnhof</b>, bis voraussichtlich <b>07:00 Uhr</b> keine Fahrten "
        "möglich.<br>Die Züge warten die Sperre ab.<br>Wir bitten um Entschuldigung."
    ),
    "link": "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&",
    "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&909865",
}


def test_the_display_never_reads_a_broken_list() -> None:
    starts = datetime(2026, 9, 7, 3, 31, tzinfo=_VIENNA)
    summary = bf._format_item_content(GEISELBERG, "oebb", starts, None).desc_text_truncated
    assert "sind bzw." not in summary
    assert summary.startswith(
        "Wegen Verlängerung der Bauarbeiten sind zwischen Wien Geiselbergstraße Bahnhst und "
        "Flughafen Wien Bahnhof bzw. Wien Hbf (U)"
    )
