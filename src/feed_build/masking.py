"""Masking of entities (stations, lines, brands) before the model pass.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from ..utils.name_letters import latin_names
from .entities import (
    _BRAND_ENTITIES, _DATE_ENTITY_RE, _DIRECTION_NAME_RE, _GATE_SUFFIX_RE, _LINE_ENTITY_RE, _MAIN_STATION_RE,
    _PLACE_NAME_ENTITY_RE, _PREPOSITION_NAME_RE, _SPACED_STREET_NAME_RE, _STREET_SUFFIX_RE, _TRAM_LETTER_LINE_RE,
)
from .placeholders import (
    _ENTITY_PLACEHOLDER_FORMAT, _ENTITY_PLACEHOLDER_RE, _HYPHEN_PREFIX_RE, _SLASH_NAME_CAME_BEFORE_RE,
    _SLASH_NAME_GOES_ON_RE, _SLASH_PARTNER_AFTER_RE, _SLASH_PARTNER_BEFORE_RE, _UNMASK_PLACEHOLDER_RE,
    _WIEN_NAME_RE, _absorb_glued_x, _normalise_placeholder_debris,
)

__all__ = [
    "_declined_station_names", "_station_base_forms", "_station_entity_names", "_mask_entities",
    "_unmask_entities",
]


# Unicode glyphs that the Helsinki opus-mt-de-en tokenizer is likely
# to map to its ``<unk>`` slot — and silently drop on detokenize.
# Empirically observed in the live feed (2026-05-22): an ÖBB title
# ``Wien Hauptbahnhof ↔ Wien Floridsdorf ↔ Wien Meidling`` translated
# to ``Wien Hauptbahnhof Wien Floridsdorf Wien Meidling`` — the
# bidirectional arrow ``↔`` (U+2194) was stripped. The fix routes
# these glyphs through the same placeholder-mask path as proper
# nouns so they survive the round trip.
#
# Coverage by Unicode block:
#   * U+2010..U+2015 — dashes (hyphen, figure dash, en-/em-dash, …)
#     plus U+2026 (horizontal ellipsis) and U+2022/U+2023 (bullets);
#   * U+2190..U+21FF — Arrows block;
#   * U+27F0..U+27FF — Supplemental Arrows-A;
#   * U+2900..U+297F — Supplemental Arrows-B;
#   * U+2B00..U+2BFF — Miscellaneous Symbols and Arrows.
#
# The class is intentionally narrow: it does NOT cover the Latin-1
# supplement (where ``ä``/``ö``/``ü`` live and MUST survive into the
# model). False-positive risk is therefore zero for typical German
# disruption text.
_PRESERVED_SYMBOLS_RE: re.Pattern[str] = re.compile(
    r"["
    r"‐-―•‣…"
    r"←-⇿"
    r"⟰-⟿"
    r"⤀-⥿"
    r"⬀-⯿"
    r"]"
)


# Aliases that look like a noise-prefixed station ("Bahnhof X", "Bf X",
# "Station X", …) are skipped — those are spelling variants of the
# canonical name, not user-facing short forms. The match is
# case-insensitive and bounded by ``\b`` so the regex does not eat
# fragments of legitimate words ("Bahnhofstraße" stays untouched).
_ALIAS_NOISE_RE: re.Pattern[str] = re.compile(
    r"(?i)\b(?:Bahnhof|Bahnst|Bahnhst|Bhf|Bf|Hbf|Station|Hp|hl\.?\s*st\.?)\b"
)

# Trailing data-source marker suffix on a canonical station name — the
# ``(WL)`` in ``Wien Schloss Hetzendorf (WL)`` or the ``(VOR)`` /
# ``(ÖBB)`` equivalents. Stripped when deriving the clean surface
# variants in :func:`_station_entity_pattern` so the masker protects
# the form real feed text carries (without the marker). Anchored to
# the END so a parenthetical that is genuinely part of a name (none
# exist today, but defensively) mid-string is left intact.
_STATION_PAREN_SUFFIX_RE: re.Pattern[str] = re.compile(r"\s*\([^)]*\)\s*$")
# The hyphen between the two named parts of a stop (``Burggasse-Stadthalle``,
# ``Währinger Straße-Volksoper``): a word of four letters or more on either
# side. Only names with a single hyphen get a comma form, so
# ``Franz-Josefs-Bahnhof`` and other hyphenated names stay whole.
_STATION_PART_HYPHEN_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?<=[a-zäöüß]{4})-(?=[A-ZÄÖÜ][a-zäöüß]{3})"
))

# Aliases must look like a clean, short ``Wien X`` station name to be
# eligible for inclusion. Length cap keeps the regex bounded; the
# character class keeps spelling variants ("Wien Schwedenplatz",
# "Wien Heiligenstadt", "Wien Mitte") in while filtering out anything
# carrying digits, parentheses, slashes, or other markup.
_ALIAS_CLEAN_RE: re.Pattern[str] = re.compile(latin_names(
    r"^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß. \-]{6,28}$"
))


def _station_comma_forms(variants: set[str]) -> set[str]:
    """The comma forms WL's realtime texts use for two-part stop names.

    The directory writes ``Burggasse-Stadthalle``, a WL notice "Richtung
    Burggasse, Stadthalle". Without this form only the street half was
    shielded and the model rendered the rest, "Burggasse, city hall"
    (2026-10-01, EN audit 2026-10-03).
    """
    forms: set[str] = set()
    for variant in variants:
        if variant.count("-") != 1:
            continue
        comma_form = _STATION_PART_HYPHEN_RE.sub(", ", variant)
        if comma_form != variant:
            forms.add(comma_form)
    return forms


# WL's stop directory abbreviates "Bahnhof" ("Bhf. Meidling", "Bhf. Hütteldorf",
# "Bhf. Atzgersdorf S, Knotzenbachgasse": ten names in data/stations.json),
# its notices write the name out: "Haltestellenverlegung der Linie 15A in
# Richtung Bahnhof Meidling S U, Schedifkaplatz" (2026-11-05). Only
# "Meidling" was shielded, and the model made "Station Meidling" of the
# stop's name (finding of the KI watcher, 2026-11-06). The written-out and
# the dotless form stand for the same name. A "Bahnhof" in front of a stop
# the directory names without it ("Bahnhof Heiligenstadt") stays running
# text, as every other word does.
_STATION_ABBREVIATION_RE: re.Pattern[str] = re.compile(r"^(?P<wien>(?:Wien )?)Bhf\.? (?=\S)")


def _station_written_out_forms(variants: set[str]) -> set[str]:
    """``Bahnhof Meidling`` and ``Bhf Meidling`` for WL's ``Bhf. Meidling`` (see above)."""
    forms: set[str] = set()
    for variant in variants:
        match = _STATION_ABBREVIATION_RE.match(variant)
        if match is None:
            continue
        rest = variant[match.end():]
        forms.add(f"{match['wien']}Bahnhof {rest}")
        forms.add(f"{match['wien']}Bhf {rest}")
    return forms


