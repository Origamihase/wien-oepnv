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


# --- Metadata-driven glossary end-to-end ------------------------------
#
# The above tests pin the translation pipeline against the BASE
# glossary. The next two tests pin the metadata-aware layering: a
# Wiener Linien item must activate the WL overlay (``Aufzug`` →
# ``elevator``); an ÖBB item must activate the ÖBB overlay
# (``Personenzug`` → ``passenger train``). The overlays kick in
# automatically because :func:`_format_item_content` extracts the
# item's ``source`` / ``category`` and threads them through the
# translation cascade. No caller code change required to benefit from
# the operator-specific vocabulary.


def test_metadata_glossary_activates_wiener_linien_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WL item containing ``Kurzführung`` must surface as
    ``short-running service`` in the EN feed — the WL source overlay
    is the only place that maps ``Kurzführung``. Without the overlay
    Marian renders it as the literal "short conduct" which is
    meaningless in transit English."""
    # Fake pipeline: passes the masked text through verbatim. The
    # glossary already substituted the WL terms BEFORE the pipeline
    # saw them, so the test does not need to translate anything itself.
    def pass_through(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": text}]

    monkeypatch.setattr(
        build_feed, "_get_translation_pipeline", lambda: pass_through
    )
    item = cast(
        FeedItem,
        {
            "title": "5: Kurzführung",
            "description": (
                "Kurzführung der Linie 5 zwischen Westbahnhof und "
                "Praterstern wegen Schadhaftem Fahrzeug."
            ),
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "wl-meta-1",
            "link": "",
        },
    )
    state: dict[str, dict[str, Any]] = {}
    formatted_en = build_feed._format_item_content(
        item,
        ident="wl-meta-1",
        starts_at=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        ends_at=None,
        lang="en",
        state=state,
    )
    desc_en = formatted_en.desc_text_truncated.lower()
    # WL overlay activated: surface form gone, EN equivalent present.
    assert "kurzführung" not in desc_en
    assert "short-running service" in desc_en
    # Base glossary still active alongside the overlay.
    assert "schadhaftem fahrzeug" not in desc_en
    assert "defective vehicle" in desc_en


def test_metadata_glossary_activates_oebb_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ÖBB item containing ``Personenzug`` and ``Bahnsteigwechsel``
    must surface as ``passenger train`` and ``platform change`` in
    the EN feed — both terms are ÖBB-only vocabulary."""
    def pass_through(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": text}]

    monkeypatch.setattr(
        build_feed, "_get_translation_pipeline", lambda: pass_through
    )
    item = cast(
        FeedItem,
        {
            "title": "Information",
            "description": (
                "Personenzug 5072 mit kurzfristigem Bahnsteigwechsel. "
                "Reisende beachten den Anschlussverlust nach Salzburg."
            ),
            "source": "ÖBB",
            "category": "Störung",
            "guid": "oebb-meta-1",
            "link": "",
        },
    )
    state: dict[str, dict[str, Any]] = {}
    formatted_en = build_feed._format_item_content(
        item,
        ident="oebb-meta-1",
        starts_at=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        ends_at=None,
        lang="en",
        state=state,
    )
    desc_en = formatted_en.desc_text_truncated.lower()
    # ÖBB overlay activated: surface forms gone, EN equivalents present.
    assert "personenzug" not in desc_en
    assert "passenger train" in desc_en
    assert "bahnsteigwechsel" not in desc_en
    assert "platform change" in desc_en
    assert "anschlussverlust" not in desc_en
    assert "missed connection" in desc_en


def test_metadata_glossary_no_cross_operator_contamination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Baustellen item must NOT activate WL Kurzführung vocabulary,
    and the WL overlay must NOT translate Baustellen-specific
    Vollsperre. Overlays are scoped per ``source``, not unioned across
    operators — pins the per-(source, category) cache-key isolation."""
    def pass_through(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": text}]

    monkeypatch.setattr(
        build_feed, "_get_translation_pipeline", lambda: pass_through
    )
    # Synthetic text that contains both surface forms — they never
    # co-occur in a real item, but the test is about overlay scoping,
    # not realistic prose.
    item = cast(
        FeedItem,
        {
            "title": "Information",
            "description": "Vollsperre wegen Kurzführung der Bauphase.",
            "source": "Stadt Wien – Baustellen",
            "category": "Baustelle",
            "guid": "bau-meta-1",
            "link": "",
        },
    )
    state: dict[str, dict[str, Any]] = {}
    formatted_en = build_feed._format_item_content(
        item,
        ident="bau-meta-1",
        starts_at=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        ends_at=None,
        lang="en",
        state=state,
    )
    desc_en = formatted_en.desc_text_truncated.lower()
    # Baustellen overlay activated: Vollsperre + Bauphase translated.
    assert "vollsperre" not in desc_en
    assert "full closure" in desc_en
    assert "bauphase" not in desc_en
    assert "construction phase" in desc_en
    # WL overlay NOT activated: Kurzführung stays untouched in the
    # Baustellen context (overlay scoping prevents WL vocabulary from
    # leaking into a road-construction item).
    assert "kurzführung" in desc_en
    assert "short-running service" not in desc_en


# --- Translation-cache epoch invalidation -----------------------------
#
# EN translations are cached per disruption identity and persisted
# across builds. The cache key (title + date-range for Baustellen
# items) survives a description-only edit, so a translation computed
# once is served for the lifetime of the item. When the masking /
# glossary logic improves, the ``_TRANSLATION_CACHE_EPOCH`` guard
# evicts the stale rendering and recomputes it through the improved
# pipeline.


def _fc(title_out: str) -> "build_feed.FormattedContent":
    """Minimal FormattedContent whose only meaningful field for the
    overlay is ``title_out`` (the German title to translate)."""
    return build_feed.FormattedContent(
        guid="g",
        link="",
        title_cdata=title_out,
        desc_text_truncated="",
        desc_cdata="",
        raw_desc="",
        title_out=title_out,
        desc_html="",
    )


def test_stale_epoch_evicts_and_retranslates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the live ``Schlachthausgasse`` → "slaughterhouse
    gas" leak. A translation cached by a build with weaker masking
    (no street-suffix protection) keeps its broken rendering because
    the cache key (title + dates) survives a description-only edit.
    The epoch guard must evict the stale entry and recompute with the
    current masker, which protects the street name verbatim."""
    # Current pipeline behaviour: preserves the XENT/XGLO placeholders
    # the masker injects; only rewrites a couple of connectors.
    def fake(text: str, **kwargs: Any) -> list[dict[str, str]]:
        out = text.replace("Fahrbahn", "Roadway").replace(" bis ", " to ")
        return [{"translation_text": out}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: fake)

    ident = "stadt wien – baustellen|baustelle|L=|D=2026-03-09|T=Fahrbahn|F=abc"
    de_title = "Fahrbahn Hauptstraße von Apostelgasse bis Schlachthausgasse"
    state: dict[str, dict[str, Any]] = {
        ident: {
            "first_seen": "2026-03-09T00:00:00+00:00",
            "translations": {
                # Stale rendering from a pre-street-masking build; note
                # the ABSENT epoch key → treated as epoch 0 → stale.
                "en": {
                    "title": "Roadway Hauptstraße from Apostelgasse to slaughterhouse gas",
                },
            },
        },
    }
    out = build_feed._apply_lang_overlay(
        _fc(de_title), "", "", ident, "en", state,
        source="Stadt Wien – Baustellen", category="Baustelle",
    )
    # Stale broken rendering is gone; the street name survives verbatim.
    assert "slaughterhouse gas" not in out.title_out
    assert "Schlachthausgasse" in out.title_out
    # Cache refreshed and stamped with the current epoch.
    assert (
        state[ident]["translations"]["epoch"]
        == build_feed._TRANSLATION_CACHE_EPOCH
    )
    assert "slaughterhouse" not in state[ident]["translations"]["en"]["title"]


def test_current_epoch_cache_is_trusted_without_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A translation tagged with the current epoch is served from the
    cache verbatim — the pipeline is never invoked (proven by a fake
    that raises if called)."""
    def boom(text: str, **kwargs: Any) -> list[dict[str, str]]:
        raise AssertionError("pipeline must not be called for fresh cache")

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: boom)
    ident = "fresh-cache-1"
    state: dict[str, dict[str, Any]] = {
        ident: {
            "translations": {
                "en": {"title": "Cached English title"},
                "epoch": build_feed._TRANSLATION_CACHE_EPOCH,
            },
        },
    }
    out = build_feed._apply_lang_overlay(
        _fc("Deutscher Titel"), "", "", ident, "en", state,
    )
    assert out.title_out == "Cached English title"
    # Epoch unchanged (still current).
    assert (
        state[ident]["translations"]["epoch"]
        == build_feed._TRANSLATION_CACHE_EPOCH
    )


def test_fresh_translation_stamps_current_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-time translation stamps the current epoch so the next
    build trusts the cache instead of recomputing."""
    def fake(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: fake)
    ident = "fresh-stamp-1"
    state: dict[str, dict[str, Any]] = {}
    build_feed._apply_lang_overlay(
        _fc("U6: Betriebsstörung"), "Eine Meldung.", "", ident, "en", state,
        source="Wiener Linien", category="Störung",
    )
    assert (
        state[ident]["translations"]["epoch"]
        == build_feed._TRANSLATION_CACHE_EPOCH
    )


def test_epoch_not_stamped_when_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient pipeline failure must NOT advance the epoch — the
    item stays flagged stale so the next (healthy) build retries
    instead of locking in a half-empty cache at the current epoch."""
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    ident = "fail-epoch-1"
    state: dict[str, dict[str, Any]] = {
        ident: {"translations": {"en": {"title": "stale"}}},  # epoch 0
    }
    out = build_feed._apply_lang_overlay(
        _fc("U6: Betriebsstörung"), "Meldung", "", ident, "en", state,
    )
    # Pipeline down → atomic German fallback (base returned unchanged).
    assert out.title_out == "U6: Betriebsstörung"
    # Epoch NOT advanced — the entry remains eligible for a retry.
    assert (
        state[ident]["translations"].get("epoch", 0)
        < build_feed._TRANSLATION_CACHE_EPOCH
    )
