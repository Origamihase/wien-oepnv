"""Combining the titles and descriptions of merged items (:mod:`src.feed.merge`).

Helper-to-helper calls resolve here; patch this module to reach them.
"""

import re
import string

# Helper for collapsing two merged names that share a substantial common
# word-aligned prefix. Without this collapse, the merge name-combining
# logic concatenates with ``&`` and produces ugly walls of text when WL
# ships near-identical Hinweis items that only differ by date or
# location. Real cache examples (current snapshot):
#
#   * ``11A: Veranstaltung am 09.06.2026`` × 4 items with different
#     dates merged into the 122-char title
#     ``11A: Veranstaltung am 09.06.2026 & Veranstaltung am 03.06.2026
#     & Veranstaltung am 11.06.2026 & Veranstaltung am 20.06.2026``
#   * ``43: Benutzen Sie die Linie 43A - Dornbacher Straße 85`` +
#     ``43: Benutzen Sie die Linie 43A - Alszeile 93`` merged into
#     the 96-char title repeating ``Benutzen Sie die Linie 43A`` twice
#
# The collapse keeps the shared prefix once and joins the differing
# suffixes with ``, ``. When ALL suffix parts are calendar dates
# (``DD.MM.YYYY``), they get sorted chronologically so the user sees
# events in time order rather than cache-insertion order.
_DATE_PIECE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def _date_sort_key(part: str) -> tuple[int, int, int] | None:
    match = _DATE_PIECE_RE.match(part.strip())
    if not match:
        return None
    day, month, year = match.groups()
    return (int(year), int(month), int(day))


def _collapse_common_prefix(
    ex_name: str,
    name: str,
    *,
    min_prefix: int = 10,
    max_suffix: int = 60,
) -> str | None:
    """Combine two names by sharing a common word-aligned prefix.

    Returns the collapsed name, or ``None`` to indicate the caller
    should fall back to the existing ``ex_name & name`` join.

    Constraints:

    * Substantial overlap — at least ``min_prefix`` shared characters,
      ending at a word boundary (space) so we never split mid-word.
    * Short new suffix — only the *incoming* remainder
      (``name_remainder``) must be ≤ ``max_suffix`` chars. Joining two
      long sentences with a comma is harder to read than the explicit
      ``&`` join the existing logic produces. The existing remainder
      (``ex_remainder``) is intentionally **not** capped because it
      accumulates previously-collapsed comma-separated parts (e.g. a
      growing date list) across successive merges.
    * No directional arrow — ÖBB chain routes carrying ``↔`` use that
      character as a chain joiner (``A ↔ B ↔ C``), not a separator,
      so collapsing them by common prefix would mangle the route.
    """
    if not ex_name or not name:
        return None
    if "↔" in ex_name or "↔" in name:
        return None

    n = min(len(ex_name), len(name))
    common_len = 0
    for i in range(n):
        if ex_name[i] != name[i]:
            break
        common_len = i + 1
    if common_len < min_prefix:
        return None
    # Backtrack to the last word boundary so the prefix never splits a
    # word in half (e.g. ``Veranstaltun`` from ``Veranstaltungen`` vs
    # ``Veranstaltung am``).
    while common_len > 0 and not ex_name[common_len - 1].isspace():
        common_len -= 1
    if common_len < min_prefix:
        return None

    prefix = ex_name[:common_len]
    ex_remainder = ex_name[common_len:].strip()
    name_remainder = name[common_len:].strip()
    if not ex_remainder or not name_remainder:
        return None
    if len(name_remainder) > max_suffix:
        return None

    # ``ex_remainder`` may already be a comma-separated list if a
    # previous merge ran through this helper. Split, dedup, append the
    # new suffix.
    ex_parts = [p.strip() for p in ex_remainder.split(",") if p.strip()]
    if name_remainder in ex_parts:
        # The new suffix is already present (e.g. duplicate Hinweis
        # republished by WL). Return the existing name unchanged.
        return ex_name

    all_parts = ex_parts + [name_remainder]
    # If every part is a calendar date, sort chronologically so the
    # user sees ``03.06.2026, 09.06.2026, 11.06.2026`` rather than
    # the cache-insertion order ``09.06.2026, 03.06.2026, 11.06.2026``.
    sort_keys = [_date_sort_key(p) for p in all_parts]
    if all(k is not None for k in sort_keys):
        all_parts = [p for _, p in sorted(zip(sort_keys, all_parts, strict=True))]

    return f"{prefix.rstrip()} " + ", ".join(all_parts)


