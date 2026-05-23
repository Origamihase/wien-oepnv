"""Sentinel auto-discoverable invariant: every JSON writer site in
``src/`` and ``scripts/`` must pin ``allow_nan=False`` — or be added
to the documented allowlist of legitimate non-disk / signed-payload
sites.

Why a walker matters
--------------------
The 2026-05-23 closing-rule rounds canonicalised programmatic walkers
for THREE of the four parser-site / writer-site canonical defence
axes::

    * RecursionError — ``tests/test_sentinel_json_audit_walker.py``
      (parser-side, since 2026-05-08).
    * Size-cap — ``tests/test_sentinel_size_cap_audit_walker.py``
      (parser-side, since 2026-05-23, i18n-coverage-gate round).
    * Non-finite literal (parser-side) —
      ``tests/test_sentinel_non_finite_literal_audit_walker.py``
      (since 2026-05-23).
    * Trojan-Source scrub (writer-side) —
      ``tests/test_sentinel_trojan_source_audit_walker.py``
      (since 2026-05-23).

The writer-side **non-finite-literal** axis — ``allow_nan=False`` on
every ``json.dump(...)`` / ``json.dumps(...)`` — was the missing
fifth leg. Per-callsite source-grep tests
(``tests/test_sentinel_committed_writer_allow_nan_drift.py``,
``tests/test_sentinel_companion_writer_allow_nan_drift.py``,
``tests/test_sentinel_safe_json_formatter_allow_nan_drift.py``) pinned
the canonical kwarg on the eleven documented disk-writing writers,
but a future contributor adding a fresh ``json.dump(...)`` /
``json.dumps(...)`` callsite would silently regress: Python's default
``json.dump`` accepts ``float('nan')`` / ``float('inf')`` /
``float('-inf')`` and emits the non-standard ``NaN`` / ``Infinity`` /
``-Infinity`` tokens that are invalid per RFC 8259 §6
(``JSON.parse`` in every modern browser rejects them; Rust
``serde_json`` strict mode + Go ``encoding/json`` + every other
strict downstream consumer breaks).

This file IS the writer-side non-finite-literal walker, run as a
regression test under pytest. Any future contributor who adds a
fresh ``json.dump(...)`` / ``json.dumps(...)`` call to ``src/`` or
``scripts/`` without ``allow_nan=False`` will fail this test at
PR-review time, regardless of whether the journal named the file.

Coverage rule
-------------
For every ``json.dump(...)`` / ``json.dumps(...)`` /
``<json-alias>.dump(...)`` / ``<json-alias>.dumps(...)`` call in
``src/`` or ``scripts/``, the call MUST carry an explicit
``allow_nan=False`` keyword argument (or a ``**kwargs`` spread the
walker tolerates as good-faith dynamic forwarding).

Why ``allow_nan=False`` is the trigger
--------------------------------------
:func:`json.dump` / :func:`json.dumps` default to ``allow_nan=True``,
which emits ``NaN`` / ``Infinity`` / ``-Infinity`` as bare literal
tokens. These tokens are INVALID per RFC 8259 §6 (a conforming JSON
text MUST NOT contain them); strict-mode parsers downstream reject
the entire document. The writer-side defence pin therefore must be
EXPLICIT — there is no equivalent of the Trojan-Source axis's
``ensure_ascii=True`` default-shape escape.

The walker pairs with the parser-side hooks at every committed
reader (``loads_finite`` / ``read_capped_json`` →
``parse_constant=_reject_non_finite_constant`` +
``parse_float=_reject_non_finite_float`` per
``tests/test_sentinel_non_finite_literal_audit_walker.py``). Together
the two axes enforce the round-trip invariant: a non-finite literal
cannot enter or leave the on-disk state without surfacing as a loud
``ValueError`` at the producing call.

Allowlist semantics
-------------------
The ``ALLOWLIST`` set lists ``(relative_path, lineno)`` tuples where
``allow_nan=False`` is intentionally NOT pinned because the call is
either (a) transient (the serialised bytes do not reach disk / wire
/ public artefact — only flow into an in-memory hash whose downstream
consumer sees the hex digest, never the JSON literal); or (b) MAC-
signed wire-protocol bytes whose ``allow_nan`` setting would change
the signature input and break the upstream verification step.

Three documented sites live here today:

* ``src/places/hafas_client.py:_serialise_payload`` (line 282) —
  the function builds the HAFAS wire-format request body whose bytes
  are hashed by the Mgate ``mac`` signing protocol. Setting
  ``allow_nan=False`` would not change today's call-graph (HAFAS
  payloads carry strings + integer station IDs, never floats), but
  pinning the kwarg would (a) shift the serialised bytes in the
  future-widening case where a HAFAS-side schema introduces a float
  field, and (b) the request is sent to the upstream endpoint, not
  committed to any operator-facing sidecar — the threat model that
  motivates the non-finite-literal axis (committed-to-main artefact
  that strict parsers must handle) does not apply.

* ``src/build_feed.py:_identity_for_item`` (lines 2317 and 2326) —
  two ``json.dumps(item, sort_keys=True, default=str)`` calls compute
  the SHA-256 input for the feed-item identity hash. The serialised
  bytes flow into ``hashlib.sha256(raw.encode("utf-8")).hexdigest()``
  in the very next line and are not retained anywhere else. The
  hex digest IS visible downstream (in the dedup map and the
  ``_calculated_identity`` cache key on the dict), but the underlying
  JSON literal is not — the threat model (non-standard literal lands
  in a parser-consumed artefact) does not apply because there is no
  parser-consumed artefact at all. Pinning ``allow_nan=False`` here
  would convert a malformed-feed-item bug into a ``ValueError`` that
  crashes the dedup step instead of producing a consistent (if
  unusual) hash for the malformed item — the dedup invariant is
  preserved either way (any two items with the same NaN-bearing
  content produce the same hash text), so the pin's behaviour shift
  is gratuitous for this in-memory-hash callsite.

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
# replaces the explicit ``allow_nan=False`` pin. See the module
# docstring above for the rationale narrative on each entry.
ALLOWLIST: frozenset[tuple[str, int]] = frozenset(
    {
        # HAFAS wire-format request body; bytes are MAC-signed by the
        # Mgate protocol and sent to the upstream HAFAS endpoint, not
        # committed to any operator-facing sidecar. The threat model
        # for the non-finite-literal axis (committed-to-main artefact)
        # does not apply.
        ("src/places/hafas_client.py", 282),
        # Feed-item identity hash compute — the serialised bytes flow
        # into ``hashlib.sha256(...).hexdigest()`` on the very next
        # line and are not retained anywhere else. No parser-consumed
        # artefact, so the threat model does not apply.
        ("src/build_feed.py", 2317),
        ("src/build_feed.py", 2326),
    }
)


def _collect_json_module_aliases(tree: ast.AST) -> set[str]:
    """Return every local name that aliases the :mod:`json` stdlib module.

    Mirrors the alias-resolution logic in the three sibling walkers
    (``tests/test_sentinel_json_audit_walker.py``,
    ``tests/test_sentinel_non_finite_literal_audit_walker.py``,
    ``tests/test_sentinel_trojan_source_audit_walker.py``): always
    includes ``"json"`` as the canonical name and extends with every
    ``import json as <alias>`` binding (e.g.
    ``import json as _json_lib`` in
    ``scripts/update_stammstrecke_status.py``). Without this
    extension the walker silently skips any aliased import and lets
    a future contributor re-introduce drift undetected.
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
    ``rapidjson``) have different ``allow_nan`` defaults and
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


