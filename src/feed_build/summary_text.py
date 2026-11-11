"""Summary sentences: clean-up, expected end, the 180-character cut, the description.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import html
import re
import string
from datetime import datetime, time, timedelta, UTC

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..providers.baustellen import REFERRAL_BOILERPLATE_RE
from ..utils.text import BLOCK_END_MARK, truncate_html

from .base import _VIENNA_TZ, _parse_datetime, _to_utc
from .relocation import _BLOCK_TERMINAL_PUNCT, _WORD_RE, _relocation_first
from .display_titles import _CATEGORY_PREFIX_WORDS, _TITLE_BODY_RE
from .wl_measures import _WL_DESC_LINE_TOKEN, _WL_MEASURE_DAY

__all__ = [
    "_strip_trailing_directional_marker", "_reason_only_summary", "_DOUBLED_FULL_STOP_RE",
    "_SPACE_BEFORE_PUNCT_RE", "_SPACE_BEFORE_COMMA_RE", "_EMPTY_PLACE_RE", "_drop_passed_expected_end",
    "_expected_end_sentence", "_close_blocks", "_WL_REFERRAL_BOILERPLATE_RE", "_truncate_summary_180",
    "_compose_description", "_title_body",
]


# WL Störung items must carry a real line prefix (``U6:``, ``41E:``,
# ``9/40/41/42:``, ``D:``); without it the user can't tell which line
# is affected and the meldung is useless. Real cache items
# ``Verkehrsunfall Betrieb ab Nordbrücke``, ``Fahrtbehinderung wegen
# Verkehrsunfall``, ``Einstieg: Brünner Straße 31-31A`` and
# ``Sperre Bahnsteig Richtung Siebenhirten`` all carry
# ``_identity='wl|störung|L=|D=...'`` (empty line set) and a title
# whose leading word *looks* like a line prefix to the eye but is
# actually a generic German noun (``Einstieg:``, ``Sperre`` without a
# colon).
#
# The validation is delegated to :func:`_extract_prefix_lines` so the
# same strict-line-token gate that protects the title-rebuild step
# (rejects ``Achtung:``, ``Hinweis:``, ``Einstieg:`` etc.) also drives
# the drop decision here. Items with NO recognisable line prefix —
# regardless of how their title is shaped — are dropped at cache-read
# time.

# WL descriptions sometimes end with a dangling ``>`` / ``<`` that the
# source uses as a "service onwards" arrow indicator. With a
# destination after it (``Betrieb ab Schwedenplatz > Praterstern``) it
# carries meaning; standalone at the end of the summary
# (``Betrieb ab Schwedenplatz >``) it reads like a broken HTML tag
# glyph or truncation artifact next to the ``[Am …]`` timeframe
# bracket. This strips the *trailing* form for every source; a WL arrow
# standing alone in front of a small word or a bracket goes as well
# (``_STANDALONE_ARROW_RE``).
# The characters a trailing WL direction marker is made of: the arrows
# and the whitespace between and around them. WL's display boards write a
# stop served in BOTH directions as ``< >`` — two arrows with a space —
# and the pattern ``\s*[<>]+\s*$`` used until 2026-09-19 took only the
# last arrow off; ``74A: Demonstration Betrieb ab Landstraße`` published
# ``Betrieb ab Landstraße <``. The replacement ``(?:\s*[<>]+)+`` nested two
# quantifiers over the same characters and backtracked exponentially on a
# long run of arrows (CodeQL, PR #1850). Stripping a character set from
# the end is what ``str.rstrip`` does, in one pass and without a regex.
_TRAILING_DIRECTIONAL_CHARS = string.whitespace + "<>"


# The cut behind a measure's label (``_truncate_summary_180``): what is left
# of the ``;``-joined measure has no colon yet.
_WL_BARE_MEASURE_LABEL_TAIL_RE: re.Pattern[str] = re.compile(
    rf";\s*(?:{_WL_MEASURE_DAY}[^;:]*?,\s+)?Linien?\s+{_WL_DESC_LINE_TOKEN}[^;:]*$"
)


def _strip_trailing_directional_marker(summary: str) -> str:
    """Drop a trailing WL ``>`` / ``<`` arrow with surrounding whitespace.

    Called before the title-body duplicate check in
    :func:`_format_item_content` so a summary that only differs from
    the title body by a dangling marker still matches and gets
    dropped as redundant.
    """
    if not summary:
        return summary
    return summary.rstrip(_TRAILING_DIRECTIONAL_CHARS)


def _reason_only_summary(category_word: str) -> str:
    """Rettet den Grund, wenn sonst ein leerer Rumpf übrig bliebe.

    ``_strip_summary_category_prefix`` wurde für die WL-*Hinweise* gebaut,
    deren HTML ein ``<h2>Gleisbauarbeiten</h2>`` vor den Fließtext setzt —
    dort ist das Wort eine durchgesickerte Überschrift und danach folgt
    weiterhin ein Satz. Die *Kurzmeldungen* der Anzeigetafeln haben
    dieselbe Form, meinen aber etwas anderes::

        T: 12A: Betrieb ab Johnstraße U
        D: Gleisbauarbeiten
           Betrieb ab Johnstraße U

    Hier ist „Gleisbauarbeiten“ der GRUND und das Einzige, was der Titel
    nicht ohnehin sagt. Nach dem Streichen bleibt exakt der Titel übrig,
    ``_summary_duplicates_title`` leert ihn folgerichtig — und das Item
    stand am 2026-09-17 als Schlagzeile über ``[16.09.2026 – 17.09.2026]``
    im Feed, ohne dem Leser zu verraten, warum.

    Greift ausschließlich dort, wo der Rumpf sonst leer bliebe: Solange
    nach dem Streichen noch ein Satz steht („Wegen Fortschreiten der
    …“), ist nichts zu retten und diese Funktion wird nie gefragt. Das
    Streichen selbst bleibt unangetastet.

    „Grund: X.“ ist dabei WLs eigene Formulierung — der Langtext derselben
    Störung endet auf „Grund: Gleisschaden im Bereich Märzstraße 62.“
    """
    if not category_word:
        return ""
    return f"Grund: {category_word.rstrip('.,;:')}."
_CONTINUING_WORDS: frozenset[str] = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "einer", "und", "oder", "sowie", "bzw", "in", "im", "an", "am", "auf",
    "aus", "bei", "beim", "mit", "nach", "von", "vom", "vor", "zu", "zum",
    "zur", "für", "über", "unter", "zwischen", "bis", "ab", "wegen", "durch",
})

# The dates of a notice are the time line under it. A ``Zeitraum:`` or
# ``Dauer:`` block repeats them in longer words, and as its own sentence it
# took the second-sentence slot from the measures ("77A: … Zeitraum: 22.
# August 2026, ca. 15:00 Uhr bis Betriebsschluss.") or, in a field-built
# text, the room the stop's new location needs.
_DATE_FIELD_RE = re.compile(r"(?:Zeitraum|Dauer):")
_DOUBLED_FULL_STOP_RE = re.compile(r"(?<!\.)\.\.(?!\.)")

# WL ticker texts built from fields whose value was empty: the stop symbol
# stripped from "Enkplatz U, Grillgasse" left "Enkplatz , Grillgasse", and a
# missing place left "Grund: Verkehrsunfall im Bereich ." or "Voraussichtliche
# Dauer: 12:15 Uhr .". A space in front of a comma, semicolon or full stop is
# never right in German; a place clause with no place says nothing.
_SPACE_BEFORE_PUNCT_RE = re.compile(r"(?<=\S)[ \t]+(?=[,;](?:\s|$)|\.(?:\s|$))")
_SPACE_BEFORE_COMMA_RE = re.compile(r"(?<=\S)[ \t]+(?=[,;](?:\s|$))")
_EMPTY_PLACE_RE = re.compile(r"\s+im\s+(?:Haltestellenbereich|Bereich)(?=\s*\.(?:\s|$))")

# WL's incident template names the expected end under a duration label:
# "Voraussichtliche Dauer: 14:10 Uhr." A clock time is no duration, and on
# the TV the sentence read as if the disruption lasted 14 hours (slides of
# 06.10.2026). Every value WL put behind the label since July is an end
# point (942 distinct texts in the WL cache: a clock time in 863, otherwise
# "Betriebsschluss", a day, "Ende August", "31.07.2026") except "Nicht
# absehbar" (48), so the sentence says
# what it means: "Voraussichtlich bis 14:10 Uhr." A value WL already opens
# with "bis" keeps a single one. Only a value that starts like an end point
# is rewritten: a clock time or date, "ca."/"circa"/"etwa"/"gegen" before
# one, "Betriebsschluss" in its three spellings, a weekday, "heute",
# "morgen", a month or "Anfang"/"Mitte"/"Ende". A real duration ("2
# Stunden", "etwa zwei Wochen") and any value of another shape ("Nicht
# absehbar") keep WL's label, which is right for them.
_EXPECTED_END_RE = re.compile(
    r"(?<!\w)(?P<v>[Vv])oraussichtliche\s+Dauer:\s*(?:[Bb]is\s+)?"
    r"(?=(?:(?:ca\.|circa|etwa|gegen)\s*)?"
    r"(?:\d|(?:Betriebs?schlu(?:ss|ß)|Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag"
    r"|[Hh]eute|[Mm]orgen|Anfang|Mitte|Ende|J[äa]nner|Januar|Februar|März|April|Mai|Juni|Juli"
    r"|August|September|Oktober|November|Dezember)\b))"
    r"(?![^:]{0,40}?\b(?:Minuten?|Stunden?|Tage|Tagen|Wochen?|Monate|Monaten|Jahre|Jahren)\b)"
)


# WL typed the clock time of that sentence wrong a few times since July:
# "15;40 Uhr", "12:15 Uht", "06:30Uhr". As the end of the sentence they read
# like a typo on the TV and stayed one in English ("Expected until
# 15;40:00").
_CLOCK_SEMICOLON_RE = re.compile(r"(?<![\d;:])(\d{1,2});(\d{2})(?=\s*Uh[rt]\b)")
_CLOCK_UHT_RE = re.compile(r"(?<=\d)\s*Uht\b")
_CLOCK_GLUED_UHR_RE = re.compile(r"(?<=\d\d:\d\d)Uhr\b")


# The expected end as a clock time, the sentence with its full stop:
# "Voraussichtlich bis 19:10 Uhr.", "… bis ca. 22 Uhr.", "… bis 10:00.". A
# date ("bis 19. September", "bis 31.07.2026", "bis 12.10.") is no clock
# time; a full stop between hour and minute counts only with "Uhr" ("10.30
# Uhr", twice since July), or "bis 12.10." would read as 12:10 that day.
_EXPECTED_CLOCK_END_RE = re.compile(
    r"(?<!\w)Voraussichtlich bis (?:(?:ca\.|circa|etwa|gegen)\s*)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2})(?:\s*Uhr)?|\.(?P<dm>\d{2})\s*Uhr|\s*Uhr)\.?(?=\s|$)\s*"
)
# How far before the item's newest time an end may lie and still mean that
# day: WL's estimate had already passed when the ticker came (N29 on
# 2026-07-30: "00:30 Uhr" in a message of 01:12). Further back, it is the
# next morning ("02:45 Uhr" in a message of 23:10).
_EXPECTED_END_BEFORE_MESSAGE = timedelta(hours=3)


def _drop_passed_expected_end(
    text: str, it: FeedItem, starts_at: datetime | None, now: datetime | None = None
) -> str:
    """*text* without "Voraussichtlich bis <Uhrzeit>." once that time has passed.

    Operator decision 2026-10-06 ("Weglassen"): in 40 of 184 feed states
    since July that carried WL's expected end as a clock time, the time had
    passed (U6 "bis 19:10" at 20:31). WL still reports the disruption, so its
    end is open; the cause and "[Seit hh:mm]" stay. The clock time belongs to
    the day of the item's newest time (``pubDate`` or ``starts_at``): WL
    writes estimates up to 17.5 hours ahead ("ca. 22 Uhr" for an event,
    entered 04:30), so a window around the present would drop a valid one.
    """
    match = _EXPECTED_CLOCK_END_RE.search(text)
    if match is None:
        return text
    hour, minute = int(match["h"]), int(match["m"] or match["dm"] or 0)
    if hour > 24 or minute > 59 or (hour == 24 and minute):
        return text
    times = [t for t in (starts_at, _parse_datetime(it.get("pubDate"))) if isinstance(t, datetime)]
    if not times:
        return text
    anchor = max(_to_utc(t) for t in times).astimezone(_VIENNA_TZ)
    end = datetime.combine(anchor.date(), time(hour % 24, minute), tzinfo=_VIENNA_TZ)
    if hour == 24 or end < anchor - _EXPECTED_END_BEFORE_MESSAGE:
        end = datetime.combine(
            anchor.date() + timedelta(days=1), time(hour % 24, minute), tzinfo=_VIENNA_TZ
        )
    if _to_utc(end) >= _to_utc(now or datetime.now(UTC)):
        return text
    return (text[: match.start()] + text[match.end():]).strip()


def _expected_end_sentence(text: str) -> str:
    """WL's "Voraussichtliche Dauer: <end>" as "Voraussichtlich bis <end>"."""
    text = _CLOCK_SEMICOLON_RE.sub(r"\1:\2", text)
    text = _CLOCK_UHT_RE.sub(" Uhr", _CLOCK_GLUED_UHR_RE.sub(" Uhr", text))
    return _EXPECTED_END_RE.sub(lambda m: f"{m['v']}oraussichtlich bis ", text)


