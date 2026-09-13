"""A provider with nothing to report is not a provider that failed.

Audit 2026-09-12, Befund 6 (second half). ``_merge_result`` treated every
provider returning zero items the same way: a ``WARNING`` in the log plus an
entry in the run report's warning list. Two different things wear that shape:

* an empty **cache** for Wiener Linien / ÖBB / Baustellen is a problem — the
  cache should hold data and does not;
* an empty **Stammstrecke** result is the S-Bahn trunk line running normally.
  Most builds look like this.

Builds run every 30 minutes, so the healthy case produced a warning around
the clock and made "working" indistinguishable from "broken" on the
dashboard — the exact opposite of what a warning is for.

Which one a provider is, is declared at registration rather than guessed from
its name here, and the default is the strict one: a provider that says
nothing keeps the warning. Both directions are pinned below, because a fix
that only silenced the noise would have silenced the real signal with it.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src.build_feed import _report_empty_provider
from src.feed import providers
from src.feed.reporting import RunReport

_LOGGER_NAME = "build_feed"


def _loader(name: str, *, empty_is_normal: bool) -> Any:
    def fetch() -> list[Any]:  # pragma: no cover - never called
        return []

    fetch._provider_cache_name = name  # type: ignore[attr-defined]
    fetch._provider_empty_is_normal = empty_is_normal  # type: ignore[attr-defined]
    return fetch


def _run(
    caplog: pytest.LogCaptureFixture,
    name: str,
    *,
    empty_is_normal: bool,
    alerts: dict[str, list[str]] | None = None,
) -> tuple[list[logging.LogRecord], RunReport]:
    report = RunReport(statuses=[])
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        _report_empty_provider(
            _loader(name, empty_is_normal=empty_is_normal),
            name,
            report,
            alerts or {},
        )
    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    return records, report


# ---------------- the healthy case must go quiet ----------------


def test_an_expected_empty_provider_logs_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    records, _report = _run(caplog, "stammstrecke", empty_is_normal=True)
    assert [r.levelno for r in records] == [logging.INFO], [
        (r.levelname, r.getMessage()) for r in records
    ]


def test_an_expected_empty_provider_adds_no_report_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning list is what a dashboard reads — it must stay clean."""
    _records, report = _run(caplog, "stammstrecke", empty_is_normal=True)
    assert report.warnings == []


def test_an_expected_empty_provider_gets_its_own_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``:empty`` alone could not tell "nothing to report" from "no data"."""
    _records, report = _run(caplog, "stammstrecke", empty_is_normal=True)
    entry = report.providers["stammstrecke"]
    assert entry.status == "ok-empty"
    assert entry.detail == "Keine Vorfälle"
    assert entry.items == 0


# ---------------- the suspect case must keep its teeth ----------------


def test_an_unexpected_empty_provider_still_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The half that must NOT change.

    An empty Wiener-Linien cache is the condition the warning exists for.
    Silencing the noise without keeping this would have thrown away the
    signal along with it.
    """
    records, report = _run(caplog, "wl", empty_is_normal=False)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert report.providers["wl"].status == "empty"
    assert report.warnings == ["Provider wl: Keine aktuellen Daten"]


def test_cache_alerts_still_reach_the_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the cache said went wrong must survive into the report."""
    _records, report = _run(
        caplog,
        "oebb",
        empty_is_normal=False,
        alerts={"oebb": ["Cache älter als 6h"]},
    )
    assert report.providers["oebb"].detail == "Cache älter als 6h"
    assert report.warnings == ["Provider oebb: Cache älter als 6h"]


def test_repeated_cache_alerts_are_not_restated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _records, report = _run(
        caplog,
        "oebb",
        empty_is_normal=False,
        alerts={"oebb": ["Cache älter als 6h", "Cache älter als 6h"]},
    )
    assert report.providers["oebb"].detail == "Cache älter als 6h"


def test_a_provider_without_the_flag_is_treated_as_suspect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail-closed: the strict path is what a silent provider gets.

    A future provider that never declares its contract must not slip into the
    quiet branch by omission.
    """

    def bare() -> list[Any]:  # pragma: no cover - never called
        return []

    report = RunReport(statuses=[])
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        _report_empty_provider(bare, "mystery", report, {})

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert [r.levelno for r in records] == [logging.WARNING]
    assert report.providers["mystery"].status == "empty"
    assert report.warnings


# ---------------- the live wiring ----------------


def test_only_stammstrecke_declares_empty_as_normal() -> None:
    """Pins the live contract, not a hand-built stand-in.

    The lesson from the ÖBB title fix (audit 2026-09-12, Befund 1): a change
    verified only on the function can miss that it never reaches the
    registered path.
    """
    providers.register_default_providers()
    declared = {
        spec.cache_key: bool(
            getattr(spec.loader, "_provider_empty_is_normal", False)
        )
        for spec in providers.iter_providers()
    }
    assert declared == {
        "wl": False,
        "oebb": False,
        "baustellen": False,
        "stammstrecke": True,
    }


def test_the_summary_line_separates_the_two_kinds() -> None:
    """End of the chain: what an operator actually reads."""
    report = RunReport(statuses=[])
    report.provider_success(
        "stammstrecke", items=0, status="ok-empty", detail="Keine Vorfälle"
    )
    report.provider_success(
        "wl", items=0, status="empty", detail="Keine aktuellen Daten"
    )
    report.provider_success("oebb", items=11)

    summary = report._provider_summary()
    assert "stammstrecke:ok-empty(Keine Vorfälle)" in summary
    assert "wl:empty(0 Items, Keine aktuellen Daten)" in summary
    assert "oebb:ok(11 Items)" in summary
    # The healthy provider must not read as the broken one.
    assert "stammstrecke:empty" not in summary
