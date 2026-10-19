"""An ÖBB text leads with the sentence that names the title's route.

The provider titles an ÖBB notice with its Wien-relevant route, which may
stand in a later section of the text. Published 2026-10-17 (operator report
of that morning): title "Wien Hütteldorf ↔ Wien Hauptbahnhof" over a text
about "Salzburg Hbf und Flughafen Wien Bahnhof bzw. Hegyeshalom", cut before
it said what happens. The title's route stood only in the section
"TEILAUSFALL", which the TV viewer never saw. A replay over all 589 ÖBB
texts cached since May changed only this notice (three of its versions).

Mutations checked against this file (each one caught, by the test named):

* no reordering at all → ``test_the_title_route_section_comes_first``.
* "bzw." alternatives not split → ``test_the_title_route_section_comes_first``.
* reordering also when sentence one shares a station →
  ``test_a_neighbouring_stretch_stays_first``.
* reordering also when sentence one names no route →
  ``test_a_first_sentence_without_route_stays_first``.
* the label left in capitals → ``test_the_title_route_section_comes_first``.

ÖBB's capital section labels read in normal case ("Teilausfall:"), operator
request 2026-10-17; codes such as "RJ 263" or "ÖBB" stay
(``test_only_labels_change_case``).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")

_VIENNA = ZoneInfo("Europe/Vienna")
_LINK = "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&"


def _summary(item: FeedItem) -> str:
    starts = datetime(2026, 10, 17, tzinfo=_VIENNA)
    ends = datetime(2026, 10, 21, 23, 59, 59, tzinfo=_VIENNA)
    return bf._format_item_content(item, "oebb", starts, ends).desc_text_truncated


# ÖBB TRACKINFO 901477 as published 2026-10-17 (cache/oebb_c40d21/events.json).
HUETTELDORF: FeedItem = {
    "source": "ÖBB",
    "category": "Störung",
    "link": _LINK,
    "title": "Wien Hütteldorf ↔ Wien Hauptbahnhof",
    "description": (
        "17.10.2026 - 21.10.2026<br/><br/>Wegen Bauarbeiten fahren<br>zwischen <b>Salzburg Hbf</b> "
        "und <b>Flughafen Wien Bahnhof </b>bzw.<b> Hegyeshalom<br></b>von <b>10.10.2026</b> "
        "(22:30 Uhr) bis <b>14.10.2026</b> (04:00 Uhr) und<br>von <b>17.10.2026 </b>(22:30 Uhr) "
        "bis <b>21.10.2026 </b>(04:00 Uhr)<b><br></b>einige Fernverkehrszüge mit geänderten "
        "Fahrzeiten.<br><br><b>TEILAUSFALL:</b><br>Von <b>18.10.2026</b> bis <b>19.10.2026</b> "
        "kann der Zug RJ 263<br>zwischen <b>Wien Hütteldorf Bahnhof (U) </b>und <b>Wien Hbf (U) "
        "</b>nicht fahren.<br><br><b>HALTAUSFALL:<br></b>Von <b>11.10.2026</b> bis "
        "<b>12.10.2026</b> kann der Zug RJ 263<br>in <b>Wien Hütteldorf Bahnhof (U) </b>nicht "
        "halten.<br><br>Ihre Reisezeit verlängert sich um bis zu 8 Minuten.<br>"
        "Anschlussverbindungen können nicht gewährleistet werden.<br><br>Wir bitten um "
        "Entschuldigung."
    ),
}


def test_the_title_route_section_comes_first() -> None:
    summary = _summary(HUETTELDORF)
    assert summary.startswith(
        "Teilausfall: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263 zwischen "
        "Wien Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren."
    )
    assert "Salzburg" not in summary


def test_a_neighbouring_stretch_stays_first() -> None:
    # ÖBB TRACKINFO 888637, 24.06.2026: the works seen from the next stretch.
    item: FeedItem = {
        "source": "ÖBB",
        "category": "Störung",
        "link": _LINK,
        "title": "Wien Floridsdorf ↔ Gänserndorf",
        "description": (
            "Wegen Bauarbeiten können zwischen <b>Gänserndorf Bahnhof</b> und <b>Deutsch Wagram "
            "Bahnhof</b> von <b>11.09.2026</b> (00:10 Uhr) bis <b>05.10.2026</b> (04:20 Uhr) einige "
            "Nahverkehrszüge nicht fahren.<br>Einige Nahverkehrszüge können zwischen <b>Gänserndorf "
            "Bahnhof</b> und <b>Wien Floridsdorf Bahnhof (U)</b> nicht fahren."
        ),
    }
    assert _summary(item).startswith("Wegen Bauarbeiten können zwischen Gänserndorf Bahnhof")


def test_a_first_sentence_without_route_stays_first() -> None:
    # ÖBB, 22.06.2026: sentence one speaks for the whole line.
    item: FeedItem = {
        "source": "ÖBB",
        "category": "Störung",
        "link": _LINK,
        "title": "Wien Meidling ↔ Wien Liesing",
        "description": (
            "Wegen eines Polizeieinsatzes sind keine Fahrten möglich.<br>Fahrgäste mit ÖBB-Tickets "
            "können zwischen <b>Wien Meidling Bahnhof (U)</b> und <b>Wien Liesing Bahnhof</b> die "
            "Wiener Linien benutzen."
        ),
    }
    assert _summary(item).startswith("Wegen eines Polizeieinsatzes sind keine Fahrten möglich.")


def test_only_labels_change_case() -> None:
    assert bf._oebb_section_labels_cased(
        "VORVERLEGTE ABFAHRTSZEITEN/GEÄNDERTE FAHRZEITEN: Der Zug RJ 263 der ÖBB: ACHTUNG: x"
    ) == "Vorverlegte Abfahrtszeiten/Geänderte Fahrzeiten: Der Zug RJ 263 der ÖBB: Achtung: x"


# Operator decision 2026-10-19 (card "Ja, als Regel"): the end follows the
# section that leads the shown text. The TV showed "Von 18.10.2026 bis
# 19.10.2026 … [Bis Mi 21.10.]", and from 20.10. a cancellation already over.


def _dated(item: FeedItem) -> dict[str, object]:
    out: dict[str, object] = dict(item)
    out["starts_at"] = datetime(2026, 10, 17, tzinfo=_VIENNA)
    out["ends_at"] = datetime(2026, 10, 21, 23, 59, 59, tzinfo=_VIENNA)
    return out


def test_the_end_follows_the_leading_section() -> None:
    item = _dated(HUETTELDORF)
    assert bf._oebb_lead_section_end(item) == datetime(2026, 10, 19, 23, 59, 59, tzinfo=_VIENNA)
    items = [item]
    bf._end_with_oebb_lead_section(items)  # type: ignore[arg-type]
    assert item["ends_at"] == datetime(2026, 10, 19, 23, 59, 59, tzinfo=_VIENNA)


def test_the_end_stays_when_sentence_one_leads() -> None:
    # Same notice, but titled with the route of sentence one: nothing moves.
    item = _dated({**HUETTELDORF, "title": "Salzburg Hbf ↔ Flughafen Wien"})
    assert bf._oebb_lead_section_end(item) is None


def test_the_end_is_never_extended() -> None:
    item = _dated(HUETTELDORF)
    item["ends_at"] = datetime(2026, 10, 19, 12, 0, tzinfo=_VIENNA)
    assert bf._oebb_lead_section_end(item) is None


def test_a_section_without_date_keeps_the_end() -> None:
    desc = str(HUETTELDORF["description"]).replace(
        "Von <b>18.10.2026</b> bis <b>19.10.2026</b> kann", "Derzeit kann"
    )
    assert bf._oebb_lead_section_end(_dated({**HUETTELDORF, "description": desc})) is None


def test_other_sources_keep_their_end() -> None:
    assert bf._oebb_lead_section_end(_dated({**HUETTELDORF, "source": "Wiener Linien"})) is None


def test_the_section_end_is_wired_into_the_build() -> None:
    from datetime import UTC

    item = _dated(HUETTELDORF)
    item["guid"] = "oebb-901477"
    item["pubDate"] = datetime(2026, 9, 3, 9, 31, tzinfo=_VIENNA)
    now = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
    kept = bf._assemble_feed_items([item], {}, now)  # type: ignore[list-item]
    assert all(it.get("guid") != "oebb-901477" for it in kept)
    # Still in the feed on its last day.
    item = _dated(HUETTELDORF)
    item["guid"] = "oebb-901477"
    item["pubDate"] = datetime(2026, 9, 3, 9, 31, tzinfo=_VIENNA)
    kept = bf._assemble_feed_items([item], {}, datetime(2026, 10, 19, 12, 0, tzinfo=UTC))  # type: ignore[list-item]
    assert [it.get("guid") for it in kept] == ["oebb-901477"]
