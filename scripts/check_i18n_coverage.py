#!/usr/bin/env python3
"""i18n coverage gate for the dashboard.

The dashboard's ``data-i18n*`` attributes pair with entries in the
``I18N_EN`` dictionary in ``docs/assets/site.js``. This gate scans both
files and rejects four drift modes:

  1. **Missing translation** — a ``data-i18n*`` attribute references a
     key that is NOT present in ``I18N_EN``. Subscribers on the EN
     locale would see the German source unchanged for that node.

  2. **Empty translation** — a key is in ``I18N_EN`` but its value is
     the empty string. Almost certainly a typo / merge mishap.

  3. **Orphan translation (informational)** — a key in ``I18N_EN`` is
     not referenced from any ``data-i18n*`` attribute in the HTML.
     This is a *warning* (printed but does not fail the gate); a
     translation that no element consumes is dead code and should be
     pruned, but it is not a regression.

  4. **One-sided locale key** — ``I18N_EN`` only covers strings that
     have a ``data-i18n*`` node. Strings the JavaScript raises itself
     live in DE/EN dictionary pairs (``CHART_TEXT_DE`` /
     ``CHART_TEXT_EN``, ``STATUS_TEXT.de`` / ``.en``, …), each resolved
     as ``dict[key] || DE[key] || key``. A key present on one side only
     therefore renders the *other* language — silently. Both directions
     fail the gate, as does an empty EN value.

Run locally::

    python scripts/check_i18n_coverage.py

The pre-commit hook (``.pre-commit-config.yaml``) and the test
workflow (``.github/workflows/test.yml``) invoke the same command, so
adding a new ``data-i18n="…"`` attribute to ``docs/site.html`` without
the matching ``I18N_EN`` entry fails CI before the change reaches
``main``.

Exit codes
----------
``0``
    No new violations.
``1``
    At least one missing or empty translation, or a DE/EN
    dictionary pair whose key sets have drifted apart.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Ensure the project root is on ``sys.path`` so the canonical
# ``read_capped_text`` defence helper resolves regardless of how the
# script is invoked (``python scripts/check_i18n_coverage.py`` from the
# pre-commit hook, ``scripts/run_static_checks.py`` subprocess in CI, or
# direct execution from a developer shell).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.utils.files import read_capped_text
except ModuleNotFoundError:
    from utils.files import read_capped_text  # type: ignore[no-redef]

HTML_PATH = REPO_ROOT / "docs" / "site.html"
JS_PATH = REPO_ROOT / "docs" / "assets" / "site.js"

# Security: per-loader byte cap for the two committed dashboard files
# (``docs/site.html`` ~20 KiB, ``docs/assets/site.js`` ~50 KiB). Pre-fix
# both sites used the unsafe ``Path.read_text(encoding="utf-8")`` shape
# with NO size cap — a planted multi-GiB file (hostile PR replacing the
# tracked source, compromised CI runner / ``main`` checkout, partial
# flush + power loss mid-edit) buffered via ``read_text()`` allocates
# O(file_size) bytes and raises :exc:`MemoryError`. ``MemoryError`` is a
# :class:`BaseException` subclass that propagates past every ordinary
# ``except`` handler in the call chain and aborts both the pre-commit
# ``i18n-coverage`` hook AND the canonical CI gauntlet at
# ``scripts/run_static_checks.py`` (which invokes this script as a
# subprocess and fails the whole gate on a non-zero exit / crash). The
# 4-MiB cap mirrors :data:`scripts.optimize_site_assets.MAX_CSS_FILE_BYTES`
# (the closest canonical sibling — the optimiser writes the very same
# two files via :func:`atomic_write` so the two scripts share the same
# legitimate ceiling). ~80x headroom over today's largest legitimate
# source (~50 KiB) while still rejecting GiB-sized planted attacks.
MAX_I18N_FILE_BYTES = 4 * 1024 * 1024

# data-i18n="key" / data-i18n-aria-label="key" / data-i18n-title="key" /
# data-i18n-content="key" / data-i18n-href="key" — we extract every
# occurrence and dedupe across attribute variants because they share a
# single namespace inside ``I18N_EN`` (a key used as a body text
# replacement also serves as the aria-label).
_HTML_KEY_RE = re.compile(
    r"""
    \bdata-i18n
    (?:                       # one of the attribute variants…
        -aria-label
        | -title
        | -content
        | -href
    )?                        # …or the bare ``data-i18n`` form
    \s*=\s*
    "([^"]+)"                 # capture the key
    """,
    re.VERBOSE,
)

# ``data-i18n-html="1"`` is a boolean marker (not a translation key)
# whose attribute name otherwise matches the regex above. We exclude
# it via name AND by filtering out the literal value ``"1"``.
_HTML_BOOL_MARKER_RE = re.compile(r'\bdata-i18n-html\s*=\s*"[^"]*"')

# Keys that are not real translation lookups: they map a frontend
# placeholder to a static href swap (``data-href-de`` / ``data-href-en``
# pair on the same element provides the actual values).
_HREF_META_KEYS = frozenset({"feed-href"})

_JS_DICT_KEY_RE = re.compile(r'"([A-Za-z][A-Za-z0-9_-]*)"\s*:')

# ----- JS-only locale dictionaries -----------------------------------
#
# ``I18N_EN`` pairs with ``data-i18n*`` nodes in the HTML. Strings the
# JavaScript raises itself have no such node and live in DE/EN
# dictionary PAIRS instead — ``CHART_TEXT_DE`` / ``CHART_TEXT_EN``,
# ``WEATHER_TEXT_DE`` / ``WEATHER_TEXT_EN``, ``COVERAGE_TEXT_DE`` /
# ``COVERAGE_TEXT_EN``, ``WEEKDAY_LONG_DE`` / ``WEEKDAY_LONG_EN``, and
# the nested ``STATUS_TEXT.de`` / ``.en``.
#
# Every one of them resolves through the same fallback shape::
#
#     dict[key] || <DE dict>[key] || key
#
# so a key present in DE but missing from EN renders the GERMAN string
# to an English visitor, silently — the exact drift mode this gate
# exists to catch, just one dictionary over. The pairs are *discovered*
# rather than listed, so a future ``FOO_TEXT_DE`` / ``FOO_TEXT_EN`` is
# covered from the day it is added.
_LOCALE_SIBLING_RE = re.compile(r"\bconst\s+([A-Z][A-Z0-9_]*)_DE\s*=\s*\{")
_LOCALE_NESTED_RE = re.compile(r"\bconst\s+([A-Z][A-Z0-9_]*)\s*=\s*\{")


def _extract_html_keys(content: str) -> set[str]:
    """Return every ``data-i18n*`` key referenced from the HTML."""
    # Strip out the boolean marker first so its ``"1"`` does not slip
    # into the captured keys.
    cleaned = _HTML_BOOL_MARKER_RE.sub("", content)
    keys: set[str] = set()
    for match in _HTML_KEY_RE.finditer(cleaned):
        key = match.group(1).strip()
        if not key or key == "1":
            continue
        keys.add(key)
    return keys


def _balanced_body(content: str, brace_start: int) -> str | None:
    """Return the body of the ``{ … }`` that starts at ``brace_start``.

    Braces are counted, so a value containing an inline ``{ … }`` (a
    template placeholder, a nested locale sub-object) does not truncate
    the block early.
    """
    if brace_start < 0 or brace_start >= len(content):
        return None
    depth = 0
    for idx in range(brace_start, len(content)):
        ch = content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[brace_start + 1 : idx]
    return None


def _const_object_body(content: str, const_name: str) -> str | None:
    """Return the body of ``const <const_name> = { … };``."""
    match = re.search(
        r"\bconst\s+" + re.escape(const_name) + r"\s*=\s*\{", content
    )
    if match is None:
        return None
    return _balanced_body(content, match.end() - 1)


def _extract_js_dict_block(content: str) -> str | None:
    """Return the contents of the ``const I18N_EN = { … };`` block."""
    return _const_object_body(content, "I18N_EN")


def _extract_js_keys(content: str) -> dict[str, str]:
    """Return ``{key: raw_value_excerpt}`` for every entry in
    ``I18N_EN``.

    ``raw_value_excerpt`` is the leading 60 chars of the literal value
    after the colon (truncated, whitespace-collapsed) — enough to spot
    empty strings without parsing JS expressions.
    """
    block = _extract_js_dict_block(content)
    if block is None:
        return {}
    out: dict[str, str] = {}
    for match in _JS_DICT_KEY_RE.finditer(block):
        key = match.group(1)
        # JavaScript object-literal duplicate-key semantics: the LAST
        # occurrence's value is what the runtime exposes. Pre-fix this
        # loop kept the FIRST value (``if key in out: continue``), so a
        # hand-written ``I18N_EN`` with a non-empty earlier entry then
        # an empty later override (``"k": "good"`` then ``"k": ""``)
        # reported "not empty" while the page rendered blank — the
        # opposite of what ``_value_is_empty`` exists to detect. The
        # ``out[key] = …`` below now overwrites, mirroring runtime.
        # Pull a short excerpt of the value to detect ``: ""``.
        value_start = match.end()
        value_chunk = block[value_start : value_start + 80]
        out[key] = re.sub(r"\s+", " ", value_chunk.lstrip()).strip()
    return out


def _value_is_empty(value_excerpt: str) -> bool:
    """Return True when the JS value literal is an empty string."""
    stripped = value_excerpt.lstrip()
    return stripped.startswith('""') or stripped.startswith("''")


def _skip_trivia(body: str, idx: int) -> int:
    """Advance past whitespace and ``//`` / ``/* */`` comments."""
    n = len(body)
    while idx < n:
        if body[idx].isspace():
            idx += 1
        elif body.startswith("//", idx):
            nl = body.find("\n", idx)
            idx = n if nl < 0 else nl + 1
        elif body.startswith("/*", idx):
            close = body.find("*/", idx + 2)
            idx = n if close < 0 else close + 2
        else:
            break
    return idx


