"""Feed-health report: the Markdown summary and the JSON payload of a build.

Moved verbatim out of ``src/feed/reporting.py`` (700-line rule, 2026-11-02);
import these names from ``src.feed.reporting``, which re-exports them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import LOG_TIMEZONE
from .report_messages import _CONTROL_CHARS_RE
from .run_report_view import RunReportView

from ..utils.files import atomic_write
from ..utils.text import escape_markdown, escape_markdown_cell, safe_markdown_codespan

@dataclass(frozen=True)
class DuplicateSummary:
    """Description of a deduplicated item cluster."""

    dedupe_key: str
    count: int
    titles: tuple[str, ...]


@dataclass(frozen=True)
class FeedHealthMetrics:
    """Aggregate metrics captured during a feed build."""

    raw_items: int
    filtered_items: int
    deduped_items: int
    new_items: int
    duplicate_count: int
    duplicates: tuple[DuplicateSummary, ...]


def _format_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    try:
        localized = dt.astimezone(LOG_TIMEZONE)
    except Exception:  # pragma: no cover - timezone edge cases
        localized = dt
    return localized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_timestamp_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    try:
        localized = dt.astimezone(LOG_TIMEZONE)
    except Exception:  # pragma: no cover - timezone edge cases
        localized = dt
    return localized.isoformat()


def _skipped_items(report: RunReportView) -> list[dict[str, str]]:
    """Items the build left out because a step failed on them.

    ``RunReport.add_skipped_item`` (``src.feed_build.isolation``). Read
    with ``getattr`` so a report object without the list counts as "none".
    """
    entries = getattr(report, "skipped_items", None)
    if not isinstance(entries, list):
        return []
    return [
        {key: str(entry.get(key) or "") for key in ("source", "title", "error")}
        for entry in entries
        if isinstance(entry, dict)
    ]


def render_feed_health_markdown(
    report: RunReportView,
    metrics: FeedHealthMetrics,
) -> str:
    """Render a human-readable Markdown summary of the latest feed build."""

    lines: list[str] = []
    lines.append("# Feed Health Report")
    lines.append("")
    status = "✅ Erfolgreich" if report.build_successful else "❌ Fehlerhaft"
    lines.append(f"- **Status:** {status}")
    lines.append(f"- **Run-ID:** `{report.run_id}`")
    lines.append(f"- **Start:** {_format_timestamp(report.started_at)}")
    lines.append(f"- **Ende:** {_format_timestamp(report.finished_at)}")
    if report.feed_path:
        # Security (Markdown injection at inline-code-span boundary): the
        # ``feed_path`` string is the POSIX form of ``OUT_PATH`` resolved by
        # ``src.feed.config.resolve_env_path`` — env-controlled (leaked CI
        # env, compromised secret store, intentional misconfig). A literal
        # backtick inside the value closes the inline code span and lets
        # attacker-controlled Markdown / HTML render in the public
        # ``docs/feed_health.md`` artefact. ``safe_markdown_codespan``
        # replaces backticks with apostrophes and strips C0/C1 controls +
        # Trojan-Source BiDi marks + line / paragraph separators that
        # would otherwise split the bullet item or smuggle a fake header.
        lines.append(
            f"- **RSS-Datei:** `{safe_markdown_codespan(report.feed_path)}`"
        )
    lines.append("")

    lines.append("## Pipeline-Kennzahlen")
    lines.append("")
    lines.append("| Schritt | Anzahl |")
    lines.append("| --- | ---: |")
    lines.append(f"| Rohdaten | {metrics.raw_items} |")
    lines.append(f"| Nach Altersfilter | {metrics.filtered_items} |")
    lines.append(f"| Nach Deduplizierung | {metrics.deduped_items} |")
    lines.append(f"| Neue Items seit letztem State | {metrics.new_items} |")
    lines.append(
        f"| Entfernte Duplikate | {metrics.duplicate_count} |")
    lines.append("")

    if report.durations:
        lines.append("### Laufzeiten")
        lines.append("")
        lines.append("| Schritt | Dauer (s) |")
        lines.append("| --- | ---: |")
        for key, value in sorted(report.durations.items()):
            lines.append(f"| {key} | {value:.2f} |")
        lines.append("")

    lines.append("## Providerübersicht")
    lines.append("")
    lines.append("| Provider | Status | Items | Dauer (s) | Details |")
    lines.append("| --- | --- | ---: | ---: | --- |")
    for name in sorted(report.providers):
        entry = report.providers[name]
        status = escape_markdown_cell(entry.status or "unbekannt")
        items = entry.items if entry.items is not None else "—"
        duration = f"{entry.duration:.2f}" if entry.duration is not None else "—"
        detail = escape_markdown_cell(entry.detail or "")
        name_cell = escape_markdown_cell(name)
        lines.append(
            f"| {name_cell} | {status} | {items} | {duration} | {detail} |"
        )
    lines.append("")

    if metrics.duplicate_count:
        lines.append("### Entfernte Duplikate im Detail")
        lines.append("")
        for dup in metrics.duplicates:
            # Security (Trojan-Source / BiDi-mark drift at the inline-code-
            # span sink): ``dup.titles`` and ``dup.dedupe_key`` are derived
            # from provider items' ``title`` / ``guid`` fields — both are
            # upstream-controlled. Pre-fix the local ``_sanitize_code_span``
            # helper only replaced backticks with apostrophes; it did NOT
            # strip the canonical floor (BiDi marks, ZWSP/ZWNJ/ZWJ, BOM,
            # variation selectors, Tag block, C0/C1 controls) that every
            # sibling sanitiser in the codebase covers. The canonical
            # ``safe_markdown_codespan`` helper (a) strips the canonical
            # floor pinned by ``_MARKDOWN_NORMALISE_UNSAFE_RE``,
            # (b) collapses whitespace, (c) replaces backticks with
            # apostrophes, and (d) caps length — the single-sourced
            # inline-code-span helper used by every other sink in
            # ``_build_body`` (``safe_markdown_codespan(report.feed_path)``,
            # ``safe_markdown_codespan(str(error_log_path))``).
            titles = ", ".join(
                f"`{safe_markdown_codespan(title)}`" for title in dup.titles if title.strip()
            )
            title_text = titles or "(keine Titelinformationen)"
            dedupe_key_escaped = safe_markdown_codespan(dup.dedupe_key)
            lines.append(
                f"- **{dup.count}×** Schlüssel `{dedupe_key_escaped}` – Beispiele: {title_text}"
            )
        lines.append("")

    if report.warnings:
        lines.append("## Warnungen")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {escape_markdown(warning)}")
        lines.append("")

    skipped = _skipped_items(report)
    if skipped:
        lines.append("## Übersprungene Meldungen")
        lines.append("")
        for skip in skipped:
            lines.append(
                f"- {escape_markdown(skip['source'])}: {escape_markdown(skip['title'])}"
                f" ({escape_markdown(skip['error'])})"
            )
        lines.append("")

    errors = list(report.iter_error_messages())
    if report.exception_message and report.exception_message not in errors:
        errors.append(report.exception_message)
    if errors:
        lines.append("## Fehler")
        lines.append("")
        for error in errors:
            lines.append(f"- {escape_markdown(error)}")
        lines.append("")

    if not metrics.duplicate_count and not report.warnings and not errors:
        lines.append("Keine zusätzlichen Auffälligkeiten festgestellt.")

    return "\n".join(lines).strip() + "\n"


def write_feed_health_report(
    report: RunReportView,
    metrics: FeedHealthMetrics,
    *,
    output_path: Path,
) -> None:
    """Persist the feed health report to ``output_path`` using an atomic write."""

    markdown = render_feed_health_markdown(report, metrics)
    with atomic_write(
        output_path, mode="w", encoding="utf-8", permissions=0o644
    ) as f:
        f.write(markdown)


def build_feed_health_payload(
    report: RunReportView,
    metrics: FeedHealthMetrics,
) -> dict[str, Any]:
    """Create a JSON-serialisable structure summarising the feed build."""

    # Security (Trojan-Source / BiDi-mark drift at the JSON sink):
    # ``summary.dedupe_key`` and each ``summary.titles`` element are
    # derived from provider items' ``guid`` / ``title`` fields. With
    # ``json.dump(..., ensure_ascii=False)`` (see ``write_feed_health_json``)
    # the canonical-floor primitives (BiDi marks, ZWSP family, BOM,
    # variation selectors, Tag block, C0/C1 controls) would otherwise
    # land as raw UTF-8 bytes inside ``docs/feed-health.json`` — the
    # public, GitHub-Pages-served, machine-readable companion of the
    # Markdown sink. ``_CONTROL_CHARS_RE`` is the canonical sibling
    # of ``_INVISIBLE_DANGEROUS_RE`` (covers C0/C1 controls + the BiDi /
    # zero-width / variation-selector / Tag-block family) and is the
    # same regex the existing sanitisation walker pins for every
    # operator-facing sink in this module.
    duplicate_entries = [
        {
            "dedupe_key": _CONTROL_CHARS_RE.sub("", summary.dedupe_key),
            "count": summary.count,
            "titles": [
                _CONTROL_CHARS_RE.sub("", title)
                for title in summary.titles
                if title.strip()
            ],
        }
        for summary in metrics.duplicates
    ]

    provider_entries = []
    for name in sorted(report.providers):
        entry = report.providers[name]
        provider_entries.append(
            {
                "name": name,
                "enabled": entry.enabled,
                "status": entry.status,
                "fetch_type": entry.fetch_type,
                "detail": entry.detail,
                "items": entry.items,
                "duration": entry.duration,
            }
        )

    warnings = list(report.warnings)
    errors = list(report.iter_error_messages())
    if report.exception_message and report.exception_message not in errors:
        errors.append(report.exception_message)

    # Security (Trojan-Source / control-character drift at the JSON sink):
    # ``report.feed_path`` is the POSIX form of ``OUT_PATH`` resolved by
    # :func:`src.feed.config.resolve_env_path`. ``validate_path`` only
    # checks that the path resolves under one of ``ALLOWED_ROOTS``
    # (``docs/``, ``data/``, ``log/``); it does NOT strip the canonical
    # CVE-2021-42574 attack-byte union (BiDi marks, ZWSP family, BOM,
    # variation selectors, Tag block, C0/C1 controls) from the path
    # bytes. With ``json.dump(..., ensure_ascii=False)`` the bytes
    # would otherwise survive as raw UTF-8 in the
    # ``docs/feed-health.json`` artefact that every build writes and any
    # local reader of it consumes. (The file is git-ignored: it is not
    # committed and not served on GitHub Pages; ``docs/development.md``.)
    # The companion
    # Markdown sink (``render_feed_health_markdown`` line 593) already
    # routes ``feed_path`` through :func:`src.utils.text.safe_markdown_codespan`;
    # this scrub mirrors the same defence at the JSON-sink boundary.
    # Same regex as the duplicate-summary scrub above so a future
    # widening of ``_CONTROL_CHARS_RE`` automatically covers both
    # sinks.
    feed_path_value: str | None = None
    if report.feed_path is not None:
        feed_path_value = _CONTROL_CHARS_RE.sub("", report.feed_path)

    return {
        "run": {
            "id": report.run_id,
            "status": "success" if report.build_successful else "error",
            "started_at": _format_timestamp_iso(report.started_at),
            "finished_at": _format_timestamp_iso(report.finished_at),
            "feed_path": feed_path_value,
            "raw_item_count": report.raw_item_count,
            "final_item_count": report.final_item_count,
        },
        "metrics": {
            "raw_items": metrics.raw_items,
            "filtered_items": metrics.filtered_items,
            "deduped_items": metrics.deduped_items,
            "new_items": metrics.new_items,
            "duplicate_count": metrics.duplicate_count,
        },
        "duplicates": duplicate_entries,
        "durations": {
            key: value for key, value in sorted(report.durations.items())
        },
        "providers": provider_entries,
        "warnings": warnings,
        "errors": errors,
        # Always present; empty when the build skipped no item.
        "skipped_items": _skipped_items(report),
    }


def write_feed_health_json(
    report: RunReportView,
    metrics: FeedHealthMetrics,
    *,
    output_path: Path,
) -> None:
    """Persist the feed health payload as JSON using an atomic write."""

    payload = build_feed_health_payload(report, metrics)
    # Security (Coordinate finite/range drift, committed-writer
    # defence-in-depth — non-coordinate sibling-writer landscape):
    # ``allow_nan=False`` mirrors the canonical writer-side pin
    # established in Round 1485 at
    # :func:`src.places.merge.write_stations` and extended in
    # Round 1487 to the five sibling ``data/stations.json``
    # writers + :func:`src.utils.cache.write_cache`.
    # ``docs/feed-health.json`` is the machine-readable companion of
    # the Markdown sink, written by every build (git-ignored, so not
    # committed and not served on GitHub Pages).
    # The payload carries ``providers[i]["duration"]`` (the
    # ``float | None`` value of :attr:`ProviderReport.duration`)
    # and ``durations`` (the ``dict[str, float]`` slot of
    # :attr:`RunReport.durations`) — both ``float``-typed slots
    # that accept :func:`float`-NaN / -Inf from any caller of
    # :meth:`ProviderReport.finish(duration=...)` /
    # :meth:`RunReport.finish(durations={...})`. Without the pin
    # a programmatic in-process injection (future provider plugin
    # wrapping a third-party instrumentation SDK that surfaces
    # ``NaN`` for a missing observation, manual diagnostic
    # harness, poisoned previous-run state that round-trips
    # through :func:`json.loads` lenient mode) lands non-standard
    # ``NaN`` / ``Infinity`` / ``-Infinity`` literals (invalid
    # per RFC 8259) in the committed artefact. Every conforming
    # downstream parser refuses them (``JSON.parse`` in every
    # modern browser, ``serde_json`` Rust strict mode,
    # ``encoding/json`` Go), so a single NaN slip breaks the
    # SIEM dashboards for the build cycle.
    with atomic_write(
        output_path, mode="w", encoding="utf-8", permissions=0o644
    ) as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")


__all__ = [
    "DuplicateSummary",
    "FeedHealthMetrics",
    "_format_timestamp",
    "_format_timestamp_iso",
    "render_feed_health_markdown",
    "write_feed_health_report",
    "build_feed_health_payload",
    "write_feed_health_json",
]
