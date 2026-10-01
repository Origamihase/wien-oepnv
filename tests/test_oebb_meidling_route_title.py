"""ÖBB titles keep Wien Meidling (feed 2026-09-13, 09-17 and 09-29).

Published 2026-09-29 07:01 for eight versions of ``docs/feed.xml``::

    Wien Hauptbahnhof ↔ Wien Hauptbahnhof

The description reads "zwischen Wien Hbf (U) und Wien Meidling Bahnhof (U)
Zugfahrten … nur eingeschränkt möglich". On 2026-09-13 and 09-17 the same
fault named "Wien Hauptbahnhof ↔ Tullnerfeld" for a disruption between
Wien Meidling and Tullnerfeld. Three faults met:

1. ``_clean_title_keep_places`` looked up "Wien Meidling Bahnhof (U)" with
   ÖBB's transfer marker. The lookup token "wien meidling u" belongs to the
   alias "Wien Bhf. Meidling U" of the Wiener-Linien stop
   "Wien Bhf. Meidling (WL)".
2. ``_normalize_endpoint_name`` took the period of "Bhf." for a sentence end
   and cut the name to "Wien Bhf", which resolves to Wien Hauptbahnhof.
   Seven Vienna stations have such a "Wien Bhf. X" stop.
3. The description route ran on into "Zugfahrten", resolved to no station
   and dropped out. Only the title carried the route, so the wrong title
   stood, and a message titled by its cause would have been dropped.

The cache keeps only the processed title; the raw titles below are rebuilt
in ÖBB's ``A < ↔ > B`` form from the description. With them the code before
the fix reproduces both published titles.

Mutations checked against this file (each one caught, by the test named):

* the ``station_info`` guard before the sentence cut is dropped →
  ``test_a_bhf_abbreviation_is_no_sentence_end``. The captured titles pass
  without it: once the marker is stripped, no "Wien Bhf. Meidling" reaches
  the cut. The guard covers every other path to such a name.
* the title lookup keeps the transfer marker →
  ``test_a_transfer_marker_does_not_lead_to_the_wl_stop`` and the captured
  titles.
* "Zugfahrten" is no boundary → ``test_the_description_route_ends_before_zugfahrten``
  and ``test_a_cause_title_keeps_the_message``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb
from src.utils.stations import station_info

# Verbatim from cache/oebb_c40d21/events.json (guid …TRACKINFO&915623,
# 2026-09-29 06:58), trimmed after the route sentence.
_HBF_MEIDLING = (
    "29.09.2026<br/><br/>Wegen einer Oberleitungsstörung sind zwischen<b> </b>"
    "<b>Wien Hbf (U) </b>und <b>Wien Meidling Bahnhof (U) Zugfahrten</b> bis "
    "voraussichtlich <b>10:00 Uhr nur eingeschränkt</b> möglich."
)
# Verbatim from the same cache (guid …TRACKINFO&911422, 2026-09-13 08:25).
_MEIDLING_TULLNERFELD = (
    "13.09.2026<br/><br/>Wegen eines defekten Zuges auf der Strecke sind zwischen "
    "<b>Wien Meidling Bahnhof (U) </b>und <b>Tullnerfeld Bahnhof Zugfahrten derzeit "
    "nur eingeschränkt</b> möglich. Planen Sie bis zu <b>15 Minuten </b>mehr "
    "Reisezeit ein.<br>Sobald uns weitere Informationen vorliegen, informieren wir "
    "Sie.<br>Wir bitten um Entschuldigung."
)

_STATIONS_WITH_A_BHF_STOP = (
    "Meidling",
    "Hütteldorf",
    "Atzgersdorf",
    "Blumental",
    "Kaiserebersdorf",
    "Süßenbrunn",
    "Zentralfriedhof",
)


def _titles(raw_title: str, description: str) -> list[str]:
    xml = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0"><channel><item>
<title><![CDATA[ {raw_title} ]]></title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&amp;tpl=showmap_external&amp;</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;915623</guid>
<pubDate>Tue, 29 Sep 2026 06:58:20 +0200</pubDate>
<description><![CDATA[ {description} ]]></description>
</item></channel></rss>
"""
    with patch.object(oebb, "_fetch_xml", return_value=ET.fromstring(xml)):
        return [str(event["title"]) for event in oebb.fetch_events()]


@pytest.mark.parametrize(
    ("raw_title", "description", "expected"),
    [
        ("Wien Hbf (U) &lt; ↔ &gt; Wien Meidling Bahnhof (U)", _HBF_MEIDLING, "Wien Hauptbahnhof ↔ Wien Meidling"),
        ("Wien Meidling Bahnhof (U) &lt; ↔ &gt; Wien Hbf (U)", _HBF_MEIDLING, "Wien Meidling ↔ Wien Hauptbahnhof"),
        ("Wien Meidling Bahnhof (U) &lt; ↔ &gt; Tullnerfeld Bahnhof", _MEIDLING_TULLNERFELD, "Wien Meidling ↔ Tullnerfeld"),
    ],
)
def test_the_captured_title_names_both_stations(raw_title: str, description: str, expected: str) -> None:
    assert _titles(raw_title, description) == [expected]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (_HBF_MEIDLING, "Wien Hauptbahnhof ↔ Wien Meidling"),
        (_MEIDLING_TULLNERFELD, "Wien Meidling ↔ Tullnerfeld"),
    ],
)
def test_a_cause_title_keeps_the_message(description: str, expected: str) -> None:
    # Without a route in the title, the description decides; it used to drop.
    assert _titles("Störung", description) == [expected]


def test_the_description_route_ends_before_zugfahrten() -> None:
    assert oebb._extract_zwischen_routes(_HBF_MEIDLING) == [("Wien", "Wien Meidling")]
    assert oebb._extract_zwischen_routes(_MEIDLING_TULLNERFELD) == [("Wien Meidling", "Tullnerfeld")]


@pytest.mark.parametrize("place", _STATIONS_WITH_A_BHF_STOP)
def test_a_bhf_abbreviation_is_no_sentence_end(place: str) -> None:
    name = oebb._normalize_endpoint_name(f"Wien Bhf. {place}")
    info = station_info(name)
    assert info is not None and info.name == f"Wien {place}"


@pytest.mark.parametrize("place", _STATIONS_WITH_A_BHF_STOP)
def test_a_transfer_marker_does_not_lead_to_the_wl_stop(place: str) -> None:
    assert oebb._clean_title_keep_places(f"Wien {place} Bahnhof (U)") == f"Wien {place}"


def test_a_sentence_end_after_the_station_is_still_cut() -> None:
    # The guard only skips names that resolve as a whole.
    assert oebb._normalize_endpoint_name("Wien Bhf. Meidling. Bitte beachten Sie") == "Wien Bhf. Meidling"
    assert oebb._normalize_endpoint_name("Mödling. Auch Auswirkung auf Reisende") == "Mödling"
