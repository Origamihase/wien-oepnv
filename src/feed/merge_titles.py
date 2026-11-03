"""Title parsing, tokenising and name overlap for :mod:`src.feed.merge`.

Helper-to-helper calls resolve here; patch this module to reach them.
"""

import re

# Line-prefix grammar tolerant of two real-world spellings:
#
#   "U6: …", "1/2: …", "13A/14A: …"   (WL-style — no internal whitespace)
#   "REX 7: …", "S 50: …", "REX 7/REX 8: …"  (ÖBB-style — letters and digits
#   separated by whitespace)
#
# Without the optional inner space the ÖBB tokens silently fell through, so a
# disruption surfaced once via VOR ("REX7") and once via ÖBB ("REX 7") — two
# items in the feed for the exact same incident. Internal whitespace inside a
# token is normalised away in :func:`_parse_title` before the line set is
# compared.
_LINE_PREFIX_RE = re.compile(
    r"^\s*("
    # ÖBB style: REX 7, S 50, RJX 12. The optional ``-Bahn`` segment
    # tolerates verbose spellings like ``S-Bahn 50`` and ``U-Bahn 6``
    # so they can fuzzy-merge with the compact ``S 50`` / ``U6``
    # variants from another provider.
    r"[A-Za-z]+(?:-[Bb]ahn)?\s*\d{1,3}[A-Za-z]?"
    r"(?:\s*[/+]\s*[A-Za-z]+(?:-[Bb]ahn)?\s*\d{1,3}[A-Za-z]?)*"
    # WL style: ``1/2``, ``U6``, ``13A`` — and also ``40+41`` (a
    # multi-line shorthand WL uses for items affecting two lines on
    # the same corridor). Without the ``+`` alternative the merge
    # falls back to a single-line set and loses the cross-line
    # overlap signal that drives ``deduplicate_fuzzy``.
    r"|[A-Za-z0-9]+(?:\s*[/+]\s*[A-Za-z0-9]+){0,20}"
    r")\s*:\s*"
)
_LINE_TOKEN_RE = re.compile(r"^(?:\d{1,3}[A-Z]?|[A-Z]{1,4}\d{0,3}[A-Z]?)$")

# Tokens that must not by themselves drive a fuzzy merge. The baseline list
# covers generic disruption verbs ("Störung", "Ausfall", …) that show up in
# nearly every title; the extended list adds German articles, prepositions
# and connector words ("im", "am", "bereich", …) so that two titles which
# only share these fillers do NOT get merged.
#
# Why this matters: without prepositions in the stop list, "U1: Störung im
# Bereich Praterstern" and "U1: Störung im Bereich Karlsplatz" share the
# tokens {"störung", "im", "bereich"} and exceed the 0.4 overlap threshold
# despite referring to two completely different stations.
_STOP_WORDS = {
    # Generic disruption nouns
    "störung",
    "stoerung",
    "ausfall",
    "ausfälle",
    "ausfaelle",
    "einschränkung",
    "einschraenkung",
    "betrieb",
    "verkehr",
    "verkehrsbehinderung",
    "fahrtbehinderung",
    "behinderung",
    "verspätung",
    "verspaetung",
    "verspätungen",
    "verspaetungen",
    "info",
    "information",
    "meldung",
    "hinweis",
    "sperre",
    "sperrung",
    "gesperrt",
    "umleitung",
    "ersatzverkehr",
    "kurzführung",
    "kurzfuehrung",
    "fahrt",
    "fahrten",
    "linie",
    "linien",
    "bauarbeiten",
    # Equipment/state words that recur across unrelated incidents
    "aufzug",
    "aufzüge",
    "aufzuege",
    "aufzugsinfo",
    "lift",
    "fahrstuhl",
    "fahrtreppe",
    "fahrtreppen",
    "fahrtreppeninfo",
    "rolltreppe",
    "rolltreppen",
    "defekt",
    "kaputt",
    "gestört",
    "gestoert",
    "blockiert",
    "weichenstörung",
    "weichenstoerung",
    "signalstörung",
    "signalstoerung",
    "polizeieinsatz",
    "rettungseinsatz",
    "feuerwehreinsatz",
    "notarzteinsatz",
    "personenschaden",
    "fahrzeugschaden",
    # German articles
    "der",
    "die",
    "das",
    "des",
    "den",
    "dem",
    "ein",
    "eine",
    "einer",
    "einem",
    "eines",
    # German prepositions
    "in",
    "im",
    "an",
    "am",
    "auf",
    "aus",
    "bei",
    "mit",
    "von",
    "vom",
    "zu",
    "zum",
    "zur",
    "nach",
    "durch",
    "für",
    "fuer",
    "gegen",
    "ohne",
    "um",
    "unter",
    "über",
    "ueber",
    "wegen",
    "während",
    "waehrend",
    "zwischen",
    "vor",
    "hinter",
    "neben",
    "ab",
    "bis",
    # German conjunctions
    "und",
    "oder",
    "aber",
    "sowie",
    "sondern",
    # Generic place-fillers
    "bereich",
    "höhe",
    "hoehe",
    "richtung",
    "fahrtrichtung",
    "station",
    "haltestelle",
    "haltestellen",
    "bahnhof",
    "bahnhst",
    "hbf",
    "bf",
    "bhf",
    # The ubiquitous city qualifier — nearly every Vienna station name
    # carries it, so without this two DIFFERENT Vienna routes that merely
    # share "Wien" reach the overlap threshold and wrongly merge (bug b6).
    # Safe to drop only because _normalize_name first folds Hbf/Bf into
    # their spelled-out forms, so cross-provider Hbf/Hauptbahnhof pairs
    # still share the distinctive station token.
    "wien",
    "vienna",
}


