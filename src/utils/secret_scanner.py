"""Utility helpers to detect accidentally committed secrets."""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Iterable, Sequence
import subprocess  # nosec B404

from .files import read_capped_text

from .secret_patterns.detectors import _BEARER_RE as _BEARER_RE
from .secret_patterns.detectors import _BASIC_AUTH_RE as _BASIC_AUTH_RE
from .secret_patterns.detectors import _NEGOTIATE_RE as _NEGOTIATE_RE
from .secret_patterns.detectors import _NTLM_RE as _NTLM_RE
from .secret_patterns.detectors import _TOKEN_SCHEME_RE as _TOKEN_SCHEME_RE
from .secret_patterns.detectors import _HOBA_RE as _HOBA_RE
from .secret_patterns.tokens_vcs_payment_ai import KNOWN_TOKENS_VCS_PAYMENT_AI
from .secret_patterns.tokens_devtools_saas import KNOWN_TOKENS_DEVTOOLS_SAAS
from .secret_patterns.tokens_gitlab_commerce import KNOWN_TOKENS_GITLAB_COMMERCE
from .secret_patterns.tokens_cloud_platform import KNOWN_TOKENS_CLOUD_PLATFORM

__all__ = [
    "Finding",
    "scan_repository",
    "load_ignore_file",
    "MAX_IGNORE_FILE_BYTES",
    "MAX_SCAN_FILE_BYTES",
    "_HIGH_ENTROPY_RE",
    "_SENSITIVE_ASSIGN_RE",
    "_AWS_ID_RE",
    "_BEARER_RE",
    "_BASIC_AUTH_RE",
    "_NEGOTIATE_RE",
    "_NTLM_RE",
    "_TOKEN_SCHEME_RE",
    "_HOBA_RE",
    "_AUTH_SCHEME_DETECTORS",
    "_PEM_RE",
]

# Security: per-loader byte caps for the two on-disk parsers in this
# module. Pre-fix both sites used ``Path.read_text(encoding="utf-8",
# errors="ignore")`` with NO size cap whatsoever — a planted huge file
# at the ignore-file path or any tracked file in the repo raised
# ``MemoryError`` past the surrounding handler and crashed the secret
# scanner CI gate, bypassing detection on subsequent commits.
#   - ``.secret-scan-ignore`` is a small list of glob patterns,
#     typically a few KiB; 1 MiB is ~1000x legit.
#   - Per-file scan content must accommodate large checked-in data
#     files (HTML test fixtures, mapping JSONs); 50 MiB matches the
#     ``DEFAULT_MAX_TEXT_FILE_BYTES`` ceiling for non-JSON disk reads
#     while still rejecting GiB-sized planted attacks.
MAX_IGNORE_FILE_BYTES = 1 * 1024 * 1024
MAX_SCAN_FILE_BYTES = 50 * 1024 * 1024

log = logging.getLogger(__name__)

_HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9])")

