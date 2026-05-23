"""Sentinel auto-discoverable invariant: every file-read site in
``src/`` and ``scripts/`` must route through the canonical
size-capped helpers, never through the unbounded ``Path.read_text()``
/ ``Path.read_bytes()`` shape.

Why a walker matters
--------------------
The 2026-05-23 GeoNetz size-bomb round (PR #1629, closed in the journal
entry that prompted this round) explicitly noted:

    Closing-checklist drift for JSON-loader fix families must
    explicitly walk EVERY ``json.loads``/``json.load``/``response.json()``
    call site in ``src/`` and ``scripts/`` — not just the named files
    in the PR's audit narrative. […] Future canonical-loader rounds
    should ship the walker alongside the per-site fix so every
    parser-site axis (RecursionError + size-cap + non-finite-literal
    + Trojan-Source scrub) is programmatically enforced from the
    start.

The 2026-05-08 round canonicalised :func:`src.utils.files.read_capped_json`
/ :func:`read_capped_text` / :func:`read_capped_bytes` and shipped a
programmatic walker for the **RecursionError** axis at
``tests/test_sentinel_json_audit_walker.py``. The **size-cap** axis was
not similarly pinned — so the i18n-coverage gate at
``scripts/check_i18n_coverage.py`` (closed in this round) and the
GeoNetz loader pair (closed in the previous round) survived multiple
sibling-drift cycles undetected.

This file IS the size-cap walker, run as a regression test under
pytest. Any future contributor who adds a bare ``path.read_text()`` /
``path.read_bytes()`` call to ``src/`` or ``scripts/`` will fail this
test at PR-review time, regardless of whether the journal named the
file.

Coverage rule
-------------
For every ``<expr>.read_text(...)`` and ``<expr>.read_bytes(...)`` call
in ``src/`` or ``scripts/``, the receiver must NOT be a ``Path`` /
file-system path expression. Routing the read through
:func:`src.utils.files.read_capped_text` /
:func:`src.utils.files.read_capped_bytes` (or, where the input is
controlled by the helper itself, a bounded ``handle.read(max_bytes + 1)``
call on an already-open file descriptor) is the only sanctioned shape.

In practice the walker bans the two bare-call shapes outright across
``src/`` and ``scripts/``; the canonical helpers internally use
``handle.read(max_bytes + 1)`` so they are not flagged. Allowlist
entries (none today) would be limited to legitimate fixed-size system
file reads (e.g. ``/proc/self/cmdline``) that are not realistically
attacker-controlled — none of which exist in this codebase today.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_TREES = ("src", "scripts")

# Methods that, when invoked directly on a path-like object, buffer the
# entire file into memory before any defence layer can run. Both shapes
# allocate O(file_size) bytes and propagate :exc:`MemoryError` (a
# :class:`BaseException` subclass) past every ordinary
# ``except OSError`` / ``except ValueError`` / ``except Exception``
# handler, crashing the surrounding pipeline.
BANNED_METHODS = frozenset({"read_text", "read_bytes"})

# Allowlist of (relative_path, lineno) pairs that the walker should
# tolerate. EMPTY by design — every legitimate file read goes through
# :func:`read_capped_text` / :func:`read_capped_bytes`, and any future
# special-case (e.g. a fixed-size system file) MUST be added here with
# a justification comment in the same PR that introduces the call.
ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


def _is_banned_read_call(node: ast.AST) -> str | None:
    """Return the banned method name if *node* is a banned read call,
    otherwise return ``None``. Walks ``<expr>.read_text(...)`` and
    ``<expr>.read_bytes(...)`` shapes; the receiver is intentionally
    not constrained to ``Path`` because the threat model applies
    equally to any path-like object (``pathlib.Path``,
    :func:`pathlib.PurePath` subclasses, third-party path wrappers)."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr in BANNED_METHODS:
        return func.attr
    return None