# WL's own name of a stop has two parts, place and stop ("Laaer Berg, Kurpark
# Nordosteingang", "Atzgersdorf, Friedhof", "Liesing, Feuerwache"), and WL's
# texts write it so ("Haltestellenverlegung der Linie 68A in Richtung Laaer
# Berg, Nordosteingang"). The directory's name of the entry is another one
# ("Wien Kurpark Nordosteingang (WL)"), so only the place was shielded and the
# model rendered the rest: "towards Laaer Berg, northeast entrance" (finding
# of the KI watcher, 08.11.2026). 79 such names of Vienna entries were not
# shielded. A direction may drop the leading words of the second part
# ("Laaer Berg, Nordosteingang" for "Laaer Berg, Kurpark Nordosteingang");
# that short form stands for the same stop and keeps its place in front, so
# it matches only after "Laaer Berg, ".
_WL_STOP_NOISE_RE: re.Pattern[str] = re.compile(r"\s*(?:\([^)]*\)|[←→]+)\s*")


def _wl_stop_names(entry: dict[str, Any]) -> set[str]:
    """WL's two-part stop names of *entry* and their short forms (see above)."""
    forms: set[str] = set()
    stops = entry.get("wl_stops")
    if not isinstance(stops, list):
        return forms
    for stop in stops:
        raw = stop.get("name") if isinstance(stop, dict) else None
        if not isinstance(raw, str):
            continue
        name = " ".join(_WL_STOP_NOISE_RE.sub(" ", raw).split()).strip(" ,")
        place, sep, rest = name.partition(", ")
        if not sep or len(place) < 4 or not rest or "," in rest:
            continue
        forms.add(name)
        words = rest.split(" ")
        for start in range(1, len(words)):
            tail = " ".join(words[start:])
            if len(tail) >= 6 and tail[0].isupper():
                forms.add(f"{place}, {tail}")
    return forms


