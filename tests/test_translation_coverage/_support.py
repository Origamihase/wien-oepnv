"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_translation_coverage.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from src import build_feed


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


# --- Translation-cache epoch invalidation -----------------------------
#
# EN translations are cached per disruption identity and persisted
# across builds. The cache key (title + date-range for Baustellen
# items) survives a description-only edit, so a translation computed
# once is served for the lifetime of the item. When the masking /
# glossary logic improves, the ``_TRANSLATION_CACHE_EPOCH`` guard
# evicts the stale rendering and recomputes it through the improved
# pipeline.


def _fc(title_out: str) -> build_feed.FormattedContent:
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


__all__ = [
    "_FAKE_DICT",
    "_fake_marian_translation",
    "_fc",
    "_isolate_translation_state",
]
