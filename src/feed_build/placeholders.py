"""Placeholder formats, their nonce and the domain glossary.

This module is the only owner of ``_PLACEHOLDER_NONCE``; every pattern built
from it is built here.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
import secrets
from functools import lru_cache
from typing import Any

from ..feed.glossary import _GLOSSARY_BASE, _GLOSSARY_BY_CATEGORY, _GLOSSARY_BY_SOURCE
from ..utils.name_letters import latin_names

__all__ = [
    "_resolve_glossary", "_norm_metadata", "_placeholder_nonce", "_PLACEHOLDER_NONCE",
    "_ENTITY_PLACEHOLDER_FORMAT", "_ENTITY_PLACEHOLDER_RE", "_HYPHEN_PREFIX_RE", "_SLASH_PARTNER_AFTER_RE",
    "_SLASH_NAME_GOES_ON_RE", "_SLASH_NAME_CAME_BEFORE_RE", "_SLASH_PARTNER_BEFORE_RE", "_WIEN_NAME_RE",
    "_GLOSSARY_PLACEHOLDER_FORMAT", "_UNMASK_PLACEHOLDER_RE", "_RESIDUAL_PLACEHOLDER_RE", "_MASK_WORD_CHAR_RE",
    "_normalise_placeholder_debris", "_apply_domain_glossary", "_with_nonce", "_has_short_placeholder_shape",
    "_to_short_placeholders", "_from_short_placeholders",
]


# ÖPNV domain glossary: the tables (``_GLOSSARY_BASE``, the overlays
# ``_GLOSSARY_BY_SOURCE`` / ``_GLOSSARY_BY_CATEGORY`` and
# ``_FIELD_LABEL_EN``) and the reasoning behind them live in
# ``src/feed/glossary.py`` since 2026-10-15; the functions that apply them
# stay below, so every test mock on ``build_feed`` keeps working.


@lru_cache(maxsize=32)
def _resolve_glossary(
    source: str | None, category: str | None
) -> dict[str, str]:
    """Return the merged glossary for a given ``(source, category)``.

    Layering order: base → source overlay → category overlay. Later
    overlays override earlier entries with the same key. Unknown
    ``source`` / ``category`` values degrade silently to the base
    layer — a typo in the feed's source string MUST NOT abort the EN
    build.

    Caches up to 32 unique combinations; today's catalogue is ~4
    sources × ~3 categories + the ``(None, None)`` legacy entry, well
    under the cap. The returned dict is INTERNAL cached state —
    callers MUST treat it as read-only (only ``.get()`` and ``.keys()``
    usages exist today).
    """
    merged: dict[str, str] = dict(_GLOSSARY_BASE)
    if source:
        merged.update(_GLOSSARY_BY_SOURCE.get(source, {}))
    if category:
        merged.update(_GLOSSARY_BY_CATEGORY.get(category, {}))
    return merged


@lru_cache(maxsize=32)
def _domain_glossary_pattern(
    source: str | None, category: str | None
) -> re.Pattern[str]:
    """Compile the case-insensitive, longest-first regex for the
    active glossary derived from ``(source, category)``.

    Cached per metadata combination so the regex compile work is paid
    once per unique combo, not per feed item. The regex matches any
    key from the active layered glossary; the actual surface →
    English mapping is resolved at match time inside
    :func:`_apply_domain_glossary` via :func:`_resolve_glossary`.

    Longest-first sort is critical: ``Schadhaftem Fahrzeug`` must beat
    the single-word ``Schadhaftem`` alternation so the model sees one
    placeholder, not two.
    """
    glossary = _resolve_glossary(source, category)
    sorted_terms = sorted(glossary.keys(), key=len, reverse=True)
    if not sorted_terms:
        # Defensive: empty glossary should never happen because the
        # base layer is always populated. Return a never-match pattern
        # rather than letting ``re.compile("(?<!\\w)(?:)(?!\\w)")``
        # produce ill-defined alternation.
        return re.compile(r"(?!)")
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(re.escape(term) for term in sorted_terms)
        + r")(?!\w)"
    )
    return re.compile(pattern, re.IGNORECASE)


def _norm_metadata(value: Any) -> str | None:
    """Normalise a ``FeedItem`` source/category field for metadata
    lookup.

    Returns ``None`` for non-string or whitespace-only input so the
    caller can treat "no metadata" uniformly. Trims edge whitespace
    so a provider that emits ``" Wiener Linien "`` still matches the
    canonical key in :data:`_GLOSSARY_BY_SOURCE`.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


