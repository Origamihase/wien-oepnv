from __future__ import annotations

from pathlib import Path
import sys

import pytest

from src import cli


def test_cli_cache_update_invokes_expected_script(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None, list[str]]] = []

    def fake_run_script(script_name: str, *, python: str | None = None, extra_args: list[str] | None = None) -> int:
        calls.append((script_name, python, list(extra_args or [])))
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["cache", "update", "wl", "--python", "python3"])

    assert exit_code == 0
    assert calls == [("update_wl_cache.py", "python3", [])]


def test_cli_stations_validate_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    output_path = tmp_path / "report.md"

    class DummyReport:
        has_issues = True

        def to_markdown(self) -> str:
            return "dummy-report\n"

    def fake_validate(stations_path: Path, *, gtfs_stops_path: Path | None = None, decimal_places: int = 5) -> DummyReport:
        assert stations_path == Path("stations.json")
        assert decimal_places == 4
        assert gtfs_stops_path == Path("stops.txt")
        return DummyReport()

    monkeypatch.setattr(cli, "validate_stations", fake_validate)

    exit_code = cli.main([
        "stations",
        "validate",
        "--stations",
        "stations.json",
        "--gtfs",
        "stops.txt",
        "--decimal-places",
        "4",
        "--output",
        str(output_path),
        "--fail-on-issues",
    ])

    captured = capsys.readouterr()
    assert "dummy-report" in captured.out
    assert output_path.read_text(encoding="utf-8") == "dummy-report\n"
    assert exit_code == 1


def test_cli_checks_forwards_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None, list[str]]] = []

    def fake_run_script(script_name: str, *, python: str | None = None, extra_args: list[str] | None = None) -> int:
        calls.append((script_name, python, list(extra_args or [])))
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["checks", "--fix", "--ruff-args", "--select", "E"])

    assert exit_code == 0
    assert calls == [("run_static_checks.py", sys.executable, ["--fix", "--ruff-args", "--select", "E"])]
