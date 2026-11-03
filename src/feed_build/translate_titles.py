"""Translation of whole texts and titles on top of ``_translate_text_attempt``.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

from collections.abc import Callable

from .display_titles import _split_reason_title
from .translate_run import _translate_text_attempt

__all__ = [
    "_translate_text", "_translate_title_attempt", "_attempt_for_field",
]


def _translate_text(
    text: str,
    ident: str = "",
    *,
    source: str | None = None,
    category: str | None = None,
) -> str:
    """Translate ``text`` from German to English; fall back to the source on failure.

    Thin wrapper around :func:`_translate_text_attempt` that converts
    the ``None`` failure signal into a safe-fallback string return.
    Kept as a public-style helper so callers that do not need to
    distinguish success from fallback have a single-string API; the
    cache-aware path inside :func:`_cached_translation` uses
    :func:`_translate_text_attempt` directly so a failed translation
    is NEVER cached as the canonical English text.

    ``source`` and ``category`` forward through to
    :func:`_translate_text_attempt` for metadata-aware glossary
    layering; default ``None`` preserves backward compatibility.
    """
    if not text or not text.strip():
        return text
    attempt = _translate_text_attempt(
        text, ident=ident, source=source, category=category,
    )
    return attempt if attempt is not None else text


def _translate_title_attempt(
    text: str,
    ident: str = "",
    *,
    source: str | None = None,
    category: str | None = None,
) -> str | None:
    """Translate a title; the C.5 reason/fragment halves go through the model apart.

    The en dash is a preserved symbol (:data:`_PRESERVED_SYMBOLS_RE`), so
    ``31: Demonstration – Betrieb ab Wallensteinstraße`` would reach the
    model as ``31: Demonstration XENT…X1X Betrieb ab XENT…X2X`` — a
    placeholder wedged between two words. Published 2026-09-19 21:30 in
    ``docs/feed.en.xml``: the model re-emitted it with debris,
    ``31: Demonstration –Xservice from Wallensteinstraße``, and mangled
    three more such titles into residuals that failed their items back to
    German. Translating ``31: Demonstration`` and ``Betrieb ab
    Wallensteinstraße`` on their own gives the model the two texts it
    handled before the separator existed; the dash is re-inserted verbatim
    and never travels. Either half failing fails the title, as before.
    """
    halves = _split_reason_title(text)
    if halves is None:
        return _translate_text_attempt(text, ident=ident, source=source, category=category)
    head_en = _translate_text_attempt(halves[0], ident=ident, source=source, category=category)
    if head_en is None:
        return None
    tail_en = _translate_text_attempt(halves[1], ident=ident, source=source, category=category)
    if tail_en is None:
        return None
    return f"{head_en.rstrip()} – {tail_en.lstrip()}"


def _attempt_for_field(field: str) -> Callable[..., str | None]:
    """The translation attempt a cached field goes through: titles may split."""
    if field == "title":
        return _translate_title_attempt
    return _translate_text_attempt
