"""The translation-cache epoch and the verbatim and source-digest marks.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..utils.logging import sanitize_log_arg

from .base import log

__all__ = [
    "_TRANSLATION_CACHE_EPOCH", "_VERBATIM_FIELDS_KEY", "_verbatim_fields", "_record_verbatim",
    "_SOURCE_DIGEST_KEY", "_source_digest", "_source_digests", "_record_source_digest",
    "_evict_stale_translations", "_stamp_translation_epoch",
]


# Translation-cache epoch. EN translations are cached per disruption
# identity in ``state[ident]["translations"]["en"]`` and persisted
# across builds. The cache key (see :func:`_identity_for_item`) is
# stable across cosmetic changes — a Baustellen item keyed on
# ``title + date-range`` survives a description-only edit — so a
# translation computed once is served for the lifetime of the item
# (multi-year for long construction projects).
#
# That persistence is a problem whenever the masking / glossary logic
# IMPROVES: an item translated by an older build keeps its stale
# rendering forever, because :func:`_cached_translation` only forces a
# retry when the cached value equals the German source (the
# "Sticky-German" guard). A cached value that is a *wrong but
# non-German* translation — e.g. ``Schlachthausgasse`` rendered as
# "slaughterhouse gas" before the street-suffix masker existed — is
# served indefinitely.
#
# The epoch breaks that lock. Bump this integer whenever a change to
# :func:`_mask_entities`, the entity patterns, or the domain glossary
# would alter the EN output for already-cached items. On the next
# build :func:`_apply_lang_overlay` evicts every translation stamped
# with an older epoch and recomputes it through the improved
# pipeline, then re-stamps the current epoch so subsequent builds
# trust the cache again (no churn).
#
# Epoch history:
#   1 — first epoch. Invalidates every pre-epoch cache entry, picking
#       up the street-suffix masker (#1624), the metadata-driven
#       glossary (#1625) and the disruption-core scope tightening.
#   2 — "ggü." / "ggü" (gegenüber) → "opp." / "opp" street-addressing
#       abbreviation added to the base glossary.
#   3 — station masker now derives clean surface variants from
#       ``(WL)`` / ``(VOR)``-suffixed canonical names (e.g.
#       ``Schloss Hetzendorf`` from ``Wien Schloss Hetzendorf (WL)``),
#       so Vienna stop names with a translatable component are no
#       longer mistranslated ("Schloss Hetzendorf" → "lock Hetzendorf").
#   6 — per-field source fingerprints (``_SOURCE_DIGEST_KEY``) added, so a
#       reworded German source invalidates its cached English instead of
#       serving it for the item's lifetime. The bump exists to stamp a
#       digest onto the ~2500 entries cached without one; it asserts no
#       change in translation quality.
#   7 — street-suffix masker learned the abbreviated ``…str.`` form and
#       the leading adjective (``Vordere``/``Hintere``/``Kleine`` …), so
#       ``Hintere Zollamtsstr.`` stops rendering as "rear customs office"
#       and ``Vordere Zollamtsstraße`` as "Front Zollamtsstraße". Without
#       the bump the affected items keep serving the wrong English for
#       their lifetime — a U4 construction notice runs until 30.11.2026.
#   8 — the translation is now rejected when the model drops a verbatim
#       entity, and repaired when it doubles a placeholder's closing ``X``.
#       Both defects were cached as successes, so without the bump the two
#       items that exposed them keep serving the wrong English: the
#       ``72A`` stop relocation names 510 as the address it moves *from*
#       (the source says 2) and runs until 18.09.2027, and the ``12A/14A``
#       notice advertises a line ``14AX`` that does not exist.
#   9 — WL's structured field labels (``Von:`` / ``Nach:`` / ``Haltestelle:``
#       / ``Dauer:`` / ``Zeitraum:`` / ``Grund:``) and the term
#       ``Haltestellenverlegung`` now resolve through the glossary instead
#       of being guessed at. ``Nach:`` was rendering as "After:" beside a
#       correct "From:" — a relocation with an origin and no destination —
#       and the opening term came back as "Stop change", "Station
#       relocation", "Stop transfer" or untranslated, depending on the item.
#       Cached items keep every one of those until the epoch moves.
#  10 — a trailing ``Label: value`` record is now split off and rendered
#       without the NMT model at all, and ``gegenüber`` joined its ``ggü.``
#       sibling in the glossary. The three relocation items themselves were
#       not cached — since epoch 8 their translation is rejected outright and
#       a failed attempt persists nothing — so the bump is for the items
#       cached BEFORE that rejection began, and for every cached item
#       carrying a spelled-out ``gegenüber``.
#  11 — ``a``/``an`` now agrees with the word the glossary substitutes, so
#       "an switch fault", "an demonstration" and "an service obstruction"
#       stop reaching subscribers. 15 occurrences across 314 published EN
#       texts, every one of them cached as a success — the article is wrong
#       without being German, so nothing else would evict it.
#  12 — measured gaps in the disruption vocabulary closed: Schadhafter
#       Zug/Bus/PKW ("Harmful train/bus/car"), Fremdunfall ("Foreign
#       accident"), Falschparker ("False Parker"), Verunreinigung
#       ("Impurity"), Wasserrohrgebrechen, Klapprampensperre ("Folding ramp
#       lock") and Verkehrsstörung. 31 published items carry one of these
#       and every one of them is cached as a success.
#  13 — the definite article in front of a line identifier is dropped:
#       "the lines 36A and 36B are being redirected" becomes "lines 36A and
#       36B …", the way Wiener Linien write it in English themselves. German
#       requires the article, English transit usage does not, and the model
#       cannot resolve the difference because the identifier it precedes is a
#       masked placeholder. 23 of 294 published EN texts carry it, all cached
#       as a success — the article is wrong without being German, so nothing
#       else would evict it.
#  14 — the same article, one word class over: German articles its street
#       and square names ("in der Althanstraße", "zum Karlsplatz") and
#       English leaves them bare. 5 of 310 published EN texts carry it,
#       all cached as a success. Only the article goes; the preposition
#       stays as the model chose it, and the street test is the masker's
#       own ``_STREET_SUFFIX_RE``, so "the Ernst-Happel-Stadion" and "the
#       Wiener Linien" — where the article is defensible English — are
#       untouched.
#  15 — the C.5 separator. "31: Demonstration – Betrieb ab …" reached the
#       model with the en dash masked as a placeholder wedged between two
#       words; the model re-emitted it with debris ("Demonstration –Xservice
#       from …", published 2026-09-19 21:30, cached as a success because the
#       stray X is glued to a word and no guard sees it). Titles of that
#       shape are now translated in two halves and never carry the dash
#       into the model; the cached debris has to go.
#  16 — the values of a label record are English too. The record never
#       reaches the model, so "Ab", "etwa", "bis", the month names and the
#       open-end phrases ("Dauer unbekannt", "bis auf Widerruf") stayed
#       German in every rendered record — 3 of the 10 EN items on
#       2026-09-24, all cached as a success. Same push: "ersatzlos
#       aufgelassen" as one phrase ("replacementless Discontinued").
#  17 — "Haltestellenauflassung" joins its sister "Haltestellenverlegung"
#       in the glossary. The N31 prose was re-translated under epoch 16 and
#       cached as "Stop stop on line N31 towards Schwedenplatz U"; only a
#       bump evicts it, the source digest is unchanged.
#  18 — three glossary phrases (audit 2026-09-25, A.3 and A.9, and the
#       ÖBB replacement bus). Cached under 17: "S80: ÖBB-replacement bus",
#       "Removing traffic restrictions: St. Pölten Hauptbahnhof", and label
#       records reading "until expected end of November". The source digests
#       are unchanged, so only a bump evicts them.
#  19 — an ``X`` the model put in front of a placeholder now becomes the
#       space it replaced (``_PLACEHOLDER_LEADING_X_RE``). The D notice
#       was cached under 18 as "WipplingerstrX39; service fromXAugasse"
#       and stood second in the EN feed on 2026-10-02; the source digest
#       is unchanged, so only a bump evicts it.
#  20 — EN audit 2026-10-03: WL's reason vocabulary in the glossary
#       ("Fremder Verkehrsunfall", "Stromstörung", "im Haltestellenbereich",
#       …), the reason of a label record rendered or translated instead of
#       left German, "Voraussichtliche Dauer:" as one label, bare-hour
#       clock times, freestanding "-er" street names, WL's comma stop names
#       and ``<Ort> Hbf`` shielded, ÖBB's templated sentences rendered from
#       their slots. Cached under 19 among others: "Reason: Fremder traffic
#       accident in the area of Hernalser Hauptstraße", "Alliance Stadion",
#       "Schlosshofer Straße"; the source digests are unchanged, so only a
#       bump evicts them.
#  21 — WL's stop tickers (Jules audits 2026-10-02 and 2026-10-04): a place
#       after "Busse/Züge halten" gets "at", the stop of another line is
#       written out ("Trains stop for lines 6 and 18" before). Cached under
#       20: "66A: Buses stop Salvatorianerplatz"; the source digests are
#       unchanged, so only a bump evicts it.
#  22 — WL's "Wegen <Ursache> <Verb> …" sentences rendered as cause plus
#       main clause (``_render_wl_because_sentence``). Cached under 21: "The
#       U6 station stops at Neue Donau U only in the direction of
#       Siebenhirten U.", the cause dropped; the source digest is unchanged,
#       so only a bump evicts it.
#  23 — a relocation's "A → B" rendered without the model again
#       (``_render_wl_sentence``), and a cause without "Due to" gets it back.
#       Cached under 22: "Wienerbergstraße27b-27c →Wienerbergstraße27a. …
#       15A5X …" and "Renovation of the platform, line U6 …".
#  24 — EN sense errors (check of 2026-10-08, every DE text since July
#       through the real model): WL's "kommt es zu Verkehrsmaßnahmen" as
#       "service changes", "unterschiedlichen Intervallen", WL's shorthand
#       ("Li.", "Ri.", "Hlst.", "ONr.", "Steig A"), "Einstieg bei Linie",
#       "nicht eingehalten", "über Gleis 2", "kurzgeführt", place names the
#       model translated ("Ring", "Oper", "Julius Raab Platz", the name
#       after "Richtung") and the roadworks vocabulary. Cached under 23:
#       "Due to a demonstration, transport is taking place.", "via 2
#       track", "Trains stop at Li. O Ri. Raxstraße."; the source digests
#       are unchanged, so only a bump evicts them.
#  25 — check of 2026-10-09: a stop sentence with a line ("Ersatzbus 26E
#       hält an …", "Linie 5 hält …") gets "stops at" like "Busse halten"
#       (cached: "Replacement bus 26E holds … ahead of …"); ÖBB's templated
#       sentence is no longer split after "St." ("in St. Pölten Hbf"), and
#       its fixed follow-up sentences are written out. The source digests
#       of the stop texts are unchanged, so only a bump evicts them.
#  26 — Jules audits 18. and 22.10.2026: a relocation's place kept German
#       words in English ("Gleiskörper", "Haltestelle Straßenbahn",
#       "Parkanlage", "Richtung Ampel", …; ``_RECORD_VALUE_RULES``). Cached
#       under 25: "Simmeringer Hauptstraße opposite 120-122, Gleiskörper →
#       …"; the source digests are unchanged, so only a bump evicts them.
#  27 — independent check 2026-10-24: "Umleitungen" is a fixed term only as
#       the head of the short form ("Umleitungen: 36B …"); inside a sentence
#       the model got no noun to build on. Cached under 26: "Due to roadworks
#       in … crossing area, diversions occurs."
#  28 — follow-up check 2026-10-24: "kommt es zu Umleitungen" is fixed as
#       "there are diversions" (operator wish: one word for the same thing
#       across sources). Cached under 27: "Due to roadworks in … intersection,
#       there are redirections."
#  29 — Jules audits 26. and 27.10.2026: a street name in the genitive
#       ("Streckensperre des Algenwegs") was not shielded and went through
#       the model; it is now shielded and restored in its base form. Cached
#       under 28: "Due to an ordered line closure of the algae path, line 89A
#       is redirected."; the source digest is unchanged, so only a bump
#       evicts it.
#  30 — Jules audits 28. and 29.10.2026: street names with a preposition
#       ("An den Eisteichen") or a hyphenated prefix ("Unter-Meidlinger
#       Straße") are shielded whole, and WL's relocation reasons are in the
#       glossary. Cached under 29: "in the area of At the ice ponds", "Under-
#       Meidlinger Straße", "Reason: Parked original stop."; the source
#       digests are unchanged, so only a bump evicts them.
#  31 — KI-Wächter of 2026-11-02 (feed state of 30.10.): the unknown half
#       of a slash-joined stop name ("Parlament/ Volkstheter", WL's typo) is
#       shielded verbatim. Cached under 30: "service from Parlament/
#       Folktheter"; the source digest is unchanged, so only a bump evicts it.
#  (no bump) KI-Wächter of 2026-11-03: the name patterns accept every Latin
#       letter (``latin_names``; "Biróstraße 17" read "Biró Street 17"). No
#       cached translation held such a name when the rule came, so nothing
#       needed evicting; new items go through the widened shield anyway.
#  32 — KI-Wächter of 2026-11-08: WL's own two-part stop names ("Laaer Berg,
#       Kurpark Nordosteingang", "Atzgersdorf, Friedhof") and their short
#       form in a direction ("Laaer Berg, Nordosteingang") are shielded
#       (``_wl_stop_names``). Cached under 31: "Stop relocation of line 68A
#       towards Laaer Berg, northeast entrance."; the source digest is
#       unchanged, so only a bump evicts it.
#  33 — follow-up check of the KI-Wächter issue 2026-11-08: the entrance in
#       a relocation's place is English ("vor Nordeingang des Kurparks" →
#       "before the north entrance of Kurpark", ``_ENTRANCE_OF_RE``). Cached
#       under 32: "Filmteichstraße before Nordeingang des Kurparks → …"; the
#       source digest is unchanged, so only a bump evicts it.
_TRANSLATION_CACHE_EPOCH = 33


# Fields whose correct English translation IS the German source, verified by
# an actual model run. Lives beside ``en`` in ``translations`` rather than
# inside it, so the ``en`` sub-dict stays a homogeneous field→string map.
#
# Why this marker has to exist (audit 2026-09-12, Befund 7). The cache lookup
# treats "stored translation equals the source" as evidence of an earlier
# BROKEN build that persisted the German text as the translation, and
# re-translates. For a title made only of station names — ``Wien Hauptbahnhof
# ↔ Felixdorf`` — the correct English *is* the German, so the condition holds
# forever and the model re-ran on every build without ever producing a
# different answer. Measured before the fix: five builds, five model runs.
#
# A stored string alone cannot tell the two cases apart. The marker can,
# because it is written ONLY where the two differ: after
# ``_translate_text_attempt`` returned a non-``None`` result. A failed attempt
# returns ``None`` and persists nothing, so a build that could not translate
# can never mark a field verbatim — the drift guard keeps its teeth.
#
# Epoch-scoped like the translations themselves: a later epoch may well
# translate the same text differently, so ``_evict_stale_translations`` drops
# this key together with ``en``.
_VERBATIM_FIELDS_KEY = "en_verbatim"


def _verbatim_fields(translations: dict[str, Any]) -> set[str]:
    """Return the field names recorded as legitimately untranslated."""
    raw = translations.get(_VERBATIM_FIELDS_KEY)
    if not isinstance(raw, list):
        return set()
    return {name for name in raw if isinstance(name, str)}


def _record_verbatim(
    translations: dict[str, Any], field: str, *, identical: bool
) -> None:
    """Record — or withdraw — ``field``'s verbatim mark after a model run.

    Withdrawing matters as much as setting: once a glossary or masking
    change makes a previously untranslatable text translate, the stale mark
    would otherwise keep asserting "identical is correct" about output that
    is no longer identical.

    The key is removed entirely when no field is left, so the 2500+ entries
    in the persisted state do not each grow an empty list.
    """
    fields = _verbatim_fields(translations)
    if identical:
        fields.add(field)
    else:
        fields.discard(field)
    if fields:
        translations[_VERBATIM_FIELDS_KEY] = sorted(fields)
    else:
        translations.pop(_VERBATIM_FIELDS_KEY, None)


# Per-field fingerprint of the GERMAN source a cached translation was made
# from — the axis the cache was missing.
#
# The cache is keyed ``(ident, field)``. ``ident`` identifies the disruption,
# not its wording, and upstream rewords a live disruption freely as the
# situation develops while keeping the same identity. Nothing compared the
# cached English against the German it came from (``cached != text`` compares
# an English string to a German one, which is essentially always true), so the
# first translation of an item was served for the item's whole lifetime.
#
# Observed live on 2026-09-13, ``docs/feed.en.xml`` item 1: the German title
# had become "44: Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz" while
# the English still read "44: event Trains stop Rosensteingasse on line 9
# direction Westbahnhof" — the translation of a title the DE feed had already
# replaced. The two feeds were telling subscribers different things about the
# same GUID, which is worse than an untranslated item: it is a wrong one.
#
# ``_TRANSLATION_CACHE_EPOCH`` could not catch this. It invalidates when *our*
# glossary or masking changes; it knows nothing about upstream edits. The
# digest is the complementary signal, and the two are deliberately kept
# separate: bumping the epoch here would assert a translation-quality change
# that did not happen.
#
# A missing digest is TRUSTED rather than treated as a miss, and the rollout
# rides the epoch bump to 6 instead. Making "no digest" a miss would have
# worked too, but it duplicates what the epoch already does — invalidate
# everything once — and it would have broken the documented contract that a
# current-epoch cache is served without touching the pipeline. After the
# epoch-6 rebuild every entry carries a digest, and the two mechanisms keep
# one job each: the epoch for changes on our side, the digest for changes on
# upstream's. An entry cannot end up at epoch 6 without a digest —
# ``_record_source_digest`` runs in lockstep with the stored string, and the
# epoch is stamped only after every field succeeded.
#
# 16 hex chars (64 bits) is ample for change detection and keeps the persisted
# state (2500+ entries × 2 fields) from growing by a full digest each.
_SOURCE_DIGEST_KEY = "en_src"
_SOURCE_DIGEST_CHARS = 16


def _source_digest(text: str) -> str:
    """Return the short fingerprint of a German source string."""
    return hashlib.sha256(
        text.encode("utf-8", "surrogatepass")
    ).hexdigest()[:_SOURCE_DIGEST_CHARS]


def _source_digests(translations: dict[str, Any]) -> dict[str, str]:
    """Return the per-field source fingerprints recorded for ``translations``."""
    raw = translations.get(_SOURCE_DIGEST_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        field: value
        for field, value in raw.items()
        if isinstance(field, str) and isinstance(value, str)
    }


def _record_source_digest(
    translations: dict[str, Any], field: str, text: str
) -> None:
    """Stamp the source fingerprint ``field``'s cached translation came from.

    Written in lockstep with the translation itself, so a field can never
    carry a translation without the digest that validates it.
    """
    digests = _source_digests(translations)
    digests[field] = _source_digest(text)
    translations[_SOURCE_DIGEST_KEY] = dict(sorted(digests.items()))


def _evict_stale_translations(
    ident: str, state: dict[str, dict[str, Any]] | None
) -> None:
    """Drop cached EN translations that predate the current
    :data:`_TRANSLATION_CACHE_EPOCH`.

    Called at the top of :func:`_apply_lang_overlay` before any cache
    lookup. When the persisted epoch is older than the current one the
    item was translated by a build with weaker masking / glossary
    logic; the whole ``en`` sub-dict is removed so every field is
    recomputed through the improved pipeline. The epoch is re-stamped
    only AFTER a successful retranslation (see
    :func:`_stamp_translation_epoch`) so a transient pipeline failure
    cannot lock in a half-empty cache at the current epoch.
    """
    if state is None or not ident:
        return
    entry = state.get(ident)
    if not isinstance(entry, dict):
        return
    translations = entry.get("translations")
    if not isinstance(translations, dict):
        return
    epoch_raw = translations.get("epoch", 0)
    epoch = epoch_raw if isinstance(epoch_raw, int) else 0
    if epoch < _TRANSLATION_CACHE_EPOCH:
        if "en" in translations:
            log.info(
                "Evicting stale EN translation for %s (epoch %s < %s).",
                sanitize_log_arg(ident),
                epoch,
                _TRANSLATION_CACHE_EPOCH,
            )
        translations.pop("en", None)
        # The verbatim marks belong to the evicted translations: they assert
        # what THIS epoch's pipeline produced. A newer epoch with a better
        # glossary may translate the same text after all, so the marks go
        # with the strings rather than outliving them.
        translations.pop(_VERBATIM_FIELDS_KEY, None)
        # Same reasoning for the source fingerprints: they validate the
        # strings that were just dropped. Leaving them behind would pair a
        # matching digest with no translation — harmless today (the lookup
        # needs both) but exactly the kind of half-state that invites a
        # later "the digest matches, trust it" shortcut.
        translations.pop(_SOURCE_DIGEST_KEY, None)


def _stamp_translation_epoch(
    ident: str, state: dict[str, dict[str, Any]] | None
) -> None:
    """Record that ``ident``'s cached translations were produced under
    the current :data:`_TRANSLATION_CACHE_EPOCH`.

    Called only after every field translated successfully, so a future
    build trusts the cache instead of evicting it again (no churn even
    for items whose translation legitimately drops a placeholder).
    """
    if state is None or not ident:
        return
    entry = state.setdefault(ident, {})
    translations = entry.setdefault("translations", {})
    if isinstance(translations, dict):
        translations["epoch"] = _TRANSLATION_CACHE_EPOCH
