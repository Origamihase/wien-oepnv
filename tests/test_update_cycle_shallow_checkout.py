# ruff: noqa: S603, S607
"""The update cycle checks out shallow; its backup-cron freshness gate must still decide right.

``update-cycle.yml`` runs every ~30 minutes. A full-history checkout
(``fetch-depth: 0``) downloaded ~55 MB per tick and grew with every
committed cache and ledger version; the tip alone is ~7 MB. The only step
that reads history is the backup-cron freshness gate (``git log -1 --
data/vor_request_count.json docs/feed.xml``). In a shallow clone the
boundary commit "adds" every path, so a naive ``git log`` would report the
boundary's commit time for a file that was not touched there and could
wrongly skip a needed backup tick. The gate therefore deepens the clone
and treats a boundary hit as "unknown" (= run).

These tests run the gate's real shell script, extracted from the workflow,
against throwaway repositories.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "update-cycle.yml"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _steps() -> list[dict[str, object]]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    steps: list[dict[str, object]] = []
    for job in jobs.values():
        steps.extend(job.get("steps", []))
    return steps


def _gate_script() -> str:
    for step in _steps():
        if step.get("name") == "Backup-cron freshness gate":
            return str(step["run"])
    raise AssertionError("freshness gate step not found")


def test_checkout_is_shallow() -> None:
    depths = [
        step["with"]["fetch-depth"]  # type: ignore[index]
        for step in _steps()
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert depths, "no checkout step found"
    assert all(depth == 1 for depth in depths), depths


def _git(cwd: Path, *args: str, when: int | None = None) -> str:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
    )
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{when} +0000"
    out = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _commit(repo: Path, path: str, when: int) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(f"{when}\n")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"touch {path}", when=when)


def _make_origin(tmp_path: Path, commits: list[tuple[str, int]]) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for path, when in commits:
        _commit(origin, path, when)
    return origin


def _run_gate(tmp_path: Path, origin: Path, event: str, *, drop_remote: bool = False) -> str:
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--branch", "main", f"file://{origin}", str(work))
    if drop_remote:
        _git(work, "remote", "remove", "origin")
    output = tmp_path / "gh_output"
    output.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        GITHUB_EVENT_NAME=event,
        GITHUB_REF_NAME="main",
        GITHUB_OUTPUT=str(output),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
    )
    subprocess.run(["bash", "-c", _gate_script()], cwd=work, env=env, check=True, capture_output=True)
    lines = [line for line in output.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1, lines
    return lines[0]


NOW = int(time.time())


def test_non_schedule_event_always_runs(tmp_path: Path) -> None:
    origin = _make_origin(tmp_path, [("docs/feed.xml", NOW - 60)])
    assert _run_gate(tmp_path, origin, "repository_dispatch") == "should_run=true"


def test_recent_tick_skips_backup(tmp_path: Path) -> None:
    origin = _make_origin(
        tmp_path,
        [("docs/feed.xml", NOW - 7200), ("data/vor_request_count.json", NOW - 300)],
    )
    assert _run_gate(tmp_path, origin, "schedule") == "should_run=false"


def test_recent_tick_found_below_the_shallow_tip(tmp_path: Path) -> None:
    # The tip is an unrelated commit (station refresh); the real tick sits
    # one commit below and only becomes visible after the gate deepens.
    origin = _make_origin(
        tmp_path,
        [("docs/feed.xml", NOW - 600), ("data/stations.json", NOW - 120)],
    )
    assert _run_gate(tmp_path, origin, "schedule") == "should_run=false"


def test_old_tick_runs_backup(tmp_path: Path) -> None:
    origin = _make_origin(
        tmp_path,
        [("docs/feed.xml", NOW - 7200), ("data/stations.json", NOW - 120)],
    )
    assert _run_gate(tmp_path, origin, "schedule") == "should_run=true"


def test_boundary_commit_is_not_mistaken_for_a_tick(tmp_path: Path) -> None:
    # Deepening fails (no remote), so only the tip is present. The tip did
    # not touch either file, but as shallow boundary it "adds" them; its
    # recent commit time must not be read as a fresh tick.
    origin = _make_origin(
        tmp_path,
        [("docs/feed.xml", NOW - 7200), ("data/stations.json", NOW - 120)],
    )
    assert _run_gate(tmp_path, origin, "schedule", drop_remote=True) == "should_run=true"
