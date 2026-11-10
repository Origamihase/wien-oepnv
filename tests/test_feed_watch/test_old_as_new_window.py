"""`alte-meldung-als-neu` meldet nur, was der Bau erst seit kurzem kennt.

Die Regel sucht eine alte Meldung, die unter der Identität einer frischen
läuft (Linie 25 am 09.10.2026). Ohne Blick auf den Bau traf sie auch einen
lange bekannten Eintrag mit alter Meldung, sobald seine Zeitzeile „[Heute]“ sagt:
Arbeiten am letzten Tag ihres gemeldeten Zeitraums. Die Feldformen stammen
aus den Ständen von ``docs/feed.xml`` der genannten Tage.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo

import pytest

from scripts import feed_watch as fw
from src.feed_build.item_meta import _CURRENT_INCIDENT_WINDOW

from ._support import NBSP, _feed, _filler, _item, _rules, _run

VIENNA = ZoneInfo("Europe/Vienna")

R95 = _item(
    "R95: Wien Hauptbahnhof ↔ Felixdorf",
    "Wegen Bauarbeiten können am 10.11.2026 (jeweils 14:00 Uhr - 22:00 Uhr) keine R95-Züge fahren. "
    "Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet. [Heute]",
    guid="https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&835981",
    pub="Fri, 19 Dec 2025 10:07:13 +0100",
    first_seen="Sun, 24 May 2026 05:30:44 +0200",
    starts_at="Tue, 10 Feb 2026 00:00:00 +0100",
)

BUS_54 = _item(
    "54A/54B: Bauarbeiten",
    "Wegen Straßenbauarbeiten im Bereich Einsiedeleigasse werden die Linien 54A und 54B umgeleitet. [Heute]",
    guid="368e944a9eb2af06010f464af138f6047466adea7e5bb23fd6f4ce14094d53dc",
    pub="Wed, 21 Oct 2026 07:33:00 +0200",
    first_seen="Wed, 28 Oct 2026 17:01:03 +0100",
    starts_at="Tue, 27 Oct 2026 00:00:00 +0100",
)


def _line_25(first_seen: str) -> str:
    return _item(
        "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        "Züge halten Donaufelder Straße 175-177. [Heute]",
        guid="1fada7614a788ccf1a33277fc05d810e42ffa22f80e787d1c7f2415eb1f42b8c",
        pub="Wed, 07 Oct 2026 00:00:18 +0200",
        first_seen=first_seen,
        starts_at="Wed, 07 Oct 2026 00:00:18 +0200",
    )


def _old_as_new(result: fw.Result) -> list[fw.Finding]:
    return [finding for finding in result.findings if finding.rule_id == "alte-meldung-als-neu"]


@pytest.mark.parametrize(
    "built",
    ["Tue, 10 Nov 2026 00:01:11 +0100", "Tue, 10 Nov 2026 17:01:27 +0100"],
    ids=["first-build-of-the-day", "afternoon"],
)
def test_planned_works_known_for_months_are_no_new_item_on_their_last_day(built: str) -> None:
    """10.11.2026, from the 00:01 build: works on single days since February, first seen in May."""
    result = _run(_feed([R95] + _filler(9), built=built))
    assert _old_as_new(result) == []


def test_works_first_seen_two_days_before_their_last_day_are_no_new_item() -> None:
    """30.10.2026, builds 00:00 to 14:00: the reported period ended that day at 15:00, in the feed since 28.10."""
    result = _run(_feed([BUS_54] + _filler(9), built="Fri, 30 Oct 2026 00:00:46 +0100"))
    assert _old_as_new(result) == []


@pytest.mark.parametrize(
    "built",
    ["Fri, 09 Oct 2026 12:01:13 +0200", "Fri, 09 Oct 2026 22:02:24 +0200"],
    ids=["first-build-with-the-ticker", "last-build-with-the-ticker"],
)
def test_line_25_stays_found_in_every_build_of_its_day(built: str) -> None:
    """09.10.2026, 28 feed states from 12:01 to 22:02: first seen at 09:36 of that day."""
    result = _run(_feed([_line_25("Fri, 09 Oct 2026 09:36:00 +0200")] + _filler(9), built=built))
    assert _rules(result) == {"alte-meldung-als-neu"}
    assert "im Feed erstmals 09.10. 09:36" in result.findings[0].text


@pytest.mark.parametrize(
    ("age", "found"),
    [
        (timedelta(0), True),
        (timedelta(hours=23, minutes=59), True),
        (timedelta(hours=24), True),
        (timedelta(hours=24, seconds=1), False),
        (timedelta(hours=40), False),
    ],
    ids=["this-build", "just-inside", "at-the-limit", "just-past", "the-day-before"],
)
def test_the_window_ends_a_day_after_the_first_sighting(age: timedelta, found: bool) -> None:
    built = datetime(2026, 10, 9, 22, 2, tzinfo=VIENNA)
    first_seen = format_datetime(built - age)
    result = _run(_feed([_line_25(first_seen)] + _filler(9), built=format_datetime(built)))
    assert bool(_old_as_new(result)) is found


@pytest.mark.parametrize(
    ("first_seen", "found"),
    [("Sat, 24 Oct 2026 09:00:00 +0200", False), ("Sat, 24 Oct 2026 10:00:00 +0200", True)],
    ids=["same-clock-25-hours", "24-hours"],
)
def test_the_window_counts_real_hours_at_the_end_of_summer_time(first_seen: str, found: bool) -> None:
    """25.10.2026 has 25 hours: 09:00 the day before is 25 hours back, not 24."""
    ticker = _item(
        "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        "Züge halten Donaufelder Straße 175-177. [Heute]",
        pub="Thu, 22 Oct 2026 00:00:18 +0200",
        first_seen=first_seen,
        starts_at="Thu, 22 Oct 2026 00:00:18 +0200",
    )
    result = _run(_feed([ticker] + _filler(9), built="Sun, 25 Oct 2026 09:00:00 +0100"))
    assert bool(_old_as_new(result)) is found


def test_an_announcement_met_yesterday_is_no_finding_on_its_day() -> None:
    """Inside the window, the begin decides: the marathon was announced, it begins after its first sighting."""
    marathon = _item(
        "18: LCC-Herbstmarathon am 11.10.2026",
        "Linie 18: Betrieb nur zwischen Burggasse und Schlachthausgasse U. [Heute]",
        pub="Mon, 28 Sep 2026 00:00:00 +0200",
        first_seen="Sat, 10 Oct 2026 12:00:00 +0200",
        starts_at="Sun, 11 Oct 2026 00:00:00 +0200",
    )
    result = _run(_feed([marathon] + _filler(9), built="Sun, 11 Oct 2026 09:00:00 +0200"))
    assert _old_as_new(result) == []


def test_an_old_message_without_a_begin_is_found() -> None:
    ticker = _item(
        "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        "Züge halten Donaufelder Straße 175-177. [Heute]",
        pub="Wed, 07 Oct 2026 00:00:18 +0200",
        first_seen="Fri, 09 Oct 2026 09:36:00 +0200",
    )
    result = _run(_feed([ticker] + _filler(9), built="Fri, 09 Oct 2026 12:01:13 +0200"))
    assert len(_old_as_new(result)) == 1


def test_an_old_message_shown_as_running_since_a_clock_time_is_found() -> None:
    """Both forms of today's time line count: "[Heute]" and "[Seit hh:mm]"."""
    ticker = _item(
        "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        f"Züge halten Donaufelder Straße 175-177. [Seit{NBSP}09:36]",
        pub="Wed, 07 Oct 2026 00:00:18 +0200",
        first_seen="Fri, 09 Oct 2026 09:36:00 +0200",
        starts_at="Wed, 07 Oct 2026 00:00:18 +0200",
    )
    result = _run(_feed([ticker] + _filler(9), built="Fri, 09 Oct 2026 12:01:13 +0200"))
    assert len(_old_as_new(result)) == 1


