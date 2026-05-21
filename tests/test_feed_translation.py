"""Regression tests for the bilingual (DE/EN) feed pipeline.

The translation overlay in :mod:`src.build_feed` is intentionally
fail-safe: when the Hugging Face pipeline cannot be loaded (e.g. on a
runner without network or torch), every translation call returns the
German original. These tests pin that behaviour and exercise the
state-based caching contract so a "fail-safe" run still produces a
valid English feed mirror.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from src import build_feed
from src.feed_types import FeedItem


def test_translate_time_line_swaps_known_prefixes() -> None:
    assert build_feed._translate_time_line_en("[Seit 05.01.2026]") == "[Since 05.01.2026]"
    assert build_feed._translate_time_line_en("[Bis 31.12.2026]") == "[Until 31.12.2026]"
    assert build_feed._translate_time_line_en("[Ab 05.05.2026]") == "[From 05.05.2026]"
    assert build_feed._translate_time_line_en("[Am 09.09.2026]") == "[On 09.09.2026]"


def test_translate_time_line_passthrough_for_ranges_and_empty() -> None:
    assert (
        build_feed._translate_time_line_en("[01.01.2026 – 02.01.2026]")
        == "[01.01.2026 – 02.01.2026]"
    )
    assert build_feed._translate_time_line_en("") == ""


def test_translate_text_returns_original_without_pipeline(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    assert build_feed._translate_text("Verspätung") == "Verspätung"


def test_cached_translation_returns_success_flag(monkeypatch: Any) -> None:
    """A pipeline failure must NOT cache the German source as the EN
    "translation" — that was the Sticky-German cache-corruption bug.

    Pre-fix behaviour: ``_cached_translation`` returned the German
    fallback AND wrote it under ``state[ident]["translations"]["en"]``,
    so subsequent runs (with a healthy pipeline) still served the
    cached German text.
    """
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    state: dict[str, dict[str, Any]] = {}
    text, succeeded = build_feed._cached_translation(
        "Hallo", "title", "ident-1", state
    )
    assert text == "Hallo"  # fallback to original (no pipeline)
    assert succeeded is False  # explicit failure flag
    # Cache MUST NOT carry the German fallback — the next run gets a
    # clean retry.
    translations = state.get("ident-1", {}).get("translations", {})
    assert "title" not in translations.get("en", {})

    # Once the cache has a real translation, the second lookup reads
    # from state without re-invoking the (still-broken) pipeline.
    state.setdefault("ident-1", {}).setdefault("translations", {}).setdefault(
        "en", {}
    )["title"] = "Hello (cached)"
    text2, succeeded2 = build_feed._cached_translation(
        "Hallo", "title", "ident-1", state
    )
    assert text2 == "Hello (cached)"
    assert succeeded2 is True


def test_format_item_content_en_falls_back_when_pipeline_unavailable(
    monkeypatch: Any,
) -> None:
    """``lang="en"`` falls back to the German item verbatim when the
    pipeline is unavailable — per the DE↔EN content-parity contract.

    The previous design rebuilt the description with the EN time-line
    plus a ``[Partially translated]`` marker; that produced a mixed-
    language item that violated "EN must offer the same content as
    DE". The new contract: per-item atomic fallback. If the model
    cannot translate the item, the EN feed item is byte-identical to
    the DE item for that disruption.
    """
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    item = cast(
        FeedItem,
        {
            "title": "U6: Verspätung",
            "description": "Es kommt zu Verzögerungen.",
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "wl-1",
            "link": "",
        },
    )
    starts = datetime(2026, 5, 16, 10, 0, tzinfo=UTC)
    state: dict[str, dict[str, Any]] = {}
    formatted_de = build_feed._format_item_content(
        item, ident="wl-1", starts_at=starts, ends_at=None,
        lang="de", state=None,
    )
    formatted_en = build_feed._format_item_content(
        item, ident="wl-1", starts_at=starts, ends_at=None,
        lang="en", state=state,
    )
    # Atomic fallback: the EN feed item is byte-identical to the DE
    # item when the pipeline cannot translate.
    assert formatted_en.title_out == formatted_de.title_out
    assert formatted_en.desc_text_truncated == formatted_de.desc_text_truncated
    assert formatted_en.desc_html == formatted_de.desc_html
    # No legacy "[Partially translated]" marker leaks into the feed.
    assert "Partially translated" not in formatted_en.desc_text_truncated
    # German time-line is preserved (no half-EN ``[Since …]`` smuggled
    # into a DE-fallback item — the byte-identical assertion above
    # already covers this, but pin the human-readable invariant too).
    assert "[Since" not in formatted_en.desc_text_truncated
    assert "[On" not in formatted_en.desc_text_truncated
    assert "[Until" not in formatted_en.desc_text_truncated
    assert "[From" not in formatted_en.desc_text_truncated
    # Cache MUST NOT contain the German fallback for either field —
    # the next run gets a clean retry once the pipeline is healthy.
    translations = state.get("wl-1", {}).get("translations", {}).get("en", {})
    assert "title" not in translations
    assert "summary" not in translations


def test_make_rss_en_writes_english_metadata_and_atom_self(
    monkeypatch: Any,
) -> None:
    """``_make_rss(lang="en")`` emits ``<language>en</language>`` and the
    EN atom self href."""
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    state: dict[str, dict[str, Any]] = {}
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    xml = build_feed._make_rss([], now, state, lang="en")
    assert "<language>en</language>" in xml
    assert "feed.en.xml" in xml
    assert "<language>de</language>" not in xml


def test_make_rss_de_writes_german_metadata_default(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    state: dict[str, dict[str, Any]] = {}
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    xml = build_feed._make_rss([], now, state)
    assert "<language>de</language>" in xml
    assert ">feed.xml<" in xml or "/feed.xml" in xml


def test_apply_lang_overlay_passthrough_for_de() -> None:
    base = build_feed.FormattedContent(
        guid="g",
        link="https://example.com",
        title_cdata="T",
        desc_text_truncated="X",
        desc_cdata="X",
        raw_desc="X",
        title_out="X",
        desc_html="X",
    )
    out = build_feed._apply_lang_overlay(base, "X", "[Seit 01.01.2026]", "id", "de", {})
    assert out is base
