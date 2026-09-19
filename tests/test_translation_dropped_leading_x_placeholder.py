"""A placeholder whose opening ``X`` the model dropped must not leak.

Published 2026-09-19 23:01 in ``docs/feed.en.xml``, the first build after the
C.5 epoch bump (14 → 15) forced this item to re-translate::

    N6: Buses stop NeilreichgasseENTec2b2350d0e7e61aX2X-22, QuellenstraßeENTec2b2350d0e7e61aX4X

Root cause: the German title ``N6: Busse halten Neilreichgasse 20-22,
Quellenstraße 189`` masks the bare house numbers ``20``/``22``/``189`` too —
``_LINE_ENTITY_RE`` cannot tell a house number from a one-to-three-digit line
code — so the masked text carries two placeholders back to back across a
bare hyphen with no separating whitespace::

    … XENTnnnX2X-XENTnnnX3X …

Marian detokenized that run and dropped the leading ``X`` of the second
placeholder. The nonce and index survived intact — the entity WAS
recoverable — but the shape no longer matched either existing alternative in
``_RESIDUAL_PLACEHOLDER_RE`` (both require a leading ``X``), so the
corrupted string was cached as a *successful* translation and reached
subscribers for at least three consecutive builds (23:01, and the two
cycles after).

The fix adds a third alternative anchored on the nonce's own shape (8–32
lowercase hex characters) rather than reusing the loose ``[A-Za-z0-9]*`` the
``XENT``/``XGLO`` alternatives use — a bare ``ENT``/``GLO`` is an ordinary
word fragment (``content``, ``entfernt``, …), so the pattern needs the hex
nonce immediately after it to stay unambiguous.

This does not touch ``_LINE_ENTITY_RE``'s house-number over-match: that
mask still fires, the two placeholders still sit back to back. What changes
is that a model corrupting them now fails the field and falls back to the
German source, exactly like every other unrecoverable translation — it does
not reach the feed as raw debris.

Mutations checked against this file (each one caught, by the test named):

* the new alternative is dropped from ``_RESIDUAL_PLACEHOLDER_RE`` →
  ``test_the_captured_leak_is_now_residual``, ``test_the_glossary_variant_is_also_caught``
  and ``test_cached_translation_self_heals_a_previously_leaked_value``.
* the hex-nonce anchor is loosened to ``[A-Za-z0-9]*`` (the ``XENT``/``XGLO``
  alternatives' own leniency) →
  ``test_the_hex_nonce_anchor_is_what_makes_the_new_rule_safe``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed
from src.feed_types import FeedItem

# Verbatim from the live 2026-09-19 23:01 build.
_CAPTURED_LEAK = (
    "N6: Buses stop NeilreichgasseENTec2b2350d0e7e61aX2X-22, "
    "QuellenstraßeENTec2b2350d0e7e61aX4X"
)
_CAPTURED_LEAK_SINGLE = "Buses stop QuellenstraßeENTec2b2350d0e7e61aX3X"


def test_the_captured_leak_is_now_residual() -> None:
    assert build_feed._RESIDUAL_PLACEHOLDER_RE.search(_CAPTURED_LEAK) is not None
    assert build_feed._RESIDUAL_PLACEHOLDER_RE.search(_CAPTURED_LEAK_SINGLE) is not None


def test_the_glossary_variant_is_also_caught() -> None:
    # Same shape, the glossary placeholder family (XGLO -> GLO).
    leaked = "Reason: WeatherGLOa1b2c3d4e5f6a7b8X0X applies"
    assert build_feed._RESIDUAL_PLACEHOLDER_RE.search(leaked) is not None


@pytest.mark.parametrize(
    "text",
    [
        "D: Track construction works – Althanstraße",
        "31: Demonstration – service from Wallensteinstraße",
        "The event is different from last year.",
        "This is an entity and a glossary term, content included.",
        "Recent silent entrance",
        "Vergangenes Ereignis, entfernt",
        "Line 43A/D services resume at 14X15 (a real house number, not a placeholder)",
    ],
)
def test_ordinary_prose_with_ent_or_glo_is_not_flagged(text: str) -> None:
    assert build_feed._RESIDUAL_PLACEHOLDER_RE.search(text) is None


def test_a_well_formed_placeholder_is_still_caught_by_the_original_rule() -> None:
    # Defence in depth: the new alternative must not have narrowed the two
    # existing ones. An intact, still-unmasked ``XENT...`` placeholder is a
    # residual sentinel regardless of the fix here.
    assert build_feed._RESIDUAL_PLACEHOLDER_RE.search(
        "Foo XENTec2b2350d0e7e61aX9X Bar"
    ) is not None


@pytest.mark.parametrize(
    ("glued", "expected"),
    [
        # No run at all between the word fragment and the ``X<n>X`` shape —
        # far too short to be a nonce, and a real detokenization artifact
        # always carries the nonce untouched.
        ("SilentX2X", False),
        # Four hex-looking characters: still short of the 16-character
        # ``secrets.token_hex(8)`` nonce (bounded below at 8).
        ("silent1a2bX2X", False),
        # A genuine 16-char lowercase-hex nonce: this is the real shape.
        ("silentdeadbeefdeadbeefX2X", True),
        ("EreignisGLOa1b2c3d4e5f6a7b8X0X", True),
    ],
)
def test_the_hex_nonce_anchor_is_what_makes_the_new_rule_safe(
    glued: str, expected: bool
) -> None:
    """Pins the anchor a loosened ``[A-Za-z0-9]*`` (matching the ``XENT``/
    ``XGLO`` alternatives' leniency) would not distinguish: a short or
    non-hex run between the word fragment and the ``X<n>X`` shape is
    ordinary prose, not a corrupted placeholder, and must not be flagged."""
    assert bool(build_feed._RESIDUAL_PLACEHOLDER_RE.search(glued)) is expected


# ---------------------------------------------------------------------------
# Wiring: a model that reproduces the leak fails the field, not the feed
# ---------------------------------------------------------------------------


def test_translate_text_attempt_fails_closed_on_the_leaked_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def corrupting_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": _CAPTURED_LEAK}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: corrupting_pipeline)
    result = build_feed._translate_text_attempt(
        "N6: Busse halten Neilreichgasse 20-22, Quellenstraße 189", ident="n6"
    )
    assert result is None


def test_cached_translation_self_heals_a_previously_leaked_value() -> None:
    # A prior build persisted the corrupted string as a "successful"
    # translation (the very bug this fix closes). The self-heal path must
    # treat that cache hit as a MISS on the next read, not serve it again.
    state: dict[str, dict[str, Any]] = {
        "n6": {
            "translations": {
                "epoch": build_feed._TRANSLATION_CACHE_EPOCH,
                "en": {"title": _CAPTURED_LEAK},
                "source_digest": {
                    "title": build_feed._source_digest(
                        "N6: Busse halten Neilreichgasse 20-22, Quellenstraße 189"
                    )
                },
            }
        }
    }
    out, ok = build_feed._cached_translation(
        "N6: Busse halten Neilreichgasse 20-22, Quellenstraße 189",
        "title",
        "n6",
        state,
    )
    # No pipeline is installed in this test process, so the retry itself
    # fails too — the point is that the corrupted cache value is never
    # returned as a success.
    assert out != _CAPTURED_LEAK
    assert ok is False


def test_the_item_falls_back_to_german_instead_of_leaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def corrupting_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        if "Neilreichgasse" in text:
            return [{"translation_text": _CAPTURED_LEAK}]
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: corrupting_pipeline)
    item = cast(
        FeedItem,
        {
            "title": "N6: Busse halten Neilreichgasse 20-22, Quellenstraße 189",
            "description": "Ersatzverkehr eingerichtet.",
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "n6",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="n6",
        starts_at=datetime(2026, 9, 19, 11, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 19, 21, 55, tzinfo=UTC),
        lang="en",
        state={},
    )
    # Falls back to the German source verbatim — never the raw placeholder.
    assert "ENT" not in formatted.title_cdata
    assert formatted.title_cdata == "N6: Busse halten Neilreichgasse 20-22, Quellenstraße 189"
