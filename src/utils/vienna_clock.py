"""Source time stamps whose offset belongs to the other season.

ÖBB and Wiener Linien both write the wall clock of a time correctly but
can attach the Vienna offset of the wrong season. Read as given, such a
time is an hour off, and a time near midnight lands on the neighbouring
day. :func:`vienna_wall_clock` reads the wall clock as Europe/Vienna time
instead (docs/architecture.md, "Zeitumstellung").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

VIENNA_TZ = ZoneInfo("Europe/Vienna")
# Central European Time and Central European Summer Time.
VIENNA_OFFSETS = frozenset({timedelta(hours=1), timedelta(hours=2)})


def vienna_wall_clock(dt: datetime) -> datetime:
    """``dt`` with the Vienna offset its wall clock had, when the source sent the other one.

    ÖBB stamps every ``pubDate`` with the offset valid at the time of the
    fetch, not at the time of publication. At the first fetch after the
    clocks went forward on 2026-03-29, all 30 running messages kept their
    wall clock and switched from +01:00 to +02:00 ("27 Mar 2026 11:42:37"),
    and all 38 winter messages cached since then, up to October, carry
    +02:00 ("19 Dec 2025 10:07:13 +0200").

    Wiener Linien gave every summer time the winter offset while winter
    time was in force: on 20.03.2026 all 17 summer times in its answer
    carried +01:00, among them the 11:11 placeholder ends
    "2027-06-30T11:11:00+01:00" and the date ends "2026-07-11T00:00:00+01:00"
    and "2026-05-31T23:56:00+01:00". Their wall clock is what WL meant: read
    as given, the 11:11 end would be 12:11 and no longer count as WL's
    placeholder, and the 23:56 end would fall on 01.06. In summer time WL's
    offsets are right for both seasons (none wrong among the 7,775 winter
    times of every 20th cache state from May to October 2026, nor among the
    53 of its raw answers since 04.10.), so the fault is expected back once
    winter time begins on 25.10.2026.

    The wall clock is what the source recorded; it is read as Vienna time.
    A stamp whose offset fits its wall clock stays as it is, which also
    keeps the given offset in the hour that repeats when summer time ends,
    the only case where the offset is needed. Offsets other than Vienna's
    (+00:00 in tests) are left alone.
    """
    offset = dt.utcoffset()
    if offset not in VIENNA_OFFSETS:
        return dt
    wall = dt.replace(tzinfo=None)
    fitting = {wall.replace(tzinfo=VIENNA_TZ, fold=fold).utcoffset() for fold in (0, 1)}
    if offset in fitting:
        return dt
    corrected = wall.replace(tzinfo=VIENNA_TZ).utcoffset()
    return dt if corrected is None else wall.replace(tzinfo=timezone(corrected))