# Detect sensitive variable assignments (e.g. key = "value")
# We use a broad list of keywords and allow common separators (hyphens, dots) in prefixes/suffixes
# to catch variations like my-api-key, config.client_secret, etc.
#
# Security (ReDoS): every key-affix repetition below is BOUNDED ``{0,64}``
# rather than the prior unbounded ``*``. The unbounded affixes caused
# catastrophic O(n²) backtracking — a hostile committed line holding the
# literal ``token`` followed by a long run of ``[a-z0-9_.-]`` chars with no
# trailing ``[:=]`` (a long base64-ish blob, an npm ``integrity`` hash list,
# a Tailscale ACL, …) made the greedy affix consume the whole run, the
# required ``\s*[:=]`` fail, and the engine backtrack one char at a time at
# every start position (~6 s for 4 KB, ~36 s for 10 KB), stalling the CI
# secret gate so sibling files never got scanned. ``re.sub``/``finditer``
# still advance the start position across the whole input, so a real key
# preceded by a long prefix is still found — only the *per-position*
# look-ahead is capped. 64 is far longer than any realistic key
# identifier, so the bound is lossless for genuine secrets.
_SENSITIVE_ASSIGN_RE = re.compile(
    r"""(?xis)
    (
        # Group 1: The key
        (?:
            # ReDoS guard: anchor the match at a [a-z0-9_.-] run boundary. The
            # bounded {0,64} prefix below was still retried at EVERY position
            # inside a long run (each retry attempting the ~46-branch keyword
            # alternation) -> O(N*64*alts), ~33 s/MB on a hostile committed
            # line (base64 blob / minified bundle / lockfile). With this
            # lookbehind, positions inside a run fail in O(1) and the prefix is
            # only attempted at run starts -> linear. Realistic key names start
            # at a boundary and are << 64 chars, so this is lossless for them.
            (?<![a-z0-9_.-])
            [a-z0-9_.-]{0,64}  # Prefix allowing letters, numbers, underscores, dots, hyphens
            (?:
                token|secret|password|passphrase|credential|
                accessid|accesskey|access-key|access.key|
                apikey|api-key|api.key|
                privatekey|private-key|private.key|
                secret-key|secret.key|client-secret|client.secret|
                authorization|auth-token|auth.token|auth|
                _key|ssh-key|ssh.key|id_rsa|
                clientid|client-id|client.id|client_id|
                session_id|session-id|session.id|sessionid|
                cookie|signature|bearer|jwt|
                webhook_url|webhook-url|webhook.url|webhook|
                dsn|subscriptionkey
            )
            [a-z0-9_.-]{0,64}  # Suffix allowing letters, numbers, underscores, dots, hyphens
        )
        |
        (?:
            # Strict matching for short/risky keywords to avoid false positives (e.g. throughput)
            (?<![a-z0-9_.-])  # ReDoS guard: see the boundary anchor above.
            [a-z0-9_.-]{0,64}  # Prefix
            (?:
                glpat|ghp|otp
            )
            (?:[-_][a-z0-9_.-]{0,64})?  # Strict suffix (underscore/hyphen required or end)
        )
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

# Security: ordered (regex, reason) table that ``_scan_content`` iterates
# over to detect HTTP-auth-scheme-prefixed credentials. Each entry's regex
# must capture the credential body in group(1) so the loop in
# ``_scan_content`` can read ``match.group(1)`` uniformly. Order matters
# for tie-breaking: more-specific auth-scheme literals (Bearer, Basic,
# Negotiate, NTLM, HOBA) MUST appear BEFORE the generic ``token`` scheme
# so their attribution wins via ``covered_ranges`` arbitration when a
# token-shaped body sits inside one of those headers. The
# ``is_covered`` check in ``_scan_content`` anchors the first matching
# reason at each span. New auth-scheme detectors (e.g. RFC 7616
# Digest, RFC 8120 Mutual, AWS SigV4 ``AWS4-HMAC-SHA256`` — a vendor
# scheme with no IETF RFC) follow the same shape and inherit the
# ``(?i)`` case-insensitivity invariant pinned per RFC 7235 §2.1.
_AUTH_SCHEME_DETECTORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_BEARER_RE, "Bearer-Token wirkt echt"),
    (_BASIC_AUTH_RE, "HTTP Basic Authentication Credential gefunden"),
    (_NEGOTIATE_RE, "SPNEGO/Negotiate Authentication Token gefunden"),
    (_NTLM_RE, "NTLM Authentication Credential gefunden"),
    (_HOBA_RE, "HOBA Authentication Credential gefunden"),
    (_TOKEN_SCHEME_RE, "HTTP Token-Scheme Authentication Credential gefunden"),
)

# Security: the inter-anchor gap is bounded (``{0,8192}?`` — well above any
# real PEM body, an RSA-4096 key is ~3.2 KiB) instead of the unbounded
# ``(?:.|\n)*?``. An unbounded lazy quantifier makes ``finditer`` restart at
# every ``BEGIN`` marker and scan to EOF when no ``END`` follows, which is
# O(markers x filesize) — a crafted file (hostile-PR threat model) could hang
# the secret-scan gate. The bound caps per-start-position work to a constant.
_PEM_RE = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(?:.|\n){0,8192}?(-----END [A-Z ]*PRIVATE KEY-----)")

# Known high-value token patterns to detect specifically
# These bypass the generic entropy checks and provide specific descriptions
_KNOWN_TOKENS = (
    KNOWN_TOKENS_VCS_PAYMENT_AI
    + KNOWN_TOKENS_DEVTOOLS_SAAS
    + KNOWN_TOKENS_GITLAB_COMMERCE
    + KNOWN_TOKENS_CLOUD_PLATFORM
)


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
    # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap so
    # a planted huge ``.secret-scan-ignore`` cannot exhaust memory and
    # crash the CI gate before secrets are detected on the rest of the
    # repo. ``errors="ignore"`` preserves the legacy lossy-decode
    # contract for non-UTF-8 fragments.
    content = read_capped_text(
        path,
        MAX_IGNORE_FILE_BYTES,
        errors="ignore",
        label="secret-scan-ignore",
        logger=log,
    )
    if content is None:
        return []
    patterns: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _tracked_files(base_dir: Path) -> list[Path]:
    try:
        # Bandit B603/B607: ``git ls-files`` runs on a trusted local path,
        # command list is fully static (no user input).
        completed = subprocess.run(  # nosec B603, B607
            ["git", "ls-files", "-z"],
            cwd=base_dir,
            check=True,
            shell=False,
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
    # Security (long-base64 entropy gap closure): cap the uniqueness
    # requirement at 32 characters. The pre-fix requirement
    # ``max(6, len(candidate) // 4)`` is mathematically unsatisfiable for
    # ANY candidate longer than ~256 chars whose alphabet is the base64
    # ceiling (``[A-Za-z0-9+/=_-]`` — at most 65 unique characters total).
    # Real-world AWS STS Session Tokens (200-700+ char base64 bodies),
    # GCP service-account key fingerprints, long Auth0 / Microsoft Graph
    # OAuth tokens, and any other multi-hundred-char base64 credential
    # were therefore SILENTLY UNDETECTED — neither ``_HIGH_ENTROPY_RE``
    # (entropy fallback) nor ``_SENSITIVE_ASSIGN_RE`` (assignment
    # heuristic) produced a finding because both branches share this
    # uniqueness gate. The 32-char cap preserves the pre-fix accept /
    # reject decision for ALL candidates <= 127 chars (the ``len // 4``
    # branch dominates) while only loosening the gate for longer
    # candidates where the base64 alphabet ceiling makes the original
    # ratio impossible to satisfy. False-positive surface is bounded:
    # ``_HIGH_ENTROPY_RE`` only matches contiguous ``[A-Za-z0-9+/=_-]``
    # spans (natural-language text breaks at punctuation / whitespace,
    # so no long English passage can match), and ``_is_binary`` already
    # skips committed binary files. The remaining surface — base64-
    # encoded data URIs inlined in HTML/Markdown source — is rare in
    # practice and can be allow-listed via ``.secret-scan-ignore`` if
    # it ever matters. The cap value 32 is generous: any realistic
    # high-entropy secret 128+ chars long contains at least 32 distinct
    # characters by simple combinatorial probability.
    if len(set(candidate)) < max(6, min(len(candidate) // 4, 32)):
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


def _scan_auth_scheme_credentials(
    content: str,
    covered_ranges: list[tuple[int, int]],
    line_resolver: Callable[[int], int],
) -> list[tuple[int, str, str]]:
    """Scan *content* for HTTP-auth-scheme-prefixed credential leaks.

    Iterates over :data:`_AUTH_SCHEME_DETECTORS` (Bearer, Basic,
    Negotiate, NTLM, HOBA and the generic Token scheme) and emits one
    finding per match
    that passes ``_looks_like_secret(candidate, is_assignment=True)`` and
    does not overlap an already-covered range. Mutates
    ``covered_ranges`` in place so subsequent detector loops in
    :func:`_scan_content` correctly suppress the same span.

    Extracted from :func:`_scan_content` to keep the latter at its C901
    complexity baseline while still extending coverage to additional
    auth-scheme literals from the HTTP authentication family (RFC 7235
    §2.1 case-insensitive auth-scheme contract). Future detectors (e.g.
    RFC 7616 Digest) extend ``_AUTH_SCHEME_DETECTORS`` without changing
    this helper or the caller.
    """
    findings: list[tuple[int, str, str]] = []
    for regex, reason in _AUTH_SCHEME_DETECTORS:
        for match in regex.finditer(content):
            candidate = match.group(1)
            span_start, span_end = match.span(1)
            if not _looks_like_secret(candidate, is_assignment=True):
                continue
            if any(span_start < ce and span_end > cs for cs, ce in covered_ranges):
                continue
            findings.append((line_resolver(match.start()), candidate, reason))
            covered_ranges.append((span_start, span_end))
    return findings


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

    # Auth-scheme detectors share the same processing shape; the helper
    # :func:`_scan_auth_scheme_credentials` iterates :data:`_AUTH_SCHEME_DETECTORS`
    # and keeps this function at its existing C901 complexity baseline
    # while extending coverage to additional auth-scheme literals from
    # the HTTP authentication family.
    findings.extend(_scan_auth_scheme_credentials(content, covered_ranges, get_line_number))

    for match in _SENSITIVE_ASSIGN_RE.finditer(content):
        candidate = match.group(2).strip()
        # Strip outer quotes if present
        quoted = False
        # Handle triple quotes first (check length >= 6 to avoid index errors)
        if candidate.startswith('"""') and candidate.endswith('"""') and len(candidate) >= 6:
            candidate = candidate[3:-3]
            quoted = True
        elif candidate.startswith("'''") and candidate.endswith("'''") and len(candidate) >= 6:
            candidate = candidate[3:-3]
            quoted = True
        elif (candidate.startswith('"') and candidate.endswith('"')) or (
            candidate.startswith("'") and candidate.endswith("'")
        ):
            candidate = candidate[1:-1]
            quoted = True

        if not quoted:
            # Ignore code-like constructs in unquoted values
            if any(c in candidate for c in "().[]:"):
                continue
            # Ignore common Python keywords to avoid flagging code as secrets
            if candidate.startswith(
                (
                    "return ",
                    "import ",
                    "from ",
                    "class ",
                    "def ",
                    "if ",
                    "else",
                    "elif",
                    "for ",
                    "while ",
                    "try",
                    "except",
                    "with ",
                    "async ",
                    "await ",
                    "raise ",
                )
            ):
                continue
            if candidate in ("None", "True", "False"):
                continue

        # Use the span of the value group (including quotes) for coverage
        span_start, span_end = match.span(2)

        if _looks_like_secret(candidate, is_assignment=True):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Verdächtige Zuweisung eines potentiellen Secrets"))
                covered_ranges.append((span_start, span_end))

    for match in _HIGH_ENTROPY_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if candidate.isalpha():
            # Reduce false positives for LongCamelCaseClassNames
            continue

        if _looks_like_secret(candidate):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Hochentropischer Token-String"))

    return findings


def _should_ignore(path: Path, patterns: Sequence[str], base_dir: Path) -> bool:
    """Apply the operator-supplied ``.secret-scan-ignore`` patterns.

    Pre-fix ``relative.match(pattern)`` used ``pathlib.PurePath.match``,
    whose semantics match only against the last path component for
    pattern-without-``/`` cases — so a single line ``*`` in
    ``.secret-scan-ignore`` matched *every* file regardless of depth
    (``PurePath('a/b/c').match('*') is True``), silently disabling the
    scanner across the whole repo; and a line ``.env`` matched every
    nested ``.env`` (e.g. ``src/config/.env``), masking real plants in
    sub-tree configs. Neither aligns with the gitignore intuition the
    one-line comment at the call site implies.

    Post-fix the match uses ``fnmatch.fnmatchcase`` against the FULL forward-
    slash-normalised relative path AND the basename (``fnmatchcase`` is
    case-sensitive on every platform, unlike ``fnmatch`` which case-folds on
    Windows via ``os.path.normcase``), so:

    * A pattern with ``/`` is anchored against the full path
      (``src/leak.py`` matches only that file).
    * A pattern without ``/`` matches the basename (``*.env`` matches
      every ``.env`` at any depth, in line with operator expectation).
    * A bare ``*`` / ``**`` pattern (only asterisks, optionally wrapped in
      whitespace) is explicitly REFUSED: it would match every basename and
      silently disable the entire scanner — the precise ignore-list footgun
      this matcher guards against. Such a pattern is skipped, never honoured.

    The behaviour is closer to ``gitignore`` than the previous
    ``PurePath.match`` and never silently drops the scanner's coverage.
    """
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        return False
    rel_str = relative.as_posix()
    rel_name = relative.name
    for pattern in patterns:
        # Security: refuse a bare "*"/"**" (only asterisks, possibly wrapped in
        # whitespace). fnmatch("*") matches every basename, so honouring it
        # would silently disable the whole scanner — the ignore-list footgun.
        if not pattern.strip().strip("*"):
            continue
        # Use fnmatchcase, NOT fnmatch: fnmatch routes both the name and the
        # pattern through os.path.normcase, which lowercases on Windows — making
        # the ignore list case-INsensitive there but case-sensitive on Linux,
        # i.e. platform-dependent coverage for a security gate (a Windows-side
        # over-broad match could silently skip a file holding a planted secret).
        # fnmatchcase is case-sensitive on every platform, matching git's
        # default case-sensitive path semantics the docstring describes.
        if "/" in pattern:
            if fnmatch.fnmatchcase(rel_str, pattern):
                return True
        else:
            if fnmatch.fnmatchcase(rel_name, pattern):
                return True
    return False


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
        # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap
        # so a planted huge tracked file (e.g. an intentionally-corrupt
        # data dump) cannot exhaust memory and crash the scanner before
        # planted secrets in sibling files are flagged.
        # ``errors="ignore"`` preserves the legacy lossy-decode contract
        # for non-UTF-8 fragments that aren't filtered by ``_is_binary``.
        content = read_capped_text(
            file_path,
            MAX_SCAN_FILE_BYTES,
            errors="ignore",
            label="scan target",
            logger=log,
        )
        if content is None:
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
