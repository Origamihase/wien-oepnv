"""Reporting primitives shared by feed builder components.

This module keeps ``ProviderReport`` and ``RunReport``. The rest was moved
verbatim (700-line rule, 2026-11-02) and is re-exported here:
``report_messages`` (message cleaning and bounds), ``feed_health`` (the
Markdown and JSON health report) and ``github_issue`` (the auto-issue for a
failed run; its ``_build_body`` reads ``error_log_path`` from there).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from threading import RLock
from time import perf_counter
from collections.abc import Iterator

from .feed_health import (
    DuplicateSummary,
    FeedHealthMetrics,
    _format_timestamp,
    _format_timestamp_iso,
    build_feed_health_payload,
    render_feed_health_markdown,
    write_feed_health_json,
    write_feed_health_report,
)
from .github_issue import (
    _BODY_TRUNCATION_MARKER,
    _GITHUB_ISSUE_URL_RE,
    _GITHUB_REPO_SLUG_RE,
    _MAX_GITHUB_BODY_LENGTH,
    _GithubIssueConfig,
    _GithubIssueReporter,
    _bounded_github_body,
    _extract_github_issue_number,
    _is_trusted_github_api,
    _is_valid_github_repo_slug,
    _split_csv,
    _submit_github_issue,
)
from .logging import diagnostics_log_path, error_log_path, prune_log_file
from .report_messages import (
    _CONTROL_CHARS_RE,
    _MAX_REPORT_MESSAGE_COUNT,
    _MAX_REPORT_MESSAGE_LENGTH,
    _REPORT_TRUNCATION_MARKER,
    _bounded_message,
    _sanitize_log_detail,
    clean_message,
)

from ..utils.logging import sanitize_log_arg

log = logging.getLogger("build_feed")


@dataclass
class ProviderReport:
    """Tracks the execution status and results of a single provider."""
    name: str
    enabled: bool
    fetch_type: str = "unknown"
    # ok, ok-empty (nothing to report, healthy), empty (no data, suspect),
    # error, disabled, skipped
    status: str = "pending"
    detail: str | None = None
    items: int | None = None
    duration: float | None = None
    _started_at: float | None = None

    def mark_disabled(self) -> None:
        """Explicitly mark the provider as disabled."""
        self.enabled = False
        self.status = "disabled"

    def start(self) -> None:
        """Mark the provider execution as started."""
        self._started_at = perf_counter()
        if self.status == "disabled":
            return
        self.status = "running"

    def finish(
        self,
        status: str,
        *,
        items: int | None = None,
        detail: str | None = None,
        duration: float | None = None,
    ) -> None:
        """Finalize the provider execution with a status, item count, and optional details."""
        if duration is None and self._started_at is not None:
            duration = perf_counter() - self._started_at
        self.duration = duration
        self.items = items
        self.detail = detail
        self.status = status


class _RunErrorCollector(logging.Handler):
    def __init__(self, report: RunReport) -> None:
        super().__init__(level=logging.ERROR)
        self.report = report
        self._formatter = logging.Formatter()
        self._detached = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._detached:
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if record.exc_info:
            try:
                exc_text = self._formatter.formatException(record.exc_info)
            except Exception:
                exc_text = ""
            if exc_text:
                msg = f"{msg}\n{exc_text}"
        source = record.name or "root"
        composed = f"{source}: {msg}" if msg else source
        self.report.add_error_message(composed)


@dataclass
class RunReport:
    """Collects all metrics, errors, and status updates for a complete feed build run."""
    statuses: list[tuple[str, bool]]
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    providers: dict[str, ProviderReport] = field(default_factory=dict)
    raw_item_count: int | None = None
    final_item_count: int | None = None
    durations: dict[str, float] = field(default_factory=dict)
    feed_path: str | None = None
    build_successful: bool = False
    exception_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    _error_messages: list[str] = field(default_factory=list)
    _seen_errors: set[str] = field(default_factory=set)
    _seen_warnings: set[str] = field(default_factory=set)
    finished_at: datetime | None = None
    _error_collector: _RunErrorCollector | None = None
    _lock: RLock = field(default_factory=RLock)
    _issue_submitted: bool = False

    def __post_init__(self) -> None:
        for name, enabled in self.statuses:
            normalized = str(name)
            entry = ProviderReport(name=normalized, enabled=enabled)
            if not enabled:
                entry.mark_disabled()
            self.providers[normalized] = entry

    @property
    def run_id(self) -> str:
        """Return a unique identifier for the run based on the start time."""
        return self.started_at.strftime("%Y%m%dT%H%M%SZ")

    def register_provider(self, name: str, enabled: bool, fetch_type: str) -> None:
        """Register a provider for tracking."""
        normalized = str(name)
        with self._lock:
            entry = self.providers.get(normalized)
            if entry is None:
                entry = ProviderReport(name=normalized, enabled=enabled, fetch_type=fetch_type)
                self.providers[normalized] = entry
            else:
                entry.enabled = enabled
                entry.fetch_type = fetch_type
            if not enabled:
                entry.mark_disabled()
            elif entry.status == "disabled":
                entry.status = "pending"

    def provider_started(self, name: str) -> None:
        """Mark a provider as started."""
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=True)
                self.providers[name] = entry
            entry.start()

    def provider_success(
        self,
        name: str,
        *,
        items: int,
        status: str = "ok",
        detail: str | None = None,
    ) -> None:
        """Record a successful provider run with the number of collected items."""
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=True)
                self.providers[name] = entry
            entry.finish(status, items=items, detail=clean_message(detail))

    def provider_empty(self, name: str, message: str | None = None) -> None:
        """Record that a provider ran successfully but returned no items."""
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=True)
                self.providers[name] = entry
            entry.finish("empty", detail=clean_message(message))

    def provider_error(self, name: str, message: str | None = None) -> None:
        """Record a provider failure with an error message."""
        cleaned = ""
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=True)
                self.providers[name] = entry
            cleaned = clean_message(message)
            entry.finish("error", detail=cleaned)
        if cleaned:
            self.add_error_message(f"{name}: {cleaned}")

    def provider_disabled(self, name: str, message: str | None = None) -> None:
        """Record that a provider was disabled during execution."""
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=False)
                self.providers[name] = entry
            entry.finish("disabled", detail=clean_message(message))

    def add_warning(self, message: str) -> None:
        """Add a global warning to the report."""
        cleaned = _bounded_message(clean_message(message))
        if not cleaned:
            return
        with self._lock:
            if cleaned in self._seen_warnings:
                return
            if len(self.warnings) >= _MAX_REPORT_MESSAGE_COUNT:
                return
            self._seen_warnings.add(cleaned)
            self.warnings.append(cleaned)

    def add_error_message(self, message: str) -> None:
        """Add a global error message to the report."""
        cleaned = _bounded_message(clean_message(message))
        if not cleaned:
            return
        with self._lock:
            if cleaned in self._seen_errors:
                return
            if len(self._error_messages) >= _MAX_REPORT_MESSAGE_COUNT:
                return
            self._seen_errors.add(cleaned)
            self._error_messages.append(cleaned)

    def iter_error_messages(self) -> Iterator[str]:
        with self._lock:
            # Snapshot the list to release lock immediately
            errors = list(self._error_messages)
        yield from errors

    def has_errors(self) -> bool:
        """Return True if any errors were recorded during the run."""
        with self._lock:
            if self.exception_message:
                return True
            if any(entry.status == "error" for entry in self.providers.values()):
                return True
            return bool(self._error_messages)

    def attach_error_collector(self) -> None:
        if self._error_collector is not None:
            return
        collector = _RunErrorCollector(self)
        logging.getLogger().addHandler(collector)
        self._error_collector = collector

    def detach_error_collector(self) -> None:
        if self._error_collector is None:
            return
        self._error_collector._detached = True
        logging.getLogger().removeHandler(self._error_collector)
        self._error_collector = None

    def finish(
        self,
        *,
        build_successful: bool,
        raw_items: int | None = None,
        final_items: int | None = None,
        durations: dict[str, float] | None = None,
        feed_path: Path | None = None,
    ) -> None:
        self.build_successful = build_successful
        if raw_items is not None:
            self.raw_item_count = raw_items
        if final_items is not None:
            self.final_item_count = final_items
        if durations:
            self.durations.update(durations)
        if feed_path is not None:
            self.feed_path = feed_path.as_posix()
        self.finished_at = datetime.now(UTC)

    def record_exception(self, exc: Exception) -> None:
        message = f"{exc.__class__.__name__}: {exc}"
        self.exception_message = clean_message(message)
        # Don't also push an "Ausnahme: …" entry into the error list: every
        # rendering sink already appends ``exception_message`` to its error
        # output (guarded by ``not in errors``), and the prefixed copy would
        # never match that guard — so the exception was being listed twice.
        # The structured "**Ausnahme:**" field still carries the label.

    def prune_logs(self) -> None:
        now = self.started_at
        prune_log_file(diagnostics_log_path, now=now)
        prune_log_file(error_log_path, now=now)

    def _provider_summary(self) -> str:
        summaries: list[str] = []
        for name in sorted(self.providers):
            entry = self.providers[name]
            details: list[str] = []
            # ``ok-empty`` is deliberately absent: the status already states
            # that the provider had nothing to report, so "0 Items" next to
            # "Keine Vorfälle" would say it a third time. ``empty`` keeps the
            # count because there the zero is the symptom being reported.
            if entry.items is not None and entry.status in {"ok", "empty"}:
                details.append(f"{entry.items} Items")
            if entry.detail:
                details.append(entry.detail)
            if entry.duration is not None:
                details.append(f"{entry.duration:.2f}s")
            details_str = ", ".join(details)
            if entry.status == "disabled":
                summaries.append(f"{name}:disabled")
                continue
            if entry.status == "pending":
                summaries.append(f"{name}:pending")
                continue
            if entry.status == "error":
                if details_str:
                    summaries.append(f"{name}:error({details_str})")
                else:
                    summaries.append(f"{name}:error")
                continue
            # "nothing to report" and "no data" both used to render as
            # ``:empty``, so a dashboard could not tell a healthy quiet
            # provider from a provider that failed to deliver.
            if entry.status in ("empty", "ok-empty"):
                if details_str:
                    summaries.append(f"{name}:{entry.status}({details_str})")
                else:
                    summaries.append(f"{name}:{entry.status}")
                continue
            if entry.status == "ok":
                if details_str:
                    summaries.append(f"{name}:ok({details_str})")
                else:
                    summaries.append(f"{name}:ok")
                continue
            summaries.append(f"{name}:{entry.status}")
        return "; ".join(summaries)

    def diagnostics_message(self) -> str:
        components: list[str] = [f"Run={self.run_id}"]
        if self.build_successful:
            components.append("Status=success")
        else:
            components.append("Status=error")
        if self.raw_item_count is not None:
            components.append(f"Items_raw={self.raw_item_count}")
        if self.final_item_count is not None:
            components.append(f"Items_final={self.final_item_count}")
        if self.durations:
            summary = ", ".join(
                f"{key}={value:.2f}s" for key, value in sorted(self.durations.items())
            )
            components.append(f"Dauer: {summary}")
        provider_summary = self._provider_summary()
        if provider_summary:
            components.append(f"Provider: {provider_summary}")
        if self.feed_path:
            components.append(f"Feed={self.feed_path}")
        if self.exception_message and not self.build_successful:
            components.append(f"Fehler={self.exception_message}")
        if self.warnings:
            components.append(f"Warnungen: {'; '.join(self.warnings)}")
        return " | ".join(components)

    def log_results(self) -> None:
        try:
            diagnostics = self.diagnostics_message()
            log.info(diagnostics)
            if self.has_errors():
                # Sentinel (Clear-Text-Logging Drift, sibling of the
                # ``src/utils/files.py`` SHA-256 path-fingerprint family
                # and the ``src/utils/env.py`` ``_warn_if_world_readable``
                # closure): ``error_log_path`` is ``Path(LOG_DIR) /
                # "errors.log"`` and ``LOG_DIR`` is env-controlled via
                # :func:`resolve_env_path`. :func:`validate_path` only
                # checks the path resolves under one of ``ALLOWED_ROOTS``;
                # it does NOT reject Trojan-Source / control-character /
                # BiDi primitives embedded in the path bytes. Routing the
                # value through :func:`sanitize_log_arg` at the call site
                # mirrors the canonical defence shape and guarantees no
                # primitive can reach pytest's ``caplog`` capture (which
                # fires BEFORE :class:`SafeFormatter`) nor any non-
                # ``SafeFormatter`` log handler that consumes the record.
                log.info(
                    "Hinweis: Fehler während des Feed-Laufs – Details siehe %s",
                    sanitize_log_arg(str(error_log_path)),
                )
                with self._lock:
                    if not self._issue_submitted:
                        self._issue_submitted = True
                        submit_now = True
                    else:
                        submit_now = False
                if submit_now:
                    _submit_github_issue(self)
        finally:
            self.detach_error_collector()


__all__ = [
    "DuplicateSummary",
    "FeedHealthMetrics",
    "build_feed_health_payload",
    "ProviderReport",
    "RunReport",
    "clean_message",
    "render_feed_health_markdown",
    "write_feed_health_report",
    "write_feed_health_json",
    "_RunErrorCollector",
    "log",
    "diagnostics_log_path",
    "error_log_path",
    "_CONTROL_CHARS_RE",
    "_MAX_REPORT_MESSAGE_LENGTH",
    "_MAX_REPORT_MESSAGE_COUNT",
    "_REPORT_TRUNCATION_MARKER",
    "_bounded_message",
    "_sanitize_log_detail",
    "_format_timestamp",
    "_format_timestamp_iso",
    "_MAX_GITHUB_BODY_LENGTH",
    "_BODY_TRUNCATION_MARKER",
    "_bounded_github_body",
    "_GITHUB_ISSUE_URL_RE",
    "_extract_github_issue_number",
    "_split_csv",
    "_GithubIssueConfig",
    "_GITHUB_REPO_SLUG_RE",
    "_is_valid_github_repo_slug",
    "_is_trusted_github_api",
    "_GithubIssueReporter",
    "_submit_github_issue",
]
