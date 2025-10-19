"""Reporting primitives shared by feed builder components."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .logging import diagnostics_log_path, error_log_path, prune_log_file

log = logging.getLogger("build_feed")


def clean_message(message: Optional[str]) -> str:
    """Normalize log and status messages for human consumption."""

    if not message:
        return ""
    import re

    return re.sub(r"\s+", " ", message).strip()


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
        entry = self.providers.get(name)
        if entry is None:
            entry = ProviderReport(name=name, enabled=True)
            self.providers[name] = entry
        entry.finish(status, items=items, detail=clean_message(detail))

    def provider_empty(self, name: str, message: str | None = None) -> None:
        entry = self.providers.get(name)
        if entry is None:
            entry = ProviderReport(name=name, enabled=True)
            self.providers[name] = entry
        entry.finish("empty", detail=clean_message(message))

    def provider_error(self, name: str, message: str | None = None) -> None:
        entry = self.providers.get(name)
        if entry is None:
            entry = ProviderReport(name=name, enabled=True)
            self.providers[name] = entry
        cleaned = clean_message(message)
        entry.finish("error", detail=cleaned)
        if cleaned:
            self.add_error_message(f"{name}: {cleaned}")

    def provider_disabled(self, name: str, message: str | None = None) -> None:
        entry = self.providers.get(name)
        if entry is None:
            entry = ProviderReport(name=name, enabled=False)
            self.providers[name] = entry
        entry.finish("disabled", detail=clean_message(message))

    def add_warning(self, message: str) -> None:
        cleaned = clean_message(message)
        if not cleaned or cleaned in self._seen_warnings:
            return
        self._seen_warnings.add(cleaned)
        self.warnings.append(cleaned)

    def add_error_message(self, message: str) -> None:
        cleaned = clean_message(message)
        if not cleaned or cleaned in self._seen_errors:
            return
        self._seen_errors.add(cleaned)
        self._error_messages.append(cleaned)

    def iter_error_messages(self) -> Iterator[str]:
        yield from self._error_messages

    def has_errors(self) -> bool:
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
        finally:
            self.detach_error_collector()


__all__ = ["ProviderReport", "RunReport", "clean_message"]