@lru_cache(maxsize=1)
def _station_entity_pattern() -> re.Pattern[str] | None:
    """A regex that matches every name of :func:`_station_entity_names`.

    A hyphen may stand between two spaces ("Burggasse - Stadthalle": the
    model made "City Hall" of the unprotected rest, 16.10.2026). Every name
    opens with a capital or a digit, and a match may not open with the
    lowercase form of one ("a", "ö", "š" of "Šatov"); the rest may differ in
    case ("STADION U"). Else a stop named like a phrase ("Am Bahnhof")
    shields the phrase in running text: "Stellwerkstörung am Bahnhof" read
    "interlocking fault am Bahnhof" in the EN feed (31.05. to 02.10.2026).
    """
    sorted_names = _station_entity_names()
    if not sorted_names:
        return None
    lower = "".join(sorted({name[0].lower() for name in sorted_names} - {name[0] for name in sorted_names}))
    pattern = (
        (rf"(?<!\w)(?!(?-i:[{re.escape(lower)}]))(?:" if lower else r"(?<!\w)(?:")
        + "|".join(re.escape(name).replace(r"\-", r"(?: - |-)") for name in sorted_names)
        + r")(?!\w)"
    )
    return re.compile(pattern, re.IGNORECASE)


# A stop name that opens with an adjective is declined in running text: WL
# wrote "Weiterfahrt über die Linie 37 bis Hohen Warte" (line 38, 31.10.2026),
# the directory knows "Hohe Warte". The masker shields names as written, so
# "Hohen Warte" reached the model as two words and the EN feed read "Continue
# via line 37 to High Wait" (the KI watcher's finding of 01.11.2026). Before the EN text is masked, such a declined
# name goes back to the form on the stop sign ("to Hohe Warte"), which the
# station pass then shields. The rule is the grammar, not a list: for every
# name of the directory whose first word ends like an adjective (-e, -er,
# -es: "Hohe Warte", "Hoher Markt", "Altes Landgut", "Obere Augartenstraße")
# the weak form in -en ("Hohen Warte", "Hohen Markt", "Alten Landgut",
# "Oberen Augartenstraße") stands for that name, unless the directory knows
# that form as a name of its own. Place adjectives ("Döblinger Hauptstraße")
# get a form no text writes ("Döblingen …"), which costs nothing. The German
# feed keeps WL's wording.
_DECLINABLE_NAME_HEAD_RE: re.Pattern[str] = re.compile(latin_names(
    r"^(?P<wien>(?:Wien )?)(?P<stem>[A-ZÄÖÜ][a-zäöüß]+?)(?:e|er|es)(?P<rest> [A-ZÄÖÜ].*)$"
))


@lru_cache(maxsize=1)
def _declined_station_names() -> dict[str, str]:
    """Declined form (``Hohen Warte``) → the directory's name (``Hohe Warte``)."""
    names = _station_entity_names()
    known = {name.casefold() for name in names}
    forms: dict[str, str] = {}
    for name in names:
        match = _DECLINABLE_NAME_HEAD_RE.match(name)
        if match is None:
            continue
        form = f"{match['wien']}{match['stem']}en{match['rest']}"
        if form.casefold() not in known:
            forms.setdefault(form, name)
    return forms


@lru_cache(maxsize=1)
def _declined_station_pattern() -> re.Pattern[str] | None:
    forms = _declined_station_names()
    if not forms:
        return None
    return re.compile(
        r"(?<![\w-])(?:" + "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True)) + r")(?![\w-])"
    )


def _station_base_forms(text: str) -> str:
    """*text* with every declined stop name in the form the directory knows (see above)."""
    pattern = _declined_station_pattern()
    if pattern is None:
        return text
    forms = _declined_station_names()
    return pattern.sub(lambda match: forms[match.group(0)], text)


