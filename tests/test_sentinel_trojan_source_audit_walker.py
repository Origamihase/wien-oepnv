"""Sentinel auto-discoverable invariant: every JSON writer site in
``src/`` and ``scripts/`` that pins ``ensure_ascii=False`` must call
``scrub_trojan_source_primitives`` in the same function — or be added
to the documented allowlist of legitimate alternative-defence sites.

Why a walker matters
--------------------
The 2026-05-23 GeoNetz / i18n size-bomb rounds (PR #1629, PR #1630)
journaled the closing rule for the canonical-loader fix family::

    Future canonical-loader rounds should ship the walker alongside
    the per-site fix so every parser-site axis (RecursionError +
    size-cap + non-finite-literal + Trojan-Source scrub) is
    programmatically enforced from the start.

The non-finite-literal closing round (2026-05-23, PR #1632) shipped
``tests/test_sentinel_non_finite_literal_audit_walker.py`` and named
the open item::

    With this round all three of the parser-site canonical axes
    (RecursionError + size-cap + non-finite-literal) are now
    programmatically enforced; the Trojan-Source scrub axis remains
    the open closing-rule item for a future round.

This file IS the Trojan-Source scrub walker, run as a regression test
under pytest. Any future contributor who adds a fresh
``json.dump(..., ensure_ascii=False, ...)`` /
``json.dumps(..., ensure_ascii=False, ...)`` call to ``src/`` or
``scripts/`` without a sibling ``scrub_trojan_source_primitives``
call in the same function will fail this test at PR-review time,
regardless of whether the journal named the file.

Coverage rule
-------------
For every ``json.dump(...)`` / ``json.dumps(...)`` /
``<json-alias>.dump(...)`` / ``<json-alias>.dumps(...)`` call in
``src/`` or ``scripts/`` that pins ``ensure_ascii=False`` as an
explicit keyword argument, the smallest enclosing
:class:`ast.FunctionDef` / :class:`ast.AsyncFunctionDef` body
must contain at least one call to
``scrub_trojan_source_primitives(...)``. Module-level writer calls
are checked against the module body.

Why ``ensure_ascii=False`` is the trigger
------------------------------------------
:func:`json.dump` / :func:`json.dumps` default to ``ensure_ascii=True``,
which escapes every non-ASCII code point as a literal ``\\uXXXX``
sequence — and an escaped ``\\u202e`` sequence in the on-disk file
does NOT trigger BiDi reversal in ``cat`` / ``less`` / the GitHub web
UI / IDE preview. The Trojan-Source attack surface only opens when
the writer explicitly chooses ``ensure_ascii=False`` to keep
legitimate German content (umlauts ä/ö/ü/Ä/Ö/Ü + sharp s ß) compact
in the diff. The walker mirrors this asymmetry: a writer that opts
in to compact UTF-8 output MUST also opt in to the canonical
attack-byte scrub. Default-shaped writers (no kwarg, or
``ensure_ascii=True``) are not flagged because the escape pass
neutralises the Trojan-Source primitives at the serialiser.

Allowlist semantics
-------------------
The ``ALLOWLIST`` set lists ``(relative_path, lineno)`` tuples where
``ensure_ascii=False`` is preserved without an in-function
``scrub_trojan_source_primitives`` call because an EQUIVALENT sibling
defence is in place. Three documented cases live here today:

* ``src/places/hafas_client.py:_serialise_payload`` — the function
  builds the HAFAS wire-format request body whose bytes are hashed
  by the MAC signing protocol. The serialised payload is sent to the
  upstream HAFAS endpoint, NOT committed to any
  operator-facing sidecar, and a scrub would change the bytes that
  flow into ``hashlib.md5`` and break the request signature. The
  upstream HAFAS server is not an ``cat`` / ``less`` / GitHub web UI
  viewer — the threat model that motivates the Trojan-Source axis
  does not apply.

* ``src/feed/reporting.py:build_feed_health_payload`` /
  ``write_feed_health_json`` — per-field
  ``_CONTROL_CHARS_RE.sub("", ...)`` calls strip the canonical
  attack-byte union from every user-controlled string field
  (``dedupe_key``, ``titles[]``, ``feed_path``) BEFORE the payload
  reaches ``json.dump``. The remaining fields (provider names /
  statuses, durations, internal counters) are populated by internal
  code with type-checked inputs that cannot carry a Trojan-Source
  primitive. The byte-equivalent regex pinned here is the canonical
  sibling of ``_TROJAN_SOURCE_PRIMITIVES_RE`` (see the
  ``_CONTROL_CHARS_RE`` declaration comment in
  ``src/utils/logging.py``).

* ``src/feed/logging_safe.py:SafeJSONFormatter.format`` — the JSON
  log formatter passes the serialised output through
  ``sanitize_log_message(dumped, strip_control_chars=False)``, which
  ALWAYS strips the canonical attack-byte union via
  ``_INVISIBLE_DANGEROUS_RE.sub("", sanitized)`` regardless of the
  ``strip_control_chars`` flag (see the unconditional always-strip
  comment in ``src/utils/logging.py:sanitize_log_message``). The
  defence runs post-serialisation rather than pre-serialisation, but
  the on-disk / on-wire byte set is byte-equivalent to the
  pre-serialisation scrubber.

Any future writer that legitimately needs to enter the allowlist
MUST add a justification comment in the SAME PR that adds the entry,
and the ``test_allowlist_is_minimal_and_documented`` test pins the
shape so the allowlist cannot silently grow.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_TREES = ("src", "scripts")

# Allowlist of ``(relative_path, lineno)`` pairs that the walker
# tolerates. Each entry pairs a documented sibling defence that
# replaces the in-function ``scrub_trojan_source_primitives`` call.
# See the module docstring above for the rationale narrative on each
# entry. Adding a new entry without an in-PR justification comment
# violates the documented allowlist contract.
ALLOWLIST: frozenset[tuple[str, int]] = frozenset(
    {
        # HAFAS wire-format request body; bytes are hashed by the MAC
        # signing protocol and sent to the upstream HAFAS endpoint,
        # not committed to any operator-facing sidecar.
        ("src/places/hafas_client.py", 289),
        # Feed-health JSON sink; per-field ``_CONTROL_CHARS_RE.sub("",
        # ...)`` calls at lines 726 / 729 / 777 strip the canonical
        # attack-byte union from every user-controlled string field
        # before ``json.dump``.
        ("src/feed/reporting.py", 846),
        # JSON log formatter; ``sanitize_log_message(dumped,
        # strip_control_chars=False)`` always strips the canonical
        # attack-byte union via ``_INVISIBLE_DANGEROUS_RE.sub("",
        # sanitized)`` post-serialisation.
        ("src/feed/logging_safe.py", 247),
        ("src/feed/logging_safe.py", 258),
    }
)


def _collect_json_module_aliases(tree: ast.AST) -> set[str]:
    """Return every local name that aliases the :mod:`json` stdlib module.

    Mirrors the alias-resolution logic in
    ``tests/test_sentinel_json_audit_walker.py`` and
    ``tests/test_sentinel_non_finite_literal_audit_walker.py``:
    always includes ``"json"`` as the canonical name and extends with
    every ``import json as <alias>`` binding. Without this extension
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


