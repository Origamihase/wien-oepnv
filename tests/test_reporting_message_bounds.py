"""Verify that RunReport caps the size and count of error / warning messages.

A misbehaving upstream can produce a near-unbounded stream of unique error
messages (e.g. each one carries a fresh request UUID or timestamp).  Without
length and count caps this will:

  * exceed GitHub's ~65 KB issue-body limit (the auto-issue step silently
    422s and the alerting channel goes dark);
  * grow ``feed_health.json`` (a public artefact) into a large, slow-to-
    serve file;
  * leak unbounded memory for any long-running build process.

These tests pin the defensive caps that ``add_error_message`` and
``add_warning`` apply.
"""

from __future__ import annotations

import pytest

from src.feed.reporting import (
    RunReport,
    _MAX_REPORT_MESSAGE_COUNT,
    _MAX_REPORT_MESSAGE_LENGTH,
    _REPORT_TRUNCATION_MARKER,
    _bounded_message,
)


# ─────────────────────────── helper ───────────────────────────────────────


def _new_report() -> RunReport:
    return RunReport(statuses=[("wl", True)])


# ─────────────────────────── _bounded_message ─────────────────────────────


def test_bounded_message_passes_short_input_unchanged() -> None:
    short = "short error"
    assert _bounded_message(short) == short


def test_bounded_message_truncates_with_marker() -> None:
    huge = "A" * (_MAX_REPORT_MESSAGE_LENGTH + 5_000)
    bounded = _bounded_message(huge)
    assert len(bounded) == _MAX_REPORT_MESSAGE_LENGTH
    assert bounded.endswith(_REPORT_TRUNCATION_MARKER)


# ─────────────────────────── add_error_message ────────────────────────────


def test_error_messages_truncate_oversized_entries() -> None:
    report = _new_report()
    huge = "stack trace " * 1000  # >> _MAX_REPORT_MESSAGE_LENGTH
    report.add_error_message(huge)
    collected = list(report.iter_error_messages())
    assert len(collected) == 1
    assert len(collected[0]) <= _MAX_REPORT_MESSAGE_LENGTH
    assert collected[0].endswith(_REPORT_TRUNCATION_MARKER)


def test_error_messages_drop_after_max_count() -> None:
    """Past the cap, additional unique messages are silently dropped."""
    report = _new_report()
    for i in range(_MAX_REPORT_MESSAGE_COUNT + 50):
        report.add_error_message(f"unique error #{i}")
    collected = list(report.iter_error_messages())
    assert len(collected) == _MAX_REPORT_MESSAGE_COUNT


def test_error_messages_keep_dedup_inside_the_cap() -> None:
    """Existing dedup behaviour still applies — duplicate messages don't stack."""
    report = _new_report()
    for _ in range(10):
        report.add_error_message("duplicate")
    assert list(report.iter_error_messages()) == ["duplicate"]


# ─────────────────────────── add_warning ──────────────────────────────────


def test_warnings_truncate_oversized_entries() -> None:
    report = _new_report()
    huge = "x" * (_MAX_REPORT_MESSAGE_LENGTH + 500)
    report.add_warning(huge)
    assert len(report.warnings) == 1
    assert len(report.warnings[0]) <= _MAX_REPORT_MESSAGE_LENGTH
    assert report.warnings[0].endswith(_REPORT_TRUNCATION_MARKER)


def test_warnings_drop_after_max_count() -> None:
    report = _new_report()
    for i in range(_MAX_REPORT_MESSAGE_COUNT + 50):
        report.add_warning(f"unique warning #{i}")
    assert len(report.warnings) == _MAX_REPORT_MESSAGE_COUNT


# ─────────────────────────── total payload bound ──────────────────────────


def test_combined_caps_apply_independently_to_errors_and_warnings() -> None:
    """Cap is per-stream: 100 errors AND 100 warnings can both be retained."""
    report = _new_report()
    # Add the cap on each side — the streams must not share a budget.
    for i in range(_MAX_REPORT_MESSAGE_COUNT + 5):
        report.add_error_message(f"unique error #{i}")
        report.add_warning(f"unique warning #{i}")
    errors = list(report.iter_error_messages())
    assert len(errors) == _MAX_REPORT_MESSAGE_COUNT
    assert len(report.warnings) == _MAX_REPORT_MESSAGE_COUNT


# ─────────────────────────── empty / falsy passthrough ────────────────────


@pytest.mark.parametrize("falsy", ["", "   ", None])
def test_falsy_messages_are_dropped(falsy: str | None) -> None:
    report = _new_report()
    report.add_error_message(falsy or "")
    report.add_warning(falsy or "")
    assert list(report.iter_error_messages()) == []
    assert report.warnings == []