def _skip_string(body: str, idx: int) -> int:
    """Return the index just past the string literal starting at ``idx``.

    Backslash escapes are honoured. A template literal's ``${ … }``
    needs no special handling here: nothing inside it can be the
    literal's own closing backtick.
    """
    quote = body[idx]
    idx += 1
    n = len(body)
    while idx < n:
        if body[idx] == "\\":
            idx += 2
            continue
        if body[idx] == quote:
            return idx + 1
        idx += 1
    return n


def _read_key(body: str, idx: int) -> tuple[str, int, int] | None:
    """Read one object key at ``idx``.

    Returns ``(key, value_start, next_index)``, or ``None`` when the
    token at ``idx`` is not a ``key:`` pair (a spread element, a
    computed key, a shorthand property).
    """
    if body[idx] in "\"'":
        end = _skip_string(body, idx)
        key = body[idx + 1 : end - 1]
    elif body[idx].isalpha() or body[idx] in "_$":
        end = idx
        while end < len(body) and (body[end].isalnum() or body[end] in "_$"):
            end += 1
        key = body[idx:end]
    else:
        return None
    after = _skip_trivia(body, end)
    if after >= len(body) or body[after] != ":":
        return None
    return key, after + 1, after + 1


def _scan_object_keys(body: str) -> list[tuple[str, int]]:
    """Return ``(key, value_start)`` for the TOP-LEVEL entries of a JS
    object-literal body.

    A regex is not enough here. ``WEEKDAY_LONG_DE`` packs several
    entries per line, so a line-anchored pattern sees only the first of
    them — a gate that silently checks 2 of 7 weekdays is worse than no
    gate. Conversely an unanchored pattern matches ``word:`` *inside* a
    value string. This walks the body instead: string literals and
    comments are skipped wholesale, and a key is only recognised where
    one can actually occur — at depth 0, at the start or after a comma.
    """
    entries: list[tuple[str, int]] = []
    idx, n, depth, expect_key = 0, len(body), 0, True
    while idx < n:
        idx = _skip_trivia(body, idx)
        if idx >= n:
            break
        char = body[idx]
        if depth == 0 and expect_key:
            found = _read_key(body, idx)
            if found is not None:
                key, value_start, idx = found
                entries.append((key, value_start))
                expect_key = False
                continue
        if char in "\"'`":
            idx = _skip_string(body, idx)
            continue
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == "," and depth == 0:
            expect_key = True
        idx += 1
    return entries


