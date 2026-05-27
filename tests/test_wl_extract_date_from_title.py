"""Unit tests for ``wl_text.extract_date_from_title``.

Covers the year-rollover resolution (the previously untested branch) and
the spelled-out German month form (incl. the Austrian ``Jänner`` /
``Feber``) that the legacy regex ignored.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.providers.wl_text import extract_date_from_title

VIENNA = ZoneInfo("Europe/Vienna")


def _ref(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=VIENNA)


def test_numeric_explicit_year() -> None:
    got = extract_date_from_title("Linie 4A: Verlegung ab 12.01.2026")
    assert got == datetime(2026, 1, 12, tzinfo=VIENNA)


def test_numeric_missing_year_december_reference_rolls_to_january() -> None:
    # Published 20 Dec 2025, "ab 02.01." => 2 Jan 2026 (nearest occurrence).
    got = extract_date_from_title("ab 02.01.", reference_date=_ref(2025, 12, 20))
    assert got == datetime(2026, 1, 2, tzinfo=VIENNA)


def test_numeric_missing_year_january_reference_keeps_previous_december() -> None:
    # Regression: the legacy "+1-only" heuristic stamped this as Dec 2026
    # (~12 months in the future). The nearest-occurrence rule correctly
    # resolves it to the recent past (Dec 2025).
    got = extract_date_from_title("ab 28.12.", reference_date=_ref(2026, 1, 5))
    assert got == datetime(2025, 12, 28, tzinfo=VIENNA)


def test_monthname_explicit_year() -> None:
    got = extract_date_from_title(
        "56A/58A: Bauarbeiten Maxingstraße ab 07. April 2026"
    )
    assert got == datetime(2026, 4, 7, tzinfo=VIENNA)


@pytest.mark.parametrize(
    ("title", "expected_month"),
    [
        ("Umleitung ab 03. Jänner 2026", 1),
        ("Umleitung ab 15. Feber 2026", 2),
        ("Umleitung ab 9. März 2026", 3),
        ("Umleitung ab 01. Juni 2026", 6),
        ("Umleitung ab 03. November 2025", 11),
    ],
)
def test_monthname_variants(title: str, expected_month: int) -> None:
    got = extract_date_from_title(title)
    assert got is not None
    assert got.month == expected_month


def test_monthname_missing_year_rolls_to_january() -> None:
    got = extract_date_from_title("ab 02. Jänner", reference_date=_ref(2025, 12, 20))
    assert got == datetime(2026, 1, 2, tzinfo=VIENNA)


def test_no_date_returns_none() -> None:
    assert extract_date_from_title("10A/42A: Umleitung Lidlgasse") is None


def test_empty_title_returns_none() -> None:
    assert extract_date_from_title("") is None


def test_invalid_calendar_date_returns_none() -> None:
    # 31 February never exists -> datetime() raises -> None.
    assert extract_date_from_title("ab 31.02.2026") is None


def test_naive_reference_is_treated_as_utc() -> None:
    # A naive reference must not raise and resolves like the aware case.
    got = extract_date_from_title("ab 02.01.", reference_date=datetime(2025, 12, 20))
    assert got == datetime(2026, 1, 2, tzinfo=VIENNA)