_SENTENCE_TERMINATOR_RE = re.compile(r"[.!?](?=\s|$)")


def _collapse_description_prefix(desc1: str, desc2: str) -> str | None:
    """Collapse two merged descriptions on a shared prefix — or decline.

    The rule the title already follows (:func:`_collapse_common_prefix`),
    with one boundary the title never needed: a description written as
    a **sentence** keeps the blank-line join. The comma the collapse
    inserts would otherwise land behind the full stop —
    ``Details about Lauf., Pfad.`` — and a text of several sentences
    would see two different second sentences glued into one statement.
    WL's clause-form descriptions (``Schadhafter Bus Betrieb ab
    Praterstern``) carry no terminator and collapse cleanly; those are
    the measured cases.
    """
    if _SENTENCE_TERMINATOR_RE.search(desc1) or _SENTENCE_TERMINATOR_RE.search(desc2):
        return None
    return _collapse_common_prefix(desc1, desc2)


# The arrows and the whitespace between and around them: WL writes "both
# directions" as ``< >``, with a space. A character set for ``str.rstrip``
# rather than a regex — ``(?:\s*[<>]+)+`` backtracked exponentially on a
# long run of arrows (CodeQL, PR #1850).
_TRAILING_DIRECTIONAL_CHARS = string.whitespace + "<>"


def _trim_trailing_directional(text: str) -> str:
    """Strip a trailing WL ``<``/``>`` arrow marker and surrounding space.

    Mirrors ``_strip_trailing_directional_marker`` in ``build_feed`` but
    runs at the merge stage so the marker doesn't end up *between* two
    concatenated descriptions (where the per-item output strip in
    ``_format_item_content`` can no longer reach it).
    """
    if not text:
        return text
    return text.rstrip(_TRAILING_DIRECTIONAL_CHARS)


def _restates_title(desc: str, title_body: str) -> bool:
    """True when *desc* says nothing the merged title does not already say.

    The emitter's ``_summary_duplicates_title`` drops a description that
    IS the title (#1836). It cannot see one paragraph of a stacked
    description, because by the time it runs the paragraphs have been
    collapsed into a single line. So the check happens here, before the
    stacking: a description that appears word for word in the merged
    title body is dropped and the other one stands alone.

    Published 2026-09-19::

        T: 2: Demonstration Züge halten Steig A & Züge halten bei Linie 46
        D: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen
           Intervallen. Züge halten bei Linie 46

    The second paragraph is the second item's headline-only description;
    alone it would have been emptied at emission, merged it survived and
    repeated what the title says three centimetres above it.

    Whole words only: ``Betrieb ab Gersthof`` is restated by ``… Betrieb ab
    Gersthof & …``, not by ``… Betrieb ab Gersthofer Straße``.
    """
    words = " ".join(_trim_trailing_directional(desc).split()).casefold().strip(" .!?")
    if not words:
        return False
    haystack = " ".join(title_body.split()).casefold()
    return re.search(rf"(?<!\w){re.escape(words)}(?!\w)", haystack) is not None


def _drop_restated(desc1: str, desc2: str, title_body: str) -> tuple[str, str]:
    """Empty whichever of the two descriptions the merged title restates."""
    return (
        "" if _restates_title(desc1, title_body) else desc1,
        "" if _restates_title(desc2, title_body) else desc2,
    )


def _join_merged_names(ex_name: str, name: str) -> str:
    """Combine two non-identical bodies — prefer prefix collapse, else ``&``.

    Extracted from :func:`deduplicate_fuzzy` to keep its McCabe count at
    the baselined 21. Tries :func:`_collapse_common_prefix` first
    (produces the user-friendly ``Veranstaltung am DATE1, DATE2`` form
    when both bodies share a substantial word-aligned prefix), and
    falls back to the legacy ``ex_name & name`` join when the collapse
    declines (short overlap, long suffix, ÖBB ``↔`` chain).
    """
    return _collapse_common_prefix(ex_name, name) or f"{ex_name} & {name}"
