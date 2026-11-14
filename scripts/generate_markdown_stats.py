#!/usr/bin/env python3
"""Generate the Stammstrecke + Störungen statistics dashboard.

Reads the append-only CSV ledgers under ``data/stats/`` (written by
:mod:`scripts.update_stammstrecke_status` and :mod:`src.build_feed`),
aggregates them by weekday / hour / provider / line, and writes a single
Markdown report to ``docs/statistik.md``.

The dashboard run (without ``--skip-dashboard``) also writes the page of
every finished year, ``docs/statistik-<Jahr>.md``
(:mod:`src.markdown_stats.year_archive`). While such years have roadworks
and no accepted review stored, that run ends with one pass over Gemini's
model chain for each of them (:mod:`src.markdown_stats.review_client`,
through ``src.utils.http``), all within one time budget
(:class:`src.markdown_stats.year_archive.ReviewBudget`), after everything
else of the run is written.

Aggregation and rendering use only the Python standard library (``csv``,
``collections``, ``datetime``, ``statistics``, ``pathlib``, ``zoneinfo``,
``argparse``). No NumPy / Pandas / Matplotlib. ASCII / Emoji bar charts
are rendered inline so the report is readable as plain text in a
terminal as well as in any Markdown viewer.

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

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.stats import (  # noqa: E402
    WEEKDAY_LABELS,
)

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
    earlier_rows_until_every_direction as earlier_rows_until_every_direction,
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
)
from src.markdown_stats.year_archive import (  # noqa: E402
    archived_years as archived_years,
    dashboard_nav as dashboard_nav,
    write_year_archives as write_year_archives,
    years_without_review as years_without_review,
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
# The run itself (``main``, its options, the end of a run) lives in
# ``src/markdown_stats/cli.py`` and ``cli_options.py`` (line limit,
# 2026-11-13); a test replaces a name ``main`` calls in ``cli``.
from src.markdown_stats.cli_options import (  # noqa: E402
    DEFAULT_README_PATH as DEFAULT_README_PATH,
)
from src.markdown_stats.cli import (  # noqa: E402
    main as main,
    _no_request_note as _no_request_note,
    _finish as _finish,
)


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
    "write_year_archives",
    "years_without_review",
    "archived_years",
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
    "_no_request_note",
    "_finish",
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
