"""The recursion around ``_translate_text_attempt``.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..utils.logging import sanitize_log_arg

from .base import log
from .placeholders import (
    _PLACEHOLDER_NONCE, _apply_domain_glossary, _has_short_placeholder_shape, _placeholder_nonce,
)
from .relocation import _RELOCATION_ARROW
from .oebb_summary import _OEBB_LABELLED_SENTENCE_RE, _oebb_section_label_en
from .translator import _get_translation_pipeline, _mask_service_compounds
from .masking import _mask_entities, _unmask_entities
from .pretranslate import (
    _HOUR_ONLY_CLOCK_RE, _LABEL_RECORD_RE, _REASON_LABEL_RE, _RECORD_BIS_VORAUSSICHTLICH_RE,
    _RECORD_EXPECTED_UNTIL_RE, _gloss_record_values, _join_record, _normalise_for_translation, _split_label_record,
)
from .en_repairs import _capitalise_sentence_start
from .model_pass import _is_non_translatable_content, _model_pass
from .en_templates import (
    _CAUSE_EN_RE, _OEBB_NO_TRAINS_RE, _OEBB_RESTRICTION_RE, _OEBB_SENTENCE_SPLIT_RE, _OebbCannotRun,
    _RELOCATION_CLOSED_LINE_RE, _WL_BECAUSE_RE, _WL_HEADED_BECAUSE_RE, _WL_IRREGULAR_INTERVALS_RE,
    _WL_MAIN_CLAUSE_SMALL_WORDS, _WL_SENTENCE_SPLIT_RE, _WL_SERVICE_CHANGES_RE, _WL_THEREFORE_EN_RE,
    _oebb_cannot_run_parts, _oebb_slots_are_names, _oebb_station_en, _render_oebb_follow_up,
    _render_wl_irregular_intervals,
)

__all__ = [
    "_render_label_record", "_render_record_table", "_oebb_cause_en", "_render_oebb_sentence",
    "_render_wl_sentence", "_render_wl_because_sentence", "_translate_text_attempt",
]


def _render_label_record(
    record: str, *, source: str | None, category: str | None
) -> str | None:
    """Render a label record to English: the table without, the reason with the model.

    Everything up to a closing ``Grund:`` goes through
    :func:`_render_record_table`. The reason is rendered the same way when the
    glossary and the masks leave no German word in it; otherwise it goes
    through the model as the short sentence it is, and a failure there fails
    the record (``None``), like any other failed translation.
    """
    reason = _REASON_LABEL_RE.search(record)
    if reason is None or _LABEL_RECORD_RE.search(record, reason.end()):
        return _render_record_table(record, source=source, category=category)
    table = record[: reason.start()].rstrip()
    reason_de = record[reason.start():].strip()
    table_en = (
        _render_record_table(table, source=source, category=category) if table else ""
    )
    glossed, glossary_mapping = _apply_domain_glossary(
        _normalise_for_translation(reason_de), source=source, category=category
    )
    masked, entity_mapping = _mask_entities(glossed)
    if _is_non_translatable_content(masked):
        reason_en: str | None = _unmask_entities(
            masked, {**glossary_mapping, **entity_mapping}
        )
    else:
        reason_en = _translate_text_attempt(
            reason_de, source=source, category=category
        )
    if reason_en is None:
        return None
    return _join_record(table_en, reason_en)


def _render_record_table(
    record: str, *, source: str | None, category: str | None
) -> str:
    """Render a label record to English WITHOUT the NMT model.

    The record is a machine-written table, not prose: every label resolves
    through the glossary and every value is a stop name, a street or a house
    number that :func:`_mask_entities` carries verbatim. Nothing in it needs
    a language model, and handing it to one is what broke it — Marian has no
    sentence to hold on to, degenerates on the placeholder run, and drops or
    doubles entities. Published 2026-09-18::

        DE: Von: 1. Haidequerstraße 2     Nach: 1. Haidequerstraße 510
        EN: From: 1. Haidequerstraße 510  Duration: From 1. Haidequerstraße 510

    The origin was dropped and the destination promoted into its place. The
    entity guard in :func:`_entities_dropped_by_translation` caught that and
    fell back to German, which is where the item has sat since — correct, and
    still not English. This is the half that makes it English: mask, resolve,
    done. Same two passes the model path uses, minus the model, so the
    rendering is deterministic and the addresses cannot move.
    """
    expected = _RECORD_EXPECTED_UNTIL_RE.match(record)
    if expected is not None:
        rendered = _render_record_table(
            f"bis {record[expected.end():]}", source=source, category=category
        )
        if rendered.startswith("Until "):
            return f"Expected until {rendered[len('Until '):]}"
        return rendered
    record = _RECORD_BIS_VORAUSSICHTLICH_RE.sub(r"\1 etwa", record)
    glossed, glossary_mapping = _apply_domain_glossary(
        _normalise_for_translation(record), source=source, category=category
    )
    labels = frozenset(
        placeholder
        for placeholder, english in glossary_mapping.items()
        if english.endswith(":")
    )
    glossed = _gloss_record_values(glossed, labels)
    masked, entity_mapping = _mask_entities(glossed)
    return _unmask_entities(masked, {**glossary_mapping, **entity_mapping})


def _oebb_cause_en(
    cause: str, ident: str, source: str | None, category: str | None
) -> str | None:
    """English for "Wegen <cause>", without the comma the sentence adds."""
    english = _translate_text_attempt(
        f"Wegen {cause}", ident, source=source, category=category, oebb_templates=False
    )
    if english is None:
        return None
    english = english.strip().rstrip(".,;:")
    # The model sometimes renders the phrase as the bare noun ("Wegen
    # Sanierung des Bahnsteigs" → "Renovation of the platform", WL U6 Neue
    # Donau, 05.10.2026), and the sentence then reads as if the cause were
    # its subject. The cause gets its "Due to" back; a capital stays where a
    # name begins ("Vienna City Marathon"), a common noun is written small.
    if not _CAUSE_EN_RE.match(english):
        words = english.split(" ", 2)
        if len(words) > 1 and words[1][:1].islower():
            english = english[:1].lower() + english[1:]
        english = f"Due to {english}"
    return _capitalise_sentence_start(english)


def _render_oebb_labelled_sentence(
    sentence: str, ident: str, source: str | None, category: str | None
) -> str | None:
    """Render a sentence headed by a known ÖBB section label; ``""`` otherwise.

    The label is written out (``_OEBB_SECTION_LABELS_EN``). The rest goes
    through its template, else the model gets the sentence as before, label
    included, and only the words in front of its colon are replaced: the
    model reads the rest in the same context, so only the label changes.
    """
    labelled = _OEBB_LABELLED_SENTENCE_RE.fullmatch(sentence)
    head = _oebb_section_label_en(labelled["label"]) if labelled is not None else None
    if labelled is None or head is None:
        return ""
    body = _render_oebb_sentence(labelled["body"], ident, source, category)
    if body == "":
        whole = _translate_text_attempt(
            sentence, ident, source=source, category=category, oebb_templates=False,
        )
        if whole is None:
            return None
        # The model's label is a few words; a colon further in belongs to the
        # body ("… Alternativverbindungen: U1 …").
        model_head, colon, body = whole.partition(":")
        if not colon or not body.strip() or len(model_head.split()) > 5:
            body = _translate_text_attempt(
                labelled["body"], ident, source=source, category=category,
                oebb_templates=False,
            )
    if body is None:
        return None
    return f"{head}: {_capitalise_sentence_start(body.strip())}"


def _render_oebb_sentence(
    sentence: str, ident: str, source: str | None, category: str | None
) -> str | None:
    """Render one templated ÖBB sentence; ``""`` when it has no template."""
    labelled = _render_oebb_labelled_sentence(sentence, ident, source, category)
    if labelled != "":
        return labelled
    sentence = _HOUR_ONLY_CLOCK_RE.sub(
        lambda m: f"{m.group(1)}:{m.group(2) or '00'}", sentence
    )
    match = _OEBB_RESTRICTION_RE.fullmatch(sentence)
    if match is not None and _oebb_slots_are_names(match, "a", "b", "at", "area"):
        cause = _oebb_cause_en(match["cause"], ident, source, category)
        if cause is None:
            return None
        past = match["tense"] == "waren"
        if match["none"]:
            core = "no trains could run" if past else "no trains can run"
        else:
            core = (
                "trains could only run to a limited extent"
                if past
                else "trains can only run to a limited extent"
            )
        if match["at"]:
            place = f" at {_oebb_station_en(match['at'])}"
        elif match["area"]:
            place = f" in the area of {_oebb_station_en(match['area'])}"
        elif match["a"]:
            place = (
                f" between {_oebb_station_en(match['a'])}"
                f" and {_oebb_station_en(match['b'])}"
            )
        else:
            place = ""
        if match["time"]:
            day = f"{match['date']}, " if match["date"] else ""
            expected = match["expected"] or match["expected_first"]
            when = (
                f" until {'approx. ' if expected else ''}{day}{match['time']}"
            )
        elif match["now"]:
            when = " at present"
        else:
            when = ""
        return f"{cause}, {core}{place}{when}."
    match = _OEBB_NO_TRAINS_RE.fullmatch(sentence)
    if match is not None and _oebb_slots_are_names(match, "a", "b"):
        cause = _oebb_cause_en(match["cause"], ident, source, category)
        if cause is None:
            return None
        first, last = (match["d1"], match["d2"]) if match["d1"] else (match["d3"], match["d4"])
        dates = f" from {first} to {last}" if first else ""
        between = (
            f" between {_oebb_station_en(match['a'])} and {_oebb_station_en(match['b'])}"
            if match["a"]
            else ""
        )
        return f"{cause}, no {match['trains']} trains can run{between}{dates}."
    parts = _oebb_cannot_run_parts(sentence)
    if parts is not None:
        return _render_oebb_cannot_run(parts, ident, source, category)
    return _render_oebb_follow_up(sentence)


def _render_oebb_cannot_run(
    parts: _OebbCannotRun, ident: str, source: str | None, category: str | None
) -> str | None:
    """English for ÖBB's "… kann … nicht fahren." from its parts (see below)."""
    cause = _oebb_cause_en(parts.cause, ident, source, category)
    if cause is None:
        return None
    if parts.rest is not None:
        return f"{cause}, {parts.head}{parts.rest}"
    # Routes or days outside the closed set: they alone go through the
    # model, behind the colon the German sentence has there.
    rest = _translate_text_attempt(
        parts.middle, ident, source=source, category=category, oebb_templates=False
    )
    if rest is None:
        return None
    rest = rest.strip()
    if rest.split(" ", 1)[0].casefold() in _WL_MAIN_CLAUSE_SMALL_WORDS:
        rest = rest[:1].lower() + rest[1:]
    return f"{cause}, {parts.head}: {rest}"


