"""Sentinel auto-discoverable invariant: every JSON parser site must
catch ``RecursionError`` (directly or via ``Exception``/``BaseException``).

Round 5 of the JSON depth-bomb drift family
(``.jules/sentinel.md`` 2026-05-08) closed two cron-pipeline parser
sites that the named-list audit had walked past, and the prevention
rule explicitly recommended replacing the closing-grep methodology
with a programmatic walker::

    Replace the audit-completion rule's "verdict cites grep output"
    with "verdict runs a programmatic walker (e.g.
    tests/_audit_json_parser_recursion_coverage.py shape: walk every
    *.py in src/ and scripts/, find every json.loads/json.load/
    response.json() call, find the smallest enclosing try block at
    lower indent, walk forward to find the matching except clauses,
    assert each clause's tuple includes RecursionError, Exception,
    or BaseException). Any future json.loads addition that lacks the
    catch fails the walker, regardless of whether the journal named
    the file.

This file IS that walker, run as a regression test under pytest.

Why the catch matters
---------------------
``json.loads`` raises ``RecursionError`` (a ``RuntimeError → Exception``
subclass — NOT a ``json.JSONDecodeError`` subclass and NOT an
``OSError``) when the input nests deeper than Python's recursion limit
(~1000 levels). A 5000-deep nested-array document is a few KB on the
wire / disk but propagates ``RecursionError`` past every
``except json.JSONDecodeError`` / ``except (OSError, ValueError)``
handler. Round 1-5 of this drift family closed 33 documented sites; the
walker below is the post-Round-5 closing rule, locking in the invariant
for every future sibling parser added in any module.

Coverage rule
-------------
For every ``json.loads(...)`` / ``json.load(...)`` / ``response.json()``
call in ``src/`` or ``scripts/``, the smallest enclosing ``try``/``except``
must have at least one handler whose exception tuple includes
``RecursionError``, ``Exception``, or ``BaseException``. Anything else
(e.g. ``except (OSError, json.JSONDecodeError)`` alone) fails the walker.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_TREES = ("src", "scripts")
RECURSION_TOLERANT_EXCEPTIONS = frozenset(
    {
        # Direct catch.
        "RecursionError",
        # Parents in the exception hierarchy. ``RuntimeError`` covers
        # ``RecursionError`` directly (RecursionError → RuntimeError →
        # Exception → BaseException). ``ValueError`` does NOT.
        "RuntimeError",
        "Exception",
        "BaseException",
    }
)


def _is_json_parser_call(node: ast.Call) -> bool:
    """Identify ``json.loads(...)``, ``json.load(...)``, ``X.json()``."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    # ``json.loads`` / ``json.load`` — qualified by the ``json`` module.
    if func.attr in ("loads", "load"):
        return isinstance(func.value, ast.Name) and func.value.id == "json"
    # ``response.json()`` — argumentless invocation. We accept any
    # receiver since requests.Response, urllib3 responses, and HTTPX
    # responses all expose this name.
    if func.attr == "json" and not node.args and not node.keywords:
        return True
    return False


def _exception_names(handler: ast.ExceptHandler) -> list[str]:
    """Extract every exception class name caught by *handler*."""
    exc_type = handler.type
    if exc_type is None:
        # Bare ``except:`` — catches BaseException, which covers
        # RecursionError. Treat as tolerant.
        return ["BaseException"]
    names: list[str] = []
    candidates: list[ast.expr] = (
        list(exc_type.elts) if isinstance(exc_type, ast.Tuple) else [exc_type]
    )
    for cand in candidates:
        if isinstance(cand, ast.Name):
            names.append(cand.id)
        elif isinstance(cand, ast.Attribute):
            # ``json.JSONDecodeError`` / ``requests.exceptions.JSONDecodeError``.
            names.append(cand.attr)
    return names


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _enclosing_try(
    parents: dict[int, ast.AST], node: ast.AST
) -> ast.Try | None:
    """Walk upward to the smallest enclosing ``ast.Try`` block."""
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.Try):
            return cur
        cur = parents.get(id(cur))
    return None


