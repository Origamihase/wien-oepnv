"""ÖBB's second sentence stays, also where ÖBB varies its first one.

Operator decision 2026-10-09 (#1993): ÖBB texts keep their second sentence;
the first leaves out the stations the title names only where the second
would not fit otherwise. The Feed-Wächter's rule ``oebb-zweiter-satz`` found
three texts since then that still lost it (back-test of 10.10.2026), and a
replay over all 563 ÖBB texts cached since June found 13:

* "… und Liesing (Wien) Zugfahrten voraussichtlich 23:00 Uhr nur
  eingeschränkt möglich." (no "bis"): the place was read from the template's
  slots, the time landed in the station slot, and the place stayed.
* "Planen Sie bis zu 15 Minuten mehr Reisezeit ein. Fahrgäste mit
  ÖBB-Tickets …": the summary's split wants four letters before a full stop
  and took the second and the third sentence as one, which did not fit.
* Planned works ("Wegen Bauarbeiten können zwischen A und B am … keine
  S45-Züge fahren."): the same sentence in another template.

Mutations checked against this file (each one caught, by the test named):

* the place is read from the template again → ``test_liesing_without_bis_keeps_its_second_sentence``.
* the second sentence is not split again → ``test_a_sentence_ending_in_ein_is_the_second_one``.
* a station ends at any lowercase word → ``test_a_place_the_title_names_only_in_part_stays``.
* the stations go although the second sentence fits with them → ``test_the_stations_stay_when_the_second_sentence_fits``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")

_OEBB = "ÖBB"
_VIENNA = ZoneInfo("Europe/Vienna")


def _summary(item: FeedItem, starts: datetime, ends: datetime | None = None) -> str:
    formatted = bf._format_item_content(item, "oebb", starts, ends)
    return formatted.desc_text_truncated


# ÖBB, 13.08.2026, 22:00 (cache/oebb_c40d21/events.json), courtesy and later
# sentences as published.
LIESING: FeedItem = {
    "source": _OEBB,
    "category": "Störung",
    "title": "Wien Meidling ↔ Wien Liesing",
    "description": (
        "13.08.2026<br/><br/>Wegen eines Schadens am Gleis sind zwischen <b>Wien Meidling "
        "Bahnhof (U) </b>und <b>Liesing (Wien) Zugfahrten voraussichtlich 23:00 Uhr nur "
        "eingeschränkt </b>möglich. Es kommt zu <b>vereinzelten Zugausfällen </b>und "
        "<b>Verspätungen</b> bis zu<b> 10 Minuten</b>.<br><b>Einzelne Züge</b> werden über "
        "Achau <b>umgeleitet</b>.<br>Beachten Sie bitte die Lautsprecherdurchsagen und "
        "Monitoranzeigen am Bahnsteig.<br>Wir bitten um Entschuldigung."
    ),
    "link": "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&",
    "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&904585",
}

# ÖBB, 09.10.2026 at 20:00, before the disruption was over.
LEOPOLDAU: FeedItem = {
    "source": _OEBB,
    "category": "Störung",
    "title": "Wien Leopoldau ↔ Wien Süßenbrunn",
    "description": (
        "09.10.2026<br/><br/>Wegen einer Stellwerkstörung am Bahnhof sind zwischen <b>Wien "
        "Leopoldau Bahnhst (U) </b>und <b>Wien Süßenbrunn Bahnhst Zugfahrten</b> bis "
        "voraussichtlich <b>20:30 Uhr nur eingeschränkt </b>möglich. Planen Sie bis zu<b> 15 "
        "Minuten </b>mehr Reisezeit ein. Fahrgäste mit ÖBB-Tickets können zwischen Wien "
        "Leopoldau Bahnhst (U) und Wien Süßenbrunn Bahnhst die Wiener Linien benutzen.<br>Wir "
        "bitten um Entschuldigung."
    ),
    "link": "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&",
    "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&918244",
}

# ÖBB, planned works announced for 01.11.2026 (shortened after the second
# sentence; the rest does not reach the summary).
S45: FeedItem = {
    "source": _OEBB,
    "category": "Störung",
    "title": "S 45: Wien Hütteldorf ↔ Wien Handelskai",
    "description": (
        "01.11.2026<br/><br/>Wegen Bauarbeiten können<br>zwischen <b>Wien Hütteldorf Bahnhof "
        "(U)</b> und <b>Wien Handelskai Bahnhst (U)</b><br>am <b>01<span>.11.2026</span></b>"
        "<span> (von 01:10 Uhr bis 04:10 Uhr) </span>keine S45-Züge fahren.<br>Ein "
        "Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet.<br><br>Wir bitten um "
        "Entschuldigung."
    ),
    "link": "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&",
    "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&904162",
}


def test_liesing_without_bis_keeps_its_second_sentence() -> None:
    summary = _summary(LIESING, datetime(2026, 8, 13, 23, 2, tzinfo=_VIENNA))
    # Was: "Wegen … sind zwischen Wien Meidling Bahnhof (U) und Liesing (Wien)
    # Zugfahrten voraussichtlich 23:00 Uhr nur eingeschränkt möglich." alone.
    assert summary.startswith(
        "Wegen eines Schadens am Gleis sind Zugfahrten voraussichtlich 23:00 Uhr nur "
        "eingeschränkt möglich. Es kommt zu vereinzelten Zugausfällen und Verspätungen bis "
        "zu 10 Minuten."
    )


def test_a_sentence_ending_in_ein_is_the_second_one() -> None:
    summary = _summary(
        LEOPOLDAU,
        datetime(2026, 10, 9, 0, 0, tzinfo=_VIENNA),
        datetime(2026, 10, 9, 23, 59, 59, tzinfo=_VIENNA),
    )
    assert summary.startswith(
        "Wegen einer Stellwerkstörung am Bahnhof sind Zugfahrten bis voraussichtlich 20:30 Uhr "
        "nur eingeschränkt möglich. Planen Sie bis zu 15 Minuten mehr Reisezeit ein."
    )
    assert "Fahrgäste" not in summary


def test_planned_works_keep_the_replacement_bus() -> None:
    summary = _summary(
        S45,
        datetime(2026, 11, 1, 0, 0, tzinfo=_VIENNA),
        datetime(2026, 11, 1, 23, 59, 59, tzinfo=_VIENNA),
    )
    assert summary.startswith(
        "Wegen Bauarbeiten können am 01.11.2026 (von 01:10 Uhr bis 04:10 Uhr) keine S45-Züge "
        "fahren. Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
    )


def test_the_stations_stay_when_the_second_sentence_fits() -> None:
    first = (
        "Wegen eines Rettungseinsatzes sind zwischen Wien Meidling Bahnhof (U) und Wien Hbf (U) "
        "bis voraussichtlich 22:00 Uhr keine Fahrten möglich."
    )
    rest = (
        "Die Züge warten die Sperre vorerst ab. Fahrgäste mit ÖBB-Tickets können die Wiener "
        "Linien benutzen."
    )
    assert bf._oebb_first_two_sentences(first, rest, "Wien Hauptbahnhof ↔ Wien Meidling") == (
        f"{first} Die Züge warten die Sperre vorerst ab."
    )


@pytest.mark.parametrize(
    ("sentence", "title", "shorter"),
    [
        (
            "Wegen Bauarbeiten können zwischen Wien Floridsdorf Bahnhof (U) und Wien Praterstern "
            "Bahnhof (U) von 12.09.2026 bis 13.09.2026 einige Nahverkehrszüge nicht fahren.",
            "Wien Floridsdorf ↔ Wien Praterstern",
            "Wegen Bauarbeiten können von 12.09.2026 bis 13.09.2026 einige Nahverkehrszüge nicht "
            "fahren.",
        ),
        (
            "Wegen Bauarbeiten werden zwischen St. Pölten Hbf und Wien Hütteldorf Bahnhof (U) am "
            "25.07.2026 die Züge 1943 und 1945 umgeleitet.",
            "Wien Hütteldorf ↔ St. Pölten Hauptbahnhof",
            "Wegen Bauarbeiten werden am 25.07.2026 die Züge 1943 und 1945 umgeleitet.",
        ),
        (
            "Wegen einer Weichenstörung sind zwischen Wien Meidling und Wien Hbf bis 20:00 Uhr "
            "keine Fahrten möglich.",
            "Wien Meidling ↔ Wien Hauptbahnhof",
            "Wegen einer Weichenstörung sind bis 20:00 Uhr keine Fahrten möglich.",
        ),
    ],
)
def test_the_place_after_the_verb_leaves(sentence: str, title: str, shorter: str) -> None:
    assert bf._oebb_sentence_without_place(sentence, title) == shorter


@pytest.mark.parametrize(
    ("sentence", "title"),
    [
        # "Brunn am Gebirge" is not "Brunn am Gebirge-Maria Enzersdorf"
        (
            "Wegen eines Polizeieinsatzes waren zwischen Liesing (Wien) und Brunn am "
            "Gebirge-Maria Enzersdorf Bahnhof bis 12:48 Uhr keine Fahrten möglich.",
            "Wien Liesing ↔ Brunn am Gebirge",
        ),
        # nor "Hennersdorf" "Hennersdorf b.Wien"
        (
            "Wegen eines Rettungseinsatzes waren zwischen Wien Meidling Bahnhof (U) und "
            "Hennersdorf b.Wien Bahnhst bis 13:59 Uhr nur eingeschränkt Fahrten möglich.",
            "Wien Meidling ↔ Hennersdorf",
        ),
        # nor "Wien Hauptbahnhof" "Wien Hbf (Südtiroler Platz)"
        (
            "Wegen Verlängerung der Bauarbeiten sind zwischen Wien Meidling Bahnhof (U) und Wien "
            "Hbf (Südtiroler Platz) Zugfahrten bis voraussichtlich 08:30 Uhr nur eingeschränkt "
            "möglich.",
            "Wien Meidling ↔ Wien Hauptbahnhof",
        ),
        # one place of a list
        (
            "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Götzendorf Bahnhof, zwischen Wien "
            "Hbf (U) und Gramatneusiedl Bahnhof einige Nahverkehrszüge nicht fahren.",
            "Wien Hauptbahnhof ↔ Götzendorf",
        ),
        # a place in the cause, before the verb
        (
            "Wegen eines Polizeieinsatzes in Wien Meidling Bahnhof sind derzeit keine Fahrten "
            "möglich.",
            "Wien Meidling",
        ),
        # no "Wegen …" sentence
        (
            "Eine Information für Fahrgäste zwischen Wien Meidling und Wien Floridsdorf.",
            "Wien Meidling ↔ Wien Floridsdorf",
        ),
    ],
)
def test_a_place_the_title_names_only_in_part_stays(sentence: str, title: str) -> None:
    assert bf._oebb_sentence_without_place(sentence, title) is None


# ---------------- EN ----------------


@pytest.fixture
def cause_stub(monkeypatch: Any) -> list[str]:
    """The model as far as ÖBB templates need it: only the cause reaches it."""
    seen: list[str] = []

    def model(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        for german, english in (("Wegen eines ", "Due to a "), ("Wegen ", "Due to ")):
            if text.startswith(german):
                return [{"translation_text": english + text[len(german):]}]
        return [{"translation_text": text}]

    monkeypatch.setattr(bf, "_get_translation_pipeline", lambda: model)
    return seen


def test_planned_works_without_a_place_render(cause_stub: list[str]) -> None:
    out = bf._translate_text_attempt(
        "Wegen Bauarbeiten können von 02.11.2026 bis 03.11.2026 keine R 40-Züge fahren.",
        source=_OEBB,
    )
    assert out is not None
    assert out.endswith("no R 40 trains can run from 02.11.2026 to 03.11.2026.")
    assert len(cause_stub) == 1