# Placeholder pattern recognised by :func:`_unmask_entities` and the
# ``X``-bookended form deliberately avoids ``_`` (which the
# SentencePiece tokenizer used by Marian models treats specially).
#
# A per-process random hex nonce is embedded between the ``XENT``/``XGLO``
# prefix and the index so a placeholder can never collide with a token of
# the same *shape* that an upstream (Zero-Trusted) title/description happens
# to carry. Without it, a crafted source token such as ``XENT0X`` either
# collided with a generated placeholder and was rewritten to that entity's
# surface form on unmask, or matched the unmask sweep and was deleted
# (``mapping.get(ph, "")``) — corrupting the EN feed body. The nonce makes
# both impossible (the source cannot predict it) while staying ``_``-free
# for SentencePiece; the literal ``X`` separator before the index keeps the
# regex unambiguous and leaves any old/foreign ``XENT<digits>X`` unmatched.
#
# A nonce is drawn again until it holds no run of six digits or six letters.
# What the model leaves of a placeholder whose prefix and index it dropped is
# a bare piece of the nonce ("Nord8d74459316ab"), and the leftover rule sees
# such a piece by its shape: six or more hex characters with a digit and a
# letter among them (:data:`_CACHED_DEBRIS_RE`). ``secrets.token_hex(8)``
# alone gives an all-digit nonce about once in 1,800 draws (10/16 to the 16th
# power); a mangled retry under it left a bare number no check saw, and
# ``test_a_mangled_nonce_placeholder_never_leaks`` failed in that run. With
# the redraw every piece of six or more characters carries both a digit and
# a letter, so the shape rule holds for each nonce instead of for most.
_NONCE_UNMIXED_RUN_RE: re.Pattern[str] = re.compile(r"[0-9]{6}|[a-f]{6}")


def _placeholder_nonce() -> str:
    """A fresh placeholder nonce: 16 hex characters, no six of one kind in a row."""
    while True:
        nonce = secrets.token_hex(8)
        if not _NONCE_UNMIXED_RUN_RE.search(nonce):
            return nonce


_PLACEHOLDER_NONCE = _placeholder_nonce()
_ENTITY_PLACEHOLDER_FORMAT = "XENT" + _PLACEHOLDER_NONCE + "X{index}X"
_ENTITY_PLACEHOLDER_RE: re.Pattern[str] = re.compile("XENT" + _PLACEHOLDER_NONCE + r"X\d+X")

# A name an earlier pass shielded only in part, because a capitalised word
# joined by a hyphen stands in front: "Unter-Meidlinger Straße" (the street
# pass took "Meidlinger Straße"), "Neu-Leopoldau", "Groß-Enzersdorfer Straße".
# The model saw the prefix alone and wrote "Under-Meidlinger Straße" (WL 7B,
# 29.10.2026). Glued to the name, the prefix belongs to it.
_HYPHEN_PREFIX_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?<![\w-])(?:[A-ZÄÖÜ][a-zäöüß]+-)+(?=" + _ENTITY_PLACEHOLDER_RE.pattern + r")"
))

# A stop WL names as a pair, "Parlament/Volkstheater", "Schmerlingplatz/
# Volkstheater": when one half is a typo the directory does not know, only
# the other half was shielded and the model read the rest as prose
# ("Betrieb ab Parlament/ Volkstheter" -> "service from Parlament/
# Folktheter", WL 2, 30.10.2026). A capitalised word glued by a slash to a
# shielded name belongs to that stop's name and stays as WL wrote it.
_SLASH_PARTNER_WORD = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]*[A-Za-zÄÖÜäöüß]"
_SLASH_PARTNER_AFTER_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?P<name>" + _ENTITY_PLACEHOLDER_RE.pattern + r")/(?P<gap> ?)"
    r"(?P<word>(?!XENT|XGLO)" + _SLASH_PARTNER_WORD + r")(?![\w/-])"
))
# The name goes on behind the half ("Sankt Marx") or came before it
# ("Spittelau Bahnhst/…"): a capitalised word or a shielded name next to it.
_SLASH_NAME_GOES_ON_RE: re.Pattern[str] = re.compile(latin_names(r" (?:[A-ZÄÖÜ]|XENT)"))
_SLASH_NAME_CAME_BEFORE_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?:[A-ZÄÖÜ][\w.-]*|" + _ENTITY_PLACEHOLDER_RE.pattern + r") $"
))
_SLASH_PARTNER_BEFORE_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?<![\w./-])(?P<word>(?!XENT|XGLO)" + _SLASH_PARTNER_WORD + r")/(?P<gap> ?)"
    r"(?P<name>" + _ENTITY_PLACEHOLDER_RE.pattern + r")"
))

