# ruff: noqa: S603, S607
"""Every workflow that writes to ``main`` publishes through one script, with a plain push.

``scripts/publish_to_main.sh`` replaced five separate publish steps on
2026-10-10. Two failure classes are pinned here, against throwaway
repositories:

* History loss. update-cycle.yml and build-feed.yml pushed with
  ``--force-with-lease``; the ``git pull --rebase`` between two attempts
  fetched ``origin/main`` and so re-armed the lease. Whenever that rebase had
  to be aborted, the next attempt force-pushed the old base over the commit
  that had landed in between (``main`` has no branch protection; on
  2026-09-11 the same lease mechanism dropped the merge of PR #1783). The
  test makes the rebase fail with a ``pre-rebase`` hook and checks that the
  concurrent commit survives.
* No retry. seo-guard.yml pushed once and went red when an update-cycle
  commit landed a second earlier (2026-10-04 17:27 UTC). The script re-syncs
  and pushes again.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "publish_to_main.sh"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="git and bash required",
)


def _env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    python = bin_dir / "python"
    if not python.exists():
        python.symlink_to(sys.executable)
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GITHUB_REF_NAME="main",
        PUBLISH_RETRY_DELAY="0",
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
    )
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    """A bare origin, the workflow's shallow checkout and a second writer."""
    env = _env(tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, env, "init", "-q", "-b", "main")
    _write(seed, "docs/feed.xml", "<rss>old</rss>\n")
    _write(seed, "README.md", "intro\n")
    _git(seed, env, "add", "-A")
    _git(seed, env, "commit", "-q", "-m", "seed")
    origin = tmp_path / "origin.git"
    _git(tmp_path, env, "clone", "-q", "--bare", str(seed), str(origin))
    work = tmp_path / "work"
    _git(tmp_path, env, "clone", "-q", "--depth", "1", "--branch", "main", f"file://{origin}", str(work))
    other = tmp_path / "other"
    _git(tmp_path, env, "clone", "-q", "--branch", "main", f"file://{origin}", str(other))
    return origin, work, other, env


def _concurrent_commit(other: Path, env: dict[str, str], path: str, text: str) -> str:
    _write(other, path, text)
    _git(other, env, "add", "-A")
    _git(other, env, "commit", "-q", "-m", f"concurrent {path}")
    _git(other, env, "push", "-q", "origin", "HEAD:main")
    return _git(other, env, "rev-parse", "HEAD")


def _publish(work: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    # The script sets the bot identity itself, as on a runner.
    run_env = {k: v for k, v in env.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=work, env=run_env, capture_output=True, text=True)


def _remote_file(origin: Path, env: dict[str, str], path: str) -> str:
    return _git(origin, env, "show", f"main:{path}")


def _is_ancestor(origin: Path, env: dict[str, str], commit: str) -> bool:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "main"], cwd=origin, env=env, capture_output=True)
    return result.returncode == 0


def test_publishes_only_the_listed_paths(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/feed.xml", "<rss>new</rss>\n")
    _write(work, "scratch.txt", "not for main\n")
    result = _publish(work, env, "--validate-feeds", "--message", "chore: rebuild feed", "--", "docs/feed.xml")
    assert result.returncode == 0, result.stderr
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>new</rss>"
    files = _git(origin, env, "ls-tree", "-r", "--name-only", "main").splitlines()
    assert "scratch.txt" not in files
    assert _git(origin, env, "log", "-1", "--format=%s %an", "main") == "chore: rebuild feed github-actions[bot]"


def test_no_changes_is_a_clean_no_op(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    before = _git(origin, env, "rev-parse", "main")
    result = _publish(work, env, "--all", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert "nothing to commit" in result.stdout
    assert _git(origin, env, "rev-parse", "main") == before


def test_malformed_feed_is_never_published(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/feed.xml", "<rss><item>\n")
    result = _publish(work, env, "--validate-feeds", "--all", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert "not well-formed XML" in result.stdout
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>old</rss>"


def test_concurrent_push_is_rebased_not_rejected(tmp_path: Path) -> None:
    # SEO Verify, 2026-10-04: a commit landed between checkout and push.
    origin, work, other, env = _setup(tmp_path)
    concurrent = _concurrent_commit(other, env, "docs/sitemap.xml", "<urlset/>\n")
    _write(work, "docs/feed.xml", "<rss>new</rss>\n")
    result = _publish(work, env, "--all", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert "Published on attempt 2/4" in result.stdout
    assert _is_ancestor(origin, env, concurrent)
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>new</rss>"
    assert _remote_file(origin, env, "docs/sitemap.xml") == "<urlset/>"


def test_conflict_keeps_the_locally_built_copy(tmp_path: Path) -> None:
    origin, work, other, env = _setup(tmp_path)
    concurrent = _concurrent_commit(other, env, "docs/feed.xml", "<rss>other</rss>\n")
    _write(work, "docs/feed.xml", "<rss>mine</rss>\n")
    result = _publish(work, env, "--all", "--label", "cycle-publish", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert "keeping the locally built copies of: docs/feed.xml" in result.stdout
    assert _is_ancestor(origin, env, concurrent)
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>mine</rss>"


@pytest.mark.parametrize("strict", [False, True])
def test_a_failed_rebase_never_overwrites_the_concurrent_commit(tmp_path: Path, strict: bool) -> None:
    # The --force-with-lease path: the pull re-armed the lease, the rebase
    # was aborted, the next attempt force-pushed over the merged commit.
    origin, work, other, env = _setup(tmp_path)
    concurrent = _concurrent_commit(other, env, "src/merged_pr.py", "x = 1\n")
    hook = work / ".git" / "hooks" / "pre-rebase"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _write(work, "docs/feed.xml", "<rss>new</rss>\n")
    args = ["--all", "--label", "cycle-publish", "--message", "chore: update cycle"]
    result = _publish(work, env, *(["--strict", *args] if strict else args))
    assert result.returncode == (1 if strict else 0), result.stdout + result.stderr
    assert "failed after 4 attempts" in result.stdout
    assert _git(origin, env, "rev-parse", "main") == concurrent
    assert _remote_file(origin, env, "src/merged_pr.py") == "x = 1"


def _workflow_runs() -> dict[str, list[str]]:
    runs: dict[str, list[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps", []):
                runs.setdefault(path.name, []).append(f"{step.get('uses', '')}\n{step.get('run', '')}")
    return runs


def test_no_workflow_force_pushes_or_pushes_on_its_own() -> None:
    publishers = set()
    for name, steps in _workflow_runs().items():
        for text in steps:
            assert not re.search(r"--force\b|--force-with-lease|\s-f\b.*push|push\s.*\s-f\b", text), name
            assert "git-auto-commit-action" not in text, name
            assert not re.search(r"\bgit push\b", text), name
            if "scripts/publish_to_main.sh" in text:
                publishers.add(name)
    assert publishers == {
        "build-feed.yml",
        "manual-full-refresh.yml",
        "seo-guard.yml",
        "update-cycle.yml",
        "update-stations.yml",
    }


def test_writers_hold_contents_write() -> None:
    for name in ("build-feed.yml", "manual-full-refresh.yml", "seo-guard.yml", "update-cycle.yml", "update-stations.yml"):
        doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        perms = [job.get("permissions", {}).get("contents") for job in doc["jobs"].values()]
        assert "write" in perms, name