@lru_cache(maxsize=1)
def _station_entity_names() -> tuple[str, ...]:
    """Every meaningful station name from the project's station
    directory, longest first.

    Three name forms per entry are emitted:

      * The canonical ``name`` field (e.g. ``Wien Stephansplatz``,
        ``Wien Mitte-Landstraße``).
      * The bare form with the leading ``Wien `` prefix dropped
        (e.g. ``Stephansplatz``) so disruption text that omits the
        city prefix still matches.
      * The written-out and the dotless form of a name WL abbreviates
        (``Bahnhof Meidling`` and ``Bhf Meidling`` for ``Bhf. Meidling``,
        :func:`_station_written_out_forms`).
      * Curated ``Wien X`` aliases from the entry's alias list — only
        for ``in_vienna`` entries, only when the alias looks like a
        user-facing short form (passes ``_ALIAS_CLEAN_RE`` and does
        NOT contain a "Bahnhof"/"Bf"/"Station" noise prefix). This
        captures real-world short forms like ``Wien Mitte`` (alias
        of canonical ``Wien Mitte-Landstraße``) that ÖBB and WL feed
        text routinely use without the long suffix. Without the
        alias coverage the masker missed ``Wien Mitte`` and the
        translation pipeline rewrote ``Wien`` → ``Vienna`` for that
        token alone, producing ``Vienna Mitte`` in the EN feed.

    The bare form is NOT emitted for aliases (only for canonicals)
    because alias text can be ambiguous outside the Vienna context —
    e.g. ``Mitte`` alone is not Vienna-specific, but ``Wien Mitte``
    is. Restricting bare-form generation to canonical names keeps
    the regex precise without false positives.

    The ~244k raw aliases in ``data/stations.json`` are reduced to
    ~11k Vienna short-forms after the ``in_vienna`` + clean-regex +
    noise-regex filters, so the compiled pattern stays under a
    megabyte and first-compile cost is ~350 ms (one-time, amortised
    over the build).

    Entries are sorted longest-first so ``Wien Hauptbahnhof`` matches
    ahead of ``Hauptbahnhof`` (greedy left-to-right alternation in
    :mod:`re` honours the first alternative that matches at a given
    position; the longest-first order makes the alternation pick the
    most specific name). Lookup is cached for the lifetime of the
    process via :func:`functools.lru_cache`.
    """
    try:
        from ..utils.stations import _station_entries
    except ImportError:  # keeps the masker working without the stations helper
        return ()

    names: set[str] = set()
    for entry in _station_entries():
        raw_name = entry.get("name")
        if not isinstance(raw_name, str):
            continue
        stripped = raw_name.strip()
        # Filter out pathological short tokens, pure-digit IDs and
        # values that would collide with the line-identifier regex
        # (e.g. ``5B`` as a station alias).
        if (
            len(stripped) < 4
            or stripped.isdigit()
            or _LINE_ENTITY_RE.fullmatch(stripped)
        ):
            continue
        names.add(stripped)
        # Emit clean surface variants. The station directory stores
        # Vienna stops with a data-source marker suffix (e.g. ``Wien
        # Schloss Hetzendorf (WL)``) — 1720 of 1773 Vienna canonicals
        # carry one. Real feed text uses the clean form WITHOUT the
        # ``(WL)`` / ``(VOR)`` marker, and frequently without the
        # ``Wien `` city prefix too. Pre-fix the bare-form derivation
        # only dropped the ``Wien `` prefix, leaving the useless
        # ``Schloss Hetzendorf (WL)`` in the regex while the feed's
        # actual ``Schloss Hetzendorf`` slipped through — the model
        # then mistranslated the ``Schloss`` component to "lock" /
        # "Castle". Emit every clean variant so the masker protects
        # the surface form the feed carries:
        #   ``Wien Schloss Hetzendorf (WL)`` (canonical, above)
        #   ``Wien Schloss Hetzendorf``       (suffix stripped)
        #   ``Schloss Hetzendorf (WL)``       (prefix stripped)
        #   ``Schloss Hetzendorf``            (both stripped)
        clean = _STATION_PAREN_SUFFIX_RE.sub("", stripped).strip()
        variants = {clean}
        if stripped.lower().startswith("wien "):
            variants.add(stripped[5:].strip())
            variants.add(clean[5:].strip())
        variants |= _station_comma_forms(variants)
        variants |= _station_written_out_forms(variants)
        for variant in variants:
            if (
                len(variant) >= 4
                and not variant.isdigit()
                and not _LINE_ENTITY_RE.fullmatch(variant)
            ):
                names.add(variant)

        # Curated ``Wien X`` aliases — restrict to Vienna entries so
        # the regex cannot accidentally protect a non-Vienna alias
        # that happens to start with ``Wien``.
        if not entry.get("in_vienna"):
            continue
        names |= _wl_stop_names(entry)
        aliases = entry.get("aliases")
        if not isinstance(aliases, list):
            continue
        for raw_alias in aliases:
            if not isinstance(raw_alias, str):
                continue
            alias = raw_alias.strip()
            if (
                not alias
                or alias == stripped
                or not alias.startswith("Wien ")
                or _ALIAS_NOISE_RE.search(alias)
                or not _ALIAS_CLEAN_RE.fullmatch(alias)
            ):
                continue
            names.add(alias)

    return tuple(sorted(names, key=len, reverse=True))