# "Wien" as part of a stop's name: "Wien Schedifkaplatz" (Badner Bahn),
# "Wien St.Marx Bahnhst", "Purkersdorf b.Wien Zentrum Bahnhst",
# "Hennersdorf b.Wien Bahnhst". The directory knows none of these forms, so
# only the street half (or nothing) was shielded and the model turned the
# city into English: "no service Vienna Schedifkaplatz – Wien Matzleinsdorfer
# Platz" (probe 2026-10-15), "between Vienna St.Marx Bahnhst" (2026-09-07),
# "Vienna centre Bahnhst" (2026-08-15). A name stays a name in the EN feed,
# as on the stop sign, so "Wien" directly in front of a name, or behind
# ÖBB's "b." ("bei"), is shielded together with that name. "Wien" on its own
# ("Fahrtrichtung Wien", "Großraum Wien") is the city and still becomes
# "Vienna". The station nouns after the name are no part of it; a masked
# brand ("Wien Stammstrecke") stays a brand of its own.
_WIEN_NAME_STATION_NOUNS = r"(?:Bahnhof|Bahnhst|Bahnst|Bhf|Bf|Hbf|Haltestelle|Station)\b"
_WIEN_NAME_WORD = (
    r"(?!" + _WIEN_NAME_STATION_NOUNS + r")(?!XENT|XGLO)"
    r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]*(?:\.[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+)*(?![\w-])"
)
_WIEN_NAME_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?<![\w.-])(?:(?:(?:" + _ENTITY_PLACEHOLDER_RE.pattern + r"|(?!XENT|XGLO)[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*) )?"
    r"b\.\s?Wien(?: " + _WIEN_NAME_WORD + r")?(?![\w-])"
    r"|Wien (?:" + _ENTITY_PLACEHOLDER_RE.pattern + r"|" + _WIEN_NAME_WORD + r"))"
))


# Glossary placeholders use a distinct ``XGLO<n>X`` prefix so the
# combined unmask regex can tell them apart from the verbatim
# ``XENT<n>X`` placeholders emitted by :func:`_mask_entities`. The
# unmasker handles both via a single union regex (see
# :data:`_UNMASK_PLACEHOLDER_RE`).
_GLOSSARY_PLACEHOLDER_FORMAT = "XGLO" + _PLACEHOLDER_NONCE + "X{index}X"

# Unified regex for the unmasker — matches both entity (``XENT…``) and glossary
# (``XGLO…``) placeholders. Built from ``_ENTITY_PLACEHOLDER_RE.pattern`` (plus
# the sibling glossary shape) so the entity regex, the entity format, and this
# combined sweep stay in lock-step from a single source of truth. Defined at
# module import time so :func:`_unmask_entities` does not pay the compile cost
# per call.
_UNMASK_PLACEHOLDER_RE: re.Pattern[str] = re.compile(
    _ENTITY_PLACEHOLDER_RE.pattern + "|XGLO" + _PLACEHOLDER_NONCE + r"X\d+X"
)

