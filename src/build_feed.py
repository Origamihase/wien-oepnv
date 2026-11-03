from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import secrets
import sys
import xml.etree.ElementTree as ET  # nosec B405
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, UTC
from email.utils import format_datetime
from itertools import pairwise
from pathlib import Path
from time import perf_counter
from typing import Any, cast, NamedTuple
from urllib.parse import urlparse

from .feed_types import FeedItem
from .feed import config as feed_config
# ``as`` re-exports explicitly: tests read the tables from ``build_feed``.
from .feed.glossary import (
    _FIELD_LABEL_EN as _FIELD_LABEL_EN,
    _GLOSSARY_BASE as _GLOSSARY_BASE,
    _GLOSSARY_BY_CATEGORY as _GLOSSARY_BY_CATEGORY,
    _GLOSSARY_BY_SOURCE as _GLOSSARY_BY_SOURCE,
)
from .feed.merge import deduplicate_fuzzy, member_guids
from .feed.logging import configure_logging
from .feed.providers import load_provider_plugins, provider_statuses
from .feed.reporting import (
    DuplicateSummary, FeedHealthMetrics, RunReport, write_feed_health_report, write_feed_health_json,
)
from .utils.files import atomic_write
from .utils.locking import file_lock
from .utils.logging import sanitize_log_arg
from .providers.baustellen import names_site_street, shares_address, site_street
from .providers.wl_lines import line_order_key
from .providers.wl_plausibility import incident_begin
from .providers.wl_text import STOP_NOTICE_LEAD_RE
from .utils.text import html_to_text, repair_glued_words, repair_saint_abbreviation

# The parts below were moved verbatim to src/feed_build/ (700-line rule,
# Etappe 8). Every name src/, scripts/ or the tests read stays importable here;
# a test patches a name in the module whose code reads it.
from .feed_build.base import (
    ATOM_NS, GEORSS_NS, MEDIA_NS, log, validate_path, _VIENNA_TZ, _SENTENCE_SPLIT_RE, _to_utc, _fmt_rfc2822,
    _OEBB_SOURCE, _CONTROL_RE, _WHITESPACE_RE, _WHITESPACE_CLEANUP_RE, _sanitize_text, _ymd_or_none,
    _parse_datetime, _coerce_datetime_field, _normalize_item_datetimes,
)
from .feed_build.entities import _LINE_ENTITY_RE, _TRAM_LETTER_LINE_RE, _STREET_SUFFIX_RE, _DATE_ENTITY_RE
from .feed_build.placeholders import (
    _resolve_glossary, _norm_metadata, _placeholder_nonce, _PLACEHOLDER_NONCE, _ENTITY_PLACEHOLDER_FORMAT,
    _ENTITY_PLACEHOLDER_RE, _GLOSSARY_PLACEHOLDER_FORMAT, _UNMASK_PLACEHOLDER_RE, _RESIDUAL_PLACEHOLDER_RE,
    _normalise_placeholder_debris, _apply_domain_glossary, _has_short_placeholder_shape, _to_short_placeholders,
    _from_short_placeholders,
)
from .feed_build.display_titles import (
    _DISPLAY_TITLE_TARGET, _shorten_wl_sentence_title, _repair_baustellen_title, _compact_baustellen_section,
    _ordnungsnummer_as_nr, _mark_house_numbers, _compact_line_prefix, _display_title_parts, _display_title,
    _section_collisions, _split_reason_title, _CATEGORY_PREFIX_WORDS, _TITLE_REASON_WORDS, _TITLE_BODY_RE,
    _DATE_RANGE_PREFIX_RE, _DATE_SINGLE_PREFIX_RE,
)
from .feed_build.oebb_summary import (
    _oebb_upcoming_dates, _oebb_long_date_lists_in_words, _oebb_what_does_not_run_first,
    _oebb_section_labels_cased, _OEBB_LABELLED_SENTENCE_RE, _oebb_section_label_en,
)
from .feed_build.translator import (
    _TRANSLATION_STATE, _TRANSLATION_MODEL_REVISION, _service_compound_defect, _MarianTranslator,
    _get_translation_pipeline, _record_translation_status,
)
from .feed_build.masking import (
    _declined_station_names, _station_base_forms, _station_entity_names, _mask_entities, _unmask_entities,
)
from .feed_build.pretranslate import (
    _normalise_for_translation, _LABEL_RECORD_RE, _MIN_LABELS_FOR_RECORD, _split_label_record, _join_record,
)
from .feed_build.en_repairs import (
    _agreeing_article, _ARTICLE_BEFORE_STREET_RE, _street_names_nominative, _drop_article_before_street,
    _drop_article_before_line, _fix_glossary_articles, _entities_dropped_by_translation, _redirected_towards,
    _cause_due_to, _english_weekdays_capitalised, _capitalise_title_body, _capitalise_sentence_start,
    _capitalise_glossary_after_stop, _drop_unpaired_quote,
)
from .feed_build.model_pass import _is_non_translatable_content, _respace_glued_placeholders, _leftover_placeholder
from .feed_build.en_templates import (
    _OEBB_SLOT_NOT_A_NAME_RE, _OEBB_SENTENCE_SPLIT_RE, _oebb_cannot_run_parts, _render_oebb_follow_up,
    _WL_SENTENCE_SPLIT_RE, _RELOCATION_CLOSED_LINE_RE,
)
from .feed_build.translate_run import (
    _render_label_record, _render_record_table, _oebb_cause_en, _render_oebb_sentence, _render_wl_sentence,
    _render_wl_because_sentence, _translate_text_attempt,
)
from .feed_build.translate_titles import _translate_text, _translate_title_attempt, _attempt_for_field
from .feed_build.wl_measures import (
    _WL_DESC_LINIE_PREFIX_RE, _strip_wl_description_line_prefix, _WL_DESC_LINE_SEGMENT_SPLIT_RE,
    _collapse_repeated_wl_line_segments, _tidy_wl_dangling_location, _wl_measure_labels_with_colon,
    _prefer_measures, _SHORT_LINE_MARKER_RE, _short_measure, _every_line_in_short, _lines_in_short,
    _text_names_line,
)
from .feed_build.cache_filters import (
    _attribute_obb_replacement_bus, _post_filter_wl, _post_filter_oebb, _post_filter_baustellen,
)
from .feed_build.providers_setup import (
    DEFAULT_PROVIDERS, PROVIDERS, reset_module_state, init_providers, _detect_stale_caches, _provider_statuses,
    _log_startup_summary, _validate_configuration, _read_optional_non_negative_int, _provider_timeout_override,
    _call_fetch_with_timeout,
)
from .feed_build.state import (
    _parse_lines_from_title, MAX_STATE_FILE_BYTES, _load_state, _read_state_capped, _write_merged_state,
    _save_state, _identity_for_item, _dedupe_key_for_item, _state_key_for_item,
)
from .feed_build.collect import (
    _report_empty_provider, _submit_network_fetches, _drain_completed_futures, _collect_items,
)
from .feed_build.time_line import (
    _time_line_day, _plausible_end, format_local_times, _MEASURED_SOURCES, _first_published, _incident_since,
    _is_all_clear, _wl_incident_start,
)
from .feed_build.summary_text import (
    _strip_trailing_directional_marker, _reason_only_summary, _DOUBLED_FULL_STOP_RE, _SPACE_BEFORE_PUNCT_RE,
    _SPACE_BEFORE_COMMA_RE, _EMPTY_PLACE_RE, _drop_passed_expected_end, _expected_end_sentence, _close_blocks,
    _WL_REFERRAL_BOILERPLATE_RE, _truncate_summary_180, _compose_description, _title_body,
)
from .feed_build.title_words import (
    _drop_category_word, _leading_category_word, _separate_reason_word, _HINDRANCE, _reason_and_fragment,
    _finish_reason_title, _last_sentence_end, _rendered_title, _short_title_collisions, _is_wl_ticker,
    _as_sentence, _is_wl_notice, _is_baustelle,
)
from .feed_build.item_history import (
    _OCCURRENCE_GAP, _restart_recurring_occurrences, _note_announced_starts, _count_new_items, _initial_first_seen,
    _carry_item_identity, _remember_item_identity,
)
from .feed_build.item_meta import (
    _sort_moment, _CURRENT_INCIDENT_WINDOW, _is_current_incident, _recency_sort_key, _build_canonical_link,
    _ITEM_IMAGE_URL, _ITEM_IMAGE_BYTES, _ITEM_IMAGE_WIDTH, _ITEM_IMAGE_HEIGHT, _add_item_image, _shown_end,
    _item_source, _source_item_link, _cdata_content, _placeholder_collides_with_formatted, FormattedContent,
    _update_item_state,
)
from .feed_build.filters import (
    _valid_until, _drop_after, _drop_old_items, _summarize_duplicates, _dedupe_items, _topic_budget_key,
    _apply_topic_budget, _defer_repeated_route_titles, _is_test_message, _drop_test_messages, _preview_last_day,
    _defer_upcoming_items, _defer_all_clear_items,
)


__all__ = [
    "RunReport", "ThreadPoolExecutor", "feed_config", "MEDIA_NS", "_ARTICLE_BEFORE_STREET_RE",
    "_CATEGORY_PREFIX_WORDS", "_CONTROL_RE", "_DATE_ENTITY_RE", "_DISPLAY_TITLE_TARGET",
    "_ENTITY_PLACEHOLDER_FORMAT", "_ENTITY_PLACEHOLDER_RE", "_GLOSSARY_PLACEHOLDER_FORMAT", "_LABEL_RECORD_RE",
    "_LINE_ENTITY_RE", "_MIN_LABELS_FOR_RECORD", "_MarianTranslator", "_OEBB_LABELLED_SENTENCE_RE",
    "_PLACEHOLDER_NONCE", "_RELOCATION_CLOSED_LINE_RE", "_RESIDUAL_PLACEHOLDER_RE", "_SENTENCE_SPLIT_RE",
    "_STREET_SUFFIX_RE", "_TITLE_REASON_WORDS", "_TRAM_LETTER_LINE_RE", "_TRANSLATION_MODEL_REVISION",
    "_TRANSLATION_STATE", "_UNMASK_PLACEHOLDER_RE", "_VIENNA_TZ", "_WL_SENTENCE_SPLIT_RE", "_agreeing_article",
    "_apply_domain_glossary", "_capitalise_glossary_after_stop", "_capitalise_sentence_start",
    "_capitalise_title_body", "_cause_due_to", "_compact_baustellen_section", "_compact_line_prefix",
    "_declined_station_names", "_display_title", "_display_title_parts", "_drop_article_before_line",
    "_drop_article_before_street", "_drop_unpaired_quote", "_english_weekdays_capitalised",
    "_entities_dropped_by_translation", "_fix_glossary_articles", "_fmt_rfc2822", "_from_short_placeholders",
    "_get_translation_pipeline", "_has_short_placeholder_shape", "_is_non_translatable_content", "_join_record",
    "_leftover_placeholder", "_mark_house_numbers", "_mask_entities", "_norm_metadata",
    "_normalise_for_translation", "_normalise_placeholder_debris", "_normalize_item_datetimes",
    "_oebb_cannot_run_parts", "_oebb_cause_en", "_oebb_long_date_lists_in_words", "_oebb_section_label_en",
    "_oebb_section_labels_cased", "_oebb_upcoming_dates", "_oebb_what_does_not_run_first", "_ordnungsnummer_as_nr",
    "_placeholder_nonce", "_record_translation_status", "_redirected_towards", "_render_label_record",
    "_render_oebb_follow_up", "_render_oebb_sentence", "_render_record_table", "_render_wl_because_sentence",
    "_render_wl_sentence", "_repair_baustellen_title", "_resolve_glossary", "_respace_glued_placeholders",
    "_sanitize_text", "_section_collisions", "_shorten_wl_sentence_title", "_split_label_record",
    "_split_reason_title", "_station_base_forms", "_station_entity_names", "_street_names_nominative",
    "_to_short_placeholders", "_to_utc", "_translate_text", "_translate_text_attempt", "_translate_title_attempt",
    "_unmask_entities", "_ymd_or_none", "validate_path", "_FIELD_LABEL_EN", "_GLOSSARY_BASE",
    "_GLOSSARY_BY_CATEGORY", "_GLOSSARY_BY_SOURCE", "format_datetime", "json", "file_lock",
    "_WL_DESC_LINIE_PREFIX_RE", "_strip_wl_description_line_prefix", "_WL_DESC_LINE_SEGMENT_SPLIT_RE",
    "_collapse_repeated_wl_line_segments", "_tidy_wl_dangling_location", "_wl_measure_labels_with_colon",
    "_prefer_measures", "_lines_in_short", "_attribute_obb_replacement_bus", "_post_filter_wl",
    "_post_filter_oebb", "_post_filter_baustellen", "DEFAULT_PROVIDERS", "PROVIDERS", "reset_module_state",
    "_detect_stale_caches", "_provider_statuses", "_read_optional_non_negative_int", "_provider_timeout_override",
    "_call_fetch_with_timeout", "_parse_lines_from_title", "MAX_STATE_FILE_BYTES", "_load_state",
    "_read_state_capped", "_write_merged_state", "_save_state", "_identity_for_item", "_dedupe_key_for_item",
    "_state_key_for_item", "_report_empty_provider", "_submit_network_fetches", "_drain_completed_futures",
    "_collect_items", "_time_line_day", "_plausible_end", "format_local_times", "_MEASURED_SOURCES",
    "_first_published", "_incident_since", "_is_all_clear", "_strip_trailing_directional_marker",
    "_reason_only_summary", "_drop_passed_expected_end", "_expected_end_sentence", "_WL_REFERRAL_BOILERPLATE_RE",
    "_truncate_summary_180", "_compose_description", "_leading_category_word", "_separate_reason_word",
    "_reason_and_fragment", "_last_sentence_end", "_short_title_collisions", "_is_wl_ticker", "_OCCURRENCE_GAP",
    "_restart_recurring_occurrences", "_note_announced_starts", "_count_new_items", "_initial_first_seen",
    "_carry_item_identity", "_remember_item_identity", "_sort_moment", "_CURRENT_INCIDENT_WINDOW",
    "_is_current_incident", "_recency_sort_key", "_build_canonical_link", "_ITEM_IMAGE_URL", "_ITEM_IMAGE_BYTES",
    "_ITEM_IMAGE_WIDTH", "_ITEM_IMAGE_HEIGHT", "_placeholder_collides_with_formatted", "FormattedContent",
    "_update_item_state", "_valid_until", "_drop_after", "_drop_old_items", "_summarize_duplicates",
    "_dedupe_items", "_topic_budget_key", "_apply_topic_budget", "_defer_repeated_route_titles",
    "_is_test_message", "_drop_test_messages", "_preview_last_day", "_defer_upcoming_items",
    "_defer_all_clear_items",
]


def refresh_from_env() -> None:
    """Refresh configuration values and reload provider plugins."""
    feed_config.refresh_from_env()
    load_provider_plugins(force=True)


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


# Words of the first measure that must still stand behind the head after the
# 180 cut for the measure block to ride along: "Linie D: Umleitung der Züge
# …" says what happens, "Linie D: …" or "Linie D: Umleitung …" does not.
_MEASURE_MIN_WORDS_SHOWN = 3


def _oebb_route_keys(text: str) -> set[frozenset[str]]:
    """The station pairs of every ``zwischen A und B`` route in *text*.

    ÖBB writes alternatives into one slot ("zwischen Salzburg Hbf und
    Flughafen Wien Bahnhof bzw. Hegyeshalom"); each one is its own pair.
    """
    from .providers.oebb import _extract_zwischen_routes
    from .utils.stations import station_named_in_text

    def names(slot: str) -> list[str]:
        infos = (station_named_in_text(part.strip()) for part in re.split(r"\s+bzw\.?\s+", slot))
        return [info.name for info in infos if info is not None]

    return {
        frozenset((a, b))
        for slot_a, slot_b in _extract_zwischen_routes(text)
        for a in names(slot_a)
        for b in names(slot_b)
    }


def _oebb_title_route_keys(title: str) -> set[frozenset[str]]:
    """The station pairs of the ``A ↔ B`` routes in an ÖBB title."""
    from .utils.stations import station_named_in_text

    rest = _OEBB_TITLE_LINES_RE.sub("", title.strip(), count=1)
    keys: set[frozenset[str]] = set()
    for segment in re.split(r"\s+/\s+", rest):
        infos = [station_named_in_text(part.strip()) for part in segment.split("↔")]
        for info_a, info_b in pairwise(infos):
            if info_a is not None and info_b is not None:
                keys.add(frozenset((info_a.name, info_b.name)))
    return keys


def _oebb_title_route_first(sentences: list[str], title: str, *, oebb: bool) -> list[str]:
    """Lead an ÖBB text with the sentence that names the title's route.

    The provider titles an ÖBB notice with its Wien-relevant route, which
    may come from a later section of the text (``_apply_route_title``),
    while the display shows sentence one. Published 2026-10-17 from the
    ÖBB notice "Salzburg Hbf => Flughafen Wien Bahnhof/Hegyeshalom"::

        Wien Hütteldorf ↔ Wien Hauptbahnhof
        Wegen Bauarbeiten fahren zwischen Salzburg Hbf und Flughafen Wien
        Bahnhof bzw. Hegyeshalom von 10.10.2026 (22:30 Uhr) bis … [Bis Mi 21.10.]

    The title came from "TEILAUSFALL: … kann der Zug RJ 263 zwischen Wien
    Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren.", a sentence the
    TV viewer never saw: title and text named two different routes.

    When sentence one names a route and none of its stations stands in the
    title, the first later sentence naming a title route moves to the
    front; sentence one follows it when both fit
    (:func:`_with_second_sentence`). Sentence one stays first when it names
    no route (it then speaks for every route) or shares a station with the
    title: "zwischen Gänserndorf und Deutsch Wagram" under "Wien Floridsdorf
    ↔ Gänserndorf" is the same works seen from the neighbouring stretch,
    and its first sentence carries the dates the later one lacks.
    """
    if not oebb or len(sentences) < 2:
        return sentences
    title_keys = _oebb_title_route_keys(title)
    first_keys = _oebb_route_keys(sentences[0])
    if not title_keys or not first_keys:
        return sentences
    if set().union(*first_keys) & set().union(*title_keys):
        return sentences
    for index, sentence in enumerate(sentences[1:], start=1):
        if _oebb_route_keys(sentence) & title_keys:
            return [sentence, *sentences[:index], *sentences[index + 1:]]
    return sentences


_OEBB_SECTION_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
# A section whose end is no date: its date range runs on "bis" to anything
# but a date ("von 04.07.2026 (ca. 02:00 Uhr) bis voraussichtlich Ende 2027",
# "bis auf Weiteres"), or it names a start date ("ab 04.07.2026", "seit …")
# and no end. Its last date is where it begins, not where it ends. A "bis"
# date anywhere in the section ends it: a "bis" elsewhere belongs to a
# weekday window ("von 31.10.2026 bis 30.11.2026 (jeweils von Samstag 23:15
# Uhr bis Sonntag 04:40 Uhr …)"), a clock time or a route ("nur bis Wien
# Meidling"), not to the range.
_OEBB_SECTION_OPEN_END_RE = re.compile(
    r"\b\d{1,2}\.\s?\d{1,2}\.(?:\d{4})?\s*(?:\([^)]*\)\s*)?bis\b", re.IGNORECASE
)
_OEBB_SECTION_OPEN_START_RE = re.compile(r"\b(?:ab|seit)\s+(?:dem\s+)?\d{1,2}\.\s?\d{1,2}\.", re.IGNORECASE)
# "bis 19.10.2026", "bis zum 19.10.", "bis voraussichtlich Sonntag, 01.11.2026".
_OEBB_SECTION_BIS_DATE_RE = re.compile(
    r"\bbis\s+(?:[^\W\d]+\.?,?\s+){0,2}\d{1,2}\.\s?\d{1,2}\.", re.IGNORECASE
)


def _oebb_section_end_is_open(section: str) -> bool:
    """Whether *section* ends without a date (see ``_OEBB_SECTION_OPEN_END_RE``)."""
    if _OEBB_SECTION_BIS_DATE_RE.search(section):
        return False
    return bool(_OEBB_SECTION_OPEN_END_RE.search(section) or _OEBB_SECTION_OPEN_START_RE.search(section))


