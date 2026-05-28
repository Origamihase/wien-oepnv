"""Helpers for normalising place names and computing distances."""

from __future__ import annotations

import re
import unicodedata

# The canonical Haversine implementation lives in ``src.utils.geo``;
# re-export under the legacy ``haversine_m`` name so existing
# ``src.places.merge`` callers keep working without churn.
from ..utils.geo import calculate_distance_meters as haversine_m

__all__ = ["normalize_name", "haversine_m"]


# Runs of any non-alphanumeric ASCII character (punctuation, hyphens,
# slashes, brackets, separators, leftover non-Latin glyphs, etc.) are
# collapsed to a single space. Applied AFTER casefold + accent-strip so
# the remaining alphabet is reliably ``[0-9a-z]``.
_NON_ALNUM_RUN_RE = re.compile(r"[^0-9a-z]+")

# Hard cap on the normalised result. The matcher in
# :mod:`src.places.merge` only ever compares for equality, so the cap
# bounds matcher memory without affecting comparison semantics for any
# real-world station name (the longest legitimate entry is ~80 chars).
_NORMALISED_MAX_LEN = 250


def _strip_accents(value: str) -> str:
    """Return ``value`` without any accent characters."""

    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    """Normalise ``name`` for fuzzy comparisons.

    Whitespace is collapsed, case is folded, accents are stripped, and
    runs of punctuation / non-alphanumeric characters are collapsed to
    a single space. The result is bounded at :data:`_NORMALISED_MAX_LEN`
    characters.

    Without the punctuation collapse the station-dedup matcher in
    :mod:`src.places.merge` treated ``"Wien Hbf"`` and ``"Wien Hbf."``
    (or ``"Wien-Hbf"``) as different stations and produced duplicate
    rows whenever upstream sources disagreed on a trailing dot, a
    hyphen, or a parenthetical suffix. The length cap used to be a
    pre-normalisation short-circuit (``if len(name) > 250: return name``)
    that returned the raw input unchanged — so a case-mixed overlong
    string and its lowercased twin compared unequal, defeating the
    same dedup invariant.
    """
    stripped = " ".join(name.strip().split())
    lowered = stripped.casefold()
    no_accents = _strip_accents(lowered)
    collapsed = _NON_ALNUM_RUN_RE.sub(" ", no_accents).strip()
    if len(collapsed) > _NORMALISED_MAX_LEN:
        collapsed = collapsed[:_NORMALISED_MAX_LEN].rstrip()
    return collapsed