def _is_json_writer_call(
    node: ast.Call, json_aliases: set[str]
) -> bool:
    """Identify ``<json-alias>.dump(...)`` and ``<json-alias>.dumps(...)``
    serialiser calls.

    The walker intentionally scopes to the stdlib ``json`` module
    aliases — third-party serialisers (``orjson``, ``ujson``,
    ``rapidjson``) have different ``ensure_ascii`` defaults and
    different threat-model surfaces; covering them would require a
    per-library policy. The codebase does not use any third-party
    JSON serialiser today (see ``requirements.txt``).
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in ("dump", "dumps"):
        return False
    return isinstance(func.value, ast.Name) and func.value.id in json_aliases


def _has_explicit_ensure_ascii_false(node: ast.Call) -> bool:
    """Return ``True`` iff the call explicitly pins
    ``ensure_ascii=False`` as a keyword argument.

    ``json.dump`` / ``json.dumps`` default to ``ensure_ascii=True``,
    which escapes every non-ASCII code point as a literal ``\\uXXXX``
    sequence — neutralising the Trojan-Source primitives at the
    serialiser. The walker only flags the explicit
    ``ensure_ascii=False`` shape because that's the opt-in that
    re-opens the attack surface.

    A ``**kwargs`` spread is treated as NOT-explicit because static
    AST analysis cannot determine the dict's keys. Sites that pass
    the kwarg via dynamic spread are rare in this codebase and would
    be caught by the per-callsite source-grep pins in
    ``tests/test_sentinel_script_station_writers_trojan_source.py``.
    """
    for kw in node.keywords:
        if kw.arg != "ensure_ascii":
            continue
        # ``ensure_ascii=False`` literal — the opt-in shape.
        if isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _enclosing_function(
    parents: dict[int, ast.AST], node: ast.AST
) -> ast.AST | None:
    """Walk upward to the smallest enclosing function definition.

    Returns the function node (``ast.FunctionDef`` or
    ``ast.AsyncFunctionDef``) so the caller can scan its body. If the
    call appears at module level (no enclosing function), returns
    ``None`` and the caller falls back to the module body.
    """
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef):
            return cur
        cur = parents.get(id(cur))
    return None


def _scope_calls_scrub(scope: ast.AST) -> bool:
    """Return ``True`` iff *scope* contains at least one
    ``scrub_trojan_source_primitives(...)`` call.

    The walker accepts both the bare-name form (``scrub_trojan_source_primitives(payload)``)
    and the attribute form (``serialize.scrub_trojan_source_primitives(payload)``)
    so an import-style change does not silently break the audit. The
    canonical callable lives at
    ``src/utils/serialize.py:scrub_trojan_source_primitives``; every
    documented call site uses the bare-name form today.
    """
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "scrub_trojan_source_primitives":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "scrub_trojan_source_primitives":
            return True
    return False


def _audit_module(path: Path, tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``[(lineno, reason), ...]`` for every unprotected JSON
    writer site in *path*. The caller is responsible for filtering
    against ``ALLOWLIST``."""
    findings: list[tuple[int, str]] = []
    parents = _build_parent_map(tree)
    json_aliases = _collect_json_module_aliases(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_json_writer_call(
            node, json_aliases
        ):
            continue
        if not _has_explicit_ensure_ascii_false(node):
            continue
        func_node = _enclosing_function(parents, node)
        scope: ast.AST = func_node if func_node is not None else tree
        if _scope_calls_scrub(scope):
            continue
        scope_name = (
            func_node.name
            if isinstance(func_node, ast.FunctionDef | ast.AsyncFunctionDef)
            else "<module>"
        )
        findings.append(
            (
                node.lineno,
                f"json.dump/dumps(..., ensure_ascii=False, ...) in "
                f"{scope_name!r} without scrub_trojan_source_primitives "
                f"call in the same scope",
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


def test_every_ensure_ascii_false_writer_has_scrub() -> None:
    """The audit-completion invariant for the Trojan-Source axis:
    zero unprotected ``ensure_ascii=False`` writer sites in ``src/``
    or ``scripts/``.

    Every ``json.dump(..., ensure_ascii=False, ...)`` /
    ``json.dumps(..., ensure_ascii=False, ...)`` call MUST be paired
    with an in-function ``scrub_trojan_source_primitives(...)`` call,
    OR be listed in :data:`ALLOWLIST` with a documented sibling
    defence. The canonical scrubber lives at
    :func:`src.utils.serialize.scrub_trojan_source_primitives`.
    """
    all_findings: list[tuple[Path, int, str]] = []
    for path in _all_python_files():
        rel = path.relative_to(REPO_ROOT)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for lineno, reason in _audit_module(path, tree):
            key = (str(rel), lineno)
            if key in ALLOWLIST:
                continue
            all_findings.append((rel, lineno, reason))

    if not all_findings:
        return
    rendered = "\n".join(
        f"  {p}:{lineno}: {reason}"
        for p, lineno, reason in all_findings
    )
    pytest.fail(
        f"{len(all_findings)} ``ensure_ascii=False`` writer site(s) "
        f"without the Trojan-Source scrub:\n{rendered}\n\n"
        "Each site must either call ``scrub_trojan_source_primitives`` "
        "in the same function (canonical fix shape — see "
        "``src/places/merge.py:write_stations`` and the eight named "
        "sinks pinned in "
        "``tests/test_sentinel_script_station_writers_trojan_source.py``), "
        "or be added to ``ALLOWLIST`` with a justification comment in "
        "the same PR. The closing-rule walker realises the 2026-05-23 "
        "non-finite-literal round's named-open-item for the "
        "Trojan-Source scrub axis."
    )


# ============================================================================
# Smoke tests pinning the walker's pattern-recognition contract
# ============================================================================


def test_walker_flags_bare_ensure_ascii_false_dump() -> None:
    """A bare ``json.dump(payload, fh, ensure_ascii=False)`` is a
    violation when the enclosing function has no scrub call."""
    sample = (
        "import json\n"
        "def f(payload, fh):\n"
        "    json.dump(payload, fh, ensure_ascii=False)\n"
    )
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    json_aliases = _collect_json_module_aliases(tree)

    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_explicit_ensure_ascii_false(matches[0])
    func_node = _enclosing_function(parents, matches[0])
    assert func_node is not None
    assert not _scope_calls_scrub(func_node), (
        "Walker must NOT find the scrub call in this synthetic fixture"
    )


def test_walker_accepts_dump_with_scrub_in_function() -> None:
    """``json.dump(payload, fh, ensure_ascii=False)`` IS accepted when
    the enclosing function calls ``scrub_trojan_source_primitives``."""
    sample = (
        "import json\n"
        "from src.utils.serialize import scrub_trojan_source_primitives\n"
        "def f(raw_payload, fh):\n"
        "    payload = scrub_trojan_source_primitives(raw_payload)\n"
        "    json.dump(payload, fh, ensure_ascii=False)\n"
    )
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    json_aliases = _collect_json_module_aliases(tree)

    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_explicit_ensure_ascii_false(matches[0])
    func_node = _enclosing_function(parents, matches[0])
    assert func_node is not None
    assert _scope_calls_scrub(func_node), (
        "Walker must find the scrub call in the synthetic fixture's function body"
    )


def test_walker_accepts_default_ensure_ascii() -> None:
    """A bare ``json.dump(payload, fh)`` (no ``ensure_ascii``) MUST
    not be flagged — the default ``ensure_ascii=True`` already
    escapes the canonical attack-byte union."""
    sample = (
        "import json\n"
        "def f(payload, fh):\n"
        "    json.dump(payload, fh)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_explicit_ensure_ascii_false(matches[0]), (
        "Walker must NOT flag the default-shape writer"
    )


def test_walker_accepts_explicit_ensure_ascii_true() -> None:
    """``json.dump(payload, fh, ensure_ascii=True)`` MUST not be
    flagged — the escape pass neutralises Trojan-Source primitives."""
    sample = (
        "import json\n"
        "def f(payload, fh):\n"
        "    json.dump(payload, fh, ensure_ascii=True)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_explicit_ensure_ascii_false(matches[0]), (
        "Walker must NOT flag ensure_ascii=True"
    )


def test_walker_flags_dumps_with_ensure_ascii_false() -> None:
    """``json.dumps(payload, ensure_ascii=False)`` (the in-memory form)
    is flagged when the enclosing function lacks the scrub call."""
    sample = (
        "import json\n"
        "def f(payload):\n"
        "    return json.dumps(payload, ensure_ascii=False)\n"
    )
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_explicit_ensure_ascii_false(matches[0])
    func_node = _enclosing_function(parents, matches[0])
    assert func_node is not None
    assert not _scope_calls_scrub(func_node)


def test_walker_recognises_aliased_json_module() -> None:
    """``import json as _json_lib; _json_lib.dump(...)`` — the
    aliased-import shape that bypassed the JSON depth-bomb walker
    pre-Round-9 — must also be picked up by THIS walker so an alias
    rename does not silently leak the drift."""
    sample = (
        "import json as _json_lib\n"
        "def f(payload, fh):\n"
        "    _json_lib.dump(payload, fh, ensure_ascii=False)\n"
    )
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    json_aliases = _collect_json_module_aliases(tree)
    assert "_json_lib" in json_aliases
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1, (
        "Walker must resolve the aliased ``_json_lib.dump(...)`` shape"
    )
    assert _has_explicit_ensure_ascii_false(matches[0])
    func_node = _enclosing_function(parents, matches[0])
    assert func_node is not None
    assert not _scope_calls_scrub(func_node)


def test_walker_ignores_json_loads_calls() -> None:
    """Sanity check: ``json.loads`` / ``json.load`` (parser sites)
    must NOT be flagged. The Trojan-Source axis covers writers only;
    the parser-side axes (RecursionError + size-cap + non-finite) live
    in their own walkers."""
    sample = (
        "import json\n"
        "def f(payload):\n"
        "    raw = json.loads(payload)\n"
        "    return raw\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert matches == [], (
        "Walker must NOT pick up json.loads — parser sites have their "
        "own walker(s)."
    )


def test_walker_ignores_unrelated_calls() -> None:
    """Sanity check: unrelated method calls (``.text``, ``.upper()``,
    third-party ``orjson.dumps``) must not produce false positives."""
    sample = (
        "import json\n"
        "def f(payload, response):\n"
        "    a = response.text\n"
        "    b = 'hello'.upper()\n"
        "    c = response.status_code\n"
        "    return a, b, c\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert matches == [], "Walker must NOT produce false positives"


def test_walker_accepts_inline_scrub_call() -> None:
    """An inline scrub call shape — ``json.dump(scrub_trojan_source_primitives(payload),
    fh, ensure_ascii=False)`` — must also be accepted because the
    scrub appears inside the function body (the call is part of the
    AST tree of the function)."""
    sample = (
        "import json\n"
        "from src.utils.serialize import scrub_trojan_source_primitives\n"
        "def f(payload, fh):\n"
        "    json.dump(scrub_trojan_source_primitives(payload), fh, ensure_ascii=False)\n"
    )
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    func_node = _enclosing_function(parents, matches[0])
    assert func_node is not None
    assert _scope_calls_scrub(func_node), (
        "Walker must accept inline ``scrub_trojan_source_primitives(...)`` "
        "inside the json.dump call's positional argument"
    )


def test_walker_accepts_attribute_form_scrub_call() -> None:
    """An attribute-form scrub call — ``serialize.scrub_trojan_source_primitives(payload)``
    — must also be accepted so an import-style change does not break
    the audit."""
    sample = (
        "import json\n"
        "from src.utils import serialize\n"
        "def f(payload, fh):\n"
        "    scrubbed = serialize.scrub_trojan_source_primitives(payload)\n"
        "    json.dump(scrubbed, fh, ensure_ascii=False)\n"
    )
    tree = ast.parse(sample)
    parents = _build_parent_map(tree)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    func_node = _enclosing_function(parents, matches[0])
    assert func_node is not None
    assert _scope_calls_scrub(func_node)


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


def test_allowlist_is_minimal_and_documented() -> None:
    """The ALLOWLIST is intentionally small — three legitimate
    sibling-defence sites today. If a future PR needs to add an
    entry, this test pins the requirement that the addition is
    intentional (not accidental) by failing if the allowlist drifts
    beyond the documented size. Update this assertion alongside any
    legitimate allowlist addition AND the module docstring."""
    assert len(ALLOWLIST) == 4, (
        f"ALLOWLIST drifted from the documented size of 4 entries — "
        f"got {len(ALLOWLIST)}. Any new entry must come with a "
        "justification comment AND an update to this assertion + the "
        "module docstring's allowlist semantics section."
    )
    # Pin the exact entries so a future PR that swaps one allowlisted
    # site for another (without updating the docstring) also fails.
    assert ALLOWLIST == frozenset(
        {
            ("src/places/hafas_client.py", 289),
            ("src/feed/reporting.py", 846),
            ("src/feed/logging_safe.py", 247),
            ("src/feed/logging_safe.py", 258),
        }
    )
