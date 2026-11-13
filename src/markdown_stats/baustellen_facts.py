"""District, period and counts of the year's Baustellen sites.

Holds how the page and the review write a site's district and period, and
what the script counts for the facts block (:func:`facts_block`,
:func:`fact_counts`, :func:`months_touched`). Moved verbatim out of
``src/markdown_stats/baustellen_year.py``, which re-exports the names.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from typing import Final

from src.markdown_stats.baustellen_sites import BaustelleSite, _one_line
from src.utils.stats import to_vienna

__all__ = [
    "DISTRICT_NAMES", "FIRST_LEDGER_YEAR", "MEASURE_NOT_GIVEN", "MEASURE_OTHER", "MONTH_NAMES",
    "district_label", "district_numbers", "fact_counts", "facts_block", "months_touched",
    "period_text", "recorded_since",
]

# The year the ledger was started in: its first row is of 24.05.2026, the
# first commit of the Baustellen cache. Every later year is recorded from
# 1 January, and a cache write without a site writes no row, so the first
# row of a later year does not say when recording began (review of
# 2026-11-10); only for this year do page and facts name the first day.
FIRST_LEDGER_YEAR: Final = 2026

# The 23 districts of Vienna in their order: index 0 is the 1st district.
# The ledger holds the number as the city writes it ("3"); the page and the
# review name the district.
DISTRICT_NAMES: Final[tuple[str, ...]] = (
    "Innere Stadt", "Leopoldstadt", "Landstraße", "Wieden", "Margareten",
    "Mariahilf", "Neubau", "Josefstadt", "Alsergrund", "Favoriten",
    "Simmering", "Meidling", "Hietzing", "Penzing", "Rudolfsheim-Fünfhaus",
    "Ottakring", "Hernals", "Währing", "Döbling", "Brigittenau",
    "Floridsdorf", "Donaustadt", "Liesing",
)
_DISTRICT_NUMBER_RE: Final = re.compile(r"(?<!\d)\d{1,4}(?!\d)")
# How the facts block names a measure (see _measure_label): words, spaces
# and hyphens only, and not longer than this.
_MEASURE_NAME_RE: Final = re.compile(r"[^\W\d_]+(?:[ -]+[^\W\d_]+)*")
_MEASURE_NAME_MAX_LEN: Final = 60
MEASURE_NOT_GIVEN: Final = "ohne Angabe"
MEASURE_OTHER: Final = "andere Angabe"
# The months as an Austrian text names them; index 0 is January.
MONTH_NAMES: Final[tuple[str, ...]] = (
    "Jänner", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


# ---- district and period as the page writes them ------------------------------


def district_numbers(district: str) -> tuple[int, ...]:
    """The districts (1 to 23) a ledger cell names, in its order.

    The city writes the number ("3", "2, 3"); a postal code ("1030") names
    the same district. Anything else names none.
    """
    found: list[int] = []
    for raw in _DISTRICT_NUMBER_RE.findall(district):
        number = int(raw)
        if 1010 <= number <= 1230 and number % 10 == 0:
            number = (number - 1000) // 10
        if 1 <= number <= len(DISTRICT_NAMES) and number not in found:
            found.append(number)
    return tuple(found)


def district_label(district: str) -> str:
    """``3`` as ``3., Landstraße``; a cell without a district number as written."""
    numbers = district_numbers(district)
    if not numbers:
        return district
    return "; ".join(f"{number}., {DISTRICT_NAMES[number - 1]}" for number in numbers)


def _day(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def period_text(site: BaustelleSite) -> str:
    """The site's period: ``25.05.2026 – 06.09.2026``, an open end as ``offen``."""
    if site.starts is not None:
        return f"{_day(site.starts)} – {_day(site.ends) if site.ends is not None else 'offen'}"
    if site.ends is not None:
        return f"bis {_day(site.ends)}"
    return "nicht angegeben"


# ---- what the script counts ---------------------------------------------------


def recorded_since(sites: Sequence[BaustelleSite], year: int) -> date | None:
    """The Vienna day the record of *year* begins, where the ledger can say it.

    That is the first sighting of the year, and only for
    :data:`FIRST_LEDGER_YEAR`: there the ledger began with the first cache
    commit. In a later year the first sighting is merely the day the first
    site with consequences appeared, while recording ran from 1 January.
    ``to_vienna``, as in :func:`months_touched`: a naive time stamp is
    Vienna time, not the time zone of the machine that renders the page.
    """
    if year != FIRST_LEDGER_YEAR or not sites:
        return None
    return to_vienna(min(site.first_seen for site in sites)).date()


def _measure_label(measure: str) -> str:
    """The measure as the facts block may name it.

    The facts block stands in the prompt outside the frame that marks the
    city's texts as data, and the measure is the city's free text. It is
    named there only when it is words, spaces and hyphens and at most
    :data:`_MEASURE_NAME_MAX_LEN` characters ("Straßenbau - Radweg
    Neuherstellung"); anything else is counted without its wording.
    """
    name = _one_line(measure)
    if not name:
        return MEASURE_NOT_GIVEN
    if len(name) <= _MEASURE_NAME_MAX_LEN and _MEASURE_NAME_RE.fullmatch(name):
        return name
    return MEASURE_OTHER