# Nonce-AGNOSTIC detector for placeholders that survived the unmask step
# because the NMT model MANGLED them. Observed corruptions in cached EN output
# (``data/first_seen.json``): a dropped hex char in the nonce, the prefix
# lower-cased (``XGLO`` → ``XGLo``), German-looking nonce fragments translated
# to English (a stray ``from`` / ``…de…`` → ``…en…``) and a truncated index.
# Each of these defeats the EXACT-nonce :data:`_UNMASK_PLACEHOLDER_RE`, so the
# token leaks into the feed verbatim. Unlike that sweep, this pattern does NOT
# pin the per-process nonce: it matches the ``XENT``/``XGLO`` prefix
# (case-insensitively, to catch the lower-cased form) followed by an arbitrary
# alphanumeric nonce body — ``[A-Za-z0-9]`` rather than hex so a *translated*
# nonce fragment still matches — and the literal ``X`` index separator. That
# trailing ``X`` is what separates a leaked sentinel from a real German word
# beginning with the same letters (e.g. ``Xentenplatz`` has no such ``X``),
# keeping the false-positive rate effectively zero on ÖPNV text (every station,
# line and operator is masked before translation). Used as a post-unmask
# safety net and a cache-hit self-heal trigger.
# The second alternative catches the SEVERE corruption in which the model
# dropped the prefix AND the nonce and kept only the bookended index — the
# ÖBB item ``S 60: Wien Meidling ↔ Wien Hauptbahnhof ↔X4X Wien Stadlau``
# published on 2026-09-05 (see ``docs/archive/audits/audit-2026-09-05.md`` §5).
# The prefix-anchored alternative above cannot see it, so the bare sentinel
# was cached and served to subscribers. The bare form is bounded by
# non-alphanumeric lookarounds and a short index so it cannot match inside a
# real word or a line code (``X`` is not a Vienna/ÖBB line letter, and every
# station, line and operator token is entity-masked before translation).
_RESIDUAL_PLACEHOLDER_RE: re.Pattern[str] = re.compile(
    r"(?:XENT|XGLO)[A-Za-z0-9]*X"
    r"|(?<![A-Za-z0-9])X\d{1,3}X(?![A-Za-z0-9])"
    # A placeholder whose OPENING ``X`` the model dropped, leaving a bare
    # ``ENT``/``GLO`` glued straight onto the previous word.
    #
    # Published 2026-09-19 23:01, ``docs/feed.en.xml``, first build after the
    # C.5 epoch bump (14 → 15) forced a re-translation::
    #
    #     N6: Buses stop NeilreichgasseENTec2b2350d0e7e61aX2X-22, …
    #
    # Root cause: ``Neilreichgasse 20-22`` masks to
    # ``XENT…X0X XENT…X2X-XENT…X3X`` — ``_LINE_ENTITY_RE`` treats the bare
    # house numbers ``20``/``22`` as line-shaped and masks them too, so two
    # placeholders sit back to back across a bare hyphen with NO separating
    # whitespace. Marian detokenized the pair and dropped the leading ``X``
    # of the second one; the result kept its nonce and index, so the entity
    # WAS recoverable, but the shape no longer matched either alternative
    # above (neither starts with ``X``) and the corrupted string reached
    # subscribers.
    #
    # Anchored on the nonce's own shape (8–32 lowercase hex chars) rather
    # than on ``[A-Za-z0-9]*`` like the sibling alternatives above: unlike
    # ``XENT``/``XGLO``, a bare ``ENT``/``GLO`` is an ordinary word fragment
    # (``content``, ``Ereignis`` after casefolding, …), so the pattern needs
    # the hex nonce immediately after it to stay effectively unambiguous.
    r"|(?:ENT|GLO)[0-9a-f]{8,32}X\d{1,3}X",
    re.IGNORECASE,
)


# A placeholder the model re-emitted with its closing ``X`` doubled.
#
# Published 2026-09-18, ``docs/feed.en.xml``::
#
#     DE: Haltestellenverlegung der Linien 12A und 14A …
#     EN: Stop transfer of lines 12A and 14AX …
#
# ``14AX`` is not a Vienna line. The model returned
# ``XENT<nonce>X4XX``; :data:`_UNMASK_PLACEHOLDER_RE` matched the valid
# ``…X4X`` prefix, restored ``14A``, and left the surplus ``X`` glued to it.
# :data:`_RESIDUAL_PLACEHOLDER_RE` cannot see it either — the leftover is a
# bare ``X`` with no prefix and no index, so neither alternative matches. The
# corruption is therefore invisible to both existing guards and reached
# subscribers for five consecutive builds.
#
# Repaired rather than rejected: the placeholder itself survived intact, so
# the entity is recoverable and the translation is otherwise sound. Falling
# back to German here would throw away a good sentence over one stray
# character.
#
# The trailing run is only absorbed when a non-alphanumeric follows, which is
# what keeps two adjacent placeholders safe: in
# ``…X4XXENT<nonce>X5X`` the ``X`` that opens the next placeholder is
# followed by ``ENT``, so the lookahead fails and the pair is left alone.
#
# The model also repeats a placeholder's index. Published 2026-09-30 16:01
# to 2026-10-01 11:01, ``docs/feed.en.xml``, 35 versions::
#
#     DE: zwischen Wien Franz-Josefs-Bahnhof und St.Andrä-Wördern Bahnhof …
#     EN: between Wien Franz-Josefs-Bahnhof0X and St.Andrä-Wördern …
#
# and once "Line 10: service obstruction0X towards Unter St. Veit". Both
# placeholders carry index 0 (``XENT…X0X``, ``XGLO…X0X``); the model
# returned ``…X0X0X``, the unmask restored ``…X0X`` and left ``0X`` behind,
# which no guard sees. Only the placeholder's own index is absorbed — the
# shape both cases show.
_PLACEHOLDER_DEBRIS_RE: re.Pattern[str] = re.compile(
    "((?:XENT|XGLO)" + _PLACEHOLDER_NONCE + r"X(\d+)X)(?:X+|(?:\2X)+)(?![A-Za-z0-9])"
)