def _join_continued_blocks(blocks: list[str]) -> list[str]:
    """Join a block to the next one where the sentence plainly goes on."""
    joined: list[str] = []
    for block in blocks:
        if joined:
            previous = joined[-1]
            last_words = _WORD_RE.findall(previous)
            continues = not previous.endswith(tuple(_BLOCK_TERMINAL_PUNCT)) and (
                block[:1].islower()
                or (last_words and last_words[-1].casefold() in _CONTINUING_WORDS)
            )
            # A label ("Maßnahmen:", "Zeitraum:") belongs to the block it
            # introduces — but not to a date field that follows it, which
            # :func:`_close_blocks` drops on its own.
            introduces = previous.endswith(":") and not _DATE_FIELD_RE.match(block)
            if continues or introduces:
                joined[-1] = f"{previous} {block}"
                continue
        joined.append(block)
    return joined


def _close_blocks(text: str, raw_title: str) -> tuple[str, bool]:
    """Turn :data:`BLOCK_END_MARK` into sentence ends.

    The flag says whether the text opens with a heading or a field — a
    block without punctuation of its own — and goes on after it. Such a
    text says little in its first two sentences ("Pantucekgasse - Früherer
    Betriebsschluss.", "Haltestelle: Stammersdorf."), so it is cut here
    instead of by the two-sentence rule: every leading heading or field,
    then the first block that is a sentence. The 180-character cut still
    applies after that.
    """
    if BLOCK_END_MARK not in text:
        return text, False
    title_words = {w.casefold() for w in _WORD_RE.findall(raw_title or "")}
    found = _join_continued_blocks([
        block
        for block in (raw.strip(" •") for raw in text.split(BLOCK_END_MARK))
        if _WORD_RE.search(block)
    ])
    # Only a heading with text behind it is dropped: a lone block is the
    # whole description, and the duplicate checks downstream decide on it.
    if len(found) > 1 and {w.casefold() for w in _WORD_RE.findall(found[0])} <= title_words:
        found = found[1:]
    if (
        len(found) > 1
        and len(_WORD_RE.findall(found[0])) == 1
        and found[0].casefold() not in _CATEGORY_PREFIX_WORDS
    ):
        found = found[1:]
    if len(found) > 1:
        kept = [block for block in found if not _DATE_FIELD_RE.match(block)]
        # A label that stood before a date field now meets its own text.
        found = _join_continued_blocks(kept) if kept else found
    found = _relocation_first(found, title_words, _truncate_summary_180)
    closed: list[tuple[str, bool]] = []
    for block in found:
        fragment = len(_WORD_RE.findall(block)) > 1 and not block.endswith(
            tuple(_BLOCK_TERMINAL_PUNCT)
        )
        closed.append((f"{block}." if fragment else block, fragment))
    if len(closed) < 2 or not closed[0][1]:
        return " ".join(block for block, _ in closed), False
    shown: list[str] = []
    for block, fragment in closed:
        shown.append(block)
        if not fragment:
            break
    return " ".join(shown), True