def _dict_entries(block: str) -> dict[str, str]:
    """Return ``{key: raw_value_excerpt}`` for one object-literal body.

    Duplicate keys keep the LAST value, mirroring JavaScript
    object-literal semantics — same rationale as ``_extract_js_keys``.
    """
    out: dict[str, str] = {}
    for key, value_start in _scan_object_keys(block):
        chunk = block[value_start : value_start + 80]
        out[key] = re.sub(r"\s+", " ", chunk.lstrip()).strip()
    return out


def _locale_pairs(js_content: str) -> dict[str, tuple[str, str]]:
    """Discover DE/EN dictionary pairs — ``{label: (de_body, en_body)}``.

    Two shapes are recognised:

    * **siblings** — ``const FOO_DE = { … }`` next to ``const FOO_EN =
      { … }``; and
    * **nested** — ``const FOO = { de: { … }, en: { … } }``.

    A constant that matches neither (``I18N_EN`` itself, any unrelated
    upper-case object) is skipped rather than reported: only a genuine
    pair carries the "EN falls back to German" failure mode.
    """
    pairs: dict[str, tuple[str, str]] = {}
    for match in _LOCALE_SIBLING_RE.finditer(js_content):
        stem = match.group(1)
        de_body = _balanced_body(js_content, match.end() - 1)
        en_body = _const_object_body(js_content, f"{stem}_EN")
        if de_body is not None and en_body is not None:
            pairs[stem] = (de_body, en_body)
    for match in _LOCALE_NESTED_RE.finditer(js_content):
        name = match.group(1)
        if name in pairs or name.endswith(("_DE", "_EN")):
            continue
        body = _balanced_body(js_content, match.end() - 1)
        if body is None:
            continue
        subs: dict[str, str] = {}
        for key, value_start in _scan_object_keys(body):
            if key not in ("de", "en"):
                continue
            at = _skip_trivia(body, value_start)
            sub_body = (
                _balanced_body(body, at)
                if at < len(body) and body[at] == "{"
                else None
            )
            if sub_body is not None:
                subs[key] = sub_body
        if "de" in subs and "en" in subs:
            pairs[name] = (subs["de"], subs["en"])
    return pairs


