"""Sentinel for the dashboard header on phones (``docs/assets/site.css``).

The header never had a narrow-screen layout: below ~1000 CSS px its nav
stacked the links vertically, the sticky header grew to 232 px on every
phone (a third of the screen at every scroll position) and, below ~530 px,
the DE | EN switch sat beyond the right edge, where ``html { overflow-x:
clip }`` cut it off — the English version could not be chosen on a phone.
Measured with Playwright device emulation on 2026-10-04; the browser checks
themselves need Chromium and stay out of the suite, so this guard pins the
rules that fix it.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "docs" / "assets"

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _css(name: str) -> str:
    return _COMMENT_RE.sub("", (ASSETS / name).read_text(encoding="utf-8"))


def _media_block(css: str, query: str) -> str:
    """Return the body of the first ``@media <query>`` block (brace-matched)."""
    start = css.find(f"@media {query}")
    assert start != -1, f"site.css: no '@media {query}' block"
    depth = 0
    for pos in range(css.index("{", start), len(css)):
        if css[pos] == "{":
            depth += 1
        elif css[pos] == "}":
            depth -= 1
            if depth == 0:
                return css[css.index("{", start) + 1 : pos]
    raise AssertionError(f"site.css: unbalanced '@media {query}' block")


def _rule(block: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", block)
    assert match, f"site.css: no '{selector}' rule in the narrow-screen block"
    return re.sub(r"\s+", " ", match.group(1))


def test_narrow_header_wraps_nav_onto_its_own_scrollable_row() -> None:
    block = _media_block(_css("site.css"), "(max-width: 767px)")
    assert "flex-wrap: wrap" in _rule(block, ".site-header__inner")
    nav = _rule(block, ".site-nav")
    assert "order: 3" in nav and "flex: 1 0 100%" in nav
    ul = _rule(block, ".site-nav ul")
    assert "flex-wrap: nowrap" in ul and "overflow-x: auto" in ul


def test_narrow_header_rules_come_after_the_base_section_rule() -> None:
    # Same specificity, so source order decides: the narrow ``.section``
    # scroll margin must follow the base rule or anchor jumps land under the
    # two-row sticky header.
    css = _css("site.css")
    assert css.index("@media (max-width: 767px)") > css.index(".section {")


def test_skip_link_is_parked_by_its_own_height() -> None:
    # A fixed negative ``top`` shorter than the link left a dark sliver at the
    # top edge of the page.
    rule = _rule(_css("site.css"), ".skip-link")
    assert "translateY(calc(-100% - 1rem))" in rule
    assert not re.search(r"top:\s*-", rule)


def test_minified_bundle_carries_the_narrow_header() -> None:
    minified = (ASSETS / "site.min.css").read_text(encoding="utf-8")
    assert "@media (max-width:767px)" in minified
    assert "flex:1 0 100%" in minified
