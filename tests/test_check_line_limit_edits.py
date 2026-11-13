"""A file at the 700-line limit is split, not trimmed.

``scripts/check_line_limit_edits.py`` compares the work tree with a base
revision (Michael's decision of 2026-11-13). Between 2026-11-03 and
2026-11-13 fourteen files were held at exactly 700 lines by changes that
added lines and removed as many (shortened comments, joined lines). The gate
fails such a change; these tests replay it in a small git repository.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import check_line_limit_edits as gate
from src.utils import line_limit
from src.utils.line_limit import LIMIT, SPLIT_FROM, SPLIT_TO

REPO = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str:
    git = shutil.which("git")
    assert git is not None
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "HOME": str(root),
    }
    proc = subprocess.run([git, *args], cwd=root, check=True, capture_output=True, timeout=60, env=env)  # noqa: S603
    return proc.stdout.decode("utf-8").strip()


def _lines(count: int, prefix: str = "line") -> str:
    return "".join(f"{prefix} {i}\n" for i in range(count))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "data").mkdir()
    (root / "src" / "full.py").write_text(_lines(LIMIT), encoding="utf-8")
    (root / "src" / "edge.py").write_text(_lines(SPLIT_FROM), encoding="utf-8")
    (root / "src" / "room.py").write_text(_lines(SPLIT_FROM - 1), encoding="utf-8")
    (root / "data" / "big.txt").write_text(_lines(LIMIT), encoding="utf-8")
    (root / "table.json").write_text(_lines(LIMIT), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _findings(root: Path, base: str = "HEAD") -> dict[str, gate.Finding]:
    return {f.path: f for f in gate.find_unsplit_edits(root, gate.resolve_base(root, base))}


def test_untouched_tree_passes(repo: Path) -> None:
    assert _findings(repo) == {}


def test_zero_sum_edit_at_the_limit_fails(repo: Path) -> None:
    """Twelve lines in, twelve out: the edit that kept update-cycle.yml at 700."""
    path = repo / "src" / "full.py"
    kept = path.read_text(encoding="utf-8").splitlines(keepends=True)[12:]
    path.write_text("".join(kept) + _lines(12, "new"), encoding="utf-8")
    found = _findings(repo)
    assert set(found) == {"src/full.py"}
    assert (found["src/full.py"].base_lines, found["src/full.py"].lines, found["src/full.py"].added) == (LIMIT, LIMIT, 12)


def test_one_changed_line_at_the_limit_fails(repo: Path) -> None:
    path = repo / "src" / "full.py"
    path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line five\n"), encoding="utf-8")
    assert set(_findings(repo)) == {"src/full.py"}


def test_trimming_down_to_the_edge_still_fails(repo: Path) -> None:
    """Ten lines removed, one added: 691 lines are still at the limit."""
    path = repo / "src" / "full.py"
    kept = path.read_text(encoding="utf-8").splitlines(keepends=True)[10:]
    path.write_text("".join(kept) + "new\n", encoding="utf-8")
    assert set(_findings(repo)) == {"src/full.py"}


def test_trimming_below_the_zone_while_adding_fails(repo: Path) -> None:
    """Twelve lines removed, one added: 689 lines are a trim, not a split."""
    path = repo / "src" / "full.py"
    kept = path.read_text(encoding="utf-8").splitlines(keepends=True)[12:]
    path.write_text("".join(kept) + "new\n", encoding="utf-8")
    found = _findings(repo)
    assert set(found) == {"src/full.py"}
    assert found["src/full.py"].lines == SPLIT_FROM - 1


def test_a_split_leaves_at_most_split_to_lines(repo: Path) -> None:
    path = repo / "src" / "full.py"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("new\n" + "".join(lines[: SPLIT_TO]), encoding="utf-8")
    assert set(_findings(repo)) == {"src/full.py"}
    path.write_text("new\n" + "".join(lines[: SPLIT_TO - 1]), encoding="utf-8")
    assert _findings(repo) == {}


def test_renamed_file_keeps_its_count(repo: Path) -> None:
    """A new name with a new line: the file is compared with its old path."""
    _git(repo, "mv", "src/full.py", "src/moved.py")
    path = repo / "src" / "moved.py"
    path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line five\n"), encoding="utf-8")
    _git(repo, "add", "-A")
    found = _findings(repo)
    assert set(found) == {"src/moved.py"}
    assert found["src/moved.py"].base_lines == LIMIT


def test_counted_file_git_reads_as_binary_is_not_skipped(repo: Path) -> None:
    path = repo / "src" / "full.py"
    path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line\x00five\n"), encoding="utf-8")
    assert set(_findings(repo)) == {"src/full.py"}


def test_revert_into_a_file_at_the_limit_fails_like_any_change(repo: Path) -> None:
    path = repo / "src" / "full.py"
    path.write_text("".join(path.read_text(encoding="utf-8").splitlines(keepends=True)[3:]), encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "three lines less")
    _git(repo, "revert", "--no-edit", "HEAD")
    assert set(_findings(repo, "HEAD^1")) == {"src/full.py"}


def test_first_line_count_of_the_zone_counts(repo: Path) -> None:
    path = repo / "src" / "edge.py"
    path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line five\n"), encoding="utf-8")
    assert set(_findings(repo)) == {"src/edge.py"}


def test_split_passes(repo: Path) -> None:
    path = repo / "src" / "full.py"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("from .full_b import *\n" + "".join(lines[:350]), encoding="utf-8")
    (repo / "src" / "full_b.py").write_text("".join(lines[350:]) + _lines(12, "new"), encoding="utf-8")
    assert _findings(repo) == {}


def test_removing_lines_only_passes(repo: Path) -> None:
    path = repo / "src" / "full.py"
    path.write_text("".join(path.read_text(encoding="utf-8").splitlines(keepends=True)[3:]), encoding="utf-8")
    assert _findings(repo) == {}


def test_growing_into_the_zone_passes_once(repo: Path) -> None:
    """A file below 690 may reach 700; the change after that has to split."""
    path = repo / "src" / "room.py"
    path.write_text(_lines(SPLIT_FROM - 1) + _lines(11, "new"), encoding="utf-8")
    assert _findings(repo) == {}
    _git(repo, "commit", "-q", "-am", "grown")
    path.write_text(path.read_text(encoding="utf-8").replace("new 3\n", "new three\n"), encoding="utf-8")
    assert set(_findings(repo)) == {"src/room.py"}


def test_new_and_renamed_files_pass(repo: Path) -> None:
    (repo / "src" / "fresh.py").write_text(_lines(LIMIT), encoding="utf-8")
    _git(repo, "mv", "src/full.py", "src/moved.py")
    _git(repo, "add", "-A")
    assert _findings(repo) == {}


def test_files_outside_the_rule_pass(repo: Path) -> None:
    for name in ("data/big.txt", "table.json"):
        path = repo / name
        path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line five\n"), encoding="utf-8")
    assert _findings(repo) == {}


def test_committed_change_is_seen_against_the_parent(repo: Path) -> None:
    """CI compares with ``HEAD^1``: the base of a merge commit or the last push."""
    path = repo / "src" / "full.py"
    path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line five\n"), encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "edit")
    assert _findings(repo, "HEAD") == {}
    assert set(_findings(repo, "HEAD^1")) == {"src/full.py"}


def test_main_exit_codes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert gate.main(["--root", str(repo), "--base", "HEAD"]) == 0
    path = repo / "src" / "full.py"
    path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line five\n"), encoding="utf-8")
    assert gate.main(["--root", str(repo), "--base", "HEAD"]) == 1
    err = capsys.readouterr().err
    assert "src/full.py: vorher 700, jetzt 700 Zeilen, 1 neue" in err
    assert "aufteilen" in err


def test_unknown_base_is_no_measurement(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A shallow checkout without the base must not pass as green."""
    assert gate.main(["--root", str(repo), "--base", "HEAD^1"]) == 2
    assert gate.main(["--root", str(repo)]) == 2  # no origin/main in this repository
    assert "keine Messung" in capsys.readouterr().err


