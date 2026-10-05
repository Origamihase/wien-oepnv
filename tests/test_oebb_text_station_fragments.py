"""A fragment of an ÖBB sentence is no station name.

``_find_stations_in_text`` slides a window over the message text and asks
the directory about every run of up to four words. ``station_info`` also
knows stations by codes and IDs, and normalises "Bahnhof", "Hbf" and the
transfer marker "(U)" away, so a fragment resolved like a name:

* "Stellwerkstörung am Bahnhof" → "am" → the WL stop "Am Bahnhof",
* "S4-Züge 4212 … und 1730 (REX1)" → "1730" → the WL stop ID of
  "Klinik Hietzing",
* "Update 2 (12.09.2026 23:15)" → "2" → the WL stop "Venediger Au".

Each counted as a Vienna station, so a disruption anywhere in Austria
reached the feed. The ÖBB cache from July to October carried six:
Wolfurt (Vorarlberg), Ebensee/Traunsee, Hinterstoder twice (Upper Austria),
Telfs-Pfaffenhofen (Tyrol) and the S4 stops Lind-Rosegg and Föderlach
(Carinthia), all in ``docs/feed.xml``. ``station_named_in_text`` accepts a
fragment only through a station's name or text alias with at least three
letters. Over the 197 ÖBB items cached since July and the 190 of the raw
snapshots since 2026-10-04, exactly these six change their decision.

Mutations checked against this file (each one caught, by the test named):

* the scan keeps ``canonical_name`` → ``test_a_far_away_disruption_stays_out``
  and ``test_the_captured_far_away_items_leave_the_feed``.
* identity keys accepted → ``test_codes_and_ids_are_no_names``.
* the three-letter floor dropped → ``test_short_fragments_are_no_names``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from xml.sax.saxutils import escape

import pytest
from defusedxml import ElementTree as ET

import src.build_feed as bf
import src.providers.oebb as oebb
from src.utils import stats
from src.utils.stations import station_info, station_named_in_text


@pytest.mark.parametrize("fragment", ["am", "Hbf am", "(U) am", ", am", "Bahnhst am"])
def test_short_fragments_are_no_names(fragment: str) -> None:
    assert station_info(fragment) is not None  # what the scan saw before
    assert station_named_in_text(fragment) is None


@pytest.mark.parametrize(
    "fragment", ["1730 (REX1)", "71 (3472)", "2 (12.09.2026 23:15)", "1528 ,", "Gn"]
)
def test_codes_and_ids_are_no_names(fragment: str) -> None:
    assert station_info(fragment) is not None
    assert station_named_in_text(fragment) is None


@pytest.mark.parametrize(
    ("fragment", "station"),
    [
        ("Wien Hbf (U)", "Wien Hauptbahnhof"),
        ("St.Pölten Hbf", "St. Pölten Hauptbahnhof"),
        ("Wr.Neustadt Hbf", "Wiener Neustadt Hauptbahnhof"),
        ("Bahnhof Laa", "Laa a.d.Thaya"),
        ("Oed", "Oed"),
        ("Mistelbach/Zaya", "Mistelbach"),
        ("Himberg b.Wien", "Himberg"),
        ("Wien Wolf in der Au", "Wien Wolf in der Au"),
    ],
)
def test_names_still_resolve(fragment: str, station: str) -> None:
    info = station_named_in_text(fragment)
    assert info is not None and info.name == station


# Verbatim from cache/oebb_c40d21/events.json (2026-07-11 02:01 UTC); the
# raw title follows ÖBB's single-station form.
_WOLFURT_TITLE = "Streckenunterbrechung: Wolfurt Bahnhst"
_WOLFURT_DESCRIPTION = (
    "11.07.2026<br/><br/>Wegen einer Stellwerkstörung am Bahnhof sind <b>in </b><b>Wolfurt "
    "Bahnhst </b>bis voraussichtlich <b>05:30 Uhr keine Fahrten</b> möglich. Die <b>Züge "
    "warten </b>die Sperre <b>vorerst ab</b>.<br>Sobald uns weitere Informationen vorliegen, "
    "informieren wir Sie.<br>Wir bitten um Entschuldigung."
)
# The same sentence about a Vienna station (2026-07-01 18:01 UTC).
_WESTBAHNHOF_DESCRIPTION = (
    "01.07.2026<br/><br/>Wegen einer Stellwerkstörung am Bahnhof <b>waren</b> in Wien "
    "Westbahnhof (U) <b>bis </b><b>19:38 Uhr</b> keine Fahrten möglich. Planen Sie derzeit "
    "noch bis zu<b> 10 Minuten </b>mehr Reisezeit ein.<br>Wir bitten um Entschuldigung."
)


def _rss(title: str, description: str) -> Any:
    return ET.fromstring(
        "<rss><channel><item>"
        f"<title>{escape(title)}</title>"
        "<link>https://fahrplan.oebb.at/bin/help.exe/dn</link>"
        "<guid>https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;895124</guid>"
        "<pubDate>Sat, 11 Jul 2026 04:23:29 +0200</pubDate>"
        f"<description>{escape(description)}</description>"
        "</item></channel></rss>"
    )


def test_a_far_away_disruption_stays_out() -> None:
    with patch.object(oebb, "_fetch_xml", return_value=_rss(_WOLFURT_TITLE, _WOLFURT_DESCRIPTION)):
        assert oebb.fetch_events() == []


def test_the_same_sentence_about_vienna_stays_in() -> None:
    rss = _rss("Streckenunterbrechung: Wien Westbahnhof (U)", _WESTBAHNHOF_DESCRIPTION)
    with patch.object(oebb, "_fetch_xml", return_value=rss):
        events = oebb.fetch_events()
    assert [e["title"] for e in events] == ["Wien Westbahnhof"]


# Title and description as cached (the build re-checks the cache on every
# run, ``_post_filter_oebb``).
_CAPTURED_FAR_AWAY = [
    ("Wolfurt", _WOLFURT_DESCRIPTION),
    (
        "Telfs-Pfaffenhofen",
        "31.08.2026<br/><br/>Wegen einer Stellwerkstörung am Bahnhof <b>waren</b> in "
        "Telfs-Pfaffenhofen Bahnhof [in Pfaffenhofen] <b>bis </b><b>11:48 Uhr</b> keine Fahrten "
        "möglich. Planen Sie derzeit noch bis zu<b> 30 Minuten </b>mehr Reisezeit ein.<br>Wir "
        "bitten um Entschuldigung.",
    ),
    (
        "S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach",
        "23.11.2026 - 30.11.2026<br/><br/>Wegen Bauarbeiten können<br>von <b>23.11.2026</b> "
        "(08:00 Uhr) bis <b>30.11.2026</b> (23:00 Uhr)<br>in <b>Lind-Rosegg Bahnhst</b> und<b> "
        "Föderlach Bahnhof </b><br>die S4-Züge 4205, 4209, 4213, 4217, 4221, 4225 und 4229<br>in "
        "<b>Föderlach Bahnhof<br></b>die S4-Züge 4212, 4216, 4220, 4224, 4228 und 1730 "
        "(REX1)<br>nicht halten.<br>Als Ersatzbeförderung haben Sie die Möglichkeit den nächsten "
        "fahrplanmäßigen Zug zu nehmen.<br><br>Wir bitten um Entschuldigung.",
    ),
]


@pytest.mark.parametrize(("title", "description"), _CAPTURED_FAR_AWAY)
def test_the_captured_far_away_items_leave_the_feed(title: str, description: str) -> None:
    item = {"source": "ÖBB", "category": "Störung", "title": title, "description": description}
    assert bf._post_filter_oebb([item]) == []


def test_the_statistics_scan_skips_fragments_too() -> None:
    # The sliding-window scan of the statistics put this WL notice under the
    # stop "Am Bahnhof" ("am Olympiaplatz"); the next name in the text is
    # the stop at the Olympiaplatz, "Stadion".
    item = {
        "source": "Wiener Linien",
        "title": "11A: Umleitung wegen Gleisbauarbeiten",
        "description": (
            "Gleisbauarbeiten Wegen Gleisbauarbeiten (Linie 18) am Olympiaplatz kommt es bei "
            "der Linie 11A zu folgender Änderung."
        ),
    }
    assert stats.extract_location_name(item) != "Wien Am Bahnhof (WL)"