def _parse_title(title: str) -> tuple[set[str], str]:
    """
    Parses a title into a set of lines and the event name.
    Example: "1/2: Event Name" -> ({"1", "2"}, "Event Name")
    Whitespace inside a token is collapsed so "REX 7" matches "REX7" across
    providers.
    """
    m = _LINE_PREFIX_RE.match(title or "")
    if not m:
        return set(), title or ""

    lines_str = m.group(1)
    event_name = title[m.end() :].strip()

    lines = set()
    # Split on ``/`` and ``+`` (WL multi-line shorthand) so all line
    # tokens of ``40+41:`` and ``40/41:`` are captured equally.
    for raw in re.split(r"[/+]", lines_str):
        # Strip a verbose ``-Bahn`` suffix so "S-Bahn 50" and "S 50"
        # produce the same canonical token "S50" — without this both
        # would coexist in the feed for the same incident.
        cleaned = re.sub(r"-bahn", "", raw, flags=re.IGNORECASE)
        # Drop inner whitespace so "REX 7" and "REX7" become the same token.
        token = re.sub(r"\s+", "", cleaned).upper()
        if _LINE_TOKEN_RE.match(token):
            lines.add(token)

    return lines, event_name


def _normalize_name(name: str) -> str:
    """Lowercase, expand the Bahnhof-family abbreviations (Hbf/Bhf/Bf →
    …bahnhof) so an abbreviated station name and its spelled-out form
    tokenise identically, then drop digits for comparison.

    The abbreviation expansion is what lets ``wien`` safely join the stop
    words (bug b6): "Wien Hbf" and "Wien Hauptbahnhof" reduce to the same
    meaningful token ``hauptbahnhof`` and still dedup across providers,
    while two different routes that merely share "Wien" no longer do.
    """
    lowered = re.sub(r"\d+", "", name).lower()
    lowered = re.sub(r"\bhbf\b", "hauptbahnhof", lowered)
    lowered = re.sub(r"\bbhf\b", "bahnhof", lowered)
    lowered = re.sub(r"\bbf\b", "bahnhof", lowered)
    return lowered.strip()


def _get_tokens(name: str) -> set[str]:
    """Splits name into tokens by non-alphanumeric characters."""
    return set(x for x in re.split(r"\W+", _normalize_name(name)) if x)


