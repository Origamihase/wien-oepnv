"""Station-name tokens, alias generation and coordinate coercion.

Moved verbatim from ``src/utils/stations.py`` (700-line rule, 2026-11-02);
the public facade stays ``src.utils.stations``. Lowest layer: this module
must not import the facade or the other ``stations_*`` parts.
"""

from __future__ import annotations

import math
import re
from enum import IntEnum
from functools import cache
from collections.abc import Iterable

from .text import strip_accents

_AN_DER_RE = re.compile(r"\ba\s*(?:[./]\s*)?d(?:[./]\s*)?\b")
_SANKT_RE = re.compile(r"\bst[. ]?\b")
_STATION_WORD_RE = re.compile(r"\b(?:bahnhof|bahnhst|bhf|hbf|bf)\b")
_NOT_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SPACES_RE = re.compile(r"\s{2,}")


# Remembered without limit: the two station indexes (``_station_lookup`` and
# ``_name_keys`` in ``stations_lookup``) ask for the same 250,000 names one
# after the other, 497,159 calls per process (measured 2026-11-10). The
# function depends on its argument alone.
@cache
def _normalize_token(value: str) -> str:
    """Produce a canonical lookup token for a station alias.

    The umlaut transliteration fold (``ae→a``, ``oe→o``, ``ue→u``) handles
    ASCII-style spellings of German names like ``Mueller`` matching ``Müller``.
    Skipping it for tokens of length ≤ 3 keeps short ÖBB Stellencodes
    distinct after normalization — e.g. ``Sue`` (Wien Süßenbrunn) and ``Su``
    (Stockerau) must not both collapse to ``su``.
    """

    if not value:
        return ""

    text = strip_accents(value)
    text = text.replace("ß", "ss")
    text = text.casefold()
    text = _AN_DER_RE.sub("an der ", text)
    if len(text) > 3:
        text = text.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    text = _SANKT_RE.sub("sankt ", text)
    text = _STATION_WORD_RE.sub("", text)
    text = _NOT_ALNUM_RE.sub(" ", text)
    text = _SPACES_RE.sub(" ", text)
    return text.strip()


def _name_owns_alias(name: str, alias_token: str) -> bool:
    """Return True when *name* designates the station the alias is named after.

    The eponymous station — the one literally called *alias_token* — should
    win that alias over a neighbour that merely carries it as a cross-reference.
    Comparing only ``_normalize_token(name)`` missed this because the stored
    name keeps the ``Wien`` city qualifier and a ``(WL)`` / ``(VOR)`` provider
    suffix (``"Wien Taborstraße (WL)"`` → ``"wien taborstrasse wl"``), which
    never equals the bare ``"taborstrasse"`` key. The bare form is compared too.
    """
    if not alias_token:
        return False
    if _normalize_token(name) == alias_token:
        return True
    bare = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    bare = re.sub(r"^\s*[Ww]ien\s+", "", bare)
    return _normalize_token(bare) == alias_token


def _source_token_set(value: object) -> frozenset[str]:
    """Return the canonical set of provider source tokens for a station entry.

    Tolerates either a comma-separated string (with or without spaces) or a
    list of strings. The legacy value ``"combined"`` is normalized to ``"wl"``
    for backwards compatibility with older cache entries.
    """

    if isinstance(value, str):
        tokens = {tok.strip() for tok in value.split(",") if tok.strip()}
    elif isinstance(value, list | tuple | set | frozenset):
        tokens = {
            str(tok).strip()
            for tok in value
            if isinstance(tok, str) and tok.strip()
        }
    else:
        tokens = set()
    if "combined" in tokens:
        tokens.discard("combined")
        tokens.add("wl")
    return frozenset(tokens)


def _coerce_float(value: object | None) -> float | None:
    """Return *value* as float if possible (accepting comma decimal separators)."""

    if value is None:
        return None

    val: float
    if isinstance(value, int | float):
        val = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        text = text.replace(",", ".")
        try:
            val = float(text)
        except ValueError:
            return None

    if not math.isfinite(val):
        return None
    return val