def _locale_pair_errors(js_content: str) -> list[str]:
    """Report every DE/EN dictionary pair whose key sets have drifted.

    Both directions are errors: a DE-only key shows the visitor German
    text under the EN locale, an EN-only key leaves the DE locale with
    the fallback (the bare key, or an empty string).
    """
    problems: list[str] = []
    for label, (de_body, en_body) in sorted(_locale_pairs(js_content).items()):
        de_entries = _dict_entries(de_body)
        en_entries = _dict_entries(en_body)
        for key in sorted(set(de_entries) - set(en_entries)):
            problems.append(
                f"{label}: key {key!r} is missing from the EN dictionary — "
                "English visitors would see the German string"
            )
        for key in sorted(set(en_entries) - set(de_entries)):
            problems.append(
                f"{label}: key {key!r} is missing from the DE dictionary — "
                "German visitors would see the fallback, not the text"
            )
        for key in sorted(en_entries):
            if _value_is_empty(en_entries[key]):
                problems.append(f"{label}: EN value for {key!r} is empty")
    return problems


def _js_source_minus_dict(js_content: str) -> str:
    """Return ``js_content`` with the ``I18N_EN`` dict literal stripped.

    A key is only an *orphan* when it appears neither in the HTML nor
    anywhere else in the JS source (programmatic consumers like
    ``statusText("status-ok")`` reference the key as a plain string
    literal). Removing the dict definition before the substring sweep
    avoids self-matches.
    """
    anchor = js_content.find("const I18N_EN")
    if anchor == -1:
        return js_content
    brace_start = js_content.find("{", anchor)
    if brace_start == -1:
        return js_content
    depth = 0
    for idx in range(brace_start, len(js_content)):
        ch = js_content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js_content[:anchor] + js_content[idx + 1:]
    return js_content