def _measure_order(item: tuple[str, int]) -> tuple[int, int, str]:
    """Most frequent first, then by name; the two labels without a wording last."""
    label, count = item
    last = (MEASURE_OTHER, MEASURE_NOT_GIVEN)
    return (last.index(label) + 1 if label in last else 0, -count, label)


def _tally(head: str, counted: Sequence[tuple[str, int]], empty: str) -> tuple[str, tuple[int, ...]]:
    """One facts line ``head: name: count, …`` (or *empty*) with the counts it prints."""
    text = ", ".join(f"{name}: {count}" for name, count in counted) or empty
    return f"{head}: {text}", tuple(count for _, count in counted)


def _fact_lines(sites: Sequence[BaustelleSite], year: int) -> list[tuple[str, tuple[int, ...]]]:
    """Every line of the facts block, each with the counts it prints.

    One list feeds :func:`facts_block` and :func:`fact_counts`, so the
    guard's set of counts cannot drift from what the model was given. The
    year and the day the record begins are no counts.
    """
    months = Counter(site.starts.month for site in sites if site.starts is not None and site.starts.year == year)
    districts = Counter(number for site in sites for number in district_numbers(site.district))
    measures = Counter(_measure_label(site.measure) for site in sites)
    before = sum(1 for site in sites if site.starts is not None and site.starts.year < year)
    after = sum(1 for site in sites if site.starts is not None and site.starts.year > year)
    unknown = sum(1 for site in sites if site.starts is None)
    lines: list[tuple[str, tuple[int, ...]]] = [
        (f"- Jahr: {year}", ()),
        (f"- Baustellen mit Folgen für den öffentlichen Verkehr: {len(sites)}", (len(sites),)),
    ]
    since = recorded_since(sites, year)
    if since is not None and since > date(year, 1, 1):
        # Without it the model takes the counts for the whole year (review of 2026-11-10).
        lines.append((f"- Erfasst seit: {_day(since)} (Baustellen, die davor endeten, fehlen in den Zahlen)", ()))
    lines.append(_tally(
        f"- Beginn im Jahr {year}, je Monat", [(MONTH_NAMES[month - 1], months[month]) for month in sorted(months)], "keiner",
    ))
    lines.append((f"- Beginn vor {year}: {before}", (before,)))
    if after:
        lines.append((f"- Beginn nach {year}: {after}", (after,)))
    if unknown:
        lines.append((f"- Beginn nicht angegeben: {unknown}", (unknown,)))
    by_count = sorted(districts, key=lambda number: (-districts[number], number))
    lines.append(_tally(
        "- Baustellen je Bezirk",
        [(f"{number}. Bezirk ({DISTRICT_NAMES[number - 1]})", districts[number]) for number in by_count],
        "keine Angabe",
    ))
    lines.append(_tally("- Baustellen je Maßnahme", sorted(measures.items(), key=_measure_order), "keine Angabe"))
    return lines


def facts_block(sites: Sequence[BaustelleSite], *, year: int) -> str:
    """The facts the script counted: the year, the sites, their starts per month, districts and measures.

    The model gets them with the prompt, and the guard accepts their
    numbers besides those of the data: a count the model could only guess
    is given to it instead. The prompt asks which measures were frequent,
    so they are counted here too; without that line the true "5 Gleisbau"
    of 2026 was rejected because no 5 stood anywhere in the data. For the
    year the ledger began in, one line says since when the sites are
    recorded (:func:`recorded_since`).
    """
    return "\n".join(line for line, _ in _fact_lines(sites, year))


def fact_counts(sites: Sequence[BaustelleSite], *, year: int) -> frozenset[int]:
    """Every count the facts block prints, from the same computation as its lines.

    The total, the starts per month, before, after and unknown, the sites
    per district and per measure. The guard of ``year_review`` takes a
    number in digits directly before "Baustelle(n)" only from this set: a
    number that merely stands somewhere in the data ("21., Floridsdorf")
    is no count of sites. The year, the number of a district and the day
    the record begins are not in it unless they equal a count.
    """
    return frozenset(count for _, counts in _fact_lines(sites, year) for count in counts)


def months_touched(sites: Sequence[BaustelleSite], year: int) -> set[int]:
    """The months of *year* that the period of at least one site touches.

    A site without a start counts from the day it was first recorded, a
    site without an end to the end of the year.
    """
    first_day, last_day = date(year, 1, 1), date(year, 12, 31)
    months: set[int] = set()
    for site in sites:
        start = max(site.starts or to_vienna(site.first_seen).date(), first_day)
        end = min(site.ends or last_day, last_day)
        if start <= end:
            months.update(range(start.month, end.month + 1))
    return months
