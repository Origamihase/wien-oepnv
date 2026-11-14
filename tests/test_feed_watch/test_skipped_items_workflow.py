"""The Update run hands the build's health report to the Feed-Wächter.

Rule ``meldung-uebersprungen`` reads ``docs/feed-health.json``, which the
step "Build feed" writes and which is never committed. Nothing passes the
file from one step to the other: both use the default path. So the rule
sees a report only while the watcher step runs after the build step of the
same job, neither step sets ``FEED_HEALTH_JSON_PATH`` or ``--health-json``,
and nothing in between removes the file. A change to one of these would
leave the rule without a report and without any message ("Fehlt der
Bericht, gibt es keinen Fund"); this test names it instead. A step that
starts a script from ``scripts/cycle/`` (since 2026-11-14) is read together
with that script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests._conftest.cycle_scripts import started_script, step_program

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "update-cycle.yml"
BUILD_STEP = "Build feed"
WATCH_STEP = "Feed-Wächter (report only)"


def _job_with_both_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    found = [
        job["steps"]
        for job in workflow["jobs"].values()
        if {BUILD_STEP, WATCH_STEP} <= {step.get("name") for step in job.get("steps", [])}
    ]
    assert len(found) == 1, "build step and watcher step must be in one job"
    steps: list[dict[str, Any]] = found[0]
    return steps


def test_the_watcher_step_runs_after_the_build_step_of_the_same_job() -> None:
    names = [step.get("name") for step in _job_with_both_steps()]

    assert names.count(BUILD_STEP) == 1
    assert names.count(WATCH_STEP) == 1
    assert names.index(BUILD_STEP) < names.index(WATCH_STEP)


def test_both_steps_run_what_writes_and_what_reads_the_report() -> None:
    steps = {step.get("name"): step for step in _job_with_both_steps()}

    assert "python -m src.cli feed build" in steps[BUILD_STEP]["run"]
    assert steps[WATCH_STEP]["run"].strip() == "python scripts/feed_watch.py"
    assert steps[BUILD_STEP]["if"] == steps[WATCH_STEP]["if"]


def test_no_step_moves_or_removes_the_report_between_the_two() -> None:
    steps = _job_with_both_steps()
    names = [step.get("name") for step in steps]
    between = steps[names.index(BUILD_STEP) + 1 : names.index(WATCH_STEP)]

    for step in between:
        assert "feed-health" not in step_program(step), step.get("name")
    # Neither the workflow nor a step (nor a script one starts) sets another path for the report.
    scripts = [script for script in map(started_script, steps) if script is not None]
    text = "\n".join(
        line
        for source in (WORKFLOW, *scripts)
        for line in source.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "FEED_HEALTH_JSON_PATH" not in text
    assert "--health-json" not in text