def _has_explicit_allow_nan_false(node: ast.Call) -> bool:
    """Return ``True`` iff the call explicitly pins
    ``allow_nan=False`` as a keyword argument.

    A ``**kwargs`` spread (``ast.keyword.arg is None``) is treated as
    protected because static AST analysis cannot determine the
    dict's keys. Sites that pass the kwarg via dynamic spread are
    rare in this codebase and would be caught by the per-callsite
    source-grep pins in
    ``tests/test_sentinel_committed_writer_allow_nan_drift.py`` and
    ``tests/test_sentinel_companion_writer_allow_nan_drift.py``.
    """
    for kw in node.keywords:
        if kw.arg is None:
            # ``**kwargs`` spread — assume good faith, walker cannot
            # statically verify dict contents.
            return True
        if kw.arg != "allow_nan":
            continue
        if isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _audit_module(path: Path, tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``[(lineno, method), ...]`` for every unprotected JSON
    writer site in *path*. The caller is responsible for filtering
    against ``ALLOWLIST``."""
    findings: list[tuple[int, str]] = []
    json_aliases = _collect_json_module_aliases(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_json_writer_call(
            node, json_aliases
        ):
            continue
        if _has_explicit_allow_nan_false(node):
            continue
        func = node.func
        assert isinstance(func, ast.Attribute)  # narrowed by _is_json_writer_call
        findings.append((node.lineno, func.attr))
    return findings


def _all_python_files() -> list[Path]:
    files: list[Path] = []
    for tree_name in SCAN_TREES:
        tree_root = REPO_ROOT / tree_name
        if not tree_root.exists():
            continue
        files.extend(sorted(tree_root.rglob("*.py")))
    return files


def test_every_json_writer_pins_allow_nan_false() -> None:
    """The audit-completion invariant for the writer-side non-finite
    literal axis: zero unprotected ``json.dump(...)`` /
    ``json.dumps(...)`` writer sites in ``src/`` or ``scripts/``.

    Every JSON writer MUST carry the explicit ``allow_nan=False``
    keyword argument (or use ``**kwargs`` spread — assumed good
    faith). Allowlist entries are reserved for transient hash-compute
    sites and MAC-signed wire-protocol bytes; see :data:`ALLOWLIST`
    docstring for the documented justifications.
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
        for lineno, method in _audit_module(path, tree):
            key = (str(rel), lineno)
            if key in ALLOWLIST:
                continue
            all_findings.append((rel, lineno, method))

    if not all_findings:
        return
    rendered = "\n".join(
        f"  {p}:{lineno}: json.{method}() missing ``allow_nan=False`` "
        "— add the kwarg or document a justification in ALLOWLIST."
        for p, lineno, method in all_findings
    )
    pytest.fail(
        f"{len(all_findings)} JSON writer site(s) without the "
        f"non-finite-literal defence-in-depth pin:\n{rendered}\n\n"
        "Each site must pin ``allow_nan=False`` so Python's lenient "
        "default cannot emit non-standard ``NaN`` / ``Infinity`` / "
        "``-Infinity`` literals (invalid per RFC 8259 §6) into any "
        "committed-to-main JSON artefact. Sites that are genuinely "
        "transient (in-memory hash compute) or MAC-signed wire "
        "payloads must be added to ALLOWLIST with a justification "
        "comment in the same PR. See the Sentinel Writer-Side "
        "Non-Finite Literal Drift Round (2026-05-23 — stammstrecke "
        "state writers + closing-rule walker) audit for the "
        "canonical fix shape."
    )


# ============================================================================
# Smoke tests pinning the walker's pattern-recognition contract
# ============================================================================


def test_walker_flags_bare_dump_without_allow_nan() -> None:
    """A bare ``json.dump(payload, fh)`` is a violation — the default
    ``allow_nan=True`` accepts and emits non-finite literals."""
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
    assert not _has_explicit_allow_nan_false(matches[0]), (
        "Walker must flag bare json.dump() with no allow_nan kwarg"
    )


def test_walker_flags_dump_with_other_kwargs_but_no_allow_nan() -> None:
    """A ``json.dump(payload, fh, indent=2, sort_keys=True)`` is a
    violation — other kwargs do not substitute for ``allow_nan=False``."""
    sample = (
        "import json\n"
        "def f(payload, fh):\n"
        "    json.dump(payload, fh, indent=2, sort_keys=True)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_explicit_allow_nan_false(matches[0])


def test_walker_accepts_explicit_allow_nan_false() -> None:
    """``json.dump(payload, fh, allow_nan=False)`` MUST pass — the
    canonical safe shape."""
    sample = (
        "import json\n"
        "def f(payload, fh):\n"
        "    json.dump(payload, fh, allow_nan=False)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_explicit_allow_nan_false(matches[0]), (
        "Walker must accept explicit allow_nan=False"
    )


def test_walker_rejects_explicit_allow_nan_true() -> None:
    """``json.dump(payload, fh, allow_nan=True)`` is the lenient-
    default shape and MUST be flagged — pinning ``allow_nan=True``
    explicitly is the only way to opt in to the non-standard literal
    surface, and that opt-in is never legitimate for an
    operator-facing sidecar."""
    sample = (
        "import json\n"
        "def f(payload, fh):\n"
        "    json.dump(payload, fh, allow_nan=True)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_explicit_allow_nan_false(matches[0]), (
        "Walker must flag explicit allow_nan=True"
    )


def test_walker_accepts_kwargs_spread() -> None:
    """``json.dump(payload, fh, **kwargs)`` — the ``**kwargs`` spread
    case where static AST analysis cannot determine the dict's keys.
    Walker treats this as protected (good-faith dynamic forwarding);
    per-callsite source-grep pins enforce the canonical kwarg names
    on each documented writer."""
    sample = (
        "import json\n"
        "def f(payload, fh, **kwargs):\n"
        "    json.dump(payload, fh, **kwargs)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert _has_explicit_allow_nan_false(matches[0]), (
        "Walker must accept **kwargs spread as good-faith forwarding"
    )


def test_walker_recognises_aliased_json_module() -> None:
    """``import json as _json_lib; _json_lib.dump(...)`` — the
    aliased-import shape used by
    ``scripts/update_stammstrecke_status.py`` must also be picked up
    by the walker so an alias rename does not silently leak the
    drift."""
    sample = (
        "import json as _json_lib\n"
        "def f(payload, fh):\n"
        "    _json_lib.dump(payload, fh)\n"
    )
    tree = ast.parse(sample)
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
    assert not _has_explicit_allow_nan_false(matches[0])


def test_walker_recognises_dumps_writer() -> None:
    """``json.dumps(payload)`` (the in-memory form) is also a writer
    — the lenient default emits non-finite literals into the
    returned string, which downstream may flow into a committed
    artefact via a separate ``write`` / ``handle.write`` call.
    Walker treats ``dumps`` identically to ``dump``."""
    sample = (
        "import json\n"
        "def f(payload):\n"
        "    return json.dumps(payload)\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert len(matches) == 1
    assert not _has_explicit_allow_nan_false(matches[0])


def test_walker_ignores_json_loads_calls() -> None:
    """Sanity check: ``json.loads`` / ``json.load`` (parser sites)
    must NOT be flagged. The writer-side non-finite-literal axis
    covers writers only; the parser-side axis lives at
    ``tests/test_sentinel_non_finite_literal_audit_walker.py``."""
    sample = (
        "import json\n"
        "def f(payload):\n"
        "    return json.loads(payload)\n"
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
        "own walker."
    )


def test_walker_ignores_unrelated_calls() -> None:
    """Sanity check: unrelated method calls (``.encode()``,
    ``response.json()`` from requests, third-party ``orjson.dumps``)
    must not produce false positives."""
    sample = (
        "import json\n"
        "def f(payload, response):\n"
        "    a = response.json()\n"
        "    b = 'hello'.encode()\n"
        "    return a, b\n"
    )
    tree = ast.parse(sample)
    json_aliases = _collect_json_module_aliases(tree)
    matches: list[ast.Call] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_writer_call(node, json_aliases)
    ]
    assert matches == [], "Walker must NOT produce false positives"


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
    transient / signed-payload sites today. If a future PR needs to
    add an entry, this test pins the requirement that the addition is
    intentional (not accidental) by failing if the allowlist drifts
    beyond the documented size. Update this assertion alongside any
    legitimate allowlist addition AND the module docstring."""
    assert len(ALLOWLIST) == 3, (
        f"ALLOWLIST drifted from the documented size of 3 entries — "
        f"got {len(ALLOWLIST)}. Any new entry must come with a "
        "justification comment AND an update to this assertion + the "
        "module docstring's allowlist semantics section."
    )
    # Pin the exact entries so a future PR that swaps one allowlisted
    # site for another (without updating the docstring) also fails.
    assert ALLOWLIST == frozenset(
        {
            ("src/places/hafas_client.py", 282),
            ("src/build_feed.py", 2317),
            ("src/build_feed.py", 2326),
        }
    )
