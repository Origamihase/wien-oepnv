"""Sentinel for the dashboard tile design (``docs/assets/site.css``, 2026-10-17).

Michael kept the layout, the colours, the train strip and the footer, and
asked for less generic tiles. The design gives every tile the colour of what
it stands for: feed tiles their source (``--src``), statistics sections a
tone (``--tone``). The browser checks (axe, overflow, screenshots) need
Chromium and stay out of the suite; this guard pins the rules that carry the
decision, so a later clean-up does not quietly fall back to plain white
boxes or reintroduce the invisible dark-mode filter chip.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "docs" / "assets"

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _css() -> str:
    return _COMMENT_RE.sub("", (ASSETS / "site.css").read_text(encoding="utf-8"))


def _rule(css: str, selector: str) -> str:
    match = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"site.css: no '{selector}' rule"
    return re.sub(r"\s+", " ", match.group(1))


def _dark_blocks(css: str) -> str:
    """Concatenate the bodies of every ``prefers-color-scheme: dark`` block."""
    out = []
    for m in re.finditer(r"@media \(prefers-color-scheme: dark\)\s*\{", css):
        depth, pos = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[pos], 0)
            pos += 1
        out.append(css[m.end() : pos - 1])
    return "\n".join(out)


def test_feed_tiles_take_the_colour_of_their_source() -> None:
    css = _css()
    for source, token in (
        ("wienerlinien", "--c-wl"),
        ("oebb", "--c-oebb"),
        ("vor", "--c-vor"),
        ("baustellen", "--c-baustellen"),
    ):
        assert re.search(
            r'\.feed-item\[data-source="' + source + r'"\][^{]*\{\s*--src:\s*var\(' + token + r"\)",
            css,
        ), f"feed tile for {source} lost its source colour"
    tile = _rule(css, ".feed-item")
    assert "inset 4px 0 0 var(--src)" in tile
    assert "background: var(--src)" in _rule(css, ".feed-item__source")


def test_source_pill_carries_no_dot() -> None:
    # Michael 2026-10-30: "Bitte entferne den Punkt aus den 'Pillen'". The
    # source label shows its name only; the filter chips keep their dot.
    css = _css()
    assert ".feed-item__source::before" not in css
    assert "background: var(--src)" in _rule(
        css, '.chip[data-filter]:not([data-filter="all"])::before'
    )


def test_dark_mode_source_colours_keep_white_pill_text_readable() -> None:
    # The pastel dark-mode tokens give white pill text only 2.7-2.9:1; the
    # darker brand variants must stay the tile colour in dark mode.
    dark = _dark_blocks(_css())
    assert "--src: #b80024" in dark
    assert "--src: #9f2a1b" in dark
    assert "--src: #406b0e" in dark


def test_statistics_sections_carry_their_bar_tone() -> None:
    css = _css()
    assert "--tone: var(--c-warning)" in _rule(css, "#stammstrecke")
    assert "--tone: var(--c-danger)" in _rule(css, "#ausfaelle")
    assert "var(--tone)" in _rule(css, ".kpi__label::before")
    assert "var(--tone)" in _rule(css, ".section__header h2::before")


def test_active_filter_chip_is_visible_in_dark_mode() -> None:
    # ``--c-brand`` equals the dark page background (#0a1126), so the old
    # active chip vanished in dark mode.
    active = _rule(_css(), ".chip.is-active")
    assert "var(--c-brand)" not in active
    assert "background: var(--c-text)" in active


def test_reduced_motion_switches_the_tile_animations_off() -> None:
    css = _css()
    start = css.index("@media (prefers-reduced-motion: reduce) {\n  .feed-item")
    block = css[start : css.index("}", start)]
    assert ".kpi" in block and ".live-tile__label::before" in block
    assert "animation: none" in block


def test_entrance_animation_never_holds_the_hover_transform() -> None:
    # ``forwards``/``both`` would pin ``transform`` after the animation and
    # block the hover lift; only ``backwards`` (fill during the delay) is safe.
    css = _css()
    for selector in (".feed-item", ".kpi"):
        rule = _rule(css, selector)
        assert "animation: tile-in" in rule and "backwards" in rule
        assert "forwards" not in rule and " both" not in rule


def test_minified_bundle_carries_the_tile_design() -> None:
    minified = (ASSETS / "site.min.css").read_text(encoding="utf-8")
    assert "@keyframes tile-in" in minified
    assert "--tone:var(--c-warning)" in minified
