"""Sentinel auto-discoverable invariant: every JSON parser site in
``src/`` and ``scripts/`` must pin BOTH the ``parse_constant`` and
``parse_float`` defence hooks (or route through a canonical wrapper
that bakes them in).

Why a walker matters
--------------------
The 2026-05-23 GeoNetz / i18n size-bomb rounds (PR #1629, PR #1630)
explicitly journaled the closing rule for this fix family::

    Future canonical-loader rounds should ship the walker alongside
    the per-site fix so every parser-site axis (RecursionError +
    size-cap + non-finite-literal + Trojan-Source scrub) is
    programmatically enforced from the start.

The 2026-05-08 round canonicalised :func:`src.utils.files.loads_finite`
/ :func:`read_capped_json` (which bake the
``parse_constant=_reject_non_finite_constant`` +
``parse_float=_reject_non_finite_float`` hooks into a single point of
audit) and shipped a programmatic walker for the **RecursionError**
axis at ``tests/test_sentinel_json_audit_walker.py``. The 2026-05-23
round shipped the **size-cap** axis walker at
``tests/test_sentinel_size_cap_audit_walker.py``. The
**non-finite-literal** axis was the missing third leg — every
committed reader pins the hooks today (per-callsite source-grep is
done in ``tests/test_sentinel_committed_reader_non_finite_drift.py``),
but a future contributor adding a fresh ``json.loads(content)`` /
``response.json()`` callsite would silently regress.

This file IS the non-finite-literal walker, run as a regression test
under pytest. Any future contributor who adds a bare
``json.loads(...)`` / ``json.load(...)`` / ``response.json()`` call to
``src/`` or ``scripts/`` will fail this test at PR-review time,
regardless of whether the journal named the file.

Coverage rule
-------------
For every ``json.loads(...)`` / ``json.load(...)`` /
``<json-alias>.loads(...)`` / ``<json-alias>.load(...)`` /
``<receiver>.json(...)`` call in ``src/`` or ``scripts/``, the call
MUST carry both ``parse_constant=...`` and ``parse_float=...``
keyword arguments. Routing through :func:`src.utils.files.loads_finite`
/ :func:`read_capped_json` is the sanctioned wrapper shape; the
underlying ``json.loads`` calls inside those wrappers carry the hooks
themselves and are picked up by the walker as protected.

A ``**kwargs`` spread is treated as protected because static AST
analysis cannot determine the dict's keys — the walker assumes good
faith on dynamic kwarg spread (rare in this codebase). Per-callsite
source-grep tests (e.g. ``test_sentinel_committed_reader_non_finite_drift.py``)
still pin the canonical hook names on the named readers.

Why both hooks matter (defence-in-depth)
----------------------------------------
``parse_constant`` covers the three literal tokens ``NaN`` /
``Infinity`` / ``-Infinity`` (lenient-mode acceptance per Python's
:mod:`json` defaults, invalid per RFC 8259 §6). ``parse_float`` covers
the scientific-notation-overflow bypass: a syntactically-valid JSON
number like ``"1e1000"`` is NOT one of the three constants, so
``parse_constant`` is NOT invoked — instead the default
``parse_float=float`` hook IEEE-754-overflows to ``float('inf')``
silently. Without BOTH hooks the defence is partial. See
``tests/test_sentinel_committed_reader_non_finite_drift.py`` for the
full threat model.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_TREES = ("src", "scripts")

# Allowlist of ``(relative_path, lineno)`` pairs that the walker should
# tolerate. EMPTY by design — every legitimate JSON parser callsite
# pins the hooks today (post 2026-05-14 / 2026-05-15 closing rounds).
# Any future special-case (e.g. a callsite that intentionally must
# accept the lenient default for round-trip-compat with an external
# tool) MUST be added here with a justification comment in the same PR
# that introduces the call.
ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


def _collect_json_module_aliases(tree: ast.AST) -> set[str]:
    """Return every local name that aliases the :mod:`json` stdlib module.

    Mirrors the alias-resolution logic in
    ``tests/test_sentinel_json_audit_walker.py``: always includes
    ``"json"`` as the canonical name and extends with every
    ``import json as <alias>`` binding (e.g.
    ``import json as _json_lib`` in
    ``scripts/update_stammstrecke_status.py``). Without this extension
    the walker silently skips any aliased import and lets a future
    contributor re-introduce drift undetected.
    """
    aliases: set[str] = {"json"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "json":
                    aliases.add(name.asname or name.name)
    return aliases


def _is_json_parser_call(
    node: ast.Call, json_aliases: set[str]
) -> bool:
    """Identify ``<json-alias>.loads(...)``, ``<json-alias>.load(...)``,
    or ``<X>.json(...)`` parser calls.

    Differs from the RecursionError walker's predicate
    (``test_sentinel_json_audit_walker.py``) on the ``.json()`` shape:
    here we accept ``.json(...)`` regardless of whether kwargs are
    present (the caller will inspect kwargs separately), because the
    walker MUST also visit ``response.json(parse_constant=..., parse_float=...)``
    callsites to confirm the hooks are in place. Argumentless
    ``response.json()`` would be a violation under this walker — the
    default lenient ``parse_float=float`` accepts ``1e1000`` overflow.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    # ``<json-alias>.loads`` / ``<json-alias>.load``: match any local
    # name that resolves to the :mod:`json` stdlib module.
    if func.attr in ("loads", "load"):
        return isinstance(func.value, ast.Name) and func.value.id in json_aliases
    # ``<receiver>.json(...)``: requests.Response, urllib3 responses,
    # and HTTPX responses all expose this name. We accept any receiver.
    if func.attr == "json":
        return True
    return False


