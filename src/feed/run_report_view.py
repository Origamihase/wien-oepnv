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
    def enabled(self) -> bool: ...

    @property
    def fetch_type(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def detail(self) -> str | None: ...

    @property
    def items(self) -> int | None: ...

    @property
    def duration(self) -> float | None: ...


class RunReportView(Protocol):
    """The fields and methods of ``RunReport`` that the reports read."""

    @property
    def started_at(self) -> datetime: ...

    @property
    def finished_at(self) -> datetime | None: ...

    @property
    def run_id(self) -> str: ...

    # ``Mapping`` (read-only, covariant) so that ``RunReport.providers``,
    # a ``dict[str, ProviderReport]``, matches ``ProviderReportView``.
    @property
    def providers(self) -> Mapping[str, ProviderReportView]: ...

    @property
    def raw_item_count(self) -> int | None: ...

    @property
    def final_item_count(self) -> int | None: ...

    @property
    def durations(self) -> dict[str, float]: ...

    @property
    def feed_path(self) -> str | None: ...

    @property
    def build_successful(self) -> bool: ...

    @property
    def exception_message(self) -> str | None: ...

    @property
    def warnings(self) -> list[str]: ...

    def iter_error_messages(self) -> Iterator[str]: ...

    def diagnostics_message(self) -> str: ...
