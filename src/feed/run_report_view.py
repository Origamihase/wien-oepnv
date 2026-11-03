"""Read-only view of ``RunReport`` for the health report and the GitHub issue.

``feed_health`` and ``github_issue`` only read a finished run report. They
annotate it with :class:`RunReportView` instead of importing
``src.feed.reporting.RunReport``, because ``reporting`` imports both modules
(no import cycle, not even a type-only one). ``RunReport`` and
``ProviderReport`` stay in ``src.feed.reporting`` and satisfy these
protocols structurally; mypy checks that at every call site.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Protocol


class ProviderReportView(Protocol):
    """The fields of ``ProviderReport`` that the reports read."""

    @property
    def enabled(self) -> bool:
        """Same as ``ProviderReport.enabled``."""

    @property
    def fetch_type(self) -> str:
        """Same as ``ProviderReport.fetch_type``."""

    @property
    def status(self) -> str:
        """Same as ``ProviderReport.status``."""

    @property
    def detail(self) -> str | None:
        """Same as ``ProviderReport.detail``."""

    @property
    def items(self) -> int | None:
        """Same as ``ProviderReport.items``."""

    @property
    def duration(self) -> float | None:
        """Same as ``ProviderReport.duration``."""


class RunReportView(Protocol):
    """The fields and methods of ``RunReport`` that the reports read."""

    @property
    def started_at(self) -> datetime:
        """Same as ``RunReport.started_at``."""

    @property
    def finished_at(self) -> datetime | None:
        """Same as ``RunReport.finished_at``."""

    @property
    def run_id(self) -> str:
        """Same as ``RunReport.run_id``."""

    # ``Mapping`` (read-only, covariant) so that ``RunReport.providers``,
    # a ``dict[str, ProviderReport]``, matches ``ProviderReportView``.
    @property
    def providers(self) -> Mapping[str, ProviderReportView]:
        """Same as ``RunReport.providers``."""

    @property
    def raw_item_count(self) -> int | None:
        """Same as ``RunReport.raw_item_count``."""

    @property
    def final_item_count(self) -> int | None:
        """Same as ``RunReport.final_item_count``."""

    @property
    def durations(self) -> dict[str, float]:
        """Same as ``RunReport.durations``."""

    @property
    def feed_path(self) -> str | None:
        """Same as ``RunReport.feed_path``."""

    @property
    def build_successful(self) -> bool:
        """Same as ``RunReport.build_successful``."""

    @property
    def exception_message(self) -> str | None:
        """Same as ``RunReport.exception_message``."""

    @property
    def warnings(self) -> list[str]:
        """Same as ``RunReport.warnings``."""

    def iter_error_messages(self) -> Iterator[str]:
        """Same as ``RunReport.iter_error_messages``."""

    def diagnostics_message(self) -> str:
        """Same as ``RunReport.diagnostics_message``."""
