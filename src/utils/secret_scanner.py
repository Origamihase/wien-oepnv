"""Utility helpers to detect accidentally committed secrets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
import re
import subprocess

__all__ = [
    "Finding",
    "scan_repository",
    "load_ignore_file",
]

_HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9])")
_SENSITIVE_ASSIGN_RE = re.compile(
    r"(?i)(token|secret|password|accessid|apikey|authorization)[^\S\n]*[:=][^\S\n]*['\"]?([A-Za-z0-9+/=_-]{16,})['\"]?"
)
_BEARER_RE = re.compile(r"Bearer\s+([A-Za-z0-9\-_.]{16,})")


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    match: str
    reason: str


def load_ignore_file(base_dir: Path, filename: str = ".secret-scan-ignore") -> list[str]:
    path = base_dir / filename
    if not path.exists():
        return []
    patterns: list[str] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _tracked_files(base_dir: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=base_dir,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [path for path in base_dir.rglob("*") if path.is_file()]
    stdout = completed.stdout.decode("utf-8", errors="ignore")
    files: list[Path] = []
    for entry in stdout.split("\0"):
        if not entry:
            continue
        files.append((base_dir / entry).resolve())
    return files


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return True
    return b"\0" in chunk


def _looks_like_secret(candidate: str) -> bool:
    if len(candidate) < 24:
        return False
    categories = 0
    categories += any(c.islower() for c in candidate)
    categories += any(c.isupper() for c in candidate)
    categories += any(c.isdigit() for c in candidate)
    if categories < 2:
        return False
    if len(set(candidate)) < max(6, len(candidate) // 4):
        return False
    return True


def _scan_line(line: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for match in _SENSITIVE_ASSIGN_RE.finditer(line):
        candidate = match.group(2)
        if _looks_like_secret(candidate):
            findings.append((candidate, "Verdächtige Zuweisung eines potentiellen Secrets"))
    for match in _BEARER_RE.finditer(line):
        candidate = match.group(1)
        if _looks_like_secret(candidate):
            findings.append((candidate, "Bearer-Token wirkt echt"))
    if "=" in line or ":" in line:
        for match in _HIGH_ENTROPY_RE.finditer(line):
            candidate = match.group(0)
            if _looks_like_secret(candidate):
                findings.append((candidate, "Hochentropischer Token-String"))
    return findings


def _should_ignore(path: Path, patterns: Sequence[str], base_dir: Path) -> bool:
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        return False
    return any(relative.match(pattern) for pattern in patterns)


def scan_repository(
    base_dir: Path,
    *,
    paths: Iterable[Path] | None = None,
    ignore_patterns: Sequence[str] | None = None,
) -> list[Finding]:
    ignore_patterns = tuple(ignore_patterns or ())
    if paths is not None:
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(p for p in path.rglob("*") if p.is_file())
            else:
                files.append(path)
    else:
        files = _tracked_files(base_dir)
    findings: list[Finding] = []
    for file_path in files:
        if not file_path.exists() or not file_path.is_file():
            continue
        if _should_ignore(file_path, ignore_patterns, base_dir):
            continue
        if _is_binary(file_path):
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            for snippet, reason in _scan_line(line):
                truncated = snippet if len(snippet) <= 80 else f"{snippet[:37]}…{snippet[-38:]}"
                findings.append(
                    Finding(
                        path=file_path,
                        line_number=lineno,
                        match=truncated,
                        reason=reason,
                    )
                )
    return findings
