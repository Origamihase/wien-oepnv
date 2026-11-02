"""README snapshot blocks and the README marker patcher.

Moved verbatim from ``scripts/generate_markdown_stats.py``, which
re-exports every name.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Final

from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES
from src.markdown_stats.markdown_report import _DASHBOARD_FIELD_MAX_LEN
from src.markdown_stats.rows import (
    LOGGER,
    AusfallRow,
    StammstreckeRow,
    ausfall_line_label,
)
from src.utils.files import atomic_write, read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.text import (
    escape_markdown_cell,
    normalise_markdown_text,
)


# Cap the README at 1 MiB on read. The current file is ~5 KiB; the cap
# defends the patcher against an oversized / planted README that would
# otherwise be buffered into memory verbatim. Mirrors the canonical
# capped-read pattern from ``src.utils.files.read_capped_text``.
README_MAX_BYTES: Final = 1 * 1024 * 1024

# Window for the README snapshot ("Aktueller Schnappschuss"). The full
# annual dashboard remains at ``docs/statistik.md``; the README block is
# intentionally short so it stays glanceable.
DEFAULT_README_WINDOW_DAYS: Final = 30
# Aliased to the single source of truth in ``src.feed.stammstrecke`` so the
# dashboard/30-day-README threshold can never silently drift from the live-block
# threshold (``DELAY_THRESHOLD_MINUTES``) — previously hardcoded ``9.0`` here and
# in two more spots, which understated/overstated exceedances on a threshold change.
STAMMSTRECKE_THRESHOLD_MINUTES: Final = DELAY_THRESHOLD_MINUTES
README_PENDING_PLACEHOLDER: Final = "_wird berechnet…_"


def _format_thousands(value: int) -> str:
    """Format *value* with German thousands separator ('.')."""
    return f"{value:,}".replace(",", ".")


def _format_window_timestamp(now: datetime) -> str:
    """Render the "Letzte Aktualisierung" cell as ``YYYY-MM-DD HH:MM TZ``."""
    return now.strftime("%Y-%m-%d %H:%M %Z").rstrip()


def render_readme_stammstrecke_live_block(
    rows: list[StammstreckeRow],
    *,
    now: datetime,
    fetch_covers_window: bool = False,
) -> str:
    """Render the inner content of the ``STATS:STAMMSTRECKE_LIVE`` README block.

    Returns the body that goes *between* the two HTML-comment markers,
    terminated by a newline so the closing marker stays on its own line.

    An empty window reads "Keine Fahrten" only when *fetch_covers_window*
    says the monitor's fetch worked inside it (:func:`_live_fetch_covers`);
    otherwise "N/A". Operator decision 2026-10-30: on 30.10. every train
    of the hour was cancelled (track damage Meidling–Liesing) and "N/A"
    read like a broken fetch. Mirrors ``renderStammstreckeLiveTile`` in
    ``docs/assets/site-js/``.
    """
    threshold_label = (
        f"{DELAY_THRESHOLD_MINUTES:.0f}"
        if DELAY_THRESHOLD_MINUTES == int(DELAY_THRESHOLD_MINUTES)
        else f"{DELAY_THRESHOLD_MINUTES:g}"
    )
    header = (
        "> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    if not rows:
        return (
            header
            + "| Beobachtungen (gesamt) | 0 |\n"
            + "| Durchschnittliche Verspätung | "
            + ("Keine Fahrten" if fetch_covers_window else "N/A")
            + " |\n"
            + f"| Kritische Verspätungen (> {threshold_label} min) | 0 |\n"
            + f"| Letzte Aktualisierung | {now.strftime('%Y-%m-%d %H:%M %Z')} |\n"
        )

    count = len(rows)
    mean_delay = statistics.mean(r.delay_minutes for r in rows)
    critical_count = sum(1 for r in rows if r.delay_minutes > DELAY_THRESHOLD_MINUTES)

    return (
        header
        + f"| Beobachtungen (gesamt) | {count} |\n"
        + f"| Durchschnittliche Verspätung | {mean_delay:.1f} min |\n"
        + f"| Kritische Verspätungen (> {threshold_label} min) | {critical_count} |\n"
        + f"| Letzte Aktualisierung | {now.strftime('%Y-%m-%d %H:%M %Z')} |\n"
    )


def render_readme_stammstrecke_block(
    rows: list[StammstreckeRow],
    *,
    now: datetime,
    window_days: int = DEFAULT_README_WINDOW_DAYS,
    threshold_minutes: float = STAMMSTRECKE_THRESHOLD_MINUTES,
    coverage_note: str = "",
) -> str:
    """Render the inner content of the ``STATS:STAMMSTRECKE`` README block.

    Returns the body that goes *between* the two HTML-comment markers,
    terminated by a newline so the closing marker stays on its own line.
    Empty input renders the canonical ``wird berechnet…`` placeholder so
    a workflow run on a brand-new repo (no CSVs yet) still produces a
    well-formed Markdown table.

    The displayed delay is the *arithmetic mean* across the window —
    the feed-event trigger uses the median over the same observations
    (defensive against single sample-level outliers) but the README
    cell shows the average, which is more intuitive for non-technical
    readers. The "Kritische Verspätungen" counter counts each *row*
    whose persisted per-sample mean delay exceeds the threshold — at
    most once per cron cycle, so the same physical cycle is never
    multiplied into the count.
    """
    threshold_label = (
        f"{threshold_minutes:.0f}"
        if threshold_minutes == int(threshold_minutes)
        else f"{threshold_minutes:g}"
    )
    header = (
        coverage_note
        + f"> _Letzte {window_days} Tage – automatisch aktualisiert vom Workflow_ "
        "[`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    if not rows:
        return (
            header
            + f"| Beobachtungen (gesamt) | {README_PENDING_PLACEHOLDER} |\n"
            + f"| Durchschnittliche Verspätung | {README_PENDING_PLACEHOLDER} |\n"
            + f"| Kritische Verspätungen (> {threshold_label} min) | "
            + f"{README_PENDING_PLACEHOLDER} |\n"
            + f"| Letzte Aktualisierung | {_format_window_timestamp(now)} |\n"
        )
    delays = [r.delay_minutes for r in rows]
    avg_delay = statistics.mean(delays)
    exceedances = sum(1 for d in delays if d > threshold_minutes)
    return (
        header
        + f"| Beobachtungen (gesamt) | {_format_thousands(len(rows))} |\n"
        + f"| Durchschnittliche Verspätung | {avg_delay:.1f} min |\n"
        + f"| Kritische Verspätungen (> {threshold_label} min) | "
        + f"{_format_thousands(exceedances)} |\n"
        + f"| Letzte Aktualisierung | {_format_window_timestamp(now)} |\n"
    )


def render_readme_ausfaelle_live_block(
    rows: list[AusfallRow],
    *,
    now: datetime,
) -> str:
    """Render the inner content of the ``STATS:AUSFAELLE_LIVE`` README block.

    The live block reflects the rolling 60-minute window — i.e., the
    same :data:`FEED_WINDOW` the delay block uses — so the two
    snapshots are scoped to the same observation horizon. Most ticks
    will show ``0``; that explicit zero is the operational signal
    "currently no cancellations on the Stammstrecke", which is more
    useful than hiding the row entirely on a quiet day.
    """

    header = (
        "> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ "
        "[`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    return (
        header
        + f"| Ausfälle (gesamt) | {len(rows)} |\n"
        + f"| Letzte Aktualisierung | {now.strftime('%Y-%m-%d %H:%M %Z')} |\n"
    )


def render_readme_ausfaelle_block(
    rows: list[AusfallRow],
    *,
    now: datetime,
    window_days: int = DEFAULT_README_WINDOW_DAYS,
    coverage_note: str = "",
) -> str:
    """Render the inner content of the ``STATS:AUSFAELLE`` README block.

    30-day rolling cancellation count for the README snapshot. Returns
    the body that goes *between* the two HTML-comment markers,
    terminated by a newline so the closing marker stays on its own
    line. The block is always rendered (even at zero cancellations)
    because an explicit ``0`` is the operationally-meaningful "stable
    service" signal — hiding it would conflate "stable" with "data
    missing".
    """

    header = (
        coverage_note
        + f"> _Letzte {window_days} Tage – automatisch aktualisiert vom Workflow_ "
        "[`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    # Sorted by count (descending) so the most-cancelled line surfaces
    # first; the secondary sort by line name keeps the rendering
    # stable when two lines tie. Limited to top 3 to keep the README
    # block compact.
    line_count: dict[str, int] = defaultdict(int)
    for row in rows:
        line_count[ausfall_line_label(row.line)] += 1
    top_lines = sorted(
        line_count.items(), key=lambda pair: (-pair[1], pair[0])
    )[:3]
    if top_lines:
        top_lines_cell = ", ".join(
            f"{escape_markdown_cell(normalise_markdown_text(line, max_len=_DASHBOARD_FIELD_MAX_LEN))} ({count})"
            for line, count in top_lines
        )
    else:
        top_lines_cell = "_keine_"
    return (
        header
        + f"| Ausfälle (gesamt) | {_format_thousands(len(rows))} |\n"
        + f"| Häufigste Linien | {top_lines_cell} |\n"
        + f"| Letzte Aktualisierung | {_format_window_timestamp(now)} |\n"
    )


# Marker pair contract: the patcher rewrites whatever sits between
# ``<!-- STATS:<NAME>:BEGIN -->`` and ``<!-- STATS:<NAME>:END -->``.
# Both markers MUST appear verbatim and on their own logical line in
# the README; markers nested inside fenced code blocks would break the
# regex (deliberately — a marker that survives a copy-paste into a code
# block was almost certainly an accident, and silent-rewriting it would
# corrupt user-authored documentation). The regex compiles non-greedy
# / DOTALL so a single sweep handles every named marker without
# back-tracking trouble.
_README_MARKER_RE_TEMPLATE: Final = (
    r"(<!-- STATS:{name}:BEGIN -->)(.*?)(<!-- STATS:{name}:END -->)"
)


def _build_marker_re(name: str) -> re.Pattern[str]:
    return re.compile(
        _README_MARKER_RE_TEMPLATE.format(name=re.escape(name)),
        re.DOTALL,
    )


def patch_readme_stats(
    readme_path: Path,
    sections: dict[str, str],
) -> bool:
    """Patch *readme_path* so each ``STATS:<name>:`` marker pair wraps the
    matching body.

    The patcher is **idempotent** in the byte-equality sense: a second
    call with identical *sections* produces the same file (and,
    crucially, when the would-be result equals the on-disk content the
    file is *not* touched — its mtime is preserved so the auto-commit
    action correctly sees a no-op).

    Missing markers are logged at WARNING and the section is skipped —
    the rest of the README is preserved untouched. A README that fails
    to load (missing / oversize / non-UTF8) is treated as a no-op so
    the dashboard generation still succeeds; the warning is the
    audit trail.

    Returns ``True`` when the file was rewritten, ``False`` when nothing
    changed (or when the file could not be read).
    """
    raw = read_capped_text(
        readme_path,
        max_bytes=README_MAX_BYTES,
        label="README",
        logger=LOGGER,
    )
    if raw is None:
        return False
    new_text = raw
    for name, body in sections.items():
        pattern = _build_marker_re(name)
        match = pattern.search(new_text)
        if match is None:
            LOGGER.warning(
                "README-Marker STATS:%s nicht gefunden in %s — überspringe Section.",
                sanitize_log_arg(name),
                sanitize_log_arg(str(readme_path)),
            )
            continue
        replacement = f"{match.group(1)}\n{body}{match.group(3)}"
        new_text = new_text[: match.start()] + replacement + new_text[match.end():]
    if new_text == raw:
        LOGGER.info(
            "README-Statistik-Block unverändert (%s) — kein Schreibvorgang.",
            sanitize_log_arg(str(readme_path)),
        )
        return False
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(
        readme_path,
        mode="w",
        encoding="utf-8",
        permissions=0o644,
    ) as fh:
        fh.write(new_text)
    LOGGER.info(
        "README-Statistik-Block aktualisiert (%s, %d Bytes).",
        sanitize_log_arg(str(readme_path)),
        len(new_text.encode("utf-8")),
    )
    return True
