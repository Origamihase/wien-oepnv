"""Wiener Linien title cosmetics: generic leading labels and a redundant "ab <Datum>" leave the title."""

from __future__ import annotations

import re

# ---------------- Titel-Kosmetik ----------------

_LABELS = [
    r"bauarbeiten",
    r"straßenbauarbeiten",
    r"strassenbauarbeiten",
    r"gleisbauarbeiten",
    r"verkehrsinfo",
    r"verkehrsinformation",
    r"verkehrsmeldung",
    r"störung",
    r"stoerung",
    r"hinweis",
    r"serviceinfo",
    r"service\-info",
    r"information",
]
_LABEL_HEAD_RE = re.compile(
    r"^\s*(?:(?:" + "|".join(_LABELS) + r")\s*(?:[-:–—/]\s*|\s+))+",
    re.IGNORECASE,
)


def _is_informative(rest: str) -> bool:
    return bool(rest and re.search(r"[A-Za-zÄÖÜäöüß0-9]{3,}", rest))


# ``ab DD.MM.[YY|YYYY]`` — the advance-notice date WL glues onto titles
# (``Bauarbeiten ab 14.09.26``). The whole phrase is dropped from the
# rendered title because the validity window is carried by the item's
# ``starts_at`` / ``ends_at`` fields (and rendered as the ``[…]`` time
# line), so repeating it in the title is redundant.
#
# The year alternation MUST cover the 2-digit form. Pre-fix the pattern
# read ``(?:\d{4})?`` — an optional FOUR-digit year — so a real title
# ``"Bauarbeiten ab 14.09.26"`` matched only through the month's
# trailing dot: ``" ab 14.09."`` was removed and the orphaned ``"26"``
# stayed behind. The trailing ``\s+`` collapse then glued it onto the
# preceding word and the feed published ``"17A: Bauarbeiten26"``
# (observed live in ``docs/feed.xml`` and ``cache/wl_9d709a/events.json``
# on 2026-09-09; see ``docs/archive/audits/audit-title-bauarbeiten26-2026-09-09.md``).
#
# Ordered alternation (``\d{4}`` before ``\d{2}``) makes "2026" consume
# all four digits; the trailing ``(?!\d)`` guard prevents a partial
# consume on a malformed 3- or 5-digit year — the pattern then fails
# as a whole and the title is left untouched rather than half-stripped
# (the exact failure mode this fix exists to prevent).
_AB_DATE_RE = re.compile(
    r"(?:^|\s+)ab\s+\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?(?!\d)",
    re.IGNORECASE,
)


def _tidy_title_wl(title: str) -> str:
    """Entfernt generische Label am Anfang, wenn danach informativer Text steht.

    Real WL feed titles sometimes carry embedded newlines (``"Ersatzbus
    41E\\nhält beim 10A"``) that survived the previous ``\\s{2,}``
    collapse — a single newline matches ``\\s`` exactly once and so was
    preserved verbatim. RSS/Atom titles are single-line by convention,
    so we now collapse ANY whitespace run (including isolated newlines
    or tabs) to a single space.
    """

    t = (title or "").strip()
    if len(t) > 500:
        t = t[:500]
    if not t:
        return t
    # Order matters: drop the ``ab <Datum>`` phrase BEFORE the generic
    # label head. With the reverse order a title carrying a 4-digit year
    # (``"Bauarbeiten ab 14.09.2026"``) lost its label first — the
    # remainder ``"ab 14.09.2026"`` passes ``_is_informative`` because
    # ``2026`` is a 4-character alphanumeric run — and the date phrase
    # then no longer matched, because it had become the start of the
    # string with no leading whitespace. The published title degenerated
    # to the contentless ``"17A: ab 14.09.2026"``. Stripping the date
    # first leaves ``"Bauarbeiten"``, which the label head then keeps
    # (an empty remainder is not informative).
    t = _AB_DATE_RE.sub("", t)
    stripped = _LABEL_HEAD_RE.sub("", t)
    if stripped and _is_informative(stripped):
        t = stripped
    t = re.sub(r"[<>«»‹›]+", "", t)  # spitze Klammern/Anführungen
    return re.sub(r"\s+", " ", t).strip(" -–—:/\t")


__all__ = ["_AB_DATE_RE", "_tidy_title_wl"]
