"""The model pass of one text and the placeholder debris checks.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

from ..utils.logging import sanitize_log_arg

from .base import log
from .placeholders import (
    _PLACEHOLDER_NONCE, _RESIDUAL_PLACEHOLDER_RE, _UNMASK_PLACEHOLDER_RE, _from_short_placeholders,
    _normalise_placeholder_debris, _to_short_placeholders, _with_nonce,
)
from .masking import _unmask_entities
from .en_repairs import (
    _cause_due_to, _drop_article_before_line, _drop_article_before_street, _english_weekdays_capitalised,
    _entities_dropped_by_translation, _fix_glossary_articles, _redirected_towards, _street_names_nominative,
)

__all__ = [
    "_is_non_translatable_content", "_respace_glued_placeholders", "_model_pass", "_leftover_placeholder",
]


def _is_non_translatable_content(masked_text: str) -> bool:
    """True when ``masked_text`` holds nothing the ML model needs to translate.

    After :func:`_mask_entities`, an all-entity title such as
    ``86A/87A: Wiedgasse`` becomes a run of verbatim entity placeholders joined
    by punctuation (``XENT…X1X/XENT…X2X: XENT…X0X``) — there is no German prose
    left to render. Feeding that to the NMT model is pure downside: the
    SentencePiece tokenizer can mangle the densely-packed placeholders (the
    EN-feed sentinel-leak bug) for zero translation benefit. When this returns
    ``True`` the caller skips the model and unmasks the masked text directly,
    reproducing the correct, language-neutral surface forms.

    Returns ``True`` only when — after removing every entity AND glossary
    placeholder — the remainder carries no alphabetic character. A glossary
    (``XGLO``) placeholder already maps to its English term, so unmasking alone
    renders it: ``94A: Verkehrsunfall`` masks to ``XENT…X0X: XGLO…X0X`` and
    unmasks to ``94A: traffic accident``. The model has nothing left to do
    there, but it could still mangle the two placeholders. On 2026-09-25 the
    builds at 15:53 and 16:01 did exactly that for every such title (94A, U1,
    U2, 5, 1), and those items stood in German in the EN feed. Glossary
    placeholders used to force the full pipeline on the assumption that they
    still needed translating. As soon as any German word is left, the
    remainder has letters and the model runs as before.
    """
    if not masked_text.strip():
        return False
    remaining = _STATION_MODE_MARKER_RE.sub(
        "", _UNMASK_PLACEHOLDER_RE.sub(" ", masked_text)
    )
    return not any(ch.isalpha() for ch in remaining)


# WL's interchange markers behind a stop name ("Meidling Hauptstraße U",
# "Hauptbahnhof S U", "Ottakring SU"). They are not German and stay as they
# are; counted as a word, they sent "Reason: traffic accident in the stop area
# of Meidling Hauptstraße U." to the model for nothing (EN audit 2026-10-03).
_STATION_MODE_MARKER_RE: re.Pattern[str] = re.compile(r"(?<!\w)(?:S ?U|U|S)(?!\w)")


def _respace_glued_placeholders(translated: str, masked_text: str) -> str:
    """A space between two placeholders the model glued that the source kept apart.

    Under a random nonce the model sometimes drops the space between two
    placeholders: "XENT…X1XXGLO…X0X" came back for "die Züge der Linie U6
    hält nicht in der Station" and was published as "the trains of line
    U6do not stop at", "line 13Adoes not stop atNeubaugasse" (EN probe,
    16.10.2026). A pair that stands together in the source too ("47–51" is
    three placeholders in a row) stays as it is.
    """
    matches = list(_UNMASK_PLACEHOLDER_RE.finditer(translated))
    cuts = [
        second.start()
        for first, second in pairwise(matches)
        if first.end() == second.start()
        and first.group(0) + second.group(0) not in masked_text
    ]
    for cut in reversed(cuts):
        translated = translated[:cut] + " " + translated[cut:]
    return translated


def _model_pass(
    pipe: Any,
    masked_text: str,
    mapping: dict[str, str],
    ident: str,
    nonce: str | None,
    german: str,
) -> tuple[str | None, bool]:
    """One model pass over *masked_text*, its placeholders shown under *nonce*.

    ``None`` shows them in the short form (see :data:`_SHORT_PLACEHOLDER_RE`).
    Returns the unmasked English and ``False``; ``None`` and ``True`` when the
    model mangled or dropped a placeholder, which another pass may avoid; and
    ``None`` and ``False`` on any other failure. The masks and *mapping* keep
    the build's nonce: only the model sees *nonce*, and its intact placeholders
    are mapped back before anything is checked or unmasked. A placeholder the
    model mangled matches neither form and fails the residual check, which
    judges the pass by the same rule as a cached value
    (:func:`_leftover_placeholder` against the German *german*).
    """
    shown = (
        _to_short_placeholders(masked_text)
        if nonce is None
        else _with_nonce(masked_text, _PLACEHOLDER_NONCE, nonce)
    )
    try:
        # ``truncation=True`` enforces the model's input cap (512 tokens
        # for opus-mt-de-en) BEFORE Marian asserts and crashes the
        # whole feed build. Without it, a single long disruption text
        # would abort the EN-feed pass for every item that follows.
        result = pipe(shown, max_length=512, truncation=True)
    except Exception as exc:
        log.warning(
            "Translation failed for identity %s — pipeline raised %s: %s",
            sanitize_log_arg(ident or "<unknown>"),
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return None, False
    translated = _translation_text(result, ident)
    if translated is None:
        return None, False
    translated = _respace_glued_placeholders(
        _normalise_placeholder_debris(
            _from_short_placeholders(translated)
            if nonce is None
            else _with_nonce(translated, nonce, _PLACEHOLDER_NONCE)
        ),
        masked_text,
    )
    invented = sorted(
        {m.group(0) for m in _UNMASK_PLACEHOLDER_RE.finditer(translated)} - mapping.keys()
    )
    if invented:
        # An intact placeholder the source never had (the model shifted an
        # index): the unmask would delete it silently, losing a word nobody
        # can name. Same verdict as a mangled one.
        log.warning(
            "Translation for identity %s carries %d placeholder(s) the source "
            "does not have under nonce %s; discarding this pass.",
            sanitize_log_arg(ident or "<unknown>"),
            len(invented),
            nonce or "<kurz>",
        )
        return None, True
    dropped = _entities_dropped_by_translation(masked_text, translated, mapping)
    if dropped:
        # The sentence that came back is missing a station, a line or a house
        # number the source carried. Discarding it costs an English rendering;
        # keeping it ships a confident statement of the wrong fact. Same
        # verdict and same fallback as the residual-sentinel check below.
        log.warning(
            "Translation for identity %s dropped %d verbatim entit%s (%s) "
            "under nonce %s; discarding this pass.",
            sanitize_log_arg(ident or "<unknown>"),
            len(dropped),
            "y" if len(dropped) == 1 else "ies",
            sanitize_log_arg(", ".join(sorted(dropped)[:5])),
            nonce or "<kurz>",
        )
        return None, True
    unmasked = _english_weekdays_capitalised(_cause_due_to(_redirected_towards(
        masked_text,
        _drop_article_before_street(
            _drop_article_before_line(
                _fix_glossary_articles(
                    _unmask_entities(translated, _street_names_nominative(mapping)),
                    mapping,
                )
            )
        ),
    )))
    if _leftover_placeholder(german, unmasked):
        # The model mangled a placeholder so badly the exact-nonce unmask could
        # not restore it (dropped/translated nonce chars, lower-cased prefix,
        # truncated index, a lost prefix leaving nonce or index glued to a
        # word). Treat the pass as FAILED: after a second failed pass the
        # caller does not cache it and falls back to the German source — a
        # raw sentinel must never reach subscribers. The rule is the one the
        # cache applies (``_cached_translation_defect``): before 2026-10-10 a
        # pass was judged by ``_RESIDUAL_PLACEHOLDER_RE`` alone, so a shape
        # only the debris rule sees ("Nord8d74459316abX5X") was published
        # and cached, and left the cache only on the next build.
        log.warning(
            "Translation for identity %s left a residual placeholder under "
            "nonce %s; discarding this pass.",
            sanitize_log_arg(ident or "<unknown>"),
            nonce or "<kurz>",
        )
        return None, True
    return unmasked, False


def _translation_text(result: Any, ident: str) -> str | None:
    """The ``translation_text`` of a pipeline *result*, ``None`` when unusable."""
    if not isinstance(result, list) or not result:
        log.warning(
            "Translation failed for identity %s — empty/invalid result shape.",
            sanitize_log_arg(ident or "<unknown>"),
        )
        return None
    first = result[0]
    if not isinstance(first, dict):
        log.warning(
            "Translation failed for identity %s — result[0] not a dict.",
            sanitize_log_arg(ident or "<unknown>"),
        )
        return None
    translated = first.get("translation_text")
    if not isinstance(translated, str) or not translated.strip():
        log.warning(
            "Translation failed for identity %s — translator returned empty text.",
            sanitize_log_arg(ident or "<unknown>"),
        )
        return None
    return translated


# What a placeholder leaves behind once its unmask half-worked: an ``X`` or a
# repeated index glued to a word ("Franz-Josefs-Bahnhof0X", "traffic
# accidentX", "Line 18X") or a prefix the model mangled ("XGLABc73c…X0X").
# :data:`_PLACEHOLDER_DEBRIS_RE` repairs fresh output since 2026-10-01, but
# values cached before kept the debris and were served from the cache:
# ``docs/feed.en.xml`` still read "between Wien Franz-Josefs-Bahnhof0X and
# St.Andrä-Wördern" on 2026-10-02, with 44 such fields in the cache (fund D).
# An ``X`` counts only behind a lowercase letter or a digit, so "REX" stays
# alone, and only when the German source does not carry the same token.
#
# Since 2026-10-10 the same rule judges every fresh model pass
# (:func:`_leftover_placeholder`), and two more shapes count. Both are what a
# placeholder leaves when the model drops its opening ``X`` or its prefix,
# in either form a pass shows it (``XGLO0X`` or ``XGLO<nonce>X0X``):
#
# * the prefix letters glued to the index, ``NordGLO0X`` or ``XGLO0``;
# * a nonce fragment, six or more lowercase hex characters with at least one
#   digit and one letter: "Nord8d74459316abX5X", or "Nord8d74459316ab" once
#   the index went too (EN check 2026-10-08).
#
# A glossary placeholder (``XGLO``) that the model loses this way is counted
# by no other check (``_entities_dropped_by_translation`` counts ``XENT``
# only), so before 2026-10-10 the leftover was published and cached and left
# the cache on the next build. Every match of either shape in the 3,003 DE/EN
# pairs the feed published from July to 2026-10-08 and in the 5,151 cached
# EN fields of 2026-10-10 was a leaked placeholder, never a word: no German
# or English word runs "ent"/"glo" into a digit, no word is a lowercase hex
# run with a digit in it, and a line code is upper case ("44A").
_CACHED_DEBRIS_RE: re.Pattern[str] = re.compile(
    r"[A-Za-zÄÖÜäöüß0-9]*(?:[a-zäöüß]|\d)X(?![A-Za-z0-9])|XG[A-Za-z]{2}[0-9a-f]{8,}\w*"
    r"|[A-Za-zÄÖÜäöüß]*(?i:ent|glo)\d+X?"
    r"|(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{6,}"
)


def _placeholder_debris(source: str, cached: str) -> bool:
    """Whether *cached* carries placeholder debris the German *source* does not."""
    return any(match.group(0) not in source for match in _CACHED_DEBRIS_RE.finditer(cached))


def _leftover_placeholder(source: str, english: str) -> bool:
    """Whether *english* carries a placeholder or its debris the German *source* does not.

    One rule for a fresh model pass (:func:`_model_pass`) and a cached value
    (:func:`_cached_translation_defect`), so the cache never has to heal what
    a pass let through.
    """
    return bool(_RESIDUAL_PLACEHOLDER_RE.search(english)) or _placeholder_debris(source, english)
