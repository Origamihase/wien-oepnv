"""The title and description of an item (``_format_item_content``) and their helpers.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..utils.text import html_to_text, repair_glued_words, repair_saint_abbreviation
from ..utils.wl_stop_markers import without_stop_markers

from .base import (
    _OEBB_SOURCE, _SENTENCE_SPLIT_RE, _VIENNA_TZ, _WHITESPACE_CLEANUP_RE, _WHITESPACE_RE, _sanitize_text,
)
from .placeholders import _norm_metadata
from .display_titles import (
    _CATEGORY_PREFIX_WORDS, _DATE_RANGE_PREFIX_RE, _DATE_SINGLE_PREFIX_RE, _TITLE_BODY_RE, _display_title_parts,
    _ordnungsnummer_as_nr,
)
from .oebb_summary import (
    _oebb_long_date_lists_in_words, _oebb_section_labels_cased, _oebb_upcoming_dates,
    _oebb_what_does_not_run_first,
)
from .oebb_lines_short import _oebb_every_line_in_short
from .wl_measures import _every_line_in_short, _prefer_measures, _wl_measure_labels_with_colon
from .time_line import _first_published, _incident_since, format_local_times
from .summary_text import (
    _DOUBLED_FULL_STOP_RE, _EMPTY_PLACE_RE, _SPACE_BEFORE_COMMA_RE, _SPACE_BEFORE_PUNCT_RE,
    _WL_REFERRAL_BOILERPLATE_RE, _close_blocks, _compose_description, _drop_passed_expected_end,
    _expected_end_sentence, _reason_only_summary, _strip_trailing_directional_marker, _title_body,
    _truncate_summary_180,
)
from .title_words import _drop_category_word, _finish_reason_title, _is_wl_ticker, _leading_category_word
from .item_meta import FormattedContent, _cdata_content, _shown_end, _source_item_link
from .oebb_lead import _OEBB_COURTESY_RE, _oebb_title_route_first, _with_second_sentence
from .translation_cache import _apply_lang_overlay

__all__ = [
    "_strip_summary_category_prefix", "_summary_is_title_without_reason", "_format_item_content",
    "_escape_description_markup",
]


# The same arrows standing ALONE inside a WL text, in front of a small word
# or a bracket. There they point at nothing; every such text since July 2026
# reads::
#
#     Betrieb ab Hütteldorfer Straße > mit Linie 46
#     Kein Betrieb zwischen Landstraße <> und Schlachthausgasse >
#     Umleitung bis Gersthof < über Linien 42 und 9!
#     Betrieb ab Gersthof < (Umleitung)
#
# ``_tidy_title_wl`` drops them from the title, the text kept them: on the TV
# they read as broken markup, and the text no longer matched its own title,
# so the reader got the sentence twice (21.10.2026, "42/9: Falschparker":
# "Umleitung bis Gersthof über Linien 42 und 9! Umleitung bis Gersthof <
# über Linien 42 und 9!"). Only WL texts, and only a run with whitespace in
# front and a lower-case word or a bracket behind it. An arrow before a
# name stays ("Betrieb ab Schwedenplatz > Praterstern": it says where to),
# so does a glyph glued to a word ("für <80", a literal "<b>" that
# ``tests/test_description_html_injection.py`` carries as text); the
# escaping of ``<description>`` is untouched.
_STANDALONE_ARROW_RE = re.compile(r"(?<!\S)[<>]+(?=\s+[a-zäöüß(])")

# The same glyph also turns up GLUED to the token after it, and there it
# makes the two halves of ONE item disagree: ``_tidy_title_wl`` ends on
# ``re.sub(r"[<>«»‹›]+", "", t)``, the description never ran through that.
# Live in the cache on 2026-09-19::
#
#     T: 1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80
#     D: Bhf. Hütteldorf ÖBB-Ersatzbus für <80
#
# This pattern is used for the COMPARISON in
# :func:`_summary_duplicates_title` only — it never rewrites published
# text. The first attempt did strip the glyph from the summary itself and
# broke five injection tests: ``&lt;b&gt;x&lt;/b&gt;`` decodes to the
# literal text ``<b>x</b>``, which the pipeline deliberately carries as
# plain text, and a rule that removes ``<`` before a word ate it.
# Reconciling the two halves is what the defect actually calls for, and a
# comparison cannot mangle anything.
_DIRECTIONAL_MARKER_RE = re.compile(r"[<>]+")

# When two items merge, ``src/feed/merge.py`` joins the two bodies with
# ``f"{ex_name} & {name}"`` for the TITLE, while the merged description
# carries the same two clauses separated by a plain space. The reader
# then gets the identical words twice, and the verbatim comparison below
# misses it because one side has an ``&`` the other has not::
#
#     T: 25: Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse & Ersatzbus ab …
#     D: Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse Ersatzbus ab …
#
# Counted over the published German feed: 8 of 232 unique items carry a
# ``&`` in title or body, 6 of them are this restatement. The other two
# are ordinary prose — ``Die Zufahrt zur Sport & Fun Halle Donaustadt ist
# möglich.`` — and stay untouched, because the rule demands the rest of
# the sentence match exactly once the joiner is levelled on BOTH sides.
_MERGE_JOINER_RE = re.compile(r"\s+&\s+")


def _strip_summary_category_prefix(summary: str, raw_title: str) -> str:
    """Remove a leading category H2 word that duplicates the title body
    or signals an HTML-heading leak.

    Real WL Hinweis items render with a ``<h2>Gleisbauarbeiten</h2>
    <p>Wegen …</p>`` HTML pair. After HTML-to-text conversion the
    heading word lands at the start of the body, producing redundant
    openings. Three patterns are recognised:

      1. ``T: "9/40/41/42: Gleisbauarbeiten"`` /
         ``D: "Gleisbauarbeiten Wegen ..."`` — first words match.
      2. ``T: "62A: Busse halten ..."`` /
         ``D: "Bauarbeiten Busse halten ..."`` — category prepended to
         an otherwise title-equivalent description.
      3. ``T: "27A/28A/29A: Fronleichnamsumzug"`` /
         ``D: "Veranstaltung Wegen Abhaltung des …"`` — the leading
         word is a known WL category AND the next word is ``Wegen``,
         so the body restates the cause and the heading word is pure
         noise. This catches the audit-round-7 cases where the title
         body is unrelated to the category word (Fronleichnamsumzug,
         Filmaufnahmen, Dornbacher Straße, …).
    """
    if not summary:
        return summary

    words = summary.split()
    if not words:
        return summary
    first_summary_word = words[0]
    summary_cf = first_summary_word.casefold()
    if summary_cf not in _CATEGORY_PREFIX_WORDS:
        return summary

    # Branch 3 — heading-leak via ``<Category> Wegen <body>``. Does not
    # depend on the title shape because real German prose never opens
    # a sentence with the bare category word followed by ``Wegen``;
    # the only producer of that pattern is the WL HTML heading leak.
    if len(words) >= 2 and words[1].casefold() == "wegen":
        return _drop_category_word(summary, first_summary_word)

    # Branches 1 & 2 — title-body comparison.
    title_match = _TITLE_BODY_RE.match(raw_title or "")
    title_body = title_match.group(1).strip() if title_match else (raw_title or "").strip()
    if not title_body:
        return summary

    first_title_word = title_body.split()[0]
    title_cf = first_title_word.casefold()
    if summary_cf == title_cf:
        return _drop_category_word(summary, first_summary_word)

    if len(words) >= 2 and words[1].casefold() == title_cf:
        return _drop_category_word(summary, first_summary_word)
    return summary


def _summary_is_title_without_reason(
    summary: str, title_out: str, category_word: str
) -> bool:
    """True when *summary* is the title body minus the title's reason word.

    The reason word is stripped from the SUMMARY upstream, never from the
    title — so with the word on both sides the verbatim check compares
    ``Kein Betrieb`` against ``Veranstaltung Kein Betrieb`` and fails,
    although the reader sees the same text twice. Until 2026-09-19 this
    was only caught when the summary itself had carried the word
    (*category_word* non-empty). WL's tickers also ship the bare text::

        T: 1A: Veranstaltung Kein Betrieb
        D: Kein Betrieb

    Ten of 247 published pairs from 17.–19.09. looked like this. Now the
    title's own reason word decides; a summary that carried a DIFFERENT
    reason word keeps it — that is information the title lacks.
    """
    if not summary:
        return False
    body = _title_body(title_out)
    reason = _leading_category_word(body)
    if not reason:
        return False
    if category_word and category_word.casefold() != reason.casefold():
        return False
    return summary.casefold() == _drop_category_word(body, reason).casefold()


def _summary_duplicates_title(summary: str, title_out: str) -> bool:
    """Return True when summary is just the title body restated verbatim."""
    if not (summary and title_out):
        return False
    title_body_compare = _title_body(title_out)
    if not title_body_compare:
        return False
    if summary.casefold() == title_body_compare.casefold():
        return True
    # The title lost only its closing mark: "18: Gasrohrgebr. Kein Halt"
    # over WL's "Gasrohrgebr. Kein Halt!!!" (04.05.2026) says it twice.
    if summary.rstrip(".!? ").casefold() == title_body_compare.rstrip(".!? ").casefold():
        return True
    # Marker-insensitive second attempt. The title has no ``<``/``>`` by
    # construction (see :data:`_DIRECTIONAL_MARKER_RE`), so a body that
    # differs from it by nothing else is the same sentence with one stray
    # glyph — and a headline that already says it all.
    #
    # Only the SUMMARY is normalised, and only for this comparison: the
    # published text keeps whatever upstream wrote. In particular nothing
    # here decides what ``<80`` was meant to be. ``S80`` is a real line at
    # Hütteldorf, so the glyph may be a mangled ``S``; turning it into a
    # line number would be inventing data, and this feed has published an
    # invented ``14AX`` before.
    # Whitespace is levelled too: a glyph between two words leaves two
    # spaces where the title has one.
    stripped = " ".join(_DIRECTIONAL_MARKER_RE.sub("", summary).split())
    if stripped != summary and stripped.casefold() == title_body_compare.casefold():
        return True
    # Joiner-insensitive third attempt — the merge ``&`` against the plain
    # space of the merged body (see :data:`_MERGE_JOINER_RE`). Levelled on
    # both sides so it does not matter which of the two halves won.
    joined_summary = _MERGE_JOINER_RE.sub(" ", summary)
    joined_title = _MERGE_JOINER_RE.sub(" ", title_body_compare)
    # Short-circuit, not a guard: with no joiner on either side both
    # strings come back unchanged and the comparison would only repeat
    # the verbatim one above. Removing it changes no behaviour — it
    # saves the work and names what this branch hangs on.
    if (joined_summary, joined_title) == (summary, title_body_compare):
        return False
    return joined_summary.casefold() == joined_title.casefold()


def _summary_as_one_line(summary: str, it: FeedItem) -> str:
    """Bullets, junction marks and stray arrows resolved into one running line."""
    if it.get("source") == "Wiener Linien":
        summary = _wl_measure_labels_with_colon(summary)
    # Bulletpoints auflösen, um einen fließenden Satz zu bilden
    summary = summary.replace(" • ", " ").replace("•", " ")
    # WL uses ``#`` as an internal street-junction marker — real cache
    # text reads ``ab Engerthstraße # Elderschplatz über …`` meaning
    # "starting from the Engerthstraße / Elderschplatz intersection".
    # The bare ``#`` glyph looks like a stray hashtag to a feed
    # subscriber, so swap it for ``/`` which mirrors WL's own
    # ``40/41`` line-separator convention and reads as a junction
    # marker without ambiguity. All 46 current cache occurrences
    # carry whitespace on both sides (``\\s+#\\s+``), so the
    # space-anchored swap can't accidentally touch a hashtag inside
    # quoted strings or a URL fragment.
    summary = summary.replace(" # ", " / ")
    if str(it.get("source") or "").strip().casefold() == "wiener linien":
        summary = _STANDALONE_ARROW_RE.sub(" ", summary)
    summary = _WHITESPACE_CLEANUP_RE.sub(" ", summary).strip()
    if it.get("source") == _OEBB_SOURCE:
        summary = _oebb_long_date_lists_in_words(summary)
    return summary


def _format_item_content(
    it: FeedItem,
    ident: str,
    starts_at: datetime | None,
    ends_at: datetime | None,
    *,
    lang: str = "de",
    state: dict[str, dict[str, Any]] | None = None,
    split_reason: bool = True,
    drop_section: bool = True,
) -> FormattedContent:
    raw_title, title_lead = _display_title_parts(it, drop_section=drop_section)
    raw_desc  = it.get("description") or ""
    # Before any comparison: "Ring , Volkstheater U" in the title and
    # "Ring, Volkstheater U" in the text are one sentence, not two.
    raw_title = _ordnungsnummer_as_nr(
        repair_saint_abbreviation(_SPACE_BEFORE_COMMA_RE.sub("", raw_title))
    )
    title_lead = repair_saint_abbreviation(_SPACE_BEFORE_COMMA_RE.sub("", title_lead))
    raw_desc = _SPACE_BEFORE_COMMA_RE.sub("", raw_desc)
    link = _source_item_link(it, ident)

    raw_guid = it.get("guid") or ident
    # Security: route the upstream-supplied guid through ``_sanitize_text``
    # (canonical ``_CONTROL_RE`` strip — C0/C1 controls + DEL + BiDi format
    # controls + zero-width chars + line separators + BOM) before it lands
    # in the published RSS XML's ``<guid>`` element. The pre-fix
    # ``str.strip()``-only path let upstream-controlled BiDi marks (CVE-
    # 2021-42574 Trojan-Source primitive), zero-width characters, and
    # line/paragraph separators flow into ``docs/feed.xml`` verbatim —
    # XML serialisation escapes ``<>&`` but does NOT strip Unicode BiDi
    # / zero-width / control chars. Mirrors the canonical sanitisation
    # already applied to the title (line ~1812: ``title_out =
    # _sanitize_text(title_out)``) and summary (line ~1782:
    # ``summary = _sanitize_text(summary).strip()``) — closes the LAST
    # per-item RSS sink that still routed through ``str.strip()``-only.
    guid = _sanitize_text(str(raw_guid)).strip() if raw_guid is not None else ident
    if not guid:
        guid = ident

    # Task: Strict 2-line Layout (Summary + Timeframe)
    # Line 1: Concise plain text summary (no HTML artifacts)
    summary, fragment_led = _close_blocks(
        html_to_text(raw_desc, collapse_newlines=True, mark_block_ends=True),
        raw_title,
    )
    if title_lead:
        summary = f"{title_lead}: {summary}" if summary.strip() else f"{title_lead}."
    summary = _sanitize_text(summary).strip()
    # Upstream prose sometimes arrives with the spaces between words
    # missing — the Stadt-Wien OGD Baustellen descriptions lost their
    # line breaks somewhere upstream, so the published feed carried
    # ``"… gehalten werden.Nähere Informationen …"`` and
    # ``"DerFußgängerverkehr …"`` verbatim. Repairing it here (before
    # sentence splitting and the 180-char truncation) also feeds the EN
    # translation pipeline readable German instead of run-together
    # tokens the NMT model has never seen.
    summary = _ordnungsnummer_as_nr(repair_glued_words(summary))
    # A sentence ends with one full stop, an abbreviation at the end of a
    # sentence included (Duden). WL typed two ("… wird die Linie 12A
    # umgeleitet.. Maßnahmen: …", three notices since July 2026); an
    # ellipsis (three or more) stays.
    summary = _DOUBLED_FULL_STOP_RE.sub(".", summary)
    summary = _SPACE_BEFORE_PUNCT_RE.sub("", _EMPTY_PLACE_RE.sub("", summary))
    summary = _drop_passed_expected_end(_expected_end_sentence(summary), it, starts_at)
    # Drop the "ask Wiener Linien" referral before the sentence split, or it
    # takes sentence one and the 180-char budget with it. Keep the referral
    # when it is all the description has: a useless sentence still beats an
    # item that shows a headline and nothing underneath it.
    without_referral = _WL_REFERRAL_BOILERPLATE_RE.sub("", summary).strip()
    if without_referral:
        summary = without_referral

    # ÖBB-spezifische Datumspräfixe (z.B. "17.09.2026 - 19.11.2026 • ") entfernen
    summary = _DATE_RANGE_PREFIX_RE.sub("", summary)
    summary = _DATE_SINGLE_PREFIX_RE.sub("", summary)

    # ÖBB's courtesy sentences take room the display needs — see
    # ``_oebb_sentence_without_place``.
    if it.get("source") == _OEBB_SOURCE:
        summary = _OEBB_COURTESY_RE.sub("", summary).strip() or summary
        summary = _oebb_section_labels_cased(summary)
        summary = _oebb_upcoming_dates(summary)

    summary = _summary_as_one_line(summary, it)

    # Doppelte Kategorie-Wortpräfixe entfernen (siehe Helper-Docstring).
    # Das Wort vorher merken: Bei WL-Kurzmeldungen ist es der Grund und
    # damit das Einzige, was der Titel nicht schon trägt — siehe
    # :func:`_reason_only_summary`.
    category_word = _leading_category_word(summary)
    summary = _strip_summary_category_prefix(summary, raw_title)
    # The text before the measures come first: a WL disruption on several
    # lines may show every line in short instead (see below).
    whole_summary = summary
    summary = _prefer_measures(summary)

    # Extrahiere maximal die ersten zwei Sätze.
    # Boundary regex: a real sentence end is a period after at least
    # FOUR letters (not a digit — German dates use ``17. Februar``,
    # not a 1-3 letter abbreviation either — German loves ``Gerasdorf
    # b. Wien``, ``Wien Hbf.``, ``Karlsplatz U.``, ``Bahnhst bzw.``
    # — all of these would otherwise be misread as sentence ends),
    # followed by whitespace and an uppercase German letter. This
    # avoids four false splits seen in the live cache:
    #   * ``17. Februar 2026`` (date; digit before period → keep)
    #   * ``Gerasdorf b. Wien`` (single-letter abbrev → keep)
    #   * ``Karlsplatz U. (Bereich)`` (single-letter U-Bahn marker → keep)
    #   * ``Bahnhst bzw. Gerasdorf`` (3-letter abbrev → keep)
    # while still cutting at genuine sentence boundaries like
    # ``Richtungen. Grund: …``, ``möglich. Reisende …`` and
    # ``ausgefallen. Wir bitten …``.
    sentences = [
        s.strip()
        for s in _SENTENCE_SPLIT_RE.split(summary)
        if s.strip()
    ]
    sentences = _oebb_title_route_first(sentences, raw_title, oebb=it.get("source") == _OEBB_SOURCE)
    if sentences and it.get("source") == _OEBB_SOURCE:
        end_shown = _shown_end(it, ends_at)
        sentences[0] = _oebb_what_does_not_run_first(
            sentences[0],
            title=raw_title,
            shown_end=end_shown.astimezone(_VIENNA_TZ).date() if end_shown else None,
        )
    if sentences and not fragment_led:
        short_summary = sentences[0]
        # Append the second sentence whenever the combined length still
        # fits below the 180-char hard limit applied below. Without this
        # WL items like ``Linie 62: … Karlsplatz U. Grund: Rettungseinsatz.``
        # silently lost the cause clause whenever the first sentence
        # exceeded the older 60-char threshold (the abbreviation period
        # after ``Karlsplatz U`` artificially terminates sentence 1, so
        # sentence 2 carrying the actual disruption reason was dropped).
        if len(sentences) > 1:
            short_summary = _with_second_sentence(
                short_summary, sentences[1], oebb=it.get("source") == _OEBB_SOURCE, raw_title=raw_title
            )
        summary = short_summary

    # Harte Begrenzung für den TV-Screen (max. 180 Zeichen).
    # Die Tail-Cleanup-Iteration (Abkürzungen, Linienkürzel, Einheits-
    # Tokens, unbalancierte Klammer) wurde in :func:`_truncate_summary_180`
    # extrahiert, damit die C901-Komplexitätsgrenze (Baseline 31) Platz
    # für den lang/state-Overlay behält.
    uncut_summary = summary
    summary = _truncate_summary_180(summary)
    short_lines = _every_line_in_short(it, whole_summary, summary, raw_title)
    if short_lines is None:
        short_lines = _oebb_every_line_in_short(it, whole_summary, summary, raw_title)
    if short_lines is not None:
        summary = uncut_summary = short_lines

    # Für XML robust aufbereiten (CDATA schützt Sonderzeichen)
    title_out = _sanitize_text(raw_title)
    if len(title_out) > feed_config.TITLE_CHAR_LIMIT:
        title_out = title_out[: feed_config.TITLE_CHAR_LIMIT].rstrip() + " …"

    # Minimal cleanup
    title_out = _WHITESPACE_RE.sub(" ", title_out).strip()

    # Line 2: Timeframe. WL's 11:11 expiry date is no end (see
    # :func:`_shown_end`); the line then reads "Seit …".
    shown_end = _shown_end(it, ends_at)
    time_line = format_local_times(
        starts_at if isinstance(starts_at, datetime) else None,
        shown_end,
        since=_incident_since(
            it, starts_at, _first_published(it, state.get(ident) if state is not None else None)
        ),
    )
    time_line = _sanitize_text(time_line)
    time_line = _WHITESPACE_CLEANUP_RE.sub(" ", time_line).strip()
    if time_line:
        time_line = f"[{time_line.strip('[]')}]"

    summary = _strip_trailing_directional_marker(summary)

    # Skip the summary entirely when it would just repeat the title
    # body verbatim. WL Störung items like ``41E: Ersatzbus 41E halten
    # bei Währinger Str 200`` produce a description that's identical
    # to the title body after the line-prefix is stripped — surfacing
    # both gives the user the same text twice. We compare casefold so
    # ``Linie 11A: Verspätung.`` and ``Verspätung`` are not flagged
    # as duplicates (different content).
    if _summary_duplicates_title(summary, title_out):
        summary = _reason_only_summary(category_word)
    elif _summary_is_title_without_reason(summary, title_out, category_word):
        # Dieselbe Redundanz, nur eine Stufe versetzt. Das Kategoriewort
        # wird oben aus der ZUSAMMENFASSUNG gestrichen, nicht aus dem
        # Titel — steht es in beiden, vergleicht der Test oben Äpfel mit
        # Birnen und schlägt fehl, obwohl der Leser dasselbe zweimal
        # sieht::
        #
        #     T: 2A: Veranstaltung Kein Betrieb
        #     D: Kein Betrieb [Am 18.09.2026]
        #
        # 12 von 222 veröffentlichten deutschen Items hatten diese Form,
        # jedes einzelne mit genau dem Kategoriewort als Unterschied.
        #
        # Hier bleibt der Rumpf LEER statt ``Grund: …`` — anders als im
        # Zweig darüber. ``_reason_only_summary`` rettet den Grund, weil
        # der Titel ihn dort nicht nennt; in diesem Zweig nennt er ihn
        # per Definition, sonst wäre das Streichen wirkungslos gewesen.
        # Ein ``Grund: Veranstaltung.`` unter einer Schlagzeile, die mit
        # „Veranstaltung" beginnt, tauscht nur eine Wiederholung gegen
        # eine kürzere.
        #
        # ``category_word and`` ist eine Kurzschluss-Bedingung, keine
        # Absicherung: ohne Kategoriewort gibt ``_drop_category_word``
        # den Rumpf unverändert zurück, und dieser Fall ist eine Zeile
        # weiter oben bereits abgehandelt. Sie spart den Regex-Aufbau im
        # Normalfall und sagt, woran dieser Zweig hängt — eine Mutation,
        # die sie streicht, ändert das Verhalten nicht.
        summary = ""

    # Last, after every comparison of the summary against the title body:
    # a ticker's consequence moves into the summary, or the joint between
    # cause and consequence gets its dash — see ``_finish_reason_title``.
    title_out, summary = _finish_reason_title(
        title_out, summary, uncut_summary, split=split_reason and _is_wl_ticker(it)
    )
    # A stop shows without WL's S-/U-Bahn marker: "N71: Rennweg S" reads
    # "N71: Rennweg" (operator decision 2026-11-04, ``without_stop_markers``),
    # and without the directory's "(WL)" (operator decision 2026-11-08).
    # Only here, on what the reader sees: every rule above (sentence split,
    # the 180-character cut, the title comparisons) still reads WL's names,
    # so the marker changes no sentence choice and no cut, only the name.
    title_out = without_stop_markers(title_out)
    summary = without_stop_markers(summary)
    title_cdata = _cdata_content(title_out)

    desc_text_truncated, desc_html = _compose_description(summary, time_line)

    # Prepare CDATA content (handle ]]> in content)
    desc_cdata = _cdata_content(desc_html)

    base = FormattedContent(
        guid, link, title_cdata, desc_text_truncated, desc_cdata,
        raw_desc, title_out, desc_html,
    )
    # Extract the per-item metadata that drives glossary layering in
    # the EN translation cascade. Both fields are normalised to
    # ``None`` for empty / non-string values so the downstream
    # ``_resolve_glossary`` cache key is stable across the
    # ``None``/``""``/``"  "`` edge cases.
    source_meta = _norm_metadata(it.get("source"))
    category_meta = _norm_metadata(it.get("category"))
    return _apply_lang_overlay(
        base, summary, time_line, ident, lang, state,
        source=source_meta, category=category_meta,
    )


# Angle brackets only — NOT ``&``. ``<description>`` is an XML text node that
# readers render as HTML, so the body needs contextual output-encoding for the
# HTML context (see the 2026-05-25 Sentinel round and
# ``tests/test_description_html_injection.py``). A tag can ONLY be formed by a
# RAW ``<``, so escaping ``<`` and ``>`` is the complete defence:
#
#   * ``<img onerror=…>``      -> ``&lt;img onerror=…&gt;`` -> XML-escaped to
#     ``&amp;lt;…`` -> the reader's single XML-decode yields ``&lt;…`` -> the
#     HTML renderer shows inert source text. Unchanged by this refinement.
#   * text that already carries an entity (``&lt;script&gt;`` as literal
#     characters, or a numeric ``&#60;``) stays entity-form through both
#     layers and renders as inert text too — an entity can never become a tag.
#
# ``&`` was collateral: ``html.escape`` turned ``GmbH & Co KG`` into
# ``&amp;`` and ElementTree escaped that again, so the published feed carried
# ``GmbH &amp;amp; Co KG``. HTML-rendering readers resolve that back to ``&``,
# but plain-text readers display the raw entity — the defect three audits
# flagged (2026-09-07, 2026-09-08, 2026-09-11 §2). Leaving ``&`` to
# ElementTree's single XML escape fixes the display in both reader classes and
# removes nothing from the injection defence.
_DESCRIPTION_MARKUP_ESCAPES = str.maketrans({"<": "&lt;", ">": "&gt;"})


def _escape_description_markup(text: str) -> str:
    """Neutralise markup characters for the HTML-rendered ``<description>``."""
    return text.translate(_DESCRIPTION_MARKUP_ESCAPES)
