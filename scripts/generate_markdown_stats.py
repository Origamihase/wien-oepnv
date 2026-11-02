#!/usr/bin/env python3
"""Generate the Stammstrecke + Störungen statistics dashboard.

Reads the append-only CSV ledgers under ``data/stats/`` (written by
:mod:`scripts.update_stammstrecke_status` and :mod:`src.build_feed`),
aggregates them by weekday / hour / provider / line, and writes a single
Markdown report to ``docs/statistik.md``.

The script is **strictly zero-dependency** — only the Python standard
library is used (``csv``, ``collections``, ``datetime``, ``statistics``,
``pathlib``, ``zoneinfo``, ``argparse``). No NumPy / Pandas /
Matplotlib. ASCII / Emoji bar charts are rendered inline so the report
is readable as plain text in a terminal as well as in any Markdown
viewer.

Design contract
---------------

* **Read-only on the inputs**: the script *never* modifies the input
  CSVs. Corruption-tolerance is provided by skipping malformed rows
  (logged at WARNING) instead of crashing — a single fat-fingered
  manual edit can never break the dashboard regeneration.
* **Idempotent on the output**: running the script twice on the same
  data produces byte-identical Markdown. Any aggregation step that is
  order-sensitive (ranked tables such as the lines) breaks ties with a
  stable secondary sort on the name itself.
* **Bounded read sizes**: each CSV file is capped at
  :data:`MAX_CSV_BYTES` (~25 MiB) on read. A planted-huge file is
  skipped with a WARNING instead of buffered into memory.
* **Atomic write**: the dashboard is written via
  :func:`src.utils.files.atomic_write` so a crash or kill-signal
  mid-write cannot leave a half-rendered Markdown report on disk.
* **Timezone**: all aggregation honours ``Europe/Vienna`` — the source
  CSVs already store timestamps in that zone (the writers normalise
  via :func:`src.utils.stats.to_vienna`), so the script just trusts
  the recorded values.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stammstrecke_poll import (  # noqa: E402
    assess_poll,
    load_poll_status,
)
from src.utils.stats import (  # noqa: E402
    DEFAULT_STATS_DIR,
    WEEKDAY_LABELS,
)
from src.feed.stammstrecke import FEED_WINDOW  # noqa: E402

# The dashboard code lives in ``src/markdown_stats/`` (line limit,
# 2026-11-02); every moved name is re-exported here unchanged.
from src.markdown_stats.rows import (  # noqa: E402
    LOGGER as LOGGER,
    VIENNA_TZ as VIENNA_TZ,
    MAX_CSV_BYTES as MAX_CSV_BYTES,
    LOCATION_UNKNOWN as LOCATION_UNKNOWN,
    StammstreckeRow as StammstreckeRow,
    StoerungRow as StoerungRow,
    StammstreckeAggregate as StammstreckeAggregate,
    StoerungAggregate as StoerungAggregate,
    AusfallRow as AusfallRow,
    AusfallAggregate as AusfallAggregate,
    _iter_csv_rows as _iter_csv_rows,
    _aware_fromisoformat as _aware_fromisoformat,
    _parse_stammstrecke_rows as _parse_stammstrecke_rows,
    _parse_stoerung_rows as _parse_stoerung_rows,
    _parse_ausfall_rows as _parse_ausfall_rows,
    aggregate_stammstrecke as aggregate_stammstrecke,
    aggregate_stoerungen as aggregate_stoerungen,
    NO_LINE_LABEL as NO_LINE_LABEL,
    _TRAIN_NUMBER_RE as _TRAIN_NUMBER_RE,
    ausfall_line_label as ausfall_line_label,
    aggregate_ausfaelle as aggregate_ausfaelle,
    _TimestampedRow as _TimestampedRow,
    _filter_rows_by_window as _filter_rows_by_window,
    _filter_rows_by_timedelta as _filter_rows_by_timedelta,
    collect_year_data as collect_year_data,
)
from src.markdown_stats.direction_coverage import (  # noqa: E402
    COVERAGE_ORIGIN_LABEL as COVERAGE_ORIGIN_LABEL,
    _last_seen_suffix as _last_seen_suffix,
    _last_seen_per_direction as _last_seen_per_direction,
    DIRECTION_SILENCE_NOTICE_HOURS as DIRECTION_SILENCE_NOTICE_HOURS,
    DIRECTION_SILENCE_NOTICE_PEER_ROWS as DIRECTION_SILENCE_NOTICE_PEER_ROWS,
    CORRIDOR_SILENCE_NOTICE_HOURS as CORRIDOR_SILENCE_NOTICE_HOURS,
    find_silent_coverage_directions as find_silent_coverage_directions,
    render_direction_coverage_note as render_direction_coverage_note,
)
from src.markdown_stats.markdown_report import (  # noqa: E402
    DEFAULT_OUTPUT_PATH as DEFAULT_OUTPUT_PATH,
    VAO_BASIS_NOTE as VAO_BASIS_NOTE,
    STOERUNGEN_SOURCES_NOTE as STOERUNGEN_SOURCES_NOTE,
    MAX_BAR_WIDTH as MAX_BAR_WIDTH,
    _DASHBOARD_FIELD_MAX_LEN as _DASHBOARD_FIELD_MAX_LEN,
    BAR_GLYPHS as BAR_GLYPHS,
    _scale_bar as _scale_bar,
    _bar_line as _bar_line,
    render_weekday_bars as render_weekday_bars,
    render_hour_bars as render_hour_bars,
    render_avg_delay_weekday as render_avg_delay_weekday,
    render_avg_delay_hour as render_avg_delay_hour,
    _format_summary_section as _format_summary_section,
    _format_directions_section as _format_directions_section,
    _format_providers_section as _format_providers_section,
    _format_ausfall_directions_section as _format_ausfall_directions_section,
    _format_ausfall_lines_section as _format_ausfall_lines_section,
    render_markdown as render_markdown,
    write_dashboard as write_dashboard,
    write_year_archive as write_year_archive,
)
from src.markdown_stats.readme import (  # noqa: E402
    README_MAX_BYTES as README_MAX_BYTES,
    DEFAULT_README_WINDOW_DAYS as DEFAULT_README_WINDOW_DAYS,
    STAMMSTRECKE_THRESHOLD_MINUTES as STAMMSTRECKE_THRESHOLD_MINUTES,
    README_PENDING_PLACEHOLDER as README_PENDING_PLACEHOLDER,
    _format_thousands as _format_thousands,
    _format_window_timestamp as _format_window_timestamp,
    render_readme_stammstrecke_live_block as render_readme_stammstrecke_live_block,
    render_readme_stammstrecke_block as render_readme_stammstrecke_block,
    render_readme_ausfaelle_live_block as render_readme_ausfaelle_live_block,
    render_readme_ausfaelle_block as render_readme_ausfaelle_block,
    _README_MARKER_RE_TEMPLATE as _README_MARKER_RE_TEMPLATE,
    _build_marker_re as _build_marker_re,
    patch_readme_stats as patch_readme_stats,
)
from src.markdown_stats.summary import (  # noqa: E402
    SUMMARY_FILENAME as SUMMARY_FILENAME,
    DEFAULT_SUMMARY_PATH as DEFAULT_SUMMARY_PATH,
    SUMMARY_SCHEMA_VERSION as SUMMARY_SCHEMA_VERSION,
    LIVE_WINDOW_MINUTES as LIVE_WINDOW_MINUTES,
    resolve_summary_path as resolve_summary_path,
    _live_window_delay as _live_window_delay,
    _live_fetch_covers as _live_fetch_covers,
    _direction_coverage as _direction_coverage,
    _poll_evidence as _poll_evidence,
    build_stats_summary as build_stats_summary,
    write_stats_summary as write_stats_summary,
    _round_floats as _round_floats,
)


DEFAULT_README_PATH: Final = REPO_ROOT / "README.md"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns ``0`` on success (incl. the empty-data case), ``1`` on a
    fatal error (write failure). Parsing errors of individual CSV rows
    are tolerated and never propagate to the exit code — the dashboard
    is regenerated from whatever can be parsed.
    """
    setup_script_logging(logging.INFO)

    parser = argparse.ArgumentParser(
        description="Generate the Wien ÖPNV statistics dashboard.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help=(
            "Calendar year to aggregate (default: the Vienna year of the "
            "run time, i.e. of --now-iso when given)."
        ),
    )
    parser.add_argument(
        "--stats-dir",
        type=Path,
        default=DEFAULT_STATS_DIR,
        help=f"Directory containing the stats CSV files (default: {DEFAULT_STATS_DIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output Markdown path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help=(
            "Output path for the pre-computed dashboard summary JSON "
            "(default: a sibling of --output named "
            f"{SUMMARY_FILENAME})."
        ),
    )
    parser.add_argument(
        "--readme-path",
        type=Path,
        default=DEFAULT_README_PATH,
        help=(
            "Path to the README.md whose <!-- STATS:* --> markers should be "
            f"patched (default: {DEFAULT_README_PATH})."
        ),
    )
    parser.add_argument(
        "--readme-window-days",
        type=int,
        default=DEFAULT_README_WINDOW_DAYS,
        help=(
            "Window size in days for the README snapshot block "
            f"(default: {DEFAULT_README_WINDOW_DAYS})."
        ),
    )
    parser.add_argument(
        "--skip-readme",
        action="store_true",
        help=(
            "Skip the README patch entirely (only the docs/statistik.md "
            "dashboard is regenerated)."
        ),
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help=(
            "Skip regenerating docs/statistik.md (only the README STATS "
            "markers are patched). Inverse of ``--skip-readme``; used by "
            "``update-cycle.yml`` so the dashboard refreshes once daily "
            "at the 00:00 Europe/Vienna tick while the README snapshot "
            "continues to update on every 30-min tick."
        ),
    )
    parser.add_argument(
        "--now-iso",
        type=str,
        default=None,
        help=(
            "Override the wall clock used for the README window cutoff and "
            "the 'Letzte Aktualisierung' cell. Accepts an ISO 8601 string; "
            "missing tzinfo is interpreted as Europe/Vienna. Intended for "
            "deterministic reproductions and tests."
        ),
    )
    args = parser.parse_args(argv)

    if args.readme_window_days < 1:
        LOGGER.error(
            "--readme-window-days muss >= 1 sein, war %d.",
            args.readme_window_days,
        )
        return 1

    if args.now_iso is not None:
        try:
            now = datetime.fromisoformat(args.now_iso)
        except ValueError:
            LOGGER.error(
                "--now-iso konnte nicht geparst werden: %s",
                sanitize_log_arg(args.now_iso),
            )
            return 1
        if now.tzinfo is None:
            # Naive --now-iso input is interpreted as Europe/Vienna (the
            # project's canonical zone — every CSV writer normalises to
            # it via :func:`src.utils.stats.to_vienna`).
            now = now.replace(tzinfo=VIENNA_TZ)
        else:
            # Convert to the named ZoneInfo so ``strftime('%Z')`` renders
            # the friendly abbreviation ("CEST" / "CET") rather than the
            # raw offset ("UTC+02:00") that ``datetime.fromisoformat``
            # would otherwise carry through.
            now = now.astimezone(VIENNA_TZ)
    else:
        now = datetime.now(VIENNA_TZ)
    # Resolved after ``now``, not as an argparse default evaluated at parse
    # time: a run replayed with ``--now-iso 2027-01-01T00:15`` must cover
    # 2027, not the year of the machine clock.
    if args.year is None:
        args.year = now.year

    sm_rows, st_rows, au_rows = collect_year_data(
        args.year, stats_dir=args.stats_dir
    )
    LOGGER.info(
        "Stats geladen: %d Stammstrecke-Zeilen, %d Störungs-Zeilen, "
        "%d Ausfall-Zeilen aus %s.",
        len(sm_rows),
        len(st_rows),
        len(au_rows),
        sanitize_log_arg(str(args.stats_dir)),
    )

    # Load the cross-year Stammstrecke window for the README snapshot.
    # The nightly workflow runs at 00:15 Europe/Vienna, so a 30-day
    # cutoff in early January legitimately spans the previous calendar
    # year. ``collect_year_data`` returns empty lists for missing files,
    # so eagerly loading both years is safe even mid-year.
    cutoff = now - timedelta(days=args.readme_window_days)
    extra_years = sorted({cutoff.year, now.year} - {args.year})
    window_sm: list[StammstreckeRow] = list(sm_rows)
    window_au: list[AusfallRow] = list(au_rows)
    for extra_year in extra_years:
        extra_sm, _, extra_au = collect_year_data(
            extra_year, stats_dir=args.stats_dir
        )
        window_sm.extend(extra_sm)
        window_au.extend(extra_au)
    sm_window = _filter_rows_by_window(
        window_sm, days=args.readme_window_days, now=now
    )
    au_window = _filter_rows_by_window(
        window_au, days=args.readme_window_days, now=now
    )
    # Audit B.1: the coverage caveat is derived from the delay ledger (the
    # cancellation ledger only carries rows when trains are actually cancelled,
    # so it cannot tell "no cancellations" apart from "direction not measured").
    # Computed here — before the dashboard branch — so the same wording feeds
    # docs/statistik.md and both 30-day README blocks. The annual dashboard
    # aggregate still contains the pre-outage rows, so absence-from-aggregate
    # alone would never fire there; the recent window is what reveals it.
    # Operator decision 2026-10-10: a dark corridor is named only while the
    # monitor's fetch works; a broken fetch is the health check's (red).
    poll_status = load_poll_status()
    fetch_working = assess_poll(poll_status, now=now).working
    coverage_note = render_direction_coverage_note(
        sm_window, now=now, all_rows=window_sm, fetch_working=fetch_working
    )
    if coverage_note:
        LOGGER.warning(
            "Eingeschränkte Richtungs-Abdeckung im %d-Tage-Fenster — "
            "veröffentlichte Kennzahlen decken nicht den gesamten Korridor ab.",
            args.readme_window_days,
        )

    # Audit E.3: the aggregation moved OUT of the ``--skip-dashboard``
    # branch. The website reads these numbers from
    # ``docs/stats-summary.json`` on every page load and must not be up
    # to 24 hours stale, so the roll-up now runs on every tick — ~14 000
    # rows, microseconds, against ~705 KB the browser no longer has to
    # download. Only the Markdown *render* stays behind the flag (that
    # is what churns the ``_Automatisch erzeugt am ..._`` timestamp
    # through the commit log every 30 minutes).
    sm_agg = aggregate_stammstrecke(sm_rows)
    st_agg = aggregate_stoerungen(st_rows)
    au_agg = aggregate_ausfaelle(au_rows)

    summary_path = resolve_summary_path(
        args.summary_path, markdown_output=args.output
    )
    try:
        write_stats_summary(
            build_stats_summary(
                year=args.year,
                generated_at=now,
                stammstrecke_rows=sm_rows,
                stammstrecke=sm_agg,
                stoerungen=st_agg,
                ausfaelle=au_agg,
                window_rows=sm_window,
                all_window_rows=window_sm,
                window_days=args.readme_window_days,
                poll=_poll_evidence(poll_status),
            ),
            output_path=summary_path,
        )
    except OSError as exc:
        # Same posture as the dashboard write below: the published page
        # renders from this file, so a failed write is a broken tick,
        # not a cosmetic miss.
        LOGGER.error(
            "Konnte Statistik-Summary nicht schreiben (%s): %s",
            sanitize_log_arg(str(summary_path)),
            sanitize_log_arg(str(exc)),
        )
        return 1

    LOGGER.info(
        "Statistik-Summary geschrieben: %s.",
        sanitize_log_arg(str(summary_path)),
    )

    if args.skip_dashboard:
        # ``update-cycle.yml`` passes this flag on every 30-min tick
        # except the one that lands inside the 00:00 Europe/Vienna hour.
        # Skipping the Markdown render avoids the 30-min churn of the
        # ``_Automatisch erzeugt am ..._`` timestamp in the commit log.
        LOGGER.info(
            "Dashboard-Schritt übersprungen (--skip-dashboard) — "
            "README wird weiterhin gepatcht."
        )
    else:
        markdown = render_markdown(
            year=args.year,
            generated_at=now,
            stammstrecke=sm_agg,
            stoerungen=st_agg,
            ausfaelle=au_agg,
            # The dashboard shows the whole year, so its caveat is judged
            # against the year's rows, not the README's 30-day window.
            coverage_note=render_direction_coverage_note(
                sm_rows,
                now=now,
                all_rows=window_sm,
                fetch_working=fetch_working,
                yearly=True,
            ),
        )

        try:
            write_dashboard(markdown, output_path=args.output)
        except OSError as exc:
            LOGGER.error(
                "Konnte Dashboard nicht schreiben (%s): %s",
                sanitize_log_arg(str(args.output)),
                sanitize_log_arg(str(exc)),
            )
            return 1

        LOGGER.info(
            "Dashboard geschrieben: %s (%d Bytes).",
            sanitize_log_arg(str(args.output)),
            len(markdown.encode("utf-8")),
        )
        try:
            write_year_archive(
                now=now, year=args.year, stats_dir=args.stats_dir, output_path=args.output
            )
        except OSError as exc:
            LOGGER.error(
                "Konnte Jahresarchiv nicht schreiben: %s",
                sanitize_log_arg(str(exc)),
            )
            return 1

    if args.skip_readme:
        return 0

    # Defense-in-depth: only patch the Stammstrecke marker when the
    # window actually carries rows. Without this gate, an unrelated
    # caller of ``main()`` that supplies a stats directory but forgets
    # to override ``--readme-path`` (or pass ``--skip-readme``) would
    # silently overwrite the production ``README.md`` with the
    # ``_wird berechnet…_`` placeholder block — that exact bug bit
    # ``tests/scripts/test_generate_markdown_stats/`` and stamped
    # placeholders into the committed README on 2026-05-09 (audit
    # trail in PR #1397).
    sections: dict[str, str] = {}
    if sm_window:
        sections["STAMMSTRECKE"] = render_readme_stammstrecke_block(
            sm_window,
            now=now,
            window_days=args.readme_window_days,
            coverage_note=coverage_note,
        )
    else:
        LOGGER.info(
            "STAMMSTRECKE README block skipped: 0 rows in %d-day window.",
            args.readme_window_days,
        )

    sm_live_window = _filter_rows_by_timedelta(
        window_sm, delta=FEED_WINDOW, now=now
    )
    sections["STAMMSTRECKE_LIVE"] = render_readme_stammstrecke_live_block(
        sm_live_window,
        now=now,
        fetch_covers_window=_live_fetch_covers(poll_status, now=now),
    )
    # Cancellation snapshots: always render — an explicit ``0`` is the
    # operationally-meaningful "stable service" signal, so the README
    # block is patched even when the window contains no cancellations.
    # Gating on ``sm_window``/``sm_live_window`` (delay data present)
    # ensures we still bail to "preserve existing README" when the
    # repository has no statistics at all (fresh clone, missing CSVs).
    au_live_window = _filter_rows_by_timedelta(
        window_au, delta=FEED_WINDOW, now=now
    )
    sections["AUSFAELLE_LIVE"] = render_readme_ausfaelle_live_block(
        au_live_window,
        now=now,
    )
    sections["AUSFAELLE"] = render_readme_ausfaelle_block(
        au_window,
        now=now,
        window_days=args.readme_window_days,
        coverage_note=coverage_note,
    )
    if not sm_window and not sm_live_window:
        LOGGER.info(
            "README patch skipped entirely: no data for any marker block in "
            "the %d-day window — existing README content preserved.",
            args.readme_window_days,
        )
        return 0
    try:
        patch_readme_stats(args.readme_path, sections)
    except OSError as exc:
        LOGGER.error(
            "Konnte README nicht aktualisieren (%s): %s",
            sanitize_log_arg(str(args.readme_path)),
            sanitize_log_arg(str(exc)),
        )
        return 1

    return 0


