"""Drift guard for §8 of the architecture map (the DE → EN pipeline).

The map is written for the person who joins in six months. A section that
names functions which no longer exist is worse than no section: it sends
that person looking for code that was renamed or deleted, and it reads as
authoritative while doing so.

This test does not check prose. It checks the two things that rot
silently:

1. every ``_symbol`` the section names still exists in
   ``src/build_feed.py``;
2. the section still exists at all, and still names the epoch constant —
   the one rule a contributor has to act on (bump it in the same PR when
   masking or the glossary changes), and the easiest to lose in an edit.

The pattern follows ``test_vor_ci_quota_gate.py::
test_architecture_doc_lists_the_ci_consumer``, which pins §7 the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_DOC = REPO_ROOT / "docs" / "architecture.md"
_SOURCE = REPO_ROOT / "src" / "build_feed.py"

_SECTION_HEADING = "## 8. Der zweisprachige Feed (DE → EN)"
_NEXT_HEADING = "## 9. Querverweise"


def _section() -> str:
    text = _DOC.read_text(encoding="utf-8")
    assert _SECTION_HEADING in text, "§8 (DE → EN pipeline) is gone from the map"
    assert _NEXT_HEADING in text, "§9 (Querverweise) is gone from the map"
    start = text.index(_SECTION_HEADING)
    end = text.index(_NEXT_HEADING)
    assert start < end, "§8 must precede §9"
    return text[start:end]


def _named_symbols(section: str) -> list[str]:
    """Every ``_private_name`` the section puts in backticks."""
    return sorted(set(re.findall(r"`(_[A-Za-z_]+)`", section)))


def test_the_section_names_symbols_at_all() -> None:
    """Guards the guard: an empty match set would make the next test vacuous."""
    assert len(_named_symbols(_section())) >= 10


@pytest.mark.parametrize("symbol", _named_symbols(_section()))
def test_every_named_symbol_still_exists(symbol: str) -> None:
    source = _SOURCE.read_text(encoding="utf-8")
    assert symbol in source, (
        f"architecture.md §8 names {symbol!r}, which no longer exists in "
        "src/build_feed.py — rename the reference or drop it"
    )


def test_the_epoch_rule_is_still_stated() -> None:
    """The one thing §8 asks a contributor to *do*.

    A masking or glossary improvement without a bump is invisible: the
    sticky-German guard only retries when the cached value equals the
    German source, so a wrong-but-English translation is served for the
    item's lifetime.
    """
    section = _section()
    assert "_TRANSLATION_CACHE_EPOCH" in section
    assert "Epoche" in section


def test_the_atomic_fallback_contract_is_still_stated() -> None:
    """The invariant an eager "at least partially translated" patch breaks."""
    section = _section()
    assert "_apply_lang_overlay" in section
    assert "deutsch" in section.casefold()
