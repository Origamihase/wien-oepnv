"""No file in the repository grows beyond 700 lines.

Michael's rule of 2026-11-02: the project becomes modular, a file may not
have more than 700 lines, and that holds for every text file (code, docs,
CHANGELOG, archived audit reports). Only the source data under ``data/``
and ``cache/`` is left out, as are JSON/CSV/XML files and images, whose
suffixes are not in ``_SUFFIXES``.

Files that were already longer on 2026-11-02 are listed in
``tests/line_limit_altfaelle.txt``. They are split stage by stage. The list
may only get shorter: a file that falls to 700 lines or fewer must leave
it, and the frozen ``_ALTFAELLE_COUNT`` stops new entries.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ALTFAELLE = _ROOT / "tests" / "line_limit_altfaelle.txt"
LIMIT = 700
_SUFFIXES = frozenset(
    {".py", ".pyi", ".js", ".mjs", ".css", ".html", ".md", ".yml", ".yaml", ".sh", ".toml", ".cfg", ".ini", ".txt"}
)
_EXEMPT_PREFIXES = ("data/", "cache/")
# Number of entries on 2026-11-02. Lower it whenever an entry is deleted;
# it may never go up.
_ALTFAELLE_COUNT = 27


def _tracked_files() -> list[str]:
    git = shutil.which("git")
    assert git is not None, "git fehlt: die Grenze zählt die Dateien aus git ls-files"
    argv = [git, "ls-files", "-z"]
    out = subprocess.run(argv, cwd=_ROOT, check=True, capture_output=True, timeout=60).stdout.decode("utf-8")  # noqa: S603
    return [p for p in out.split("\0") if p]


def is_counted(path: str) -> bool:
    if path.startswith(_EXEMPT_PREFIXES):
        return False
    return Path(path).suffix.lower() in _SUFFIXES


def count_lines(path: Path) -> int:
    """Count lines like ``wc -l`` plus a last line without newline."""
    data = path.read_bytes()
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _load_altfaelle() -> list[str]:
    paths: list[str] = []
    for raw in _ALTFAELLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            paths.append(line)
    return paths


def _measure() -> dict[str, int]:
    return {
        path: count_lines(_ROOT / path)
        for path in _tracked_files()
        if is_counted(path) and (_ROOT / path).is_file()
    }


def test_no_file_exceeds_the_limit() -> None:
    altfaelle = set(_load_altfaelle())
    too_long = [
        f"{path}: {lines} Zeilen"
        for path, lines in sorted(_measure().items())
        if lines > LIMIT and path not in altfaelle
    ]
    assert not too_long, f"Dateien über {LIMIT} Zeilen (aufteilen):\n" + "\n".join(too_long)


def test_altfaelle_list_only_shrinks() -> None:
    paths = _load_altfaelle()
    assert len(paths) == len(set(paths)), f"Doppelte Einträge in {_ALTFAELLE.name}"
    assert len(paths) <= _ALTFAELLE_COUNT, f"{_ALTFAELLE.name} darf keine neuen Einträge bekommen"
    assert len(paths) == _ALTFAELLE_COUNT, f"Eintrag gelöscht: _ALTFAELLE_COUNT auf {len(paths)} senken"


def test_altfaelle_are_still_over_the_limit() -> None:
    measured = _measure()
    stale = []
    for path in _load_altfaelle():
        lines = measured.get(path)
        if lines is None:
            stale.append(f"{path}: nicht mehr vorhanden oder nicht gezählt")
        elif lines <= LIMIT:
            stale.append(f"{path}: nur noch {lines} Zeilen, Eintrag löschen")
    assert not stale, f"Veraltete Einträge in {_ALTFAELLE.name}:\n" + "\n".join(stale)


def test_count_lines_matches_wc(tmp_path: Path) -> None:
    sample = tmp_path / "x.py"
    sample.write_bytes(b"a\nb\nc")
    assert count_lines(sample) == 3
    sample.write_bytes(b"a\nb\n")
    assert count_lines(sample) == 2
    sample.write_bytes(b"")
    assert count_lines(sample) == 0


def test_exemptions() -> None:
    assert is_counted("CHANGELOG.md")
    assert is_counted("docs/archive/audits/audit-2026-09-13.md")
    assert not is_counted("data/stations.json")
    assert not is_counted("tests/providers/data/wl_stop_notices_2026-10-04.json")
    assert not is_counted("docs/assets/feed-item-raufaser.jpg")
    assert is_counted("src/build_feed.py")
    assert is_counted("docs/architecture.md")
    assert is_counted("docs/assets/site.js")