def _has_finite_hooks(node: ast.Call) -> bool:
    """Return ``True`` iff *node* pins both ``parse_constant`` AND
    ``parse_float`` keyword arguments, OR uses a ``**kwargs`` spread.

    A ``**kwargs`` spread (``ast.keyword.arg is None``) is treated as
    protected because static AST inspection cannot determine the
    dict's keys. This is rare in production code; the named-list
    source-grep pins in
    ``tests/test_sentinel_committed_reader_non_finite_drift.py``
    enforce the canonical hook NAMES on every documented reader.
    """
    has_parse_constant = False
    has_parse_float = False
    for kw in node.keywords:
        if kw.arg is None:
            # ``**kwargs`` spread — assume good faith, walker cannot
            # statically verify dict contents.
            return True
        if kw.arg == "parse_constant":
            has_parse_constant = True
        elif kw.arg == "parse_float":
            has_parse_float = True
    return has_parse_constant and has_parse_float


def _audit_module(path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, reason), ...]`` for every unprotected JSON
    parser site in *path*."""
    findings: list[tuple[int, str]] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings

    json_aliases = _collect_json_module_aliases(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_json_parser_call(
            node, json_aliases
        ):
            continue
        if _has_finite_hooks(node):
            continue
        # Render a short shape hint to make the failure diagnostic
        # operator-actionable — name the missing kwargs explicitly.
        missing_pc = not any(
            kw.arg == "parse_constant" for kw in node.keywords if kw.arg
        )
        missing_pf = not any(
            kw.arg == "parse_float" for kw in node.keywords if kw.arg
        )
        missing: list[str] = []
        if missing_pc:
            missing.append("parse_constant=_reject_non_finite_constant")
        if missing_pf:
            missing.append("parse_float=_reject_non_finite_float")
        findings.append((node.lineno, ", ".join(missing)))
    return findings


def _all_python_files() -> list[Path]:
    files: list[Path] = []
    for tree_name in SCAN_TREES:
        tree_root = REPO_ROOT / tree_name
        if not tree_root.exists():
            continue
        files.extend(sorted(tree_root.rglob("*.py")))
    return files


def test_every_json_parser_site_pins_non_finite_hooks() -> None:
    """The audit-completion invariant: zero unprotected JSON parser
    sites in ``src/`` or ``scripts/``.

    Every ``json.loads(...)`` / ``json.load(...)`` /
    ``response.json(...)`` MUST carry both
    ``parse_constant=_reject_non_finite_constant`` and
    ``parse_float=_reject_non_finite_float`` (or use ``**kwargs``
    spread). The canonical wrappers
    :func:`src.utils.files.loads_finite` and :func:`read_capped_json`
    bake the hooks into a single point of audit; callers routing
    through them inherit the defence automatically.
    """
    all_findings: list[tuple[Path, int, str]] = []
    for path in _all_python_files():
        rel = path.relative_to(REPO_ROOT)
        for lineno, missing in _audit_module(path):
            key = (str(rel), lineno)
            if key in ALLOWLIST:
                continue
            all_findings.append((rel, lineno, missing))

    if not all_findings:
        return
    rendered = "\n".join(
        f"  {p}:{lineno}: missing {missing}"
        for p, lineno, missing in all_findings
    )
    pytest.fail(
        f"{len(all_findings)} JSON parser site(s) missing the non-finite "
        f"literal defence hooks:\n{rendered}\n\n"
        "Each site must pin both ``parse_constant=_reject_non_finite_constant`` "
        "AND ``parse_float=_reject_non_finite_float`` so a planted NaN / "
        "Infinity / 1e1000 literal cannot propagate as ``float('nan')`` / "
        "``float('inf')`` into Python computation. Route through "
        "``src.utils.files.loads_finite`` / ``read_capped_json`` (canonical "
        "wrappers that bake in both hooks) or add the kwargs directly. See "
        "the Sentinel Non-Finite Literal Drift Round (2026-05-14 / "
        "2026-05-15 — committed-state-file readers) audit and "
        "``tests/test_sentinel_committed_reader_non_finite_drift.py`` for "
        "the canonical fix shape."
    )


# ============================================================================
# Smoke tests pinning the walker's pattern-recognition contract
# ============================================================================


def test_walker_flags_bare_json_loads() -> None:
    """A bare ``json.loads(content)`` with no kwargs is a violation."""
    sample = "import json\n" + "def f(c):\n" + "    return json.loads(c)\n"
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1, "Walker must visit the json.loads call"
    assert not _has_finite_hooks(matches[0]), (
        "Walker must flag bare json.loads(c) — both hooks missing"
    )


def test_walker_accepts_json_loads_with_both_hooks() -> None:
    """``json.loads(c, parse_constant=..., parse_float=...)`` is the
    sanctioned shape and must NOT be flagged."""
    sample = (
        "import json\n"
        "def f(c):\n"
        "    return json.loads(\n"
        "        c,\n"
        "        parse_constant=_reject_non_finite_constant,\n"
        "        parse_float=_reject_non_finite_float,\n"
        "    )\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_finite_hooks(matches[0]), (
        "Walker must accept json.loads with both parse_constant + parse_float"
    )


def test_walker_flags_json_loads_missing_parse_constant() -> None:
    """``json.loads(c, parse_float=...)`` without parse_constant is a
    violation — NaN / Infinity / -Infinity tokens leak through."""
    sample = (
        "import json\n"
        "def f(c):\n"
        "    return json.loads(c, parse_float=_reject_non_finite_float)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_finite_hooks(matches[0]), (
        "Walker must flag parse_float-only — NaN tokens bypass without "
        "parse_constant"
    )


def test_walker_flags_json_loads_missing_parse_float() -> None:
    """``json.loads(c, parse_constant=...)`` without parse_float is a
    violation — the 1e1000 scientific-notation overflow leaks through."""
    sample = (
        "import json\n"
        "def f(c):\n"
        "    return json.loads(c, parse_constant=_reject_non_finite_constant)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_finite_hooks(matches[0]), (
        "Walker must flag parse_constant-only — 1e1000 overflow bypasses "
        "without parse_float"
    )


def test_walker_flags_argumentless_response_json() -> None:
    """A bare ``response.json()`` is a violation — the request library's
    default ``json.loads`` accepts NaN / Infinity / 1e1000."""
    sample = (
        "def f(response):\n"
        "    return response.json()\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_finite_hooks(matches[0]), (
        "Walker must flag argumentless response.json() — hooks missing"
    )


def test_walker_accepts_response_json_with_both_hooks() -> None:
    """``response.json(parse_constant=..., parse_float=...)`` is
    the sanctioned shape for HTTP response parsing."""
    sample = (
        "def f(response):\n"
        "    return response.json(\n"
        "        parse_constant=_reject_non_finite_constant,\n"
        "        parse_float=_reject_non_finite_float,\n"
        "    )\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_finite_hooks(matches[0]), (
        "Walker must accept response.json() with both hooks"
    )


def test_walker_recognises_aliased_json_module() -> None:
    """``import json as _json_lib; _json_lib.loads(c)`` — the exact
    shape used at ``scripts/update_stammstrecke_status.py:113`` — must
    be flagged when both hooks are missing.

    Mirrors the alias-resolution invariant pinned in
    ``test_sentinel_json_audit_walker.py::test_walker_recognises_aliased_json_module``.
    """
    sample = (
        "import json as _json_lib\n"
        "def f(c):\n"
        "    return _json_lib.loads(c)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)

    # Alias set must include both canonical and aliased names.
    assert "_json_lib" in json_aliases
    assert "json" in json_aliases

    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1, (
        "Walker must resolve the aliased ``_json_lib.loads(c)`` shape"
    )
    assert not _has_finite_hooks(matches[0])


def test_walker_accepts_kwargs_spread() -> None:
    """``json.loads(c, **kwargs)`` — static AST analysis cannot
    determine the dict's keys, so the walker tolerates the spread.

    Named-list source-grep pins in
    ``tests/test_sentinel_committed_reader_non_finite_drift.py``
    enforce the canonical hook names on every documented reader so a
    dynamic-spread bypass is caught at a different layer.
    """
    sample = (
        "import json\n"
        "def f(c, kwargs):\n"
        "    return json.loads(c, **kwargs)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_finite_hooks(matches[0]), (
        "Walker must tolerate **kwargs spread — static analysis cannot "
        "introspect the dict contents"
    )


def test_walker_ignores_unrelated_calls() -> None:
    """Sanity check: unrelated method calls (``.text``, ``.upper()``,
    ``.get()``) and unrelated module functions (``json.dumps``) must
    not produce false positives.
    """
    sample = (
        "import json\n"
        "def f(payload, response):\n"
        "    a = json.dumps(payload)\n"
        "    b = response.text\n"
        "    c = response.status_code\n"
        "    d = 'hello'.upper()\n"
        "    return a, b, c, d\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert matches == [], (
        "Walker must NOT produce false positives on json.dumps / "
        ".text / unrelated method calls"
    )


def test_walker_ignores_handle_read_calls() -> None:
    """Sanity check: file-handle ``handle.read()`` calls (the
    canonical safe shape used inside ``read_capped_json`` /
    ``read_capped_text``) must not match the JSON parser predicate."""
    sample = (
        "def f(handle):\n"
        "    return handle.read(1024)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_parser_call(node, json_aliases)
    ]
    assert matches == []


def test_collect_json_module_aliases_includes_canonical_and_aliased() -> None:
    """Pin the alias-collection contract: the canonical ``json`` name
    is always present; every ``import json as <name>`` binding adds a
    new alias; non-json imports do not pollute the set."""
    sample = (
        "import json\n"
        "import json as _json_lib\n"
        "import json as another_alias\n"
        "import os\n"
        "import requests\n"
    )
    tree = ast.parse(sample)
    aliases = _collect_json_module_aliases(tree)
    assert aliases == {"json", "_json_lib", "another_alias"}


def test_allowlist_is_empty_today() -> None:
    """The ALLOWLIST is empty by design — every legitimate JSON parser
    site pins both hooks today (post 2026-05-14 / 2026-05-15 closing
    rounds). If a future PR needs to add an entry, this test pins the
    requirement that the addition is intentional (not accidental) by
    failing if the allowlist drifts. Update this assertion alongside
    any legitimate allowlist addition."""
    assert ALLOWLIST == frozenset(), (
        "ALLOWLIST drifted from empty — any new entry must come with "
        "a justification comment AND an update to this assertion."
    )
