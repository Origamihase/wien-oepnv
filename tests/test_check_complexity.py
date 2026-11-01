"""The C901 gate (``scripts/check_complexity.py``) passes only on an exact match.

Before 2026-11-01 the gate measured ``src/`` only, keyed its baseline by bare
function name, failed only on new or increased entries and never read ruff's
exit code. Four consequences are pinned here:

* a decrease above 15 went unnoticed (``fetch_events`` kept its entry 51
  for a month while the code measured 35); decreased and gone entries now
  fail until the baseline is regenerated,
* with bare names, a function in another file would have passed under the
  entry of a namesake (``main`` alone is defined in 38 files); keys now
  carry the file path,
* a ruff that started but failed (exit 2, nothing on stdout) counted every
  entry as fixed and the gate passed; every failed measurement now ends
  with exit 2,
* ``scripts/`` was not measured at all (nine functions above 15).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import check_complexity as gate

REPO = Path(__file__).resolve().parents[1]


def _diagnostic(path: str, name: str, value: int, threshold: int = 15, code: str | None = "C901") -> dict[str, Any]:
    return {
        "code": code,
        "filename": str(REPO / path),
        "message": f"`{name}` is too complex ({value} > {threshold})",
    }


def _completed(stdout: str, returncode: int, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# ---------------------------------------------------------------------------
# Baseline file
# ---------------------------------------------------------------------------


def test_parse_baseline_reads_path_keys_and_skips_comments(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        "# header\n\nsrc/a.py::foo 16\nscripts/b.py::main 20\n",
        encoding="utf-8",
    )
    assert gate._parse_baseline(baseline) == [("src/a.py::foo", 16), ("scripts/b.py::main", 20)]


def test_parse_baseline_keeps_each_line_as_one_allowance(tmp_path: Path) -> None:
    """A duplicated line is two allowances, not a relaxed one (see the gate test below)."""
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("src/a.py::foo 16\nsrc/a.py::foo 20\n", encoding="utf-8")
    assert gate._parse_baseline(baseline) == [("src/a.py::foo", 16), ("src/a.py::foo", 20)]


def test_parse_baseline_ignores_malformed_lines_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        "src/a.py::foo 16\n<<<<<<< HEAD\nsrc/b.py::bar x\nsrc/c.py::baz \u00b2\nsrc/d.py::qux -5\n",
        encoding="utf-8",
    )
    assert gate._parse_baseline(baseline) == [("src/a.py::foo", 16)]
    stderr = capsys.readouterr().err
    assert stderr.count("malformed baseline line ignored") == 4


def test_parse_baseline_flags_the_old_name_only_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("fetch_events 51\n", encoding="utf-8")
    assert gate._parse_baseline(baseline) == [("fetch_events", 51)]
    assert "has no file path" in capsys.readouterr().err


def test_missing_baseline_is_the_strictest_state(tmp_path: Path) -> None:
    assert gate._parse_baseline(tmp_path / "absent.txt") == []


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_exact_match_passes() -> None:
    entries = [("src/a.py::foo", 16), ("scripts/b.py::main", 20)]
    assert gate._compare(entries, list(reversed(entries))).passed


def test_new_increased_decreased_and_gone_each_fail() -> None:
    baseline = [("src/a.py::grew", 16), ("src/a.py::shrank", 30), ("src/a.py::fixed", 18)]
    current = [("src/a.py::grew", 17), ("src/a.py::shrank", 25), ("src/a.py::fresh", 16)]
    result = gate._compare(baseline, current)
    assert not result.passed
    assert result.new == [("src/a.py::fresh", 16)]
    assert result.increased == [("src/a.py::grew", 16, 17)]
    assert result.decreased == [("src/a.py::shrank", 30, 25)]
    assert result.gone == [("src/a.py::fixed", 18)]


@pytest.mark.parametrize("current", [[("src/a.py::foo", 17)], [("src/a.py::foo", 16)], [("src/a.py::foo", 20)]])
def test_a_duplicated_baseline_line_never_relaxes_the_gate(current: list[tuple[str, int]]) -> None:
    """``foo 16`` plus ``foo 20`` (merge conflict) with one real ``foo``: always red."""
    baseline = [("src/a.py::foo", 16), ("src/a.py::foo", 20)]
    assert not gate._compare(baseline, current).passed


def test_same_name_in_another_file_is_a_new_entry() -> None:
    """With bare names, ``b.py``'s ``main`` passed under ``a.py``'s entry."""
    baseline = [("scripts/a.py::main", 20)]
    current = [("scripts/a.py::main", 20), ("scripts/b.py::main", 16)]
    result = gate._compare(baseline, current)
    assert result.new == [("scripts/b.py::main", 16)]
    assert not result.passed


def test_two_functions_of_one_name_in_one_file_pair_by_value() -> None:
    baseline = [("src/a.py::run", 16), ("src/a.py::run", 22)]
    assert gate._compare(baseline, [("src/a.py::run", 22), ("src/a.py::run", 16)]).passed
    grown = gate._compare(baseline, [("src/a.py::run", 22), ("src/a.py::run", 17)])
    assert grown.increased == [("src/a.py::run", 16, 17)]


# ---------------------------------------------------------------------------
# Measurement: fails closed
# ---------------------------------------------------------------------------


def test_ruff_output_becomes_repository_relative_entries() -> None:
    stdout = json.dumps([_diagnostic("src/providers/wl_fetch.py", "fetch_events", 35)])
    assert gate._parse_ruff_output(stdout, 1) == [("src/providers/wl_fetch.py::fetch_events", 35)]
    assert gate._parse_ruff_output("[]", 0) == []


@pytest.mark.parametrize(
    ("stdout", "returncode", "reason"),
    [
        ("", 1, "not JSON"),
        ("Found 3 errors.", 1, "not JSON"),
        ('{"code": "C901"}', 1, "not a JSON list"),
        ("[]", 1, "exited with 1 but reported 0"),
        (json.dumps([_diagnostic("src/a.py", "foo", 16)]), 0, "exited with 0 but reported 1"),
        (json.dumps([_diagnostic("src/a.py", "foo", 16, code=None)]), 1, "instead of C901"),
        (json.dumps([_diagnostic("src/a.py", "foo", 16, code="E501")]), 1, "instead of C901"),
        (json.dumps([_diagnostic("src/a.py", "foo", 16, threshold=10)]), 1, "threshold 10"),
        (
            json.dumps([{"code": "C901", "filename": str(REPO / "src/a.py"), "message": "foo is complex"}]),
            1,
            "unreadable C901 message",
        ),
        (
            json.dumps([{"code": "C901", "filename": "/elsewhere/a.py", "message": "`foo` is too complex (16 > 15)"}]),
            1,
            "outside the repository",
        ),
    ],
)
def test_unreadable_ruff_output_is_no_measurement(stdout: str, returncode: int, reason: str) -> None:
    with pytest.raises(gate.MeasurementError, match=reason):
        gate._parse_ruff_output(stdout, returncode)


@pytest.mark.parametrize(
    "outcome",
    [
        OSError("No such file or directory: 'python'"),
        subprocess.TimeoutExpired(cmd="ruff", timeout=120),
        _completed("", 2, stderr="ruff failed: invalid configuration"),
        _completed("", 1, stderr="/usr/bin/python: No module named ruff"),
    ],
)
def test_a_ruff_that_cannot_measure_fails_the_gate_with_exit_2(
    outcome: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each ends with exit 2 and leaves the baseline untouched.

    Before 2026-11-01 the failed run (exit 2) ended as "14 fixed" and exit
    0; a missing or hanging ruff ended in a traceback. The "No module named
    ruff" case is new with ``python -m ruff``.
    """

    def fake_run(*_args: object, **_kwargs: object) -> object:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    baseline = tmp_path / "baseline.txt"
    baseline.write_text("src/a.py::foo 16\n", encoding="utf-8")
    monkeypatch.setattr(gate, "BASELINE", baseline)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gate.main([]) == 2
    assert gate.main(["--write-baseline"]) == 2
    assert baseline.read_text(encoding="utf-8") == "src/a.py::foo 16\n"


