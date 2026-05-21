"""Coverage audit for the EN translation pipeline.

After the "feed.en.xml partially translated" bug report, this module
exercises the full ``_format_item_content(lang="en")`` path with a
realistic Wiener Linien disruption sentence containing every entity
class (operator brand, ÖPNV line identifier, station name) plus a
small German clause that the ML model is expected to handle. The
mocked translator simulates the canonical Marian/Helsinki-NLP
behaviour: a word-for-word substitution on the German remainder
that preserves the entity placeholders verbatim.

The asserts pin three invariants:

  (a) Entity preservation: every brand / line / station name appears
      in the final English output **verbatim** (no mistranslation).
  (b) Full-message translation: the German remainder around the
      entities is fully translated; no German connector words
      survive into the rendered EN feed.
  (c) Sticky-German guard: when the pipeline succeeds, the resulting
      cache entry is the real English translation (never the German
      source).
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed
from src.feed_types import FeedItem


# A minimal German→English dictionary that the fake pipeline applies
# word-for-word. Matches the kind of substitution a real opus-mt-de-en
# pass produces around the masked entities (the entities themselves
# pass through verbatim because the masker turned them into
# ``XENT<n>X`` placeholders before the fake pipeline saw them).
_FAKE_DICT: dict[str, str] = {
    "Aufgrund": "Due to",
    "wegen": "due to",
    "einer": "a",
    "eines": "a",
    "technischen": "technical",
    "Störung": "disruption",
    "kommt": "comes",
    "es": "it",
    "auf": "on",
    "der": "the",
    "die": "the",
    "Linie": "line",
    "zwischen": "between",
    "und": "and",
    "zu": "to",
    "Verzögerungen": "delays",
    "Schienenersatzverkehr": "rail replacement service",
    "informieren": "inform",
    "Reisende": "Travellers",
    "werden": "are",
    "gebeten": "asked",
    "alternative": "alternative",
    "Routen": "routes",
    "Reisepläne": "travel plans",
    "anzupassen": "to adjust",
    "Es": "There",
    "im": "in",
    "Abschnitt": "section",
    "Bauarbeiten": "construction works",
    "ist": "is",
}


def _fake_marian_translation(text: str, **kwargs: Any) -> list[dict[str, str]]:
    """Mock Helsinki-NLP/opus-mt-de-en that rewrites German via
    :data:`_FAKE_DICT`. Placeholders (``XENT0X``..) and unknown words
    are passed through verbatim, mirroring real Marian behaviour."""
    tokens = re.split(r"(\W+)", text)
    out: list[str] = []
    for token in tokens:
        if token in _FAKE_DICT:
            out.append(_FAKE_DICT[token])
        elif token.lower() in _FAKE_DICT:
            out.append(_FAKE_DICT[token.lower()])
        else:
            out.append(token)
    return [{"translation_text": "".join(out)}]


@pytest.fixture(autouse=True)
def _isolate_translation_state() -> Iterator[None]:
    """Snapshot and restore the global translation state so an early
    failure inside one test does not leak its load_failed=True flag
    into the next."""
    saved_pipeline = build_feed._TRANSLATION_STATE["pipeline"]
    saved_failed = build_feed._TRANSLATION_STATE["load_failed"]
    try:
        yield
    finally:
        build_feed._TRANSLATION_STATE["pipeline"] = saved_pipeline
        build_feed._TRANSLATION_STATE["load_failed"] = saved_failed


def test_complex_disruption_is_fully_translated_with_entity_preservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end audit on a realistic complex disruption message.

    Assertions:
      (a) Entities (operator brand, U-Bahn line, station names) are
          preserved verbatim in the EN output.
      (b) The German remainder around the entities is fully translated
          (no German connectors survive in the rendered description).
    """
    monkeypatch.setattr(
        build_feed, "_get_translation_pipeline", lambda: _fake_marian_translation
    )
    item = cast(
        FeedItem,
        {
            "title": "U6: Verspätung wegen technischer Störung",
            "description": (
                "Aufgrund einer technischen Störung kommt es auf der Linie "
                "U6 zwischen Wien Hauptbahnhof und Stephansplatz zu "
                "Verzögerungen. Wiener Linien informieren."
            ),
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "wl-cov-1",
            "link": "",
        },
    )
    starts = datetime(2026, 5, 16, 10, 0, tzinfo=UTC)
    state: dict[str, dict[str, Any]] = {}

    formatted_en = build_feed._format_item_content(
        item,
        ident="wl-cov-1",
        starts_at=starts,
        ends_at=None,
        lang="en",
        state=state,
    )

    title_en = formatted_en.title_out
    desc_en = formatted_en.desc_text_truncated

    # (a) Entity preservation — every proper noun survives the
    #     round trip.
    assert "U6" in title_en
    assert "U6" in desc_en
    assert "Wien Hauptbahnhof" in desc_en
    assert "Stephansplatz" in desc_en
    assert "Wiener Linien" in desc_en

    # (b) Full-message translation — German connectors are gone and
    #     the English equivalents are present.
    for de_token in (
        "Aufgrund",
        "wegen",
        "einer",
        "technischen",
        "Störung",
        "zwischen",
        "informieren",
    ):
        assert de_token not in title_en, (
            f"German token {de_token!r} leaked into translated title: {title_en!r}"
        )
        assert de_token not in desc_en, (
            f"German token {de_token!r} leaked into translated description: {desc_en!r}"
        )
    for en_token in ("Due to", "technical", "disruption", "between", "inform"):
        assert en_token in desc_en, (
            f"Expected English token {en_token!r} missing from {desc_en!r}"
        )

    # (c) Sticky-German cache guard — the cached translation must NOT
    #     equal the German source.
    translations = state["wl-cov-1"]["translations"]["en"]
    assert translations["title"] != item["title"]
    assert "Stephansplatz" in translations["summary"]
    # No legacy "[Partially translated]" marker because every field
    # translated successfully — and the marker has been retired in
    # favour of the per-item atomic fallback contract.
    assert "Partially translated" not in desc_en


