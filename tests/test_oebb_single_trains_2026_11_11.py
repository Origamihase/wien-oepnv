"""ÖBB notices about single trains stay out of the feed.

Operator decision 2026-11-11, Michael: "Einzelzüge sollen nicht in den Feed."
(with his card answer "Nein, nur echte Sperren", independent check
2026-11-11). A notice whose lead sentence, the sentence the feed shows
first, names its trains by number or as "einzelne" trains, and in which no
sentence says that no trains run or stop, leaves the feed at build time
(``_post_filter_oebb``); it stays in the ÖBB cache. "Einige" trains without
numbers stay: the section runs fewer trains. Every fixture below is ÖBB's
text from ``cache/oebb_c40d21/events.json`` unless it says otherwise.

Of the 688 distinct ÖBB texts from 01.05. to 11.11.2026 that pass today's
Wien filter (cache, and today's fetch over the raw RSS since 04.10.), the rule
takes 22 out, versions of 13 notices, every one with dates: S1 23626 (four
versions), RJ 263 Hütteldorf ↔ Hbf (three), S2 23360, RJ 820, the IC and D
trains Westbahnhof ↔ Hütteldorf of 03. to 08.06. and the REX 6/S 60 trains
Hbf ↔ Gramatneusiedl of 03. to 05.10. (two each), REX 1 (2334, 2338 und
2342), D 347, four trains St. Pölten ↔ Hütteldorf (25./26.07.), eleven S-Bahn
trains Liesing ↔ Meidling (02.08.), REX6 7799 with S60 26093 (02.08.), S60
26093 (06.09.) and RJ 12265. The 24 texts about "einige" trains stay, among
them Wien Hbf ↔ Gramatneusiedl/Götzendorf/Bruck an der Leitha from 08.03. to
22.06.2026; the last of them left the cache on 31.08.

A single-train lead in a notice that closes the line elsewhere stays in the
feed; its rank is the normal one (``test_the_single_train_keeps_its_normal_place``
in ``tests/test_oebb_closure_lines_2026_11_11.py``).

Mutations checked against this file (each one caught, by the test named):

* no closure check → ``test_a_closure_keeps_its_train_list``.
* ÖBB's first sentence instead of the shown one →
  ``test_the_shown_lead_decides`` (both directions).
* "einzelne" not counted → ``test_names_single_trains``.
* "einige" counted → ``test_names_no_single_trains`` and
  ``test_some_trains_stay_in_the_feed``.
* a train number in brackets only before ")" ("REX 1 (2334, 2338 und
  2342)") → ``test_names_single_trains``.
* two-digit regional numbers counted as trains (the line "REX 63") →
  ``test_names_no_single_trains``.
* no "Zü ge", no "Zug/Bus", no long-distance trains of two digits, no
  "RJX-Zug 62" → ``test_names_single_trains``.
* the source check removed → ``test_only_oebb_notices_count``.
* the call in ``_post_filter_oebb`` removed →
  ``test_the_feed_reads_the_cache_without_single_trains``.
"""

from __future__ import annotations

from typing import Any

import pytest

import src.feed_build.providers_setup as providers_setup
from src.build_feed import _post_filter_oebb
from src.feed_build.oebb_lead import _oebb_shown_sentences
from src.feed_build.oebb_single_trains import _oebb_names_single_trains, _oebb_single_trains_only

