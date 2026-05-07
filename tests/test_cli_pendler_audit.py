"""CLI integration tests for the ``stations pendler-audit`` subcommand."""

from __future__ import annotations

import pytest

from src import cli


def test_cli_stations_pendler_audit_invokes_script(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, *, extra_args: list[str] | None = None) -> int:
        captured.append((script_name, list(extra_args or [])))
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(
        [
            "stations",
            "pendler-audit",
            "--candidates",
            "data/pendler_candidates.json",
            "--stations",
            "data/stations.json",
            "--output",
            "docs/report.md",
            "--max-stale-days",
            "180",
            "--reference-date",
            "2026-05-07",
            "--fail-on-orphan",
        ]
    )

    assert exit_code == 0
    assert captured == [
        (
            "audit_pendler_candidates.py",
            [
                "--candidates",
                "data/pendler_candidates.json",
                "--stations",
                "data/stations.json",
                "--output",
                "docs/report.md",
                "--max-stale-days",
                "180",
                "--reference-date",
                "2026-05-07",
                "--fail-on-orphan",
            ],
        )
    ]


def test_cli_stations_pendler_audit_minimal_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, *, extra_args: list[str] | None = None) -> int:
        captured.append((script_name, list(extra_args or [])))
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["stations", "pendler-audit"])
    assert exit_code == 0
    assert captured == [("audit_pendler_candidates.py", [])]


def test_cli_stations_pendler_audit_propagates_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_script(_script_name: str, *, extra_args: list[str] | None = None) -> int:
        return 1

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["stations", "pendler-audit", "--fail-on-orphan"])
    assert exit_code == 1
