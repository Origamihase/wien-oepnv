"""Stop relocation fields (``Ersatzhaltestelle`` arrows) of a summary.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from .abbreviations import without_full_stop

__all__ = [
    "_BLOCK_TERMINAL_PUNCT", "_WORD_RE", "_RELOCATION_ARROW", "_relocation_first",
]


# A paragraph end is a sentence end. WL builds its notices from paragraphs
# and headings, and the plain-text conversion turned every boundary into a
# bare space, so a heading or a field ran straight into the next one::
#
#     U1: Starke Nachfrage Die U1 wird aufgrund der ÖBB S-Bahn-Sperre …
#     Bauarbeiten Wien Stammstrecke Phase 2 Zeitraum: Ab Montag, …
#     … Schwedenplatz U Haltestelle: Stammersdorf Von: Brünner Straße
#     gegenüber 262 Ersatzlos aufgelassen Dauer: Ab 30. September …
#
# A block that ends without punctuation gets the full stop it stands for —
# unless the sentence plainly goes on in the next block: WL also breaks
# paragraphs inside a sentence ("… der damit einhergehenden" / "prov.
# Einbahnführung …"), so a next block that opens lower-case, or a block
# that ends on a function word, is joined without one. A leading block
# whose words the title already carries all of ("U1: Starke Nachfrage",
# "Bauarbeiten S80" under "S80: Bauarbeiten") is the heading repeated and
# goes. A single word is WL's category heading ("<h2>Gleisbauarbeiten</h2>").
# A word of :data:`_CATEGORY_PREFIX_WORDS` is left alone, because
# :func:`_strip_summary_category_prefix` and :func:`_reason_only_summary`
# handle it as a bare word. Any other heading word ran into the text on the
# TV ("Bahnsteigsanierung Wegen Sanierung des Bahnsteigs hält …", U6 Neue
# Donau, 05.10.2026; 11 heading words since July, "Laufsportveranstaltung",
# "Netzänderung", "Kanalgebrechen" among them), so it goes like the
# repeated heading: the text behind it says what happens, mostly opening
# with "Wegen …" and the cause itself.
_BLOCK_TERMINAL_PUNCT = ".!?:;,…"
_WORD_RE = re.compile(r"\w+")


# A WL stop relocation is a list of fields: the lines and their directions,
# "Haltestelle:", "Von:", "Nach:", "Dauer:", "Grund:". What a rider needs is
# where the stop went, and that came fourth: on the TV on 05.10.2026 slide 10
# read "Haltestellenverlegung der Linie 26E in Richtung Josef-Baumann-Gasse
# und Linie N20 in Richtung Eßling, Stadtgrenze. Haltestelle: Fultonstraße.
# Von: Donaufelder Straße …" and the 180-character cut took "Nach: Donaufelder
# Straße 40-42". 46 of 139 relocations cached since July lost their "Nach:"
# that way. "Von:" and "Nach:" now lead as "Donaufelder Straße 48 →
# Donaufelder Straße 40-42", the directions follow, and a "Haltestelle:" that
# only repeats the title goes (operator decision 2026-10-05, "Von/Nach
# zuerst", then the arrow). A cut then shortens the directions.
_RELOCATION_FROM = "Von:"
_RELOCATION_TO = "Nach:"
_RELOCATION_STOP = "Haltestelle:"
_RELOCATION_ARROW = "→"
# A field of the notice ("Haltestelle:", "Grund:"); any other block is a
# direction line ("Haltestellenverlegung der Linie 17A in Richtung …").
_RELOCATION_FIELD_RE = re.compile(r"[^\W\d]+:\s")
# A closed stop has no new place: WL writes "Nach: Ersatzlos aufgelassen".
# Behind the arrow that read as a destination ("Hartäckerstraße 65 →
# Ersatzlos aufgelassen", 40A on 06.10.2026; 5A/5B Malzgasse in August), so
# the old place takes a colon instead: "Hartäckerstraße 65: ersatzlos
# aufgelassen".
# WL also writes a bare "Nach: aufgelassen" (56A/56B/N62 Strampfergasse,
# 20.10.2026).
_RELOCATION_CLOSED_RE = re.compile(r"(?:ersatzlos\s+)?aufgelassen\b", re.IGNORECASE)
# A notice for both directions of one stop sets each direction as a field of
# its own, its "Von:" and "Nach:" below it ("Fahrtrichtung: Bhf. Atzgersdorf
# S bzw. Lainzer Tor"). Both "Von:" can name the same place: on 24.10.2026
# the TV read "Hermesstraße nach Strampfergasse → etwa 100 Meter in Richtung
# Dr.-Schober-Straße. Hermesstraße nach Strampfergasse → aufgelassen." —
# moved and closed at once. Each pair now leads with its direction, the
# first destination WL names: "Richtung Bhf. Atzgersdorf S: … → …".
_RELOCATION_DIRECTION = "Fahrtrichtung:"


# Two destinations do not always fit the 180 characters: "40A: Bauernfeldplatz"
# read "Richtung Döblinger Friedhof, Felix-Dahn-Straße: Liechtensteinstraße 42 →
# Liechtensteinstraße 38. Richtung Schottentor U: Liechtensteinstraße 37a
# gegenüber Bauernfeldplatz → …" (27.–29.10.2026, Jules audits 28./29.10.), the
# new stop towards Schottentor U was cut. When a destination would fall behind
# the cut, the old places go and every destination keeps its arrow: "Richtung
# Schottentor U: → Liechtensteinstraße 39." (operator decision 2026-10-29, "Von
# weglassen, wenn zu lang"). A text that shows every destination stays as it is.
#
# Whether a destination is shown is measured with the feed's own cut
# (``_truncate_summary_180``), not with a count of 180 characters: the cut
# ends at 175 characters on a word and, until 2026-11-11, dropped any bare
# number with a full stop behind it, so a house number at character 167 fell
# off. "54B: Stock im Weg" read "… Ghelengasse gegenüber 28 → Ghelengasse
# gegenüber …" on 11.11.2026 (KI-Wächter, issue #2200). Since 2026-11-12 the
# cut keeps a number that ends its sentence (docs/architecture-teil-16.md,
# §13). A destination counts as shown only when the cut text holds it whole.


def _relocation_targets_shown(blocks: list[str], targets: Sequence[str], cut: Callable[[str], str]) -> int:
    """How many of *targets* the feed's 180-character *cut* of *blocks*, closed as sentences, shows whole."""
    text = cut(
        " ".join(b if b.endswith(tuple(_BLOCK_TERMINAL_PUNCT)) else f"{b}." for b in blocks)
    )
    return sum(1 for target in targets if _shows_whole(text, target))