# 11.11.2026.
S1 = (
    "24.10.2026 - 28.11.2026<br/><br/>Wegen Bauarbeiten kann zwischen <b>Deutsch Wagram Bahnhof</b> und "
    "<b>Gänserndorf Bahnhof</b> von <b>2</b> 4.10.2026 bis 26.10.2026 von 31.10.2026 bis 01.11.2026 von "
    "14.11.2026 bis 15.11.2026 von 21.11.2026 bis 22.11.2026 am 28.11.2026 und zwischen <b>Wien Leopoldau "
    "Bahnhst</b> und <b>Deutsch Wagram Bahnhof</b> am 25.10.2026 und 01.11.2026 der S1-Zug 23626 nicht "
    "fahren.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)
# 11.11.2026.
RJ_12265 = (
    "19.03.2027 - 30.03.2027<br/><br/>Wegen Bauarbeiten wird von <b>19.03.2027 </b>bis<b> 30.03.2027</b> der "
    "Zug RJ 12265 umgeleitet. Der Halt in Wien Hütteldorf entfällt. Aufgrund der Bauarbeiten kann es teilweise "
    "zu Einschränkungen bei der Buchung von Sitzplatzreservierungen kommen.<br>Wir bitten um Entschuldigung."
)
# 05.10.2026.
GRAMATNEUSIEDL_TRAINS = (
    "03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf (U)</b> und "
    "<b>Gramatneusiedl Bahnhof </b><br>von <b>03.10.2026</b> bis <b>04.10.2026 </b><br>der Zug REX64(2679)"
    "<br>von <b>03.</b> auf <b>04.10.2026</b> bis <b>04.</b> auf <b>05.10.2026</b><br>die Züge REX6(7777), "
    "S60(26085), S60(26086) und REX6(7776)<br>von<b> 04.10.2026</b> bis <b>05.10.2026</b><br>der Zug "
    "S60(7781)<br>am <b>03.10.2026</b><br>der Zug REX63(7974)<br>am <b>04.10.2026</b><br>die Züge S60(26093) "
    "und REX64(2674) nicht fahren.<br>Von <b>04.10.2026</b> bis <b>05.10.2026</b> kann der Zug REX6(7799) "
    "zwischen <b>Wien Meidling Bahnhof</b> nach <b>Gramatneusiedl Bahnhof</b> nicht fahren.<br>Ein "
    "Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)
# 31.08.2026.
FLORIDSDORF_SOME = (
    "12.09.2026 - 13.09.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Floridsdorf Bahnhof (U)</b> "
    "und <b>Wien Praterstern Bahnhof (U)</b><br>von <b>12.09.2026</b> bis <b>13.09.2026 </b><br>einige "
    "Nahverkehrszüge nicht fahren.<br>Als Ersatz haben Sie die Möglichkeit den nächsten fahrplanmäßigen Zug zu "
    "nehmen. <br><br>Wir bitten um Entschuldigung."
)
# 10.11.2026, the train lists shortened.
REX50 = (
    "02.11.2026 - 11.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>St.Pölten Hbf</b> und <b>Wien "
    "Westbahnhof (U)</b><br>von <b>03.11.2026</b> bis <b>10.11.2026 </b><br>keine REX50-Züge fahren.<br>"
    "Zwischen <b> Wien Westbahnhof (U) </b>und <b>Wien Hütteldorf Bahnhof (U) </b>können<br>von "
    "<b>03.11.2026</b> bis <b>10.11.2026</b><br>die REX51-Züge 1604, 1608, 1614 und 1657 <br>von "
    "<b>02.11.2026</b> bis <b>10.11.2026</b><br>die S50-Züge 20063, 20161 und 20078<br>nicht fahren.<br>Als "
    "Ersatz haben Sie die Möglichkeit den nächsten fahrplanmäßigen Zug zu nehmen."
)
# 01.11.2026.
HBF_WESTBAHNHOF = (
    "11.09.2026 - 01.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf (U)</b> und <b>Wien "
    "Westbahnhof (U)</b><br>von <b>23.10.2026 </b>(23:00 Uhr) bis <b>27.10.2026 </b>(04:00 Uhr)<b> </b><br>"
    "keine Fernverkehrszüge fahren.<br>Als Ersatz haben Sie die Möglichkeit den nächsten fahrplanmäßigen Zug zu "
    "nehmen.<br><br><b>HALTAUSFALL:<br></b>Von <b>11.09.2026</b> bis <b>09.10.2026</b>,<br>am "
    "<b>16.10.2026</b> und<br>von <b>23.10.2026</b> bis <b>01.11.2026</b><br>kann der Zug RJ 263 in <b>Wien "
    "Hütteldorf Bahnhof (U)</b> nicht halten."
)
# 27.08.2026, an unplanned disruption.
GRAMATNEUSIEDL_INCIDENT = (
    "26.08.2026 - 29.08.2026<br/><br/>Wegen Reparaturarbeiten nach einem Unfall sind zwischen <b>Wien Hbf (U) "
    "</b>und <b>Gramatneusiedl Bahnhof Zugfahrten derzeit nur eingeschränkt</b> möglich. Für die Züge REX 63 "
    "und REX 64 haben wir für Sie einen Autobus zw. Wien Hbf (U) bis Gramatneusiedl Bahnhof organisiert. Planen "
    "Sie bis zu <b>60 Minuten </b>mehr Reisezeit ein."
)
# ÖBB TRACKINFO 901477 as published 2026-10-17 (tests/test_oebb_titelstrecke_zuerst.py):
# ÖBB's first sentence is about Salzburg – Flughafen Wien, the feed shows the
# Vienna section under the title first.
HUETTELDORF = (
    "17.10.2026 - 21.10.2026<br/><br/>Wegen Bauarbeiten fahren<br>zwischen <b>Salzburg Hbf</b> "
    "und <b>Flughafen Wien Bahnhof </b>bzw.<b> Hegyeshalom<br></b>von <b>10.10.2026</b> "
    "(22:30 Uhr) bis <b>14.10.2026</b> (04:00 Uhr) und<br>von <b>17.10.2026 </b>(22:30 Uhr) "
    "bis <b>21.10.2026 </b>(04:00 Uhr)<b><br></b>einige Fernverkehrszüge mit geänderten "
    "Fahrzeiten.<br><br><b>TEILAUSFALL:</b><br>Von <b>18.10.2026</b> bis <b>19.10.2026</b> "
    "kann der Zug RJ 263<br>zwischen <b>Wien Hütteldorf Bahnhof (U) </b>und <b>Wien Hbf (U) "
    "</b>nicht fahren.<br><br><b>HALTAUSFALL:<br></b>Von <b>11.10.2026</b> bis "
    "<b>12.10.2026</b> kann der Zug RJ 263<br>in <b>Wien Hütteldorf Bahnhof (U) </b>nicht "
    "halten.<br><br>Ihre Reisezeit verlängert sich um bis zu 8 Minuten."
)
# Constructed from it: ÖBB's first sentence speaks of all long-distance trains,
# the shown Vienna section still of RJ 263 …
HUETTELDORF_FIRST_ALL = HUETTELDORF.replace("einige Fernverkehrszüge", "alle Fernverkehrszüge")
# … and the other way round: ÖBB's first sentence names a train, the shown
# Vienna section speaks of all of them.
HUETTELDORF_SHOWN_ALL = HUETTELDORF.replace(
    "Wegen Bauarbeiten fahren<br>", "Wegen Bauarbeiten fährt<br>").replace(
    "einige Fernverkehrszüge mit", "der Zug RJ 160 mit").replace(
    "kann der Zug RJ 263<br>zwischen <b>Wien Hütteldorf Bahnhof (U) </b>und <b>Wien Hbf (U) </b>nicht fahren.",
    "fahren<br>zwischen <b>Wien Hütteldorf Bahnhof (U) </b>und <b>Wien Hbf (U) </b>alle Fernverkehrszüge mit "
    "geänderten Fahrzeiten.",
)


def _oebb(title: str, description: str) -> dict[str, Any]:
    return {
        "source": "ÖBB", "category": "Störung", "title": title, "description": description,
        "guid": f"oebb|{title}", "link": "https://fahrplan.oebb.at/",
    }


@pytest.mark.parametrize(
    "sentence",
    [
        "Wegen Bauarbeiten kann zwischen Wien Leopoldau Bahnhst (U) und Mistelbach/Zaya Bahnhof am 25.10.2026 und "
        "01.11.2026 der S2-Zug 23360 nicht fahren.",
        "TEILAUSFALL: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263 zwischen Wien Hütteldorf Bahnhof (U) und "
        "Wien Hbf (U) nicht fahren.",
        "Wegen Bauarbeiten wird von 19.03.2027 bis 30.03.2027 der Zug RJ 12265 umgeleitet.",
        "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof am 04.10.2026 die Züge "
        "S60(26093) und REX64(2674) nicht fahren.",
        "Zwischen Wien Hbf (U) und Wien Stadlau Bahnhst können die S80-Züge 2590, 25080 und 25091 nicht fahren.",
        "Der REX 41-Zug 2157 kann zwischen Tulln/Donau Bahnhof und Wien Franz-Josefs-Bahnhof nicht fahren.",
        "Wegen Bauarbeiten werden von 04.01.2027 bis 17.01.2027 die NJ-Züge 446, 466 und 40462 umgeleitet.",
        "Wegen Bauarbeiten können am 26.11.2026 die S3- Züge 7563 und 7565 nicht fahren.",
        "Wegen Bauarbeiten können am 12.11.2026 die S4-Zü ge 8977 und 8981 nicht fahren.",
        "Wegen Bauarbeiten kann am 23.10.2026 der Zug R 55 (6734) nicht fahren.",
        "Wegen Bauarbeiten fährt am 15.11.2026 der Zug S1 (4000) mit geänderten Fahrzeiten.",
        "ACHTUNG: Der Zug/Bus REX(7603) fährt zwischen Deutschkreuz und Ebreichsdorf früher ab.",
        "Wegen Bauarbeiten können am 18.01.2027 die Züge 3802, 3803, 3804 und 3805 nicht fahren.",
        "Wegen Bauarbeiten fährt am 15.11.2026 der Zug REX 1826 nur bis Brennero/Brenner.",
        "Wegen Bauarbeiten kann am 07.10.2026 der Zug RJX 62 nicht fahren.",
        "Wegen Bauarbeiten kann am 07.10.2026 der RJX-Zug 62 nicht fahren.",
        # Cache 05.05.2026.
        "Wegen Bauarbeiten können zwischen Gänserndorf Bahnhof und Bernhardsthal Bahnhof von 09.03.2026 bis "
        "03.07.2026 die Züge REX 1 (2334, 2338 und 2342) nicht fahren.",
        "Wegen Bauarbeiten können am 21.10.2026 einzelne Züge nicht halten.",
        "Wegen Bauarbeiten können am 21.10.2026 einzelne Nahverkehrszüge nicht fahren.",
        "Wegen Bauarbeiten können am 21.10.2026 einzelne S50-Züge nicht fahren.",
    ],
)
def test_names_single_trains(sentence: str) -> None:
    assert _oebb_names_single_trains(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Wegen Bauarbeiten können zwischen St.Pölten Hbf und Wien Westbahnhof (U) von 03.11.2026 bis 10.11.2026 "
        "keine REX50-Züge fahren.",
        "Von 31.10.2026 (22:00 Uhr) bis 03.11.2026 können zwischen Wien Franz-Josefs-Bahnhof und Wien Nußdorf "
        "Bahnhst keine S 40-Züge fahren.",
        "Für die Züge REX 63 und REX 64 haben wir für Sie einen Autobus zw. Wien Hbf (U) bis Gramatneusiedl "
        "Bahnhof organisiert.",
        "REX 1-Züge werden zwischen Deutsch Wagram Bahnhof und Wien Hbf (U) über Wien Stadlau Bahnhst (U) "
        "umgeleitet.",
        "Wegen eines defekten Zuges auf der Strecke sind zwischen Wien Meidling Bahnhof (U) und Wien Liesing "
        "Bahnhof Zugfahrten derzeit nur eingeschränkt möglich.",
        "Wegen Bauarbeiten können an einzelnen Tagen keine Nahverkehrszüge fahren.",
        "Als Ersatz haben Sie die Möglichkeit den nächsten fahrplanmäßigen Zug zu nehmen.",
        "Planen Sie einige Minuten mehr Reisezeit ein.",
        # "Einige" trains without numbers: the section runs fewer trains (cache, May to August 2026).
        "Wegen Bauarbeiten können von 08.03.2026 (04:40 Uhr) bis 22.06.2026 (04:40 Uhr) zwischen Wien Hbf (U) und "
        "Götzendorf Bahnhof, zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof, und zwischen Wien Hbf (U) und "
        "Bruck/Leitha Bahnhof einige Nahverkehrszüge nicht fahren.",
        "Wegen Bauarbeiten werden zwischen Wien Hbf (U) und Deutsch Wagram Bahnhof von 28.03.2026 bis 02.05.2026 "
        "einige REX 1-Züge umgeleitet und können in Wien Mitte-Landstraße und Wien Praterstern nicht halten.",
        "Wegen Bauarbeiten können zwischen Wien Westbahnhof (U) und Wien Hütteldorf Bahnhof (U) von 02.11.2026 "
        "(20:00 Uhr) bis 14.11.2026 (17:00 Uhr) einige S50-Züge nicht fahren.",
        "Wegen Bauarbeiten fahren zwischen Wien Meidling Bahnhof und Baden b.Wien Bahnhof von 08.06.2026 bis "
        "14.06.2026 einige Nahverkehrszüge mit geänderten Fahrzeiten.",
        "Wegen Bauarbeiten können am 21.10.2026 einige REX Züge nicht fahren.",
    ],
)
def test_names_no_single_trains(sentence: str) -> None:
    assert not _oebb_names_single_trains(sentence)


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("S1: Wien Leopoldau ↔ Deutsch Wagram", S1),
        ("Bauarbeiten: St. Pölten Hbf Flughafen Wien", RJ_12265),
        ("Wien Hauptbahnhof ↔ Gramatneusiedl", GRAMATNEUSIEDL_TRAINS),
        ("Wien Hütteldorf ↔ Wien Hauptbahnhof", HUETTELDORF),
    ],
)
def test_single_trains_leave_the_feed(title: str, description: str) -> None:
    item = _oebb(title, description)
    assert _oebb_single_trains_only(item)
    assert _post_filter_oebb([item]) == []