def _coerce_lat(value: object | None) -> float | None:
    """Coerce value to a valid latitude.

    Bounds span Europe so the directory can store distant terminus
    stations (Berlin, Roma, Praha, Venezia, Zagreb, Budapest-Keleti).
    The Wien containment decision lives in :func:`is_in_vienna`'s
    polygon test, not in these bounds.
    """
    val = _coerce_float(value)
    if val is not None and (35.0 <= val <= 65.0):
        return val
    return None


def _coerce_lon(value: object | None) -> float | None:
    """Coerce value to a valid longitude.

    See :func:`_coerce_lat` for the rationale behind the European
    bounds.
    """
    val = _coerce_float(value)
    if val is not None and (-10.0 <= val <= 30.0):
        return val
    return None


class _MatchStrength(IntEnum):
    """Strength of a match between an alias token and a station record.

    Higher values win in tie-breaking. ``IDENTITY``-class tokens come from
    fields that uniquely identify a station (``bst_code``, ``bst_id``,
    ``vor_id``, ``wl_diva``, ``stop_id``). ``TEXT``-class tokens come from
    the canonical name, its textual variations, and the freeform
    ``aliases`` list. An IDENTITY match by one station outranks a TEXT
    match by another station for the same token, which is what prevents a
    foreign station's ``aliases`` entry from shadowing the rightful
    ID-bearing station (the root cause of the '900100' bug, see #1082).
    """

    TEXT = 1
    IDENTITY = 2


def _iter_aliases_with_strength(
    name: str,
    code: str | None,
    identity_extras: Iterable[str] | None = None,
    text_extras: Iterable[str] | None = None,
) -> list[tuple[str, _MatchStrength]]:
    """Yield ``(alias, strength)`` pairs for a station entry.

    The canonical *name* and its variations (bracket-stripped, hyphen and
    slash flattened, St./Sankt forms) are emitted as
    :attr:`_MatchStrength.TEXT`. The *code* (a station's ``bst_code``) and
    any *identity_extras* (``vor_id``, ``wl_diva``, stop IDs) are emitted
    as :attr:`_MatchStrength.IDENTITY`. Entries in *text_extras*
    (``aliases`` array, stop names) are emitted as TEXT.

    Order: first the
    name and its variations, then identity_extras, then text_extras. The
    first occurrence of a candidate string wins, so identity-class wins
    over text-class on duplicates within a single station entry.
    """

    seen: set[str] = set()
    variants: list[tuple[str, _MatchStrength]] = []

    def add(raw: str, strength: _MatchStrength) -> None:
        candidate = re.sub(r"\s{2,}", " ", raw.strip())
        if candidate and candidate not in seen:
            seen.add(candidate)
            variants.append((candidate, strength))

    add(name, _MatchStrength.TEXT)
    if code:
        add(code, _MatchStrength.IDENTITY)

    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    add(no_paren, _MatchStrength.TEXT)
    add(no_paren.replace("-", " "), _MatchStrength.TEXT)
    add(no_paren.replace("/", " "), _MatchStrength.TEXT)
    add(name.replace("-", " "), _MatchStrength.TEXT)
    add(name.replace("/", " "), _MatchStrength.TEXT)

    if re.search(r"\bSt\.?\b", name):
        add(re.sub(r"\bSt\.?\b", "St", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSt\.?\b", "St ", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSt\.?\b", "Sankt ", name), _MatchStrength.TEXT)
    if re.search(r"\bSankt\b", name):
        add(re.sub(r"\bSankt\b", "St.", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSankt\b", "St ", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSankt\b", "St", name), _MatchStrength.TEXT)

    if identity_extras:
        for extra in identity_extras:
            add(str(extra), _MatchStrength.IDENTITY)

    if text_extras:
        for extra in text_extras:
            add(str(extra), _MatchStrength.TEXT)

    return variants
