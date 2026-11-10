"""Direction coverage note of the statistics dashboard.

Moved verbatim from ``scripts/generate_markdown_stats.py``, which
re-exports every name.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from src.markdown_stats.rows import VIENNA_TZ, StammstreckeRow
from src.utils.stats import STAMMSTRECKE_DIRECTIONS
from src.utils.text import escape_markdown_cell


# The coverage note names NO cause. It used to carry an operator-maintained
# one ("Streckensperre – Bauarbeiten und Kabelbrand-Folgen"), which was
# accurate for the 2026 outage and wrong for every other way a direction can
# fall silent: a timetable change, a provider renaming the platform, our own
# monitor failing. The note states the observation instead, in one sentence
# shape that fits every case — one direction, the other, or both.
#
# Measurement point of the Stammstrecke observations. Named here so the note
# and the website say the same thing; mirrors the footer wording in
# ``docs/site.html`` ("Stammstrecken-Beobachtungen am Wien Hbf").
COVERAGE_ORIGIN_LABEL: Final = "Wien Hbf"


def _last_seen_suffix(
    missing: list[str], latest: dict[str, datetime]
) -> str:
    """Parenthetical naming when the silent direction(s) last reported.

    One direction gets the short form; several get one date each, because a
    single date would otherwise stand for a sentence covering all of them.
    Directions that never reported are simply left out — there is no "last"
    for them.
    """
    dated = [d for d in missing if d in latest]
    if not dated:
        return ""
    if len(dated) == 1 and len(missing) == 1:
        return f" (zuletzt am {latest[dated[0]].strftime('%d.%m.%Y')})"
    inner = ", ".join(
        f"{escape_markdown_cell(d)} am {latest[d].strftime('%d.%m.%Y')}"
        for d in dated
    )
    return f" (zuletzt {inner})"


def _last_seen_per_direction(
    rows: list[StammstreckeRow],
) -> dict[str, datetime]:
    """Newest timestamp per direction across *rows*."""
    latest: dict[str, datetime] = {}
    for row in rows:
        current = latest.get(row.direction)
        if current is None or row.timestamp > current:
            latest[row.direction] = row.timestamp
    return latest


# The banner waits ONE HOUR without a journey before it appears, and clears
# as soon as the direction reports again. One hour on its own is far below
# the corridor's own rhythm, though: observations arrive every ~30 min at
# best, the p99 gap per direction is 3.5 h, and the Stammstrecke pauses
# entirely for about 3:41 h every night (01:12 -> 04:53, strikingly
# consistent). Replayed over 4 324 half-hourly ticks of healthy 2026
# operation, a bare "quiet for 1 h" rule raised the banner at 10.6 % of them
# — 87 % in the small hours, when nothing is running by design. A caveat that
# shows up every night is one readers stop seeing.
#
# So the hour is measured against EVIDENCE THAT TRAINS ARE RUNNING, not
# against the clock alone, and the evidence differs by case:
#
#   * One direction quiet, the other reporting — the peer is the evidence.
#     The quiet direction is named once a peer has logged
#     ``DIRECTION_SILENCE_NOTICE_PEER_ROWS`` journeys since its own last one:
#     trains demonstrably ran through the corridor and this direction was not
#     among them. Peer rows accumulate and never expire, so the banner does
#     not flicker off during the nightly pause of an ongoing outage (replay:
#     zero interruptions across all 1 628 ticks since 2026-08-14).
#   * Every direction quiet — no peer is left to compare against, and a dark
#     corridor at 03:00 is a timetable, not a fault. Only a stretch longer
#     than any healthy pause says otherwise.
#
# With the values below the replay raises the banner at 3 of 4 324 healthy
# ticks, and all three are the same episode: 2026-08-14 06:27 -> 11:27, a real
# five-hour northbound gap on the morning the outage set in. Outside that,
# normal operation produces no banner at all. The 2026-08-14 outage is caught
# 3:03 h after the last northbound journey and stays caught.
#
# Note what the rule can and cannot claim. With a peer reporting, "no
# journeys in this direction" is sound: the peer proves our own collector was
# alive. With the whole corridor dark the ledger alone cannot tell "no trains
# ran" from "we observed nothing", so the corridor route additionally needs
# the monitor's own poll record to show a working fetch
# (:mod:`src.utils.stammstrecke_poll`). Operator decision 2026-10-10: a broken
# fetch turns the health check red and is not the readers' business; a
# working fetch without trains is, and goes on the website — "Aktuell keine
# Fahrten" is then a claim the monitor can actually make.
#: Hours a direction must go without a journey before the banner names it.
DIRECTION_SILENCE_NOTICE_HOURS: Final = 1.0
#: Journeys a peer direction must log in that stretch to prove service is
#: running. Each extra row buys quiet at ~30 min of detection delay: at 4 the
#: replay shows 4 episodes, at 5 three, at 6 only the outage onset itself.
#: Six is the first value with no banner at all in normal operation.
DIRECTION_SILENCE_NOTICE_PEER_ROWS: Final = 6
#: Hours the WHOLE corridor must be quiet before the banner names every
#: direction — needed because the peer rule cannot fire with no peer left.
#: The nightly pause is a consistent 3:41 h and the longest corridor-wide
#: silence healthy 2026 operation produced was 7:45 h (2026-08-06 17:27, a
#: collection outage rather than a timetable). Eight hours is the smallest
#: whole-hour threshold clear of both, at 2.2x the nightly pause, and the
#: replay never reaches it outside a real gap. Deliberately far above the
#: one-hour per-direction rule: a dark corridor is normal every night, a dark
#: *direction* beside a running one never is.
CORRIDOR_SILENCE_NOTICE_HOURS: Final = 8.0


def find_silent_coverage_directions(
    rows: list[StammstreckeRow],
    *,
    now: datetime,
    directions: tuple[str, ...] = STAMMSTRECKE_DIRECTIONS,
    fetch_working: bool = True,
) -> list[str]:
    """Directions with no journey for long enough to be worth saying so.

    Returns them in *directions* order. Empty when every direction has
    reported within the hour, and empty for a direction that was never
    measured at all — a fresh clone has nothing that stopped.

    See the constants above for why the hour is gated on evidence that
    trains are running rather than applied to the clock alone.
    *fetch_working* gates the whole-corridor route: without a working poll
    a dark corridor is a broken fetch, not a corridor without trains.
    """
    latest = _last_seen_per_direction(rows)
    threshold = timedelta(hours=DIRECTION_SILENCE_NOTICE_HOURS)
    quiet = [
        d
        for d in directions
        if d in latest and now - latest[d] >= threshold
    ]
    if not quiet:
        return []

    silent = [
        d
        for d in quiet
        if sum(
            1
            for row in rows
            if row.direction != d
            and row.direction in directions
            and latest[d] < row.timestamp <= now
        )
        >= DIRECTION_SILENCE_NOTICE_PEER_ROWS
    ]
    if silent:
        return silent

    # No peer to compare against: only a stretch longer than any healthy
    # pause distinguishes a stopped corridor from a sleeping one — and only a
    # working fetch distinguishes it from a monitor that sees nothing.
    if not fetch_working:
        return []
    if len(quiet) == len([d for d in directions if d in latest]):
        corridor = timedelta(hours=CORRIDOR_SILENCE_NOTICE_HOURS)
        if all(now - latest[d] >= corridor for d in quiet):
            return quiet
    return []


def render_direction_coverage_note(
    window_rows: list[StammstreckeRow],
    *,
    now: datetime,
    all_rows: list[StammstreckeRow] | None = None,
    directions: tuple[str, ...] = STAMMSTRECKE_DIRECTIONS,
    fetch_working: bool = True,
    yearly: bool = False,
) -> str:
    """Return a Markdown warning while a direction has no journeys.

    Audit B.1: the northbound direction stopped reporting on 2026-08-14, but
    README and dashboard kept presenting the surviving southbound sample as a
    whole-corridor figure — in the time series the measurement gap reads like a
    drop in disruptions. This note makes the restricted coverage explicit.

    Derived entirely from the data, never hard-coded. It names no cause: the
    ledger records that journeys stopped, never why. The trigger is
    :func:`find_silent_coverage_directions` — one hour without a journey,
    gated on evidence that trains are actually running — and the note vanishes
    on its own as soon as the direction reports again. That self-clearing
    behaviour is a requirement, not a nicety: the closure is temporary and the
    restart has to be picked up without anyone editing Markdown.

    The note is built from two independent statements, because they can be
    true at different times:

    * The **lead** is about service right now — no journeys in this direction
      for at least an hour. It is the whole note while the figures below are
      still sound, which they are for the first hours of a gap.
    * The **caveat** is about the figures, and is added only once the silence
      has actually hollowed out the window: a direction that fell quiet an
      hour ago still contributes thousands of rows to a 30-day window, and
      calling those figures a part-corridor sample would be false. Once a
      direction really is absent from the window the figures are either a
      part-corridor sample (some direction still reporting) or simply old
      (none is) — a reader needs different things from those two.

    The caveat is judged against the rows the figures below are made of,
    ``window_rows``: the 30-day window for the README blocks, the whole year
    for ``docs/statistik.md`` (``yearly=True``). The yearly tables still hold
    a direction that fell silent mid-year (Praterstern 3,831 rows up to
    14.08.2026), so there the note says how far they reach instead of
    claiming that they cover the other direction only, which the table right
    under it contradicted.

    Returns an empty string when every direction has reported within the hour,
    and when no canonical direction was ever measured (a fresh clone has
    nothing to caveat).
    """
    ledger = list(all_rows) if all_rows else window_rows
    missing = find_silent_coverage_directions(
        ledger, now=now, directions=directions, fetch_working=fetch_working
    )
    if not missing:
        return ""

    latest = _last_seen_per_direction(ledger)
    missing_txt = " und ".join(f"**{escape_markdown_cell(d)}**" for d in missing)
    lead = (
        "> ⚠️ **Eingeschränkte Abdeckung:** Aktuell keine Fahrten von "
        f"{COVERAGE_ORIGIN_LABEL} Richtung {missing_txt} auf der Stammstrecke"
        f"{_last_seen_suffix(missing, latest)}."
    )

    # Does the silence reach the figures below, or only the present moment?
    present = {row.direction for row in window_rows}
    absent = [d for d in missing if d not in present]
    if not absent:
        if yearly:
            return (
                f"{lead} Die Jahreszahlen unten enthalten Richtung "
                f"{missing_txt} daher nur bis dahin.\n\n"
            )
        return f"{lead}\n\n"

    covered = [d for d in directions if d in present]
    if covered:
        covered_txt = " und ".join(f"**{escape_markdown_cell(d)}**" for d in covered)
        return (
            f"{lead} Die Zahlen unten decken daher nur Richtung "
            f"{covered_txt} ab — **kein Korridor-Gesamtwert**.\n"
            "\n"
        )
    return (
        f"{lead} Die Zahlen unten beruhen daher ausschließlich auf "
        "**älteren Daten**.\n"
        "\n"
    )


def render_finished_year_coverage_note(
    year_rows: list[StammstreckeRow],
    *,
    year: int,
    directions: tuple[str, ...] = STAMMSTRECKE_DIRECTIONS,
) -> str:
    """The same caveat for the page of a finished *year*.

    ``docs/statistik-<Jahr>.md`` shows a year that is over, so "Aktuell keine
    Fahrten" has nothing to say there, and the page must read the same on
    whatever day it is written: the note is judged at the end of the year
    and against the year's rows alone. A direction is named when the rule of
    :func:`find_silent_coverage_directions` holds at that moment (the other
    direction went on reporting, or the whole corridor was quiet for longer
    than any normal pause) and its last journey lies before the year's last
    day; the note gives dates, and a direction that reported on 31.12.
    reaches the end of the year in them. Until 2026-11-10 the archive page
    carried no note at all: Praterstern, silent since 14.08.2026, stood
    under Meidling with half its rows and no word why, while the dashboard
    of the same year said so until its last day.

    A direction without any row in the year is the fallback of
    ``_format_directions_section`` ("In diesem Zeitraum keine Fahrten").
    """
    year_end = datetime(year + 1, 1, 1, tzinfo=VIENNA_TZ)
    latest = _last_seen_per_direction(year_rows)
    last_day = (year_end - timedelta(days=1)).date()
    missing = [
        d
        for d in find_silent_coverage_directions(
            year_rows, now=year_end, directions=directions
        )
        if latest[d].astimezone(VIENNA_TZ).date() < last_day
    ]
    if not missing:
        return ""
    names = " und ".join(f"**{escape_markdown_cell(d)}**" for d in missing)
    if len(missing) == 1:
        return (
            "> ⚠️ **Eingeschränkte Abdeckung:** Von "
            f"{COVERAGE_ORIGIN_LABEL} Richtung {names} sind auf der "
            "Stammstrecke Fahrten nur bis "
            f"{latest[missing[0]].astimezone(VIENNA_TZ).strftime('%d.%m.%Y')} "
            f"erfasst. Die Jahreszahlen unten enthalten Richtung {names} "
            "daher nur bis dahin.\n\n"
        )
    until = ", ".join(
        f"{escape_markdown_cell(d)} bis "
        f"{latest[d].astimezone(VIENNA_TZ).strftime('%d.%m.%Y')}"
        for d in missing
    )
    return (
        "> ⚠️ **Eingeschränkte Abdeckung:** Von "
        f"{COVERAGE_ORIGIN_LABEL} Richtung {names} sind auf der Stammstrecke "
        f"Fahrten nicht bis zum Jahresende erfasst ({until}). Die "
        "Jahreszahlen unten enthalten diese Richtungen daher nur bis "
        "dahin.\n\n"
    )