# Truncation tail-cleanup helpers — extracted from
# :func:`_format_item_content` for the same C901 budget reason as
# :func:`_strip_summary_category_prefix` above. Both blocks were tight
# self-contained sub-blocks; pulling them out preserves behaviour.
_TRUNCATION_PUNCT_STRIP = " ,;:-)/"
_TRUNCATION_UNIT_TOKENS: frozenset[str] = frozenset(
    {"Uhr", "min", "sec", "h", "km", "kg", "m", "cm", "s", "ms"}
)
# A word that only leads into what follows — an article, a preposition, a
# conjunction, "Richtung" or "Linie" without its name — says nothing once the
# cut took what it leads into. On the TV on 06.10.2026 two stop relocations
# ended "… und N62 in Richtung …" and "… bzw. N66 in Richtung …": the reader
# is told a direction and not shown it. 95 of 308 cut descriptions since
# July ended on such a word ("Rettungseinsatz im …", "Maßnahmen: Linie …",
# "Umleitung ab …"). The cut now ends on the last word that carries meaning.
_DANGLING_TAIL_WORDS: frozenset[str] = _CONTINUING_WORDS | {"richtung", "linie", "linien"}


def _should_drop_trailing_tail(tail: str) -> bool:
    """Decide whether the trailing token after a truncation rsplit is noise.

    Drops short German abbreviations, all-uppercase line markers, bare
    numbers, and standalone unit tokens; preserves real content words.
    """
    tail_stripped = tail.rstrip(".")
    ends_with_period = tail.endswith(".")
    if not tail_stripped:
        return True
    if len(tail) > 5:
        return False
    if ends_with_period and tail_stripped.isalpha():
        return True
    if ends_with_period and tail_stripped.isdigit():
        return True
    if tail_stripped.isdigit():
        return True
    if tail_stripped.isalpha() and tail_stripped.isupper():
        return True
    return tail in _TRUNCATION_UNIT_TOKENS or tail_stripped in _TRUNCATION_UNIT_TOKENS


