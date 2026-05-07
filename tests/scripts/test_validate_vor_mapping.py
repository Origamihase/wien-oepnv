"""Tests for the VOR mapping ID validator script."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import validate_vor_mapping as validator


@pytest.fixture
def in_tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the validator with ``tmp_path/data`` as the mapping location."""
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_mapping(repo: Path, payload: object) -> None:
    target = repo / "data" / "vor-haltestellen.mapping.json"
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_validator_accepts_valid_mapping(
    in_tmp_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_mapping(
        in_tmp_repo,
        [
            {"station_name": "Wien Hbf", "vor_id": "490132000"},
            {"station_name": "Wien Mitte", "vor_id": "490137000"},
        ],
    )

    assert validator.main() == 0
    captured = capsys.readouterr()
    assert "Validation successful" in captured.out


def test_validator_reports_invalid_format(
    in_tmp_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_mapping(
        in_tmp_repo, [{"station_name": "Wien Hbf", "vor_id": "abc"}]
    )

    assert validator.main() == 1
    captured = capsys.readouterr()
    assert "Invalid VOR ID format" in captured.err


@pytest.mark.parametrize(
    "bad_entry",
    [
        None,
        42,
        "not-an-object",
        ["nested", "list"],
        True,
    ],
)
def test_validator_rejects_non_object_entries(
    in_tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
    bad_entry: object,
) -> None:
    """Zero Trust: non-dict entries must be rejected, not crash with AttributeError.

    Without the per-entry isinstance guard, a tampered or hand-edited mapping
    with scalar / null / nested-list elements would crash the validator with
    ``AttributeError: '<type>' object has no attribute 'get'`` instead of
    going through the documented "Found N errors" exit path.
    """
    _write_mapping(
        in_tmp_repo,
        [{"station_name": "Wien Hbf", "vor_id": "490132000"}, bad_entry],
    )

    assert validator.main() == 1
    captured = capsys.readouterr()
    assert "is not a JSON object" in captured.err
    assert "Found 1 errors" in captured.err
