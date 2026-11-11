"""ÖBB closure days in every form ÖBB writes them, and texts without a readable day.

Second look at the feed order of 2026-11-08 (``_is_oebb_closure_day``), same
independent check 2026-11-11, after "Nein, nur echte Sperren":

* A closure inside a list the trains share was missed: "… von 03.11.2026
  bis 10.11.2026 keine S50-Züge von 02.11.2026 bis 10.11.2026 die S50-Züge
  20063, … nicht fahren." The verb may stand anywhere in the sentence.
* "bis voraussichtlich am 07.10.2026" was read as a day of the measure, so
  a disruption ranked like a notice; its twin without "am" ranked every day.
* A lead the display moved to the front and that names no date counted on
  every day, also a diversion: "REX 3/REX 1: Wien Hauptbahnhof ↔ Deutsch
  Wagram" showed "REX 1-Züge werden … umgeleitet." (cache 04.09.2026). Its
  days come from ÖBB's first sentence now.
* A planned measure whose days the reader does not take ("von 20.10.2026
  auf 21.10.2026", "in der Nacht von 14.02. auf 15.02.2026") counted on
  every day. It counts only when it closes the line; a text naming no day
  at all (an unplanned disruption) still counts on every day.
* Days without their year or with two digits for it were not read ("von
  08.03. bis 10.03.2027 und von 15.03. bis 17.03.2027", "am 02.07., 06.08.,
  03.09., 01.10. und 05.11.2026", "am 03.02.27, …"): a recurring closure
  counted on one of its days or on every day of a nine-month period. Nor
  were "zwischen 29.10.2026 und 01.11.2026", a section opening with "Am"
  and weekdays in the plural ("jeweils Sonntags … und Montags …").
* Closure hours within a day did not end the closure: "R95: Wien
  Hauptbahnhof ↔ Felixdorf" ("(jeweils 14:00 Uhr - 22:00 Uhr)") stood
  ahead of the WL notices until 23:30 on 10.11.2026 and at the top of the
  feed on 13.10. at 22:30. The hours end a day unless the next day is
  closed too; hours across midnight keep the whole days.
* "Zugfahrten derzeit nicht möglich" says that no train runs, like "keine
  Züge fahren"; "nur eingeschränkt möglich" stays a restriction.

Over the 648 ÖBB records ÖBB published for all of Austria from 04.10. to
11.11.2026 and the 323 cache variants from 24.08. on, the changes in Vienna
are the REX 1 diversion (no closure day any more) and the closure hours of
R95 and S45; the date forms occur outside Vienna. Replaying the real chain
over 1,220 feed states from 01.10. to 11.11.2026 changes 9 of them, all by
the closure hours: R95 leaves the first ten after 22:00 on 13.10. and
10.11. (5 states), S45 moves behind "Wien Hauptbahnhof ↔ Gramatneusiedl"
after 04:10 on 01.11. (4 states).

Mutations checked against this file (each one caught, by the test named):

* the verb within two words of the trains, as before →
  ``test_a_closure_in_a_shared_list_closes_the_line``.
* no "bis" check for single dates →
  ``test_an_expected_end_is_no_day_of_the_measure``.
* no days from ÖBB's first sentence for a moved lead →
  ``test_a_moved_lead_takes_its_days_from_the_first_sentence``.
* no check whether an unread text names a day →
  ``test_a_planned_measure_without_a_readable_day_ranks_only_as_a_closure``.
* that check on the shown lead only →
  ``test_a_moved_lead_takes_its_days_from_the_first_sentence``.
* no year from the next date, no year before it, no two-digit years, no
  comma before a day or a range → ``test_days_in_every_form_ÖBB_writes``.
* one word only between "bis" and the date →
  ``test_an_expected_end_is_no_day_of_the_measure``.
* no "zwischen … und" range, "am" only in lower case →
  ``test_days_in_every_form_ÖBB_writes``.
* weekdays only in the singular →
  ``test_weekdays_in_the_plural_narrow_the_days``.
* no closure hours, or hours that end every day →
  ``test_closure_hours_end_a_day_the_next_day_does_not_continue`` and
  ``test_weekdays_in_the_plural_narrow_the_days``.
* hours across midnight read as an end →
  ``test_closure_hours_end_a_day_the_next_day_does_not_continue``.
* no "Zugfahrten … nicht möglich" →
  ``test_a_closure_in_a_shared_list_closes_the_line``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from src.feed_build.cache_filters import _post_filter_oebb
from src.feed_build.oebb_days import _is_oebb_closure_day, _oebb_closes_line, _oebb_closure_days
from src.feed_build.oebb_lead import _oebb_shown_sentences
from src.feed_build.oebb_single_trains import _oebb_single_trains_only
from src.feed_types import FeedItem

_VIENNA = ZoneInfo("Europe/Vienna")

# ÖBB RSS, 04.10.2026 (REX 50/REX 51/S 50 between Westbahnhof and Hütteldorf), lists shortened.
S50_LIST = (
    "Zwischen Wien Westbahnhof (U) und Wien Hütteldorf Bahnhof (U) können von 03.11.2026 bis 10.11.2026 "
    "die REX51-Züge 1604, 1608 und 1657 von 02.11.2026 bis 10.11.2026 die REX51-Züge 1670, 1674 und 1675 "
    "von 03.11.2026 bis 11.11.2026 der REX51-Zug 1679 von 03.11.2026 bis 10.11.2026 keine S50-Züge von "
    "02.11.2026 bis 10.11.2026 die S50-Züge 20063, 20161 und 20078 von 03.11.2026 bis 11.11.2026 die "
    "S50-Züge 20083 und 20080 nicht fahren."
)
# ÖBB RSS, "Update 6 (05.10.2026 05:15) Verkehrseinschränkung: Gramatneusiedl Bahnhof - Bruck/Leitha Bahnhof".
GRAMATNEUSIEDL_BRUCK = (
    "04.10.2026 - 07.10.2026<br/><br/>Wegen Reparaturarbeiten nach einer Oberleitungsstörung sind zwischen"
    "<b> </b><b>Gramatneusiedl Bahnhof </b>und<b> </b><span>Bruck/Leitha Bahnhof<b> </b>Zugfahrten</span> "
    "bis voraussichtlich am <b>07.10.2026</b>, <b>23:59 Uhr nur eingeschränkt</b> möglich. <b>Die "
    "Fernverkehrszüge RJX verkehren planmäßig.</b>"
)
# ÖBB TRACKINFO 887095, cache/oebb_c40d21/events.json of 04.09.2026, notes shortened.
REX1_ITEM: dict[str, Any] = {
    "source": "ÖBB", "category": "Störung",
    "title": "REX 3: Wien Floridsdorf ↔ Wien Jedlersdorf / Wien Hauptbahnhof ↔ Deutsch Wagram",
    "description": (
        "10.08.2026 - 06.09.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Floridsdorf Bahnhof "
        "(U)</b> und <b>Wien Jedlersdorf Bahnhst, Wien Süßenbrunn Bahnhst </b><span>bzw.</span><b> Gerasdorf "
        "b. Wien Bahnhst</b><br>von <b>10.08.2026</b> bis <b>06.09.2026 </b>keine Züge fahren.<br>Ein "
        "Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet.<br><br>REX 2 und REX 3-Züge enden "
        "bereits in <b>Wolkersdorf im Weinviertel Bahnhof</b> bzw. <b>Korneuburg Bahnhof</b>.<br>Ein "
        "Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet.<br><br>REX 1-Züge werden zwischen "
        "<b>Deutsch Wagram Bahnhof</b> und <b>Wien Hbf (U) </b>über <b>Wien Stadlau Bahnhst (U)</b> "
        "umgeleitet.<br><br>Wir bitten um Entschuldigung."
    ),
    "link": "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&",
    "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&887095",
    "pubDate": "2026-06-19T14:11:12+02:00", "starts_at": "2026-06-19T14:11:12+02:00", "ends_at": None,
}
REX1_DIVERSION = "REX 1-Züge werden zwischen Deutsch Wagram Bahnhof"
# The same notice with its REX 1 section a closure (no such text yet).
REX1_CLOSED = str(REX1_ITEM["description"]).replace(
    "REX 1-Züge werden zwischen <b>Deutsch Wagram Bahnhof</b> und <b>Wien Hbf (U) </b>über <b>Wien Stadlau "
    "Bahnhst (U)</b> umgeleitet.",
    "Zwischen <b>Deutsch Wagram Bahnhof</b> und <b>Wien Hbf (U) </b>können keine REX 1-Züge fahren.",
)
# ÖBB RSS, 04.10.2026 (outside Vienna), the second section left out.
EN414 = (
    "20.10.2026 - 21.10.2026<br/><br/>Wegen Bauarbeiten werden<br>von <b>20.10.2026</b> auf <b>21.10.2026</b>"
    "<br>die Züge EN 414, EN 40414, EN 40237 und EN 40465<br>umgeleitet und können daher zwischen<br><b>Zidani "
    "Most</b> und <b>Schwarzach im Pongau-St.Veit Bahnhof</b> nicht halten."
)
# ÖBB RSS, "Update 5 (01.11.2026 07:16) Verkehrseinschränkung: Wels - Linz", shortened.
WELS = (
    "01.11.2026<br/><br/>Wegen Verlängerung der Bauarbeiten sind zwischen<b> </b><b>Wels Hbf </b>und "
    "<b>Linz/Donau Hbf </b>Zugfahrten derzeit nur eingeschränkt möglich. Planen Sie in diesem Bereich derzeit "
    "bis zu<b> 15 Minuten</b> mehr Reisezeit ein."
)
# ÖBB RSS, 01.11.2026, "Verkehrseinschränkung/Verzögerung Fahrgastinformation: Wien Hbf (U) - Graz Hbf".
SYSTEM = (
    "01.11.2026 - 02.11.2026<br/><br/>Wegen einer technischen Störung im System kann es derzeit zu "
    "Unregelmäßigkeiten im Zugverkehr sowie bei den Anzeigen an den Bahnhöfen und in der ÖBB Fahrplanauskunft "
    "SCOTTY kommen. Unsere Mitarbeiter:innen arbeiten bereits an der Behebung der Störung. <br>Wir bitten um "
    "Entschuldigung.<br>"
)
# ÖBB RSS, 04.10. to 05.11.2026 (outside Vienna), notes left out.
WOLFSBERG = (
    "02.07.2026 - 05.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wolfsberg in Ktn Bahnhof</b> "
    "und <b>St.Paul im Lavanttal Bahnhof </b><br>am <b>02</b><b>.07., 06.08., 03.09., 01.10. </b><span>und "
    "</span><b>05</b><b>.11.2026 </b><span>(jeweils von 09:30 Uhr bis 15:30 Uhr)</span><b><br></b>keine "
    "Nahverkehrszüge fahren.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)

# ÖBB, cache/oebb_c40d21/events.json from 24.08.2026, the list of days shortened.
R95 = (
    "10.02.2026 - 10.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf (U)</b> und <b>Felixdorf "
    "Bahnhof </b><br>am <b>08.09.2026</b><br>am <b>13.10.2026 </b>und<br>am <span><b>10.11.2026 </b>(jeweils "
    "14:00 Uhr - 22:00 Uhr)<b> </b></span>keine <i>R95-Züge</i> fahren.<br>Ein Schienenersatzverkehr mit "
    "Autobussen wird für Sie eingerichtet."
)
# ÖBB, cache/oebb_c40d21/events.json from 24.08.2026.
S45 = (
    "01.11.2026<br/><br/>Wegen Bauarbeiten können<br>zwischen <b>Wien Hütteldorf Bahnhof (U)</b> und <b>Wien "
    "Handelskai Bahnhst (U)</b><br>am <b>01<span>.11.2026</span></b><span> (von 01:10 Uhr bis 04:10 Uhr) "
    "</span>keine S45-Züge fahren."
)
# ÖBB RSS, 12.10. to 11.11.2026 (outside Vienna).
FELIXDORF = (
    "01.11.2026 - 16.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Felixdorf Bahnhof</b> und "
    "<b>Wr.Neustadt Hbf </b><br>von <b>01.11.2026</b> bis <b>16.11.2026 </b><span>(jeweils Sonntags 00:10 Uhr "
    "- 04:30 Uhr und Montags 00:10 Uhr - 03:50 Uhr)</span><b> </b>keine Nahverkehrszüge fahren."
)


def _vienna(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=_VIENNA).astimezone(UTC)


def _oebb(title: str, description: str, start: date, end: date) -> FeedItem:
    return {
        "source": "ÖBB", "category": "Störung", "title": title, "description": description,
        "guid": f"oebb|{title}", "link": "https://fahrplan.oebb.at/",
        "pubDate": _vienna(start.year, start.month, start.day, 0),
        "starts_at": datetime(start.year, start.month, start.day, tzinfo=_VIENNA),
        "ends_at": datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=_VIENNA),
    }


def _days(text: str) -> list[str] | None:
    days = _oebb_closure_days(text)
    return None if days is None else sorted(day.strftime("%d.%m.%Y") for day in days)


def test_a_closure_in_a_shared_list_closes_the_line() -> None:
    assert _oebb_closes_line(S50_LIST)
    assert _oebb_closes_line(
        "Zwischen Wien Westbahnhof (U) und Wien Hütteldorf Bahnhof (U) können keine S50-Züge von 03.11.2026 "
        "bis 10.11.2026 fahren."
    )
    assert not _oebb_closes_line(
        "Zwischen Wien Westbahnhof (U) und Wien Hütteldorf Bahnhof (U) können von 02.11.2026 bis 10.11.2026 "
        "die S50-Züge 20063, 20161 und 20078 nicht fahren."
    )
    # No train runs: "Zugfahrten … nicht möglich" (cache, 18.10.2026); a restriction is no closure.
    assert _oebb_closes_line(
        "Wegen Personen im Gleisbereich sind zwischen Wien Hbf (U) und Wien Matzleinsdorfer Platz Bahnhst "
        "Zugfahrten derzeit nicht möglich ."
    )
    assert not _oebb_closes_line(
        "Wegen Verlängerung der Bauarbeiten sind zwischen Wels Hbf und Linz/Donau Hbf Zugfahrten derzeit nur "
        "eingeschränkt möglich."
    )
    # The closure keeps the notice in the feed although the list names trains.
    item = _oebb("REX 51/S 50: Wien Westbahnhof ↔ Wien Hütteldorf", S50_LIST, date(2026, 11, 2), date(2026, 11, 11))
    assert not _oebb_single_trains_only(item)


def test_an_expected_end_is_no_day_of_the_measure() -> None:
    lead = _oebb_shown_sentences(_oebb("Gramatneusiedl ↔ Bruck/Leitha", GRAMATNEUSIEDL_BRUCK,
                                       date(2026, 10, 4), date(2026, 10, 7)))[1][0]
    assert "bis voraussichtlich am 07.10.2026" in lead
    assert _days(lead) is None
    assert _days("Zwischen A und B sind bis voraussichtlich Sonntag, 01.11.2026 keine Fahrten möglich.") is None
    item = _oebb("Gramatneusiedl ↔ Bruck/Leitha", GRAMATNEUSIEDL_BRUCK, date(2026, 10, 4), date(2026, 10, 7))
    assert [day for day in range(4, 8) if _is_oebb_closure_day(item, _vienna(2026, 10, day))] == [4, 5, 6, 7]


def test_a_moved_lead_takes_its_days_from_the_first_sentence() -> None:
    (item,) = _post_filter_oebb([dict(REX1_ITEM)])
    assert item["title"] == "REX 3/REX 1: Wien Hauptbahnhof ↔ Deutsch Wagram"
    assert _oebb_shown_sentences(item)[1][0].startswith(REX1_DIVERSION)
    # A diversion closes nothing, on no day.
    for day in (date(2026, 6, 20), date(2026, 8, 20), date(2026, 10, 15)):
        assert not _is_oebb_closure_day(item, _vienna(day.year, day.month, day.day))
    closed = cast(FeedItem, {**item, "description": REX1_CLOSED})
    assert _oebb_shown_sentences(closed)[1][0].startswith("Zwischen Deutsch Wagram Bahnhof und Wien Hbf (U)")
    assert _is_oebb_closure_day(closed, _vienna(2026, 8, 20))
    assert not _is_oebb_closure_day(closed, _vienna(2026, 6, 20))
    assert not _is_oebb_closure_day(closed, _vienna(2026, 10, 15))
    # ÖBB's first sentence names its day in a form the reader does not take:
    # still a planned measure, and the diversion shown first closes nothing.
    night = cast(FeedItem, {**item, "description": str(item["description"]).replace(
        "von <b>10.08.2026</b> bis <b>06.09.2026 </b>", "in der Nacht von <b>14.08.</b> auf <b>15.08.2026</b> ")})
    sentences, shown = _oebb_shown_sentences(night)
    assert "in der Nacht von 14.08. auf 15.08.2026" in sentences[0] and shown[0].startswith(REX1_DIVERSION)
    assert not _is_oebb_closure_day(night, _vienna(2026, 8, 14))


def test_a_planned_measure_without_a_readable_day_ranks_only_as_a_closure() -> None:
    en414 = _oebb("Schwarzach im Pongau-St.Veit ↔ Zidani Most", EN414, date(2026, 10, 20), date(2026, 10, 21))
    assert _days(_oebb_shown_sentences(en414)[1][0]) is None
    assert not _is_oebb_closure_day(en414, _vienna(2026, 10, 20))
    night = (
        "14.02.2027 - 15.02.2027<br/><br/>Wegen Bauarbeiten werden in der Nacht von 14.02. auf 15.02.2027 die "
        "REX 1-Züge zwischen <b>Deutsch Wagram Bahnhof</b> und <b>Wien Hbf (U)</b> über <b>Wien Stadlau "
        "Bahnhst (U)</b> umgeleitet."
    )
    diversion = _oebb("REX 1: Wien Hauptbahnhof ↔ Deutsch Wagram", night, date(2027, 2, 14), date(2027, 2, 15))
    assert not _is_oebb_closure_day(diversion, _vienna(2027, 2, 14))
    closure = _oebb(
        "REX 1: Wien Hauptbahnhof ↔ Deutsch Wagram",
        night.replace("werden in der Nacht", "können in der Nacht").replace(
            "die REX 1-Züge zwischen", "zwischen").replace(
            "über <b>Wien Stadlau Bahnhst (U)</b> umgeleitet.", "keine Züge fahren."),
        date(2027, 2, 14), date(2027, 2, 15),
    )
    assert _is_oebb_closure_day(closure, _vienna(2027, 2, 14))
    assert _is_oebb_closure_day(closure, _vienna(2027, 2, 15))


def test_a_disruption_without_a_day_still_counts_every_day() -> None:
    wels = _oebb("Wels Hbf ↔ Linz/Donau Hbf", WELS, date(2026, 11, 1), date(2026, 11, 1))
    assert _is_oebb_closure_day(wels, _vienna(2026, 11, 1))
    system = _oebb("Wien Hauptbahnhof ↔ Graz Hbf", SYSTEM, date(2026, 11, 1), date(2026, 11, 2))
    assert _is_oebb_closure_day(system, _vienna(2026, 11, 1))
    assert _is_oebb_closure_day(system, _vienna(2026, 11, 2))


@pytest.mark.parametrize(
    ("lead", "days"),
    [
        # Spittal/Drau – Lienz, ÖBB RSS from 02.11.2026.
        (
            "Wegen Bauarbeiten können zwischen Spittal/Drau-Millstätter See Bahnhof und Lienz in Osttirol Bahnhof "
            "von 08.03. bis 10.03.2027 und von 15.03. bis 17.03.2027 keine Fernverkehrszüge fahren.",
            ["08.03.2027", "09.03.2027", "10.03.2027", "15.03.2027", "16.03.2027", "17.03.2027"],
        ),
        # Pöchlarn – Scheibbs, ÖBB RSS from 10.11.2026.
        (
            "Wegen Bauarbeiten können zwischen Pöchlarn Bahnhof und Scheibbs Bahnhof am 03.02.27, 03.03.27, "
            "05.05.27, 02.06.27, 07.07.27, 04.08.27, 01.09.27, 06.10.27 und 03.11.27 (jeweils 11:30 Uhr - "
            "17:30 Uhr) keine Nahverkehrszüge fahren.",
            ["01.09.2027", "02.06.2027", "03.02.2027", "03.03.2027", "03.11.2027", "04.08.2027", "05.05.2027",
             "06.10.2027", "07.07.2027"],
        ),
        # Bruck/Mur – Graz, ÖBB RSS from 22.10.2026: ranges listed with commas, the year once.
        (
            "Wegen Bauarbeiten kann zwischen Bruck/Mur Bahnhof und Graz Hbf von 07.02 bis 08.02., 12.02 bis "
            "15.02., 19.02. bis 21.02.2027 der Zug RJ 650 nicht fahren.",
            ["07.02.2027", "08.02.2027", "12.02.2027", "13.02.2027", "14.02.2027", "15.02.2027", "19.02.2027",
             "20.02.2027", "21.02.2027"],
        ),
        # Spielfeld-Straß – Bad Radkersburg, ÖBB RSS from 02.11.2026: "07,04,2027" stays unread.
        (
            "Wegen Bauarbeiten können zwischen Spielfeld-Straß in Stmk Bahnhof und Bad Radkersburg Bahnhof am "
            "03.02.2027, 03.03.2027, 07,04,2027, 05.05.2027 und 02.06.2027 (jeweils von 08:30 Uhr - 16:00 Uhr) "
            "keine Nahverkehrszüge fahren.",
            ["02.06.2027", "03.02.2027", "03.03.2027", "05.05.2027"],
        ),
        # Across the turn of the year (no such text yet).
        (
            "Wegen Bauarbeiten können zwischen A und B von 30.12. bis 02.01.2027 keine Züge fahren.",
            ["01.01.2027", "02.01.2027", "30.12.2026", "31.12.2026"],
        ),
        # Golling – Schwarzach, ÖBB RSS from 04.10.2026: "zwischen <date> und <date>" is a range.
        (
            "Wegen Bauarbeiten fahren zwischen 29.10.2026 und 01.11.2026 von Golling/Salzach-Abtenau Bahnhof bis "
            "Schwarzach im Pongau-St.Veit Bahnhof die Züge 25737 und 25739 mit geänderten Fahrzeiten.",
            ["01.11.2026", "29.10.2026", "30.10.2026", "31.10.2026"],
        ),
        # A section opening with "Am" (no such text yet; "Von" in the Hütteldorf notice of 17.10.2026).
        ("HALTAUSFALL: Am 12.10.2026 können zwischen A und B keine Züge halten.", ["12.10.2026"]),
        # A day without a full date after it stays unread.
        ("Wegen Bauarbeiten können zwischen A und B am 14.02. keine Züge fahren.", None),
    ],
)
def test_days_in_every_form_ÖBB_writes(lead: str, days: list[str] | None) -> None:
    assert _days(lead) == days


def test_a_recurring_closure_counts_on_each_of_its_days() -> None:
    item = _oebb("Wolfsberg in Ktn ↔ St.Paul im Lavanttal", WOLFSBERG, date(2026, 7, 2), date(2026, 11, 5))
    ranked = [day for day in (date(2026, 7, 2), date(2026, 7, 3), date(2026, 8, 6), date(2026, 9, 3),
                              date(2026, 10, 1), date(2026, 10, 2), date(2026, 11, 5))
              if _is_oebb_closure_day(item, _vienna(day.year, day.month, day.day))]
    assert ranked == [date(2026, 7, 2), date(2026, 8, 6), date(2026, 9, 3), date(2026, 10, 1), date(2026, 11, 5)]


def test_closure_hours_end_a_day_the_next_day_does_not_continue() -> None:
    r95 = _oebb("R 95: Wien Hauptbahnhof ↔ Felixdorf", R95, date(2026, 2, 10), date(2026, 11, 10))
    assert _is_oebb_closure_day(r95, _vienna(2026, 11, 10, 9))
    assert _is_oebb_closure_day(r95, _vienna(2026, 11, 10, 21))
    assert not _is_oebb_closure_day(r95, _vienna(2026, 11, 10, 22))
    s45 = _oebb("S 45: Wien Hütteldorf ↔ Wien Handelskai", S45, date(2026, 11, 1), date(2026, 11, 1))
    assert _is_oebb_closure_day(s45, _vienna(2026, 11, 1, 3))
    assert not _is_oebb_closure_day(s45, _vienna(2026, 11, 1, 5))
    # Every night from 01. to 16.11.: the next night's closure keeps the day, the last one ends at 04:30.
    nightly = _oebb_closure_days(
        "Wegen Bauarbeiten können zwischen Felixdorf Bahnhof und Wr.Neustadt Hbf von 01.11.2026 bis 16.11.2026 "
        "(jeweils 00:10 Uhr - 04:30 Uhr) keine Nahverkehrszüge fahren."
    ) or {}
    assert len(nightly) == 16 and nightly[date(2026, 11, 15)] is None and nightly[date(2026, 11, 16)] == time(4, 30)
    # Hours across midnight keep the whole days.
    across = _oebb_closure_days(
        "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof von 05.12.2026 bis 07.12.2026 "
        "(jeweils von 23:15 Uhr bis 04:40 Uhr) keine Nahverkehrszüge fahren."
    ) or {}
    assert sorted(across) == [date(2026, 12, 5), date(2026, 12, 6), date(2026, 12, 7)]
    assert set(across.values()) == {None}


def test_weekdays_in_the_plural_narrow_the_days() -> None:
    item = _oebb("Felixdorf ↔ Wr.Neustadt Hbf", FELIXDORF, date(2026, 11, 1), date(2026, 11, 16))
    days = _oebb_closure_days(_oebb_shown_sentences(item)[1][0]) or {}
    assert sorted(day.day for day in days) == [1, 2, 8, 9, 15, 16]
    assert not _is_oebb_closure_day(item, _vienna(2026, 11, 4))
    assert _is_oebb_closure_day(item, _vienna(2026, 11, 8))
    assert not _is_oebb_closure_day(item, _vienna(2026, 11, 9, 5))
