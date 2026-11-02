"""Pre-computed dashboard summary (``docs/stats-summary.json``).

Moved verbatim from ``scripts/generate_markdown_stats.py``, which
re-exports every name. ``REPO_ROOT`` is recomputed from this file's
location so ``DEFAULT_SUMMARY_PATH`` stays the same path.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from src.markdown_stats.direction_coverage import (
    CORRIDOR_SILENCE_NOTICE_HOURS,
    DIRECTION_SILENCE_NOTICE_HOURS,
    DIRECTION_SILENCE_NOTICE_PEER_ROWS,
    _last_seen_per_direction,
)
from src.markdown_stats.rows import (
    AusfallAggregate,
    StammstreckeAggregate,
    StammstreckeRow,
    StoerungAggregate,
)
from src.utils.files import atomic_write
from src.utils.serialize import scrub_trojan_source_primitives
from src.utils.stammstrecke_poll import POLL_STALE_HOURS, PollStatus
from src.utils.stats import STAMMSTRECKE_DIRECTIONS

REPO_ROOT = Path(__file__).resolve().parents[2]


# ----- Pre-computed dashboard summary --------------------------------
#
# Audit E.3: ``docs/assets/site-js/`` used to fetch the three raw yearly
# ledgers from ``raw.githubusercontent.com`` on every page load and do
# the roll-up in the browser — ~705 KB in September, and growing every
# tick, to render a handful of KPI tiles and bar charts. Two problems:
# the payload grows without bound over the year, and the data comes from
# an origin that is not GitHub Pages, has its own rate limits, and is
# outside the Pages availability promise.
#
# The aggregation already happens here, once per tick, for the Markdown
# dashboard. Emitting the same numbers as JSON next to the page costs
# nothing extra and replaces ~705 KB of CSV with ~2 KB that stays flat
# all year (the dimensions are fixed: 7 weekdays, 24 hours, a handful of
# providers, lines and directions).
#
# The raw ledgers stay exactly where they are under ``data/stats/`` and
# stay linked for anyone who wants to analyse them.
SUMMARY_FILENAME: Final = "stats-summary.json"
DEFAULT_SUMMARY_PATH: Final = REPO_ROOT / "docs" / SUMMARY_FILENAME

# Bumped whenever the shape changes incompatibly. ``site.js`` refuses a
# version it does not know rather than rendering half a dashboard from
# fields it cannot find.
SUMMARY_SCHEMA_VERSION: Final = 1

# Rolling window for the "letzte Stunde" live tile. Mirrors
# ``render_readme_stammstrecke_live_block``.
LIVE_WINDOW_MINUTES: Final = 60


def resolve_summary_path(
    explicit: Path | None,
    *,
    markdown_output: Path,
) -> Path:
    """Decide where the summary goes.

    Defaults to a *sibling of the Markdown dashboard* rather than to a
    fixed repository path. A caller that redirects ``--output`` into a
    temporary directory — every test does — then redirects the summary
    with it, instead of silently overwriting the published
    ``docs/stats-summary.json``. That exact class of bug once stamped
    placeholder blocks into the committed README (see the ``sm_window``
    gate in :func:`main` for the audit trail), so the safe default is
    the one that follows the caller.
    """
    if explicit is not None:
        return explicit
    return markdown_output.parent / SUMMARY_FILENAME


def _live_window_delay(
    rows: list[StammstreckeRow],
    *,
    now: datetime,
    minutes: int = LIVE_WINDOW_MINUTES,
) -> tuple[float | None, int]:
    """Mean ``delay_minutes`` over the trailing *minutes*.

    Returns ``(average, sample_count)``; the average is ``None`` when the
    window holds no sample, which the page renders as "n/a" rather than
    as a zero-minute delay.
    """
    cutoff = now - timedelta(minutes=minutes)
    delays = [row.delay_minutes for row in rows if row.timestamp >= cutoff]
    if not delays:
        return (None, 0)
    return (statistics.fmean(delays), len(delays))


def _live_fetch_covers(
    status: PollStatus | None,
    *,
    now: datetime,
    minutes: int = LIVE_WINDOW_MINUTES,
) -> bool:
    """Did the monitor's fetch work inside the trailing live window?

    Only then does an empty window mean that no train ran ("Keine
    Fahrten"); without a successful fetch in the window it means nothing
    was measured ("N/A"). The bound is the live window itself, not the
    health check's three hours: a fetch two hours old says nothing about
    the last 60 minutes.
    """
    if status is None or status.last_success is None:
        return False
    return now - status.last_success <= timedelta(minutes=minutes)


def _direction_coverage(
    window_rows: list[StammstreckeRow],
    *,
    all_rows: list[StammstreckeRow],
    window_days: int,
    directions: tuple[str, ...] = STAMMSTRECKE_DIRECTIONS,
    poll: dict[str, object] | None = None,
) -> dict[str, object]:
    """Per-direction evidence for the coverage banner.

    Deliberately ships the *evidence*, not the verdict: when each direction
    was last seen, how many journeys a peer direction logged since then, how
    many rows it contributes to the window — plus the thresholds, so the
    browser applies the rule of :func:`find_silent_coverage_directions`
    rather than a copy of it that can drift. ``site.js`` evaluates it against
    the reader's own clock, which is why the verdict is not baked in here.

    ``peer_rows_since`` is the one number the browser cannot derive: the raw
    ledger stopped being shipped when the dashboard moved to this summary. It
    counts journeys in *other* canonical directions after this direction's
    last one — the evidence that trains were running while this direction
    logged nothing.

    When a silent direction resumes, its ``last_seen`` moves and the banner
    disappears on the next tick with nobody editing anything — which is the
    whole point.

    *poll* (``last_success``, ``stale_hours``) is the monitor's own record
    of its last working fetch (:func:`_poll_evidence`). The browser names a
    dark corridor only while that fetch is recent — the same gate as
    ``fetch_working`` in :func:`find_silent_coverage_directions`. Omitted
    when unknown; the browser then keeps its earlier behaviour.
    """
    last_seen = _last_seen_per_direction(all_rows)
    in_window: dict[str, int] = defaultdict(int)
    for row in window_rows:
        in_window[row.direction] += 1

    known = sorted({*directions, *last_seen, *in_window})
    payload: dict[str, object] = {
        "window_days": window_days,
        "silence_hours": DIRECTION_SILENCE_NOTICE_HOURS,
        "peer_rows_required": DIRECTION_SILENCE_NOTICE_PEER_ROWS,
        "corridor_silence_hours": CORRIDOR_SILENCE_NOTICE_HOURS,
        "directions": {
            direction: {
                "rows_in_window": in_window.get(direction, 0),
                "last_seen": (
                    last_seen[direction].isoformat(timespec="seconds")
                    if direction in last_seen
                    else None
                ),
                "peer_rows_since": (
                    sum(
                        1
                        for row in all_rows
                        if row.direction != direction
                        and row.direction in directions
                        and row.timestamp > last_seen[direction]
                    )
                    if direction in last_seen
                    else 0
                ),
            }
            for direction in known
        },
    }
    if poll is not None:
        payload["poll"] = poll
    return payload


def _poll_evidence(status: PollStatus | None) -> dict[str, object]:
    """The monitor's last working fetch, as shipped to the browser."""
    last_success = status.last_success if status is not None else None
    return {
        "last_success": (
            last_success.isoformat(timespec="seconds") if last_success else None
        ),
        "stale_hours": POLL_STALE_HOURS,
    }


def build_stats_summary(
    *,
    year: int,
    generated_at: datetime,
    stammstrecke_rows: list[StammstreckeRow],
    stammstrecke: StammstreckeAggregate,
    stoerungen: StoerungAggregate,
    ausfaelle: AusfallAggregate,
    window_rows: list[StammstreckeRow],
    all_window_rows: list[StammstreckeRow],
    window_days: int,
    poll: dict[str, object] | None = None,
) -> dict[str, object]:
    """Assemble the JSON payload ``docs/assets/site-js/`` renders from.

    Every number here is one the browser used to derive itself from the
    raw ledgers. ``avg_delay_minutes`` and ``max_delay_minutes`` are the
    only two the Markdown dashboard does not already carry, so they are
    computed here rather than widened into
    :class:`StammstreckeAggregate` (which exists to serve the Markdown
    render and has no use for them).
    """
    delays = [row.delay_minutes for row in stammstrecke_rows]
    live_avg, live_count = _live_window_delay(
        stammstrecke_rows, now=generated_at
    )

    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "year": year,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "stammstrecke": {
            "total_observations": stammstrecke.total_observations,
            "avg_delay_minutes": statistics.fmean(delays) if delays else 0.0,
            "max_delay_minutes": max(delays) if delays else 0.0,
            "threshold_minutes": stammstrecke.threshold_minutes,
            "threshold_exceedances": stammstrecke.threshold_exceedances,
            "by_weekday_avg": dict(stammstrecke.by_weekday_avg),
            "by_hour_avg": {
                f"{hour:02d}": value
                for hour, value in stammstrecke.by_hour_avg.items()
            },
            "by_direction": dict(stammstrecke.by_direction),
            "live": {
                "window_minutes": LIVE_WINDOW_MINUTES,
                "avg_delay_minutes": live_avg,
                "observations": live_count,
            },
            "coverage": _direction_coverage(
                window_rows,
                all_rows=all_window_rows,
                window_days=window_days,
                poll=poll,
            ),
        },
        "stoerungen": {
            "total": stoerungen.total_disruptions,
            "by_provider": dict(stoerungen.by_provider),
            "by_weekday": dict(stoerungen.by_weekday),
            "by_hour": {
                f"{hour:02d}": value for hour, value in stoerungen.by_hour.items()
            },
        },
        "ausfaelle": {
            "total": ausfaelle.total_cancellations,
            "by_line": dict(ausfaelle.by_line),
            "by_direction": dict(ausfaelle.by_direction),
            "by_weekday": dict(ausfaelle.by_weekday),
            "by_hour": {
                f"{hour:02d}": value for hour, value in ausfaelle.by_hour.items()
            },
        },
    }