def test_missing_git_is_no_measurement(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert gate.main(["--root", str(repo), "--base", "HEAD"]) == 2


def test_unreadable_numstat_is_no_measurement(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = gate._git

    def fake(root: Path, *args: str) -> bytes:
        if args[:2] == ("diff", "--numstat"):
            return b"garbage\0"
        return real(root, *args)

    monkeypatch.setattr(gate, "_git", fake)
    assert gate.main(["--root", str(repo), "--base", "HEAD"]) == 2


def test_tests_workflow_runs_the_gate_with_its_base() -> None:
    """The gate needs the parent commit: every checkout of the job fetches two."""
    text = (REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "python scripts/check_line_limit_edits.py --base HEAD^1" in text
    assert text.count("uses: actions/checkout@") == text.count("fetch-depth: 2") == 2
    assert text.index("check_line_limit_edits.py") < text.index("python -m pytest")


def test_file_that_cannot_be_counted_is_no_measurement(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file above the read cap gives no count, and no count is no pass."""
    path = repo / "src" / "full.py"
    path.write_text(path.read_text(encoding="utf-8").replace("line 5\n", "line five\n"), encoding="utf-8")
    monkeypatch.setattr(line_limit, "MAX_COUNTED_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="nicht lesbar oder größer"):
        line_limit.count_lines(path)
    assert gate.main(["--root", str(repo), "--base", "HEAD"]) == 2