def test_some_trains_stay_in_the_feed() -> None:
    item = _oebb("Wien Floridsdorf ↔ Wien Praterstern", FLORIDSDORF_SOME)
    assert not _oebb_single_trains_only(item)
    assert [kept["description"] for kept in _post_filter_oebb([item])] == [FLORIDSDORF_SOME]


def test_a_closure_keeps_its_train_list() -> None:
    # The lead closes the line; the trains named after it change nothing.
    for title, description in (
        ("REX 50/REX 51/S 50: Wien Westbahnhof ↔ St. Pölten Hauptbahnhof", REX50),
        ("Wien Hauptbahnhof ↔ Wien Westbahnhof", HBF_WESTBAHNHOF),
    ):
        item = _oebb(title, description)
        assert not _oebb_single_trains_only(item)
        assert [kept["description"] for kept in _post_filter_oebb([item])] == [description]
    # A closure in a later sentence keeps a notice whose lead names a train.
    late = _oebb("Wien Hütteldorf ↔ Wien Hauptbahnhof", HUETTELDORF + "<br>Am 19.10.2026 können keine Züge fahren.")
    assert not _oebb_single_trains_only(late)


def test_a_line_is_no_train() -> None:
    item = _oebb("Wien Hauptbahnhof ↔ Gramatneusiedl", GRAMATNEUSIEDL_INCIDENT)
    assert not _oebb_single_trains_only(item)
    assert len(_post_filter_oebb([item])) == 1


