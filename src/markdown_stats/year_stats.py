"""The section "Das Jahr im Überblick" of a year page, ``docs/statistik-<Jahr>.md``.

Michael's decision of 2026-11-10 17:44 (Europe/Vienna): the page of a
finished year summarises all cancellations (Ausfälle) and delays
(Verspätungen) in a statistic. Until then the page held the year totals
and the distributions per weekday and hour, but no month, no single day
and no highest delay (review of 2026-11-10).

:func:`render_year_overview` adds that: the recorded period of each ledger,
all twelve months, the delays per direction, and the days with the highest
mean delay and with the most cancellations. It is pure (no file, no clock):
the caller loads the rows with ``collect_year_data`` and gets Markdown lines
back.

Days and months are those of Europe/Vienna, taken from each row's timestamp
and not from its ``weekday`` column: the column is what the writer stored
(or what a hand-added row says), the timestamp is the measurement.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Final, TypeVar

from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES
from src.markdown_stats.markdown_report import _DASHBOARD_FIELD_MAX_LEN
from src.markdown_stats.rows import (
    AusfallRow,
    StammstreckeRow,
    StoerungRow,
    ausfall_line_label,
)
from src.utils.stats import WEEKDAY_LABELS, to_vienna
from src.utils.text import escape_markdown_cell, normalise_markdown_text

# A day enters the ranking of mean delays only with this many observations.
# The monitor writes one row per direction and poll, about every 30 minutes,
# so a full day has several dozen; a day with a handful (an outage of the
# monitor, the first hours of a ledger) would lead the table on the strength
# of two or three polls. The page states the number under the table.
MIN_DAY_OBSERVATIONS: Final = 10

# Rows of the two day tables. The page says under each table how many days
# it chose from, so the cut is never silent.
TOP_DAYS: Final = 5

# The ledger holds two directions (``STAMMSTRECKE_DIRECTIONS``). The cap only
# keeps a damaged ledger with many spellings from flooding the page (docs
# pages end at 700 lines, tests/test_file_line_limit.py); what it cuts is
# named in a line under the table.
MAX_DIRECTION_ROWS: Final = 6

# A cell without a value: the month lies outside the recorded period of its
# data series, or there is no observation to take a mean or a maximum from.
# Never used for "nothing happened", which is 0.
NOT_RECORDED: Final = "–"

# One decimal, rounded half up, for every delay the section prints
# (:func:`_fmt_delay`). The context is that wide because a float has up to
# 309 digits before the point: with the 28 digits of the default context a
# hand-added row of 1e30 would raise ``InvalidOperation`` and take the page
# with it.
_TENTH: Final = Decimal("0.1")
_HALF_UP: Final = Context(prec=400, rounding=ROUND_HALF_UP)

# A mean is a float division and can land a hair beside the value it stands
# for: (0.01 + 0.09) / 2 is 0.049999999999999996 and would print as 0.0.
# Rounding to this many decimals first removes that hair and cannot make a
# tie of its own: the mean of n ledger values (two decimals each) is a
# multiple of 1/(100·n), so one that is no tie lies at least 1/(100·n) from
# the next tie, which is more than 5e-10 for up to 20 million rows. A year
# holds a few tens of thousands: the 2026 ledger had 11,206 rows on
# 2026-11-11, at most 98 on one day (the hourly backup tick of the update
# cycle adds polls to the half-hourly ones). Measured on 2026-11-10 over the
# means of all 80,200 pairs of values from 0.00 to 3.99: 282 printed one
# tenth low without this step, none with it. The day ranking sorts by the
# same rounded mean (:func:`_delay_days_section`).
_FLOAT_NOISE_DECIMALS: Final = 9

# The last sentence of the opening paragraph, which is the legend of the
# section. The overview rounds half up (:func:`_fmt_delay`); "Kennzahlen auf
# einen Blick" above it and the sections of ``markdown_report`` below it
# keep the float format of the dashboard, which they share with
# ``docs/statistik.md`` and the README and which must not change on a
# normal day. A mean on an exact half can therefore show 3.3 here and 3.2
# there (review of 2026-11-10, second round); the sentence says so. Below
# the overview only the bar charts per weekday and per hour print a delay
# (the tables there hold counts), so the sentence names the bars.
ROUNDING_NOTE: Final = (
    "Die Verspätungen dieses Abschnitts sind auf eine Nachkommastelle "
    "kaufmännisch gerundet; „Kennzahlen auf einen Blick“ und die "
    "Balkendiagramme weiter unten runden wie das Dashboard."
)

# Austrian German: "Jänner", as Wiener Linien titles write it
# (src/providers/wl_title_dates.py); the other eleven are the same everywhere.
MONTH_NAMES: Final = (
    "Jänner",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

_Row = TypeVar("_Row", StammstreckeRow, StoerungRow, AusfallRow)
# One delay row, reduced to what the tables need: its Vienna day and its delay.
_Sample = tuple[date, float]
# First and last Vienna day of a data series within the year.
_Span = tuple[date, date]


@dataclass(frozen=True)
class _DelayFigures:
    """The figures the tables show for one group of delay rows."""

    count: int
    mean: float
    exceedances: int
    peak: float
    peak_day: date


def _rows_of_year(rows: Iterable[_Row], year: int) -> list[tuple[datetime, _Row]]:
    """Each row whose Vienna year is *year*, with its Vienna time.

    A ledger file holds the rows of its Vienna year, but the function is
    also handed rows by callers and tests; a row of another year is left
    out instead of being counted under a month of this one.
    """
    stamped = ((to_vienna(row.timestamp), row) for row in rows)
    return [(when, row) for when, row in stamped if when.year == year]


def _span(days: Iterable[date]) -> _Span | None:
    """First and last day of a series; ``None`` for a series without rows."""
    ordered = sorted(days)
    return (ordered[0], ordered[-1]) if ordered else None


def _delay_figures(samples: Sequence[_Sample], threshold: float) -> _DelayFigures:
    """Count, mean, exceedances and maximum of a non-empty group of rows.

    ``math.fsum`` makes the mean independent of the order of the rows, so
    the same ledger gives the same ranking however it was sorted. An
    exceedance is a row strictly above the threshold, as everywhere else
    (``aggregate_stammstrecke``). Of several rows with the maximum the
    earliest day is named.
    """
    delays = [delay for _, delay in samples]
    peak_day, peak = min(samples, key=lambda sample: (-sample[1], sample[0]))
    return _DelayFigures(
        count=len(delays),
        mean=math.fsum(delays) / len(delays),
        exceedances=sum(1 for delay in delays if delay > threshold),
        peak=peak,
        peak_day=peak_day,
    )


def _fmt_delay(minutes: float) -> str:
    """``12.3 min``, rounded half up as a reader rounds; never ``-0.0 min``.

    ``f"{11.25:.1f}"`` is ``11.2``: the float format rounds an exact half
    to the even digit. The ledger stores two decimals and "Höchste
    Verspätung" prints single ledger values, so a reader who compares page
    and ledger found 11.25 shown as 11.2 (review of 2026-11-10). The
    shortest decimal form of the float is rounded instead, a half away from
    zero. The older tables of the page (``markdown_report``) keep the float
    format of the dashboard; the opening paragraph says so
    (:data:`ROUNDING_NOTE`).

    A value that is not finite keeps the float format ("inf min"): such a
    row does not reach this module (see :func:`render_year_overview`), and
    if one ever does, the page is still written.
    """
    if not math.isfinite(minutes):
        return f"{minutes:.1f} min"
    shortest = Decimal(repr(round(minutes, _FLOAT_NOISE_DECIMALS)))
    text = format(shortest.quantize(_TENTH, context=_HALF_UP), "f")
    return f"{'0.0' if text == '-0.0' else text} min"


def _fmt_date(day: date) -> str:
    return f"{day:%d.%m.%Y}"


def _fmt_day(day: date) -> str:
    """``Mo 08.06.2026``; the weekday comes from the date, not the ledger."""
    return f"{WEEKDAY_LABELS[day.weekday()]} {_fmt_date(day)}"


def _cell(text: str) -> str:
    """Ledger text for a table cell, as the tables of the page escape it.

    Directions and line names are what VAO sent. A ``|`` in one would add a
    column to its row, ``*`` or ``<`` would format or inject
    (``_format_directions_section`` in ``markdown_report`` has the threat
    model).
    """
    cell = escape_markdown_cell(
        normalise_markdown_text(text, max_len=_DASHBOARD_FIELD_MAX_LEN)
    )
    return cell or NOT_RECORDED


def _period_section(series: Sequence[tuple[str, _Span | None]]) -> list[str]:
    """Say which part of the year each ledger covers.

    The 2026 ledgers begin on 12 and 16 May; without these dates the year
    figures would read as those of twelve months.
    """
    lines = [
        "### Erfasster Zeitraum",
        "",
        "| Datenreihe | Erste Zeile | Letzte Zeile |",
        "| --- | --- | --- |",
    ]
    for label, span in series:
        first, last = (
            (_fmt_date(span[0]), _fmt_date(span[1]))
            if span is not None
            else (NOT_RECORDED, NOT_RECORDED)
        )
        lines.append(f"| {label} | {first} | {last} |")
    lines.append("")
    return lines


def _recorded(month: int, span: _Span | None) -> bool:
    """Whether *month* lies in the recorded period of a data series.

    The recorded period of a series is the run of months from the month of
    its first row to the month of its last row within the year, both
    included. A month in it without a row is a 0: the series was being
    written and had nothing for that month. A month before or after it is
    "not recorded": the page cannot tell an empty month from one in which
    nothing was written (2026 began in May), and says so with
    :data:`NOT_RECORDED` instead of a 0.

    Delays and disruptions are judged by their own rows only. Cancellations
    are written by the monitor that writes the delay rows, and only when a
    train is cancelled: a month with delay rows and no cancellation is a 0,
    so a month counts as recorded for them when it lies in their own period
    or in that of the delay rows (:func:`_count_cell` takes both).
    """
    return span is not None and span[0].month <= month <= span[1].month


def _count_cell(count: int, month: int, *spans: _Span | None) -> str:
    """The count of a month that lies in one of *spans*, else "not recorded".

    Each period is asked on its own. Until the review of 2026-11-10 the two
    periods of the cancellations were joined into one, from the earlier
    start to the later end: with cancellations in January and February and
    delay rows from June on, March to May showed 0, although the legend
    under the table promises :data:`NOT_RECORDED` for a month after the last
    cancellation row and without a delay row.
    """
    recorded = any(_recorded(month, span) for span in spans)
    return str(count) if recorded else NOT_RECORDED


def _delay_cells(
    samples: Sequence[_Sample], threshold: float, *, recorded: bool
) -> tuple[str, str, str, str]:
    """Observations, mean, exceedances and maximum of one month.

    A recorded month without an observation has the counts 0 and no mean
    and no maximum: there is nothing to take them from.
    """
    if not recorded:
        return (NOT_RECORDED, NOT_RECORDED, NOT_RECORDED, NOT_RECORDED)
    if not samples:
        return ("0", NOT_RECORDED, "0", NOT_RECORDED)
    figures = _delay_figures(samples, threshold)
    return (
        str(figures.count),
        _fmt_delay(figures.mean),
        str(figures.exceedances),
        _fmt_delay(figures.peak),
    )


def _months_section(
    delays: Sequence[_Sample],
    ausfall_days: Sequence[date],
    stoerung_days: Sequence[date],
    *,
    threshold: float,
) -> list[str]:
    """All twelve months in one table, each series within its recorded period.

    The legend names the reasons for :data:`NOT_RECORDED` in a count: the
    month lies outside the series' period, or the series has no row at all
    (a year with cancellations only), and that cancellations follow the
    delay rows (:func:`_recorded`). It does not say "a 0 means a month
    without a row": the column of delays above the threshold is 0 in every
    month whose rows all stay at or below it.
    """
    delays_of: dict[int, list[_Sample]] = defaultdict(list)
    for sample in delays:
        delays_of[sample[0].month].append(sample)
    ausfaelle_of = Counter(day.month for day in ausfall_days)
    stoerungen_of = Counter(day.month for day in stoerung_days)
    delay_span = _span(day for day, _ in delays)
    ausfall_span = _span(ausfall_days)
    stoerung_span = _span(stoerung_days)

    lines = [
        "### Nach Monaten",
        "",
        f"| Monat | Beobachtungen | ⌀ Verspätung | Verspätungen > {threshold:g} min "
        "| Höchste Verspätung | Ausfälle | Störungen |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for month, name in enumerate(MONTH_NAMES, start=1):
        cells = (
            *_delay_cells(
                delays_of[month], threshold, recorded=_recorded(month, delay_span)
            ),
            _count_cell(ausfaelle_of[month], month, ausfall_span, delay_span),
            _count_cell(stoerungen_of[month], month, stoerung_span),
        )
        lines.append(f"| {name} | {' | '.join(cells)} |")
    lines.extend(
        [
            "",
            f"„{NOT_RECORDED}“: Der Monat liegt vor der ersten oder nach der letzten "
            "Zeile der Datenreihe, oder die Datenreihe hat keine Zeile (siehe "
            "„Erfasster Zeitraum“). Ausfälle gelten auch in jedem Monat mit "
            "Stammstrecke-Beobachtungen als erfasst. Für einen erfassten Monat "
            "ohne Zeile steht 0; Mittel- und Höchstwert gibt es dort nicht.",
            "",
        ]
    )
    return lines


def _directions_section(
    by_direction: dict[str, list[_Sample]], *, threshold: float
) -> list[str]:
    """Delays per direction, most observations first (name breaks a tie)."""
    title = "### Verspätungen nach Richtung"
    if not by_direction:
        return [title, "", "_Keine Beobachtungen erfasst._", ""]
    ranked = sorted(by_direction.items(), key=lambda item: (-len(item[1]), item[0]))
    lines = [
        title,
        "",
        f"| Richtung | Beobachtungen | ⌀ Verspätung | Verspätungen > {threshold:g} min "
        "| Höchste Verspätung |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for direction, samples in ranked[:MAX_DIRECTION_ROWS]:
        figures = _delay_figures(samples, threshold)
        lines.append(
            f"| {_cell(direction)} | {figures.count} | {_fmt_delay(figures.mean)} "
            f"| {figures.exceedances} "
            f"| {_fmt_delay(figures.peak)} ({_fmt_day(figures.peak_day)}) |"
        )
    lines.append("")
    rest = ranked[MAX_DIRECTION_ROWS:]
    if rest:
        observations = sum(len(samples) for _, samples in rest)
        # One spelling beyond the cap has nothing to add up ("zusammen").
        spellings = (
            "1 weitere Richtungsangabe mit"
            if len(rest) == 1
            else f"{len(rest)} weitere Richtungsangaben mit zusammen"
        )
        noun = "Beobachtung" if observations == 1 else "Beobachtungen"
        lines.extend(
            [f"Nicht einzeln aufgeführt: {spellings} {observations} {noun}.", ""]
        )
    return lines


def _chosen_days(count: int) -> str:
    """How many of *count* days a day table shows; one day in the singular.

    "bis zu 5 der 1 Tage" stood on the page of a year with a single such
    day (review of 2026-11-10).
    """
    if count == 1:
        return "den einzigen Tag"
    return f"bis zu {TOP_DAYS} der {count} Tage"


def _delay_days_section(delays: Sequence[_Sample], *, threshold: float) -> list[str]:
    """The days with the highest mean delay; an earlier day wins a tie.

    The order follows the mean of the ledger values, the cell shows one
    decimal: two days can show the same value with the later one first. The
    note under the table therefore names the unrounded mean as the order;
    without that, such a pair contradicts the sentence about the earlier day
    (review of 2026-11-10). Ordering by the shown value instead would put a
    day with a lower mean above one with a higher.

    The key is the float mean rounded to :data:`_FLOAT_NOISE_DECIMALS`, as
    :func:`_fmt_delay` rounds it. Two days with the same mean of their
    ledger values can get floats a hair apart (10 rows summing to 20.92
    give 2.0919999999999996 on one day and 2.092 on another), and the
    later day stood first (review of 2026-11-10, second round). Rounding
    joins such a pair into a tie and keeps every other pair in its order:
    two different means of days with n and m rows lie at least
    1/(100·n·m) apart, more than 1e-9 for days of up to 3,000 rows; the
    busiest day of the 2026 ledger had 98 (measured on 2026-11-11).
    """
    title = "### Tage mit der höchsten mittleren Verspätung"
    if not delays:
        return [title, "", "_Keine Beobachtungen erfasst._", ""]
    samples_of: dict[date, list[_Sample]] = defaultdict(list)
    for sample in delays:
        samples_of[sample[0]].append(sample)
    eligible = [
        (day, _delay_figures(samples, threshold))
        for day, samples in samples_of.items()
        if len(samples) >= MIN_DAY_OBSERVATIONS
    ]
    if not eligible:
        return [
            title,
            "",
            f"_Kein Tag mit mindestens {MIN_DAY_OBSERVATIONS} Beobachtungen "
            f"(Tage mit Beobachtungen: {len(samples_of)})._",
            "",
        ]
    eligible.sort(key=lambda item: (-round(item[1].mean, _FLOAT_NOISE_DECIMALS), item[0]))
    lines = [
        title,
        "",
        "| Tag | ⌀ Verspätung | Beobachtungen | Höchste Verspätung |",
        "| --- | ---: | ---: | ---: |",
    ]
    for day, figures in eligible[:TOP_DAYS]:
        lines.append(
            f"| {_fmt_day(day)} | {_fmt_delay(figures.mean)} | {figures.count} "
            f"| {_fmt_delay(figures.peak)} |"
        )
    lines.extend(
        [
            "",
            f"Die Tabelle zeigt {_chosen_days(len(eligible))} mit mindestens "
            f"{MIN_DAY_OBSERVATIONS} Beobachtungen (Tage mit Beobachtungen insgesamt: "
            f"{len(samples_of)}), geordnet nach dem ungerundeten Mittelwert; bei "
            "gleichem Mittelwert steht der frühere Tag zuerst.",
            "",
        ]
    )
    return lines


def _most_frequent_line(labels: Iterable[str]) -> tuple[str, int]:
    """The line with the most cancellations of a day; a tie goes to the alphabet."""
    counts = Counter(labels)
    return min(
        counts.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
    )


def _ausfall_days_section(ausfaelle: Sequence[tuple[date, str]]) -> list[str]:
    """The days with the most cancellations; an earlier day wins a tie."""
    title = "### Tage mit den meisten Ausfällen"
    if not ausfaelle:
        return [title, "", "_Keine Ausfälle erfasst._", ""]
    labels_of: dict[date, list[str]] = defaultdict(list)
    for day, label in ausfaelle:
        labels_of[day].append(label)
    ranked = sorted(labels_of.items(), key=lambda item: (-len(item[1]), item[0]))
    lines = [
        title,
        "",
        "| Tag | Ausfälle | Häufigste Linie |",
        "| --- | ---: | --- |",
    ]
    for day, labels in ranked[:TOP_DAYS]:
        line, count = _most_frequent_line(labels)
        lines.append(f"| {_fmt_day(day)} | {len(labels)} | {_cell(line)} ({count}) |")
    lines.extend(
        [
            "",
            f"Die Tabelle zeigt {_chosen_days(len(ranked))} mit Ausfällen; "
            "bei gleicher Zahl steht der frühere Tag zuerst, bei gleich häufigen Linien "
            "die im Alphabet erste. In Klammern: Ausfälle dieser Linie an diesem Tag.",
            "",
        ]
    )
    return lines


def render_year_overview(
    sm_rows: Sequence[StammstreckeRow],
    st_rows: Sequence[StoerungRow],
    au_rows: Sequence[AusfallRow],
    *,
    year: int,
    coverage_note: str = "",
) -> list[str]:
    """The Markdown lines of "## Das Jahr im Überblick" for *year*.

    Michael's decision of 2026-11-10, point 3: the year page summarises all
    cancellations and delays in a statistic. Returns ``[]`` when none of
    the three inputs has a row of *year*; rows of other Vienna years are
    ignored. The lines end with an empty string, like every section of
    ``markdown_report``.

    *coverage_note* is the year's note on a direction that is not recorded
    to the end of the year (``render_finished_year_coverage_note``), or
    empty. It stands once on the page, directly under the opening
    paragraph: its sentence "Die Jahreszahlen unten …" is then true for
    every month and direction figure of this section. Until the review of
    2026-11-10 the note stood further down, under "## Stammstrecke", and
    the 2026 tables here set Meidling (recorded to 10.11.) beside
    Praterstern (to 14.08.) with no word why. The caller computes the
    note; this function stays pure.

    The opening sentence says what an observation is. ``data/stats/README.md``
    (table row ``stammstrecke_YYYY.csv``) and the website (``docs/site.html``,
    "S-Bahn-Beobachtungen am Wien Hauptbahnhof") call the trains "S-Bahn";
    neither defines the word "Beobachtung". The sentence names the four
    kinds of train the monitor takes on the Stammstrecke platforms
    (``_S_BAHN_LINE_RE`` in ``src/stammstrecke/core.py``): 801 of the 1955
    cancellations recorded up to 2026-11-10 are R, REX or CJX trains, and
    the last table of this section names such lines (review of that day).
    The sentence also says that "Höchste Verspätung" is the highest such
    row: the mean of one poll, not the delay of a single train, which the
    ledger does not hold.

    The delays are expected to be finite: ``_parse_stammstrecke_rows`` drops
    a row with NaN or infinity before it reaches a renderer.
    """
    threshold = DELAY_THRESHOLD_MINUTES
    sm_year = _rows_of_year(sm_rows, year)
    delays: list[_Sample] = [(when.date(), row.delay_minutes) for when, row in sm_year]
    ausfaelle = [
        (when.date(), ausfall_line_label(row.line))
        for when, row in _rows_of_year(au_rows, year)
    ]
    stoerung_days = [when.date() for when, _ in _rows_of_year(st_rows, year)]
    if not (delays or ausfaelle or stoerung_days):
        return []
    ausfall_days = [day for day, _ in ausfaelle]
    by_direction: dict[str, list[_Sample]] = defaultdict(list)
    for when, row in sm_year:
        by_direction[row.direction].append((when.date(), row.delay_minutes))

    lines = [
        "## Das Jahr im Überblick",
        "",
        "Eine Beobachtung ist eine Zeile je Richtung und Abfrage: die mittlere "
        "Verspätung aller dabei am Wien Hauptbahnhof beobachteten Abfahrten auf der "
        "Stammstrecke (S-Bahn, R, REX und CJX); "
        f"„Verspätungen > {threshold:g} min“ zählt die Beobachtungen über diesem Wert, "
        "„Höchste Verspätung“ nennt die höchste Beobachtung. "
        f"{ROUNDING_NOTE}",
        "",
    ]
    if coverage_note.strip():
        # ``splitlines`` keeps the rule of this list: one entry, one line.
        lines.extend([*coverage_note.rstrip("\n").splitlines(), ""])
    lines.extend(
        _period_section(
            [
                ("Stammstrecke-Beobachtungen", _span(day for day, _ in delays)),
                ("Ausfälle", _span(ausfall_days)),
                ("Störungen", _span(stoerung_days)),
            ]
        )
    )
    lines.extend(
        _months_section(delays, ausfall_days, stoerung_days, threshold=threshold)
    )
    lines.extend(_directions_section(dict(by_direction), threshold=threshold))
    lines.extend(_delay_days_section(delays, threshold=threshold))
    lines.extend(_ausfall_days_section(ausfaelle))
    return lines