def _render_wl_sentence(
    sentence: str, ident: str, source: str | None, category: str | None
) -> str | None:
    """Render one WL sentence that has a fixed form; ``""`` for the ordinary path.

    A relocation's way ("Wienerbergstraße 27b-27c → Wienerbergstraße 27a.",
    see :func:`_relocation_first`) is the old ``Von:``/``Nach:`` pair and is
    rendered like it was, without the model (:func:`_render_record_table`).
    Through the model it came out glued and with a placeholder left over
    (05.10.2026 17:45: "Wienerbergstraße27b-27c →Wienerbergstraße27a. … 15A5X
    in the direction of Enkplatz U").
    """
    if f" {_RELOCATION_ARROW} " in sentence or _RELOCATION_CLOSED_LINE_RE.search(sentence):
        return _render_record_table(sentence, source=source, category=category)
    intervals = _WL_IRREGULAR_INTERVALS_RE.fullmatch(sentence)
    if intervals is not None:
        rendered = _render_wl_irregular_intervals(intervals, source)
        if rendered:
            return rendered
    return _render_wl_because_sentence(sentence, ident, source, category)


def _render_wl_service_changes(
    match: re.Match[str], ident: str, source: str | None, category: str | None
) -> str | None:
    """English for WL's "Wegen … kommt es zu (folgenden) Verkehrsmaßnahmen."."""
    cause = _oebb_cause_en(match["cause"], ident, source, category)
    if cause is None:
        return None
    lines = ""
    if match["lines"]:
        noun = "lines" if match["noun"] == "Linien" else "line"
        listed = re.sub(r"\s+(?:und|sowie)\s+", " and ", match["lines"].strip())
        lines = f" to {noun} {listed}" if match["following"] else f" on {noun} {listed}"
    if match["following"]:
        return f"{cause}, the following service changes apply{lines}."
    return f"{cause}, there are service changes{lines}."