def _audit_module(path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, reason), ...]`` for every uncovered parser
    site in *path*."""
    findings: list[tuple[int, str]] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings

    parents = _build_parent_map(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_json_parser_call(node):
            continue
        try_node = _enclosing_try(parents, node)
        if try_node is None:
            findings.append(
                (
                    node.lineno,
                    "no enclosing try/except — RecursionError propagates "
                    "out and crashes the caller",
                )
            )
            continue

        covers_recursion = False
        for handler in try_node.handlers:
            if RECURSION_TOLERANT_EXCEPTIONS.intersection(
                _exception_names(handler)
            ):
                covers_recursion = True
                break
        if not covers_recursion:
            findings.append(
                (
                    node.lineno,
                    "enclosing except clause lacks RecursionError, "
                    "Exception, or BaseException — JSON depth-bomb "
                    "would propagate past the handler",
                )
            )
    return findings


def _all_python_files() -> list[Path]:
    files: list[Path] = []
    for tree_name in SCAN_TREES:
        tree_root = REPO_ROOT / tree_name
        if not tree_root.exists():
            continue
        files.extend(sorted(tree_root.rglob("*.py")))
    return files


def test_every_json_parser_site_catches_recursion_error() -> None:
    """The audit-completion invariant from Round 5: zero uncovered sites."""
    all_findings: list[tuple[Path, int, str]] = []
    for path in _all_python_files():
        for lineno, reason in _audit_module(path):
            all_findings.append((path, lineno, reason))

    if not all_findings:
        return
    # Build a readable failure report listing every uncovered site.
    rendered = "\n".join(
        f"  {p.relative_to(REPO_ROOT)}:{lineno}: {reason}"
        for p, lineno, reason in all_findings
    )
    pytest.fail(
        f"{len(all_findings)} JSON parser site(s) without "
        f"RecursionError-tolerant exception coverage:\n{rendered}\n\n"
        "Each site must have an enclosing try/except whose tuple "
        "includes RecursionError, Exception, or BaseException. "
        "See .jules/sentinel.md (JSON Depth-Bomb Drift Round 5) for "
        "the full closing rule."
    )


def test_walker_recognises_json_parser_calls() -> None:
    """Smoke test: the walker must actually identify the canonical
    parser shapes. Pin the precondition that the walker's pattern
    matches the three shapes listed in the journal."""
    sample = """\
import json
import requests

def f1(content):
    try:
        return json.loads(content)
    except (json.JSONDecodeError, RecursionError):
        return None

def f2(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None

def f3(session, url):
    try:
        response = session.get(url)
        return response.json()
    except (ValueError, RecursionError):
        return None
"""
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    parser_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node)
    ]
    assert len(parser_calls) == 3, (
        "Walker must recognise json.loads, json.load, and .json() shapes"
    )
    # All three must be covered.
    for call in parser_calls:
        try_node = _enclosing_try(parents, call)
        assert try_node is not None
        assert any(
            RECURSION_TOLERANT_EXCEPTIONS.intersection(_exception_names(h))
            for h in try_node.handlers
        )


def test_walker_flags_uncovered_call() -> None:
    """Smoke test: the walker correctly rejects a parser site whose
    enclosing except lacks RecursionError/Exception/BaseException."""
    sample = """\
import json

def f(content):
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None
"""
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_json_parser_call(node):
            try_node = _enclosing_try(parents, node)
            assert try_node is not None
            covers = any(
                RECURSION_TOLERANT_EXCEPTIONS.intersection(_exception_names(h))
                for h in try_node.handlers
            )
            assert not covers, (
                "Walker must flag sites whose only handler is "
                "json.JSONDecodeError"
            )
            return
    pytest.fail("Walker did not visit the json.loads call")


def test_walker_flags_call_without_try_block() -> None:
    """Smoke test: a bare ``json.loads`` with no enclosing try is
    flagged — it's the worst case (RecursionError propagates straight
    to the caller)."""
    sample = """\
import json

def f(raw):
    return json.loads(raw)
"""
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_json_parser_call(node):
            assert _enclosing_try(parents, node) is None
            return
    pytest.fail("Walker did not visit the json.loads call")
