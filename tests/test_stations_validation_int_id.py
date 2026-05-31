"""bug #8: _find_alias_issues guarded the identity fields with
``isinstance(bst_code, str)`` only, while the sibling validators
(_find_cross_station_id_conflicts, _find_identity_field_conflicts) accept
``str | int``. A directory entry whose bst_code is a JSON integer was
therefore silently skipped, so a genuinely missing required alias for that
station went unreported.
"""
from __future__ import annotations

from typing import Any

from src.utils.stations_validation import _find_alias_issues


def _station(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Teststation",
        "aliases": ["Teststation"],
        "source": "oebb",
    }
    base.update(over)
    return base


def test_integer_bst_code_missing_alias_is_reported() -> None:
    issues = list(_find_alias_issues([_station(bst_code=12345)]))
    assert len(issues) == 1
    assert "12345" in issues[0].reason


def test_integer_bst_code_present_as_alias_is_ok() -> None:
    issues = list(
        _find_alias_issues([_station(bst_code=12345, aliases=["Teststation", "12345"])])
    )
    assert issues == []


def test_string_bst_code_path_unchanged() -> None:
    # The original str path must keep reporting a missing required alias.
    issues = list(_find_alias_issues([_station(bst_code="ABC")]))
    assert len(issues) == 1
    assert "ABC" in issues[0].reason