def _render_wl_because_sentence(
    sentence: str, ident: str, source: str | None, category: str | None
) -> str | None:
    """Render one WL "Wegen …" sentence; ``""`` when it takes the ordinary path."""
    headed = _WL_HEADED_BECAUSE_RE.fullmatch(sentence)
    if headed is not None:
        # WL glues its category word in front ("Bahnsteigsanierung Wegen …",
        # "Ausgangssperre Wegen …"). Through the model whole, the cause got
        # lost (05.10.: "Refurbishing the platform The U6 line stops …"); the
        # word goes through on its own and the sentence as usual.
        body = _render_wl_because_sentence(headed["body"], ident, source, category)
        if not body:
            return body
        head = _translate_text_attempt(
            headed["head"], ident, source=source, category=category, oebb_templates=False
        )
        if head is None:
            return None
        return f"{_capitalise_sentence_start(head.strip())} {body}"
    service_changes = _WL_SERVICE_CHANGES_RE.fullmatch(sentence)
    if service_changes is not None:
        return _render_wl_service_changes(service_changes, ident, source, category)
    match = _WL_BECAUSE_RE.fullmatch(sentence)
    if match is None:
        return ""
    main = _translate_text_attempt(
        f"Deshalb {match['verb']} {match['rest']}", ident,
        source=source, category=category, oebb_templates=False,
    )
    if main is None:
        return None
    lead = _WL_THEREFORE_EN_RE.match(main.strip())
    if lead is None:
        return ""
    main = main.strip()[lead.end():]
    if not main:
        return ""
    first = main.split(" ", 1)[0]
    if first.casefold() in _WL_MAIN_CLAUSE_SMALL_WORDS:
        main = main[0].lower() + main[1:]
    cause = _oebb_cause_en(match["cause"], ident, source, category)
    if cause is None:
        return None
    return f"{cause}, {main}"