def test_the_gate_measures_src_and_scripts_with_the_interpreters_ruff_as_json() -> None:
    command = gate._ruff_command()
    assert command[:4] == [sys.executable, "-m", "ruff", "check"]
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--select") + 1] == "C901"
    assert "--config=lint.mccabe.max-complexity=15" in command
    assert gate.SCAN_PATHS == ("src", "scripts")
    assert command[-len(gate.SCAN_PATHS):] == list(gate.SCAN_PATHS)


# ---------------------------------------------------------------------------
# main(): exit codes and the baseline round trip
# ---------------------------------------------------------------------------


def _fake_measurement(monkeypatch: pytest.MonkeyPatch, entries: list[tuple[str, int]]) -> None:
    stdout = json.dumps([_diagnostic(key.split("::")[0], key.split("::")[1], value) for key, value in entries])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout, 1 if entries else 0),
    )


def test_write_baseline_round_trips_to_a_passing_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    entries = [("src/a.py::run", 16), ("src/a.py::run", 22), ("scripts/b.py::main", 18)]
    baseline = tmp_path / "baseline.txt"
    monkeypatch.setattr(gate, "BASELINE", baseline)
    _fake_measurement(monkeypatch, entries)
    assert gate.main(["--write-baseline"]) == 0
    text = baseline.read_text(encoding="utf-8")
    assert text.splitlines()[-3:] == ["scripts/b.py::main 18", "src/a.py::run 22", "src/a.py::run 16"]
    assert gate.main([]) == 0
    assert "C901 gate passed" in capsys.readouterr().out