def _shows_whole(text: str, target: str) -> bool:
    """Whether *text* holds *target* as whole words ("Gasse 32", not "Gasse 3")."""
    index = text.find(target)
    while index >= 0:
        end = index + len(target)
        if end == len(text) or not text[end].isalnum():
            return True
        index = text.find(target, index + 1)
    return False


def _without_origins(blocks: list[str]) -> list[str]:
    """*blocks* without their ``Von:`` fields, each ``Nach:`` as ``→ B`` (see above)."""
    out: list[str] = []
    for block in blocks:
        if block.startswith(_RELOCATION_FROM):
            continue
        if block.startswith(_RELOCATION_TO):
            target = block[len(_RELOCATION_TO):].strip()
            if not target:
                continue
            if _RELOCATION_CLOSED_RE.match(target):
                block = f"{target[0].lower()}{target[1:]}"
            else:
                block = f"{_RELOCATION_ARROW} {target}"
        out.append(block)
    return out


def _relocation_first(blocks: list[str], title_words: set[str], cut: Callable[[str], str]) -> list[str]:
    """Put a relocation's ``Von:`` and ``Nach:`` fields in front; old places go if a destination would be cut.

    *cut* is the feed's 180-character cut (``_truncate_summary_180``, passed in
    by :mod:`.summary_text`, which imports this module).
    """
    if not any(block.startswith(_RELOCATION_TO) for block in blocks):
        return blocks
    ordered = _relocation_ordered(blocks, title_words)
    targets = list(dict.fromkeys(
        b[len(_RELOCATION_TO):].strip() for b in blocks if b.startswith(_RELOCATION_TO)
    ))
    targets = [t for t in targets if t and not _RELOCATION_CLOSED_RE.match(t)]
    shown = _relocation_targets_shown(ordered, targets, cut)
    if shown >= len(targets):
        return ordered
    short = _without_origins(_relocation_ordered(blocks, title_words, keep_origins=False))
    return short if _relocation_targets_shown(short, targets, cut) > shown else ordered