@lru_cache(maxsize=1)
def _brand_entity_pattern() -> re.Pattern[str]:
    """Compile the static brand list into a longest-first, case-
    sensitive regex.

    Case-sensitive matching is critical: the brand list contains
    all-caps abbreviations (``VOR``, ``VAO``, ``WLB``, ``ÖBB``) that
    would collide with everyday German prepositions under
    ``re.IGNORECASE`` — the canonical example was ``VOR`` (operator)
    matching ``vor`` (preposition "before") in disruption text such
    as ``"… Pasettistraße vor Hellwagstraße"``. The case-sensitive
    pattern keeps the brand list useful for the upper-case forms
    operators actually publish, while letting the surrounding prose
    flow through to the translator untouched.
    """
    sorted_brands = sorted(_BRAND_ENTITIES, key=len, reverse=True)
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(re.escape(brand) for brand in sorted_brands)
        + r")(?!\w)"
    )
    return re.compile(pattern)


def _mask_entities(text: str) -> tuple[str, dict[str, str]]:
    """Replace known entities in ``text`` with stable placeholders.

    The masker applies seven passes in priority order — each pass is a
    **verbatim shield**: the placeholder restores to the original
    German surface form on unmask:

      1. **Brands** — static operator / network names.
      2. **Stations** — canonical names + bare forms + curated
         ``Wien X`` aliases from the project's station directory.
      3. **Gate designations** — the ``3. Tor`` group in WL's
         Zentralfriedhof stop names, preserved verbatim so one feed
         cannot render the same stop as both "3. Tor" and "3. Gate".
         Runs before the line pass, whose bare-ordinal shape would
         otherwise mask the digit and leave the noun translatable.
      4. **Calendar dates** — ``27.09.2026`` as one verbatim token,
         ahead of the line pass that would otherwise mask its day alone
         and hand the model ``<placeholder>.09.2026`` to tear apart.
      5. **Line tokens** — ``U6``, ``S40``, ``5A``, …
      6. **Street suffixes** — capitalised compound nouns ending in a
         German street/place suffix (``…straße``, ``…gasse``,
         ``…platz``, …) are preserved verbatim. This catches every
         street name that is NOT also a registered station alias.
      7. **Preserved Unicode symbols** — arrows, bullets, em-/en-
         dashes, the ellipsis: glyphs that Marian's SentencePiece
         tokenizer otherwise maps to ``<unk>`` and drops.

    Domain-jargon translation (``Betriebsstörung`` →
    ``service disruption`` etc.) is a SEPARATE concern handled by
    :func:`_apply_domain_glossary` and composed in
    :func:`_translate_text_attempt`. Keeping the two passes split
    preserves the masker's identity contract — ``unmask(mask(x)) == x``
    for arbitrary text — and lets the property-based tests in
    ``tests/test_entity_masking_properties.py`` keep their strong
    round-trip invariant.

    Longest-matching span wins regardless of which source discovered
    it. Each unique surface form gets exactly one placeholder so a
    sentence mentioning the same station twice — or the same ``↔``
    arrow twice — survives one round-trip through the translation
    model with identical tokens.

    Returns a tuple ``(masked_text, mapping)`` where ``mapping``
    resolves each placeholder back to the German surface form the
    feed should restore.
    """
    if not text:
        return text, {}

    mapping: dict[str, str] = {}
    surface_to_placeholder: dict[str, str] = {}

    def _register(surface: str) -> str:
        cached = surface_to_placeholder.get(surface)
        if cached is not None:
            return cached
        placeholder = _ENTITY_PLACEHOLDER_FORMAT.format(index=len(mapping))
        mapping[placeholder] = surface
        surface_to_placeholder[surface] = placeholder
        return placeholder

    def _replace(match: re.Match[str]) -> str:
        return _register(match.group(0))

    working = _brand_entity_pattern().sub(_replace, text)
    station_pattern = _station_entity_pattern()
    if station_pattern is not None:
        working = station_pattern.sub(_replace, working)
    # The gate pass MUST precede the line pass: the bare ordinal in
    # ``4. Tor`` is itself a valid line-code shape, so ``_LINE_ENTITY_RE``
    # would mask the digit alone and leave the ``Tor`` noun exposed to
    # the translator — exactly the half-masked state that produced
    # "4. Gate".
    working = _MAIN_STATION_RE.sub(_replace, working)
    working = _GATE_SUFFIX_RE.sub(_replace, working)
    working = _DATE_ENTITY_RE.sub(_replace, working)
    working = _LINE_ENTITY_RE.sub(_replace, working)
    # After the line pass, so that ``43A/D`` has already become
    # ``XENT…X0X/D`` and the slash context the tram letters are recognised
    # by is still intact — placeholders keep the separators in place.
    working = _TRAM_LETTER_LINE_RE.sub(_replace, working)
    # Before the street passes, so "Am Kaisermühlendamm" stays one name.
    working = _PREPOSITION_NAME_RE.sub(
        lambda m: m.group("lead") + _register(m.group("name")), working
    )
    working = _SPACED_STREET_NAME_RE.sub(_replace, working)
    working = _STREET_SUFFIX_RE.sub(_replace, working)
    # After the street pass, so "Währinger Gürtel" stays one name.
    working = _PLACE_NAME_ENTITY_RE.sub(_replace, working)
    working = _DIRECTION_NAME_RE.sub(_replace, working)
    working = _PRESERVED_SYMBOLS_RE.sub(_replace, working)
    # Last, so that the name behind "Wien" is already masked by whichever
    # pass knows it ("Wien Schedifkaplatz": the street pass).
    working = _absorb_wien_into_names(working, mapping, _register)
    working = _absorb_hyphen_prefixes(working, mapping, _register)
    working = _shield_slash_partners(working, mapping, _register)
    return _absorb_glued_x(working, mapping, _register), mapping


