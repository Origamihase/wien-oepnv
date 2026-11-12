"""Stop names without Wiener Linien's S-Bahn/U-Bahn marker.

Wiener Linien name a stop with an interchange to the S-Bahn or the U-Bahn
with a standalone "S" or "U" behind the place: "Rennweg S", "Schottentor
U", "Praterstern S U" (and once in front: "Parlament, U Volkstheater").
Their messages carry those names into title and text ("N71: Rennweg S",
"Fahrtbehinderung in Richtung Floridsdorf S U"). The feed shows the place
alone: "N71: Rennweg", "in Richtung Floridsdorf" (operator decision
2026-11-04: "Der Name „Rennweg S“ gefällt mir nicht … Gibt es weitere
Bahnhöfe mit einem S oder einem U? Dies würde mich auch in den Meldungen
stören.").

The rule is structural, not a word list: a marker only goes behind a word
that ends a stop name in the WL stop directory (OGD ``haltepunkte.csv``
``StopText`` and ``haltestellen.csv`` ``PlatformText``, refreshed with the
station data), so "in beiden Richtungen U." (a WL text cut before "U6") or
"der S-Bahn" stay as they are. The directory word may itself carry no
marker: WL wrote "Tscherttegasse U" and "Michelbeuern-AKH U" in messages
while the directory names those stops without one. In front of a name a
marker goes only before a word the directory writes behind one ("U
Volkstheater"). A marker in front of a digit ("Kagran U 26A") or glued to a
hyphen ("U-Bahn") is no stop marker and stays. A new stop reaches the rule
with the next directory refresh, without a code change. A name WL writes
with dots the directory leaves out counts as that name ("Kaisermühlen,
V.I.C. U" for "Kaisermühlen, VIC"), and its last period also ends the
sentence ("… V.I.C. U." → "… V.I.C."; since 2026-11-12, when the cut at 180
characters stopped dropping the "U." of a 92B text of 18.08.2026 as if it
were an abbreviation).

The same goes for the "(WL)" the station directory puts behind a name of a
Wiener Linien stop to keep it apart from a railway station ("Wien
Liechtenwerder Platz (WL)", "Wien Baumgarten (WL)"): it says where the
entry came from, not where the stop is, and no source writes it (operator
decision 2026-11-08: "Den Zusatz „WL“ kannst du gerne, so wie „S“ oder „U“
auch entfernen.").

Only the display changes (``_format_item_content``): matching, merging,
twins and the map position still read the names as WL sends them.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from functools import lru_cache
from pathlib import Path

from .files import read_capped_text

__all__ = ["without_stop_markers"]

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parents[2] / "data"
# (path, name column) of the two WL stop lists.
_WL_STOP_LISTS: tuple[tuple[Path, str], ...] = (
    (_DATA / "wienerlinien-ogd-haltepunkte.csv", "StopText"),
    (_DATA / "wienerlinien-ogd-haltestellen.csv", "PlatformText"),
)
MAX_WL_STOP_LIST_FILE_BYTES = 10 * 1024 * 1024

# A marker run: "S", "U" or "S U", each letter standing alone.
_MARKER_RUN = r"(?:S|U)(?:\s+(?:S|U))*"
# A word of a name: letters, then letters, digits, dots and hyphens
# ("Bhf.", "Kaisermühlen-VIC", "Franz-Josefs-Bahnhof").
_WORD = r"[^\W\d_][\w.\-]*"
_MARKER_TOKEN_RE = re.compile(r"^(?:S|U)$")
# A name splits into places at a comma, a bracket or a spaced dash
# ("Liechtensteinstraße - Schottentor U", "Quartier Belvedere S (Expedit)").
_NAME_SEGMENT_SPLIT_RE = re.compile(r",|\(|\)|\s[-–]\s")
_DIRECTORY_PREFIX_RE = re.compile(rf"(?:^|,\s*){_MARKER_RUN}\s+({_WORD})")
# Where a name may start: not inside a word, but behind a list dash glued
# to it ("-Johnstraße U", "-Simmering S U", WL lists of 2026).
_NAME_START = r"(?:(?<![\w.\-])|(?<=^-)|(?<=\s-))"
# In a text: the word, the marker run, and then no word character, no
# hyphen and no number ("U-Bahn", "U6", "Kagran U 26A" stay). A period
# behind the marker comes along, for a name that ends in its own period.
_TEXT_SUFFIX_RE = re.compile(
    rf"(?P<word>{_NAME_START}{_WORD})(?P<marker>\s+{_MARKER_RUN})(?![\w\-]|\s*\d)(?P<period>\.?)"
)
_TEXT_PREFIX_RE = re.compile(
    rf"(?<![\w.\-])(?P<marker>{_MARKER_RUN}\s+)(?P<word>{_WORD})(?![\w\-])"
)
# A bracket that holds only marker letters, behind a name: "Wien Hbf (U)",
# "Wien Leopoldau Bahnhst (U)" (ÖBB texts), "Simmering (S U)" (WL). Such a
# bracket says nothing but the interchange, so it goes whole, with the
# space before it; the ÖBB title already drops it
# (``providers.oebb_routes._without_transfer_markers``). Each separator is
# either one sign with spaces or spaces alone, so a long "(S S S …" has one
# reading only (no backtracking blow-up, CodeQL 2026-11-04).
_BRACKET_MARKER_RE = re.compile(
    r"(?<=[^\W_])\.?\s*\(\s*[SU](?:(?:\s*[+/,&]\s*|\s+)[SU])*\s*\)(?![\w\-])"
)


# The directory's provenance marker behind a name ("Wien Baumgarten (WL)"),
# with the space before it. Only the exact bracket: no source text writes
# "(WL)" (ÖBB, WL and Baustellen caches and raw data checked 2026-11-08).
_PROVENANCE_MARKER_RE = re.compile(r"(?<=[^\W_]|\.)\s*\(WL\)(?![\w\-])")


@lru_cache(maxsize=1)
def _marker_words() -> tuple[frozenset[str], frozenset[str]]:
    """Casefolded words a marker may follow, and words a marker may stand in front of."""

    ends: set[str] = set()
    after: set[str] = set()
    for path, column in _WL_STOP_LISTS:
        text = read_capped_text(
            path,
            MAX_WL_STOP_LIST_FILE_BYTES,
            encoding="utf-8-sig",
            label=f"WL OGD {path.stem}",
            logger=logger,
        )
        if text is None:
            continue
        for row in csv.DictReader(io.StringIO(text), delimiter=";"):
            name = " ".join((row.get(column) or "").split())
            after.update(m.group(1).casefold() for m in _DIRECTORY_PREFIX_RE.finditer(name))
            for segment in _NAME_SEGMENT_SPLIT_RE.split(name):
                tokens = segment.split()
                # The word before a marker ("Michelbeuern U AKH") ...
                ends.update(
                    tokens[i - 1].casefold()
                    for i, token in enumerate(tokens)
                    if i
                    and _MARKER_TOKEN_RE.match(token)
                    and not _MARKER_TOKEN_RE.match(tokens[i - 1])
                    and re.fullmatch(_WORD, tokens[i - 1])
                )
                # ... and the last word of every place ("Tscherttegasse").
                words = [w for w in tokens if not _MARKER_TOKEN_RE.match(w)]
                if words and re.fullmatch(_WORD, words[-1]):
                    ends.add(words[-1].casefold())
    return frozenset(ends), frozenset(after)


def without_stop_markers(text: str) -> str:
    """*text* with every WL stop marker of the directory removed (see above).

    "N71: Rennweg S" → "N71: Rennweg"; "zwischen Kagran U und Praterstern
    S U." → "zwischen Kagran und Praterstern."; "Parlament, U Volkstheater"
    → "Parlament, Volkstheater"; "Wien Baumgarten (WL)" → "Wien Baumgarten".
    Text without such a name comes back as is.
    """

    if text and "(WL)" in text:
        text = _PROVENANCE_MARKER_RE.sub("", text)
    if not text or ("S" not in text and "U" not in text):
        return text
    text = _BRACKET_MARKER_RE.sub(lambda m: "." if m[0].startswith(".") else "", text)
    ends, after = _marker_words()
    if not ends and not after:
        return text

    def known(word: str, words: frozenset[str]) -> bool:
        # A name at the end of a sentence carries its period: "U Volkstheater."
        # WL writes the directory's "VIC" with dots: "Kaisermühlen, V.I.C. U".
        key = word.casefold()
        return key in words or key.rstrip(".") in words or key.replace(".", "") in words

    def suffix(match: re.Match[str]) -> str:
        if not known(match["word"], ends):
            return match[0]
        # "Floridsdorf S U." keeps the sentence's period, "V.I.C. U." its own.
        return match["word"] if match["word"].endswith(".") else match["word"] + match["period"]

    def prefix(match: re.Match[str]) -> str:
        if known(match["word"], after):
            return match["word"]
        return match[0]

    return _TEXT_PREFIX_RE.sub(prefix, _TEXT_SUFFIX_RE.sub(suffix, text))