# Public names of this script: its own entry points and the names other
# files and tests read through it. The code lives in src/ (700-line limit,
# 2026-11-02); listing the imports marks them as deliberate re-exports.
__all__ = [
    "CORRIDOR_SILENCE_NOTICE_HOURS",
    "COVERAGE_ORIGIN_LABEL",
    "DEFAULT_OUTPUT_PATH",
    "DEFAULT_README_PATH",
    "DEFAULT_README_WINDOW_DAYS",
    "DIRECTION_SILENCE_NOTICE_HOURS",
    "DIRECTION_SILENCE_NOTICE_PEER_ROWS",
    "LOCATION_UNKNOWN",
    "MAX_CSV_BYTES",
    "README_MAX_BYTES",
    "README_PENDING_PLACEHOLDER",
    "STAMMSTRECKE_THRESHOLD_MINUTES",
    "AusfallAggregate",
    "AusfallRow",
    "StammstreckeAggregate",
    "StammstreckeRow",
    "StoerungAggregate",
    "StoerungRow",
    "aggregate_ausfaelle",
    "aggregate_stammstrecke",
    "aggregate_stoerungen",
    "collect_year_data",
    "find_silent_coverage_directions",
    "main",
    "patch_readme_stats",
    "render_direction_coverage_note",
    "render_hour_bars",
    "render_markdown",
    "render_readme_ausfaelle_block",
    "render_readme_ausfaelle_live_block",
    "render_readme_stammstrecke_block",
    "render_weekday_bars",
    "write_dashboard",
    "write_year_archive",
    "_iter_csv_rows",
    "_aware_fromisoformat",
    "_parse_stammstrecke_rows",
    "_parse_stoerung_rows",
    "_parse_ausfall_rows",
    "NO_LINE_LABEL",
    "_TRAIN_NUMBER_RE",
    "ausfall_line_label",
    "_TimestampedRow",
    "_last_seen_suffix",
    "_last_seen_per_direction",
    "VAO_BASIS_NOTE",
    "STOERUNGEN_SOURCES_NOTE",
    "MAX_BAR_WIDTH",
    "_DASHBOARD_FIELD_MAX_LEN",
    "BAR_GLYPHS",
    "_scale_bar",
    "_bar_line",
    "render_avg_delay_weekday",
    "render_avg_delay_hour",
    "_format_summary_section",
    "_format_directions_section",
    "_format_providers_section",
    "_format_ausfall_directions_section",
    "_format_ausfall_lines_section",
    "_format_thousands",
    "_format_window_timestamp",
    "_README_MARKER_RE_TEMPLATE",
    "_build_marker_re",
    "DEFAULT_SUMMARY_PATH",
    "SUMMARY_SCHEMA_VERSION",
    "LIVE_WINDOW_MINUTES",
    "_live_window_delay",
    "_direction_coverage",
    "_round_floats",
]


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


# Exposed for tests; intentionally module-private otherwise.


# Sanity import check: ensure the WEEKDAY_LABELS constant from
# :mod:`src.utils.stats` matches the local one used for rendering. Drift
# between these would silently mis-bucket weekdays.
if tuple(WEEKDAY_LABELS) != ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"):  # pragma: no cover - drift guard
    raise RuntimeError(
        "WEEKDAY_LABELS drifted between src.utils.stats and the dashboard renderer."
    )