def _oebb_lead_section_end(it: Any) -> datetime | None:
    """The end of the ÖBB section that leads the shown text, when it ends sooner.

    Operator decision 2026-10-19 (card "Ja, als Regel"): when
    :func:`_oebb_title_route_first` moves a later section to the front
    because it names the title's route, the end marker and the item's end
    follow that section. Published 2026-10-19 from TRACKINFO 901477 (notice
    17.10.–21.10. for works between Salzburg and Flughafen Wien)::

        Wien Hütteldorf ↔ Wien Hauptbahnhof
        Teilausfall: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263
        zwischen Wien Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren.
        [Bis Mi 21.10.]

    The text and the marker disagreed, and from 20.10. the slide would
    have shown a cancellation already over. The section's last date counts
    (end of that day, Vienna); it only ever shortens ``ends_at``, never
    extends it, and a section without a date keeps the notice's end.

    So does a section whose end is no date (``_oebb_section_end_is_open``):
    "Von 04.07.2026 (ca. 02:00 Uhr) bis voraussichtlich Ende 2027" or "Ab
    04.07.2026" names only its start, and the start day as the end would
    take a closure of a year off the feed after its first day (independent
    check 2026-10-24; no such section has led an ÖBB text yet).
    """
    if it.get("source") != _OEBB_SOURCE:
        return None
    ends_at = it.get("ends_at")
    if not isinstance(ends_at, datetime):
        return None
    raw_title, _ = _display_title_parts(it)
    text, _ = _close_blocks(
        html_to_text(it.get("description") or "", collapse_newlines=True, mark_block_ends=True),
        raw_title,
    )
    text = _sanitize_text(text).strip()
    text = _DATE_SINGLE_PREFIX_RE.sub("", _DATE_RANGE_PREFIX_RE.sub("", text))
    text = _WHITESPACE_CLEANUP_RE.sub(" ", text.replace("•", " ")).strip()
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    lead = _oebb_title_route_first(sentences, raw_title, oebb=True)
    if not lead or not sentences or lead[0] == sentences[0]:
        return None
    if _oebb_section_end_is_open(lead[0]):
        return None
    days: list[date] = []
    for day, month, year in _OEBB_SECTION_DATE_RE.findall(lead[0]):
        try:
            days.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    if not days:
        return None
    last = max(days)
    end = datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=_VIENNA_TZ)
    starts_at = it.get("starts_at")
    if isinstance(starts_at, datetime) and end < starts_at:
        return None
    return end if end < ends_at else None


def _end_with_oebb_lead_section(items: list[FeedItem]) -> None:
    """Shorten ``ends_at`` to the shown ÖBB section (:func:`_oebb_lead_section_end`)."""
    for it in items:
        if isinstance(it, dict):
            end = _oebb_lead_section_end(it)
            if end is not None:
                it["ends_at"] = end


def _with_second_sentence(first: str, second: str, *, oebb: bool, raw_title: str) -> str:
    """The summary's first sentence, with the second one when it may follow.

    The second sentence follows whenever both fit the 180 characters. ÖBB's
    second sentence says what still goes on (``_oebb_first_two_sentences``).

    A WL measure block (see :func:`_prefer_measures`) follows even when it
    does not fit, and ``_truncate_summary_180`` cuts it with an ellipsis:
    the block is joined into one sentence, so as a whole it nearly never
    fits behind a kept sentence, and the 180-check dropped every measure.
    Published 2026-10-13 for the demonstration on the Ring::

        1/71/D: Demonstration am 13.10.2026
        Wegen einer Demonstration kommt es zu einer Einschränkung des
        öffentlichen Verkehrs. [Heute]

    while the text went on "Linie D: Umleitung der Züge zwischen
    Absberggasse und Börse …" (18 texts since July, Jules audit
    2026-10-13). It follows only when the cut still shows the first
    measure's line and :data:`_MEASURE_MIN_WORDS_SHOWN` words of it;
    behind a long first sentence a bare "Linie D: …" says nothing.
    """
    candidate = f"{first} {second}"
    if len(candidate) > 180 and oebb:
        candidate = _oebb_first_two_sentences(first, second, raw_title) or candidate
    if len(candidate) <= 180:
        return candidate
    label = _WL_DESC_LINIE_PREFIX_RE.match(second)
    if label is None:
        return first
    shown = _truncate_summary_180(candidate)[len(first):].removesuffix("…").strip()
    label_shown = _WL_DESC_LINIE_PREFIX_RE.match(shown)
    if label_shown is None or len(shown[label_shown.end():].split()) < _MEASURE_MIN_WORDS_SHOWN:
        return first
    return candidate


# Translation-cache epoch. EN translations are cached per disruption
# identity in ``state[ident]["translations"]["en"]`` and persisted
# across builds. The cache key (see :func:`_identity_for_item`) is
# stable across cosmetic changes — a Baustellen item keyed on
# ``title + date-range`` survives a description-only edit — so a
# translation computed once is served for the lifetime of the item
# (multi-year for long construction projects).
#
# That persistence is a problem whenever the masking / glossary logic
# IMPROVES: an item translated by an older build keeps its stale
# rendering forever, because :func:`_cached_translation` only forces a
# retry when the cached value equals the German source (the
# "Sticky-German" guard). A cached value that is a *wrong but
# non-German* translation — e.g. ``Schlachthausgasse`` rendered as
# "slaughterhouse gas" before the street-suffix masker existed — is
# served indefinitely.
#
# The epoch breaks that lock. Bump this integer whenever a change to
# :func:`_mask_entities`, the entity patterns, or the domain glossary
# would alter the EN output for already-cached items. On the next
# build :func:`_apply_lang_overlay` evicts every translation stamped
# with an older epoch and recomputes it through the improved
# pipeline, then re-stamps the current epoch so subsequent builds
# trust the cache again (no churn).
#
# Epoch history:
#   1 — first epoch. Invalidates every pre-epoch cache entry, picking
#       up the street-suffix masker (#1624), the metadata-driven
#       glossary (#1625) and the disruption-core scope tightening.
#   2 — "ggü." / "ggü" (gegenüber) → "opp." / "opp" street-addressing
#       abbreviation added to the base glossary.
#   3 — station masker now derives clean surface variants from
#       ``(WL)`` / ``(VOR)``-suffixed canonical names (e.g.
#       ``Schloss Hetzendorf`` from ``Wien Schloss Hetzendorf (WL)``),
#       so Vienna stop names with a translatable component are no
#       longer mistranslated ("Schloss Hetzendorf" → "lock Hetzendorf").
#   6 — per-field source fingerprints (``_SOURCE_DIGEST_KEY``) added, so a
#       reworded German source invalidates its cached English instead of
#       serving it for the item's lifetime. The bump exists to stamp a
#       digest onto the ~2500 entries cached without one; it asserts no
#       change in translation quality.
#   7 — street-suffix masker learned the abbreviated ``…str.`` form and
#       the leading adjective (``Vordere``/``Hintere``/``Kleine`` …), so
#       ``Hintere Zollamtsstr.`` stops rendering as "rear customs office"
#       and ``Vordere Zollamtsstraße`` as "Front Zollamtsstraße". Without
#       the bump the affected items keep serving the wrong English for
#       their lifetime — a U4 construction notice runs until 30.11.2026.
#   8 — the translation is now rejected when the model drops a verbatim
#       entity, and repaired when it doubles a placeholder's closing ``X``.
#       Both defects were cached as successes, so without the bump the two
#       items that exposed them keep serving the wrong English: the
#       ``72A`` stop relocation names 510 as the address it moves *from*
#       (the source says 2) and runs until 18.09.2027, and the ``12A/14A``
#       notice advertises a line ``14AX`` that does not exist.
#   9 — WL's structured field labels (``Von:`` / ``Nach:`` / ``Haltestelle:``
#       / ``Dauer:`` / ``Zeitraum:`` / ``Grund:``) and the term
#       ``Haltestellenverlegung`` now resolve through the glossary instead
#       of being guessed at. ``Nach:`` was rendering as "After:" beside a
#       correct "From:" — a relocation with an origin and no destination —
#       and the opening term came back as "Stop change", "Station
#       relocation", "Stop transfer" or untranslated, depending on the item.
#       Cached items keep every one of those until the epoch moves.
#  10 — a trailing ``Label: value`` record is now split off and rendered
#       without the NMT model at all, and ``gegenüber`` joined its ``ggü.``
#       sibling in the glossary. The three relocation items themselves were
#       not cached — since epoch 8 their translation is rejected outright and
#       a failed attempt persists nothing — so the bump is for the items
#       cached BEFORE that rejection began, and for every cached item
#       carrying a spelled-out ``gegenüber``.
#  11 — ``a``/``an`` now agrees with the word the glossary substitutes, so
#       "an switch fault", "an demonstration" and "an service obstruction"
#       stop reaching subscribers. 15 occurrences across 314 published EN
#       texts, every one of them cached as a success — the article is wrong
#       without being German, so nothing else would evict it.
#  12 — measured gaps in the disruption vocabulary closed: Schadhafter
#       Zug/Bus/PKW ("Harmful train/bus/car"), Fremdunfall ("Foreign
#       accident"), Falschparker ("False Parker"), Verunreinigung
#       ("Impurity"), Wasserrohrgebrechen, Klapprampensperre ("Folding ramp
#       lock") and Verkehrsstörung. 31 published items carry one of these
#       and every one of them is cached as a success.
#  13 — the definite article in front of a line identifier is dropped:
#       "the lines 36A and 36B are being redirected" becomes "lines 36A and
#       36B …", the way Wiener Linien write it in English themselves. German
#       requires the article, English transit usage does not, and the model
#       cannot resolve the difference because the identifier it precedes is a
#       masked placeholder. 23 of 294 published EN texts carry it, all cached
#       as a success — the article is wrong without being German, so nothing
#       else would evict it.
#  14 — the same article, one word class over: German articles its street
#       and square names ("in der Althanstraße", "zum Karlsplatz") and
#       English leaves them bare. 5 of 310 published EN texts carry it,
#       all cached as a success. Only the article goes; the preposition
#       stays as the model chose it, and the street test is the masker's
#       own ``_STREET_SUFFIX_RE``, so "the Ernst-Happel-Stadion" and "the
#       Wiener Linien" — where the article is defensible English — are
#       untouched.
#  15 — the C.5 separator. "31: Demonstration – Betrieb ab …" reached the
#       model with the en dash masked as a placeholder wedged between two
#       words; the model re-emitted it with debris ("Demonstration –Xservice
#       from …", published 2026-09-19 21:30, cached as a success because the
#       stray X is glued to a word and no guard sees it). Titles of that
#       shape are now translated in two halves and never carry the dash
#       into the model; the cached debris has to go.
#  16 — the values of a label record are English too. The record never
#       reaches the model, so "Ab", "etwa", "bis", the month names and the
#       open-end phrases ("Dauer unbekannt", "bis auf Widerruf") stayed
#       German in every rendered record — 3 of the 10 EN items on
#       2026-09-24, all cached as a success. Same push: "ersatzlos
#       aufgelassen" as one phrase ("replacementless Discontinued").
#  17 — "Haltestellenauflassung" joins its sister "Haltestellenverlegung"
#       in the glossary. The N31 prose was re-translated under epoch 16 and
#       cached as "Stop stop on line N31 towards Schwedenplatz U"; only a
#       bump evicts it, the source digest is unchanged.
#  18 — three glossary phrases (audit 2026-09-25, A.3 and A.9, and the
#       ÖBB replacement bus). Cached under 17: "S80: ÖBB-replacement bus",
#       "Removing traffic restrictions: St. Pölten Hauptbahnhof", and label
#       records reading "until expected end of November". The source digests
#       are unchanged, so only a bump evicts them.
#  19 — an ``X`` the model put in front of a placeholder now becomes the
#       space it replaced (``_PLACEHOLDER_LEADING_X_RE``). The D notice
#       was cached under 18 as "WipplingerstrX39; service fromXAugasse"
#       and stood second in the EN feed on 2026-10-02; the source digest
#       is unchanged, so only a bump evicts it.
#  20 — EN audit 2026-10-03: WL's reason vocabulary in the glossary
#       ("Fremder Verkehrsunfall", "Stromstörung", "im Haltestellenbereich",
#       …), the reason of a label record rendered or translated instead of
#       left German, "Voraussichtliche Dauer:" as one label, bare-hour
#       clock times, freestanding "-er" street names, WL's comma stop names
#       and ``<Ort> Hbf`` shielded, ÖBB's templated sentences rendered from
#       their slots. Cached under 19 among others: "Reason: Fremder traffic
#       accident in the area of Hernalser Hauptstraße", "Alliance Stadion",
#       "Schlosshofer Straße"; the source digests are unchanged, so only a
#       bump evicts them.
#  21 — WL's stop tickers (Jules audits 2026-10-02 and 2026-10-04): a place
#       after "Busse/Züge halten" gets "at", the stop of another line is
#       written out ("Trains stop for lines 6 and 18" before). Cached under
#       20: "66A: Buses stop Salvatorianerplatz"; the source digests are
#       unchanged, so only a bump evicts it.
#  22 — WL's "Wegen <Ursache> <Verb> …" sentences rendered as cause plus
#       main clause (``_render_wl_because_sentence``). Cached under 21: "The
#       U6 station stops at Neue Donau U only in the direction of
#       Siebenhirten U.", the cause dropped; the source digest is unchanged,
#       so only a bump evicts it.
#  23 — a relocation's "A → B" rendered without the model again
#       (``_render_wl_sentence``), and a cause without "Due to" gets it back.
#       Cached under 22: "Wienerbergstraße27b-27c →Wienerbergstraße27a. …
#       15A5X …" and "Renovation of the platform, line U6 …".
#  24 — EN sense errors (check of 2026-10-08, every DE text since July
#       through the real model): WL's "kommt es zu Verkehrsmaßnahmen" as
#       "service changes", "unterschiedlichen Intervallen", WL's shorthand
#       ("Li.", "Ri.", "Hlst.", "ONr.", "Steig A"), "Einstieg bei Linie",
#       "nicht eingehalten", "über Gleis 2", "kurzgeführt", place names the
#       model translated ("Ring", "Oper", "Julius Raab Platz", the name
#       after "Richtung") and the roadworks vocabulary. Cached under 23:
#       "Due to a demonstration, transport is taking place.", "via 2
#       track", "Trains stop at Li. O Ri. Raxstraße."; the source digests
#       are unchanged, so only a bump evicts them.
#  25 — check of 2026-10-09: a stop sentence with a line ("Ersatzbus 26E
#       hält an …", "Linie 5 hält …") gets "stops at" like "Busse halten"
#       (cached: "Replacement bus 26E holds … ahead of …"); ÖBB's templated
#       sentence is no longer split after "St." ("in St. Pölten Hbf"), and
#       its fixed follow-up sentences are written out. The source digests
#       of the stop texts are unchanged, so only a bump evicts them.
#  26 — Jules audits 18. and 22.10.2026: a relocation's place kept German
#       words in English ("Gleiskörper", "Haltestelle Straßenbahn",
#       "Parkanlage", "Richtung Ampel", …; ``_RECORD_VALUE_RULES``). Cached
#       under 25: "Simmeringer Hauptstraße opposite 120-122, Gleiskörper →
#       …"; the source digests are unchanged, so only a bump evicts them.
#  27 — independent check 2026-10-24: "Umleitungen" is a fixed term only as
#       the head of the short form ("Umleitungen: 36B …"); inside a sentence
#       the model got no noun to build on. Cached under 26: "Due to roadworks
#       in … crossing area, diversions occurs."
#  28 — follow-up check 2026-10-24: "kommt es zu Umleitungen" is fixed as
#       "there are diversions" (operator wish: one word for the same thing
#       across sources). Cached under 27: "Due to roadworks in … intersection,
#       there are redirections."
#  29 — Jules audits 26. and 27.10.2026: a street name in the genitive
#       ("Streckensperre des Algenwegs") was not shielded and went through
#       the model; it is now shielded and restored in its base form. Cached
#       under 28: "Due to an ordered line closure of the algae path, line 89A
#       is redirected."; the source digest is unchanged, so only a bump
#       evicts it.
#  30 — Jules audits 28. and 29.10.2026: street names with a preposition
#       ("An den Eisteichen") or a hyphenated prefix ("Unter-Meidlinger
#       Straße") are shielded whole, and WL's relocation reasons are in the
#       glossary. Cached under 29: "in the area of At the ice ponds", "Under-
#       Meidlinger Straße", "Reason: Parked original stop."; the source
#       digests are unchanged, so only a bump evicts them.
#  31 — KI-Wächter of 2026-11-02 (feed state of 30.10.): the unknown half
#       of a slash-joined stop name ("Parlament/ Volkstheter", WL's typo) is
#       shielded verbatim. Cached under 30: "service from Parlament/
#       Folktheter"; the source digest is unchanged, so only a bump evicts it.
_TRANSLATION_CACHE_EPOCH = 31

# Static lookup for the German words of the bracketed ``[…]`` time line (see
# ``format_local_times``). Translating these via the ML model would be
# wasteful — every item shares the same handful of words — and slow (each
# translate call hits the tokenizer). The mapping mirrors every word
# ``format_local_times`` emits; dates pass through unchanged.
_TIME_WORDS_DE_TO_EN: dict[str, str] = {
    "Seit": "Since",
    "Bis": "Until",
    "Ab": "From",
    "Am": "On",
    "bis": "until",
    "Heute": "Today",
    "heute": "today",
    "Mo": "Mon",
    "Di": "Tue",
    "Mi": "Wed",
    "Do": "Thu",
    "Fr": "Fri",
    "Sa": "Sat",
    "So": "Sun",
}


# One word of a bracketed time line: no space, no bracket.
_TIME_LINE_WORD_RE = re.compile(r"[^\s\[\]]+")


# Fields whose correct English translation IS the German source, verified by
# an actual model run. Lives beside ``en`` in ``translations`` rather than
# inside it, so the ``en`` sub-dict stays a homogeneous field→string map.
#
# Why this marker has to exist (audit 2026-09-12, Befund 7). The cache lookup
# treats "stored translation equals the source" as evidence of an earlier
# BROKEN build that persisted the German text as the translation, and
# re-translates. For a title made only of station names — ``Wien Hauptbahnhof
# ↔ Felixdorf`` — the correct English *is* the German, so the condition holds
# forever and the model re-ran on every build without ever producing a
# different answer. Measured before the fix: five builds, five model runs.
#
# A stored string alone cannot tell the two cases apart. The marker can,
# because it is written ONLY where the two differ: after
# ``_translate_text_attempt`` returned a non-``None`` result. A failed attempt
# returns ``None`` and persists nothing, so a build that could not translate
# can never mark a field verbatim — the drift guard keeps its teeth.
#
# Epoch-scoped like the translations themselves: a later epoch may well
# translate the same text differently, so ``_evict_stale_translations`` drops
# this key together with ``en``.
_VERBATIM_FIELDS_KEY = "en_verbatim"


def _verbatim_fields(translations: dict[str, Any]) -> set[str]:
    """Return the field names recorded as legitimately untranslated."""
    raw = translations.get(_VERBATIM_FIELDS_KEY)
    if not isinstance(raw, list):
        return set()
    return {name for name in raw if isinstance(name, str)}


def _record_verbatim(
    translations: dict[str, Any], field: str, *, identical: bool
) -> None:
    """Record — or withdraw — ``field``'s verbatim mark after a model run.

    Withdrawing matters as much as setting: once a glossary or masking
    change makes a previously untranslatable text translate, the stale mark
    would otherwise keep asserting "identical is correct" about output that
    is no longer identical.

    The key is removed entirely when no field is left, so the 2500+ entries
    in the persisted state do not each grow an empty list.
    """
    fields = _verbatim_fields(translations)
    if identical:
        fields.add(field)
    else:
        fields.discard(field)
    if fields:
        translations[_VERBATIM_FIELDS_KEY] = sorted(fields)
    else:
        translations.pop(_VERBATIM_FIELDS_KEY, None)


# Per-field fingerprint of the GERMAN source a cached translation was made
# from — the axis the cache was missing.
#
# The cache is keyed ``(ident, field)``. ``ident`` identifies the disruption,
# not its wording, and upstream rewords a live disruption freely as the
# situation develops while keeping the same identity. Nothing compared the
# cached English against the German it came from (``cached != text`` compares
# an English string to a German one, which is essentially always true), so the
# first translation of an item was served for the item's whole lifetime.
#
# Observed live on 2026-09-13, ``docs/feed.en.xml`` item 1: the German title
# had become "44: Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz" while
# the English still read "44: event Trains stop Rosensteingasse on line 9
# direction Westbahnhof" — the translation of a title the DE feed had already
# replaced. The two feeds were telling subscribers different things about the
# same GUID, which is worse than an untranslated item: it is a wrong one.
#
# ``_TRANSLATION_CACHE_EPOCH`` could not catch this. It invalidates when *our*
# glossary or masking changes; it knows nothing about upstream edits. The
# digest is the complementary signal, and the two are deliberately kept
# separate: bumping the epoch here would assert a translation-quality change
# that did not happen.
#
# A missing digest is TRUSTED rather than treated as a miss, and the rollout
# rides the epoch bump to 6 instead. Making "no digest" a miss would have
# worked too, but it duplicates what the epoch already does — invalidate
# everything once — and it would have broken the documented contract that a
# current-epoch cache is served without touching the pipeline. After the
# epoch-6 rebuild every entry carries a digest, and the two mechanisms keep
# one job each: the epoch for changes on our side, the digest for changes on
# upstream's. An entry cannot end up at epoch 6 without a digest —
# ``_record_source_digest`` runs in lockstep with the stored string, and the
# epoch is stamped only after every field succeeded.
#
# 16 hex chars (64 bits) is ample for change detection and keeps the persisted
# state (2500+ entries × 2 fields) from growing by a full digest each.
_SOURCE_DIGEST_KEY = "en_src"
_SOURCE_DIGEST_CHARS = 16


def _source_digest(text: str) -> str:
    """Return the short fingerprint of a German source string."""
    return hashlib.sha256(
        text.encode("utf-8", "surrogatepass")
    ).hexdigest()[:_SOURCE_DIGEST_CHARS]


def _source_digests(translations: dict[str, Any]) -> dict[str, str]:
    """Return the per-field source fingerprints recorded for ``translations``."""
    raw = translations.get(_SOURCE_DIGEST_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        field: value
        for field, value in raw.items()
        if isinstance(field, str) and isinstance(value, str)
    }