def _shield_slash_partners(
    working: str,
    mapping: dict[str, str],
    register: Callable[[str], str],
) -> str:
    """Shield the unknown half of a slash-joined stop name (``_SLASH_PARTNER_*_RE``).

    Only a half next to a shielded *name* counts: a line, a brand or a date
    on the other side ("U2/U3", "ÖBB/WL") makes no stop name. The half is
    shielded verbatim, a typo included, as WL wrote it. Only a one-word half
    at the outer end of the pair counts: a half the name goes on behind
    ("Hauptbahnhof/Sankt Marx") or in front of ("Wien Spittelau
    Bahnhst/Wien Mitte") stays with the model, which read those whole names
    better than a name split in two placeholders (real model, 2026-11-02).
    """

    def _shield(match: re.Match[str]) -> str:
        surface = mapping.get(match.group("name"), "")
        if (
            not surface[:1].isupper()
            or surface in _BRAND_ENTITIES
            or _LINE_ENTITY_RE.fullmatch(surface)
        ):
            return match.group(0)
        text = match.string
        if match.re is _SLASH_PARTNER_AFTER_RE:
            rest = text[match.end():]
            if _SLASH_NAME_GOES_ON_RE.match(rest):
                return match.group(0)
        elif _SLASH_NAME_CAME_BEFORE_RE.search(text[: match.start()]):
            return match.group(0)
        word = register(match.group("word"))
        if match.re is _SLASH_PARTNER_AFTER_RE:
            return match.group("name") + "/" + match.group("gap") + word
        return word + "/" + match.group("gap") + match.group("name")

    working = _SLASH_PARTNER_AFTER_RE.sub(_shield, working)
    return _SLASH_PARTNER_BEFORE_RE.sub(_shield, working)