def test_en_feed_falls_back_to_de_when_pipeline_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-item atomic DE↔EN parity: when the pipeline fails the EN
    item is byte-identical to the DE item for that disruption.

    Predecessor (#1597): a ``[Partially translated]`` marker was
    appended to a half-translated item (DE summary + EN time-line +
    marker). Subscribers saw inconsistent content between feed.xml
    and feed.en.xml, which violated the explicit "EN must offer the
    same content as DE" rule. The new contract: the EN feed item
    falls back to a verbatim copy of the German item so the two feeds
    are content-identical when translation is unavailable.
    """
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    item = cast(
        FeedItem,
        {
            "title": "U6: Verspätung",
            "description": "Zwischen Wien Hauptbahnhof und Stephansplatz.",
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "wl-cov-2",
            "link": "",
        },
    )
    state: dict[str, dict[str, Any]] = {}

    formatted_de = build_feed._format_item_content(
        item,
        ident="wl-cov-2",
        starts_at=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        ends_at=None,
        lang="de",
        state=None,
    )
    formatted_en = build_feed._format_item_content(
        item,
        ident="wl-cov-2",
        starts_at=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        ends_at=None,
        lang="en",
        state=state,
    )

    # Atomic DE↔EN parity: every text-bearing field matches DE verbatim.
    assert formatted_en.title_out == formatted_de.title_out
    assert formatted_en.desc_text_truncated == formatted_de.desc_text_truncated
    assert formatted_en.desc_html == formatted_de.desc_html
    # No marker leaks into the published feed.
    assert "Partially translated" not in formatted_en.desc_text_truncated
    # The German time-line is preserved — no half-EN ``[Since …]``
    # smuggled into a DE-fallback item.
    assert "[Am " in formatted_en.desc_text_truncated or formatted_en.desc_text_truncated == formatted_de.desc_text_truncated
    # And: the German fallback was NOT cached as the EN translation
    # (sticky-German fix from PR #1597 still in force).
    translations = state.get("wl-cov-2", {}).get("translations", {}).get("en", {})
    assert "title" not in translations
    assert "summary" not in translations


def test_truncation_kwarg_is_forwarded_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long inputs must reach the pipeline with ``truncation=True`` so
    Marian truncates at its 512-token context window instead of
    asserting and aborting the entire EN-feed build."""
    captured: dict[str, Any] = {}

    def fake_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        captured["kwargs"] = kwargs
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: fake_pipeline)
    build_feed._translate_text("Eine Meldung über die U6.")
    assert captured["kwargs"].get("truncation") is True
    assert captured["kwargs"].get("max_length") == 512


