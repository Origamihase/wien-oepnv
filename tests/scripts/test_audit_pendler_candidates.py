"""End-to-end tests for ``scripts/audit_pendler_candidates.py``.

The script is a thin CLI wrapper around :mod:`src.utils.pendler_audit`;
these tests verify argument parsing, file I/O and exit-code semantics.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_pendler_candidates.py"


def _run_script(argv: list[str]) -> int:
    """Execute the CLI script in-process and return its exit code."""
    original_argv = sys.argv[:]
    sys.argv = [str(SCRIPT_PATH), *argv]
    try:
        try:
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
        except SystemExit as exc:
            if exc.code is None:
                return 0
            return int(exc.code)
        return 0
    finally:
        sys.argv = original_argv


def _write_candidates(path: Path, names: list[tuple[str, str]]) -> None:
    payload = {
        "candidates": [
            {"name": n, "priority": 1, "added": added}
            for n, added in names
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_stations(path: Path, station_names: list[str]) -> None:
    payload = {
        "stations": [
            {
                "name": name,
                "pendler": True,
                "in_vienna": False,
                "bst_id": str(100 + idx),
                "aliases": [],
            }
            for idx, name in enumerate(station_names)
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_script_writes_markdown_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    candidates = tmp_path / "candidates.json"
    stations = tmp_path / "stations.json"
    output = tmp_path / "report.md"
    _write_candidates(
        candidates, [("Pfaffstätten", "2026-05-05"), ("Phantom", "2026-05-05")]
    )
    _write_stations(stations, ["Pfaffstätten"])

    exit_code = _run_script(
        [
            "--candidates",
            str(candidates),
            "--stations",
            str(stations),
            "--output",
            str(output),
            "--reference-date",
            "2026-05-07",
        ]
    )

    assert exit_code == 0
    contents = output.read_text(encoding="utf-8")
    assert "Pendler Candidates Audit" in contents
    assert "Pfaffstätten" in contents
    assert "Phantom" in contents
    captured = capsys.readouterr()
    assert "Report written to" in captured.out


def test_script_fail_on_orphan_returns_nonzero(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.json"
    stations = tmp_path / "stations.json"
    _write_candidates(candidates, [("Phantom", "2026-05-05")])
    _write_stations(stations, [])

    exit_code = _run_script(
        [
            "--candidates",
            str(candidates),
            "--stations",
            str(stations),
            "--reference-date",
            "2026-05-07",
            "--fail-on-orphan",
        ]
    )

    assert exit_code == 1


def test_script_fail_on_orphan_returns_zero_when_clean(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.json"
    stations = tmp_path / "stations.json"
    _write_candidates(candidates, [("Pfaffstätten", "2026-05-05")])
    _write_stations(stations, ["Pfaffstätten"])

    exit_code = _run_script(
        [
            "--candidates",
            str(candidates),
            "--stations",
            str(stations),
            "--reference-date",
            "2026-05-07",
            "--fail-on-orphan",
        ]
    )
    assert exit_code == 0


def test_script_invalid_reference_date_is_rejected(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.json"
    stations = tmp_path / "stations.json"
    _write_candidates(candidates, [("X", "2026-05-05")])
    _write_stations(stations, [])

    exit_code = _run_script(
        [
            "--candidates",
            str(candidates),
            "--stations",
            str(stations),
            "--reference-date",
            "not-a-date",
        ]
    )
    # argparse exits with code 2 on bad arguments.
    assert exit_code == 2


def test_script_prints_to_stdout_when_no_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidates = tmp_path / "candidates.json"
    stations = tmp_path / "stations.json"
    _write_candidates(candidates, [("Pfaffstätten", "2026-05-05")])
    _write_stations(stations, ["Pfaffstätten"])

    exit_code = _run_script(
        [
            "--candidates",
            str(candidates),
            "--stations",
            str(stations),
            "--reference-date",
            "2026-05-07",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Pendler Candidates Audit" in captured.out