def _absorb_hyphen_prefixes(
    working: str,
    mapping: dict[str, str],
    register: Callable[[str], str],
) -> str:
    """Shield a hyphenated prefix together with the name it is glued to (``_HYPHEN_PREFIX_RE``).

    Like :func:`_absorb_wien_into_names`: a placeholder the text uses only
    here takes the prefix into its own surface, one used elsewhere too stays
    as it is there, and the whole name gets a new one. A line or a brand
    ("U6", "ÖBB") is no name a prefix belongs to.
    """
    counts = Counter(_ENTITY_PLACEHOLDER_RE.findall(working))

    out: list[str] = []
    position = 0
    for match in _HYPHEN_PREFIX_RE.finditer(working):
        found = _ENTITY_PLACEHOLDER_RE.match(working, match.end())
        if found is None:
            continue
        placeholder = found.group(0)
        surface = mapping.get(placeholder, "")
        if not surface[:1].isupper() or surface in _BRAND_ENTITIES or _LINE_ENTITY_RE.fullmatch(surface):
            continue
        whole = match.group(0) + surface
        if counts[placeholder] == 1:
            mapping[placeholder] = whole
            out.append(working[position:match.start()] + placeholder)
        else:
            out.append(working[position:match.start()] + register(whole))
        position = found.end()
    out.append(working[position:])
    working = "".join(out)
    for placeholder in [p for p in mapping if p.startswith("XENT") and p not in working]:
        del mapping[placeholder]
    return working


def _absorb_wien_into_names(
    working: str,
    mapping: dict[str, str],
    register: Callable[[str], str],
) -> str:
    """Shield "Wien" together with the name it belongs to (``_WIEN_NAME_RE``).

    A name an earlier pass already masked takes the city into its own
    placeholder; a placeholder the text uses elsewhere too, without the
    city, stays as it is there, and the whole name gets a new one.
    """
    counts = Counter(_ENTITY_PLACEHOLDER_RE.findall(working))

    def _absorb(match: re.Match[str]) -> str:
        text = match.group(0)
        inner = _ENTITY_PLACEHOLDER_RE.search(text)
        if inner is None:
            return register(text)
        placeholder = inner.group(0)
        surface = mapping.get(placeholder, "")
        if (
            not surface[:1].isupper()
            or surface in _BRAND_ENTITIES
            or _LINE_ENTITY_RE.fullmatch(surface)
        ):
            return text
        whole = text.replace(placeholder, surface)
        if counts[placeholder] == 1:
            mapping[placeholder] = whole
            return placeholder
        return register(whole)

    working = _WIEN_NAME_RE.sub(_absorb, working)
    for placeholder in [p for p in mapping if p.startswith("XENT") and p not in working]:
        del mapping[placeholder]
    return working


def _unmask_entities(text: str, mapping: dict[str, str]) -> str:
    """Restore entity surface forms in ``text`` using ``mapping``.

    Handles BOTH placeholder formats emitted by the masking pipeline:

      * ``XENT<n>X`` — verbatim entity placeholders produced by
        :func:`_mask_entities` (brands, stations, lines, streets,
        symbols). Mapping value is the German surface form to
        restore.
      * ``XGLO<n>X`` — domain-glossary placeholders produced by
        :func:`_apply_domain_glossary`. Mapping value is the English
        equivalent of the German term.

    Tolerant of the translator dropping or reordering placeholders —
    only placeholders that still appear in ``text`` are restored, the
    rest are silently discarded so the output never carries a literal
    ``XENT3X`` / ``XGLO2X`` token to subscribers.
    """
    if not mapping:
        return text

    def _restore(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        return mapping.get(placeholder, "")

    # Repair a doubled closing ``X`` before matching, or the valid prefix is
    # restored and the surplus character survives as part of the entity —
    # the ``14AX`` leak of 2026-09-18 (see _PLACEHOLDER_DEBRIS_RE).
    return _UNMASK_PLACEHOLDER_RE.sub(
        _restore, _normalise_placeholder_debris(text)
    )