def _relocation_ordered(blocks: list[str], title_words: set[str], *, keep_origins: bool = True) -> list[str]:
    """``Von:``/``Nach:`` in front of their directions; without origins only the destinations."""
    if not keep_origins:
        blocks = [b for b in blocks if not b.startswith(_RELOCATION_FROM)]
    # One notice can list the same stop for two lines ("12A" and "N8" at
    # Längenfeldgasse U): their "Von:" and "Nach:" are the same words.
    lead = list(dict.fromkeys(
        b for b in blocks if b.startswith((_RELOCATION_FROM, _RELOCATION_TO))
    ))
    rest = [
        b
        for b in blocks
        if not (
            b.startswith(_RELOCATION_STOP)
            and {w.casefold() for w in _WORD_RE.findall(b[len(_RELOCATION_STOP):])}
            <= title_words
        )
    ]
    if sum(b.startswith(_RELOCATION_DIRECTION) for b in rest) >= 2:
        return _relocation_by_direction(rest)
    # Without its "Von:" a destination stays in front of its own direction.
    if len(lead) <= 2 and keep_origins:
        return _relocation_arrows(
            lead + [b for b in rest if not b.startswith((_RELOCATION_FROM, _RELOCATION_TO))]
        )
    # Several stops or both directions in one notice: each "Von:"/"Nach:"
    # belongs to the direction above it, so it moves in front of that
    # direction, not of the whole text. "15A/7A/N62: Eibesbrunnergasse" kept
    # its order until 05.10.2026 and ended on the TV with "Wienerbergstraße
    # 27b-27c → …": the first new place stood behind three lines and their
    # directions.
    ordered: list[str] = []
    group_start = 0
    for block in rest:
        if block.startswith((_RELOCATION_FROM, _RELOCATION_TO)):
            ordered.insert(group_start, block)
            group_start += 1
            continue
        if not _RELOCATION_FIELD_RE.match(block):
            group_start = len(ordered)
        ordered.append(block)
    return _relocation_arrows(ordered)


def _relocation_by_direction(blocks: list[str]) -> list[str]:
    """Each direction's ``Von:``/``Nach:`` as ``Richtung X: A → B``, in front (see above)."""
    pairs: list[str] = []
    others: list[str] = []
    group: list[str] = []
    direction = ""

    def flush() -> None:
        prefix = f"Richtung {direction}: " if direction else ""
        pairs.extend(f"{prefix}{pair}" for pair in _relocation_arrows(group))
        group.clear()

    origins: set[str] = set()
    for block in blocks:
        if block.startswith(_RELOCATION_DIRECTION):
            flush()
            direction = re.split(r"\s+bzw\.\s+", block[len(_RELOCATION_DIRECTION):].strip(), maxsplit=1)[0]
            direction = without_full_stop(direction)
        elif block.startswith(_RELOCATION_FROM):
            origin = block[len(_RELOCATION_FROM):].strip().rstrip(".")
            # The same old place as a direction before: said once.
            if origin not in origins:
                group.append(block)
            origins.add(origin)
        elif block.startswith(_RELOCATION_TO):
            if group and group[-1].startswith(_RELOCATION_FROM):
                group.append(block)
                continue
            target = block[len(_RELOCATION_TO):].strip()
            if _RELOCATION_CLOSED_RE.match(target):
                group.append(f"{target[0].lower()}{target[1:]}")
            elif target:
                group.append(f"{_RELOCATION_ARROW} {target}")
        else:
            others.append(block)
    flush()
    return pairs + others


def _relocation_arrows(blocks: list[str]) -> list[str]:
    """``Von: A`` and the ``Nach: B`` right after it become ``A → B``.

    Shorter on the display, and the line above already says it is a
    relocation (operator wish 2026-10-05).
    """
    joined: list[str] = []
    for block in blocks:
        if (
            joined
            and block.startswith(_RELOCATION_TO)
            and joined[-1].startswith(_RELOCATION_FROM)
        ):
            origin = without_full_stop(joined[-1][len(_RELOCATION_FROM):].strip())
            target = block[len(_RELOCATION_TO):].strip()
            if origin and _RELOCATION_CLOSED_RE.match(target):
                joined[-1] = f"{origin}: {target[0].lower()}{target[1:]}"
                continue
            if origin and target:
                joined[-1] = f"{origin} {_RELOCATION_ARROW} {target}"
                continue
        joined.append(block)
    return joined