def test_a_path_with_whitespace_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The key holds the file path unquoted; the reader splits at the last space.

    With ``str.split`` the line ``scripts/my tool.py::busy 16`` had three parts
    and was dropped as malformed, so the regenerated baseline never passed.
    """
    entries = [("scripts/my tool.py::busy", 16), ("src/a\tb.py::run", 17), ("src/a.py::foo", 18)]
    baseline = tmp_path / "baseline.txt"
    monkeypatch.setattr(gate, "BASELINE", baseline)
    _fake_measurement(monkeypatch, entries)
    assert gate.main(["--write-baseline"]) == 0
    assert sorted(gate._parse_baseline(baseline)) == sorted(entries)
    assert gate.main([]) == 0
    captured = capsys.readouterr()
    assert "C901 gate passed" in captured.out
    assert "malformed" not in captured.err


@pytest.mark.parametrize("separator", ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", " "])
def test_a_path_with_a_line_break_is_no_measurement(separator: str) -> None:
    """Such a key could not be written as one baseline line."""
    stdout = json.dumps([_diagnostic(f"scripts/a{separator}b.py", "foo", 16)])
    with pytest.raises(gate.MeasurementError, match="line break"):
        gate._parse_ruff_output(stdout, 1)


def test_a_decrease_fails_until_the_baseline_follows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("src/providers/wl_fetch.py::fetch_events 51\n", encoding="utf-8")
    monkeypatch.setattr(gate, "BASELINE", baseline)
    _fake_measurement(monkeypatch, [("src/providers/wl_fetch.py::fetch_events", 35)])
    assert gate.main([]) == 1
    out = capsys.readouterr().out
    assert "src/providers/wl_fetch.py::fetch_events: 51 → 35" in out
    assert "regen_c901_baseline.sh" in out
    assert gate.main(["--write-baseline"]) == 0
    assert gate.main([]) == 0


def test_regen_script_delegates_to_the_gate() -> None:
    script = (REPO / "scripts" / "regen_c901_baseline.sh").read_text(encoding="utf-8")
    assert "exec python scripts/check_complexity.py --write-baseline" in script
    assert "sed " not in script


def test_the_real_ruff_measures_the_repository() -> None:
    """The real JSON shape parses; every key names a measured tree."""
    entries = gate._measure()
    assert entries
    for key, value in entries:
        path, _, name = key.partition("::")
        assert path.split("/", 1)[0] in gate.SCAN_PATHS and path.endswith(".py"), key
        assert name and value > gate.THRESHOLD, key