def _translate_oebb_templates(
    text: str,
    ident: str,
    source: str | None,
    category: str | None,
    render: Callable[[str, str, str | None, str | None], str | None] | None = None,
    split: re.Pattern[str] | None = None,
) -> str | None:
    """Translate a text whose sentences follow an operator's templates.

    *render* renders one sentence (:func:`_render_oebb_sentence` unless
    given, :func:`_render_wl_because_sentence` for WL). Returns the text
    unchanged (the caller's cue for the ordinary path) when no sentence has
    a template. Otherwise each templated sentence is rendered and every run
    of other sentences goes through the ordinary path; a failure anywhere
    fails the whole text (``None``).
    """
    render = render or _render_oebb_sentence
    sentences = (split or _OEBB_SENTENCE_SPLIT_RE).split(text.strip())
    parts: list[str] = []
    prose: list[str] = []
    templated = False

    def _flush() -> bool:
        if not prose:
            return True
        english = _translate_text_attempt(
            " ".join(prose), ident, source=source, category=category,
            oebb_templates=False,
        )
        prose.clear()
        if english is None:
            return False
        parts.append(english)
        return True

    for sentence in sentences:
        rendered = render(sentence, ident, source, category)
        if rendered is None:
            return None
        if not rendered:
            prose.append(sentence)
            continue
        templated = True
        if not _flush():
            return None
        parts.append(rendered)
    if not templated:
        return text
    if not _flush():
        return None
    return " ".join(parts)