# An ``X`` the model put IN FRONT of a placeholder, in place of the space.
#
# Published 2026-10-02 23:42, ``docs/feed.en.xml``, item 2 of 10::
#
#     DE: … Züge halten in Schleife, Wipplingerstr 39; Betrieb ab Augasse, Börse.
#     EN: … Trains keep in loop, WipplingerstrX39; service fromXAugasse, Börse.
#
# Each stray ``X`` sits exactly where the German has a space before a
# masked entity: the model returned ``…X0XXXENT…X1X`` and
# ``fromXXENT…X2X``. The unmask restores both placeholders and leaves the
# surplus ``X`` glued between them; :data:`_PLACEHOLDER_DEBRIS_RE` only
# looks AFTER a placeholder and :data:`_RESIDUAL_PLACEHOLDER_RE` only sees
# a prefix or an index, so both guards pass it.
#
# Repaired like its sibling: the stray run becomes the space it replaced
# when a word or placeholder precedes it, and vanishes otherwise. The
# lookbehinds keep the CLOSING ``X`` of a preceding placeholder (``X0X``)
# out of the match, so only the surplus is touched.
_PLACEHOLDER_LEADING_X_RE: re.Pattern[str] = re.compile(
    r"(?<!X\d)(?<!X\d\d)(?<!X\d\d\d)X+"
    r"(?=(?:XENT|XGLO)" + _PLACEHOLDER_NONCE + r"X\d+X)"
)

# Any word character. Used to tell a verbatim entity that MUST survive
# translation (``14A``, ``Justgasse``, the house number ``2``) from a masked
# punctuation glyph (``…``, ``–``) that the model may legitimately drop.
_MASK_WORD_CHAR_RE: re.Pattern[str] = re.compile(r"\w")


def _leading_x_replacement(match: re.Match[str]) -> str:
    start = match.start()
    return " " if start and match.string[start - 1].isalnum() else ""


def _normalise_placeholder_debris(text: str) -> str:
    """Strip the surplus ``X`` or repeated index from placeholders the model doubled.

    Also turns an ``X`` glued in front of a placeholder back into the space
    it replaced (see :data:`_PLACEHOLDER_LEADING_X_RE`).

    Idempotent, so it is safe to apply on both the translation path and
    inside :func:`_unmask_entities`.
    """
    text = _PLACEHOLDER_LEADING_X_RE.sub(_leading_x_replacement, text)
    return _PLACEHOLDER_DEBRIS_RE.sub(r"\1", text)


