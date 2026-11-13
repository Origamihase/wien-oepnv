"""The report ``build_feed.lint`` prints, and the exit code that follows from it.

Until 2026-11-14 this block stood inside ``build_feed.lint``. It left with
the output blocks of ``main`` (operator decision Michael 2026-11-14, see
``src/feed_build/outputs.py``); ``lint`` keeps the order of its steps and
calls :func:`print_lint_report` where the block stood. The statements and
their comments are the ones ``lint`` had.
"""

from __future__ import annotations

from ..feed_types import FeedItem
from ..feed.reporting import DuplicateSummary, FeedHealthMetrics, RunReport

from .base import _sanitize_text

__all__ = ["print_lint_report"]


def print_lint_report(
    metrics: FeedHealthMetrics,
    duplicate_summaries: list[DuplicateSummary],
    stale_cache_messages: list[str],
    missing_guid_items: list[FeedItem],
    report: RunReport,
) -> int:
    """Print the lint report to stdout and return the exit code of ``lint``.

    0: nothing found; 1: duplicates, items without GUID or stale caches;
    2: ``report`` holds a provider error (asked after the findings are
    printed, where ``lint`` asked it).
    """
    print("Feed-Lint Bericht")
    print("==================")
    print(f"Rohdaten: {metrics.raw_items}")
    print(f"Nach Altersfilter: {metrics.filtered_items}")
    print(
        f"Nach Deduplizierung: {metrics.deduped_items} "
        f"(entfernte Duplikate: {metrics.duplicate_count})"
    )
    print(f"Neue Items (vs. State): {metrics.new_items}")

    if duplicate_summaries:
        print("\nErkannte Duplikat-Gruppen:")
        for summary in duplicate_summaries:
            titles = ", ".join(
                title or "<ohne Titel>" for title in summary.titles if title is not None
            )
            titles = titles or "<keine Beispiele>"
            print(
                f"- {summary.count}× Schlüssel {summary.dedupe_key}: {titles}"
            )

    if stale_cache_messages:
        print("\nVeraltete Cache-Dateien:")
        for message in stale_cache_messages:
            print(f"- {message}")

    if missing_guid_items:
        print("\nEinträge ohne GUID:")
        for item in missing_guid_items:
            # Security: ``source`` / ``title`` are upstream-controlled and
            # reach this operator-facing stdout sink BEFORE the per-item
            # ``_format_item_content`` sanitisation runs (lint never formats
            # the items). Route both through ``_sanitize_text`` so the
            # canonical ``_CONTROL_RE`` floor (C0/C1 + BiDi + zero-width +
            # line/paragraph separators + Tag/VS blocks) is stripped here too
            # — the sibling duplicate-group print already sanitises its
            # titles via ``_summarize_duplicates``; this closes the
            # un-sanitised sibling path (Trojan-Source / terminal-escape /
            # log-forgery on the lint report).
            source = _sanitize_text(str(item.get("source") or "unbekannt"))
            title = _sanitize_text(str(item.get("title") or "<ohne Titel>"))
            print(f"- {source}: {title}")

    provider_failures = report.has_errors()
    if provider_failures:
        print("\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.")

    if not duplicate_summaries and not missing_guid_items and not provider_failures:
        print("\nKeine strukturellen Probleme gefunden.")

    if provider_failures:
        exit_code = 2
    elif duplicate_summaries or missing_guid_items or stale_cache_messages:
        exit_code = 1
    else:
        exit_code = 0
    return exit_code