def write_stats_summary(
    summary: dict[str, object],
    *,
    output_path: Path = DEFAULT_SUMMARY_PATH,
) -> None:
    """Atomically write *summary* as JSON to *output_path*.

    Same :func:`src.utils.files.atomic_write` rationale as
    :func:`write_dashboard`: the file is served from GitHub Pages, so a
    partially-written replacement would be a parse error for every
    visitor until the next tick.

    Floats are rounded to two decimals. The dashboard renders one
    decimal place, and full binary precision would otherwise churn the
    file — and therefore the commit log — on every tick for digits
    nobody sees.

    Security (Trojan-Source / BiDi-Mark, ingestion-boundary defence):
    provider names, line labels and direction strings in this payload
    originate in upstream feeds, and the file is committed to ``main``
    and served from GitHub Pages. The canonical CVE-2021-42574
    attack-byte union is stripped from every reachable string BEFORE
    ``json.dumps`` — same shape as
    :func:`src.places.merge.write_stations`. ``ensure_ascii=False`` is
    kept at the writer so legitimate German content (Meidling,
    Praterstern, umlauts in line names) stays compact in the 30-minute
    commit diff.

    ``allow_nan=False`` is pinned so Python's lenient default cannot
    emit ``NaN`` / ``Infinity`` literals — invalid per RFC 8259 §6, and
    a hard ``JSON.parse`` failure in the browser, i.e. the whole
    dashboard rather than one tile. :func:`_round_floats` already maps
    non-finite values to ``null``; the pin is the defence in depth that
    makes a future bypass fail loudly instead of shipping a broken
    document.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        scrub_trojan_source_primitives(_round_floats(summary)),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    with atomic_write(
        output_path, mode="w", encoding="utf-8", permissions=0o644
    ) as fh:
        fh.write(payload + "\n")


def _round_floats(value: object, *, digits: int = 2) -> object:
    """Recursively round every float in *value* to *digits* decimals.

    A non-finite float becomes ``None``. ``NaN`` / ``Infinity`` are not
    valid JSON (RFC 8259 §6) and would make the browser's ``JSON.parse``
    throw — taking the whole dashboard down, not one tile. ``None``
    renders through the same "no data" path an empty window already
    uses.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, digits)
    if isinstance(value, dict):
        return {key: _round_floats(item, digits=digits) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_floats(item, digits=digits) for item in value]
    return value