def _trim_truncation_tail(truncated: str) -> str:
    """Iteratively drop noise tokens at the end of a hard-truncated summary."""
    dangling_dropped = False
    for _ in range(8):
        # A label whose value the cut swallowed ("… Grund:", "… Ersatz:",
        # "… Zeitraum:") announces information that is no longer there —
        # the published summary read "… Grund …", which is strictly worse
        # than ending one word earlier (see
        # ``docs/archive/audits/audit-2026-09-06.md`` §4). Drop the label
        # itself, not just its colon, before the generic punctuation strip
        # below removes the ``:`` that identifies it.
        label_candidate = truncated.rstrip(" ")
        if label_candidate.endswith(":"):
            label_start = label_candidate.rfind(" ")
            if label_start > 0:
                truncated = label_candidate[:label_start]
                continue
        # Behind a dropped word a closing bracket is whole ("(Strecke Linien
        # 42 und 9); Linie").
        truncated = truncated.rstrip(
            _TRUNCATION_PUNCT_STRIP.replace(")", "") if dangling_dropped else _TRUNCATION_PUNCT_STRIP
        )
        last_space = truncated.rfind(" ")
        if last_space <= 0:
            break
        tail = truncated[last_space + 1:]
        if tail.casefold() in _DANGLING_TAIL_WORDS:
            truncated = truncated[:last_space]
            dangling_dropped = True
            continue
        # What stands in front of a dropped word is whole: a line list
        # ("auf die Linien U3, 5, 12, 46, 52 und"), a stop ("Donaumarina U
        # über die") or a sentence ("Ersatz: U1, U2, D, 2 und 71. Die").
        if dangling_dropped:
            break
        if _should_drop_trailing_tail(tail):
            truncated = truncated[:last_space]
        else:
            break
    return truncated