def _record_source_digest(
    translations: dict[str, Any], field: str, text: str
) -> None:
    """Stamp the source fingerprint ``field``'s cached translation came from.

    Written in lockstep with the translation itself, so a field can never
    carry a translation without the digest that validates it.
    """
    digests = _source_digests(translations)
    digests[field] = _source_digest(text)
    translations[_SOURCE_DIGEST_KEY] = dict(sorted(digests.items()))


# ÖBB says in its second sentence what is still going on: "Planen Sie
# derzeit noch bis zu 10 Minuten mehr Reisezeit ein." under a disruption
# that is over, "Über die Dauer der Unterbrechung kann derzeit noch keine
# Angabe gemacht werden." under one that runs. The German summary keeps two
# sentences within 180 characters, and the first one, with the two stations
# in ÖBB's long form ("zwischen Wien Leopoldau Bahnhst (U) und Wien
# Süßenbrunn Bahnhst"), took 140 of them: in 59 of the 106 ÖBB texts the
# German feed showed since September the second sentence was cut, and no
# text kept it. "Wien Leopoldau ↔ Wien Süßenbrunn" on 09.10.2026 at 20:30
# read "… waren … bis 20:18 Uhr keine Fahrten möglich. [Heute]", as if all
# were over. The title names the stations already, so where the second
# sentence would not fit, the first one leaves out the place it repeats.
# ÖBB's courtesy sentences tell a reader at the display nothing ("Wir
# bitten um Entschuldigung." closes 310 of the 316 ÖBB texts since June) and
# would take that room, so they go first.
_OEBB_COURTESY_RE: re.Pattern[str] = re.compile(
    r"\s*(?:Wir bitten um Entschuldigung|Sobald uns weitere Informationen vorliegen,"
    r" informieren wir Sie|Details finden Sie hier)\s*[.!…]*"
)
_OEBB_PLACE_SUFFIX_RE: re.Pattern[str] = re.compile(
    r"(?:\s+\[[^\]]*\]|\s+\((?:U|Wien)\)|\s+(?:Bahnhof|Bahnhst\.?|Bhf\.?))+$"
)


def _oebb_place_key(name: str) -> str:
    """*name* compared without ÖBB's suffixes, spaces and dots ("Wien Hbf (U)" → "wienhauptbahnhof")."""
    core = _OEBB_PLACE_SUFFIX_RE.sub("", name.strip())
    core = re.sub(r"\bHbf\b\.?", "Hauptbahnhof", core)
    return re.sub(r"[\s.]", "", core).casefold()


def _oebb_title_names(place: str, title: str) -> bool:
    """Whether the ÖBB *title* names the station *place* of a templated sentence."""
    key = _oebb_place_key(place)
    bare = key.removeprefix("wien")
    shown = re.sub(r"[\s.]", "", title).casefold()
    return len(bare) >= 4 and bare in shown


# Where the place stands is a matter of sentence structure, not of one
# template: "Wegen <Ursache> <Verb> … zwischen <A> und <B> | in <A> | im
# Bereich <A> …". Each station runs to ÖBB's suffixes ("Bahnhof", "(U)",
# "(Wien)") or to the first word that opens no name, and the title must name
# all of it. Read from the template's slots instead, a sentence that varies the
# wording behind the second station kept its place, and its second sentence
# went: "zwischen Wien Meidling Bahnhof (U) und Liesing (Wien) Zugfahrten
# voraussichtlich 23:00 Uhr nur eingeschränkt möglich." (no "bis", ÖBB on
# 13.08.2026) put "Zugfahrten voraussichtlich 23:00 Uhr" into the station
# slot, and "Es kommt zu vereinzelten Zugausfällen und Verspätungen bis zu 10
# Minuten." never reached the display. ÖBB's planned works ("Wegen
# Bauarbeiten können zwischen <A> und <B> am 01.11.2026 … keine S45-Züge
# fahren.") are the same sentence and lost "Ein Schienenersatzverkehr mit
# Autobussen wird für Sie eingerichtet." the same way. The place follows the
# main verb, so a place inside the cause ("Wegen eines Polizeieinsatzes in
# Wien Meidling …") stays; a place in a list (", und zwischen …") stays too.
# So does one that a list continues behind it ("zwischen A und B bzw. C und
# D, bis …", "in A und B …") or that another route follows ("zwischen A und B
# der Zug REX6 (7799) zwischen C und D der Zug S60 (26093) …"): without it
# the sentence read "Wegen Verlängerung der Bauarbeiten sind bzw. Wien Hbf
# (U) und Flughafen Wien Bahnhof, bis …" (ÖBB, 07.09.2026, ten characters
# short of the display), or gave the REX 6 the other route.
_OEBB_MAIN_VERB_RE: re.Pattern[str] = re.compile(
    r"Wegen .+? (?:sind|waren|ist|war|können|kann|konnten|fahren|fährt|werden|wird) "
)
_OEBB_PLACE_LEAD_RE: re.Pattern[str] = re.compile(r" (?P<lead>zwischen|im Bereich|in) ")
_OEBB_NAME_SUFFIXES_RE: re.Pattern[str] = re.compile(
    r"(?: (?:Bahnhof|Bahnhst\.?|Bhf\.?|\((?:U|Wien)\)|\[[^\]]*\]))*"
)


def _oebb_station_end(sentence: str, start: int, title: str, *, before_und: bool) -> int | None:
    """End of the station that begins at *start*, when *title* names it, else ``None``.

    The station is the shortest run of words, with ÖBB's suffixes, that " und "
    follows (*before_und*) or, behind it, the rest of the sentence: after a
    suffix any word, without one a word that opens no name ("bis",
    "Zugfahrten", "am 01.11.2026"). "Brunn am Gebirge-Maria Enzersdorf
    Bahnhof" thus stays one name, and a title with "Brunn am Gebirge" does
    not name it.
    """
    for gap in re.finditer(r" |$", sentence[start:]):
        name = sentence[start : start + gap.start()]
        if not name or "," in name or _OEBB_SLOT_NOT_A_NAME_RE.search(name):
            return None
        cut = start + gap.start()
        suffixes = _OEBB_NAME_SUFFIXES_RE.match(sentence, cut)
        end = suffixes.end() if suffixes is not None else cut
        if _oebb_name_ends_at(sentence[end:], suffixed=end > cut, before_und=before_und):
            return end if _oebb_title_names(name, title) else None
    return None


# A word behind a station without suffix that opens no name: the template's
# own words, a date or a clock time ("am 01.11.2026", "bis 20:18 Uhr").
_OEBB_AFTER_NAME_RE: re.Pattern[str] = re.compile(
    r"(?:(?:bis|derzeit|keine?|nur|einige|erneut|noch|Zugfahrten|Fahrten)"
    r"|\S*\d\S*|(?:am|um|von|vom|ab) \S*\d\S*"
    r")(?=[ .,;)]|$)"
)


# Words behind a station that continue a list of places.
_OEBB_PLACE_LIST_RE: re.Pattern[str] = re.compile(r" (?:und|bzw\.?|sowie|oder|u\.) ")


def _oebb_name_ends_at(rest: str, *, suffixed: bool, before_und: bool) -> bool:
    """Whether a station name may end where *rest* begins (see above)."""
    if before_und:
        return rest.startswith(" und ")
    if rest in {"", ".", "!"}:
        return True
    if not rest.startswith(" ") or _OEBB_PLACE_LIST_RE.match(rest):
        return False
    if suffixed:
        return True
    return _OEBB_AFTER_NAME_RE.match(rest, 1) is not None


def _oebb_sentence_without_place(sentence: str, title: str) -> str | None:
    """*sentence* without the place the ÖBB *title* already names, else ``None`` (see above)."""
    verb = _OEBB_MAIN_VERB_RE.match(sentence)
    if verb is None:
        return None
    lead = _OEBB_PLACE_LEAD_RE.search(sentence, verb.end() - 1)
    if lead is None:
        return None
    if lead["lead"] == "zwischen":
        first = _oebb_station_end(sentence, lead.end(), title, before_und=True)
        if first is None:
            return None
        end = _oebb_station_end(sentence, first + len(" und "), title, before_und=False)
    else:
        end = _oebb_station_end(sentence, lead.end(), title, before_und=False)
    if end is None or re.search(r"\bzwischen\b", sentence[end:]):
        return None
    return sentence[: lead.start()] + sentence[end:]


# ÖBB ends a sentence after a short word too: "Planen Sie bis zu 15 Minuten
# mehr Reisezeit ein.", "Die Züge warten die Sperre vorerst ab.",
# "… informieren wir Sie." The summary's split wants four letters before the
# full stop (for "Gerasdorf b. Wien", "Bahnhst bzw. Gerasdorf") and took such
# a sentence together with the next one as the second; the two did not fit,
# and the second sentence went with the third (Leopoldau ↔ Süßenbrunn,
# 09.10.2026, 20:00: "… Reisezeit ein. Fahrgäste mit ÖBB-Tickets können …").
# Where that happens, the second sentence ends at the first full stop that
# follows no abbreviation.
_OEBB_SECOND_SENTENCE_END_RE: re.Pattern[str] = re.compile(
    r"(?<=[.!])(?<!\b[A-Za-z]\.)(?<!\bbzw\.)(?<!\bzw\.)(?<!\bca\.)(?<!\bNr\.)(?<!\bSt\.)"
    r"(?<!\bStr\.)(?<!\bWr\.)(?<!\bHbf\.)(?<!\bBhf\.)\s+(?=[A-ZÄÖÜ])"
)


def _oebb_first_two_sentences(first: str, second: str, title: str) -> str | None:
    """ÖBB's first sentence with its *second*, within 180 characters, else ``None``.

    The first sentence leaves out the place the title names only where the
    second would not fit otherwise (operator decision 2026-10-09); *second*
    may hold a third sentence the summary's split did not see, which then
    goes before the place does.
    """
    shorter = _oebb_sentence_without_place(first, title)
    alone = _OEBB_SECOND_SENTENCE_END_RE.split(second, maxsplit=1)[0]
    candidates = [f"{first} {second}"]
    if shorter is not None:
        candidates.append(f"{shorter} {second}")
    if alone != second:
        candidates.append(f"{first} {alone}")
        if shorter is not None:
            candidates.append(f"{shorter} {alone}")
    return next((c for c in candidates if len(c) <= 180), None)


def _translate_time_line_en(time_line: str) -> str:
    """Swap the German words of a time line for English, word by word.

    ``time_line`` is the bracketed form emitted by
    :func:`_format_item_content` — e.g. ``[Seit 05.01.]``, ``[Heute]`` or
    ``[Ab Mo 05.10. bis 11.11.]`` (``[From Mon 05.10. until 11.11.]``).
    Dates, anything else unknown and the spaces between the words
    (NO-BREAK SPACE from ``format_local_times``) pass through unchanged.
    """
    return _TIME_LINE_WORD_RE.sub(
        lambda match: _TIME_WORDS_DE_TO_EN.get(match.group(0), match.group(0)), time_line
    )


def _cached_translation_defect(source: str, cached: str) -> str | None:
    """Return why a persisted EN value must not be served, or ``None``.

    Two defects can sit in ``data/first_seen.json`` as a cached *success*:

    * a residual placeholder — the model mangled a sentinel so badly that
      the exact-nonce unmask could not restore it (2026-06-01), or the
      unmask restored it and left debris behind (:data:`_CACHED_DEBRIS_RE`);
    * a calendar date the German source carries that the cached English
      does not — the day used to be masked alone and the model dropped the
      period, giving ``on 2709.2026`` for ``am 27.09.2026`` (published
      2026-09-24 19:31, two items). Dates are verbatim entities since then
      (:data:`_DATE_ENTITY_RE`), so a retry renders them correctly; without
      this check the value would sit in the cache until the item expires
      or the epoch moves — and an epoch move re-translates every item.

    Either way :func:`_cached_translation` treats the hit as a miss.
    """
    if _leftover_placeholder(source, cached):
        return "residual placeholder"
    if set(_DATE_ENTITY_RE.findall(source)) - set(_DATE_ENTITY_RE.findall(cached)):
        return "missing or mangled date"
    return None


def _oebb_section_label_defect(source: str, cached: str) -> str | None:
    """``"section label"`` when *cached* lacks a fixed name *source* needs.

    ÖBB's section labels have fixed English names since 2026-10-23
    (``_OEBB_SECTION_LABELS_EN``); a value cached before reads "Partial
    failure:". Only such an item is translated again, not every item, as an
    epoch move would.
    """
    for sentence in _OEBB_SENTENCE_SPLIT_RE.split(source.strip()):
        labelled = _OEBB_LABELLED_SENTENCE_RE.fullmatch(sentence)
        head = _oebb_section_label_en(labelled["label"]) if labelled is not None else None
        if head is not None and f"{head}:" not in cached:
            return "section label"
    return None


def _oebb_cannot_run_defect(source: str, cached: str) -> str | None:
    """``"trains sentence"`` when *cached* lacks what the template says.

    ÖBB's "… kann … nicht fahren." is rendered from its parts since
    2026-10-25 (:func:`_oebb_cannot_run_parts`); a value cached before reads
    "… the S2 train can't drive 23360." Only such an item is translated
    again, not every item, as an epoch move would.
    """
    for sentence in _OEBB_SENTENCE_SPLIT_RE.split(source.strip()):
        parts = _oebb_cannot_run_parts(sentence)
        if parts is not None and parts.head not in cached:
            return "trains sentence"
    return None


def _cached_translation(
    text: str,
    field: str,
    ident: str,
    state: dict[str, dict[str, Any]] | None,
    *,
    source: str | None = None,
    category: str | None = None,
) -> tuple[str, bool]:
    """Return the cached EN translation of ``text`` for ``ident``/``field``.

    Returns a tuple ``(translation, succeeded)`` where ``succeeded`` is
    ``True`` only when the ML pipeline actually produced a translation
    (either fresh or from the persistent cache). On failure the German
    source is returned with ``succeeded=False`` and **nothing is
    cached** — this fixes the "Sticky German" cache-corruption bug
    where a transient pipeline failure (model not yet downloaded,
    transformers import error, OOM) would poison the cache with the
    German source text and lock the item in German forever.

    Two further drift-protection guards:

      * If the persisted "translation" is byte-identical to the
        German source, treat it as a stale fallback from an earlier
        buggy build and retry — UNLESS the field is marked verbatim
        (:data:`_VERBATIM_FIELDS_KEY`), i.e. a real model run already
        produced that identical output. Without the mark the retry can
        never converge for a text whose correct English is the German,
        such as a title made only of station names.
      * If ``state`` or ``ident`` is missing, fall through to a
        cache-less translation via :func:`_translate_text_attempt`.

    ``source`` and ``category`` forward through to
    :func:`_translate_text_attempt` for metadata-aware glossary
    layering. The translation cache key remains ``(ident, field)`` —
    item metadata is implicit in ``ident`` (one disruption belongs to
    exactly one operator), so the cache layout does not need
    additional axes.
    """
    if not text:
        return text, True  # empty input is trivially "translated"
    if state is None or not ident:
        attempt = _attempt_for_field(field)(
            text, ident=ident, source=source, category=category,
        )
        return (attempt, True) if attempt is not None else (text, False)
    entry = state.setdefault(ident, {})
    translations_raw = entry.get("translations")
    if not isinstance(translations_raw, dict):
        translations_raw = {}
        entry["translations"] = translations_raw
    en_raw = translations_raw.get("en")
    if not isinstance(en_raw, dict):
        en_raw = {}
        translations_raw["en"] = en_raw
    cached = en_raw.get(field)
    # Source-drift guard. A cached translation is only valid for the exact
    # German text it was produced from; upstream rewords live disruptions
    # under an unchanged identity. A digest that does not match — or is
    # absent, i.e. the entry predates this guard — is a MISS, so the item
    # re-translates instead of serving English for a headline the German
    # feed no longer carries. See ``_SOURCE_DIGEST_KEY``.
    stored_digest = _source_digests(translations_raw).get(field)
    if (
        isinstance(cached, str)
        and cached
        and stored_digest is not None
        and stored_digest != _source_digest(text)
    ):
        log.info(
            "Source text changed for %s/%s — re-translating (cached EN was "
            "made from a different German source).",
            sanitize_log_arg(ident),
            sanitize_log_arg(field),
        )
        cached = None
    if isinstance(cached, str) and cached and cached != text:
        defect = _cached_translation_defect(text, cached)
        if defect is None and source == _OEBB_SOURCE:
            defect = _oebb_section_label_defect(text, cached) or _oebb_cannot_run_defect(text, cached)
        if defect is None:
            defect = _service_compound_defect(
                text, cached, source=source, category=category
            )
        if defect is None:
            # Cached before "due to" was the one cause preposition
            # (``_cause_due_to``); the model's words are otherwise kept.
            return _english_weekdays_capitalised(_cause_due_to(cached)), True
        # Self-heal: a value persisted by an earlier build is unfit to serve
        # (see ``_cached_translation_defect``). Treat the hit as a MISS and
        # re-translate below so the defect is never served from cache.
        log.info(
            "Cache self-heal: %s in EN %s for %s; retrying.",
            defect,
            sanitize_log_arg(field),
            sanitize_log_arg(ident),
        )
    verbatim = _verbatim_fields(translations_raw)
    if isinstance(cached, str) and cached == text:
        if field in verbatim and not _RESIDUAL_PLACEHOLDER_RE.search(cached):
            # Verified verbatim: the model DID run on this text and returned
            # it unchanged, because the correct English is the German. Serving
            # the cache here is what closes the loop below.
            return cached, True
        # Stale-fallback heuristic: a prior run cached the German
        # source as the "translation" — re-attempt now that the
        # pipeline may be healthy.
        log.info(
            "Cached EN translation for %s/%s equals source; retrying.",
            sanitize_log_arg(ident),
            sanitize_log_arg(field),
        )
    attempt = _attempt_for_field(field)(
        text, ident=ident, source=source, category=category,
    )
    if attempt is None:
        # Translation failed — do NOT persist the German source as
        # the "translation". The next run gets a clean retry.
        return text, False
    en_raw[field] = attempt
    _record_verbatim(translations_raw, field, identical=attempt == text)
    # Stamp the source this translation was made from, so a later reword of
    # the same disruption is detected as a miss rather than served stale.
    _record_source_digest(translations_raw, field, text)
    return attempt, True


def _invoke_collect_items(report: RunReport) -> list[FeedItem]:
    return _collect_items(report=report)


# --- One incident, one slot (operator decision 2026-09-26) -----------------
#
# WL sends one display-board ticker per consequence of an incident. On
# 2026-09-26 line 62 took three of the ten slots within 86 seconds::
#
#     15:10:21  62: ÖBB Bauarbeiten Betrieb ab Kliebergasse
#     15:10:51  62: Züge halte bei Linie 18, Richtung Burggasse
#     15:11:47  62: ÖBB Bauarbeiten Kein Betrieb
#
# Over 693 feed versions 242 carried such a group, 485 slots in all. The
# provider folds a ticker into a long message that already says it
# (``_fold_display_tickers``), and ``_short_title_collisions`` keeps equal
# short titles apart; tickers among themselves were never joined. Now WL
# disruptions of the same lines published within WL_TICKER_CLUSTER_SECONDS of
# the first one become one item: the most frequent cause in the title, every
# consequence in the description, in the order WL published them::
#
#     62: ÖBB Bauarbeiten
#     Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein Betrieb.
#
# Nothing a ticker says is dropped unless another one of the group, or the
# title, already says it; WL's stock sentence gives way to anything concrete.
# A long message that says more than the stock sentence stands, and its
# tickers take no slot of their own (2026-09-27). What tickers of its cause,
# or of none, announce beyond its text is kept (``_with_consequences``,
# 2026-10-01); a ticker of another cause stays out of it.
WL_TICKER_CLUSTER_SECONDS = 600

_WL_STOCK_SENTENCE_RE = re.compile(
    r"^Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen\.?$", re.IGNORECASE
)
_TICKER_ARROWS = " <>"  # a ticker line may end in the display's arrow
_MAX_CAUSE_WORDS = 3


class _TickerPart(NamedTuple):
    """What one WL disruption contributes to its group."""

    cause: str  # "" when it names none
    body: str  # the title without the line prefix, as WL wrote it
    consequence: str  # "" for a message that only names its cause
    extra: str  # text beyond the title (a long message's sentences)


def _ticker_prefix(title: str) -> str:
    """``62: ÖBB Bauarbeiten`` → ``62: `` (empty without a line prefix)."""
    match = _TITLE_BODY_RE.match(title)
    return title[: match.start(1)] if match else ""


def _ticker_text(item: FeedItem) -> str:
    """*item*'s description as one line, without the display's arrows.

    The arrows ("Hütteldorfer Straße > mit Linie 46") point, they say nothing.
    """
    return " ".join(
        word for word in html_to_text(str(item.get("description") or "")).split() if word not in ("<", ">")
    )


def _is_long_message(text: str) -> bool:
    """WL's long message (``stoerunglang``) is written in sentences.

    A display ticker is one or two board lines without a full stop. The
    ``Linie 48A:`` label cannot mark the long message: ``_post_filter_wl``
    strips it when the feed reads the cache, and some long messages never
    had it ("Die Linie U1 fährt derzeit …"). WL cache, September 2026: all
    504 texts in sentences end in a full stop, none of 247 tickers does.
    """
    return text.endswith(".")