def _apply_domain_glossary(
    text: str,
    *,
    source: str | None = None,
    category: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Substitute ÖPNV-domain German terms with placeholders that
    resolve to their canonical English equivalents.

    The Helsinki opus-mt-de-en model has only seen everyday German
    prose during training; it routinely mistranslates Austrian
    transit jargon because the closest token in its vocabulary lives
    in a different domain. Live regressions on the public feed
    (2026-05-22):

      * ``Betriebsstörung``        → "Harmful vehicle"
      * ``Fahrtbehinderung``       → "Disability"
      * ``Aufgelassen``            → "Open" (opposite meaning!)
      * ``Hauptfahrbahn``          → "main runway"

    This helper short-circuits the mistranslation: each known DE
    term is replaced by a stable ``XGLO<n>X`` placeholder whose
    mapping entry carries the *English* equivalent. After the model
    finishes translating the (now jargon-free) surrounding prose,
    :func:`_unmask_entities` substitutes the English term back in.

    The mask/unmask round-trip is therefore **DE → EN** for glossary
    entries (unlike :func:`_mask_entities` which is strictly
    identity). The two operations compose in
    :func:`_translate_text_attempt`.

    Metadata-driven layering: when ``source`` and/or ``category`` are
    passed in (typically extracted from the ``FeedItem`` by
    :func:`_format_item_content`), the active glossary is the merger
    of :data:`_GLOSSARY_BASE` with the matching overlays in
    :data:`_GLOSSARY_BY_SOURCE` / :data:`_GLOSSARY_BY_CATEGORY`. The
    overlay broadens coverage for vocabulary that is too narrow to
    apply universally (``Aufzug`` should only resolve to "elevator"
    for Wiener Linien items; ``Vollsperre`` only for Stadt Wien
    Baustellen items). Without metadata the function uses only the
    base layer — preserves backward compatibility for callers that
    do not care about per-item context.
    """
    if not text:
        return text, {}

    glossary = _resolve_glossary(source, category)
    pattern = _domain_glossary_pattern(source, category)

    mapping: dict[str, str] = {}
    surface_to_placeholder: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        surface = match.group(0)
        en_term = glossary.get(surface)
        if en_term is None:
            # Case-insensitive fallback (the glossary pattern matches
            # ``betriebsstörung`` at sentence start; the dict keys are
            # capitalised because that is how WL and ÖBB publish the
            # term in 99 % of items). Small dict, linear scan is fine.
            for key, value in glossary.items():
                if key.lower() == surface.lower():
                    en_term = value
                    break
        if en_term is None:
            # Pattern matched but resolution failed — return the
            # surface unchanged so no placeholder dangles in the
            # output. Defensive only; should not happen given the
            # pattern is built from the active glossary's keys.
            return surface
        cached = surface_to_placeholder.get(surface)
        if cached is not None:
            return cached
        placeholder = _GLOSSARY_PLACEHOLDER_FORMAT.format(index=len(mapping))
        mapping[placeholder] = en_term
        surface_to_placeholder[surface] = placeholder
        return placeholder

    processed = pattern.sub(_replace, text)
    return processed, mapping


def _with_nonce(text: str, old: str, new: str) -> str:
    """*text* with every intact ``old``-nonce placeholder under nonce *new*."""
    if old == new:
        return text
    return re.sub("(XENT|XGLO)" + re.escape(old) + r"(X\d+X)", r"\g<1>" + new + r"\g<2>", text)


# What the model sees of a placeholder: ``XENT3X``, without the nonce.
#
# The nonce makes a placeholder some ten SentencePiece pieces of random hex,
# and WL's short texts are mostly placeholders ("XGLO…X0X der Linien XENT…X1X
# in Richtung XENT…X0X U bzw. XENT…X2X XENT…X3X"). Marian copies that badly:
# measured with the real model on all 243 distinct model inputs of the feed
# from 2026-09-20 to 2026-10-06, 227 of 972 passes under random nonces (23 %)
# mangled or dropped a placeholder, and the same 243 inputs under the short
# form lost none; over all 814 inputs since 2026-07-01, one pass each, 167
# against 8. A text that fails both passes puts the German item into the EN
# feed for a tick: nine items since 2026-10-04, the latest 66A/N66 at 17:23
# on 2026-10-06 ("left a residual placeholder" under both nonces).
#
# The nonce still guards the masks, the mapping and the unmask: only the
# model's copy is short, and its placeholders are mapped back to the build's
# nonce before anything is checked. A source text that already carries the
# short shape (``XENT0X``) would be mapped back as one of ours, so such a text
# is shown under the nonce as before (``_has_short_placeholder_shape``).
_SHORT_PLACEHOLDER_SHAPE_RE: re.Pattern[str] = re.compile(
    r"(?:XENT|XGLO)\d+X", re.IGNORECASE
)
_FULL_PLACEHOLDER_RE: re.Pattern[str] = re.compile(
    "(XENT|XGLO)" + _PLACEHOLDER_NONCE + r"X(\d+)X"
)
_SHORT_PLACEHOLDER_RE: re.Pattern[str] = re.compile(r"(XENT|XGLO)(\d+)X")


def _has_short_placeholder_shape(masked_text: str) -> bool:
    """Whether *masked_text* outside its own placeholders looks like a short one."""
    return bool(_SHORT_PLACEHOLDER_SHAPE_RE.search(_FULL_PLACEHOLDER_RE.sub(" ", masked_text)))


def _to_short_placeholders(text: str) -> str:
    """*text* with every intact placeholder of this build in its short form."""
    return _FULL_PLACEHOLDER_RE.sub(r"\g<1>\g<2>X", text)


def _from_short_placeholders(text: str) -> str:
    """The model's short placeholders under the build's nonce again."""
    return _SHORT_PLACEHOLDER_RE.sub(
        lambda m: f"{m.group(1)}{_PLACEHOLDER_NONCE}X{m.group(2)}X", text
    )