def test_the_shown_lead_decides() -> None:
    # ÖBB's first sentence speaks of all trains, the shown one of RJ 263: out.
    assert _oebb_single_trains_only(_oebb("Wien Hütteldorf ↔ Wien Hauptbahnhof", HUETTELDORF_FIRST_ALL))
    # ÖBB's first sentence names RJ 160, the shown one speaks of all trains: in.
    shown_all = _oebb("Wien Hütteldorf ↔ Wien Hauptbahnhof", HUETTELDORF_SHOWN_ALL)
    sentences, shown = _oebb_shown_sentences(shown_all)
    assert "der Zug RJ 160" in sentences[0] and "alle Fernverkehrszüge" in shown[0]
    assert not _oebb_single_trains_only(shown_all)


def test_only_oebb_notices_count() -> None:
    wl = _oebb("U6: Zugausfall", "Der Zug 1234 fällt aus.") | {"source": "Wiener Linien"}
    assert not _oebb_single_trains_only(wl)


def test_the_feed_reads_the_cache_without_single_trains(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = [
        _oebb("S1: Wien Leopoldau ↔ Deutsch Wagram", S1),
        _oebb("REX 50/REX 51/S 50: Wien Westbahnhof ↔ St. Pölten Hauptbahnhof", REX50),
    ]
    monkeypatch.setattr(providers_setup, "read_cache", lambda name: cached if name == "oebb" else [])
    assert [item["description"] for item in providers_setup.read_cache_oebb()] == [REX50]