def _ticker_part(item: FeedItem) -> _TickerPart:
    """Split one WL disruption into cause, consequence and further text."""
    title = _rendered_title(item)
    prefix = _ticker_prefix(title)
    body = title[len(prefix):].strip()
    text = _ticker_text(item)
    split = _reason_and_fragment(title)
    if _is_long_message(text):
        if split is not None:
            return _TickerPart(split[0][len(_ticker_prefix(split[0])):], body, split[1], text)
        return _TickerPart(body, body, "", text)
    if split is not None:
        return _TickerPart(split[0][len(_ticker_prefix(split[0])):], body, split[1], "")
    shown = text
    if shown.casefold().endswith(body.casefold()):
        # The display's first line is the cause when the title left it out:
        # "Gleisbauarbeiten / Betrieb ab Johnstraße U" under "12A: Betrieb ab Johnstraße U".
        lead = shown[: len(shown) - len(body)].strip(" -–:")
        words = lead.split()
        if 0 < len(words) <= _MAX_CAUSE_WORDS and lead[:1].isupper() and not any(ch.isdigit() for ch in lead):
            return _TickerPart(lead, body, body, "")
        return _TickerPart("", body, body, "")
    return _TickerPart("", body, body, text)


def _message_tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", text.casefold()))


def _distinct_messages(candidates: Sequence[str], title: str) -> list[str]:
    """*candidates* without what the title or a fuller candidate already says."""
    title_tokens = _message_tokens(title[len(_ticker_prefix(title)):])
    texts = [c.strip().rstrip(_TICKER_ARROWS).strip() for c in candidates]
    texts = [t for t in texts if t and not _message_tokens(t) <= title_tokens]
    kept: list[str] = []
    for index, text in enumerate(texts):
        tokens = _message_tokens(text)
        covered = any(
            tokens < _message_tokens(other) or (tokens == _message_tokens(other) and other_index < index)
            for other_index, other in enumerate(texts)
            if other_index != index
        )
        if not covered:
            kept.append(text)
    concrete = [t for t in kept if not _WL_STOCK_SENTENCE_RE.match(t)]
    return concrete or kept


def _shared_openings(messages: Sequence[str]) -> list[str]:
    """Consequences that open with the same two words name the opening once.

    ``Busse halten Bessemerstraße 1-3; Busse halten auf Hauptfahrbahn`` reads
    ``Busse halten Bessemerstraße 1-3, auf Hauptfahrbahn``. Until 2026-10-01
    ``deduplicate_fuzzy`` wrote that list when it joined two tickers first
    (``_collapse_common_prefix``); the WL merge joins them now. A
    consequence with a comma of its own ("Züge halten bei Li. 18 Richtung
    Burggasse, Stadthalle") stays apart: a list inside the list would read
    as one more stop.
    """
    openings: dict[str, int] = {}
    joined: list[str] = []
    for message in messages:
        words = message.split()
        opening = " ".join(words[:2]).casefold()
        if len(words) < 3 or "," in message or any(ch.isdigit() for ch in opening):
            joined.append(message)
            continue
        index = openings.get(opening)
        if index is None:
            openings[opening] = len(joined)
            joined.append(message)
        else:
            joined[index] = f"{joined[index]}, {' '.join(words[2:])}"
    return joined


def _without_cause(text: str, cause: str) -> str:
    """Drop the title's cause from the front of a consequence the split missed.

    "Stromstörung Züge fahren bis Johann-Nepomuk-Berger-Platz" under
    ``43: Stromstörung`` reads "Züge fahren bis …".
    """
    if cause and text.casefold().startswith(f"{cause} "):
        return text[len(cause):].strip(" –-:")
    return text


def _ticker_time(item: FeedItem) -> datetime | None:
    when = item.get("pubDate") or item.get("starts_at")
    return _to_utc(when) if isinstance(when, datetime) else None


def _ticker_lines(item: FeedItem) -> str:
    """``62: ÖBB Bauarbeiten`` → ``62`` (casefolded, empty without a line prefix)."""
    return _ticker_prefix(_rendered_title(item)).strip().rstrip(":").casefold()


def _publication_key(items: Sequence[FeedItem], index: int) -> tuple[datetime, int, str, int]:
    """WL's publication order: time, a long message before tickers of the same second, GUID."""
    item = items[index]
    when = _ticker_time(item) or datetime.min.replace(tzinfo=UTC)
    long_first = 0 if _is_long_message(_ticker_text(item)) else 1
    return when, long_first, str(item.get("guid") or ""), index


def _ticker_groups(items: Sequence[FeedItem]) -> list[list[int]]:
    """Indices of WL disruptions by incident: one line set, one publication window."""
    by_lines: dict[str, list[tuple[datetime, int, str, int]]] = defaultdict(list)
    for index, item in enumerate(items):
        if not _is_wl_ticker(item):
            continue
        prefix = _ticker_lines(item)
        if not prefix or _ticker_time(item) is None:
            continue
        by_lines[prefix].append(_publication_key(items, index))
    groups: list[list[int]] = []
    for members in by_lines.values():
        members.sort()
        start: datetime | None = None
        for when, _long_first, _guid, index in members:
            if start is None or (when - start).total_seconds() > WL_TICKER_CLUSTER_SECONDS:
                groups.append([])
                start = when
            groups[-1].append(index)
    return _join_twin_groups(items, groups)


# --- One ticker, several lines (2026-10-02) ----------------------------------
#
# WL sends the same display ticker once per line. On 2026-10-02 at 01:00:12
# "N65: Busse halten Laxenburger Straße 66" and "N66: Busse halten
# Laxenburger Straße 66" came in the same second; grouped by line, the stop
# stood in two of the ten slots, once as "N65: Busse halten Laxenburger
# Straße 66" and once inside "N66: Bauarbeiten". Two tickers of different
# lines with the same title body and the same text, published within
# WL_TICKER_CLUSTER_SECONDS, now join their groups: one entry under all
# their lines. Nothing is lost, the texts are equal; a consequence that only
# some of the lines announced keeps their label::
#
#     N65/N66: Bauarbeiten
#     Busse halten Laxenburger Straße 66; N66: Busse halten Salvatorianerplatz.
#
# A group with a long message joins only a group that says exactly the same,
# message for message (2026-10-03). WL sends its long message once per line
# too: "6: Schadhaftes Fahrzeug" and "18: Schadhaftes Fahrzeug", both
# "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."
# in the same minute, and four such messages for 16A, 17A, 67A and 67B
# ("Störung an einem Bahnübergang"). Replayed over the 3,875 German feed
# versions since 2026-07-15, such twins stood in 540 of them and took 808
# slots that other disruptions lacked; 9 are left. Where the groups differ
# in any message, the long message speaks for its own line set and the
# groups stay apart.


def _twin_key(item: FeedItem) -> str:
    """What two tickers of different lines must share to be one: body and text."""
    return f"{_ticker_part(item).body.casefold()}\n{_ticker_text(item).casefold()}"


def _ticker_line_tokens(item: FeedItem) -> list[str]:
    """``N65/N66: …`` → ``["N65", "N66"]``."""
    return [token.strip() for token in _ticker_prefix(_rendered_title(item)).strip().rstrip(":").split("/") if token.strip()]


def _join_twin_groups(items: Sequence[FeedItem], groups: list[list[int]]) -> list[list[int]]:
    """*groups* with those joined that share a ticker of another line (see above)."""
    owner = {index: number for number, group in enumerate(groups) for index in group}
    has_long = {number for number, group in enumerate(groups) if any(_is_long_message(_ticker_text(items[i])) for i in group)}
    parent = list(range(len(groups)))

    def root(number: int) -> int:
        while parent[number] != number:
            number = parent[number]
        return number

    by_key: dict[str, list[tuple[datetime, int, str, int]]] = defaultdict(list)
    for index, number in owner.items():
        if number not in has_long:
            by_key[_twin_key(items[index])].append(_publication_key(items, index))
    for number in has_long:
        # A group with a long message joins only a group that says exactly
        # the same, message for message (see below).
        signature = "\n\n".join(sorted({_twin_key(items[index]) for index in groups[number]}))
        by_key[f"long\n{signature}"].append(_publication_key(items, groups[number][0]))
    for twins in by_key.values():
        twins.sort()
        for (when, _l, _g, first), (later, _l2, _g2, second) in zip(twins, twins[1:], strict=False):
            if (later - when).total_seconds() <= WL_TICKER_CLUSTER_SECONDS and _ticker_lines(items[first]) != _ticker_lines(items[second]):
                parent[root(owner[second])] = root(owner[first])
    if all(parent[number] == number for number in range(len(groups))):
        return groups
    joined: dict[int, list[int]] = {}
    for number, group in enumerate(groups):
        joined.setdefault(root(number), []).extend(group)
    return [sorted(group, key=lambda index: _publication_key(items, index)) for group in joined.values()]


def _union_prefix(members: Sequence[FeedItem]) -> str | None:
    """``N65/N66: `` for members of several line sets, ``None`` for one set."""
    if len({_ticker_lines(member) for member in members}) < 2:
        return None
    tokens: dict[str, str] = {}
    for member in members:
        for token in _ticker_line_tokens(member):
            tokens.setdefault(token.casefold(), token)
    return "/".join(sorted(tokens.values(), key=line_order_key)) + ": "


def _group_lines_key(members: Sequence[FeedItem]) -> str:
    """The line set a group stands for: its union for joined twins (see above)."""
    union = _union_prefix(members)
    return union.rstrip(": ").casefold() if union else _ticker_lines(members[0])


def _twin_labels(members: Sequence[FeedItem], union: str | None) -> tuple[list[FeedItem], list[str]]:
    """*members* with each twin set once, and the line label each message keeps.

    A message all lines of the union announced needs no label; one only some
    of them announced is prefixed with those (``N66: ``).
    """
    if union is None:
        return list(members), [""] * len(members)
    all_lines = {token.casefold() for token in union.rstrip(": ").split("/")}
    shown: dict[str, tuple[FeedItem, dict[str, str]]] = {}
    for member in members:
        _item, lines = shown.setdefault(_twin_key(member), (member, {}))
        for token in _ticker_line_tokens(member):
            lines.setdefault(token.casefold(), token)
    kept = [item for item, _lines in shown.values()]
    labels = [
        "" if set(lines) == all_lines else "/".join(sorted(lines.values(), key=line_order_key)) + ": "
        for _item, lines in shown.values()
    ]
    return kept, labels


def _telling_long_message(members: Sequence[FeedItem]) -> FeedItem | None:
    """The first of *members* that is a long message saying more than the stock sentence."""
    for member in members:
        text = _ticker_text(member)
        if _is_long_message(text) and not _WL_STOCK_SENTENCE_RE.match(text):
            return member
    return None


# WL names one incident differently on the display and in the long message.
# Within one publication window, cache since September: "Schadhafter Zug"
# beside "Schadhaftes Fahrzeug" 14 times, "Fremdunfall" or "Verkehrsunfall"
# beside "Fremder Verkehrsunfall" 9, "Beschädigte Oberleitung" beside
# "Oberleitungsgebrechen" 6, "Schadhafter Bus" beside "Schadhaftes
# Fahrzeug" once, "Gasrohrbruch" beside "Gasrohrgebrechen" (2026-10-11).
# Only what WL wrote side by side; "Bauarbeiten" and
# "Gleisbauarbeiten" can be two sites and stay apart.
_CAUSE_SYNONYMS: dict[str, str] = {
    "schadhafter zug": "schadhaftes fahrzeug",
    "schadhafter bus": "schadhaftes fahrzeug",
    "fremdunfall": "fremder verkehrsunfall",
    "verkehrsunfall": "fremder verkehrsunfall",
    "beschädigte oberleitung": "oberleitungsgebrechen",
    "oberleitungsgebr": "oberleitungsgebrechen",
    "rettungseinatz": "rettungseinsatz",
    "gasrohrbruch": "gasrohrgebrechen",
    # "62: Beschädigte Oberleitung" beside "62: Fahrleitungsschaden Betrieb
    # ab Hofwiesengasse" four minutes later (2026-10-28).
    "fahrleitungsschaden": "oberleitungsgebrechen",
}


def _cause_key(cause: str) -> str:
    """*cause* for comparison: casefolded, one space, WL's synonyms resolved."""
    key = " ".join(cause.casefold().split())
    return _CAUSE_SYNONYMS.get(key, key)


def _chosen_cause(parts: Sequence[_TickerPart]) -> str:
    """The most frequent cause, the earliest on a tie; casefolded, "" for none."""
    counts: dict[str, int] = defaultdict(int)
    for part in parts:
        if part.cause:
            counts[part.cause.casefold()] += 1
    order = [part.cause.casefold() for part in parts if part.cause]
    return max(counts, key=lambda cause: (counts[cause], -order.index(cause)), default="")


def _span_group(merged: FeedItem, members: Sequence[FeedItem]) -> None:
    """Give *merged* the time span of its group.

    The start stays the lead's: WL can reuse an old ticker for a new
    incident, and its start must not spread to the group (25 on 2026-09-27:
    a breakdown that morning under "23.09.2026 – 27.09.2026").
    """
    ends = [e for e in (m.get("ends_at") for m in members) if isinstance(e, datetime)]
    # An open end of one member keeps the incident open.
    merged["ends_at"] = max(ends, key=_to_utc) if len(ends) == len(members) else None
    published = [p for p in (m.get("pubDate") for m in members) if isinstance(p, datetime)]
    incidents = [s for s in map(_wl_incident_start, members) if s is not None]
    if incidents:
        merged["_incident_start"] = min(incidents, key=_to_utc)
    if published:
        # A ticker WL rewrote for this incident keeps an old start
        # (``wl_plausibility.incident_begin``).
        merged["pubDate"] = incident_begin(min(published, key=_to_utc), _wl_incident_start(merged))


def _merged_ticker(members: Sequence[FeedItem]) -> FeedItem:
    """One item for the WL disruptions *members* (publication order, lead first)."""
    lead = members[0]
    merged = cast(FeedItem, dict(lead))
    _span_group(merged, members)
    long_message = _telling_long_message(members)
    if long_message is not None:
        # WL's own text stands. A ticker of another cause stays out: beside
        # the text it read as a second cause (48A on 2026-09-27: "Falschparker"
        # over "Grund: Fremder Verkehrsunfall"). Tickers of the same cause, or
        # of none, keep what the text does not say (``_with_consequences``):
        # "Betrieb ab Mühlbreiten" of 64A on 2026-10-01 stood nowhere else.
        cause = _cause_key(_ticker_part(long_message).cause)
        same = [m for m in members if m is not long_message and _cause_key(_ticker_part(m).cause) in ("", cause)]
        stands = _with_consequences(long_message, same) if same else long_message
        union = _union_prefix(members)
        title = str(stands.get("title") or "")
        # Twins of several lines (see ``_join_twin_groups``) stand under all of them.
        merged["title"] = f"{union}{title[len(_ticker_prefix(title)):]}" if union else title
        merged["description"] = str(stands.get("description") or "")
        return merged
    union = _union_prefix(members)
    shown, labels = _twin_labels(members, union)
    parts = [_ticker_part(member) for member in shown]
    chosen = _chosen_cause(parts)
    lead_title = _rendered_title(lead)
    prefix = union or _ticker_prefix(lead_title)
    if chosen:
        cause = next(part.cause for part in parts if part.cause.casefold() == chosen)
        title = f"{prefix}{cause}"
    else:
        title = f"{prefix}{lead_title[len(_ticker_prefix(lead_title)):]}"
    candidates: list[str] = []
    for part, label in zip(parts, labels, strict=True):
        if part.cause and _cause_key(part.cause) != _cause_key(chosen):
            candidates.append(label + part.body)  # WL's wording keeps the other cause with its consequence
        elif part.consequence:
            candidates.append(label + _without_cause(part.consequence, chosen))
        if part.extra:
            candidates.append(part.extra)
    messages = _shared_openings(_distinct_messages(candidates, title))
    merged["title"] = title
    # One sentence: the summary keeps at most two (``_SENTENCE_SPLIT_RE``), and
    # three short consequences would lose the third. The 180-character cap
    # still applies; the order is WL's.
    merged["description"] = "; ".join(m.rstrip(" .") for m in messages) + ("." if messages else "")
    return merged


def _group_span(members: Sequence[FeedItem]) -> tuple[datetime, datetime | None] | None:
    """Earliest start and latest end of *members* in UTC; an open end stays open."""
    starts = [_to_utc(s) for s in (m.get("starts_at") or m.get("pubDate") for m in members) if isinstance(s, datetime)]
    if not starts:
        return None
    ends = [e for e in (m.get("ends_at") for m in members) if isinstance(e, datetime)]
    return min(starts), (max(_to_utc(e) for e in ends) if len(ends) == len(members) else None)


def _spans_meet(a: tuple[datetime, datetime | None], b: tuple[datetime, datetime | None]) -> bool:
    """Whether two validity spans overlap; ``None`` is an open end."""
    return (b[1] is None or a[0] <= b[1]) and (a[1] is None or b[0] <= a[1])


# --- One line, one slot (operator decision 2026-10-01) ---------------------
#
# "Mehrere Störungsmeldungen zur selben Linie sollte so gut wie möglich
# zusammengefasst werden"; where different lines are affected, each keeps its
# entry. An incident above ends with its publication window. On 2026-10-01
# the four tickers of 18's "Gleisschaden" came at 16:25, 16:31, 16:33 and
# 16:37, and 66A stood twice for an afternoon: "Rettungseinsatz" over
# "Busse halten Salvatorianerplatz" (Bauarbeiten, 01.10.–02.10.). Over the
# 236 feed versions since 2026-09-27, 112 had a line in more than one entry.
#
# So after the windows, the WL disruptions of the same lines whose validity
# meets (``_line_runs``) become one entry; this also brings a long message
# and its tickers of another window together:
#
# * one cause, WL's synonyms counted (``_CAUSE_SYNONYMS``): one incident,
#   merged as above; a long message stands for it, and what the other
#   tickers announce beyond its text is kept (``_incident_entry``). A
#   disruption without a cause joins the incident published nearest to it;
# * several causes: one entry naming each, the newest first, under the GUID
#   of the newest, so a new incident still opens the feed::
#
#     66A: Rettungseinsatz, Bauarbeiten
#     Rettungseinsatz: Unregelmäßige Intervalle in beiden Richtungen. Bauarbeiten: Busse halten Salvatorianerplatz.
#
# A cause longer than _MAX_LISTED_WORDS words is a sentence, not a cause to
# list: 18's "Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus der
# Linie 18 aufgelassen. Bitte …", a disruption in the cache since July, keeps
# its own entry and joins no other.
_MAX_LISTED_WORDS = 6


def _group_cause(members: Sequence[FeedItem]) -> str:
    """The cause the entry of a group names, casefolded; "" for none."""
    long_message = _telling_long_message(members)
    if long_message is not None:
        return _ticker_part(long_message).cause.casefold()
    return _chosen_cause([_ticker_part(member) for member in members])


