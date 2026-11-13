"""What a build writes: the feed files, the state and the health report.

Until 2026-11-14 these blocks stood inside ``build_feed.main``. Operator
decision Michael 2026-11-14 (card "Wähle, wie build_feed.py (692 Zeilen)
wieder Luft bekommt", answer "Teile von main auslagern": "Schreiben von Feed,
Zustand und Health-Bericht wird zu eigenen Funktionen; das ist ein Umbau,
kein Verschieben, und bringt rund 570 Zeilen."). ``main`` keeps the order of
the steps and calls the functions below; the feed chain
(``_assemble_feed_items``), ``lint`` and ``main`` stay in ``src/build_feed.py``.

Every function takes what it works on as a parameter; the statements and
their comments are the ones ``main`` had. The values of ``feed_config`` are
read when a function runs, as before.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..feed.reporting import (
    DuplicateSummary, FeedHealthMetrics, RunReport, write_feed_health_report, write_feed_health_json,
)
from ..utils.files import atomic_write
from ..utils.logging import sanitize_log_arg

from .base import log, validate_path
from .rss import _make_rss
from .state import _save_state

__all__ = [
    "AssemblyCounts", "health_metrics_from", "write_health_outputs", "write_feed", "write_en_mirror",
    "save_state_or_warn", "record_successful_build",
]


class AssemblyCounts(Protocol):
    """What the functions here read of ``build_feed.FeedAssemblyStats``."""

    filtered_count: int
    deduped_count: int
    duplicates_removed: int
    duplicate_summaries: list[DuplicateSummary]
    filter_duration: float
    dedupe_duration: float


def health_metrics_from(raw_count: int, stats: AssemblyCounts, new_items_count: int) -> FeedHealthMetrics:
    """The health metrics of a run from its counters, as they stand at the call."""
    return FeedHealthMetrics(
        raw_items=raw_count,
        filtered_items=stats.filtered_count,
        deduped_items=stats.deduped_count,
        new_items=new_items_count,
        duplicate_count=stats.duplicates_removed,
        duplicates=tuple(stats.duplicate_summaries),
    )


def write_health_outputs(
    report: RunReport, active_metrics: FeedHealthMetrics, health_path: Path, health_json_path: Path
) -> None:
    """Write the health report as Markdown and as JSON; a failed write is logged and does not end the run."""
    try:
        write_feed_health_report(
            report, active_metrics, output_path=health_path
        )
    except Exception as exc:  # pragma: no cover - defensive
        # Security (Clear-Text-Logging Drift): defensive framework catch.
        log.warning(
            "Feed-Health-Markdown konnte nicht geschrieben werden: %s",
            sanitize_log_arg(str(exc)),
        )
    try:
        write_feed_health_json(
            report, active_metrics, output_path=health_json_path
        )
    except Exception as exc:  # pragma: no cover - defensive
        # Security (Clear-Text-Logging Drift): defensive framework catch.
        log.warning(
            "Feed-Health-JSON konnte nicht geschrieben werden: %s",
            sanitize_log_arg(str(exc)),
        )


def write_feed(rss_de: str) -> Path:
    """Write the German feed to ``OUT_PATH`` and return the validated path; an error is raised to the caller."""
    out_path = validate_path(Path(feed_config.OUT_PATH), "OUT_PATH")
    with atomic_write(
        out_path, mode="w", encoding="utf-8", permissions=0o644
    ) as f:
        f.write(rss_de)
    return out_path


def write_en_mirror(out_path: Path, items: list[FeedItem], now: datetime, state: dict[str, Any]) -> None:
    """Render and write ``feed.en.xml`` next to ``out_path``; only the path check raises."""
    # English mirror — written next to ``feed.xml`` as ``feed.en.xml``.
    # Failures during translation degrade gracefully to the German
    # original via ``_translate_text`` / ``_apply_lang_overlay``;
    # write errors are isolated so the primary feed (already on disk)
    # stays committed even if the EN file cannot be produced.
    en_path = out_path.with_name("feed.en.xml")
    en_out_path = validate_path(en_path, "OUT_PATH")
    try:
        rss_en = _make_rss(
            items, now, state, lang="en"
        )
        with atomic_write(
            en_out_path, mode="w", encoding="utf-8", permissions=0o644
        ) as f:
            f.write(rss_en)
    except Exception as exc:
        log.warning(
            "EN-Feed konnte nicht geschrieben werden (%s) – "
            "deutscher Feed ist bereits aktualisiert.",
            sanitize_log_arg(str(exc)),
        )


def save_state_or_warn(state: dict[str, Any], report: RunReport) -> None:
    """Save the state; a failure is logged and entered into ``report`` as a warning, the run goes on."""
    # Without deletions: a message that left keeps its entry (``_drop_old_items``).
    try:
        _save_state(state)
    except Exception as e:
        # Security (Clear-Text-Logging Drift): broad framework catch.
        log.warning(
            "State speichern fehlgeschlagen (%s) – Feed wurde geschrieben, State bleibt veraltet.",
            sanitize_log_arg(str(e)),
        )
        # Surface the failure on the structured report so monitoring
        # / dashboards see "degraded" instead of a clean
        # ``build_successful=True``. Pre-fix the swallowed exception
        # let the run report record a fully-successful build while
        # first_seen / translations / stats counters silently
        # drifted out of sync with the on-disk feed.
        report.add_warning(
            f"State save failed: {sanitize_log_arg(type(e).__name__)} — "
            "first_seen / translations / stats may drift on the next run."
        )


def record_successful_build(
    report: RunReport,
    out_path: Path,
    items: list[FeedItem],
    *,
    raw_count: int,
    stats: AssemblyCounts,
    collect_duration: float,
    rss_duration: float,
    total_duration: float,
) -> None:
    """Log the written feed and close ``report`` as a successful build with its durations."""
    log.info(
        "Feed geschrieben: %s (%d Items) in %.2fs (RSS-Erzeugung: %.2fs)",
        out_path,
        min(len(items), feed_config.MAX_ITEMS),
        total_duration,
        rss_duration,
    )
    report.finish(
        build_successful=True,
        raw_items=raw_count,
        final_items=len(items),
        durations={
            "collect": collect_duration,
            "filter": stats.filter_duration,
            "dedupe": stats.dedupe_duration,
            "rss": rss_duration,
            "total": total_duration,
        },
        feed_path=out_path,
    )
