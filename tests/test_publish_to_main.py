# ruff: noqa: S603, S607
"""Every workflow that writes to ``main`` publishes through one script, with a plain push.

``scripts/publish_to_main.sh`` replaced five separate publish steps on
2026-10-10. Two failure classes are pinned here, against throwaway
repositories:

* History loss. update-cycle.yml and build-feed.yml pushed with
  ``--force-with-lease``; the ``git pull --rebase`` between two attempts
  fetched ``origin/main`` and so re-armed the lease. Whenever that rebase had
  to be aborted, the next attempt force-pushed the old base over the commit
  that had landed in between (``main`` had no branch protection then, the
  ruleset "main schützen" blocks force pushes since 2026-10-16; on
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
    assert "not checked" not in result.stdout
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>old</rss>"


def test_a_staged_page_with_liquid_is_made_inert(tmp_path: Path) -> None:
    # 2026-11-01: one bare "{%" under docs/ failed every Pages build; bot
    # commits start the Pages build but no test run (step 2b).
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/stations_diff.md", "# Diff\n\n- Wien Mitte {% Ersatz\n- S{{{1}}}\n")
    _write(work, "docs/feed.xml", "<rss>{{ static }}</rss>\n")
    _write(work, "docs/ok.md", "<!-- {% raw %} -->\n\n`${{ x }}`\n\n<!-- {% endraw %} -->\n")
    result = _publish(work, env, "--all", "--label", "Stationspflege", "--message", "chore: stations")
    assert result.returncode == 0, result.stderr
    assert "::warning title=Stationspflege::Liquid in docs/stations_diff.md (line 3,4) made inert" in result.stdout
    assert _remote_file(origin, env, "docs/stations_diff.md") == "# Diff\n\n- Wien Mitte { % Ersatz\n- S{ { {1}}}"
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>{{ static }}</rss>"
    assert "{{ x }}" in _remote_file(origin, env, "docs/ok.md")
    assert "ok.md" not in result.stdout


def test_a_page_with_any_file_name_is_made_inert(tmp_path: Path) -> None:
    # git quotes a name with an umlaut or a quote in --name-only output
    # unless -z is given; such a page passed step 2b unchanged until the
    # review of 2026-11-01.
    origin, work, _other, env = _setup(tmp_path)
    for name in ("docs/Störung.md", 'docs/say "hi".md', "docs/with space.md"):
        _write(work, name, "x {% y\n")
    result = _publish(work, env, "--all", "--label", "Stationspflege", "--message", "chore: stations")
    assert result.returncode == 0, result.stderr
    for name in ("docs/Störung.md", 'docs/say "hi".md', "docs/with space.md"):
        assert f"Liquid in {name} (line 1) made inert" in result.stdout
        assert _remote_file(origin, env, name) == "x { % y"


def test_a_clean_page_publishes_without_a_liquid_warning(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/statistik.md", "# Statistik\n\n| S1 | 3 |\n")
    result = _publish(work, env, "--message", "chore: stats", "--", "docs/statistik.md")
    assert result.returncode == 0, result.stderr
    assert "Liquid" not in result.stdout
    assert _remote_file(origin, env, "docs/statistik.md") == "# Statistik\n\n| S1 | 3 |"


def test_a_symlinked_page_publishes_its_target_made_inert(tmp_path: Path) -> None:
    # Review of 2026-11-01: the target was rewritten but only the
    # link, whose blob does not change, was staged again; the Liquid went out.
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/note.md", "x {% y\n")
    (work / "docs" / "alias.md").symlink_to("note.md")
    _write(work, "docs/notiz.txt", "Notiz: {% ohne Tag\n")
    (work / "docs" / "notiz.md").symlink_to("notiz.txt")
    result = _publish(work, env, "--all", "--label", "L", "--message", "m")
    assert result.returncode == 0, result.stderr
    assert "Liquid in docs/note.md (line 1) made inert" in result.stdout
    assert "Liquid in docs/notiz.txt (line 1) made inert" in result.stdout
    assert _remote_file(origin, env, "docs/note.md") == "x { % y"
    assert _remote_file(origin, env, "docs/notiz.txt") == "Notiz: { % ohne Tag"
    assert _git(work, env, "status", "--porcelain") == ""


def test_a_page_the_check_cannot_read_is_named_in_a_warning(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    (work / "docs" / "latin1.md").write_bytes("# St\xf6rung\n\nText {% x\n".encode("latin-1"))
    _write(work, "docs/ok.md", "fine {% z\n")
    result = _publish(work, env, "--all", "--label", "L", "--message", "m")
    assert result.returncode == 0, result.stderr
    assert (
        "::warning title=L::Liquid check passed over staged pages under docs/ it could not read"
        " (liquid.py names them in the log)" in result.stdout
    )
    assert "latin1.md skipped (UnicodeDecodeError" in result.stderr
    assert _remote_file(origin, env, "docs/ok.md") == "fine { % z"


def _bare_path(tmp_path: Path, *, python3: bool) -> str:
    """A PATH with only the tools the script needs, python at most as python3."""
    bin_dir = tmp_path / "bare-bin"
    bin_dir.mkdir()
    for tool in ("git", "bash", "mktemp", "rm", "dirname", "sleep", "cat"):
        found = shutil.which(tool)
        assert found, tool
        (bin_dir / tool).symlink_to(found)
    if python3:
        (bin_dir / "python3").symlink_to(sys.executable)
    return str(bin_dir)


def test_without_python_the_page_is_published_with_a_warning(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/x.md", "x {% y\n")
    env = {**env, "PATH": _bare_path(tmp_path, python3=False)}
    result = _publish(work, env, "--all", "--label", "L", "--message", "m")
    assert result.returncode == 0, result.stderr
    # liquid.py never ran, so the warning must not send the reader to its log.
    assert "::warning title=L::Liquid check skipped: no python on PATH" in result.stdout
    assert "names them" not in result.stdout
    assert _remote_file(origin, env, "docs/x.md") == "x {% y"


def test_a_liquid_check_that_stops_is_named_in_a_warning(tmp_path: Path) -> None:
    # A python that fails before liquid.py writes anything (exit 1) names no
    # page; only exit 2 does.
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/x.md", "x {% y\n")
    bare = _bare_path(tmp_path, python3=False)
    stub = Path(bare) / "python"
    stub.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {**env, "PATH": bare}
    result = _publish(work, env, "--all", "--label", "L", "--message", "m")
    assert result.returncode == 0, result.stderr
    assert "::warning title=L::Liquid check stopped (liquid.py exit 1)" in result.stdout
    assert "names them" not in result.stdout
    assert _remote_file(origin, env, "docs/x.md") == "x {% y"


def test_with_only_python3_the_page_is_made_inert(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/x.md", "x {% y\n")
    env = {**env, "PATH": _bare_path(tmp_path, python3=True)}
    result = _publish(work, env, "--all", "--label", "L", "--message", "m")
    assert result.returncode == 0, result.stderr
    assert "Liquid in docs/x.md (line 1) made inert" in result.stdout
    assert "Liquid check" not in result.stdout
    assert _remote_file(origin, env, "docs/x.md") == "x { % y"


def test_the_feed_check_uses_the_same_python_as_the_liquid_check(tmp_path: Path) -> None:
    # Step 1 called a bare "python" while step 2b already found python3:
    # with only python3, a well-formed feed was called malformed and the
    # publish skipped (review of 2026-11-01).
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/feed.xml", "<rss>new</rss>\n")
    env = {**env, "PATH": _bare_path(tmp_path, python3=True)}
    result = _publish(work, env, "--validate-feeds", "--all", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert "feed-validate" not in result.stdout
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>new</rss>"


def test_without_python_the_feed_is_not_published_and_the_warning_says_why(tmp_path: Path) -> None:
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/feed.xml", "<rss>new</rss>\n")
    env = {**env, "PATH": _bare_path(tmp_path, python3=False)}
    result = _publish(work, env, "--validate-feeds", "--all", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert "::warning title=feed-validate::docs/feed.xml not checked: no python on PATH" in result.stdout
    assert "not well-formed" not in result.stdout
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>old</rss>"


def test_a_feed_check_that_cannot_run_does_not_call_the_feed_malformed(tmp_path: Path) -> None:
    # A python that fails before parsing (exit 1 here; 127 from a shim) was
    # reported as "not well-formed XML" (review of 2026-11-01).
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/feed.xml", "<rss>new</rss>\n")
    bare = _bare_path(tmp_path, python3=False)
    stub = Path(bare) / "python"
    stub.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {**env, "PATH": bare}
    result = _publish(work, env, "--validate-feeds", "--all", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert f"::warning title=feed-validate::docs/feed.xml not checked: {stub} exit 1" in result.stdout
    assert "not well-formed" not in result.stdout
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>old</rss>"


# "foo" is unknown to Python (LookupError); "shift_jis" is known, but the
# parser takes no multi-byte encoding (ValueError).
@pytest.mark.parametrize("encoding", ["foo", "shift_jis"])
def test_a_feed_with_an_encoding_the_parser_rejects_is_called_malformed(
    tmp_path: Path, encoding: str
) -> None:
    origin, work, _other, env = _setup(tmp_path)
    _write(work, "docs/feed.xml", f'<?xml version="1.0" encoding="{encoding}"?>\n<rss>new</rss>\n')
    result = _publish(work, env, "--validate-feeds", "--all", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stderr
    assert "docs/feed.xml is not well-formed XML" in result.stdout
    assert "not checked" not in result.stdout
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
    assert "keeping the locally built lines of: docs/feed.xml" in result.stdout
    assert _is_ancestor(origin, env, concurrent)
    assert _remote_file(origin, env, "docs/feed.xml") == "<rss>mine</rss>"


def test_a_conflict_keeps_the_concurrent_lines_it_does_not_touch(tmp_path: Path) -> None:
    # Gesamtprüfung 11.10.2026: a hand edit next to a regenerated block
    # conflicts; the whole local copy replaced it and the edit was gone.
    origin, work, other, env = _setup(tmp_path)
    # The edit of "intro" abuts the stats line: that hunk conflicts and takes
    # the local lines; the title, two lines away, merges as it is.
    base = "# Title\n\nintro\n3 Meldungen\n"
    _concurrent_commit(other, env, "README.md", base)
    _git(work, env, "pull", "-q", "origin", "main")
    _concurrent_commit(
        other, env, "README.md", base.replace("# Title", "# New title").replace("intro", "intro, edited")
    )
    _write(work, "README.md", base.replace("3 Meldungen", "5 Meldungen"))
    result = _publish(work, env, "--all", "--label", "cycle-publish", "--message", "chore: update cycle")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "keeping the locally built lines of: README.md" in result.stdout
    readme = _remote_file(origin, env, "README.md")
    assert readme.startswith("# New title\n")
    assert "edited" not in readme
    assert "5 Meldungen" in readme


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
        "ki-waechter.yml",
        "manual-full-refresh.yml",
        "seo-guard.yml",
        "update-cycle.yml",
        "update-stations.yml",
    }


def test_writers_hold_contents_write() -> None:
    writers = ("build-feed.yml", "ki-waechter.yml", "manual-full-refresh.yml", "seo-guard.yml", "update-cycle.yml", "update-stations.yml")
    for name in writers:
        doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        perms = [job.get("permissions", {}).get("contents") for job in doc["jobs"].values()]
        assert "write" in perms, name


def test_publishers_check_out_the_branch_tip() -> None:
    # Without ``ref`` actions/checkout fetches ``github.sha``, the commit at
    # trigger time. A run its concurrency lane held back would build on that
    # older commit and its state files would win the rebase conflict over the
    # newer ones (update tick 17.10.2026 08:30 UTC behind the rebuild of #2034).
    for name, steps in _workflow_runs().items():
        if not any("scripts/publish_to_main.sh" in text for text in steps):
            continue
        doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        checkouts = [
            step
            for job in doc["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        assert checkouts, name
        for step in checkouts:
            assert step.get("with", {}).get("ref") == "${{ github.ref }}", (name, step.get("name"))