# Numbers that pin a title to a specific platform / track. ONLY digits
# directly after a platform/track keyword are captured — a date ("ab 03.
# November") or line number is deliberately NOT, so a same-event pair on
# different dates still merges (bug b7). Linear; the keyword alternation is
# plain literals and the capture is a bounded ``\d+``.
_PLATFORM_NUMBER_RE = re.compile(
    r"\b(?:bahnsteigkante|bahnsteige|bahnsteig|gleise|gleis|steig|sektor|bstg)"
    r"\s*\.?\s*(\d+)",
    re.IGNORECASE,
)


def _platform_numbers(name: str) -> frozenset[str]:
    """Return the platform/track numbers a title pins to, e.g.
    ``frozenset({"1"})`` for "Bahnsteig 1 Wien Mitte". Two otherwise-
    overlapping titles that name DIFFERENT, non-overlapping platforms
    describe distinct incidents and must not be merged (bug b7)."""
    return frozenset(_PLATFORM_NUMBER_RE.findall(name))


def _has_significant_overlap(name1: str, name2: str) -> bool:
    """
    Checks if names share significant words or a long common substring.
    """
    return _has_significant_overlap_cached(
        _normalize_name(name1),
        _normalize_name(name2),
        _get_tokens(name1),
        _get_tokens(name2),
        _platform_numbers(name1),
        _platform_numbers(name2),
    )


def _has_significant_overlap_cached(
    n1: str,
    n2: str,
    t1: set[str],
    t2: set[str],
    p1: frozenset[str] = frozenset(),
    p2: frozenset[str] = frozenset(),
) -> bool:
    """Variant of ``_has_significant_overlap`` that consumes pre-computed
    normalized names and token sets.

    Performance: ``deduplicate_fuzzy``'s outer/inner loop is O(n²) over
    ``merged_items``. The original ``_has_significant_overlap`` recomputed
    ``_normalize_name`` (re.sub + lower + strip) and ``_get_tokens``
    (which itself calls ``_normalize_name`` again, plus an re.split) on
    BOTH names per pair — five regex operations × n² pairs. Caching the
    parsed values once when an item is appended to ``merged_items`` and
    reusing them on every comparison drops the parse work from O(n²) to
    O(n) without changing the comparison semantics.
    """
    # Distinct, non-overlapping platform/track numbers ("Bahnsteig 1" vs
    # "Bahnsteig 5") mark different incidents at the same station — never
    # merge them even when the rest of the title matches (bug b7). Only
    # fires when BOTH titles pin a platform, so a platform-specific item
    # still merges into a general one.
    if p1 and p2 and p1.isdisjoint(p2):
        return False
    if not n1 or not n2:
        return False

    # 1. Token Overlap
    intersection = t1 & t2
    union = t1 | t2
    if not union:
        return False

    # Compare only meaningful (non-stop-word) tokens. The intent: two
    # disruption titles must share something distinctive (typically a
    # station/place token) — sharing only "Störung" or "im Bereich" is not
    # enough.
    meaningful_intersection = intersection - _STOP_WORDS
    meaningful_union = union - _STOP_WORDS

    if not meaningful_union:
        # Both titles consist solely of stop words. Only merge if they are
        # token-identical — otherwise different stop-word combinations like
        # "Sperre wegen Bauarbeiten" and "Sperre wegen Polizeieinsatz"
        # would be falsely lumped together.
        return t1 == t2

    if not meaningful_intersection:
        # Distinguishing tokens exist somewhere, but none are shared. The
        # titles describe different events at the same generic level.
        return False

    # Token overlap threshold — measured against the meaningful tokens so
    # that sharing many fillers does not inflate the score.
    if len(meaningful_intersection) / len(meaningful_union) >= 0.4:
        return True

    return False


def _compute_overlap_cache(
    title: str,
) -> tuple[set[str], str, str, set[str]]:
    """Pre-compute the parse + normalize + tokenize results for one title.

    Performance: returned tuple captures everything ``deduplicate_fuzzy``'s
    inner loop needs — line set (from ``_parse_title``), event name,
    normalized name, and token set. The caller stores this alongside each
    ``merged_items`` entry so the inner loop never has to re-parse a
    previously-seen title.
    """
    lines, name = _parse_title(title)
    return lines, name, _normalize_name(name), _get_tokens(name)