def _translate_text_attempt(
    text: str,
    ident: str = "",
    *,
    source: str | None = None,
    category: str | None = None,
    oebb_templates: bool = True,
) -> str | None:
    """Translate ``text`` from German to English with entity preservation.

    Pipeline:

      1. :func:`_mask_entities` replaces known brands, station names
         and ÖPNV line identifiers with alphanumeric placeholders so
         the ML model cannot mistranslate proper nouns.
      2. The masked text goes through the Hugging Face translation
         pipeline. ``truncation=True`` caps long inputs at the model's
         max context window instead of letting Marian crash on > 512
         tokens.
      3. :func:`_unmask_entities` restores the original surface forms
         from the placeholder mapping.

    Returns ``None`` on ANY failure (pipeline unavailable, runtime
    error, malformed output, empty result) so callers can distinguish
    "translated" from "had to fall back to the German source". Use
    :func:`_translate_text` if you want a safe fallback that always
    returns a string.

    ``ident`` is included in warning logs (sanitised) so the GitHub
    Actions log shows exactly which feed identities failed to
    translate — critical for diagnosing partial-translation drift.

    ``source`` and ``category`` (when supplied — they are the
    matching ``FeedItem`` fields) drive the metadata-aware glossary
    layering in :func:`_apply_domain_glossary`: operator-specific
    vocabulary activates only when the item came from that operator.
    """
    if not text or not text.strip():
        return None
    pipe = _get_translation_pipeline()
    if pipe is None:
        return None
    if oebb_templates and source == "ÖBB":
        templated = _translate_oebb_templates(text, ident, source, category)
        if templated != text:
            return templated
    if oebb_templates and source == "Wiener Linien":
        templated = _translate_oebb_templates(
            text, ident, source, category,
            render=_render_wl_sentence, split=_WL_SENTENCE_SPLIT_RE,
        )
        if templated != text:
            return templated
    # A trailing ``Label: value`` record is a table, not prose. Split it off
    # and render it without the model (see ``_render_label_record``; only a
    # closing reason the glossary cannot render still needs it); only the
    # prose in front of it goes through Marian. Untouched for the 154 of 157
    # published descriptions that carry fewer than two labels.
    prose, label_record = _split_label_record(text)
    record_en = (
        _render_label_record(label_record, source=source, category=category)
        if label_record
        else ""
    )
    if record_en is None:
        return None
    if not prose:
        # Nothing but the record — there is no prose left to translate.
        return record_en
    text = prose
    # Compose two mask passes:
    #   1. Domain-glossary substitution — DE jargon → ``XGLO<n>X``
    #      placeholders that resolve to canonical English terms. Runs
    #      FIRST so the entity masker afterwards sees the placeholder
    #      tokens (which look like opaque proper nouns) and leaves
    #      them untouched.
    #   2. Verbatim entity masking — brands, stations, lines, streets,
    #      Unicode symbols → ``XENT<n>X`` placeholders that resolve
    #      to the original German surface form.
    # The two mappings are merged into a single dict for unmasking
    # (each pass uses a distinct placeholder format so indices cannot
    # collide).
    glossary_processed, glossary_mapping = _apply_domain_glossary(
        _normalise_for_translation(text), source=source, category=category,
    )
    glossary_processed = _mask_service_compounds(
        glossary_processed, glossary_mapping, source=source, category=category,
    )
    masked_text, entity_mapping = _mask_entities(glossary_processed)
    combined_mapping = {**glossary_mapping, **entity_mapping}
    # Entity-only fast path: when masking leaves nothing translatable (a title
    # that is just line/station placeholders + punctuation), skip the NMT model
    # entirely. Round-tripping such a string through Marian risks mangling the
    # densely-packed placeholders (EN-feed sentinel leak) for no translation
    # gain — unmasking the masked text reproduces the correct, language-neutral
    # surface forms directly.
    if _is_non_translatable_content(masked_text):
        return _join_record(
            _unmask_entities(masked_text, combined_mapping), record_en
        )
    unmasked, placeholder_failure = _model_pass(
        pipe,
        masked_text,
        combined_mapping,
        ident,
        _PLACEHOLDER_NONCE if _has_short_placeholder_shape(masked_text) else None,
        prose,
    )
    if placeholder_failure:
        # The model mangled or dropped a placeholder. One more pass under a
        # fresh nonce, a different SentencePiece split of every placeholder:
        # before the short form, a bad nonce hit several texts of a build
        # (2026-09-26 17:01, nonce ``c3ed7873665b7570``, three summaries).
        nonce = _placeholder_nonce()
        log.info(
            "Retrying translation for identity %s under a fresh placeholder nonce (%s).",
            sanitize_log_arg(ident or "<unknown>"),
            nonce,
        )
        unmasked, _placeholder_failure = _model_pass(
            pipe, masked_text, combined_mapping, ident, nonce, prose
        )
    if unmasked is None:
        return None
    return _join_record(unmasked, record_en)
