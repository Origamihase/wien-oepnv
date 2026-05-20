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


def test_cached_translation_persists_in_state(monkeypatch: Any) -> None:
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    state: dict[str, dict[str, Any]] = {}
    first = build_feed._cached_translation("Hallo", "title", "ident-1", state)
    assert first == "Hallo"  # fallback to original (no pipeline)
    # State now carries the translation under the canonical layout.
    assert state["ident-1"]["translations"]["en"]["title"] == "Hallo"

    # Overwrite the cached value — second lookup must read from state and
    # NOT re-trigger ``_translate_text``.
    state["ident-1"]["translations"]["en"]["title"] = "Hello (cached)"
    assert (
        build_feed._cached_translation("Hallo", "title", "ident-1", state)
        == "Hello (cached)"
    )


def test_format_item_content_en_falls_back_when_pipeline_unavailable(
    monkeypatch: Any,
) -> None:
    """``lang="en"`` runs the overlay and rebuilds desc with EN time-line.

    The pipeline is stubbed to ``None`` so the title/summary stay as the
    German original; the time-line prefix is translated via the static
    dictionary so the EN feed still carries the English bracketed form.
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
    formatted_en = build_feed._format_item_content(
        item, ident="wl-1", starts_at=starts, ends_at=None,
        lang="en", state=state,
    )
    assert "[Since" in formatted_en.desc_text_truncated
    # German fallback preserved for title because the model is unreachable.
    assert formatted_en.title_out == "U6: Verspätung"
    # Cache populated for the second call.
    assert "title" in state["wl-1"]["translations"]["en"]
    assert "summary" in state["wl-1"]["translations"]["en"]


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
