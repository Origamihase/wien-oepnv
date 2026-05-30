"""Regression coverage for the batched ``<lastmod>`` resolution.

The sitemap generator used to spawn one ``git log -1`` per file (an N+1
pattern). These tests pin the replacement: a single ``git`` process for
the whole tree, with the same git-commit-date / mtime-fallback / future
clamp semantics the per-file version had.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import generate_sitemap


def _git(repo: Path, *args: str, committer_date: str | None = None) -> None:
    env = dict(os.environ)
    if committer_date is not None:
        env["GIT_COMMITTER_DATE"] = committer_date
        env["GIT_AUTHOR_DATE"] = committer_date
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway, full-history git repo wired into the module globals."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    # Hermetic commits: the throwaway repo must not depend on (or invoke) any
    # ambient commit-signing config inherited from the host's global git setup.
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(generate_sitemap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(generate_sitemap, "DOCS_DIR", tmp_path / "docs")
    return tmp_path


def test_git_lastmod_map_returns_newest_commit_date(tmp_repo: Path) -> None:
    page = tmp_repo / "docs" / "page.md"
    page.write_text("# Page\n", encoding="utf-8")
    _git(tmp_repo, "add", "docs/page.md")
    _git(tmp_repo, "commit", "-qm", "add page",
         committer_date="2025-03-04T12:00:00+00:00")

    result = generate_sitemap._git_lastmod_map([page])

    assert result[page].startswith("2025-03-04")


def test_git_lastmod_map_skips_untracked_paths(tmp_repo: Path) -> None:
    untracked = tmp_repo / "docs" / "draft.md"
    untracked.write_text("# Draft\n", encoding="utf-8")

    # Not committed → absent from the map → caller falls back to mtime.
    assert generate_sitemap._git_lastmod_map([untracked]) == {}


def test_git_lastmod_map_ignores_paths_outside_repo(tmp_path: Path) -> None:
    # A path outside REPO_ROOT (the real module global) must not raise and
    # must simply be skipped — this is the patched-DOCS_DIR test scenario.
    outsider = tmp_path / "elsewhere.md"
    outsider.write_text("x", encoding="utf-8")
    assert generate_sitemap._git_lastmod_map([outsider]) == {}


def test_resolve_lastmod_falls_back_to_mtime(tmp_repo: Path) -> None:
    page = tmp_repo / "docs" / "x.md"
    page.write_text("x", encoding="utf-8")
    today = _dt.date.today().isoformat()

    # No git timestamp → mtime (just written ≈ now) → at most today.
    assert generate_sitemap._resolve_lastmod(page, None) <= today


def test_resolve_lastmod_clamps_future_dates(tmp_repo: Path) -> None:
    page = tmp_repo / "docs" / "x.md"
    page.write_text("x", encoding="utf-8")
    future = (_dt.date.today() + _dt.timedelta(days=400)).isoformat()

    resolved = generate_sitemap._resolve_lastmod(page, f"{future}T00:00:00+00:00")

    assert resolved == _dt.date.today().isoformat()


def test_collect_entries_uses_single_git_process(tmp_repo: Path) -> None:
    for index in range(6):
        leaf = tmp_repo / "docs" / f"p{index}.md"
        leaf.write_text(f"# {index}\n", encoding="utf-8")
    _git(tmp_repo, "add", "docs")
    _git(tmp_repo, "commit", "-qm", "bulk add",
         committer_date="2025-01-01T00:00:00+00:00")

    with patch.object(subprocess, "Popen", wraps=subprocess.Popen) as spawned:
        entries = generate_sitemap._collect_entries("https://forker.github.io/base")

    # One process for the whole tree, not one per file.
    assert spawned.call_count == 1
    assert len(entries) == 6
    assert all(lastmod.startswith("2025-01-01") for _, lastmod, _ in entries)
