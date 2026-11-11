"""Markdown rendering and writers of ``docs/statistik.md``.

Moved verbatim from ``scripts/generate_markdown_stats.py``, which
re-exports every name. ``REPO_ROOT`` is recomputed from this file's
location so ``DEFAULT_OUTPUT_PATH`` stays the same path.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from src.markdown_stats.direction_coverage import COVERAGE_ORIGIN_LABEL
from src.markdown_stats.rows import (
    AusfallAggregate,
    StammstreckeAggregate,
    StoerungAggregate,
)
from src.utils.files import atomic_write
from src.utils.liquid import liquid_safe
from src.utils.stats import STAMMSTRECKE_DIRECTIONS, WEEKDAY_LABELS
from src.utils.text import (
    escape_markdown_cell,
    normalise_markdown_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


DEFAULT_OUTPUT_PATH: Final = REPO_ROOT / "docs" / "statistik.md"
# VAO "Start" contract, point 5.3: the statistics page states that the VAO
# "Start" access is the basis of the Stammstrecke data (NOTICE.md).
VAO_BASIS_NOTE: Final = (
    "> Stammstrecken-Daten: Grundlage ist der VAO-„Start“-Zugang der "
    "Verkehrsauskunft Österreich VAO GmbH."
)
# Michael, 17.10.2026: every source gets its own attribution. The disruption
# statistics count feed items from all four sources, each under its own.
STOERUNGEN_SOURCES_NOTE: Final = (
    "> Quellen: die Meldungen des Feeds, jede unter ihrer eigenen Quelle – "
    "Wiener Linien, ÖBB, Stadt Wien (Baustellen) und VOR/VAO (Stammstrecke)."
)

# Bar-chart geometry. The bar widths are intentionally short so the
# rendered Markdown stays comfortable even on a 96-col terminal viewer.
MAX_BAR_WIDTH: Final = 24

# Per-cell length cap applied at every CSV-derived Markdown sink. Each
# CSV writer in :mod:`src.utils.stats` already caps ``provider`` /
# ``location_name`` / ``direction`` at 200 chars on persistence, but
# the dashboard renders inside narrow Markdown table columns; an
# additional render-side cap keeps the layout legible even if a future
# writer relaxes its own cap.
_DASHBOARD_FIELD_MAX_LEN: Final = 80

# Visual vocabulary. Different glyphs per chart type so the eye can
# tell them apart at a glance even when the dashboard is rendered in
# monochrome.
BAR_GLYPHS: Final = {
    "weekday": "🟦",
    "hour": "🟧",
    "delay_weekday": "🟥",
    "delay_hour": "🟨",
    "ausfall_weekday": "🟪",
    "ausfall_hour": "🟫",
}


# ---- Bar rendering ---------------------------------------------------------


def _scale_bar(value: float, max_value: float, *, width: int = MAX_BAR_WIDTH) -> int:
    """Return the integer block count for *value* relative to *max_value*.

    A non-zero value always renders as at least one block so a faint
    signal does not vanish entirely. Zero stays zero.
    """
    if max_value <= 0 or value <= 0:
        return 0
    raw = (value / max_value) * width
    return max(1, min(width, int(round(raw))))


def _bar_line(
    label: str,
    value: float,
    max_value: float,
    glyph: str,
    *,
    label_width: int = 12,
    suffix: str = "",
    width: int = MAX_BAR_WIDTH,
) -> str:
    """Render a single ``label │ ████░░░░ 12.3`` chart row."""
    blocks = _scale_bar(value, max_value, width=width)
    bar = (glyph * blocks) + ("·" * (width - blocks))
    padded_label = label.ljust(label_width)
    return f"`{padded_label}` │ {bar} {suffix}".rstrip()


def render_weekday_bars(
    counts: dict[str, int],
    *,
    glyph: str,
    title: str,
) -> list[str]:
    """Render a Mo-So weekday chart with monotone glyphs."""
    if not counts:
        return [f"### {title}", "", "_Keine Daten verfügbar._", ""]
    max_value = max(counts.values())
    lines: list[str] = [f"### {title}", "", "```"]
    for label in WEEKDAY_LABELS:
        value = counts.get(label, 0)
        suffix = f" {value}" if value else " 0"
        lines.append(_bar_line(label, value, max_value, glyph, label_width=4, suffix=suffix))
    lines.append("```")
    lines.append("")
    return lines


def render_hour_bars(
    counts: dict[int, int],
    *,
    glyph: str,
    title: str,
) -> list[str]:
    """Render a 0-23 hour chart with monotone glyphs."""
    if not counts:
        return [f"### {title}", "", "_Keine Daten verfügbar._", ""]
    max_value = max(counts.values())
    lines: list[str] = [f"### {title}", "", "```"]
    for hour in range(24):
        value = counts.get(hour, 0)
        suffix = f" {value}" if value else " 0"
        lines.append(
            _bar_line(f"{hour:02d}h", value, max_value, glyph, label_width=4, suffix=suffix)
        )
    lines.append("```")
    lines.append("")
    return lines


def render_avg_delay_weekday(avgs: dict[str, float], glyph: str) -> list[str]:
    """Render the weekday breakdown of *average* delay."""
    if not avgs:
        return [
            "### Verspätungen ⌀ Minuten je Wochentag",
            "",
            "_Keine Daten verfügbar._",
            "",
        ]
    max_value = max(avgs.values())
    lines: list[str] = [
        "### Verspätungen ⌀ Minuten je Wochentag",
        "",
        "```",
    ]
    for label in WEEKDAY_LABELS:
        value = avgs.get(label, 0.0)
        suffix = f" {value:5.1f} min" if value else "  0.0 min"
        lines.append(
            _bar_line(label, value, max_value, glyph, label_width=4, suffix=suffix)
        )
    lines.append("```")
    lines.append("")
    return lines


def render_avg_delay_hour(avgs: dict[int, float], glyph: str) -> list[str]:
    """Render the hour-of-day breakdown of *average* delay."""
    if not avgs:
        return [
            "### Verspätungen ⌀ Minuten je Stunde",
            "",
            "_Keine Daten verfügbar._",
            "",
        ]
    max_value = max(avgs.values())
    lines: list[str] = [
        "### Verspätungen ⌀ Minuten je Stunde",
        "",
        "```",
    ]
    for hour in range(24):
        value = avgs.get(hour, 0.0)
        suffix = f" {value:5.1f} min" if value else "  0.0 min"
        lines.append(
            _bar_line(
                f"{hour:02d}h", value, max_value, glyph, label_width=4, suffix=suffix
            )
        )
    lines.append("```")
    lines.append("")
    return lines


# ---- Markdown assembly -----------------------------------------------------


def _format_summary_section(
    *,
    year: int,
    generated_at: datetime,
    stammstrecke: StammstreckeAggregate,
    stoerungen: StoerungAggregate,
    ausfaelle: AusfallAggregate,
    nav: str = "",
) -> list[str]:
    """Render the top-of-report key metrics block.

    Every cell in this block is scoped to the calendar *year*. The
    rolling 30-day window only feeds the README snapshot — keeping
    the two views distinct (year vs. 30 days) avoids the same data
    point being summed into both numbers at once. *nav* is the line that
    leads to the pages of the other years
    (:mod:`src.markdown_stats.year_archive`); empty, the block is the one
    it always was.
    """
    if stammstrecke.total_observations:
        # Observation-weighted (micro) mean over all rows of *year*.
        # Multiplying each per-weekday mean by its observation count
        # recovers the original delay sum exactly without re-iterating
        # the raw rows, which keeps the rendering side-effect-free on
        # the aggregate input.
        total = sum(
            stammstrecke.by_weekday_avg[wd] * stammstrecke.by_weekday_count[wd]
            for wd in stammstrecke.by_weekday_count
        )
        avg_cell = f"{total / stammstrecke.total_observations:.1f} min"
    else:
        avg_cell = "_keine Daten_"

    return [
        f"# Wien ÖPNV — Statistik {year}",
        "",
        f"_Automatisch erzeugt am {generated_at.isoformat(timespec='minutes')} (Europe/Vienna)._",
        "",
        *([nav, ""] if nav else []),
        "## Kennzahlen auf einen Blick",
        "",
        "| Kennzahl | Wert |",
        "| --- | ---: |",
        f"| Stammstrecke-Beobachtungen ({year}) | {stammstrecke.total_observations} |",
        f"| Verspätungen > {stammstrecke.threshold_minutes:g} min ({year}) | "
        f"{stammstrecke.threshold_exceedances} |",
        f"| ⌀ Verspätung ({year}) | {avg_cell} |",
        f"| Stammstrecke-Ausfälle ({year}) | {ausfaelle.total_cancellations} |",
        f"| Erfasste Störungen ({year}) | {stoerungen.total_disruptions} |",
        "",
    ]


def _format_directions_section(
    stammstrecke: StammstreckeAggregate, *, coverage_note: str = ""
) -> list[str]:
    """Render the per-direction breakdown table.

    Routes ``direction`` through :func:`normalise_markdown_text` +
    :func:`escape_markdown_cell` so a CSV row whose direction field
    contains a Markdown-meaningful character (``|`` / ``<`` / `` ` ``
    / ``[`` / embedded newline) cannot break out of the 2-column
    table cell. See the module-level threat model in the Sentinel
    journal (2026-05-09 Markdown sibling drift round).
    """
    if not stammstrecke.by_direction:
        return ["### Beobachtungen je Richtung", "", "_Keine Daten._", ""]
    items = sorted(
        stammstrecke.by_direction.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = ["### Beobachtungen je Richtung", ""]
    # Audit B.1: say so when a direction has stopped contributing, rather than
    # letting its absence pass as "no disruptions there". Two sources, in order
    # of precedence:
    #
    #   1. ``coverage_note`` from the caller (``yearly=True``: the silence is
    #      read off the recent ledger, the caveat off the year's rows). This is
    #      the one that fires in practice: the ANNUAL aggregate still contains
    #      the pre-outage rows, so a direction that went silent mid-year is
    #      present in ``by_direction`` and would be invisible here.
    #   2. A direction missing from the aggregate entirely (a direction that
    #      never reported in the whole year).
    #
    # Both are self-clearing — they disappear as soon as the direction reports
    # again, which the temporary nature of the closure requires.
    if coverage_note:
        lines.extend([coverage_note.rstrip("\n"), ""])
    else:
        missing = [
            d for d in STAMMSTRECKE_DIRECTIONS if d not in stammstrecke.by_direction
        ]
        if missing and len(missing) < len(STAMMSTRECKE_DIRECTIONS):
            names = " und ".join(f"**{escape_markdown_cell(d)}**" for d in missing)
            lines.extend(
                [
                    "> ⚠️ **Eingeschränkte Abdeckung:** In diesem Zeitraum "
                    f"keine Fahrten von {COVERAGE_ORIGIN_LABEL} Richtung "
                    f"{names} auf der Stammstrecke. Die Zahlen unten sind "
                    "daher **kein Korridor-Gesamtwert**.",
                    "",
                ]
            )
    lines.extend(["| Richtung | Anzahl |", "| --- | ---: |"])
    for direction, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(direction, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


def _format_providers_section(stoerungen: StoerungAggregate) -> list[str]:
    """Render the per-provider breakdown table.

    See :func:`_format_directions_section` for the threat model.
    The ``provider`` field flows from
    :data:`cache/wl/wl_baustellen.json["source"]` (and siblings) which
    a poisoned cache file can populate verbatim — defending the
    rendering boundary closes that path even when the upstream cache
    integrity check is bypassed.
    """
    if not stoerungen.by_provider:
        return ["### Störungen je Quelle", "", "_Keine Daten._", ""]
    items = sorted(
        stoerungen.by_provider.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = ["### Störungen je Quelle", "", "| Quelle | Anzahl |", "| --- | ---: |"]
    for provider, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(provider, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


def _format_ausfall_directions_section(ausfaelle: AusfallAggregate) -> list[str]:
    """Render the per-direction breakdown of cancellations.

    Same threat model as :func:`_format_directions_section` — the
    ``direction`` field flows from the CSV writer's
    :func:`src.utils.stats._sanitize_csv_text_field` defence and then
    through :func:`normalise_markdown_text` / :func:`escape_markdown_cell`
    at the rendering boundary.
    """
    if not ausfaelle.by_direction:
        return ["### Ausfälle je Richtung", "", "_Keine Ausfälle erfasst._", ""]
    items = sorted(
        ausfaelle.by_direction.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = [
        "### Ausfälle je Richtung",
        "",
        "| Richtung | Anzahl |",
        "| --- | ---: |",
    ]
    for direction, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(direction, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


def _format_ausfall_lines_section(ausfaelle: AusfallAggregate) -> list[str]:
    """Render the per-line breakdown of cancellations.

    Surfaces "which S-Bahn / REX line cancels most often?" — the
    primary operational signal for cancellations. The ``line`` field
    is the canonicalised VAO line designation (``S1``, ``REX3`` …)
    written by the collectors via
    :func:`src.utils.stats._sanitize_csv_text_field`, so the rendering
    boundary uses the same Markdown escaping pipeline as the direction
    cells.
    """
    if not ausfaelle.by_line:
        return ["### Ausfälle je Linie", "", "_Keine Ausfälle erfasst._", ""]
    items = sorted(
        ausfaelle.by_line.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = [
        "### Ausfälle je Linie",
        "",
        "| Linie | Anzahl |",
        "| --- | ---: |",
    ]
    for line, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(line, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


# The ledgers every page is made from, in the order of the source line.
_LEDGER_SOURCES = (
    "data/stats/stammstrecke_*.csv",
    "data/stats/ausfaelle_*.csv",
    "data/stats/stoerungen_*.csv",
)


def render_markdown(
    *,
    year: int,
    generated_at: datetime,
    stammstrecke: StammstreckeAggregate,
    stoerungen: StoerungAggregate,
    ausfaelle: AusfallAggregate,
    coverage_note: str = "",
    nav: str = "",
    after_summary: Sequence[str] = (),
    before_sources: Sequence[str] = (),
    extra_sources: Sequence[str] = (),
) -> str:
    """Compose the full Markdown dashboard string from the aggregates.

    *after_summary* and *before_sources* are finished Markdown lines a page
    of a finished year adds: the overview of the year below the key
    figures, the roadworks above the source line
    (:mod:`src.markdown_stats.year_archive`). *extra_sources* are the
    files those lines were made from, named after the three ledgers in the
    source line (review of 2026-11-10: a page with the roadworks named only
    the three). Empty, the page is the dashboard it always was.
    """
    sections: list[str] = []
    sections.extend(
        _format_summary_section(
            year=year,
            generated_at=generated_at,
            stammstrecke=stammstrecke,
            stoerungen=stoerungen,
            ausfaelle=ausfaelle,
            nav=nav,
        )
    )
    sections.extend(after_summary)

    # VAO "Start" contract, point 5.3: name the VAO "Start" access as the
    # basis of the Stammstrecke data (Michael, 17.10.2026; NOTICE.md). The
    # Ausfälle section below comes from the same VAO queries and repeats it.
    sections.extend(["## Stammstrecke", "", VAO_BASIS_NOTE, ""])
    sections.extend(
        _format_directions_section(stammstrecke, coverage_note=coverage_note)
    )
    sections.extend(
        render_weekday_bars(
            stammstrecke.by_weekday_count,
            glyph=BAR_GLYPHS["weekday"],
            title="Beobachtungen je Wochentag",
        )
    )
    sections.extend(
        render_hour_bars(
            stammstrecke.by_hour_count,
            glyph=BAR_GLYPHS["hour"],
            title="Beobachtungen je Stunde",
        )
    )
    sections.extend(
        render_avg_delay_weekday(
            stammstrecke.by_weekday_avg, BAR_GLYPHS["delay_weekday"]
        )
    )
    sections.extend(
        render_avg_delay_hour(stammstrecke.by_hour_avg, BAR_GLYPHS["delay_hour"])
    )

    sections.extend(["## Ausfälle", "", VAO_BASIS_NOTE, ""])
    sections.extend(_format_ausfall_directions_section(ausfaelle))
    sections.extend(_format_ausfall_lines_section(ausfaelle))
    sections.extend(
        render_weekday_bars(
            ausfaelle.by_weekday,
            glyph=BAR_GLYPHS["ausfall_weekday"],
            title="Ausfälle je Wochentag",
        )
    )
    sections.extend(
        render_hour_bars(
            ausfaelle.by_hour,
            glyph=BAR_GLYPHS["ausfall_hour"],
            title="Ausfälle je Stunde",
        )
    )

    sections.extend(["## Störungen", "", STOERUNGEN_SOURCES_NOTE, ""])
    sections.extend(_format_providers_section(stoerungen))
    sections.extend(
        render_weekday_bars(
            stoerungen.by_weekday,
            glyph=BAR_GLYPHS["weekday"],
            title="Störungen je Wochentag",
        )
    )
    sections.extend(
        render_hour_bars(
            stoerungen.by_hour,
            glyph=BAR_GLYPHS["hour"],
            title="Störungen je Stunde",
        )
    )

    sections.extend(before_sources)
    if before_sources and before_sources[-1].strip():
        # kramdown (GitHub Pages) reads a "---" directly under a line of text
        # as a setext heading: the roadworks' attribution became an <h2> and
        # the rule was gone (review of 2026-11-10).
        sections.append("")
    sources = ", ".join(f"`{name}`" for name in (*_LEDGER_SOURCES, *extra_sources))
    sections.extend(
        [
            "---",
            "",
            f"_Quellen_: {sources}. Generiert von `scripts/generate_markdown_stats.py`.",
            "",
        ]
    )

    return "\n".join(sections).rstrip() + "\n"


def write_dashboard(
    markdown: str,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> None:
    """Atomically write *markdown* to *output_path*.

    Uses :func:`src.utils.files.atomic_write` so a partially-written
    dashboard cannot replace the previous one. GitHub Pages renders the
    dashboard with Liquid, and line names come from the VAO answers: a
    ``{%`` in one would stop the Pages build, a ``{{ … }}`` would vanish
    (:func:`src.utils.liquid.liquid_safe`, review of 2026-11-01).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(output_path, mode="w", encoding="utf-8", permissions=0o644) as fh:
        fh.write(liquid_safe(markdown))