def main() -> int:
    if not HTML_PATH.exists():
        print(f"ERROR: {HTML_PATH} not found", file=sys.stderr)
        return 1
    if not JS_PATH.exists():
        print(f"ERROR: {JS_PATH} not found", file=sys.stderr)
        return 1

    # Security: ``read_capped_text`` enforces a TOCTOU-safe byte-size cap
    # (open-then-fstat on the open file descriptor, defensive
    # ``read(max_bytes + 1)`` budget) so a planted oversized file is
    # rejected before buffering — closes the ``MemoryError`` propagation
    # path that would crash the static-checks pipeline. A ``None`` return
    # signals "missing / oversized / invalid UTF-8"; either shape is a
    # hard fail of the gate (we cannot validate i18n coverage against a
    # corrupt input).
    html_content = read_capped_text(
        HTML_PATH, MAX_I18N_FILE_BYTES, label="site.html"
    )
    if html_content is None:
        print(
            f"ERROR: {HTML_PATH} exceeds the "
            f"{MAX_I18N_FILE_BYTES}-byte cap or is unreadable",
            file=sys.stderr,
        )
        return 1
    js_content = read_capped_text(
        JS_PATH, MAX_I18N_FILE_BYTES, label="site.js"
    )
    if js_content is None:
        print(
            f"ERROR: {JS_PATH} exceeds the "
            f"{MAX_I18N_FILE_BYTES}-byte cap or is unreadable",
            file=sys.stderr,
        )
        return 1

    html_keys = _extract_html_keys(html_content)
    js_entries = _extract_js_keys(js_content)
    js_keys = set(js_entries.keys())

    missing = (html_keys - js_keys) - _HREF_META_KEYS
    empty = {
        key for key, value in js_entries.items() if _value_is_empty(value)
    }
    # Programmatic consumers (e.g. ``statusText("status-ok")``) reference
    # a key as a plain string literal elsewhere in the JS source. Drop
    # those from the orphan set so legitimate runtime lookups are not
    # flagged as dead code.
    js_source_outside_dict = _js_source_minus_dict(js_content)
    candidate_orphans = (js_keys - html_keys) - _HREF_META_KEYS
    orphans = {
        key
        for key in candidate_orphans
        if f'"{key}"' not in js_source_outside_dict
    }

    errors = 0

    if missing:
        print(
            "ERROR: data-i18n* keys in docs/site.html have no matching "
            "entry in docs/assets/site.js I18N_EN dict:",
            file=sys.stderr,
        )
        for key in sorted(missing):
            print(f"  - {key}", file=sys.stderr)
        print(
            "  Hint: add the missing English translation(s) to the "
            "I18N_EN dictionary in docs/assets/site.js.",
            file=sys.stderr,
        )
        errors += len(missing)

    if empty:
        print(
            "ERROR: I18N_EN keys in docs/assets/site.js have empty values:",
            file=sys.stderr,
        )
        for key in sorted(empty):
            print(f"  - {key}", file=sys.stderr)
        errors += len(empty)

    pair_problems = _locale_pair_errors(js_content)
    if pair_problems:
        print(
            "ERROR: DE/EN dictionary pairs in docs/assets/site.js have "
            "drifted apart:",
            file=sys.stderr,
        )
        for problem in pair_problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "  Hint: these dictionaries hold the strings the JS raises "
            "itself (no data-i18n node exists for them). Each resolves "
            "as `dict[key] || DE[key] || key`, so a one-sided key is "
            "invisible at runtime — it just renders the wrong language.",
            file=sys.stderr,
        )
        errors += len(pair_problems)

    if orphans:
        print(
            "Note: I18N_EN keys not referenced from docs/site.html "
            "(dead code, prune at your convenience):"
        )
        for key in sorted(orphans):
            print(f"  - {key}")

    if errors:
        print(f"i18n coverage gate FAILED — {errors} issue(s).", file=sys.stderr)
        return 1

    pairs = _locale_pairs(js_content)
    pair_keys = sum(
        len(_dict_entries(de_body)) for de_body, _en_body in pairs.values()
    )
    print(
        f"i18n coverage gate passed — "
        f"{len(html_keys)} HTML keys, {len(js_keys)} JS keys, "
        f"{pair_keys} keys across {len(pairs)} DE/EN dictionary pairs, "
        f"all matched."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
