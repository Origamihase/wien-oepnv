#!/usr/bin/env python3
"""i18n coverage gate for the dashboard.

The dashboard's ``data-i18n*`` attributes pair with entries in the
``I18N_EN`` dictionary in ``docs/assets/site.js``. This gate scans both
files and rejects three drift modes:

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
    At least one missing or empty translation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = REPO_ROOT / "docs" / "site.html"
JS_PATH = REPO_ROOT / "docs" / "assets" / "site.js"

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


def _extract_js_dict_block(content: str) -> str | None:
    """Return the contents of the ``const I18N_EN = { … };`` block."""
    anchor = content.find("const I18N_EN")
    if anchor == -1:
        return None
    brace_start = content.find("{", anchor)
    if brace_start == -1:
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
        if key in out:
            # Duplicate keys silently overwrite at runtime; surface it.
            continue
        # Pull a short excerpt of the value to detect ``: ""``.
        value_start = match.end()
        value_chunk = block[value_start : value_start + 80]
        out[key] = re.sub(r"\s+", " ", value_chunk.lstrip()).strip()
    return out


def _value_is_empty(value_excerpt: str) -> bool:
    """Return True when the JS value literal is an empty string."""
    stripped = value_excerpt.lstrip()
    return stripped.startswith('""') or stripped.startswith("''")


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

    html_content = HTML_PATH.read_text(encoding="utf-8")
    js_content = JS_PATH.read_text(encoding="utf-8")

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

    print(
        f"i18n coverage gate passed — "
        f"{len(html_keys)} HTML keys, {len(js_keys)} JS keys, all matched."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