def _audit_module(path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, banned_method), ...]`` for every uncovered
    read-site in *path*."""
    findings: list[tuple[int, str]] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        banned = _is_banned_read_call(node)
        if banned is None:
            continue
        findings.append((node.lineno, banned))
    return findings


def _all_python_files() -> list[Path]:
    files: list[Path] = []
    for tree_name in SCAN_TREES:
        tree_root = REPO_ROOT / tree_name
        if not tree_root.exists():
            continue
        files.extend(sorted(tree_root.rglob("*.py")))
    return files


def test_every_file_read_uses_capped_helper() -> None:
    """The audit-completion invariant for the size-cap axis: zero
    bare ``Path.read_text()`` / ``Path.read_bytes()`` calls in
    ``src/`` or ``scripts/``."""
    all_findings: list[tuple[Path, int, str]] = []
    for path in _all_python_files():
        rel = path.relative_to(REPO_ROOT)
        for lineno, banned in _audit_module(path):
            key = (str(rel), lineno)
            if key in ALLOWLIST:
                continue
            all_findings.append((rel, lineno, banned))

    if not all_findings:
        return
    rendered = "\n".join(
        f"  {p}:{lineno}: bare .{banned}() — route through "
        f"src.utils.files.read_capped_text / read_capped_bytes "
        f"or add (path, lineno) to ALLOWLIST with justification."
        for p, lineno, banned in all_findings
    )
    pytest.fail(
        f"{len(all_findings)} unbounded file-read site(s) in src/ or "
        f"scripts/:\n{rendered}\n\n"
        "Each site must route through the canonical size-capped "
        "helpers so a planted multi-GiB file cannot allocate "
        "O(file_size) bytes and propagate MemoryError past the "
        "surrounding handler. See the Sentinel Size-Bomb Drift Round "
        "(2026-05-23 — i18n coverage gate) audit for the closing "
        "rule."
    )


# ============================================================================
# Smoke tests pinning the walker's pattern-recognition contract
# ============================================================================


def test_walker_recognises_read_text() -> None:
    """``path.read_text(...)`` is a banned shape — pin recognition."""
    sample = "from pathlib import Path\n" + "p = Path('x')\n" + "p.read_text()\n"
    tree = ast.parse(sample)
    findings = [
        node.lineno
        for node in ast.walk(tree)
        if _is_banned_read_call(node) == "read_text"
    ]
    assert findings == [3], "Walker must flag bare path.read_text() calls"


def test_walker_recognises_read_bytes() -> None:
    """``path.read_bytes(...)`` is a banned shape — pin recognition."""
    sample = "from pathlib import Path\n" + "p = Path('x')\n" + "p.read_bytes()\n"
    tree = ast.parse(sample)
    findings = [
        node.lineno
        for node in ast.walk(tree)
        if _is_banned_read_call(node) == "read_bytes"
    ]
    assert findings == [3], "Walker must flag bare path.read_bytes() calls"


def test_walker_ignores_handle_read_with_bound() -> None:
    """A bounded ``handle.read(n)`` call is the canonical safe shape and
    must NOT be flagged. The walker pattern-matches on the method name
    ``read_text`` / ``read_bytes`` specifically; a generic ``.read(n)``
    is out of scope (callers that route through ``read_capped_text`` use
    ``handle.read(max_bytes + 1)`` internally)."""
    sample = (
        "with open('x', 'rb') as h:\n"
        "    payload = h.read(1024)\n"
    )
    tree = ast.parse(sample)
    findings = [
        node.lineno
        for node in ast.walk(tree)
        if _is_banned_read_call(node) is not None
    ]
    assert findings == [], (
        "Walker must NOT flag bounded handle.read(n) — that's the "
        "canonical safe shape used inside read_capped_text itself."
    )


def test_walker_ignores_unrelated_calls() -> None:
    """Sanity check: unrelated function calls (string methods,
    arithmetic, attribute access) must not produce false positives."""
    sample = (
        "x = 'hello'.upper()\n"
        "y = some_dict.get('k')\n"
        "z = open('x').close()\n"
    )
    tree = ast.parse(sample)
    findings = [
        node.lineno
        for node in ast.walk(tree)
        if _is_banned_read_call(node) is not None
    ]
    assert findings == [], (
        "Walker must NOT produce false positives on unrelated "
        "method calls."
    )


def test_allowlist_is_empty_today() -> None:
    """The ALLOWLIST is empty by design — every legitimate file read
    routes through the canonical capped helpers today. If a future PR
    needs to add an entry, this test pins the requirement that the
    addition is intentional (not accidental) by failing if the
    allowlist drifts. Update this assertion alongside any legitimate
    allowlist addition."""
    assert ALLOWLIST == frozenset(), (
        "ALLOWLIST drifted from empty — any new entry must come with "
        "a justification comment AND an update to this assertion."
    )
