"""Reporting primitives shared by feed builder components."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Dict, Iterator, List, Optional, Tuple
import tempfile

import requests

from .config import LOG_TIMEZONE
from .logging import diagnostics_log_path, error_log_path, prune_log_file

try:  # pragma: no cover - support package and script execution
    from utils.env import get_bool_env
    from utils.files import atomic_write
except ModuleNotFoundError:  # pragma: no cover
    from ..utils.env import get_bool_env
    from ..utils.files import atomic_write

log = logging.getLogger("build_feed")


def clean_message(message: Optional[str]) -> str:
    """Normalize log and status messages for human consumption."""

    if not message:
        return ""
    import re

    return re.sub(r"\s+", " ", message).strip()


def _escape_cell(text: str) -> str:
    """Escape pipe characters to prevent Markdown table breakage."""
    return text.replace("|", r"\|")


def _sanitize_code_span(text: str) -> str:
    """Sanitize text intended for inline code spans by replacing backticks."""
    return text.replace("`", "'")


@dataclass
class ProviderReport:
    name: str
    enabled: bool
    fetch_type: str = "unknown"
    status: str = "pending"  # ok, empty, error, disabled, skipped
    detail: Optional[str] = None
    items: Optional[int] = None
    duration: Optional[float] = None
    _started_at: Optional[float] = None

    def mark_disabled(self) -> None:
        self.enabled = False
        self.status = "disabled"

    def start(self) -> None:
        self._started_at = perf_counter()
        if self.status == "disabled":
            return
        self.status = "running"

    def finish(
        self,
        status: str,
        *,
        items: Optional[int] = None,
        detail: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> None:
        if duration is None and self._started_at is not None:
            duration = perf_counter() - self._started_at
        self.duration = duration
        self.items = items
        self.detail = detail
        self.status = status


class _RunErrorCollector(logging.Handler):
    def __init__(self, report: "RunReport") -> None:
        super().__init__(level=logging.ERROR)
        self.report = report
        self._formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - defensive
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
    statuses: List[Tuple[str, bool]]
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    providers: Dict[str, ProviderReport] = field(default_factory=dict)
    raw_item_count: Optional[int] = None
    final_item_count: Optional[int] = None
    durations: Dict[str, float] = field(default_factory=dict)
    feed_path: Optional[str] = None
    build_successful: bool = False
    exception_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    _error_messages: List[str] = field(default_factory=list)
    _seen_errors: set[str] = field(default_factory=set)
    _seen_warnings: set[str] = field(default_factory=set)
    finished_at: Optional[datetime] = None
    _error_collector: Optional[_RunErrorCollector] = None
    _lock: RLock = field(default_factory=RLock)

    def __post_init__(self) -> None:
        for name, enabled in self.statuses:
            normalized = str(name)
            entry = ProviderReport(name=normalized, enabled=enabled)
            if not enabled:
                entry.mark_disabled()
            self.providers[normalized] = entry

    @property
    def run_id(self) -> str:
        return self.started_at.strftime("%Y%m%dT%H%M%SZ")

    def register_provider(self, name: str, enabled: bool, fetch_type: str) -> None:
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
        detail: Optional[str] = None,
    ) -> None:
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=True)
                self.providers[name] = entry
            entry.finish(status, items=items, detail=clean_message(detail))

    def provider_empty(self, name: str, message: str | None = None) -> None:
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=True)
                self.providers[name] = entry
            entry.finish("empty", detail=clean_message(message))

    def provider_error(self, name: str, message: str | None = None) -> None:
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
        with self._lock:
            entry = self.providers.get(name)
            if entry is None:
                entry = ProviderReport(name=name, enabled=False)
                self.providers[name] = entry
            entry.finish("disabled", detail=clean_message(message))

    def add_warning(self, message: str) -> None:
        cleaned = clean_message(message)
        if not cleaned:
            return
        with self._lock:
            if cleaned in self._seen_warnings:
                return
            self._seen_warnings.add(cleaned)
            self.warnings.append(cleaned)

    def add_error_message(self, message: str) -> None:
        cleaned = clean_message(message)
        if not cleaned:
            return
        with self._lock:
            if cleaned in self._seen_errors:
                return
            self._seen_errors.add(cleaned)
            self._error_messages.append(cleaned)

    def iter_error_messages(self) -> Iterator[str]:
        with self._lock:
            # Snapshot the list to release lock immediately
            errors = list(self._error_messages)
        yield from errors

    def has_errors(self) -> bool:
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
        logging.getLogger().removeHandler(self._error_collector)
        self._error_collector = None

    def finish(
        self,
        *,
        build_successful: bool,
        raw_items: Optional[int] = None,
        final_items: Optional[int] = None,
        durations: Optional[Dict[str, float]] = None,
        feed_path: Optional[Path] = None,
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
        self.finished_at = datetime.now(timezone.utc)

    def record_exception(self, exc: Exception) -> None:
        message = f"{exc.__class__.__name__}: {exc}"
        self.exception_message = clean_message(message)
        self.add_error_message(f"Ausnahme: {message}")

    def prune_logs(self) -> None:
        now = self.started_at
        prune_log_file(diagnostics_log_path, now=now)
        prune_log_file(error_log_path, now=now)

    def _provider_summary(self) -> str:
        summaries: List[str] = []
        for name in sorted(self.providers):
            entry = self.providers[name]
            details: List[str] = []
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
            if entry.status == "empty":
                if details_str:
                    summaries.append(f"{name}:empty({details_str})")
                else:
                    summaries.append(f"{name}:empty")
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
        components: List[str] = [f"Run={self.run_id}"]
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
                log.info(
                    "Hinweis: Fehler während des Feed-Laufs – Details siehe %s",
                    error_log_path,
                )
                _submit_github_issue(self)
        finally:
            self.detach_error_collector()


@dataclass(frozen=True)
class DuplicateSummary:
    """Description of a deduplicated item cluster."""

    dedupe_key: str
    count: int
    titles: Tuple[str, ...]


@dataclass(frozen=True)
class FeedHealthMetrics:
    """Aggregate metrics captured during a feed build."""

    raw_items: int
    filtered_items: int
    deduped_items: int
    new_items: int
    duplicate_count: int
    duplicates: Tuple[DuplicateSummary, ...]


def _format_timestamp(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    try:
        localized = dt.astimezone(LOG_TIMEZONE)
    except Exception:  # pragma: no cover - timezone edge cases
        localized = dt
    return localized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_timestamp_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        localized = dt.astimezone(LOG_TIMEZONE)
    except Exception:  # pragma: no cover - timezone edge cases
        localized = dt
    return localized.isoformat()


def render_feed_health_markdown(
    report: RunReport,
    metrics: FeedHealthMetrics,
) -> str:
    """Render a human-readable Markdown summary of the latest feed build."""

    lines: List[str] = []
    lines.append("# Feed Health Report")
    lines.append("")
    status = "✅ Erfolgreich" if report.build_successful else "❌ Fehlerhaft"
    lines.append(f"- **Status:** {status}")
    lines.append(f"- **Run-ID:** `{report.run_id}`")
    lines.append(f"- **Start:** {_format_timestamp(report.started_at)}")
    lines.append(f"- **Ende:** {_format_timestamp(report.finished_at)}")
    if report.feed_path:
        lines.append(f"- **RSS-Datei:** `{report.feed_path}`")
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
        status = entry.status or "unbekannt"
        items = entry.items if entry.items is not None else "—"
        duration = f"{entry.duration:.2f}" if entry.duration is not None else "—"
        detail = _escape_cell(entry.detail or "")
        lines.append(
            f"| {name} | {status} | {items} | {duration} | {detail} |"
        )
    lines.append("")

    if metrics.duplicate_count:
        lines.append("### Entfernte Duplikate im Detail")
        lines.append("")
        for dup in metrics.duplicates:
            titles = ", ".join(
                f"`{_sanitize_code_span(title)}`" for title in dup.titles if title.strip()
            )
            title_text = titles or "(keine Titelinformationen)"
            lines.append(
                f"- **{dup.count}×** Schlüssel `{dup.dedupe_key}` – Beispiele: {title_text}"
            )
        lines.append("")

    if report.warnings:
        lines.append("## Warnungen")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    errors = list(report.iter_error_messages())
    if report.exception_message and report.exception_message not in errors:
        errors.append(report.exception_message)
    if errors:
        lines.append("## Fehler")
        lines.append("")
        for error in errors:
            lines.append(f"- {error}")
        lines.append("")

    if not metrics.duplicate_count and not report.warnings and not errors:
        lines.append("Keine zusätzlichen Auffälligkeiten festgestellt.")

    return "\n".join(lines).strip() + "\n"


def write_feed_health_report(
    report: RunReport,
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
    report: RunReport,
    metrics: FeedHealthMetrics,
) -> Dict[str, Any]:
    """Create a JSON-serialisable structure summarising the feed build."""

    duplicate_entries = [
        {
            "dedupe_key": summary.dedupe_key,
            "count": summary.count,
            "titles": [title for title in summary.titles if title.strip()],
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

    return {
        "run": {
            "id": report.run_id,
            "status": "success" if report.build_successful else "error",
            "started_at": _format_timestamp_iso(report.started_at),
            "finished_at": _format_timestamp_iso(report.finished_at),
            "feed_path": report.feed_path,
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
    }


def write_feed_health_json(
    report: RunReport,
    metrics: FeedHealthMetrics,
    *,
    output_path: Path,
) -> None:
    """Persist the feed health payload as JSON using an atomic write."""

    payload = build_feed_health_payload(report, metrics)
    with atomic_write(
        output_path, mode="w", encoding="utf-8", permissions=0o644
    ) as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


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
]


def _split_csv(value: str | None) -> Tuple[str, ...]:
    if not value:
        return ()
    parts = [item.strip() for item in value.split(",")]
    return tuple(part for part in parts if part)


@dataclass(frozen=True)
class _GithubIssueConfig:
    enabled: bool
    repository: Optional[str]
    token: Optional[str]
    api_url: str
    labels: Tuple[str, ...]
    assignees: Tuple[str, ...]
    title_prefix: str

    @classmethod
    def from_env(cls) -> "_GithubIssueConfig":
        enabled = get_bool_env("FEED_GITHUB_CREATE_ISSUES", False)
        repository = (
            os.getenv("FEED_GITHUB_REPOSITORY")
            or os.getenv("GITHUB_REPOSITORY")
            or None
        )
        token = os.getenv("FEED_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or None
        api_url = (
            os.getenv("FEED_GITHUB_API_URL")
            or os.getenv("GITHUB_API_URL")
            or "https://api.github.com"
        )
        labels = _split_csv(os.getenv("FEED_GITHUB_ISSUE_LABELS"))
        assignees = _split_csv(os.getenv("FEED_GITHUB_ISSUE_ASSIGNEES"))
        title_prefix = os.getenv("FEED_GITHUB_ISSUE_TITLE_PREFIX", "Fehlerbericht")
        return cls(
            enabled=enabled,
            repository=repository,
            token=token,
            api_url=api_url.rstrip("/"),
            labels=labels,
            assignees=assignees,
            title_prefix=title_prefix.strip() or "Fehlerbericht",
        )


class _GithubIssueReporter:
    def __init__(self, config: _GithubIssueConfig) -> None:
        self._config = config

    def submit(self, report: RunReport) -> None:
        if not self._config.enabled:
            return
        if not self._config.repository or not self._config.token:
            log.warning(
                "Automatisches GitHub-Issue kann nicht erstellt werden – Token oder Repository fehlen."
            )
            return

        url = (
            f"{self._config.api_url}/repos/{self._config.repository}/issues"
        )
        payload = {
            "title": self._build_title(report),
            "body": self._build_body(report),
        }
        if self._config.labels:
            payload["labels"] = list(self._config.labels)
        if self._config.assignees:
            payload["assignees"] = list(self._config.assignees)

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._config.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "wien-oepnv-feed-reporter",
        }

        try:
            response = requests.post(url, json=payload, timeout=10, headers=headers)
        except requests.RequestException as exc:
            log.warning(
                "Automatisches GitHub-Issue fehlgeschlagen (Netzwerkfehler %s: %s)",
                type(exc).__name__,
                exc,
            )
            return

        if response.status_code >= 400:
            detail: str
            try:
                detail = response.json().get("message", response.text)
            except ValueError:
                detail = response.text
            log.warning(
                "GitHub-Antwort %s beim Erstellen des Issues: %s",
                response.status_code,
                detail,
            )
            return

        issue_url: Optional[str] = None
        try:
            data = response.json()
            issue_url = data.get("html_url")
        except ValueError:
            issue_url = None

        if issue_url:
            log.info("Automatisches GitHub-Issue erstellt: %s", issue_url)
        else:
            log.info("Automatisches GitHub-Issue erstellt.")

    def _build_title(self, report: RunReport) -> str:
        return f"{self._config.title_prefix}: Feed-Lauf {report.run_id}"

    def _build_body(self, report: RunReport) -> str:
        started = _format_timestamp(report.started_at)
        finished = _format_timestamp(report.finished_at)
        errors = list(report.iter_error_messages())
        if report.exception_message and report.exception_message not in errors:
            errors.append(report.exception_message)

        lines: List[str] = []
        lines.append(
            "Dieser Issue wurde automatisch erstellt, weil der Feed-Lauf Fehler gemeldet hat."
        )
        lines.append("")
        lines.append("## Zusammenfassung")
        lines.append("")
        lines.append(f"- **Run-ID:** `{report.run_id}`")
        lines.append("- **Status:** Fehler")
        lines.append(f"- **Start:** {started}")
        lines.append(f"- **Ende:** {finished}")
        if report.feed_path:
            lines.append(f"- **Feed-Datei:** `{report.feed_path}`")
        if report.raw_item_count is not None:
            lines.append(f"- **Items (roh):** {report.raw_item_count}")
        if report.final_item_count is not None:
            lines.append(f"- **Items (final):** {report.final_item_count}")
        if report.exception_message:
            lines.append(f"- **Ausnahme:** {report.exception_message}")
        if report.warnings:
            lines.append(f"- **Warnungen:** {len(report.warnings)}")
        lines.append("")

        if report.warnings:
            lines.append("## Warnungen")
            lines.append("")
            for warning in report.warnings:
                lines.append(f"- {warning}")
            lines.append("")

        if errors:
            lines.append("## Fehler")
            lines.append("")
            for error in errors:
                lines.append(f"- {error}")
            lines.append("")

        lines.append("## Providerstatus")
        lines.append("")
        lines.append("| Provider | Status | Details | Items | Dauer (s) |")
        lines.append("| --- | --- | --- | ---: | ---: |")
        for name in sorted(report.providers):
            entry = report.providers[name]
            detail = _escape_cell(entry.detail or "")
            items = entry.items if entry.items is not None else "—"
            duration = f"{entry.duration:.2f}" if entry.duration is not None else "—"
            lines.append(
                f"| {name} | {entry.status or 'unbekannt'} | {detail} | {items} | {duration} |"
            )
        lines.append("")

        diagnostics = report.diagnostics_message()
        if diagnostics:
            lines.append("## Diagnosedaten")
            lines.append("")
            lines.append("```text")
            lines.append(diagnostics)
            lines.append("```")
            lines.append("")

        lines.append(
            f"Weitere Details finden sich in der Logdatei `{error_log_path}`."
        )

        return "\n".join(lines).strip() + "\n"


def _submit_github_issue(report: RunReport) -> None:
    config = _GithubIssueConfig.from_env()
    reporter = _GithubIssueReporter(config)
    reporter.submit(report)
