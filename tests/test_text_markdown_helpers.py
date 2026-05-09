"""Tests for the Markdown-safety helpers in :mod:`src.utils.text`.

Companion to ``tests/scripts/test_generate_markdown_stats_md_injection.py``,
which exercises the helpers end-to-end through the dashboard renderer.
This file pins the contract of the primitives themselves so a future
regex regression (LRM/RLM dropped, BiDi-mark range narrowed, length
cap removed) shows up as a focused unit-test failure rather than a
diffuse dashboard test.
"""
from __future__ import annotations

from src.utils.text import (
    escape_markdown,
    escape_markdown_cell,
    normalise_markdown_text,
    safe_markdown_codespan,
)


def test_normalise_markdown_text_strips_c0_controls() -> None:
    """C0 controls (NUL through US, except TAB / LF / CR) must be stripped."""
    assert normalise_markdown_text("Foo\x00Bar") == "FooBar"
    assert normalise_markdown_text("Foo\x07Bar") == "FooBar"
    assert normalise_markdown_text("Foo\x1fBar") == "FooBar"
    assert normalise_markdown_text("Foo\x0bBar") == "FooBar"  # VT
    assert normalise_markdown_text("Foo\x0cBar") == "FooBar"  # FF


def test_normalise_markdown_text_collapses_tab_lf_cr_to_space() -> None:
    """TAB / LF / CR survive control-byte stripping but collapse to space."""
    assert normalise_markdown_text("Foo\tBar") == "Foo Bar"
    assert normalise_markdown_text("Foo\nBar") == "Foo Bar"
    assert normalise_markdown_text("Foo\rBar") == "Foo Bar"
    assert normalise_markdown_text("Foo\r\n\tBar") == "Foo Bar"


def test_normalise_markdown_text_strips_c1_controls_and_del() -> None:
    """DEL + C1 range (\\x80-\\x9f) must be stripped — legacy display directives."""
    assert normalise_markdown_text("Foo\x7fBar") == "FooBar"  # DEL
    assert normalise_markdown_text("Foo\x80Bar") == "FooBar"
    assert normalise_markdown_text("Foo\x9fBar") == "FooBar"


def test_normalise_markdown_text_strips_bidi_marks() -> None:
    """Trojan-Source BiDi marks (CVE-2021-42574) must not survive."""
    assert normalise_markdown_text("Foo" + chr(0x202E) + "Bar") == "FooBar"  # RLO
    assert normalise_markdown_text("Foo" + chr(0x202D) + "Bar") == "FooBar"  # LRO
    assert normalise_markdown_text("Foo" + chr(0x200E) + "Bar") == "FooBar"  # LRM
    assert normalise_markdown_text("Foo" + chr(0x200F) + "Bar") == "FooBar"  # RLM
    assert normalise_markdown_text("Foo" + chr(0x061C) + "Bar") == "FooBar"  # ALM
    assert normalise_markdown_text("Foo" + chr(0x2066) + "Bar") == "FooBar"  # LRI
    assert normalise_markdown_text("Foo" + chr(0x2069) + "Bar") == "FooBar"  # PDI


def test_normalise_markdown_text_strips_zero_width_chars() -> None:
    """ZWSP / ZWNJ / ZWJ / BOM are pure invisible-clutter primitives."""
    assert normalise_markdown_text("Foo" + chr(0x200B) + "Bar") == "FooBar"  # ZWSP
    assert normalise_markdown_text("Foo" + chr(0x200C) + "Bar") == "FooBar"  # ZWNJ
    assert normalise_markdown_text("Foo" + chr(0x200D) + "Bar") == "FooBar"  # ZWJ
    assert normalise_markdown_text("Foo" + chr(0xFEFF) + "Bar") == "FooBar"  # BOM


def test_normalise_markdown_text_strips_unicode_line_separators() -> None:
    """U+2028 / U+2029 split log records and Markdown table rows."""
    assert normalise_markdown_text("Foo" + chr(0x2028) + "Bar") == "FooBar"  # LINE SEP
    assert normalise_markdown_text("Foo" + chr(0x2029) + "Bar") == "FooBar"  # PARA SEP


def test_normalise_markdown_text_caps_length() -> None:
    """The length cap defends against unbounded operator strings."""
    assert normalise_markdown_text("A" * 500, max_len=80) == "A" * 80


def test_normalise_markdown_text_handles_empty_and_whitespace() -> None:
    assert normalise_markdown_text("") == ""
    assert normalise_markdown_text("   ") == ""
    assert normalise_markdown_text("\n\n\t\r") == ""


def test_normalise_markdown_text_preserves_legitimate_unicode() -> None:
    """Umlauts and other legitimate Unicode must NOT be stripped."""
    assert normalise_markdown_text("Wien Floridsdorf") == "Wien Floridsdorf"
    assert normalise_markdown_text("Karlsplatz / Oper") == "Karlsplatz / Oper"
    assert normalise_markdown_text("ÖBB") == "ÖBB"
    assert normalise_markdown_text("Wiener Linien") == "Wiener Linien"


def test_safe_markdown_codespan_replaces_backticks() -> None:
    """A backtick inside `` `…` `` closes the inline code span — replace it."""
    assert safe_markdown_codespan("Foo`evil`bar") == "Foo'evil'bar"
    assert safe_markdown_codespan("`leading") == "'leading"
    assert safe_markdown_codespan("trailing`") == "trailing'"


def test_safe_markdown_codespan_inherits_normalisation() -> None:
    """Code-span normalisation includes control-byte / whitespace cleanup."""
    assert safe_markdown_codespan("Foo\nbar") == "Foo bar"
    assert safe_markdown_codespan("Foo\x00bar") == "Foobar"
    assert safe_markdown_codespan("Foo" + chr(0x202E) + "bar") == "Foobar"
    assert safe_markdown_codespan("A" * 500, max_len=30) == "A" * 30


def test_escape_markdown_cell_escapes_pipe_and_html() -> None:
    """Existing cell escape contract: pipe + Markdown + HTML defanged."""
    assert escape_markdown_cell("Foo|Bar") == r"Foo\|Bar"
    assert escape_markdown_cell("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert escape_markdown_cell("[click](http://x)") == r"\[click\]\(http://x\)"


def test_escape_markdown_strips_dangerous_markdown_meta_chars() -> None:
    """Existing inline escape contract: Markdown specials backslash-escaped."""
    assert escape_markdown("**bold**") == r"\*\*bold\*\*"
    assert escape_markdown("`code`") == r"\`code\`"
    assert escape_markdown("@here") == r"\@here"
