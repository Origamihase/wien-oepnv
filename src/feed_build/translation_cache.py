"""The EN translation cache: checks of a cached text and the language overlay.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from typing import Any

from ..feed import config as feed_config
from ..utils.logging import sanitize_log_arg

from .base import _OEBB_SOURCE, _WHITESPACE_RE, _sanitize_text, log
from .entities import _DATE_ENTITY_RE
from .placeholders import _RESIDUAL_PLACEHOLDER_RE
from .oebb_summary import _OEBB_LABELLED_SENTENCE_RE, _oebb_section_label_en
from .translator import _service_compound_defect
from .line_routes import _line_route_defect
from .en_repairs import (
    _capitalise_glossary_after_stop, _capitalise_sentence_start, _capitalise_title_body, _cause_due_to,
    _drop_unpaired_quote, _english_weekdays_capitalised,
)
from .model_pass import _leftover_placeholder
from .en_templates import _OEBB_SENTENCE_SPLIT_RE, _oebb_cannot_run_parts
from .translate_titles import _attempt_for_field
from .summary_text import _compose_description, _truncate_summary_180
from .item_meta import FormattedContent, _cdata_content
from .translation_epoch import (
    _evict_stale_translations, _record_source_digest, _record_verbatim, _source_digest, _source_digests,
    _stamp_translation_epoch, _verbatim_fields,
)

__all__ = [
    "_TIME_WORDS_DE_TO_EN", "_translate_time_line_en", "_cached_translation_defect", "_oebb_section_label_defect",
    "_oebb_cannot_run_defect", "_cached_translation", "_apply_lang_overlay",
]


# Static lookup for the German words of the bracketed ``[…]`` time line (see
# ``format_local_times``). Translating these via the ML model would be
# wasteful — every item shares the same handful of words — and slow (each
# translate call hits the tokenizer). The mapping mirrors every word
# ``format_local_times`` emits; dates pass through unchanged.
_TIME_WORDS_DE_TO_EN: dict[str, str] = {
    "Seit": "Since",
    "Bis": "Until",
    "Ab": "From",
    "Am": "On",
    "bis": "until",
    "Heute": "Today",
    "heute": "today",
    "Mo": "Mon",
    "Di": "Tue",
    "Mi": "Wed",
    "Do": "Thu",
    "Fr": "Fri",
    "Sa": "Sat",
    "So": "Sun",
}


# One word of a bracketed time line: no space, no bracket.
_TIME_LINE_WORD_RE = re.compile(r"[^\s\[\]]+")


def _translate_time_line_en(time_line: str) -> str:
    """Swap the German words of a time line for English, word by word.

    ``time_line`` is the bracketed form emitted by
    :func:`_format_item_content` — e.g. ``[Seit 05.01.]``, ``[Heute]`` or
    ``[Ab Mo 05.10. bis 11.11.]`` (``[From Mon 05.10. until 11.11.]``).
    Dates, anything else unknown and the spaces between the words
    (NO-BREAK SPACE from ``format_local_times``) pass through unchanged.
    """
    return _TIME_LINE_WORD_RE.sub(
        lambda match: _TIME_WORDS_DE_TO_EN.get(match.group(0), match.group(0)), time_line
    )


def _cached_translation_defect(source: str, cached: str) -> str | None:
    """Return why a persisted EN value must not be served, or ``None``.

    Two defects can sit in ``data/first_seen.json`` as a cached *success*:

    * a residual placeholder — the model mangled a sentinel so badly that
      the exact-nonce unmask could not restore it (2026-06-01), or the
      unmask restored it and left debris behind (:data:`_CACHED_DEBRIS_RE`);
    * a calendar date the German source carries that the cached English
      does not — the day used to be masked alone and the model dropped the
      period, giving ``on 2709.2026`` for ``am 27.09.2026`` (published
      2026-09-24 19:31, two items). Dates are verbatim entities since then
      (:data:`_DATE_ENTITY_RE`), so a retry renders them correctly; without
      this check the value would sit in the cache until the item expires
      or the epoch moves — and an epoch move re-translates every item.

    Either way :func:`_cached_translation` treats the hit as a miss.
    """
    if _leftover_placeholder(source, cached):
        return "residual placeholder"
    if set(_DATE_ENTITY_RE.findall(source)) - set(_DATE_ENTITY_RE.findall(cached)):
        return "missing or mangled date"
    return None


def _oebb_section_label_defect(source: str, cached: str) -> str | None:
    """``"section label"`` when *cached* lacks a fixed name *source* needs.

    ÖBB's section labels have fixed English names since 2026-10-23
    (``_OEBB_SECTION_LABELS_EN``); a value cached before reads "Partial
    failure:". Only such an item is translated again, not every item, as an
    epoch move would.
    """
    for sentence in _OEBB_SENTENCE_SPLIT_RE.split(source.strip()):
        labelled = _OEBB_LABELLED_SENTENCE_RE.fullmatch(sentence)
        head = _oebb_section_label_en(labelled["label"]) if labelled is not None else None
        if head is not None and f"{head}:" not in cached:
            return "section label"
    return None


def _oebb_cannot_run_defect(source: str, cached: str) -> str | None:
    """``"trains sentence"`` when *cached* lacks what the template says.

    ÖBB's "… kann … nicht fahren." is rendered from its parts since
    2026-10-25 (:func:`_oebb_cannot_run_parts`); a value cached before reads
    "… the S2 train can't drive 23360." Only such an item is translated
    again, not every item, as an epoch move would.
    """
    for sentence in _OEBB_SENTENCE_SPLIT_RE.split(source.strip()):
        parts = _oebb_cannot_run_parts(sentence)
        if parts is not None and parts.head not in cached:
            return "trains sentence"
    return None


def _cached_translation(
    text: str,
    field: str,
    ident: str,
    state: dict[str, dict[str, Any]] | None,
    *,
    source: str | None = None,
    category: str | None = None,
) -> tuple[str, bool]:
    """Return the cached EN translation of ``text`` for ``ident``/``field``.

    Returns a tuple ``(translation, succeeded)`` where ``succeeded`` is
    ``True`` only when the ML pipeline actually produced a translation
    (either fresh or from the persistent cache). On failure the German
    source is returned with ``succeeded=False`` and **nothing is
    cached** — this fixes the "Sticky German" cache-corruption bug
    where a transient pipeline failure (model not yet downloaded,
    transformers import error, OOM) would poison the cache with the
    German source text and lock the item in German forever.

    Two further drift-protection guards:

      * If the persisted "translation" is byte-identical to the
        German source, treat it as a stale fallback from an earlier
        buggy build and retry — UNLESS the field is marked verbatim
        (:data:`_VERBATIM_FIELDS_KEY`), i.e. a real model run already
        produced that identical output. Without the mark the retry can
        never converge for a text whose correct English is the German,
        such as a title made only of station names.
      * If ``state`` or ``ident`` is missing, fall through to a
        cache-less translation via :func:`_translate_text_attempt`.

    ``source`` and ``category`` forward through to
    :func:`_translate_text_attempt` for metadata-aware glossary
    layering. The translation cache key remains ``(ident, field)`` —
    item metadata is implicit in ``ident`` (one disruption belongs to
    exactly one operator), so the cache layout does not need
    additional axes.
    """
    if not text:
        return text, True  # empty input is trivially "translated"
    if state is None or not ident:
        attempt = _attempt_for_field(field)(
            text, ident=ident, source=source, category=category,
        )
        return (attempt, True) if attempt is not None else (text, False)
    entry = state.setdefault(ident, {})
    translations_raw = entry.get("translations")
    if not isinstance(translations_raw, dict):
        translations_raw = {}
        entry["translations"] = translations_raw
    en_raw = translations_raw.get("en")
    if not isinstance(en_raw, dict):
        en_raw = {}
        translations_raw["en"] = en_raw
    cached = en_raw.get(field)
    # Source-drift guard. A cached translation is only valid for the exact
    # German text it was produced from; upstream rewords live disruptions
    # under an unchanged identity. A digest that does not match — or is
    # absent, i.e. the entry predates this guard — is a MISS, so the item
    # re-translates instead of serving English for a headline the German
    # feed no longer carries. See ``_SOURCE_DIGEST_KEY``.
    stored_digest = _source_digests(translations_raw).get(field)
    if (
        isinstance(cached, str)
        and cached
        and stored_digest is not None
        and stored_digest != _source_digest(text)
    ):
        log.info(
            "Source text changed for %s/%s — re-translating (cached EN was "
            "made from a different German source).",
            sanitize_log_arg(ident),
            sanitize_log_arg(field),
        )
        cached = None
    if isinstance(cached, str) and cached and cached != text:
        defect = _cached_translation_defect(text, cached)
        if defect is None and source == _OEBB_SOURCE:
            defect = _oebb_section_label_defect(text, cached) or _oebb_cannot_run_defect(text, cached)
        if defect is None:
            defect = _service_compound_defect(
                text, cached, source=source, category=category
            )
        if defect is None:
            defect = _line_route_defect(text, cached)
        if defect is None:
            # Cached before "due to" was the one cause preposition
            # (``_cause_due_to``); the model's words are otherwise kept.
            return _english_weekdays_capitalised(_cause_due_to(cached)), True
        # Self-heal: a value persisted by an earlier build is unfit to serve
        # (see ``_cached_translation_defect``). Treat the hit as a MISS and
        # re-translate below so the defect is never served from cache.
        log.info(
            "Cache self-heal: %s in EN %s for %s; retrying.",
            defect,
            sanitize_log_arg(field),
            sanitize_log_arg(ident),
        )
    verbatim = _verbatim_fields(translations_raw)
    if isinstance(cached, str) and cached == text:
        if field in verbatim and not _RESIDUAL_PLACEHOLDER_RE.search(cached):
            # Verified verbatim: the model DID run on this text and returned
            # it unchanged, because the correct English is the German. Serving
            # the cache here is what closes the loop below.
            return cached, True
        # Stale-fallback heuristic: a prior run cached the German
        # source as the "translation" — re-attempt now that the
        # pipeline may be healthy.
        log.info(
            "Cached EN translation for %s/%s equals source; retrying.",
            sanitize_log_arg(ident),
            sanitize_log_arg(field),
        )
    attempt = _attempt_for_field(field)(
        text, ident=ident, source=source, category=category,
    )
    if attempt is None:
        # Translation failed — do NOT persist the German source as
        # the "translation". The next run gets a clean retry.
        return text, False
    en_raw[field] = attempt
    _record_verbatim(translations_raw, field, identical=attempt == text)
    # Stamp the source this translation was made from, so a later reword of
    # the same disruption is detected as a miss rather than served stale.
    _record_source_digest(translations_raw, field, text)
    return attempt, True


def _apply_lang_overlay(
    base: FormattedContent,
    summary_de: str,
    time_line_de: str,
    ident: str,
    lang: str,
    state: dict[str, dict[str, Any]] | None,
    *,
    source: str | None = None,
    category: str | None = None,
) -> FormattedContent:
    """Translate the German formatted output into English, if requested.

    Cache lookups go through :func:`_cached_translation` so each
    disruption identity is translated exactly once per lifecycle (the
    EN strings are persisted in ``state[ident]["translations"]["en"]``
    and round-trip through :func:`_save_state`).

    **Per-item atomic fallback contract**: the EN feed promises the
    same content as the DE feed, only in another language. To honour
    that promise an item must be either fully translated (title +
    summary + time-line) OR a verbatim copy of the German source — a
    mixed-language item or a "Partially translated" marker would mean
    the EN subscriber sees information the DE subscriber does not,
    which violates the content-parity contract requested by the
    operator. When ``_cached_translation`` reports a failure on
    either field the function returns ``base`` unchanged so the EN
    feed item is byte-identical to the DE item for that disruption.

    For ``lang != "en"`` the input is returned unchanged.

    ``source`` / ``category`` (normalised
    :data:`FeedItem` fields) drive metadata-aware glossary layering
    inside the translation cascade. Default ``None`` keeps the
    function callable from the existing tests that do not need to
    exercise the metadata path.

    Cache freshness: cached translations are tagged with the
    :data:`_TRANSLATION_CACHE_EPOCH` they were produced under. Stale
    entries (older epoch) are evicted up-front so a masking / glossary
    improvement is picked up on the next build instead of serving the
    old rendering for the lifetime of the item.
    """
    if lang != "en":
        return base

    # Pick up masking / glossary improvements: discard cached
    # translations produced under an older epoch so the lookups below
    # recompute them. Re-stamped at the end only when every field
    # translated successfully.
    _evict_stale_translations(ident, state)

    title_raw, title_ok = _cached_translation(
        base.title_out, "title", ident, state,
        source=source, category=category,
    )
    if summary_de:
        summary_raw, summary_ok = _cached_translation(
            summary_de, "summary", ident, state,
            source=source, category=category,
        )
    else:
        summary_raw = ""
        summary_ok = True

    if not (title_ok and summary_ok):
        log.info(
            "Translation incomplete for identity %s "
            "(title_ok=%s, summary_ok=%s) — EN feed item falls back to "
            "the German source verbatim to preserve DE↔EN content parity.",
            sanitize_log_arg(ident or "<unknown>"),
            title_ok,
            summary_ok,
        )
        return base

    # Every field translated under the current epoch — stamp it so the
    # next build trusts the cache instead of evicting and recomputing.
    _stamp_translation_epoch(ident, state)

    # Mirror the DE length contract (see ``_format_item_content`` lines
    # ~3826-3839): German→English expansion (compound nouns split into
    # several words) can push a translated title past ``TITLE_CHAR_LIMIT``
    # or a summary past the 180-char TV-screen cap. The DE strings fed into
    # translation were already capped, so re-apply both caps to the
    # translated output to keep the EN feed within the same contract.
    title_en = _sanitize_text(title_raw)
    if len(title_en) > feed_config.TITLE_CHAR_LIMIT:
        title_en = title_en[: feed_config.TITLE_CHAR_LIMIT].rstrip() + " …"
    title_en = _WHITESPACE_RE.sub(" ", title_en).strip()
    # Applied after the cap and the whitespace collapse so it acts on the
    # string that actually ships, not on an intermediate one.
    title_en = _capitalise_glossary_after_stop(
        _capitalise_title_body(_drop_unpaired_quote(title_en))
    )
    summary_en = _capitalise_glossary_after_stop(
        _capitalise_sentence_start(
            _drop_unpaired_quote(_truncate_summary_180(_sanitize_text(summary_raw)))
        )
    )
    time_line_en = _translate_time_line_en(time_line_de)
    desc_text_truncated_en, desc_html_en = _compose_description(
        summary_en, time_line_en
    )
    return FormattedContent(
        guid=base.guid,
        link=base.link,
        title_cdata=_cdata_content(title_en),
        desc_text_truncated=desc_text_truncated_en,
        desc_cdata=_cdata_content(desc_html_en),
        raw_desc=base.raw_desc,
        title_out=title_en,
        desc_html=desc_html_en,
    )