def _line_runs(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> list[list[list[int]]]:
    """*groups* of the same lines whose validity meets, transitively.

    A group with a sentence for a cause, or without a span, stands as a run
    of its own.
    """
    by_lines: dict[str, list[tuple[datetime, datetime | None, list[int]]]] = defaultdict(list)
    runs: list[list[list[int]]] = []
    for group in groups:
        members = [items[index] for index in group]
        span = _group_span(members)
        if span is None or len(_group_cause(members).split()) > _MAX_LISTED_WORDS:
            runs.append([group])
            continue
        by_lines[_group_lines_key(members)].append((span[0], span[1], group))
    for entries in by_lines.values():
        entries.sort(key=lambda entry: entry[0])
        run: list[list[int]] = []
        reach: datetime | None = None
        for start, end, group in entries:
            if run and (reach is None or start <= reach):
                run.append(group)
                reach = None if reach is None or end is None else max(reach, end)
                continue
            if run:
                runs.append(run)
            run, reach = [group], end
        runs.append(run)
    return runs


def _run_incidents(
    items: Sequence[FeedItem], run: Sequence[list[int]]
) -> tuple[list[list[list[int]]], list[list[int]]]:
    """The incidents of *run*, its groups by cause (WL's synonyms counted), and the groups of none.

    A group without a cause joins the incident published nearest to it, of
    those that had begun when it appeared: a display text cannot announce
    an incident that came later. Tickers that come a few minutes ahead of
    their incident (``wl_resolved.TICKER_LEAD``) share its group anyway
    (``WL_TICKER_CLUSTER_SECONDS``). One older than every incident belongs
    to none of them and comes back second (``_with_unowned``). Without any
    incident, the groups without a cause are the one incident.
    """
    incidents: dict[str, list[list[int]]] = {}
    causeless: list[list[int]] = []
    for group in run:
        cause = _cause_key(_group_cause([items[index] for index in group]))
        if cause:
            incidents.setdefault(cause, []).append(group)
        else:
            causeless.append(group)
    if not incidents:
        return ([causeless] if causeless else []), []
    alone: list[list[int]] = []
    for group in causeless:
        when = _publication_key(items, group[0])[0]
        begun = [
            groups
            for groups in incidents.values()
            if min(_publication_key(items, other[0])[0] for other in groups) <= when
        ]
        if not begun:
            alone.append(group)
            continue
        nearest = min(
            begun,
            key=lambda groups: min(abs((_publication_key(items, other[0])[0] - when).total_seconds()) for other in groups),
        )
        nearest.append(group)
    return list(incidents.values()), alone


# A WL long message names the measure first, then the advice, the expected
# end and the cause: "Betrieb nur zwischen Schottenring U und Stephansplatz U.
# Voraussichtliche Dauer: 14:45 Uhr. Grund: Polizeieinsatz …". The sentence
# split cannot see the end behind "U.", these openings can.
_LONG_MESSAGE_TAIL_RE = re.compile(r"\s+(?:Weichen Sie|Voraussichtliche Dauer|Grund:)")


# What a rider has to do, as WL's texts say it.
_RIDER_CONSEQUENCE_RE = re.compile(
    r"\b(?:Kein Betrieb|Betrieb (?:ab|nur|über|zwischen|bis)|Züge halten|Busse halten|hält|Umleitung|umgeleitet"
    r"|Ersatzbus|Shuttlebus|Einstieg|benützen|benutzen|ausweichen)\b",
    re.IGNORECASE,
)
# A negation says nothing a text in other words does not: "Kein Betrieb"
# beside "Derzeit ist ein Betrieb nicht möglich" (1A on 2026-09-22).
_NEGATIONS: frozenset[str] = frozenset({"kein", "keine", "keinen", "nicht"})


def _said_already(message: str, said: frozenset[str]) -> bool:
    """Whether every word of *message* that carries meaning is among *said*.

    Words of up to three letters carry none here: "Betrieb ab
    Hofwiesengasse" adds nothing to "Betrieb nur zwischen Westbahnhof S U
    und Hofwiesengasse" (60 on 2026-09-22). A longer or shorter form counts
    from five letters on: "Verspätung" is in "Verspätungen".
    """
    return all(
        token in said
        or any(len(token) >= 5 and len(word) >= 5 and (word.startswith(token) or token.startswith(word)) for word in said)
        for token in _message_tokens(message)
        if len(token) > 3 and token not in _NEGATIONS
    )


def _with_consequences(entry: FeedItem, members: Sequence[FeedItem]) -> FeedItem:
    """*entry* with what *members* announce and its own text does not.

    A long message stands for its incident, within its window and beyond;
    without this it would drop the tickers of the same cause, or of none,
    with their stops. A bare "Fahrtbehinderung" adds nothing. Where its
    measure says what a rider has to do, the tickers join its sentence
    (60 on 2026-09-22: "Betrieb nur zwischen Westbahnhof S U und
    Hofwiesengasse; Züge halten bei der Linie 62 Fahrtrichtung Lainz.
    Weichen Sie …"). Where it does not, they come first (26E from
    2026-09-25: "Busse halten Satzingerweg 41, …; Ersatzbus hält
    Karl-Waldbrunner-Platz …" before "Die Kapazitäten der Ersatzlinie
    26E …"). What tells a rider nothing to do comes last.
    """
    text = _ticker_text(entry)
    said = _message_tokens(f"{_rendered_title(entry)} {text}")
    cause = _ticker_part(entry).cause.casefold()
    # The cause glued in front ("Gasrohrgebrechen Shuttlebus eingerichtet, …")
    # and WL's display markup ("**Verspätung**") say nothing.
    candidates = [
        _without_cause(part.consequence or part.body, cause).replace("*", "").strip()
        for part in map(_ticker_part, members)
    ]
    messages = [
        m for m in _distinct_messages(candidates, _rendered_title(entry))
        if m.casefold() != _HINDRANCE.casefold() and not _said_already(m, said)
    ]
    if not messages:
        return entry
    rider = [m.rstrip(" .") for m in _shared_openings(messages) if _RIDER_CONSEQUENCE_RE.search(m)]
    rest = [m.rstrip(" .") for m in messages if not _RIDER_CONSEQUENCE_RE.search(m)]
    tail = _LONG_MESSAGE_TAIL_RE.search(text)
    cut = tail.start() if tail else len(text)
    measure = text[:cut].rstrip()
    if rider and _RIDER_CONSEQUENCE_RE.search(measure):
        # In the measure's own sentence, before the advice, expected end and
        # cause: the summary keeps two sentences, and "Wipplingerstr 39." ends
        # none it can see (D on 2026-09-30).
        text = f"{measure.rstrip('.')}; {_as_sentence('; '.join(rider))}{text[cut:]}"
    elif rider:
        text = f"{_as_sentence('; '.join(rider))} {text}"
    if rest:
        # What tells a rider nothing to do ("Derzeit längere Wartezeiten!")
        # must not push the text's own cause or end out of the summary.
        text = f"{text} {_as_sentence('; '.join(rest))}"
    merged = cast(FeedItem, dict(entry))
    merged["description"] = text
    return merged


def _incident_entry(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> tuple[int, FeedItem]:
    """The entry of one incident and the index it takes.

    A long message stands for its incident, as within its window; of
    several, the newest. What the groups without a long message announce
    beyond its text is kept (``_with_consequences``). Otherwise all
    messages of the incident are merged as one window would be.
    """
    telling = [group for group in groups if _telling_long_message([items[index] for index in group]) is not None]
    if not telling:
        members = sorted((index for group in groups for index in group), key=lambda i: _publication_key(items, i))
        if len(members) == 1:
            return members[0], items[members[0]]
        return members[0], _merged_ticker([items[index] for index in members])
    chosen = max(telling, key=lambda group: _publication_key(items, group[0]))
    entry = items[chosen[0]] if len(chosen) == 1 else _merged_ticker([items[index] for index in chosen])
    others = [items[index] for group in groups if group not in telling for index in group]
    return chosen[0], _with_consequences(entry, others) if others else entry


def _incident_start(entry: FeedItem) -> datetime:
    when = entry.get("starts_at") or entry.get("pubDate")
    return _to_utc(when) if isinstance(when, datetime) else datetime.min.replace(tzinfo=UTC)


def _listed_incident(entry: FeedItem) -> tuple[str, str]:
    """Label and detail of one incident in a combined entry.

    ``Demonstration`` and ``Betrieb nur zwischen St. Marx S und Landstraße S
    U; Betrieb ab Landstraße`` — the measure a long message opens with,
    without the advice, the expected end and the "Grund: …" the label
    already says (``_LONG_MESSAGE_TAIL_RE``), then the consequence the
    display names where it adds to that ("Busse halten bei der Linie 14A");
    a bare "Fahrtbehinderung" beside a measure adds nothing. "" when
    nothing is left.
    """
    part = _ticker_part(entry)
    label = part.cause or part.body
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(_LONG_MESSAGE_TAIL_RE.split(part.extra, maxsplit=1)[0])]
    reason = f"grund: {label.casefold()}"
    measure = next((s for s in sentences if s and s.rstrip(" .").casefold() != reason), "").rstrip(" .")
    consequence = part.consequence.strip().rstrip(" .")
    if consequence and _WL_STOCK_SENTENCE_RE.match(f"{measure}."):
        measure = ""  # WL's stock sentence gives way to anything concrete
    details = [measure] if measure else []
    if consequence and not (
        measure and (consequence.casefold() == _HINDRANCE.casefold() or _message_tokens(consequence) <= _message_tokens(measure))
    ):
        details.append(consequence)
    return label, "; ".join(d for d in details if d.casefold() != label.casefold())


def _incident_sentences(listed: Sequence[tuple[str, str]]) -> list[str]:
    """One sentence per incident that says more than its label, within 180 characters.

    The summary keeps two sentences and 180 characters. An incident that
    only has its label (or WL's stock sentence beside something concrete)
    is named by the title and takes no sentence; over the budget, the
    longest detail gives up its last consequence first (62 on 2026-09-28:
    "ÖBB Bauarbeiten: Züge halten bei Linie 18, Richtung Burggasse; Kein
    Betrieb" beside "Oberleitungsgebrechen: …"). A label whose agent the
    title leaves out (:func:`_title_cause`) says it here, with or without
    a detail: "Beschädigte Oberleitung durch einen LKW."
    """
    concrete = any(detail and not _WL_STOCK_SENTENCE_RE.match(f"{detail}.") for _, detail in listed)
    said = [
        (label, detail if detail and not (concrete and _WL_STOCK_SENTENCE_RE.match(f"{detail}.")) else "")
        for label, detail in listed
    ]
    kept = [(label, detail.split("; ") if detail else []) for label, detail in said if detail or _title_cause(label) != label]
    if not kept:
        return [_as_sentence(label) for label, _ in listed]

    def sentence(label: str, parts: Sequence[str]) -> str:
        return _as_sentence(f"{label}: {'; '.join(parts)}" if parts else label)

    shared = {"; ".join(parts).casefold() for _, parts in kept if parts}
    if len(kept) > 1 and len(shared) == 1 and all(parts or _title_cause(label) != label for label, parts in kept):
        # Two incidents with one consequence say it once: "Fahrtbehinderung."
        # under "71: Polizeieinsatz, Rettungseinsatz" (2026-10-29), not once
        # per cause; an agent the title leaves out says it with it.
        agents = [label for label, _ in kept if _title_cause(label) != label]
        parts = next(parts for _, parts in kept if parts)
        return [sentence(" und ".join(agents), parts) if agents else _as_sentence("; ".join(parts))]

    while len(" ".join(sentence(label, parts) for label, parts in kept)) > 180:
        longest = max(kept, key=lambda entry: len("; ".join(entry[1])))
        if len(longest[1]) < 2:
            break
        longest[1].pop()
    return [sentence(label, parts) for label, parts in kept]


# Who caused it stands in the text, not in the title (operator decision
# 2026-11-02): "27: Stromstörung, Signalstörung, Beschädigte Oberleitung"
# over "Beschädigte Oberleitung durch einen LKW." (2026-10-27). WL's causes
# since July with such an agent: "Beschädigte Oberleitung durch einen LKW",
# "Fahrleitungsgebrechen durch LKW"; a place ("PKW im Gleis", "Störung an
# einem Bahnübergang") is part of the cause and stays.
_CAUSE_AGENT_RE = re.compile(r"^(?P<cause>[A-ZÄÖÜ].*?)\s+durch\s+\S.*$")


def _title_cause(label: str) -> str:
    """*label* as a combined title names it: without the agent behind "durch"."""
    match = _CAUSE_AGENT_RE.match(label)
    return match["cause"] if match else label


def _combined_incidents(entries: Sequence[FeedItem]) -> FeedItem:
    """One entry for different incidents of the same lines, newest first (see above)."""
    lead = entries[0]
    merged = cast(FeedItem, dict(lead))
    listed = [_listed_incident(entry) for entry in entries]
    merged["title"] = _ticker_prefix(_rendered_title(lead)) + ", ".join(_title_cause(label) for label, _ in listed)
    merged["description"] = " ".join(_incident_sentences(listed))
    ends = [e for e in (entry.get("ends_at") for entry in entries) if isinstance(e, datetime)]
    merged["ends_at"] = max(ends, key=_to_utc) if len(ends) == len(entries) else None
    merged["_wl_incidents"] = len(entries)
    return merged


def _with_members(entry: FeedItem, items: Sequence[FeedItem], groups: Sequence[list[int]]) -> FeedItem:
    """*entry* with the GUIDs of the messages of *groups* as ``_members``."""
    marked = cast(FeedItem, dict(entry))
    marked["_members"] = sorted({guid for group in groups for index in group for guid in member_guids(items[index])})
    return marked


def _merged_run(items: Sequence[FeedItem], run: Sequence[list[int]]) -> tuple[int, FeedItem]:
    """The entry for *run* and the index it takes.

    Its ``_members`` are the messages of the incident whose GUID it carries
    (see ``_carry_item_identity``): of several incidents, the newest. What
    belongs to no incident comes last and is no member (``_with_unowned``).
    """
    found, unowned = _run_incidents(items, run)
    incidents = [
        (place, _with_members(entry, items, groups))
        for groups in found
        for place, entry in (_incident_entry(items, groups),)
    ]
    if len(incidents) == 1:
        place, entry = incidents[0]
    else:
        ordered = sorted(
            incidents,
            key=lambda incident: (_incident_start(incident[1]), str(incident[1].get("guid") or "")),
            reverse=True,
        )
        place, entry = ordered[0][0], _combined_incidents([entry for _, entry in ordered])
    if unowned:
        entry = _with_unowned(entry, [items[index] for group in unowned for index in group])
    return place, entry


def _with_unowned(entry: FeedItem, members: Sequence[FeedItem]) -> FeedItem:
    """*entry* with what *members*, display texts older than its incidents, say, at the end.

    WL repeats the stops of planned works on the displays for weeks
    without a cause ("25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor
    Schloßhofer Straße", "Züge halten Donaufelder Straße 175-177", in the
    data since 22.09.2026). On 2026-10-09 at 09:36 they joined "25:
    Verspätungen" (Grund: Schadhaftes Fahrzeug) as its consequences: the
    feed read the works' stops in front of the delay and lost its cause,
    and when the delay ended, the stops kept its GUID and begin and stood
    as a new disruption from 12:01 to 20:30, on up to place 2. The line
    still takes one slot (operator decision 2026-10-01), but the stops
    follow the incident's own text, after its cause, and stay out of
    ``_members``: when the incident ends, the item falls back to their own
    GUID and begin.
    """
    text = _ticker_text(entry)
    said = _message_tokens(f"{_rendered_title(entry)} {text}")
    candidates = [(part.consequence or part.body).replace("*", "").strip() for part in map(_ticker_part, members)]
    messages = [
        m.rstrip(" .")
        for m in _distinct_messages(candidates, _rendered_title(entry))
        if m.casefold() != _HINDRANCE.casefold() and not _said_already(m, said)
    ]
    if not messages:
        return entry
    merged = cast(FeedItem, dict(entry))
    merged["description"] = f"{text} {_as_sentence('; '.join(messages))}" if text else _as_sentence("; ".join(messages))
    return merged


# --- Two causes in one publication window (2026-11-02) -----------------------
#
# A publication window held every WL disruption of its lines, whatever its
# cause, and ``_merged_ticker`` named only the most frequent cause, the
# earliest on a tie. On 2026-10-29 from 18:31 to 18:59 Vienna time WL had two
# incidents on the 71 nine minutes apart (I20261029-0032 and -0034), and the
# feed read::
#
#     71: Rettungseinsatz
#     Fahrtbehinderung wegen Polizeieinsatz.
#
# The second cause stood in the text only, as if it were the first one's
# consequence ("42: Falschparker" over "Fahrtbehinderung wegen
# Rettungseinsatz." the same evening). Two incidents ten minutes apart were
# already listed side by side ("U3: Polizeieinsatz, Rettungseinsatz",
# ``_combined_incidents``); within one window they were not.
#
# Now a window without a long message of its own (``_telling_long_message``)
# is split by cause, WL's synonyms counted (``_CAUSE_SYNONYMS``) and a cause
# with WL's advice behind it counted as the cause it opens with
# (``_window_cause``), before the
# line runs; each cause is an incident of the run, and the entry names each,
# the newest first. A ticker without a cause stays with the cause the window
# named so far. Where a cause is a sentence (``_MAX_LISTED_WORDS``) the
# window stays whole: it would take a slot of its own. Operator decision
# 2026-11-02 ("Titel nennt beide Ursachen").
def _split_by_cause(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> list[list[int]]:
    """*groups* with each window of several causes split into one group per cause (see above)."""
    split: list[list[int]] = []
    for group in groups:
        members = [items[index] for index in group]
        parts = [_ticker_part(member) for member in members]
        keys = {_cause_key(part.cause) for part in parts if part.cause}
        causes = list(dict.fromkeys(_window_cause(part.cause, keys) for part in parts if part.cause))
        if (
            len(causes) < 2
            or _telling_long_message(members) is not None
            or any(len(cause.split()) > _MAX_LISTED_WORDS for cause in causes)
        ):
            split.append(group)
            continue
        chosen = _window_cause(_chosen_cause(parts), keys)
        by_cause: dict[str, list[int]] = {cause: [] for cause in causes}
        for index, part in zip(group, parts, strict=True):
            by_cause[_window_cause(part.cause, keys) if part.cause else chosen].append(index)
        split.extend(by_cause.values())
    return split


def _window_cause(cause: str, keys: set[str]) -> str:
    """*cause*'s key, or that of another cause of its window it opens with.

    "Gleisschaden - Linie 11 benutzen" is the "Gleisschaden" of the ticker
    beside it (O on 2026-10-24): WL's display wrote the advice behind the
    cause, and the title read "O: Gleisschaden - Linie 11 benutzen, Gleisschaden".
    """
    words = cause.casefold().split()
    for end in range(1, len(words)):
        key = _cause_key(" ".join(words[:end]))
        if key in keys:
            return key
    return _cause_key(cause)


def _merge_wl_ticker_clusters(items: list[FeedItem]) -> list[FeedItem]:
    """Join the WL disruptions of one incident, and of one line, into one item (see above)."""
    groups = _split_by_cause(items, _ticker_groups(items))
    replaced: dict[int, FeedItem] = {}
    dropped: set[int] = set()
    for run in _line_runs(items, groups):
        members = [index for group in run for index in group]
        if len(members) < 2:
            continue
        place, entry = _merged_run(items, run)
        replaced[place] = entry
        dropped.update(index for index in members if index != place)
        log.info(
            "WL-Kurzmeldungen zusammengelegt: %s ← %s",
            sanitize_log_arg(entry["title"]),
            sanitize_log_arg(" | ".join(_rendered_title(items[index]) for index in sorted(members))),
        )
    if dropped:
        items = [replaced.get(index, item) for index, item in enumerate(items) if index not in dropped]
    return _join_part_line_tickers(items)


# --- Tickers of some lines of a longer incident (2026-10-15) -----------------
#
# The merge above joins WL disruptions of the same line set. WL writes one
# long message for all lines of an incident but a display ticker per line,
# so these never met. On 2026-10-11 from 11:30 to 23:30 Vienna time a gas
# leak in Brünner Straße took four of the ten slots::
#
#     36B/30A/31A: Gasrohrgebrechen   Die Linie 36B wird … umgeleitet.
#     30A: Gasrohrbruch               Betrieb ab Gschweidlgasse.
#     36B: Gasrohrbruch               Betrieb ab Brünner Straße.
#     31A: Gasrohrbruch               Kein Betrieb.
#
# The provider folds a ticker into a long message only when the message
# repeats every word of the ticker (``wl_fetch._fold_display_tickers``);
# the display says "Gasrohrbruch" where the message says "Gasrohrgebrechen".
# "37/38/40/41/42: Schadhaftes Fahrzeug" stood beside "38/42: Schadhafter
# Zug" and "37: Schadhafter Zug" the same day.
#
# Now a WL disruption that is display tickers only joins the entry of a WL
# incident (one with a WL number) of more lines when it is that incident's
# ticker: its lines are among the incident's, it names the same cause, WL's
# synonyms counted (``_CAUSE_SYNONYMS``), it began at most
# ``wl_resolved.TICKER_LEAD`` before the incident and their validity meets.
# Of several such incidents the one begun last owns it, as in
# ``wl_resolved``. A ticker of another cause keeps its slot ("43:
# Falschparker" beside "43/44: Rettungseinsatz", 2026-10-09).
#
# A line the incident's text speaks of ("Die Linie 31A wird … umgeleitet",
# or the measure in front of the first label, whose "Linie 2:" the reading
# stripped: ``_stripped_label_line``) has WL's own statement there; the
# ticker of one stop ("Kein Betrieb" at the Brünner Straße display, while
# the 31A runs a detour) adds nothing to it and would contradict it on the
# screen. What a ticker of a line the text does
# not name announces is kept under its line ("37: Betrieb ab …").
_TICKER_LEAD = timedelta(minutes=10)  # ``wl_resolved.TICKER_LEAD``


def _has_wl_number(item: FeedItem) -> bool:
    """Whether *item* stands for a WL message with an incident number (not tickers only)."""
    return any(member.startswith("wl:") for member in member_guids(item))


def _stripped_label_line(text: str, lines: Sequence[str]) -> str | None:
    """The line whose "Linie X:" ``_post_filter_wl`` stripped from the start of *text*, if any.

    WL's "Linie 2: Umleitung … Linie 10: Betrieb nur …" reaches the merge as
    "Umleitung … Linie 10: Betrieb nur …": the text speaks of the 2 without
    naming it (2026-10-19, "2/10: Fremder Verkehrsunfall"). A text begins
    with that line's measure when one line is unnamed and what stands in
    front of the first label is a measure the short form knows
    (:func:`_short_measure`); a cause sentence there is no line's.
    """
    unnamed = [line for line in lines if not _text_names_line(text, line)]
    marker = _SHORT_LINE_MARKER_RE.search(text)
    if len(unnamed) != 1 or marker is None or marker.start() == 0:
        return None
    lead = text[: marker.start()].strip()
    return unnamed[0] if _short_measure(lead) is not None else None


def _ticker_owner(ticker: FeedItem, incidents: Sequence[tuple[int, FeedItem]]) -> int | None:
    """The index of the incident entry whose ticker *ticker* is, if any (see above)."""
    lines = {token.casefold() for token in _ticker_line_tokens(ticker)}
    cause = _cause_key(_ticker_part(ticker).cause)
    began, span = _ticker_time(ticker), _group_span([ticker])
    if not lines or not cause or began is None or span is None:
        return None
    fitting: list[tuple[datetime, int]] = []
    for index, entry in incidents:
        start, entry_span = _ticker_time(entry), _group_span([entry])
        if start is None or entry_span is None or began < start - _TICKER_LEAD:
            continue
        if not lines < {token.casefold() for token in _ticker_line_tokens(entry)}:
            continue
        if _cause_key(_ticker_part(entry).cause) != cause or not _spans_meet(span, entry_span):
            continue
        fitting.append((start, index))
    return max(fitting)[1] if fitting else None


def _with_line_tickers(entry: FeedItem, tickers: Sequence[FeedItem]) -> FeedItem:
    """*entry* with its *tickers* joined (see above)."""
    text = _ticker_text(entry)
    said = _message_tokens(f"{_rendered_title(entry)} {text}")
    cause = _ticker_part(entry).cause.casefold()
    stripped = _stripped_label_line(text, _ticker_line_tokens(entry))
    added: list[str] = []
    for ticker in tickers:
        lines = _ticker_line_tokens(ticker)
        if all(line == stripped or _text_names_line(text, line) for line in lines):
            continue
        part = _ticker_part(ticker)
        message = _without_cause(part.consequence or part.extra or part.body, cause).replace("*", "").strip(" .")
        if not message or message.casefold() == _HINDRANCE.casefold() or _said_already(message, said):
            continue
        added.append(f"{'/'.join(lines)}: {message}")
    merged = cast(FeedItem, dict(entry))
    merged["_members"] = sorted({guid for item in (entry, *tickers) for guid in member_guids(item)})
    ends = [e for e in (item.get("ends_at") for item in (entry, *tickers)) if isinstance(e, datetime)]
    merged["ends_at"] = max(ends, key=_to_utc) if len(ends) == 1 + len(tickers) else None
    if not added:
        return merged
    # Placed as in ``_with_consequences``: behind the measure, in front of
    # the advice, the expected end and the cause; in front of a text that
    # names no measure (WL's stock sentence); what tells a rider nothing to
    # do at the end.
    rider = [m for m in added if _RIDER_CONSEQUENCE_RE.search(m)]
    rest = [m for m in added if not _RIDER_CONSEQUENCE_RE.search(m)]
    tail = _LONG_MESSAGE_TAIL_RE.search(text)
    cut = tail.start() if tail else len(text)
    measure = text[:cut].rstrip()
    if rider and _RIDER_CONSEQUENCE_RE.search(measure):
        text = f"{measure} {_as_sentence('; '.join(rider))}{text[cut:]}"
    elif rider:
        text = f"{_as_sentence('; '.join(rider))} {text}"
    if rest:
        text = f"{text} {_as_sentence('; '.join(rest))}"
    merged["description"] = text
    return merged


def _join_part_line_tickers(items: list[FeedItem]) -> list[FeedItem]:
    """Join display tickers of some lines into their incident of more lines (see above)."""
    incidents = [(index, item) for index, item in enumerate(items) if _is_wl_ticker(item) and _has_wl_number(item)]
    if not incidents:
        return items
    owned: dict[int, list[FeedItem]] = defaultdict(list)
    dropped: set[int] = set()
    for index, item in enumerate(items):
        if not _is_wl_ticker(item) or _has_wl_number(item):
            continue
        owner = _ticker_owner(item, incidents)
        if owner is None:
            continue
        owned[owner].append(item)
        dropped.add(index)
    if not dropped:
        return items
    out: list[FeedItem] = []
    for index, item in enumerate(items):
        if index in dropped:
            continue
        if index in owned:
            item = _with_line_tickers(item, owned[index])
            log.info(
                "WL-Kurzmeldungen in die Meldung mehrerer Linien aufgenommen: %s ← %s",
                sanitize_log_arg(_rendered_title(item)),
                sanitize_log_arg(" | ".join(_rendered_title(ticker) for ticker in owned[index])),
            )
        out.append(item)
    return out


# --- Display tickers of planned works (2026-10-02) --------------------------
#
# WL announces planned works as a notice (``Hinweis``) and shows them on the
# displays as tickers ("Gleisbauarbeiten / Betrieb ab Johnstraße U"). On
# 2026-10-01 the fuzzy merge took the long message "D: Gleisbauarbeiten"
# into the notice "D: Gleisbauarbeiten Althanstraße"; the four tickers were
# left to the WL merge and stood beside the notice as "D: Gleisbauarbeiten
# [Am 01.10.2026]" for works from 28.09. to 07.11. "12A", "25", "N20",
# "N49", "37A" and "74A" did the same, in each of the 68 WL cache versions
# of 30.09.–02.10. WL republishes the tickers every night with a new
# identity, so they took a front slot every day; "D: Gleisbauarbeiten"
# stood in 53 of 94 German feed versions, its notice in none of them.
#
# Running the WL merge before the fuzzy merge removed them too, but took a
# rescue operation on D (02.10. 05:31, joined with the tickers into "D:
# Rettungseinsatz, Gleisbauarbeiten") into the notice as well: "D:
# Gleisbauarbeiten Althanstraße & Rettungseinsatz, Gleisbauarbeiten". So
# only one incident's entry goes up in a notice, and only when the notice
# names its cause, covers its lines, spans its validity and names a street
# of it. The street keeps two sites of one cause apart: "66A: Busse halten
# Salvatorianerplatz" (Bauarbeiten, 02.10.) is not the notice "65A/66A:
# Inzersdorfer Straße # Leibnizgasse" (Bauarbeiten too).
_WORKS_SPAN_SLACK = timedelta(days=1)
_HEADING_RE = re.compile(r"^\s*<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_STREET_SUFFIXES = "straße|str|gasse|platz|weg|allee|ring|kai|brücke|zeile|markt|steig|gürtel|lände"
_STREET_RE = re.compile(
    rf"\b[A-ZÄÖÜ][\w\-]*(?i:{_STREET_SUFFIXES})\b"
    rf"|\b[A-ZÄÖÜ][\w\-]*er(?=\s+(?i:{_STREET_SUFFIXES})\b)",
    re.UNICODE,
)


def _streets(text: str) -> set[str]:
    """The street names in *text*, casefolded ("Schweglerstraße", "Donaufelder" of "Donaufelder Straße")."""
    return {match.group(0).casefold() for match in _STREET_RE.finditer(text)}


def _notice_names_cause(notice: FeedItem, cause: str) -> bool:
    """Whether the title or the ``<h2>`` heading of *notice* names *cause* (a ``_cause_key``)."""
    title = _rendered_title(notice)
    body = title[len(_ticker_prefix(title)):]
    heading = _HEADING_RE.match(str(notice.get("description") or ""))
    for text in (body, html_to_text(heading.group(1)) if heading else ""):
        if f" {cause} " in f" {_cause_key(text)} ":
            return True
    return False


def _shares_a_street(item: FeedItem, notice: FeedItem) -> bool:
    named = _streets(f"{_rendered_title(item)} {_ticker_text(item)}")
    return bool(named & _streets(f"{_rendered_title(notice)} {html_to_text(str(notice.get('description') or ''))}"))


def _within(inner: FeedItem, outer: FeedItem) -> bool:
    """Whether the validity of *inner* lies in that of *outer*, give or take a day."""
    span, works = _group_span([inner]), _group_span([outer])
    if span is None or works is None:
        return False
    if span[0] < works[0] - _WORKS_SPAN_SLACK:
        return False
    if works[1] is None:
        return True
    return span[1] is not None and span[1] <= works[1] + _WORKS_SPAN_SLACK


# --- Stop tickers of a relocation (2026-10-29) -------------------------------
#
# WL shows a relocated stop on the displays as well: "40A: Busse halten
# Liechtensteinstraße 39" and "… 38", every day from 27.10.2026, beside the
# notice "40A: Bauernfeldplatz" that moves the stop there ("Nach:
# Liechtensteinstraße 38", "Nach: Liechtensteinstraße 39"). A relocation's
# title names the stop, not the cause, and the board said "Bauarbeiten" where
# the notice says "Kabelarbeiten", so the rule above never matched: the German
# feed showed the same new stop twice in about 100 versions of 27.–29.10.
# (Jules audits 28. and 29.10. took both as correct). The WL cache since July
# holds six such pairs (13A, 12A/14A, 7A/N65/N66, 3A, N20, 40A). A ticker that
# says nothing but "Busse/Züge halten <Adresse>", every address one the notice
# moves the stop to, goes up in the notice while it is in force — as long as
# the notice's own short text still shows each address. "40A: Bauernfeldplatz"
# loses "Liechtensteinstraße 39" behind the 180 cut ("… gegenüber
# Bauernfeldplatz → …"); there the ticker is the only place the new stop is
# read, and it keeps its slot.
_STOP_TICKER_RE = re.compile(r"(?:Busse|Züge|Straßenbahnen) halten (?P<places>.+)")
_RELOCATION_TARGET_RE = re.compile(r"^Nach:\s*(?P<place>.+?)\s*$", re.MULTILINE)


def _same_place(shown: str, target: str) -> bool:
    """Whether the board's *shown* address is the notice's *target* ("Kärntner Ring 5" of "Kärntner Ring 5, Nebenfahrbahn")."""
    shown, target = " ".join(shown.casefold().split()), " ".join(target.casefold().split())
    return target == shown or (target.startswith(shown) and target[len(shown)] in " ,(")


def _relocated_stops_shown(item: FeedItem, notice: FeedItem) -> bool:
    """Whether the WL ticker *item* only shows the stops *notice* relocates."""
    targets = _RELOCATION_TARGET_RE.findall(html_to_text(str(notice.get("description") or "")))
    if not targets:
        return False
    part = _ticker_part(item)
    text = (part.extra or part.consequence).rstrip(".").strip()
    text = _without_cause(text, part.cause)
    messages = [m.strip() for m in text.split(";") if m.strip()]
    if not messages:
        return False
    shown_places: list[str] = []
    for message in messages:
        match = _STOP_TICKER_RE.fullmatch(message)
        if match is None:
            return False
        places = [p.strip() for p in match["places"].split(",") if p.strip()]
        if not places or not all(any(_same_place(p, t) for t in targets) for p in places):
            return False
        shown_places.extend(places)
    shown = _format_item_content(
        notice,
        str(notice.get("guid") or ""),
        _parse_datetime(notice.get("starts_at")),
        _parse_datetime(notice.get("ends_at")),
        lang="de",
    ).desc_text_truncated.casefold()
    return all(
        re.search(rf"(?<![\w-]){re.escape(place.casefold())}(?![\w-])", shown) for place in shown_places
    )


def _works_shown(item: FeedItem, notices: Sequence[FeedItem]) -> FeedItem | None:
    """The notice whose planned works or relocated stops the WL disruption *item* only shows, if any."""
    if not _is_wl_ticker(item):
        return None
    cause = _cause_key(_ticker_part(item).cause)
    lines = set(_ticker_lines(item).split("/")) - {""}
    if not lines:
        return None
    for notice in notices:
        if not lines <= set(_ticker_lines(notice).split("/")) or not _within(item, notice):
            continue
        if _notice_reported_live(notice, [item]) is not None:
            # A live incident the notice gives way to (``_yield_notices_to_incidents``).
            continue
        if cause and _notice_names_cause(notice, cause) and _shares_a_street(item, notice):
            return notice
        if _relocated_stops_shown(item, notice):
            return notice
    return None


def _drop_baustellen_twins(items: list[FeedItem], now: datetime) -> list[FeedItem]:
    """Drop a Stadt Wien Baustelle that a running WL notice already reports.

    The city's roadworks list says what changes for transit without naming
    the line ("Die Haltestelle des betroffenen öffentlichen Verkehrsmittels
    wird von Burggasse ONr.67 nach Burggasse ONr. 69 verlegt"); Wiener Linien
    publish the same measure with line, direction and reason ("48A:
    Neubaugasse, Burggasse … Nach: Burggasse 69"). Since May 2026 nearly
    every site that passed the transit rule had such a WL twin, and twice
    both stood among the ten displayed items for days ("Inzersdorfer Straße
    Kreuzung Leibnizgasse" next to "65A/66A/7A: Inzersdorfer Straße #
    Leibnizgasse", 06.–13.08.; "Floridsdorfer Brücke …" next to the line 31,
    09.–13.07.). Operator decision 2026-10-09: the WL notice is enough.

    A twin is a WL ``Hinweis`` (a planned measure, not a live incident) that
    is in force now and either gives the site's street as the place of its
    cause ("Wegen Bauarbeiten im Bereich Neilreichgasse …",
    :func:`src.providers.baustellen.names_site_street`) or names an address
    the site's text names too ("Nach: Burggasse 69",
    :func:`src.providers.baustellen.shares_address`). A street that only
    appears in a detour or a stop list does not make a twin. A site without
    one stays as before.
    """
    now_utc = _to_utc(now)
    notices: list[str] = []
    for it in items:
        if str(it.get("source") or "").strip().casefold() != "wiener linien":
            continue
        if str(it.get("category") or "").strip().casefold() != "hinweis":
            continue
        start = _parse_datetime(it.get("starts_at"))
        if isinstance(start, datetime) and _to_utc(start) > now_utc:
            continue
        notices.append(
            f"{it.get('title') or ''} {html_to_text(str(it.get('description') or ''))}"
        )
    if not notices:
        return items
    out: list[FeedItem] = []
    for it in items:
        if _is_baustelle(it):
            street = site_street(str(it.get("title") or ""))
            site_text = f"{it.get('title') or ''}. {it.get('description') or ''}"
            twin = next(
                (
                    text
                    for text in notices
                    if (street and names_site_street(text, street))
                    or shares_address(site_text, text)
                ),
                None,
            )
            if twin is not None:
                log.info(
                    "Baustelle %s verworfen: WL meldet dieselbe Maßnahme (%s).",
                    sanitize_log_arg(str(it.get("title") or "")),
                    sanitize_log_arg(twin[:80]),
                )
                continue
        out.append(it)
    return out


def _absorb_works_tickers(items: list[FeedItem]) -> list[FeedItem]:
    """Drop the WL disruptions that only show a notice's planned works or relocated stops (see above)."""
    notices = [item for item in items if _is_wl_notice(item)]
    if not notices:
        return items
    kept: list[FeedItem] = []
    for item in items:
        works = _works_shown(item, notices)
        if works is None:
            kept.append(item)
            continue
        log.info(
            "WL-Kurzmeldung in den Hinweis aufgenommen: %s → %s",
            sanitize_log_arg(_rendered_title(item)),
            sanitize_log_arg(_rendered_title(works)),
        )
    return kept


# --- A notice gives way to the live incident that reports it (2026-11-01) -----
#
# WL announces a planned event as a notice ("1/2/71/D: Nationalfeiertag",
# <h2> "Veranstaltung", Ringstraße, in the feed since 16.10.) and, when the
# day comes, opens a live incident for it as well ("1/2/71/D: Veranstaltung",
# I20261026-0004, "Grund: Veranstaltung im Bereich Ringstraße."). Both said
# the same diversions; on 26.10.2026 they stood side by side from 09:01 to
# 17:31 Vienna time on two of the ten places (the KI watcher's finding of
# 01.11.; on 25.10. the pair of that day shared one title, and the fuzzy
# merge already showed it once). ``_absorb_works_tickers`` does not take
# the incident: a live incident has no end, so it never lies within the
# notice (``_within``). The incident is the better of the two for the viewer:
# it carries WL's expected end ("Voraussichtlich bis 18:00 Uhr") and leaves
# the feed the moment WL closes it. So the notice gives way while a running
# WL incident (a long message with WL's incident start) names its cause (in
# the notice's title or its <h2>), shares a street with it, began no earlier
# than a day before the notice and covers every line of the notice. An
# incident on only some of the notice's lines ("4A/74A: Demonstration"
# beside the notice of 16 lines on 22.10.) says nothing about the others,
# and both stay. Only a notice of one operating day gives way: it must end
# by 04:00 after the incident's Vienna date (``_service_day_end``). A notice
# of weeks ("1: Gleisbauarbeiten", 21.10. to 05.12.) tells what holds after
# a short incident of the same cause is over, and it stays. The same holds
# when WL gives the incident an end: ``_absorb_works_tickers`` no longer
# takes such an incident into a notice of one day. On 11.10.2026 it took
# "18: Laufveranstaltung" (I20261011-0007, "bis 23:55") into "18:
# LCC-Herbstmarathon am 11.10.2026" from 09:01 to 14:30 Vienna time, the
# opposite of the 26.10. rule only because WL named an end. When the
# incident ends, the notice is back as before. The replay of 555 feed
# states since 01.10. and of every state of 11.10. and 26.10. changes two
# days only: 11.10. 09:01 to 14:30 and 26.10. 09:01 to 17:31 Vienna time.
# Operator decision 2026-11-01 ("Störung bleibt"): of the two, the live
# incident stays. His general rule of the same day: "Störungen haben höchste
# Priorität im Feed! Hinweise sind weniger wichtig."


# WL's operating day ends in the night: night works end "04:00" of the next
# date ("1: Gleisbauarbeiten" … 05.12.2026 04:00).
_SERVICE_DAY_END = timedelta(hours=4)


def _service_day_end(moment: datetime) -> datetime:
    """The end (UTC) of the WL operating day of the Vienna date of *moment*."""
    local = _to_utc(moment).astimezone(_VIENNA_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(), tzinfo=_VIENNA_TZ)
    return _to_utc(midnight + _SERVICE_DAY_END)


def _notice_reported_live(notice: FeedItem, items: Sequence[FeedItem]) -> FeedItem | None:
    """The running WL incident among *items* that reports *notice*'s measures, if any (see above)."""
    lines = set(_ticker_lines(notice).split("/")) - {""}
    if not lines:
        return None
    start = _parse_datetime(notice.get("starts_at"))
    end = _parse_datetime(notice.get("ends_at"))
    if not isinstance(end, datetime):
        return None
    for item in items:
        if not _is_wl_ticker(item):
            continue
        begin = _wl_incident_start(item)
        if begin is None:
            continue
        if isinstance(start, datetime) and _to_utc(begin) < _to_utc(start) - _WORKS_SPAN_SLACK:
            continue
        if _to_utc(end) > _service_day_end(begin):
            continue
        if not lines <= set(_ticker_lines(item).split("/")):
            continue
        cause = _cause_key(_ticker_part(item).cause)
        if cause and _notice_names_cause(notice, cause) and _shares_a_street(item, notice):
            return item
    return None


def _yield_notices_to_incidents(items: list[FeedItem], now: datetime) -> list[FeedItem]:
    """Drop a WL notice in force while a running WL incident reports the same measures (see above)."""
    now_utc = _to_utc(now)
    kept: list[FeedItem] = []
    for item in items:
        if _is_wl_notice(item):
            start = _parse_datetime(item.get("starts_at"))
            if not isinstance(start, datetime) or _to_utc(start) <= now_utc:
                incident = _notice_reported_live(item, items)
                if incident is not None:
                    log.info(
                        "WL-Hinweis tritt zurück, solange die Störung läuft: %s → %s",
                        sanitize_log_arg(_rendered_title(item)),
                        sanitize_log_arg(_rendered_title(incident)),
                    )
                    continue
        kept.append(item)
    return kept


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


def _evict_stale_translations(
    ident: str, state: dict[str, dict[str, Any]] | None
) -> None:
    """Drop cached EN translations that predate the current
    :data:`_TRANSLATION_CACHE_EPOCH`.

    Called at the top of :func:`_apply_lang_overlay` before any cache
    lookup. When the persisted epoch is older than the current one the
    item was translated by a build with weaker masking / glossary
    logic; the whole ``en`` sub-dict is removed so every field is
    recomputed through the improved pipeline. The epoch is re-stamped
    only AFTER a successful retranslation (see
    :func:`_stamp_translation_epoch`) so a transient pipeline failure
    cannot lock in a half-empty cache at the current epoch.
    """
    if state is None or not ident:
        return
    entry = state.get(ident)
    if not isinstance(entry, dict):
        return
    translations = entry.get("translations")
    if not isinstance(translations, dict):
        return
    epoch_raw = translations.get("epoch", 0)
    epoch = epoch_raw if isinstance(epoch_raw, int) else 0
    if epoch < _TRANSLATION_CACHE_EPOCH:
        if "en" in translations:
            log.info(
                "Evicting stale EN translation for %s (epoch %s < %s).",
                sanitize_log_arg(ident),
                epoch,
                _TRANSLATION_CACHE_EPOCH,
            )
        translations.pop("en", None)
        # The verbatim marks belong to the evicted translations: they assert
        # what THIS epoch's pipeline produced. A newer epoch with a better
        # glossary may translate the same text after all, so the marks go
        # with the strings rather than outliving them.
        translations.pop(_VERBATIM_FIELDS_KEY, None)
        # Same reasoning for the source fingerprints: they validate the
        # strings that were just dropped. Leaving them behind would pair a
        # matching digest with no translation — harmless today (the lookup
        # needs both) but exactly the kind of half-state that invites a
        # later "the digest matches, trust it" shortcut.
        translations.pop(_SOURCE_DIGEST_KEY, None)


def _stamp_translation_epoch(
    ident: str, state: dict[str, dict[str, Any]] | None
) -> None:
    """Record that ``ident``'s cached translations were produced under
    the current :data:`_TRANSLATION_CACHE_EPOCH`.

    Called only after every field translated successfully, so a future
    build trusts the cache instead of evicting it again (no churn even
    for items whose translation legitimately drops a placeholder).
    """
    if state is None or not ident:
        return
    entry = state.setdefault(ident, {})
    translations = entry.setdefault("translations", {})
    if isinstance(translations, dict):
        translations["epoch"] = _TRANSLATION_CACHE_EPOCH


def _apply_lang_overlay(
    base: FormattedContent,
    summary_de: str,
    time_line_de: str,
    ident: str,
    lang: str,
    state: dict[str, dict[str, Any]] | None,
    *,
    source: str | None = None,
    category: str | None = None,
) -> FormattedContent:
    """Translate the German formatted output into English, if requested.

    Cache lookups go through :func:`_cached_translation` so each
    disruption identity is translated exactly once per lifecycle (the
    EN strings are persisted in ``state[ident]["translations"]["en"]``
    and round-trip through :func:`_save_state`).

    **Per-item atomic fallback contract**: the EN feed promises the
    same content as the DE feed, only in another language. To honour
    that promise an item must be either fully translated (title +
    summary + time-line) OR a verbatim copy of the German source — a
    mixed-language item or a "Partially translated" marker would mean
    the EN subscriber sees information the DE subscriber does not,
    which violates the content-parity contract requested by the
    operator. When ``_cached_translation`` reports a failure on
    either field the function returns ``base`` unchanged so the EN
    feed item is byte-identical to the DE item for that disruption.

    For ``lang != "en"`` the input is returned unchanged.

    ``source`` / ``category`` (normalised
    :data:`FeedItem` fields) drive metadata-aware glossary layering
    inside the translation cascade. Default ``None`` keeps the
    function callable from the existing tests that do not need to
    exercise the metadata path.

    Cache freshness: cached translations are tagged with the
    :data:`_TRANSLATION_CACHE_EPOCH` they were produced under. Stale
    entries (older epoch) are evicted up-front so a masking / glossary
    improvement is picked up on the next build instead of serving the
    old rendering for the lifetime of the item.
    """
    if lang != "en":
        return base

    # Pick up masking / glossary improvements: discard cached
    # translations produced under an older epoch so the lookups below
    # recompute them. Re-stamped at the end only when every field
    # translated successfully.
    _evict_stale_translations(ident, state)

    title_raw, title_ok = _cached_translation(
        base.title_out, "title", ident, state,
        source=source, category=category,
    )
    if summary_de:
        summary_raw, summary_ok = _cached_translation(
            summary_de, "summary", ident, state,
            source=source, category=category,
        )
    else:
        summary_raw = ""
        summary_ok = True

    if not (title_ok and summary_ok):
        log.info(
            "Translation incomplete for identity %s "
            "(title_ok=%s, summary_ok=%s) — EN feed item falls back to "
            "the German source verbatim to preserve DE↔EN content parity.",
            sanitize_log_arg(ident or "<unknown>"),
            title_ok,
            summary_ok,
        )
        return base

    # Every field translated under the current epoch — stamp it so the
    # next build trusts the cache instead of evicting and recomputing.
    _stamp_translation_epoch(ident, state)

    # Mirror the DE length contract (see ``_format_item_content`` lines
    # ~3826-3839): German→English expansion (compound nouns split into
    # several words) can push a translated title past ``TITLE_CHAR_LIMIT``
    # or a summary past the 180-char TV-screen cap. The DE strings fed into
    # translation were already capped, so re-apply both caps to the
    # translated output to keep the EN feed within the same contract.
    title_en = _sanitize_text(title_raw)
    if len(title_en) > feed_config.TITLE_CHAR_LIMIT:
        title_en = title_en[: feed_config.TITLE_CHAR_LIMIT].rstrip() + " …"
    title_en = _WHITESPACE_RE.sub(" ", title_en).strip()
    # Applied after the cap and the whitespace collapse so it acts on the
    # string that actually ships, not on an intermediate one.
    title_en = _capitalise_glossary_after_stop(
        _capitalise_title_body(_drop_unpaired_quote(title_en))
    )
    summary_en = _capitalise_glossary_after_stop(
        _capitalise_sentence_start(
            _drop_unpaired_quote(_truncate_summary_180(_sanitize_text(summary_raw)))
        )
    )
    time_line_en = _translate_time_line_en(time_line_de)
    desc_text_truncated_en, desc_html_en = _compose_description(
        summary_en, time_line_en
    )
    return FormattedContent(
        guid=base.guid,
        link=base.link,
        title_cdata=_cdata_content(title_en),
        desc_text_truncated=desc_text_truncated_en,
        desc_cdata=_cdata_content(desc_html_en),
        raw_desc=base.raw_desc,
        title_out=title_en,
        desc_html=desc_html_en,
    )


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


# GeoRSS (operator decision 2026-10-16, "Eine präzise Variante würde das
# Projekt aufwerten. Falsche Daten bringen uns hingegen nichts."): an item
# carries a ``<georss:point>`` or ``<georss:line>`` only where its place is
# certain, and most items carry none, by design.
#
# Measured over every notice title since 2026-07-01 (reports/
# georss-einschaetzung-2026-10-16.md): taking every station a text names, in
# text order, draws wrong shapes for Wiener Linien — 15 % of the names follow
# "Richtung" (the line's terminus, not the place), 26 % of the lines zigzag
# through detours and replacement lines, street names hit stops named after
# them ("Simmeringer Hauptstraße" is the stop Hauptwerkstätte). So:
#
# * ÖBB: the title only. One station → point; exactly one "A ↔ B" → a line
#   from A to B (a straight segment between the two stations, never a
#   computed midpoint). A title with several sections ("A ↔ B / C ↔ D"), a
#   chain of three or more, or any part that is not exactly a station name
#   gets nothing.
# * Stadt Wien Baustellen: the site's own coordinate from the city's data
#   (the first point of its geometry), only inside Vienna.
# * Wiener Linien: only a stop relocation or closure ("15A: Max-Mauermann-
#   Gasse" with "Haltestellenverlegung …"), whose title names exactly one stop
#   that the directory lists for one of the title's lines. Every other WL
#   notice — the large majority — gets nothing.
#
# Coordinates come only from open datasets with known terms (ÖBB-Infrastruktur
# GeoNetz under CC BY 3.0 AT, Wiener Linien OGD and Stadt Wien OGD under
# CC BY 4.0), never from the
# directory's merged OSM / HAFAS values
# (:func:`src.utils.stations.open_data_coordinate`). A station without an
# open coordinate leaves the item without geo.

_OEBB_TITLE_LINES_RE = re.compile(
    r"^[A-Z]{1,4}\s?\d{1,3}[A-Z]?(?:\s*/\s*[A-Z]{1,4}\s?\d{1,3}[A-Z]?)*:\s+"
)
_WL_STOP_TITLE_RE = re.compile(r"^([A-Za-z0-9]+(?:/[A-Za-z0-9]+)*):\s*(\S.*)$")

_Coord = tuple[float, float]


def _oebb_title_geometry(title: str) -> tuple[_Coord, ...] | None:
    from .utils.stations import open_data_coordinate, station_named_in_text

    rest = _OEBB_TITLE_LINES_RE.sub("", title.strip(), count=1)
    if not rest or "/" in rest or ":" in rest:
        return None
    parts = [part.strip() for part in rest.split("↔")]
    if len(parts) > 2 or not all(parts):
        return None
    names: list[str] = []
    for part in parts:
        info = station_named_in_text(part)
        if info is None:
            return None
        if info.name not in names:
            names.append(info.name)
    coords: list[_Coord] = []
    for name in names:
        hit = open_data_coordinate(name)
        if hit is None:
            return None
        coords.append((hit.latitude, hit.longitude))
    return tuple(coords)


def _wl_stop_notice_geometry(item: FeedItem) -> tuple[_Coord, ...] | None:
    from .utils.stations import open_data_coordinate, station_named_in_text, station_wl_lines

    if not STOP_NOTICE_LEAD_RE.match(html_to_text(str(item.get("description") or ""))[:200]):
        return None
    match = _WL_STOP_TITLE_RE.match(str(item.get("title") or "").strip())
    if match is None:
        return None
    info = station_named_in_text(match.group(2).strip())
    if info is None:
        return None
    if not set(match.group(1).split("/")) & station_wl_lines(info.name):
        return None
    hit = open_data_coordinate(info.name, prefer="wl")
    return ((hit.latitude, hit.longitude),) if hit else None


def _baustelle_geometry(item: FeedItem) -> tuple[_Coord, ...] | None:
    from .utils.stations import is_in_vienna

    location = cast(dict[str, Any], item).get("location")
    coordinates = location.get("coordinates") if isinstance(location, dict) else None
    if not isinstance(coordinates, dict):
        return None
    lat, lon = coordinates.get("lat"), coordinates.get("lon")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    if not is_in_vienna(lat, lon):
        return None
    return ((float(lat), float(lon)),)


def _item_geometry(item: FeedItem) -> tuple[_Coord, ...] | None:
    """The certain place of ``item`` as one point or a two-point line, or ``None``.

    See the GeoRSS block comment above for the rules per source.
    """
    source = str(item.get("source") or "").strip()
    if source == "ÖBB":
        return _oebb_title_geometry(str(item.get("title") or ""))
    if source == "Wiener Linien":
        return _wl_stop_notice_geometry(item)
    if _is_baustelle(item):
        return _baustelle_geometry(item)
    return None


def _georss_coords(coords: tuple[_Coord, ...]) -> str:
    return " ".join(f"{lat:.6f} {lon:.6f}" for lat, lon in coords)


def _emit_item(
    it: FeedItem,
    now: datetime,
    state: dict[str, dict[str, Any]],
    *,
    lang: str = "de",
    split_reason: bool = True,
    drop_section: bool = True,
) -> tuple[str, ET.Element, dict[str, str]]:
    """Convert a normalized item dictionary into an RSS <item> element and CDATA replacements.

    Args:
        it: The normalized item dictionary.
        now: The current datetime (used for relative time calculations).
        state: The state dictionary (used to persist first_seen timestamps).
        lang: Target output language (``"de"`` or ``"en"``). When ``"en"``
            the formatter applies a translation overlay (cached in
            ``state[ident]["translations"]["en"]``).
        split_reason: Whether a WL ticker title may shrink to its cause
            (see :func:`_finish_reason_title`); ``False`` for an item whose
            short title another visible item already has.
        drop_section: Whether a long Baustellen title may move its section
            into the description (see :func:`_baustellen_display`); ``False``
            when another visible item would then read the same.

    Returns:
        A tuple containing:
         - The item identity (str)
         - The generated ElementTree.Element
         - A dictionary mapping placeholder strings to their CDATA-wrapped content.
    """
    it_dict = cast(dict[str, Any], it)
    pubDate = _coerce_datetime_field(it_dict, "pubDate")
    starts_at = _coerce_datetime_field(it_dict, "starts_at")
    ends_at = _coerce_datetime_field(it_dict, "ends_at")

    ident, fs_dt = _update_item_state(it, now, state)

    formatted = _format_item_content(
        it,
        ident,
        starts_at if isinstance(starts_at, datetime) else None,
        ends_at if isinstance(ends_at, datetime) else None,
        lang=lang,
        state=state,
        split_reason=split_reason,
        drop_section=drop_section,
    )

    if not isinstance(pubDate, datetime) and feed_config.FRESH_PUBDATE_WINDOW_MIN > 0:
        age = _to_utc(now) - _to_utc(fs_dt)
        if age <= timedelta(minutes=feed_config.FRESH_PUBDATE_WINDOW_MIN):
            pubDate = now

    # Generate unique placeholders.
    # We use a cryptographically secure random token to ensure uniqueness within the document.
    # ``_placeholder_collides_with_formatted`` verifies the candidate
    # placeholders do not appear in ANY of the eight text-bearing
    # ``FormattedContent`` fields — closing the upstream-controlled
    # collision gap on ``link`` / ``guid`` / ``desc_text_truncated`` /
    # ``title_cdata`` / ``desc_cdata`` that the pre-fix inline check
    # missed (only ``desc_html`` / ``raw_desc`` / ``title_out`` were
    # examined).
    max_attempts = 100
    attempts = 0
    while True:
        if attempts >= max_attempts:
            raise RuntimeError("Konnte keinen eindeutigen Platzhalter generieren")
        uid = secrets.token_hex(16)
        PH_CONTENT = f"___CDATA_CONTENT_{uid}___"
        PH_TITLE = f"___CDATA_TITLE_{uid}___"
        if not _placeholder_collides_with_formatted(PH_CONTENT, PH_TITLE, formatted):
            break
        attempts += 1

    # --- ElementTree Construction ---
    item = ET.Element("item")

    # Title
    ET.SubElement(item, "title").text = PH_TITLE

    # Link
    ET.SubElement(item, "link").text = formatted.link

    # GUID
    guid_elem = ET.SubElement(item, "guid")
    guid_elem.text = formatted.guid

    # guid attributes (isPermaLink)
    parsed = urlparse(formatted.guid)
    if not (parsed.scheme and parsed.netloc and formatted.guid == formatted.link):
        guid_elem.set("isPermaLink", "false")

    # pubDate
    if isinstance(pubDate, datetime):
        ET.SubElement(item, "pubDate").text = _fmt_rfc2822(pubDate)

    # Source tag (see ``_ITEM_SOURCES``): the name of the source, with its
    # page in the ``url`` attribute RSS 2.0 requires.
    item_source = _item_source(it_dict)
    if item_source is not None:
        source_elem = ET.SubElement(item, "source")
        source_elem.text = item_source.name_en if lang == "en" else item_source.name_de
        source_elem.set("url", item_source.url)

    # One plain image for every item, so TV slides stay uniformly grey.
    _add_item_image(item)

    # Extensions
    ET.SubElement(item, "{https://wien-oepnv.example/schema}first_seen").text = _fmt_rfc2822(fs_dt)

    if isinstance(starts_at, datetime):
        ET.SubElement(item, "{https://wien-oepnv.example/schema}starts_at").text = _fmt_rfc2822(starts_at)

    shown_end = _shown_end(it, ends_at)
    if shown_end is not None:
        ET.SubElement(item, "{https://wien-oepnv.example/schema}ends_at").text = _fmt_rfc2822(shown_end)

    # GeoRSS: one point or a line where the place is certain, else nothing
    # (see :func:`_item_geometry`). Language-independent, so DE and EN agree.
    geometry = _item_geometry(it)
    if geometry:
        tag = "point" if len(geometry) == 1 else "line"
        ET.SubElement(item, f"{{{GEORSS_NS}}}{tag}").text = _georss_coords(geometry)

    # Description
    # Security (stored HTML/JS injection on the public feed — ``<description>``
    # sibling of the ``<content:encoded>`` output-encoding fix): ``<description>``
    # is an XML TEXT node, so ElementTree escapes ``<>&`` for XML *well-formedness*
    # on serialise. That alone is NOT enough — a conformant RSS reader XML-decodes
    # the node exactly ONCE and the overwhelming majority then render the result
    # as HTML (RSS 2.0 ``<description>`` is HTML by convention; ``content:encoded``
    # was added only to carry the *full* body). ``desc_text_truncated`` carries
    # plain display text in which an upstream ``&lt;img onerror=…&gt;`` has already
    # been decoded by ``html_to_text`` into a live ``<img onerror=…>``; without
    # output-encoding here that tag would execute in the subscriber's reader after
    # its single XML-decode. Escape the markup characters at this sink so the
    # reader's lone XML-decode yields inert ``&lt;img…&gt;`` *source* text. This
    # is the single per-item ``<description>`` sink for both the DE and EN feeds
    # (``_emit_item`` is invoked once per language with the language-resolved
    # ``formatted``). ``&`` is deliberately NOT escaped here — see
    # :func:`_escape_description_markup` for why that is complete and why
    # escaping it was a display bug.
    ET.SubElement(item, "description").text = _escape_description_markup(
        formatted.desc_text_truncated
    )

    # content:encoded
    ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded").text = PH_CONTENT

    replacements = {
        PH_CONTENT: f"<![CDATA[{formatted.desc_cdata}]]>",
        PH_TITLE: f"<![CDATA[{formatted.title_cdata}]]>",
    }

    return ident, item, replacements


# CC BY 4.0, section 3(a)(1): the attribution names the licence with its URI
# and says that the material was modified. The feed shortens and reformats the
# notice texts (titles, descriptions) and the English feed machine-translates
# them, so both channel lines say so and link the licence and NOTICE.md in
# full (a feed reader has no repository to resolve a bare file name against).
_CC_BY_4_URL = "https://creativecommons.org/licenses/by/4.0/"
# The ÖBB-Infrastruktur GeoNetz on data.oebb.at is CC BY 3.0 AT, not 4.0, and
# asks for the attribution "Datenquelle: ÖBB-Infrastruktur AG" with the name
# linked to infrastruktur.oebb.at (dataset page, read 17.10.2026). A feed
# line cannot carry a hyperlink, so the URL follows the name.
_CC_BY_3_AT_URL = "https://creativecommons.org/licenses/by/3.0/at/deed.de"
_OEBB_INFRA_URL = "https://infrastruktur.oebb.at/de/"
_NOTICE_URL = "https://github.com/Origamihase/wien-oepnv/blob/main/NOTICE.md"

# VAO "Start" contract, point 5.3: name the VAO "Start" access as the basis
# of the Stammstrecke data (see the comment above ``_CHANNEL_COPYRIGHT_DE``).
_VAO_BASIS_DE = (
    "Stammstrecken-Daten: Grundlage ist der VAO-„Start“-Zugang der "
    "Verkehrsauskunft Österreich VAO GmbH."
)
_VAO_BASIS_EN = (
    "S-Bahn trunk-line data: based on the VAO “Start” access of "
    "Verkehrsauskunft Österreich VAO GmbH."
)

# Michael, 17.10.2026 (licence audit): the channel says first that the project is unofficial and that
# the linked original notices are authoritative, so nobody reads the
# shortened or translated texts as an operator's own announcement. Channel
# level only; titles and texts on the EasySignage displays stay unchanged.
_UNOFFICIAL_DE = (
    "Inoffizielles Projekt ohne Verbindung zu Wiener Linien, ÖBB, Stadt Wien "
    "und VOR/VAO; maßgeblich sind die verlinkten Originalmeldungen."
)
_UNOFFICIAL_EN = (
    "Unofficial project, not affiliated with Wiener Linien, ÖBB, the City of "
    "Vienna or VOR/VAO; the linked original notices are authoritative."
)

# Channel-level metadata for the English feed mirror (docs/feed.en.xml).
# Kept as a module-level constant so the EN strings are reviewable in one
# place and never reach the upstream-controlled translation pipeline —
# channel metadata is build-time-owned, not item-derived.
_CHANNEL_METADATA_EN: dict[str, str] = {
    "title": "Vienna Public Transport — Disruptions & Commuter Info",
    "description": (
        "Active disruptions, construction works and restrictions from "
        "official sources (Wiener Linien, ÖBB, VOR/VAO, City of Vienna)."
    ),
    "language": "en",
    "copyright": (
        f"{_UNOFFICIAL_EN} "
        "Code: MIT licence. Notice texts remain with their sources and are "
        "shortened, edited and machine-translated for this feed: "
        "Wiener Linien and City of Vienna – data.wien.gv.at (CC BY 4.0, "
        f"{_CC_BY_4_URL}), ÖBB (© ÖBB, not openly licensed). GeoRSS coordinates: "
        f"Datenquelle: ÖBB-Infrastruktur AG ({_OEBB_INFRA_URL}; GeoNetz, CC BY 3.0 AT, "
        f"{_CC_BY_3_AT_URL}), Wiener Linien (OGD stops) and City of Vienna – "
        f"data.wien.gv.at (CC BY 4.0). {_VAO_BASIS_EN} Details: {_NOTICE_URL}"
    ),
}

# Michael, 16.10.2026: the code stays MIT, the notice texts are not ours to
# license. The channel names the sources and their terms once; titles and
# texts stay untouched, so nothing changes on the EasySignage displays (each
# item names its source in ``<source>`` since 17.10.2026, ``_ITEM_SOURCES``).
# VAO is named as the basis of the Stammstrecke data, not as a licensor:
# the Stammstrecke delay notice (``src.feed.stammstrecke``, ``<source>``
# VOR/VAO) is our own text computed from VAO departure data. The VAO "Start"
# contract (point 5.3) requires the service to state unmistakably that the
# VAO "Start" access is its basis; Michael chose on 17.10.2026 to carry that
# sentence in both feeds, the site footer, docs/statistik.md, README and
# NOTICE.md (docs/architecture.md §7). Should the contract end, point 7.7
# requires removing it again.
# The GeoRSS coordinates (:func:`_item_geometry`) come only from the three
# open datasets ``open_data_coordinate`` and the Baustellen data accept
# (ÖBB-Infrastruktur GeoNetz, Wiener Linien OGD stops, Stadt Wien), so the
# line names exactly those; never OSM, HAFAS or Google.
_CHANNEL_COPYRIGHT_DE = (
    f"{_UNOFFICIAL_DE} "
    "Code: MIT-Lizenz. Die Meldungstexte bleiben bei ihren Quellen und sind "
    "für den Feed gekürzt und bearbeitet: "
    "Wiener Linien und Stadt Wien – data.wien.gv.at (CC BY 4.0, "
    f"{_CC_BY_4_URL}), ÖBB (© ÖBB, keine offene Lizenz). GeoRSS-Koordinaten: "
    f"Datenquelle: ÖBB-Infrastruktur AG ({_OEBB_INFRA_URL}; GeoNetz, CC BY 3.0 AT, "
    f"{_CC_BY_3_AT_URL}), Wiener Linien (OGD-Haltestellen) und Stadt Wien – "
    f"data.wien.gv.at (CC BY 4.0). {_VAO_BASIS_DE} Details: {_NOTICE_URL}"
)


def _channel_metadata(lang: str) -> dict[str, str]:
    """Resolve channel-level metadata for ``lang`` (``"de"`` or ``"en"``)."""
    if lang == "en":
        return _CHANNEL_METADATA_EN
    return {
        "title": feed_config.FEED_TITLE,
        "description": feed_config.FEED_DESC,
        "language": "de",
        "copyright": _CHANNEL_COPYRIGHT_DE,
    }


def _make_rss(
    items: list[FeedItem],
    now: datetime,
    state: dict[str, dict[str, Any]],
    *,
    lang: str = "de",
) -> str:
    """
    Generate the full RSS XML document from a list of items using ElementTree.

    Args:
        items: List of item dictionaries.
        now: Current timestamp.
        state: State dictionary for tracking items.
        lang: Target language for the output (``"de"`` or ``"en"``).
            Drives channel metadata, ``<language>``, the atom self
            ``href`` (``feed.xml`` vs ``feed.en.xml``) and the per-item
            translation overlay forwarded to :func:`_emit_item`.

    Returns:
        The generated RSS XML string with CDATA sections.
    """
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    metadata = _channel_metadata(lang)
    feed_filename = "feed.en.xml" if lang == "en" else "feed.xml"

    # Security: route the env-controlled FEED_TITLE / FEED_DESC through
    # the canonical ``_sanitize_text`` (``_CONTROL_RE`` strip — C0/C1
    # controls + DEL + BiDi format controls + zero-width chars + line
    # separators + BOM) before they land in the channel-level RSS XML.
    # Pre-fix the env vars flowed verbatim into ``<title>`` / ``<description>``,
    # letting a leaked CI env / compromised secret store / intentional
    # misconfig plant CVE-2021-42574 Trojan-Source primitives (RLO, LSEP,
    # ZWSP, …) into the published ``docs/feed.xml`` channel metadata —
    # the prominent "feed name" displayed by every subscriber's RSS
    # reader. Mirrors the per-item sinks already routed through the
    # helper (title / description / time-line / guid) so the closing-
    # checklist of the *Trojan-Source RSS Drift* family covers BOTH
    # per-item AND channel-level RSS sinks. ``FEED_LINK`` is already
    # pinned by ``validate_public_feed_url`` (HTTPS-only + GitHub host
    # allow-list + ``_UNSAFE_URL_CHARS`` strip) so its emission needs
    # no additional sanitisation. The EN strings come from the
    # module-level constant ``_CHANNEL_METADATA_EN`` and are still routed
    # through the sanitiser for defense-in-depth uniformity.
    ET.SubElement(channel, "title").text = _sanitize_text(metadata["title"])
    ET.SubElement(channel, "link").text = feed_config.FEED_LINK
    ET.SubElement(channel, "description").text = _sanitize_text(metadata["description"])

    # Atom self/alternate-Links + Sprache. Diese drei Tags wurden früher
    # vom Perl-basierten "Normalize feed metadata (SEO)"-Step in
    # .github/workflows/build-feed.yml nachträglich injiziert. Generierung
    # direkt im Python-Builder hält das XML strukturell wohlgeformt und
    # entfernt den Sprachen-Mix in CI.
    pages_base = feed_config.PAGES_BASE_URL.rstrip("/")
    atom_alternate = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_alternate.set("rel", "alternate")
    atom_alternate.set("type", "text/html")
    atom_alternate.set("href", f"{pages_base}/")
    atom_self = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_self.set("rel", "self")
    atom_self.set("type", "application/rss+xml")
    atom_self.set("href", f"{pages_base}/{feed_filename}")
    ET.SubElement(channel, "language").text = metadata["language"]
    ET.SubElement(channel, "copyright").text = metadata["copyright"]

    ET.SubElement(channel, "lastBuildDate").text = _fmt_rfc2822(now)
    ET.SubElement(channel, "ttl").text = str(feed_config.FEED_TTL)

    item_replacements: dict[str, str] = {}
    shown = items[: max(feed_config.MAX_ITEMS, 0)]
    # Decided on the visible items together, from the German titles, so DE
    # and EN shorten the same items.
    keep_long = _short_title_collisions(shown)
    keep_section = _section_collisions(shown)
    for index, it in enumerate(shown):
        _ident, elem, repl = _emit_item(
            it,
            now,
            state,
            lang=lang,
            split_reason=index not in keep_long,
            drop_section=index not in keep_section,
        )
        channel.append(elem)
        item_replacements.update(repl)

    # Pretty print the tree
    if hasattr(ET, "indent"):
        ET.indent(rss, space="  ", level=0)

    # Serialize to string using native ElementTree declaration
    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    xml_str = xml_bytes.decode("utf-8")

    # Inject CDATA
    for placeholder, cdata in item_replacements.items():
        xml_str = xml_str.replace(placeholder, cdata)

    return cast(str, xml_str)


def lint() -> int:
    """Run structural checks on the aggregated feed items without writing RSS."""
    init_providers()
    refresh_from_env()
    configure_logging()

    statuses = provider_statuses()
    report = RunReport(statuses)
    report.prune_logs()
    report.attach_error_collector()
    _log_startup_summary(statuses)
    _validate_configuration(statuses)

    now = datetime.now(UTC)
    state = _load_state()
    stale_cache_messages = _detect_stale_caches(report, now)
    if stale_cache_messages:
        log.warning("Veraltete Caches erkannt: %s", "; ".join(stale_cache_messages))
    exit_code = 0

    try:
        items = _invoke_collect_items(report)
        raw_count = len(items)
        # The same chain as ``main`` (``_assemble_feed_items``), so lint
        # counts what the health report of a build would count. ``state`` is
        # only read back by ``_count_new_items`` and never saved here.
        stats = FeedAssemblyStats()
        deduped_items = _assemble_feed_items(items, state, now, stats=stats)
        filtered_count = stats.filtered_count
        duplicate_summaries = stats.duplicate_summaries
        duplicates_removed = stats.duplicates_removed
        deduped_count = stats.deduped_count
        new_items_count = _count_new_items(deduped_items, state)
        missing_guid_items = [it for it in deduped_items if not it.get("guid")]

        metrics = FeedHealthMetrics(
            raw_items=raw_count,
            filtered_items=filtered_count,
            deduped_items=deduped_count,
            new_items=new_items_count,
            duplicate_count=duplicates_removed,
            duplicates=tuple(duplicate_summaries),
        )

        print("Feed-Lint Bericht")
        print("==================")
        print(f"Rohdaten: {metrics.raw_items}")
        print(f"Nach Altersfilter: {metrics.filtered_items}")
        print(
            f"Nach Deduplizierung: {metrics.deduped_items} "
            f"(entfernte Duplikate: {metrics.duplicate_count})"
        )
        print(f"Neue Items (vs. State): {metrics.new_items}")

        if duplicate_summaries:
            print("\nErkannte Duplikat-Gruppen:")
            for summary in duplicate_summaries:
                titles = ", ".join(
                    title or "<ohne Titel>" for title in summary.titles if title is not None
                )
                titles = titles or "<keine Beispiele>"
                print(
                    f"- {summary.count}× Schlüssel {summary.dedupe_key}: {titles}"
                )

        if stale_cache_messages:
            print("\nVeraltete Cache-Dateien:")
            for message in stale_cache_messages:
                print(f"- {message}")

        if missing_guid_items:
            print("\nEinträge ohne GUID:")
            for item in missing_guid_items:
                # Security: ``source`` / ``title`` are upstream-controlled and
                # reach this operator-facing stdout sink BEFORE the per-item
                # ``_format_item_content`` sanitisation runs (lint never formats
                # the items). Route both through ``_sanitize_text`` so the
                # canonical ``_CONTROL_RE`` floor (C0/C1 + BiDi + zero-width +
                # line/paragraph separators + Tag/VS blocks) is stripped here too
                # — the sibling duplicate-group print already sanitises its
                # titles via ``_summarize_duplicates``; this closes the
                # un-sanitised sibling path (Trojan-Source / terminal-escape /
                # log-forgery on the lint report).
                source = _sanitize_text(str(item.get("source") or "unbekannt"))
                title = _sanitize_text(str(item.get("title") or "<ohne Titel>"))
                print(f"- {source}: {title}")

        provider_failures = report.has_errors()
        if provider_failures:
            print("\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.")

        if not duplicate_summaries and not missing_guid_items and not provider_failures:
            print("\nKeine strukturellen Probleme gefunden.")

        if provider_failures:
            exit_code = 2
        elif duplicate_summaries or missing_guid_items or stale_cache_messages:
            exit_code = 1
        else:
            exit_code = 0

        report.finish(
            build_successful=exit_code == 0,
            raw_items=metrics.raw_items,
            final_items=metrics.deduped_items,
        )
        return exit_code
    except Exception as exc:  # pragma: no cover - defensive
        # Security (Clear-Text-Logging Drift): broad framework catch-all
        # — sanitise the bound exception name in the message string.
        log.exception("Feed-Lint fehlgeschlagen: %s", sanitize_log_arg(str(exc)))
        report.record_exception(exc)
        report.finish(build_successful=False)
        return 2
    finally:
        report.log_results()


@dataclasses.dataclass
class FeedAssemblyStats:
    """Counters and timings of one ``_assemble_feed_items`` run.

    Filled step by step, so a caller that catches an exception mid-chain
    still reads what was counted up to that point (``main`` writes them to
    the health report either way).
    """

    filtered_count: int = 0
    deduped_count: int = 0
    duplicates_removed: int = 0
    duplicate_summaries: list[DuplicateSummary] = dataclasses.field(default_factory=list)
    dropped_ids: set[str] = dataclasses.field(default_factory=set)
    filter_duration: float = 0.0
    dedupe_duration: float = 0.0


def _assemble_feed_items(
    items: list[FeedItem],
    state: dict[str, Any],
    now: datetime,
    *,
    stats: FeedAssemblyStats | None = None,
) -> list[FeedItem]:
    """Turn the collected provider items into the ordered feed list.

    This is the whole feed chain between fetching and writing: test notices
    out, age filter, dedupe, WL merges, Baustellen twins, identity carry-over
    and the ordering rules. ``main`` calls it, and so must every replay or
    probe that rebuilds a past feed state from cached data, so a probe can
    never compute a different feed than the real build (older probes that
    copied the steps by hand missed the twin step). Fetching, RSS rendering,
    file writes and ``_save_state`` stay in ``main``.

    ``state`` is updated in place as before (first_seen, restarts, carried
    identities). The list is not cut to ``MAX_ITEMS``; ``_make_rss`` does that.
    """
    if stats is None:
        stats = FeedAssemblyStats()
    collected_count = len(items)
    items = _drop_test_messages(items)

    filter_start = perf_counter()
    # Before the age filter: it reads the same first_seen.
    _restart_recurring_occurrences(items, state, now)
    _note_announced_starts(items, state, now)
    # The shown ÖBB section's end, before the age filter reads ``ends_at``.
    _end_with_oebb_lead_section(items)
    items, dropped_ids = _drop_old_items(items, now, state)
    stats.dropped_ids = dropped_ids
    stats.filter_duration = perf_counter() - filter_start
    stats.filtered_count = len(items)
    log.info(
        "Altersfilter angewendet: %d Items nach %.2fs (vorher: %d)",
        len(items),
        stats.filter_duration,
        collected_count,
    )

    # Performance: capture the pre-dedupe count and run the duplicate
    # summary BEFORE ``_dedupe_items`` mutates the list. The previous
    # ``copy.deepcopy(items)`` snapshot was defensive overhead — at
    # ~200 items per typical run it costs ~MBs of RAM and ~100ms of
    # CPU; at 100x scale (a stress-day with thousands of items) it
    # would dominate the build. ``_summarize_duplicates`` only reads
    # from the items (the only mutation it can trigger is a benign
    # ``_calculated_identity`` cache key on the dict, which doesn't
    # affect the summary's output); ``_dedupe_items`` runs strictly
    # after both observers have seen the pre-dedupe state, so the
    # snapshot copy was redundant.
    pre_dedupe_count = len(items)
    stats.duplicate_summaries = _summarize_duplicates(items)

    dedupe_start = perf_counter()
    deduped = _dedupe_items(items)
    stats.dedupe_duration = perf_counter() - dedupe_start
    log.info(
        "Duplikate entfernt: %d eindeutige Items nach %.2fs (vorher: %d)",
        len(deduped),
        stats.dedupe_duration,
        pre_dedupe_count,
    )

    fuzzy_start = perf_counter()
    fuzzy_deduped = cast(
        list[FeedItem],
        deduplicate_fuzzy(cast(list[dict[str, Any]], deduped)),
    )
    fuzzy_duration = perf_counter() - fuzzy_start
    if len(fuzzy_deduped) < len(deduped):
        log.info(
            "Fuzzy Duplikate entfernt: %d verbleiben nach %.2fs (vorher: %d)",
            len(fuzzy_deduped),
            fuzzy_duration,
            len(deduped),
        )

    # One WL incident, one slot — see ``_merge_wl_ticker_clusters``; the
    # display tickers of planned works go up in their notice.
    items = _absorb_works_tickers(_merge_wl_ticker_clusters(fuzzy_deduped))
    # A notice gives way while WL's live incident reports it — see
    # ``_yield_notices_to_incidents``.
    items = _yield_notices_to_incidents(items, now)
    # A Baustelle that a running WL notice reports gives way to it.
    items = _drop_baustellen_twins(items, now)
    # A merged item keeps its GUID and begin while it runs — see
    # ``_carry_item_identity``. The entry of a GUID carried on stays,
    # though its own message may have run out above.
    items = _carry_item_identity(items, state, now)
    stats.dropped_ids -= {str(it.get("guid")) for it in items if it.get("guid")}
    stats.deduped_count = len(items)
    stats.duplicates_removed = sum(
        summary.count - 1 for summary in stats.duplicate_summaries
    )
    if not items:
        log.warning("Keine Items gesammelt.")
        items = []
    else:
        log.debug("Sortiere %d Items nach Priorität (first_seen, neueste zuerst).", len(items))
    now_utc = _to_utc(now)
    sort_keys = {id(it): _recency_sort_key(it, state, now_utc) for it in items}
    items.sort(key=lambda it: sort_keys[id(it)])
    # One route must not stand in the feed once per construction phase —
    # see ``_defer_repeated_route_titles``; a current incident keeps its
    # place (tier 0 of the sort key).
    items = _defer_repeated_route_titles(
        items, is_current=lambda it: sort_keys[id(it)][0] == 0
    )
    # One event must not take every slot — see ``_apply_topic_budget``.
    items = _apply_topic_budget(items, feed_config.MAX_ITEMS_PER_TOPIC)
    # What applies now comes before what is only announced — see
    # ``_defer_upcoming_items``.
    items = _defer_upcoming_items(items, now_utc, feed_config.UPCOMING_PREVIEW_DAYS)
    # An all-clear only fills a slot nothing else needs — see
    # ``_defer_all_clear_items``.
    return _defer_all_clear_items(items)


def main() -> int:
    """Execute the full feed generation pipeline (collect, dedupe, generate RSS)."""
    init_providers()
    refresh_from_env()
    configure_logging()

    statuses = provider_statuses()
    report = RunReport(statuses)
    report.prune_logs()
    report.attach_error_collector()
    _log_startup_summary(statuses)
    _validate_configuration(statuses)

    job_start = perf_counter()
    now = datetime.now(UTC)
    state = _load_state()
    stale_cache_messages = _detect_stale_caches(report, now)
    if stale_cache_messages:
        log.warning("Veraltete Caches erkannt: %s", "; ".join(stale_cache_messages))
    health_metrics: FeedHealthMetrics | None = None
    # Initialize counters to 0 to avoid redundant checks later (Task 3A);
    # the feed chain counts into ``stats``.
    raw_count: int = 0
    new_items_count: int = 0
    items: list[FeedItem] = []
    stats = FeedAssemblyStats()
    health_path = validate_path(Path(feed_config.FEED_HEALTH_PATH), "FEED_HEALTH_PATH")
    health_json_path = validate_path(
        Path(feed_config.FEED_HEALTH_JSON_PATH), "FEED_HEALTH_JSON_PATH"
    )

    def _write_health_outputs(active_metrics: FeedHealthMetrics) -> None:
        try:
            write_feed_health_report(
                report, active_metrics, output_path=health_path
            )
        except Exception as exc:  # pragma: no cover - defensive
            # Security (Clear-Text-Logging Drift): defensive framework catch.
            log.warning(
                "Feed-Health-Markdown konnte nicht geschrieben werden: %s",
                sanitize_log_arg(str(exc)),
            )
        try:
            write_feed_health_json(
                report, active_metrics, output_path=health_json_path
            )
        except Exception as exc:  # pragma: no cover - defensive
            # Security (Clear-Text-Logging Drift): defensive framework catch.
            log.warning(
                "Feed-Health-JSON konnte nicht geschrieben werden: %s",
                sanitize_log_arg(str(exc)),
            )

    try:
        collect_start = perf_counter()
        items = _invoke_collect_items(report)
        collect_duration = perf_counter() - collect_start
        raw_count = len(items)
        log.info(
            "Provider-Abfrage abgeschlossen: %d Items in %.2fs",
            raw_count,
            collect_duration,
        )
        items = _assemble_feed_items(items, state, now, stats=stats)

        new_items_count = _count_new_items(items, state)

        health_metrics = FeedHealthMetrics(
            raw_items=raw_count,
            filtered_items=stats.filtered_count,
            deduped_items=stats.deduped_count,
            new_items=new_items_count,
            duplicate_count=stats.duplicates_removed,
            duplicates=tuple(stats.duplicate_summaries),
        )

        # German feed (primary) is built first so the public ``feed.xml``
        # is always refreshed regardless of any translation-pipeline
        # issues encountered for the EN variant.
        rss_start = perf_counter()
        rss_de = _make_rss(items, now, state, lang="de")
        rss_duration = perf_counter() - rss_start

        out_path = validate_path(Path(feed_config.OUT_PATH), "OUT_PATH")
        with atomic_write(
            out_path, mode="w", encoding="utf-8", permissions=0o644
        ) as f:
            f.write(rss_de)

        # English mirror — written next to ``feed.xml`` as ``feed.en.xml``.
        # Failures during translation degrade gracefully to the German
        # original via ``_translate_text`` / ``_apply_lang_overlay``;
        # write errors are isolated so the primary feed (already on disk)
        # stays committed even if the EN file cannot be produced.
        en_path = out_path.with_name("feed.en.xml")
        en_out_path = validate_path(en_path, "OUT_PATH")
        try:
            rss_en = _make_rss(
                items, now, state, lang="en"
            )
            with atomic_write(
                en_out_path, mode="w", encoding="utf-8", permissions=0o644
            ) as f:
                f.write(rss_en)
        except Exception as exc:
            log.warning(
                "EN-Feed konnte nicht geschrieben werden (%s) – "
                "deutscher Feed ist bereits aktualisiert.",
                sanitize_log_arg(str(exc)),
            )

        _record_translation_status(now)
        _remember_item_identity(items, state, now)

        try:
            _save_state(state, deletions=stats.dropped_ids)
        except Exception as e:
            # Security (Clear-Text-Logging Drift): broad framework catch.
            log.warning(
                "State speichern fehlgeschlagen (%s) – Feed wurde geschrieben, State bleibt veraltet.",
                sanitize_log_arg(str(e)),
            )
            # Surface the failure on the structured report so monitoring
            # / dashboards see "degraded" instead of a clean
            # ``build_successful=True``. Pre-fix the swallowed exception
            # let the run report record a fully-successful build while
            # first_seen / translations / stats counters silently
            # drifted out of sync with the on-disk feed.
            report.add_warning(
                f"State save failed: {sanitize_log_arg(type(e).__name__)} — "
                "first_seen / translations / stats may drift on the next run."
            )

        total_duration = perf_counter() - job_start
        log.info(
            "Feed geschrieben: %s (%d Items) in %.2fs (RSS-Erzeugung: %.2fs)",
            out_path,
            min(len(items), feed_config.MAX_ITEMS),
            total_duration,
            rss_duration,
        )
        report.finish(
            build_successful=True,
            raw_items=raw_count,
            final_items=len(items),
            durations={
                "collect": collect_duration,
                "filter": stats.filter_duration,
                "dedupe": stats.dedupe_duration,
                "rss": rss_duration,
                "total": total_duration,
            },
            feed_path=out_path,
        )
        _write_health_outputs(health_metrics)
        report.log_results()
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        # Security (Clear-Text-Logging Drift): outer-most pipeline catch.
        log.exception("Feed-Bau fehlgeschlagen: %s", sanitize_log_arg(str(exc)))
        report.record_exception(exc)
        if health_metrics is None:
            # Simplified fallback using pre-initialized counters (Task 3A);
            # ``stats`` holds what the feed chain counted before it failed.
            health_metrics = FeedHealthMetrics(
                raw_items=raw_count,
                filtered_items=stats.filtered_count,
                deduped_items=stats.deduped_count,
                new_items=new_items_count,
                duplicate_count=stats.duplicates_removed,
                duplicates=tuple(stats.duplicate_summaries),
            )
        report.finish(build_successful=False)
        _write_health_outputs(health_metrics)
        report.log_results()
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Security: Prevent stack trace and sensitive info leakage to stderr
        if os.getenv("WIEN_OEPNV_DEBUG") == "1":
            raise

        # The exception is likely already logged by the application logger if configured.
        # We fail securely by not exposing internal details.
        print("Error: An unexpected error occurred. See logs for details (or set WIEN_OEPNV_DEBUG=1).", file=sys.stderr)
        sys.exit(1)
