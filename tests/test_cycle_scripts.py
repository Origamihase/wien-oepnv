# ruff: noqa: S603, S607
"""The shell programs of the update cycle in ``scripts/cycle/`` and their steps.

``.github/workflows/update-cycle.yml`` stood at 700 lines; on 2026-11-14 it
was split by topic (rule "teilen statt kürzen", docs/modularisierung.md). The
longer shell programs moved with their reasoning to ``scripts/cycle/``; their
steps keep name, id, condition, time limit and environment and run
``exec bash scripts/cycle/<name>.sh``. With ``exec`` the step's process is
the script's shell, as it was the step's shell before.

``bash <file>`` starts without the options GitHub gives a step's shell
(``shell: bash``: ``-e -o pipefail``; no ``shell``: ``-e``), so each script
sets them first, unless its code switches them off itself (the fetch:
``set +e``). These tests keep steps and scripts together, run the two
programs no other test runs: the parallel fetch and the torch backstop (the
freshness gate: ``tests/test_update_cycle_shallow_checkout.py``; the
statistics step: ``tests/test_year_page_more.py``), each with the literal
``env`` of its step, and follow the fetch's exit codes to the Feed-Wächter.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.feed_watch.sources import parse_fetch_exit
from tests._conftest.cycle_scripts import CYCLE_SCRIPTS, REPO_ROOT, SCRIPT_CALL, started_script, workflow_program

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "update-cycle.yml"
#: Per script: the ``shell`` of its step and the first commands that stand in
#: for that shell's options.
OPTIONS: dict[str, tuple[str | None, list[str]]] = {
    "freshness_gate.sh": ("bash", ["set -e", "set -uo pipefail"]),
    "ensure_torch.sh": ("bash", ["set -e", "set -uo pipefail"]),
    "fetch_free_apis.sh": (None, ["set +e"]),
    "refresh_stats.sh": (None, ["set -e"]),
}
#: Scripts no step starts any more, kept unchanged for runs triggered before
#: their removal (rule in the head of update-cycle.yml); a later change deletes
#: file and entry once a run triggered after the removal has gone through.
RETIRED: frozenset[str] = frozenset()
#: How GitHub starts the ``run`` text of a step with that ``shell``.
STEP_SHELL: dict[str | None, list[str]] = {
    "bash": ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c"],
    None: ["bash", "-e", "-c"],
}
CPU_WHEEL = "pip install torch --index-url https://download.pytorch.org/whl/cpu"
PART = " — cache refreshed, that part taken from its last good answer in data/raw/."
STALE = " — last committed cache will be used by feed build."


def _steps() -> list[dict[str, Any]]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = doc["jobs"]["cycle"]["steps"]
    return steps


def _step_of(script: str) -> dict[str, Any]:
    (step,) = [step for step in _steps() if started_script(step) == CYCLE_SCRIPTS / script]
    return step


def _step_env(step: dict[str, Any]) -> dict[str, str]:
    """The literal ``env`` of *step*; a ``${{ … }}`` value needs GitHub and stays out."""
    return {name: str(value) for name, value in (step.get("env") or {}).items() if "${{" not in str(value)}


def _run_step(step: dict[str, Any], tmp_path: Path, stubs: dict[str, str], **env: str) -> subprocess.CompletedProcess[str]:
    """Run *step*'s ``run`` text from the repository root under its shell and ``env``, *stubs* first on PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in stubs.items():
        tool = bindir / name
        tool.write_text(f"#!/bin/bash\n{body}", encoding="utf-8")
        tool.chmod(0o755)
    (tmp_path / "gh_output").write_text("", encoding="utf-8")
    full_env = {
        "PATH": f"{bindir}{os.pathsep}/usr/bin:/bin",
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        **_step_env(step),
        **env,
    }
    return subprocess.run(
        [*STEP_SHELL[step.get("shell")], str(step["run"])],
        cwd=REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _forwards_to(script: Path) -> Path | None:
    """The script that *script* hands on to, when its only command is ``exec bash scripts/cycle/<new>.sh``.

    A renamed script keeps its old path in this form until a run triggered
    after the change has gone through (rule in the head of update-cycle.yml);
    a removed one stays unchanged and is listed in ``RETIRED``.
    """
    lines = script.read_text(encoding="utf-8").splitlines()
    commands = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    match = SCRIPT_CALL.match(commands[0]) if len(commands) == 1 else None
    return REPO_ROOT / match.group(1) if match else None


def test_every_script_has_its_step_and_every_call_its_script() -> None:
    callers = [step for step in _steps() if "scripts/cycle/" in str(step.get("run", ""))]
    started = []
    for step in callers:
        assert SCRIPT_CALL.match(str(step["run"]).strip()), (step["name"], step["run"])
        script = started_script(step)
        assert script is not None and script.is_file(), step["name"]
        started.append(script.name)
    assert sorted(started) == sorted(set(started)) == sorted(OPTIONS)
    assert not RETIRED & set(started)
    assert all((CYCLE_SCRIPTS / name).is_file() for name in RETIRED)
    # Any other file is a retired script or a forwarder from an old path to a
    # script a step starts.
    for path in sorted(CYCLE_SCRIPTS.glob("*.sh")):
        if path.name not in started and path.name not in RETIRED:
            target = _forwards_to(path)
            assert target is not None and target.name in started, path.name


def test_a_forwarder_is_one_exec_line(tmp_path: Path) -> None:
    old = tmp_path / "old.sh"
    old.write_text("#!/usr/bin/env bash\n# Renamed.\nexec bash scripts/cycle/fetch_free_apis.sh\n", encoding="utf-8")
    assert _forwards_to(old) == CYCLE_SCRIPTS / "fetch_free_apis.sh"
    old.write_text("set -e\nexec bash scripts/cycle/fetch_free_apis.sh\n", encoding="utf-8")
    assert _forwards_to(old) is None
    assert _forwards_to(CYCLE_SCRIPTS / "refresh_stats.sh") is None


def test_the_workflow_program_holds_every_started_script() -> None:
    """What tests that scan a whole workflow read (``workflow_program``)."""
    program = workflow_program(WORKFLOW)
    assert program.startswith(WORKFLOW.read_text(encoding="utf-8"))
    for script in sorted(OPTIONS):
        assert (CYCLE_SCRIPTS / script).read_text(encoding="utf-8") in program, script


@pytest.mark.parametrize("script", sorted(OPTIONS))
def test_no_script_holds_a_workflow_expression(script: str) -> None:
    """GitHub fills ``${{ … }}`` in the workflow file only; bash rejects it ("bad substitution")."""
    assert "${{" not in (CYCLE_SCRIPTS / script).read_text(encoding="utf-8")


@pytest.mark.parametrize("script", sorted(OPTIONS))
def test_the_script_begins_with_the_option_lines_for_its_steps_shell(script: str) -> None:
    """``set -e`` where the step's shell set ``-e`` (the two ``shell: bash`` scripts then their own
    ``set -uo pipefail``); the fetch keeps ``set +e``."""
    shell, first = OPTIONS[script]
    assert _step_of(script).get("shell") == shell
    lines = (CYCLE_SCRIPTS / script).read_text(encoding="utf-8").splitlines()
    commands = [line for line in lines if line.strip() and not line.startswith("#")]
    assert commands[: len(first)] == first


@pytest.mark.parametrize(
    ("codes", "warnings"),
    [
        (
            {"wl": 3, "oebb": 1, "baustellen": 0},
            [
                "::warning title=wl cache fetcher::part of the source gave no usable answer (exit 3)" + PART,
                "::warning title=oebb cache fetcher::non-zero exit (1)" + STALE,
            ],
        ),
        (
            {"wl": 0, "oebb": 124, "baustellen": 137},
            [
                "::warning title=oebb cache fetcher::non-zero exit (124)" + STALE,
                "::warning title=baustellen cache fetcher::non-zero exit (137)" + STALE,
            ],
        ),
    ],
)
def test_the_fetch_runs_the_three_sources_and_hands_on_their_exit_codes(
    tmp_path: Path, codes: dict[str, int], warnings: list[str]
) -> None:
    calls = tmp_path / "calls"
    cases = " ".join(f"{name}) exit {code};;" for name, code in codes.items())
    python = (
        f'echo "$* RAW_CAPTURE=$RAW_CAPTURE" >> "{calls}"\n'
        'name=$(basename "$1" .py); name=${name#update_}; name=${name%_cache}\n'
        'echo "log of $name"\n'
        f'case "$name" in {cases} esac\n'
    )
    # ``timeout --kill-after=10 90 python …``: note the limits, then run the rest.
    timeout = f'echo "timeout $1 $2" >> "{calls}.timeout"\nshift 2\nexec "$@"\n'
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()

    # ``RAW_CAPTURE`` comes from the step's ``env``.
    result = _run_step(_step_of("fetch_free_apis.sh"), tmp_path, {"python": python, "timeout": timeout}, RUNNER_TEMP=str(runner_temp))

    assert result.returncode == 0, result.stderr  # best effort: a source never fails the step
    output = (tmp_path / "gh_output").read_text(encoding="utf-8")
    expected = " ".join(f"{name}={code}" for name, code in codes.items())
    assert output == f"exit_codes={expected}\n"
    assert parse_fetch_exit(output.strip().removeprefix("exit_codes=")) == codes  # what the Feed-Wächter reads
    assert sorted(calls.read_text(encoding="utf-8").splitlines()) == [
        f"scripts/update_{name}_cache.py RAW_CAPTURE=1" for name in sorted(codes)
    ]
    assert (tmp_path / "calls.timeout").read_text(encoding="utf-8").splitlines() == ["timeout --kill-after=10 90"] * 3
    for name, code in codes.items():
        assert f"::group::{name} fetcher (exit {code})\nlog of {name}\n::endgroup::\n" in result.stdout
    assert [line for line in result.stdout.splitlines() if line.startswith("::warning")] == warnings


def test_the_watcher_gets_the_exit_codes_of_the_fetch_step() -> None:
    """The output ``exit_codes`` (above) reaches the Feed-Wächter under the step's ``id``."""
    steps = _steps()
    ids = [step.get("id") for step in steps]
    assert _step_of("fetch_free_apis.sh")["id"] == "fetch"
    assert ids.index("fetch") < ids.index("watch")
    assert steps[ids.index("watch")]["env"]["FEED_WATCH_FETCH_EXIT"] == "${{ steps.fetch.outputs.exit_codes }}"


def _torch(tmp_path: Path, *, present: bool, pip_codes: list[int]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "calls"
    stubs = {
        "python": f'echo "python $*" >> "{calls}"\n[ "$1" = "-c" ] && exit {0 if present else 1}\nexit 99\n',
        "pip": (
            f'echo "pip $*" >> "{calls}"\nn=$(grep -c "^pip " "{calls}")\n'
            f'codes=({" ".join(map(str, pip_codes))})\nexit "${{codes[$((n - 1))]:-42}}"\n'
        ),
        "sleep": f'echo "sleep $*" >> "{calls}"\n',
    }
    result = _run_step(_step_of("ensure_torch.sh"), tmp_path, stubs)
    return result, calls.read_text(encoding="utf-8").splitlines()


def test_torch_in_the_venv_costs_no_install(tmp_path: Path) -> None:
    result, calls = _torch(tmp_path, present=True, pip_codes=[])
    assert result.returncode == 0
    assert calls == ["python -c import torch"]
    assert "torch already present" in result.stdout


def test_missing_torch_is_installed_from_the_cpu_index_with_retries(tmp_path: Path) -> None:
    result, calls = _torch(tmp_path, present=False, pip_codes=[1, 0])
    assert result.returncode == 0
    assert calls == ["python -c import torch", CPU_WHEEL, "sleep 3", CPU_WHEEL]
    assert "torch installed on attempt 2/3." in result.stdout


def test_torch_that_cannot_be_installed_only_warns(tmp_path: Path) -> None:
    result, calls = _torch(tmp_path, present=False, pip_codes=[1, 1, 1])
    assert result.returncode == 0  # the EN feed degrades for one tick; the cycle goes on
    assert calls == ["python -c import torch", CPU_WHEEL, "sleep 3", CPU_WHEEL, "sleep 6", CPU_WHEEL, "sleep 9"]
    assert "::warning title=torch-install::torch still missing after 3 attempts" in result.stdout