def test_failure_log_includes_identity(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A translation failure must log the feed item's identity so
    operators can grep the GitHub Actions log for partial-translation
    drift."""
    def fake_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        raise RuntimeError("simulated model crash")

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: fake_pipeline)

    import logging
    with caplog.at_level(logging.WARNING, logger="build_feed"):
        attempt = build_feed._translate_text_attempt("Eine Meldung.", ident="wl-fail-1")
    assert attempt is None
    failure_logs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("wl-fail-1" in msg for msg in failure_logs), failure_logs


def test_cache_repair_after_sticky_german(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A state file inherited from a buggy pre-fix build where the
    cached "translation" equals the German source must be repaired on
    the next healthy run instead of serving the stale German text
    forever."""
    state: dict[str, dict[str, Any]] = {
        "wl-cov-3": {
            "first_seen": "2026-05-01T00:00:00+00:00",
            "translations": {
                "en": {"title": "Verspätung"},  # stale: equals source
            },
        },
    }
    # Healthy pipeline now produces a real translation.
    monkeypatch.setattr(
        build_feed,
        "_get_translation_pipeline",
        lambda: lambda text, **kwargs: [{"translation_text": "Delay"}],
    )
    text, succeeded = build_feed._cached_translation(
        "Verspätung", "title", "wl-cov-3", state
    )
    assert succeeded is True
    assert text == "Delay"
    # Cache repaired with the real translation.
    assert state["wl-cov-3"]["translations"]["en"]["title"] == "Delay"


def test_de_and_en_feed_items_have_identical_content_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DE and EN feeds must always describe the same set of
    disruptions in the same order.

    This is the integration-level invariant behind the user's
    "selben Inhalt bieten" contract: even when the pipeline fails
    for every item, the EN feed must enumerate the same GUIDs in
    the same order as the DE feed — no item dropped, no item
    duplicated, no item silently re-ordered.
    """
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    items = [
        cast(
            FeedItem,
            {
                "title": f"U{i}: Verspätung",
                "description": f"Test disruption {i}",
                "source": "Wiener Linien",
                "category": "Störung",
                "guid": f"wl-set-{i}",
                "link": "",
                "pubDate": datetime(2026, 5, 16, 10, i, tzinfo=UTC),
            },
        )
        for i in range(1, 6)
    ]
    state: dict[str, dict[str, Any]] = {}
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    rss_de = build_feed._make_rss(items, now, state, lang="de")
    rss_en = build_feed._make_rss(items, now, state, lang="en")

    de_guids = re.findall(r"<guid[^>]*>([^<]+)</guid>", rss_de)
    en_guids = re.findall(r"<guid[^>]*>([^<]+)</guid>", rss_en)
    # Same set, same order — the GUIDs are the canonical identity.
    assert de_guids == en_guids
    assert len(de_guids) == len(items)


def test_de_and_en_feed_items_are_byte_identical_when_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-item atomic fallback: when the pipeline cannot translate,
    every ``<item>…</item>`` block in feed.en.xml is byte-identical
    to the corresponding block in feed.xml. No mixed-language text,
    no marker leaking into the EN feed body.
    """
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    item = cast(
        FeedItem,
        {
            "title": "U6: Verspätung wegen Bauarbeiten",
            "description": (
                "Aufgrund einer technischen Störung kommt es zu "
                "Verzögerungen zwischen Wien Hauptbahnhof und Stephansplatz."
            ),
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "wl-parity-1",
            "link": "",
            "pubDate": datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
            "starts_at": datetime(2026, 5, 16, 9, 0, tzinfo=UTC),
        },
    )
    state: dict[str, dict[str, Any]] = {}
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    rss_de = build_feed._make_rss([item], now, state, lang="de")
    rss_en = build_feed._make_rss([item], now, state, lang="en")

    # Extract the <item>…</item> body from each feed. Only ONE item
    # in this fixture, so a single regex group is enough.
    de_item = re.search(r"<item>.*?</item>", rss_de, re.DOTALL)
    en_item = re.search(r"<item>.*?</item>", rss_en, re.DOTALL)
    assert de_item is not None and en_item is not None

    # Atomic fallback contract: the EN item is byte-identical to the
    # DE item when the pipeline cannot translate.
    assert en_item.group(0) == de_item.group(0)
    # And: no legacy marker survived in the EN feed body.
    assert "Partially translated" not in rss_en
