"""Cause and consequence in a title, and the small shared predicates on items.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..feed_types import FeedItem
from ..feed import config as feed_config

from .abbreviations import is_abbreviation_period
from .base import _WHITESPACE_RE, _sanitize_text
from .display_titles import _CATEGORY_PREFIX_WORDS, _TICKER_ABBREVIATIONS, _TITLE_BODY_RE, _TITLE_REASON_WORDS
from .summary_text import _strip_trailing_directional_marker, _title_body, _truncate_summary_180

__all__ = [
    "_drop_category_word", "_leading_category_word", "_separate_reason_word", "_HINDRANCE", "_reason_and_fragment",
    "_finish_reason_title", "_last_sentence_end", "_rendered_title", "_short_title_collisions", "_is_wl_ticker",
    "_as_sentence", "_then_sentence", "_is_wl_notice", "_is_baustelle",
]


def _drop_category_word(summary: str, word: str) -> str:
    """Strip ``word`` (and a following separator) from the start of summary."""
    leading = re.match(
        rf"^{re.escape(word)}\s*[:.,;–—-]?\s+", summary, re.IGNORECASE
    )
    if leading:
        return summary[leading.end():].strip()
    return summary


def _leading_category_word(summary: str) -> str:
    """Das führende WL-Kategoriewort einer Beschreibung, sonst ``""``."""
    words = summary.split() if summary else []
    if not words:
        return ""
    first = words[0]
    return first if first.casefold() in _CATEGORY_PREFIX_WORDS else ""


def _separate_reason_word(title_out: str) -> str:
    """``31: Demonstration Betrieb ab Wallensteinstraße`` → ``31: Demonstration – Betrieb ab …``.

    WL's display-board tickers put the reason in front of the consequence
    with nothing between them. Read from a distance that is two fragments
    laid end to end, and word for word in English it becomes
    ``Demonstration service from Wallensteinstraße`` — as if „Demonstration
    service" were a kind of service (C.5, audit 2026-09-17). Over 300
    published revisions 26 of 188 distinct titles had this shape, the
    longest 99 characters against a limit of 256.

    The joint gets an en dash when the title body opens with a word from
    ``_TITLE_REASON_WORDS`` (planned work plus incident causes such as
    ``Fremdunfall``) and the next word starts with a capital —
    a fragment, not prose (``Demonstration am 19.09.2026`` keeps its
    sentence). Applied to the finished German title, after the duplicate
    checks that compare summaries against the title body, so those see
    the text WL wrote and nothing downstream has to know about the dash
    (``_drop_category_word`` happens to tolerate one today — that is
    luck, not a contract). The English title is translated from the
    result and inherits the dash. Idempotent: an existing dash is not a
    capital. A ticker abbreviation in the reason slot
    (:data:`_TICKER_ABBREVIATIONS`) is spelled out first.
    """
    match = _TITLE_BODY_RE.match(title_out)
    body = match.group(1) if match else title_out
    prefix = title_out[: match.start(1)] if match else ""
    words = body.split(maxsplit=1)
    if words and words[0].casefold() in _TICKER_ABBREVIATIONS:
        body = _TICKER_ABBREVIATIONS[words[0].casefold()] + body[len(words[0]):]
        title_out = f"{prefix}{body}"
        words = body.split(maxsplit=1)
    if len(words) < 2 or words[0].casefold() not in _TITLE_REASON_WORDS:
        return title_out
    if not words[1][:1].isupper():
        return title_out
    return f"{prefix}{words[0]} – {words[1]}"


# The consequences a WL ticker announces after its cause, measured over the
# cache since June (87 split titles: "Betrieb ab" 44, "Züge halten" 17,
# "Umleitung …" 9, "Kein Betrieb" 5, "Betrieb nur" 4, "Busse halten" 2, …).
# The cause in front: one to three words, capitalised, no digit — "Schadhafter
# Zug", "Signalstörung", "PKW im Gleis", but not "Linien 25 und 26" or
# "3A Netzänderung", where the digits show it is not a cause.
_CONSEQUENCE_START_RE = re.compile(
    r"^(?P<cause>[A-ZÄÖÜ][^\d:–]*?)\s+(?P<fragment>(?:Kein Betrieb|Betrieb (?:ab|nur|über|zwischen|bis)"
    r"|Züge halten|Busse halten|Umleitung)\b.*)$"
)
_CONSEQUENCE_CAUSE_MAX_WORDS = 3

# WL's tickers name the kind of hindrance before the cause: "Fahrtbehinderung
# Verkehrsunfall", "Fahrtbehinderung wegen Polizeieinsatz" (106 titles since
# June). Operator decision 2026-09-25 (audit A.13): the cause goes into the
# title, "Fahrtbehinderung" into the description.
_HINDRANCE = "Fahrtbehinderung"
_HINDRANCE_RE = re.compile(r"^Fahrtbehinderung\s+(?:–\s+)?(?:wegen\s+)?(?P<rest>[A-ZÄÖÜ].*)$")


def _reason_and_fragment(title_out: str) -> tuple[str, str] | None:
    """``14A: Rettungseinsatz Betrieb ab …`` → (``14A: Rettungseinsatz``, ``Betrieb ab …``).

    Two ways to find the joint between cause and consequence:

    * the cause is a word of ``_TITLE_REASON_WORDS`` — the joint
      :func:`_separate_reason_word` puts its dash on (a title that already
      carries the dash is accepted too);
    * otherwise the consequence opens with a phrase WL's tickers use for it
      (:data:`_CONSEQUENCE_START_RE`), after a short cause the word list does
      not know: ``O: Schadhafter Zug Betrieb ab Quartier Belvedere``,
      ``5: Stromstörung Betrieb ab Rosensteingasse`` (13 such titles since
      June).

    ``None`` when neither applies.
    """
    dashed = _separate_reason_word(title_out)
    match = _TITLE_BODY_RE.match(dashed)
    body = match.group(1) if match else dashed
    prefix = dashed[: match.start(1)] if match else ""
    hindrance = _HINDRANCE_RE.match(body)
    if hindrance is not None:
        # The cause follows the hindrance, possibly with a consequence of its
        # own ("Fahrtbehinderung Verkehrsunfall Betrieb ab …").
        cause_title = f"{prefix}{hindrance.group('rest').strip()}"
        return _reason_and_fragment(cause_title) or (cause_title, _HINDRANCE)
    reason, dash, fragment = body.partition(" – ")
    if dash and reason.casefold() in _TITLE_REASON_WORDS and fragment.strip():
        return f"{prefix}{reason}", fragment.strip()
    consequence = _CONSEQUENCE_START_RE.match(body)
    if consequence is None:
        return None
    cause = consequence.group("cause").strip()
    if len(cause.split()) > _CONSEQUENCE_CAUSE_MAX_WORDS:
        return None
    return f"{prefix}{cause}", consequence.group("fragment").strip()


def _finish_reason_title(
    title_out: str, summary: str, uncut_summary: str, *, split: bool
) -> tuple[str, str]:
    """Final title and summary of a WL ticker ``Linie: Ursache Folge``.

    With ``split`` (a disruption, and no other visible item would end up
    with the same short title — see :func:`_short_title_collisions`) the
    title keeps only the line and the cause, and the consequence opens
    the summary::

        T: 14A: Rettungseinsatz
        D: Betrieb ab Laxenburger Straße / Gudrunstraße [Am 25.09.2026]

    Operator request 2026-09-25: until then the title carried both halves
    (``14A: Rettungseinsatz – Betrieb ab …``) and the description only the
    date, because WL's ticker description repeats the title word for word
    and the duplicate check emptied it. A read-out-from-afar title is the
    cause; what the rider has to do belongs underneath. When the summary
    already says the consequence it is left as it is; otherwise the
    consequence goes in front of it and the 180-character budget applies
    to both together.

    Without ``split`` (a notice, whose second half is a place —
    ``D: Gleisbauarbeiten – Althanstraße`` — or a colliding short title)
    the title gets the dash of :func:`_separate_reason_word` as before.
    """
    parts = _reason_and_fragment(title_out) if split else None
    if parts is None:
        return _separate_reason_word(title_out), summary
    short_title, fragment = parts
    # WL's ticker description is often cause and consequence once more
    # ("Betriebsstörung\nKein Betrieb" under "13A: Betriebsstörung Kein
    # Betrieb ab 10 Uhr"). With the cause now in the title, what is left
    # of such a summary says nothing the consequence does not.
    rest = _drop_category_word(summary, _title_body(short_title))
    if not rest or rest.casefold() in fragment.casefold():
        return short_title, _truncate_summary_180(_as_sentence(fragment))
    # "Fahrtbehinderung" says less than any description WL writes ("Nach
    # einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.");
    # it only fills an empty one.
    if fragment == _HINDRANCE or fragment.casefold() in uncut_summary.casefold():
        return short_title, summary
    return short_title, _lead_with_fragment(fragment, uncut_summary)


def _lead_with_fragment(fragment: str, uncut_summary: str) -> str:
    """``fragment`` in front of the summary, cut behind a whole sentence.

    The consequence in front costs the summary room: "Busse halten bei der
    Linie 14A." pushed the 13A summary past 180 characters, and a plain cut
    ended it on "… 48A aus. Voraussichtliche …". Over budget, the text ends
    behind the last sentence of the summary that fits
    (:func:`_last_sentence_end`). When not even its first sentence fits, the
    text is cut mid-sentence like every other summary: the consequence alone
    dropped all of WL's text, and five such descriptions were published
    between 29.09. and 06.11.2026 ("13A: Feuerwehreinsatz" on 05.11. read
    only "Betrieb ab Neubaugasse, Burggasse.").
    """
    lead = _as_sentence(fragment)
    text = f"{lead} {uncut_summary}"
    if len(text) > 180:
        end = _last_sentence_end(text, 180, start=len(fragment))
        if end is not None and end > len(lead):
            text = text[:end]
    return _strip_trailing_directional_marker(_truncate_summary_180(text))


# A period between a word and a capitalised word, as candidates for a
# sentence end in :func:`_last_sentence_end`.
_LOOSE_SENTENCE_END_RE = re.compile(r"(?<=\w)\.(?=\s+[A-ZÄÖÜ])")

def _last_sentence_end(text: str, limit: int, *, start: int = 0) -> int | None:
    """End (after the period) of the last sentence in ``text[start:limit]``.

    Looser than :data:`_SENTENCE_SPLIT_RE`: a period before a capital ends
    a sentence unless :func:`is_abbreviation_period` says it closes an
    abbreviation or an ordinal ("Bhf.", "Hbf.", "bzw.", "prov.", "b.",
    "Fahrleitungsgebr.", "17. Februar"); the N8 summary of 30.10.2026 would
    have ended "… in beiden Richtungen zwischen Bhf." because "Bhf.
    Meidling" did. Words of every length end one ("aus.", "Uhr.", "PKW.",
    the stop marker "U.", the line "D.", "Gleis 2."): until 2026-11-12 words
    of one or two letters and numbers never did, and the lead "Betrieb ab
    Matzleinsdorfer Platz." of line 6 (21.08.2026) stood over "… über
    Hauptbahnhof S U. Grund: Polizeieinsatz …" cut mid-sentence although
    the sentence before "Grund" fit. The capital after the period may lie
    behind ``limit``: "… 52 und 48A aus." of 13A (26.09.2026) ended at
    character 180 and must count.
    """
    best: int | None = None
    for match in _LOOSE_SENTENCE_END_RE.finditer(text, start):
        if match.end() > limit:
            break
        if not is_abbreviation_period(text, match.start()):
            best = match.end()
    return best


def _rendered_title(item: FeedItem) -> str:
    """``item``'s German title as :func:`_format_item_content` renders it,
    up to the reason/fragment step."""
    title = _sanitize_text(item.get("title") or "Mitteilung")
    if len(title) > feed_config.TITLE_CHAR_LIMIT:
        title = title[: feed_config.TITLE_CHAR_LIMIT].rstrip() + " …"
    return _WHITESPACE_RE.sub(" ", title).strip()


def _short_title_collisions(items: Sequence[FeedItem]) -> set[int]:
    """Indices in ``items`` that must keep the long ``Ursache – Folge`` title.

    Shortening is only safe while the short title is unique among the
    visible items. WL often sends several tickers for one incident::

        49: Gleisschaden
        49: Gleisschaden Betrieb ab Urban-Loritz-Platz
        49: Gleisschaden Betrieb ab Hütteldorfer Straße

    Shortened, three slots on the display would read ``49: Gleisschaden``
    (feed history since June: 18 of 314 sampled feeds had such a group).
    So per short title:

    * another visible item already carries it as its own title → every
      ticker that would shorten onto it keeps the dashed long form;
    * otherwise the highest-placed ticker shortens and the others keep the
      long form — ``18: Verkehrsunfall`` over "Betrieb ab
      Ernst-Happel-Stadion", then ``18: Fahrtbehinderung – Verkehrsunfall``
      (live 2026-09-25), rather than both long with the first one's
      consequence back in the title and its description empty.
    """
    keys: list[tuple[str, bool]] = []
    for item in items:
        title = _rendered_title(item)
        parts = _reason_and_fragment(title) if _is_wl_ticker(item) else None
        keys.append(
            (parts[0].casefold(), True) if parts
            else (_separate_reason_word(title).casefold(), False)
        )
    taken = {key for key, shortened in keys if not shortened}
    keep_long: set[int] = set()
    for index, (key, shortened) in enumerate(keys):
        if not shortened:
            continue
        if key in taken:
            keep_long.add(index)
        taken.add(key)
    return keep_long


def _is_wl_ticker(item: FeedItem) -> bool:
    """A Wiener-Linien disruption, whose title is a display-board ticker.

    Notices (``Hinweis``) put a place behind the cause, not a consequence
    (``D: Gleisbauarbeiten Althanstraße``). ÖBB and the Stammstrecke
    monitor file under ``Störung`` too, but their titles are routes. An
    entry listing several incidents of one line (:func:`_combined_incidents`)
    is no ticker either: its title is a list of causes, not cause and
    consequence.
    """
    return (
        str(item.get("source") or "").strip().casefold() == "wiener linien"
        and str(item.get("category") or "").strip().casefold() == "störung"
        and not item.get("_wl_incidents")
    )


def _as_sentence(text: str) -> str:
    """*text* with a full stop, unless it ends in one of its own ("… Linie 46!").

    Also closes a ticker's consequence in both branches of
    :func:`_finish_reason_title`; only the one with more text behind it
    used to, so the feed showed "Betrieb ab Schwedenplatz U." under one
    ticker and "Busse halten bei Haltestelle N71" under the next.
    """
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


def _then_sentence(text: str, sentence: str) -> str:
    """*text*, closed as a sentence, then *sentence*: every WL join behind a text.

    A WL long message ends with a full stop, a display ticker does not:
    "Fahrtbehinderung wegen Rettungseinsatz" (26 on 2026-11-05 at 15:56
    Vienna time) took the day's display text "Züge halten in Tokiostraße"
    behind it with a space only, and the feed read "Fahrtbehinderung wegen
    Rettungseinsatz Züge halten in Tokiostraße." as one sentence. Every
    place that adds a message behind a WL text (``_with_unowned``,
    ``_with_consequences``, ``_with_line_tickers``) closes the text first,
    whatever its source wrote (operator decision 2026-11-06).
    """
    return f"{_as_sentence(text.rstrip())} {sentence}"


def _is_wl_notice(item: FeedItem) -> bool:
    return (
        str(item.get("source") or "").strip().casefold() == "wiener linien"
        and str(item.get("category") or "").strip().casefold() == "hinweis"
    )


def _is_baustelle(item: FeedItem) -> bool:
    return str(item.get("source") or "").strip().casefold().startswith("stadt wien")
