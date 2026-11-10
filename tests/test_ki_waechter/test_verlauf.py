# ruff: noqa: S603, S607
"""Der Verlauf des Prüflaufs hält den ganzen Tag (Prüfung des KI-Wächters vom 10.11.2026).

Bis 2026-11-10 holte der Prüflauf den Verlauf mit ``git fetch
--shallow-since`` (26 Stunden hinter dem geprüften Commit). Der Server
schneidet einen solchen Verlauf an jedem Commit ab, der einen älteren
Elternteil hat, und der Klon sieht diesen Commit dann ohne Eltern. Ein
Merge-Commit auf ``main``, dessen Zweig älter ist als die 26 Stunden, ist ein
solcher Commit: ``git log`` endete dort, und jede Fassung des Feeds davor
fehlte, obwohl sie im Prüfzeitraum lag. Nachgestellt am 10.11.2026 mit den
Befehlen des Workflows: der Lauf vom 07.11.2026 las 38 von 51 Fassungen (50
von 85 Texten, abgeschnitten am Merge von #2169), der vom 10.11.2026 47 von 51
(84 von 92, Merge von #2185). Der Bericht nannte den kürzeren Zeitraum, sonst
fiel nichts auf.

Seither lädt der Workflow nach Tiefe und verdoppelt sie, bis ``history_covers``
``main`` (die ersten Eltern) bis vor den Beginn des Zeitraums findet;
``prepare`` lehnt einen Verlauf ab, der im Zeitraum endet.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import ki_waechter as kw
from src.ki_waechter.inputs import history_covers

from ._support import REPO, _write_feeds, _write_repo

HOUR = 3600
# Mittwoch, 11.11.2026, 12:00 UTC: der geprüfte Commit.
T = 1_794_398_400


def _git(cwd: Path, *args: str, when: int | None = None) -> str:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
    )
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{when} +0000"
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def _tick(origin: Path, when: int) -> None:
    """One Update cycle: a new feed version on main."""
    row = ("40A: Bauarbeiten", f"Busse halten Liechtensteinstraße {when}. [Heute]", "g1")
    _write_feeds(origin, [row], [row])
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "chore: update cycle", when=when)


def _origin(tmp_path: Path, *, merge_old_branch: bool) -> Path:
    """main with one feed version an hour for 52 hours; a branch of two days ago merged 10.5 hours before T."""
    origin = tmp_path / "origin"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    _write_repo(origin)
    for hours_ago in range(52, 10, -1):
        _tick(origin, T - hours_ago * HOUR)
        if hours_ago == 50:
            _git(origin, "branch", "alt")
    if merge_old_branch:
        # A pull request opened two days ago and merged without "Update branch".
        _git(origin, "checkout", "-q", "alt")
        (origin / "notiz.txt").write_text("x\n", encoding="utf-8")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "Änderung im Zweig", when=T - 49 * HOUR)
        _git(origin, "checkout", "-q", "main")
        _git(origin, "merge", "-q", "--no-ff", "-m", "Merge pull request", "alt", when=T - 10 * HOUR - 1800)
    for hours_ago in range(10, -1, -1):
        _tick(origin, T - hours_ago * HOUR)
    return origin


def _checkout(tmp_path: Path, origin: Path) -> Path:
    """``actions/checkout`` with ``fetch-depth: 1``."""
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--branch", "main", f"file://{origin}", str(work))
    return work


def _load_until_covered(work: Path, depth: int = 128) -> int:
    """The loop of the step "Prepare the input"; the depth it ended with."""
    for _ in range(5):
        _git(work, "fetch", "--quiet", f"--depth={depth}", "origin", "main")
        if history_covers(work, "HEAD", 24):
            break
        depth *= 2
    return depth


def test_the_fetch_of_the_workflow_holds_the_day(tmp_path: Path) -> None:
    work = _checkout(tmp_path, _origin(tmp_path, merge_old_branch=True))
    # Only the tip: main ends inside the window.
    assert not history_covers(work, "HEAD", 24)
    assert _load_until_covered(work) == 128
    assert history_covers(work, "HEAD", 24)
    snapshots = kw.git_snapshots(work, "HEAD", 24)
    # T-24 h to T, one version an hour; the merged branch and its merge commit are no versions.
    assert len(snapshots) == 25
    assert [s.at for s in snapshots] == sorted(s.at for s in snapshots)


def test_the_loop_doubles_the_depth_until_main_reaches_the_start(tmp_path: Path) -> None:
    work = _checkout(tmp_path, _origin(tmp_path, merge_old_branch=True))
    # A day with more commits than the first depth holds (here: a depth of 8 against 25 versions).
    assert _load_until_covered(work, depth=8) == 32
    assert len(kw.git_snapshots(work, "HEAD", 24)) == 25


def test_a_fetch_by_date_was_cut_at_the_merge_of_an_older_branch(tmp_path: Path) -> None:
    """The fetch until 2026-11-10: 26 hours by date. Kept as the reason for the depth."""
    plain = _checkout(tmp_path / "a", _origin(tmp_path / "a", merge_old_branch=False))
    _git(plain, "fetch", "--quiet", f"--shallow-since=@{T - 26 * HOUR}", "origin", "main")
    assert history_covers(plain, "HEAD", 24) and len(kw.git_snapshots(plain, "HEAD", 24)) == 25
    cut = _checkout(tmp_path / "b", _origin(tmp_path / "b", merge_old_branch=True))
    _git(cut, "fetch", "--quiet", f"--shallow-since=@{T - 26 * HOUR}", "origin", "main")
    # The merge commit stands without parents: the 14 versions before it are gone, and nothing says so.
    assert not history_covers(cut, "HEAD", 24)
    assert len(kw.git_snapshots(cut, "HEAD", 24)) == 12


def test_prepare_refuses_a_history_that_ends_inside_the_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    work = _checkout(tmp_path, _origin(tmp_path, merge_old_branch=True))
    _git(work, "fetch", "--quiet", "--depth=8", "origin", "main")
    out = tmp_path / "out"
    assert kw.main(["covered", "--root", str(work), "--rev", "HEAD"]) == 1
    assert kw.main(["prepare", "--root", str(work), "--rev", "HEAD", "--out", str(out)]) == 1
    assert "::error title=KI-Wächter::Der Verlauf endet innerhalb des Prüfzeitraums" in capsys.readouterr().out
    assert not out.exists()
    # Looking at a shallow clone by hand stays possible, and says so by its flag.
    assert kw.main(["prepare", "--root", str(work), "--rev", "HEAD", "--out", str(out), "--allow-cut"]) == 0
    _load_until_covered(work)
    assert kw.main(["covered", "--root", str(work), "--rev", "HEAD"]) == 0
    assert kw.main(["prepare", "--root", str(work), "--rev", "HEAD", "--out", str(out)]) == 0
    assert "25 Fassungen" in capsys.readouterr().out


def test_a_complete_clone_with_a_short_history_is_whole(tmp_path: Path) -> None:
    # A repository younger than the window (the tests' own histories): nothing is cut, nothing to load.
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _write_repo(origin)
    for hours_ago in (3, 2, 1):
        _tick(origin, T - hours_ago * HOUR)
    assert history_covers(origin, "HEAD", 24)


def test_the_workflow_loads_by_depth_until_the_window_is_whole() -> None:
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    steps = {s.get("name"): s for s in doc["jobs"]["audit"]["steps"]}
    code = "\n".join(line for line in steps["Prepare the input"]["run"].splitlines() if not line.lstrip().startswith("#"))
    fetch, covered, double, prepare = (
        code.index('git fetch --quiet --depth="${depth}" origin "${target}"'), code.index("ki_waechter.py covered"),
        code.index("depth * 2"), code.index("ki_waechter.py prepare"),
    )
    assert "depth=128" in code and fetch < covered < double < prepare
    # The same revision and window for the check and for the input; no fetch by date, no escape flag.
    assert code.count('--rev "${REV}" --hours 24') == 2
    assert "--shallow-since" not in code and "--allow-cut" not in code