# The Stadt-Wien referral ("Nähere Informationen zu den betroffenen
# öffentlichen Verkehrsmittel sind der Auskunft der Wiener Linien … zu
# entnehmen") tells the reader to ask somebody else. It carries no
# information about the disruption, and at 130 characters it eats most of
# the 180-character summary budget — so the sentence that says what actually
# happens never fits.
#
# Published 2026-09-18, the whole of item 8 of ten::
#
#     Ruthnergasse Kreuzung Justgasse
#     Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel sind
#     der Auskunft der Wiener Linien GmbH & Co KG zu entnehmen. [20.09. – 16.10.]
#
# On a rotating info display that slot cost a real disruption its airtime and
# told the viewer nothing. Dropped before sentence splitting so the
# informative sentence becomes sentence one.
#
# The pattern lives in the provider module because two decisions hang on it:
# the relevance gate (``is_transit_relevant``) must not count the sentence as
# an ÖPNV mention, and the summary here must not spend its budget on it
# (audit 2026-09-19, F.1). The local name is kept for the tests.
_WL_REFERRAL_BOILERPLATE_RE: re.Pattern[str] = REFERRAL_BOILERPLATE_RE


# The cut stays at a word boundary, not at the end of the last complete
# sentence (operator decision 2026-11-05, card "Soll der Feed satzweise
# kürzen, wenn der letzte Satz nicht mehr passt?": "Wortweise bleibt").
# Measured over the 56 feed versions from 04.11. 16:00 to 05.11. 15:31
# Vienna time, three of 65 items were cut. A sentence-level cut (the started
# sentence dropped whole) would have read cleaner for one of them ("… →
# Liechtensteinstraße 39. Haltestellenverlegung der Linie 40A in beiden …"
# loses only a repetition of the title) and would have dropped information
# for the other two: "… Grund: Verkehrsunfall im Bereich Grinzinger Allee …"
# (38) keeps the place, and the EN text of "25: Verkehrsunfall" (181
# characters where the German has 176) keeps "trains stop at Donaufelder
# Straße …" where only "After a service obstruction, there are irregular
# intervals. …" would have remained. A started sentence behind "…" is the
# accepted reading on the TV (changelog/2026-11-05.md).
def _truncate_summary_180(summary: str) -> str:
    """Hard-limit ``summary`` to 180 characters with TV-friendly tail cleanup."""
    if len(summary) <= 180:
        return summary
    truncated = summary[:175].rsplit(" ", 1)[0]
    # A WL measure the cut left with its line label alone ("…; Linie N8")
    # says nothing; the ellipsis stands behind the measure before it.
    truncated = _WL_BARE_MEASURE_LABEL_TAIL_RE.sub("", truncated)
    truncated = _trim_truncation_tail(truncated)
    if truncated.count("(") > truncated.count(")"):
        last_open = truncated.rfind("(")
        if last_open >= 0:
            truncated = truncated[:last_open].rstrip(_TRUNCATION_PUNCT_STRIP)
    # A bracket that closes one opened in the text stays ("(Strecke Linien
    # 42 und 9) …").
    strip = _TRUNCATION_PUNCT_STRIP
    if truncated.endswith(")") and truncated.count("(") == truncated.count(")"):
        strip = strip.replace(")", "")
    return truncated.rstrip(strip) + " …"