@pytest.mark.parametrize(
    ("pub", "found"),
    [("Thu, 08 Oct 2026 09:36:00 +0200", False), ("Thu, 08 Oct 2026 09:35:59 +0200", True)],
    ids=["a-day-older", "more-than-a-day-older"],
)
def test_the_message_has_to_be_more_than_a_day_older_than_the_first_sighting(pub: str, found: bool) -> None:
    ticker = _item(
        "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        "Züge halten Donaufelder Straße 175-177. [Heute]",
        pub=pub,
        first_seen="Fri, 09 Oct 2026 09:36:00 +0200",
    )
    result = _run(_feed([ticker] + _filler(9), built="Fri, 09 Oct 2026 12:01:13 +0200"))
    assert bool(_old_as_new(result)) is found


@pytest.mark.parametrize(
    ("starts_at", "found"),
    [("Thu, 08 Oct 2026 09:36:00 +0200", False), ("Thu, 08 Oct 2026 09:35:59 +0200", True)],
    ids=["began-a-day-before", "began-more-than-a-day-before"],
)
def test_the_begin_has_to_be_more_than_a_day_before_the_first_sighting(starts_at: str, found: bool) -> None:
    ticker = _item(
        "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        "Züge halten Donaufelder Straße 175-177. [Heute]",
        pub="Wed, 07 Oct 2026 00:00:18 +0200",
        first_seen="Fri, 09 Oct 2026 09:36:00 +0200",
        starts_at=starts_at,
    )
    result = _run(_feed([ticker] + _filler(9), built="Fri, 09 Oct 2026 12:01:13 +0200"))
    assert bool(_old_as_new(result)) is found


def test_a_first_sighting_after_the_build_counts_as_new() -> None:
    """Clocks of two steps may differ by seconds: such an item is new, not long known."""
    built = datetime(2026, 10, 9, 12, 1, 13, tzinfo=VIENNA)
    ticker = _line_25(format_datetime(built + timedelta(seconds=20)))
    result = _run(_feed([ticker] + _filler(9), built=format_datetime(built)))
    assert len(_old_as_new(result)) == 1


def test_a_feed_without_build_time_is_judged_at_the_time_of_the_run() -> None:
    xml = _feed([_line_25("Fri, 09 Oct 2026 09:36:00 +0200")] + _filler(9)).replace(
        "<lastBuildDate>Fri, 09 Oct 2026 16:00:00 +0200</lastBuildDate>", ""
    )
    feed = fw.parse_feed(xml)
    assert feed.built is None

    def run(now: datetime) -> fw.Result:
        return fw.evaluate(feed, now=now, state={}, oebb_cache={}, sources=None)

    assert len(_old_as_new(run(datetime(2026, 10, 9, 16, 0, tzinfo=VIENNA)))) == 1
    assert _old_as_new(run(datetime(2026, 10, 12, 16, 0, tzinfo=VIENNA))) == []


def test_the_window_is_as_long_as_the_builders_for_current_incidents() -> None:
    """Two windows of one length: how long an item is new here, how long an incident leads there."""
    assert fw.NEW_IN_FEED == _CURRENT_INCIDENT_WINDOW
