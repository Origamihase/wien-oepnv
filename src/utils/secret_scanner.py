"""Utility helpers to detect accidentally committed secrets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
import re
import subprocess  # nosec B404

__all__ = [
    "Finding",
    "scan_repository",
    "load_ignore_file",
]

_HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9])")

# Detect sensitive variable assignments (e.g. key = "value")
# We use a broad list of keywords and allow common separators (hyphens, dots) in prefixes/suffixes
# to catch variations like my-api-key, config.client_secret, etc.
_SENSITIVE_ASSIGN_RE = re.compile(
    r"""(?xis)
    (
        [a-z0-9_.-]*  # Prefix allowing letters, numbers, underscores, dots, hyphens
        (?:
            token|secret|password|passphrase|credential|
            accessid|accesskey|access-key|access.key|
            apikey|api-key|api.key|
            privatekey|private-key|private.key|
            secret-key|secret.key|client-secret|client.secret|
            authorization|auth-token|auth.token|auth|
            _key|ssh-key|ssh.key|id_rsa|
            clientid|client-id|client.id|client_id|
            session_id|session-id|session.id|
            cookie|signature|bearer|jwt|
            webhook_url|webhook-url|webhook.url|webhook|
            dsn|subscriptionkey|
            glpat|ghp
        )
        [a-z0-9_.-]*  # Suffix allowing letters, numbers, underscores, dots, hyphens
    )
    \s*[:=]\s*  # Assignment operator (= or :) surrounded by flexible whitespace (including newlines)
    (
        (?:\"{3}.*?\"{3})|         # Triple-double-quoted value (non-greedy)
        (?:'{3}.*?'{3})|           # Triple-single-quoted value (non-greedy)
        (?:\"(?:\\.|[^\"\\])*\")|  # Double-quoted value
        (?:'(?:\\.|[^'\\])*')|     # Single-quoted value
        [^;#'\"\n]+                # Unquoted value (until comment or newline)
    )
    """
)

_AWS_ID_RE = re.compile(r"(?<![A-Za-z0-9])(AKIA|ASIA|ACCA)[A-Z0-9]{16}(?![A-Za-z0-9])")
_BEARER_RE = re.compile(r"Bearer\s+([A-Za-z0-9\-_.]{16,})")
_PEM_RE = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(?:.|\n)*?(-----END [A-Z ]*PRIVATE KEY-----)")

# Known high-value token patterns to detect specifically
# These bypass the generic entropy checks and provide specific descriptions
_KNOWN_TOKENS = [
    (re.compile(r"(?<![A-Za-z0-9])glpat-[0-9a-zA-Z_\-]{20}(?![A-Za-z0-9])"), "GitLab Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])ghp_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])github_pat_[0-9a-zA-Z_]{22,}(?![A-Za-z0-9])"), "GitHub Fine-Grained Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])sk_live_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Live Secret Key gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}(?![A-Za-z0-9])"), "Slack Bot Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])xoxp-[0-9]{10,}-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{32}(?![A-Za-z0-9])"), "Slack User Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])npm_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "NPM Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])pypi-[0-9a-zA-Z_\-]{20,}(?![A-Za-z0-9])"), "PyPI API Token gefunden"),
]


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
        completed = subprocess.run(  # nosec B603, B607
            ["git", "ls-files", "-z"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
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


def _looks_like_secret(candidate: str, is_assignment: bool = False) -> bool:
    # Allow shorter secrets for explicit assignments (e.g. password="...")
    min_len = 8 if is_assignment else 24
    if len(candidate) < min_len:
        return False
    categories = 0
    categories += any(c.islower() for c in candidate)
    categories += any(c.isupper() for c in candidate)
    categories += any(c.isdigit() for c in candidate)

    # In strict contexts (assignment to sensitive var), allow symbols/spaces as entropy
    if is_assignment:
        categories += any(not c.isalnum() for c in candidate)

    # In strict contexts (assignments), we allow single-category secrets (e.g. all-lowercase)
    # provided they meet the length and entropy requirements.
    min_categories = 1 if is_assignment else 2
    if categories < min_categories:
        return False
    if len(set(candidate)) < max(6, len(candidate) // 4):
        return False
    return True


def _mask_secret(value: str) -> str:
    """Mask a secret value for display (e.g. 'AKIA***1234')."""
    length = len(value)
    if length <= 8:
        return "***"
    if length <= 20:
        return f"{value[:2]}***{value[-2:]}"
    return f"{value[:4]}***{value[-4:]}"


def _scan_content(content: str) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    covered_ranges: list[tuple[int, int]] = []

    # Pre-calculate line offsets for fast lookup
    # Using simple list of newline positions
    newlines = [i for i, char in enumerate(content) if char == "\n"]

    def get_line_number(index: int) -> int:
        from bisect import bisect_left
        # newlines contains indices of newlines.
        # If index is before first newline, it's line 1 (bisect returns 0)
        # If index is after first newline, it's line 2 (bisect returns 1)
        return bisect_left(newlines, index) + 1

    def is_covered(start: int, end: int) -> bool:
        for c_start, c_end in covered_ranges:
            if start < c_end and end > c_start:
                return True
        return False

    for match in _PEM_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if not is_covered(span_start, span_end):
            findings.append((get_line_number(match.start()), candidate, "Private Key (PEM) gefunden"))
            covered_ranges.append((span_start, span_end))

    for regex, reason in _KNOWN_TOKENS:
        for match in regex.finditer(content):
            candidate = match.group(0)
            span_start, span_end = match.span(0)

            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, reason))
                covered_ranges.append((span_start, span_end))

    for match in _AWS_ID_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if not is_covered(span_start, span_end):
            findings.append((get_line_number(match.start()), candidate, "AWS Access Key ID gefunden"))
            covered_ranges.append((span_start, span_end))

    for match in _BEARER_RE.finditer(content):
        candidate = match.group(1)
        span_start, span_end = match.span(1)

        if _looks_like_secret(candidate, is_assignment=True):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Bearer-Token wirkt echt"))
                covered_ranges.append((span_start, span_end))

    for match in _SENSITIVE_ASSIGN_RE.finditer(content):
        candidate = match.group(2).strip()
        # Strip outer quotes if present
        # Handle triple quotes first (check length >= 6 to avoid index errors)
        if candidate.startswith('"""') and candidate.endswith('"""') and len(candidate) >= 6:
            candidate = candidate[3:-3]
        elif candidate.startswith("'''") and candidate.endswith("'''") and len(candidate) >= 6:
            candidate = candidate[3:-3]
        elif (candidate.startswith('"') and candidate.endswith('"')) or (
            candidate.startswith("'") and candidate.endswith("'")
        ):
            candidate = candidate[1:-1]

        # Use the span of the value group (including quotes) for coverage
        span_start, span_end = match.span(2)

        if _looks_like_secret(candidate, is_assignment=True):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Verdächtige Zuweisung eines potentiellen Secrets"))
                covered_ranges.append((span_start, span_end))

    for match in _HIGH_ENTROPY_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if _looks_like_secret(candidate):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Hochentropischer Token-String"))

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

        for lineno, snippet, reason in _scan_content(content):
            # Mask the secret value to prevent leakage in logs/CI
            masked = _mask_secret(snippet)
            findings.append(
                Finding(
                    path=file_path,
                    line_number=lineno,
                    match=masked,
                    reason=reason,
                )
            )
    return findings