def _compose_description(summary: str, time_line: str) -> tuple[str, str]:
    """Assemble (desc_text_truncated, desc_html) from summary + time_line."""
    desc_parts: list[str] = []
    if summary:
        desc_parts.append(summary)
    if time_line:
        desc_parts.append(time_line)
    # Security (stored HTML/JS injection on the public feed): ``summary`` and
    # ``time_line`` are PLAIN TEXT, but ``desc_html`` is emitted verbatim into
    # the ``<content:encoded>`` CDATA body, which every conformant RSS reader
    # renders as HTML. ``html_to_text`` decodes entity-escaped angle brackets
    # (``&lt;img onerror=…&gt;`` → ``<img onerror=…>``) via
    # ``HTMLParser(convert_charrefs=True)``, so a compromised/MITM'd upstream
    # could otherwise land an executable tag in subscribers' readers. Escape
    # each text part for the HTML context; only the builder's own structural
    # ``<br/>`` separators stay live. ``desc_text_truncated`` below stays PLAIN
    # display text here (so the WL directional ``>`` markers, line-prefix logic
    # and truncation tests keep operating on the unescaped form); contextual
    # output-encoding for it happens at its HTML-rendered ``<description>`` sink
    # in :func:`_emit_item`.
    desc_html = "<br/>".join(html.escape(part, quote=False) for part in desc_parts)
    desc_text = " ".join(desc_parts)
    if len(desc_text) > feed_config.DESCRIPTION_CHAR_LIMIT:
        desc_text_truncated = (
            desc_text[: feed_config.DESCRIPTION_CHAR_LIMIT].rstrip()
            + "... [TRUNCATED]"
        )
    else:
        desc_text_truncated = desc_text
    desc_html = truncate_html(
        desc_html,
        feed_config.DESCRIPTION_CHAR_LIMIT,
        ellipsis="... [TRUNCATED]",
    )
    return desc_text_truncated, desc_html


def _title_body(title_out: str) -> str:
    """Return ``title_out`` without its ``11A: ``-style line prefix."""
    match = _TITLE_BODY_RE.match(title_out)
    return match.group(1).strip() if match else title_out
