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

    exit_code = cli.main(["cache", "update", "--python", "python3", "wl", "oebb", "wl"])

    assert exit_code == 0
    assert calls == [
        ("update_wl_cache.py", "python3", []),
        ("update_oebb_cache.py", "python3", []),
    ]


def test_cli_cache_update_defaults_to_all(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_run_script(script_name: str, *, python: str | None = None, extra_args: list[str] | None = None) -> int:
        calls.append(script_name)
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["cache", "update", "--python", sys.executable])

    assert exit_code == 0
    assert calls == [
        "update_wl_cache.py",
        "update_oebb_cache.py",
        "update_vor_cache.py",
    ]


def test_cli_cache_update_rejects_mixed_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run_script", lambda *args, **kwargs: 0)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["cache", "update", "--all", "wl"])

    assert excinfo.value.code == 2


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


def test_cli_tokens_verify_defaults_to_all(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_run_script(script_name: str, *, python: str | None = None, extra_args: list[str] | None = None) -> int:
        calls.append(script_name)
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["tokens", "verify"])

    assert exit_code == 0
    assert calls == [
        "verify_vor_access_id.py",
        "verify_google_places_access.py",
        "check_vor_auth.py",
    ]


def test_cli_tokens_verify_stops_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_run_script(script_name: str, *, python: str | None = None, extra_args: list[str] | None = None) -> int:
        calls.append(script_name)
        return 1 if script_name == "verify_google_places_access.py" else 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["tokens", "verify", "--stop-on-error"])

    assert exit_code == 1
    assert calls == [
        "verify_vor_access_id.py",
        "verify_google_places_access.py",
    ]


def test_cli_config_wizard_forwards_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str | None, list[str]]] = []

    def fake_run_script(script_name: str, *, python: str | None = None, extra_args: list[str] | None = None) -> int:
        captured.append((script_name, python, list(extra_args or [])))
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["config", "wizard", "--python", "python3", "--", "--dry-run"])

    assert exit_code == 0
    assert captured == [("configure_feed.py", "python3", ["--dry-run"])]


def test_cli_security_scan_forwards_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str | None, list[str]]] = []

    def fake_run_script(script_name: str, *, python: str | None = None, extra_args: list[str] | None = None) -> int:
        captured.append((script_name, python, list(extra_args or [])))
        return 0

    monkeypatch.setattr(cli, "_run_script", fake_run_script)

    exit_code = cli.main(["security", "scan", "--python", sys.executable, "--", "--no-fail"])

    assert exit_code == 0
    assert captured == [("scan_secrets.py", sys.executable, ["--no-fail"])]


def test_run_script_uses_runpy_for_current_interpreter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "echo_args.py"
    script.write_text("import sys\nprint('ok', sys.argv[1:])\n", encoding="utf-8")
    monkeypatch.setattr(cli, "SCRIPTS_DIR", tmp_path)

    exit_code = cli._run_script("echo_args.py", python=sys.executable, extra_args=["--", "foo"])

    assert exit_code == 0
    assert "ok ['foo']" in capsys.readouterr().out


def test_run_script_invokes_subprocess_for_custom_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "noop.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(cli, "SCRIPTS_DIR", tmp_path)

    calls: list[tuple[list[str], bool]] = []

    class DummyResult:
        returncode = 5

    def fake_run(command: list[str], *, check: bool = False) -> DummyResult:
        calls.append((command, check))
        return DummyResult()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = cli._run_script("noop.py", python="/custom/python", extra_args=["--", "arg1"])

    assert exit_code == 5
    assert calls == [(["/custom/python", str(script), "arg1"], False)]


def test_cli_feed_lint_invokes_module(monkeypatch: pytest.MonkeyPatch) -> None:
    invoked: list[int] = []

    def fake_lint() -> int:
        invoked.append(1)
        return 0

    monkeypatch.setattr(cli.build_feed_module, "lint", fake_lint)

    exit_code = cli.main(["feed", "lint"])

    assert exit_code == 0
    assert invoked == [1]
