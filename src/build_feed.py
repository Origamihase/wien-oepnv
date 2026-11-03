from __future__ import annotations

import dataclasses
import hashlib
import html
import inspect
import json
import os
import re
import string
import secrets
import sys
import xml.etree.ElementTree as ET  # nosec B405
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from datetime import date, datetime, time, timedelta, UTC
from email.utils import format_datetime
from itertools import pairwise
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter
from typing import Any, cast, NamedTuple
from urllib.parse import quote, urlparse

import requests

from .feed_types import FeedItem
from .feed import config as feed_config
# ``as`` re-exports explicitly: tests read the tables from ``build_feed``.
from .feed.glossary import (
    _FIELD_LABEL_EN as _FIELD_LABEL_EN,
    _GLOSSARY_BASE as _GLOSSARY_BASE,
    _GLOSSARY_BY_CATEGORY as _GLOSSARY_BY_CATEGORY,
    _GLOSSARY_BY_SOURCE as _GLOSSARY_BY_SOURCE,
)
from .feed.merge import _is_wl_disruption, deduplicate_fuzzy, member_guids
from .feed.logging import configure_logging
from .feed.providers import (
    iter_providers,
    load_provider_plugins,
    provider_statuses,
    register_provider,
    resolve_provider_name,
)
from .feed.reporting import (
    DuplicateSummary,
    FeedHealthMetrics,
    RunReport,
    clean_message,
    write_feed_health_report,
    write_feed_health_json,
)

from .utils.cache import (
    cache_modified_at,
    read_cache as _core_read_cache,
    register_cache_alert_hook,
)
from .utils.files import _reject_non_finite_constant, _reject_non_finite_float, atomic_write
from .utils.http import validate_http_url
from .utils.locking import file_lock
from .utils.logging import sanitize_log_arg
from .utils.stats import append_disruption_row, extract_location_name
from .providers.baustellen import (
    REFERRAL_BOILERPLATE_RE,
    names_site_street,
    shares_address,
    site_street,
)
from .providers.wl_lines import line_order_key
from .providers.wl_plausibility import end_unknown, incident_begin
from .providers.wl_text import STOP_NOTICE_LEAD_RE
from .utils.text import BLOCK_END_MARK, html_to_text, repair_glued_words, repair_saint_abbreviation, truncate_html

# The parts below were moved verbatim to src/feed_build/ (700-line rule,
# Etappe 8). Every name src/, scripts/ or the tests read stays importable here;
# a test patches a name in the module whose code reads it.
from .feed_build.base import (
    ATOM_NS, GEORSS_NS, MEDIA_NS, log, validate_path, _VIENNA_TZ, _SENTENCE_SPLIT_RE, _to_utc, _fmt_rfc2822,
    _PLANNED_DISRUPTION_RE, _OEBB_SOURCE, _CONTROL_RE, _LINE_TOKEN_RE, _LINE_PREFIX_RE, _WHITESPACE_RE,
    _WHITESPACE_CLEANUP_RE, _sanitize_text, _ymd_or_none, _parse_datetime, _coerce_datetime_field,
    _normalize_item_datetimes, _parse_state_time, _parse_first_seen,
)
from .feed_build.entities import _LINE_ENTITY_RE, _TRAM_LETTER_LINE_RE, _STREET_SUFFIX_RE, _DATE_ENTITY_RE
from .feed_build.placeholders import (
    _resolve_glossary, _norm_metadata, _placeholder_nonce, _PLACEHOLDER_NONCE, _ENTITY_PLACEHOLDER_FORMAT,
    _ENTITY_PLACEHOLDER_RE, _GLOSSARY_PLACEHOLDER_FORMAT, _UNMASK_PLACEHOLDER_RE, _RESIDUAL_PLACEHOLDER_RE,
    _normalise_placeholder_debris, _apply_domain_glossary, _has_short_placeholder_shape, _to_short_placeholders,
    _from_short_placeholders,
)
from .feed_build.relocation import _BLOCK_TERMINAL_PUNCT, _WORD_RE, _relocation_first
from .feed_build.display_titles import (
    _DISPLAY_TITLE_TARGET, _shorten_wl_sentence_title, _repair_baustellen_title, _compact_baustellen_section,
    _ordnungsnummer_as_nr, _mark_house_numbers, _compact_line_prefix, _display_title_parts, _display_title,
    _section_collisions, _split_reason_title, _CATEGORY_PREFIX_WORDS, _TITLE_REASON_WORDS, _TICKER_ABBREVIATIONS,
    _TITLE_BODY_RE, _DATE_RANGE_PREFIX_RE, _DATE_SINGLE_PREFIX_RE,
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
    "_GLOSSARY_BY_CATEGORY", "_GLOSSARY_BY_SOURCE", "format_datetime",
]


def refresh_from_env() -> None:
    """Refresh configuration values and reload provider plugins."""
    feed_config.refresh_from_env()
    load_provider_plugins(force=True)


read_cache = _core_read_cache


# German prepositions/connectors that *require* a following object —
# when a WL title ends with one of these alone (no object), the WL
# source data was truncated mid-sentence and the meldung is useless.
# Real cache item ``41E: Ersatzbus 41E hält gegenüber`` is the
# canonical example — the user reads "stops opposite [nothing]".
_INCOMPLETE_TITLE_TAIL_RE = re.compile(
    r"\b(?:bei|gegen[üu]ber|an|in|vor|nach|zu|über|ueber|am|im|zur|zum)\s*$",
    re.IGNORECASE,
)

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

# Line prefixes WL puts at the start of descriptions are redundant
# noise — the title already carries the line attribution via the
# canonical ``40/41:`` prefix. Real cache examples:
#
#   * ``40+41: Betrieb ab Gersthof``
#   * ``Linie 40: Nach einer Fahrtbehinderung …``
#   * ``Linien 9/40/41/42: Umleitung``
#   * ``Linie D: Unregelmäßige Intervalle …`` (WL tram ``D`` — single
#     bare letter, no digit)
#
# Stripping the prefix at cache-read time cleans up the user-visible
# summary and avoids it being copied verbatim into the description
# during cross-line dedup-merge. The line-token shape mirrors
# ``_STRICT_LINE_TOKEN_RE`` in ``src/providers/wl_lines.py``: either a
# digit-bearing code (``[A-Z]{0,4}\d{1,3}[A-Z]?``, plus the Rufbus ``R``
# of ``44BR``), a single bare uppercase letter (WL tram ``D``) or the
# Badner Bahn ``LB``/``WLB``. Pure multi-letter German words
# (``Achtung``, ``Information``, ``Hinweis``) cannot match either
# shape so generic prefixes and time markers like ``17:30 Uhr…``
# stay untouched.
_WL_DESC_LINE_TOKEN = r"(?:[A-Z]{0,4}\d{1,3}(?:[A-Z]R?)?|[A-Z]|W?LB)"  # nosec B105  # noqa: S105 — regex fragment, not a secret
_WL_DESC_LINIE_PREFIX_RE = re.compile(
    rf"^\s*Linien?\s+(?:{_WL_DESC_LINE_TOKEN})"
    rf"(?:\s*[/+,]\s*(?:{_WL_DESC_LINE_TOKEN})){{0,20}}\s*:\s+",
    re.IGNORECASE,
)
_WL_DESC_COMPACT_PREFIX_RE = re.compile(
    rf"^\s*(?:{_WL_DESC_LINE_TOKEN})"
    rf"(?:\s*[/+,]\s*(?:{_WL_DESC_LINE_TOKEN})){{0,20}}\s*:\s+",
    re.IGNORECASE,
)


def _strip_wl_description_line_prefix(desc: str) -> str:
    """Remove a leading WL ``Linie N:`` / ``40+41:`` prefix from a description.

    The title already carries the line attribution, so the prefix is
    pure noise that mirrors back into the rendered summary.
    """
    if not desc:
        return desc
    cleaned = _WL_DESC_LINIE_PREFIX_RE.sub("", desc, count=1)
    if cleaned == desc:
        cleaned = _WL_DESC_COMPACT_PREFIX_RE.sub("", desc, count=1)
    return cleaned


# WL attributes a multi-line disruption by listing every affected line in
# ONE description, each with its own ``Linie X:`` prefix. When the lines
# are hit the same way, every segment carries the IDENTICAL sentence::
#
#     Linie 5B: Unregelmäßige Intervalle in beiden Richtungen.
#     Linie 49A: Unregelmäßige Intervalle in beiden Richtungen.
#     Linie 50A: Unregelmäßige Intervalle in beiden Richtungen.
#     Grund: Verkehrsstörung.
#
# :func:`_strip_wl_description_line_prefix` removes only the FIRST prefix
# — the title already attributes the lines — so what reached the feed was
# the same sentence twice, one arbitrary line out of three named as if it
# were special, and ``Grund: Verkehrsstörung.`` pushed past the
# 180-character limit::
#
#     Unregelmäßige Intervalle in beiden Richtungen. Linie 49A:
#     Unregelmäßige Intervalle in beiden Richtungen. …
#
# 13 of 250 published German items looked like this. Dropping the repeats
# leaves ``Unregelmäßige Intervalle in beiden Richtungen. Grund:
# Verkehrsstörung.`` — shorter, and it finally says why.
#
# The segment boundary reuses :data:`_WL_DESC_LINE_TOKEN`, the same token
# shape the two prefix patterns above use, rather than a second list that
# could drift away from them.
_WL_DESC_LINE_SEGMENT_SPLIT_RE: re.Pattern[str] = re.compile(
    rf"(?<=[.!?])\s+(?=Linien?\s+(?:{_WL_DESC_LINE_TOKEN})"
    rf"(?:\s*[/+,]\s*(?:{_WL_DESC_LINE_TOKEN})){{0,20}}\s*:\s)",
    re.IGNORECASE,
)

# First sentence end inside a segment body. Deliberately not a general
# sentence splitter: it is only ever applied to the text of one segment,
# and the split above already happens at a ``Linie X:`` boundary, so a
# date like ``30.10.2026`` in a later sentence can never become a
# segment boundary.
_WL_DESC_SENTENCE_END_RE: re.Pattern[str] = re.compile(r"[.!?](?:\s|$)")


def _collapse_repeated_wl_line_segments(desc: str) -> str:
    """Drop ``Linie X:`` segments that repeat an earlier segment's sentence.

    The title names every affected line, so a segment whose sentence is
    word-identical to one already in the description carries no
    information — it only crowds out the trailing ``Grund: …`` field,
    which does.

    Only the repeated *leading sentence* of a segment is dropped;
    whatever follows it is kept in place. Measured over 267 revisions of
    the WL cache, that remainder is always the global ``Grund: …`` field
    and never line-specific text, so nothing loses its attribution.

    Segments whose sentences differ are left completely untouched,
    prefix included — there the ``Linie X:`` attribution is the whole
    point.

    Args:
        desc: A Wiener-Linien description, before the leading prefix is
            stripped by :func:`_strip_wl_description_line_prefix`.

    Returns:
        The description with repeated line segments removed, or *desc*
        unchanged when it holds fewer than two line segments.
    """
    segments = _WL_DESC_LINE_SEGMENT_SPLIT_RE.split(desc)
    if len(segments) < 2:
        return desc
    kept: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        body = _WL_DESC_LINIE_PREFIX_RE.sub("", segment, count=1)
        end = _WL_DESC_SENTENCE_END_RE.search(body)
        lead = body[: end.end()] if end else body
        normalised = " ".join(lead.split())
        if normalised in seen:
            remainder = body[end.end() :].strip() if end else ""
            if remainder:
                kept.append(remainder)
            continue
        seen.add(normalised)
        kept.append(segment.strip())
    return " ".join(part for part in kept if part)


# WL sometimes ships the location slot of a ``Grund: …`` field empty::
#
#     Grund: Verkehrsüberlastung im Bereich .
#
# and sometimes ships it filled but with a stray space before the full
# stop::
#
#     Grund: Polizeieinsatz im Haltestellenbereich Atzgersdorfer Straße .
#
# 9 of 417 cached descriptions carry one of the two. Both used to sit
# past the 180-character limit and were cut away unseen; collapsing the
# repeated line segments above brings the ``Grund: …`` field back into
# view, so they would now reach the display. The dangling ``im Bereich``
# is only dropped when NOTHING follows it — a named location keeps its
# preposition.
_WL_DESC_EMPTY_LOCATION_RE: re.Pattern[str] = re.compile(
    r"\s+im\s+(?:Haltestellen)?[Bb]ereich\s*(?=[.!?]|$)"
)
_WL_DESC_SPACE_BEFORE_STOP_RE: re.Pattern[str] = re.compile(r"\s+(?=[.!?])")


def _tidy_wl_dangling_location(desc: str) -> str:
    """Repair a ``Grund: …`` field whose location slot WL left empty.

    Drops an ``im Bereich`` / ``im Haltestellenbereich`` that is followed
    by nothing, then removes the space WL leaves in front of the full
    stop. A location that IS named keeps its preposition and only loses
    the stray space.

    Args:
        desc: A Wiener-Linien description.

    Returns:
        The tidied description.
    """
    if not desc:
        return desc
    return _WL_DESC_SPACE_BEFORE_STOP_RE.sub(
        "", _WL_DESC_EMPTY_LOCATION_RE.sub("", desc)
    )

# A per-line measure sentence in a WL notice — ``Linie D: …``,
# ``Linien 56A und 56B: …`` — at the text start or right after a sentence
# end or a label colon (``Maßnahmen: Linie D: …``). Built on the shared
# ``_WL_DESC_LINE_TOKEN`` so it recognises exactly the line codes the
# other WL description rules do. The label may carry what WL writes
# between the lines and the colon — a note in brackets (``Linie 71
# (ganztägig):``), the direction (``Linie 11A in Richtung Stadion U:``,
# ``Linie N71 in beiden Richtungen:``) — and the days in front of it
# (``Montag-Freitag, Linie 80A …:``, ``Samstag, Sonntag und Feiertag,
# Linie 80A:``). Without them 19 notices in the cache since July showed only
# "… kommt es zu folgenden Verkehrsmaßnahmen." (see ``_prefer_measures``).
_WL_MEASURE_DAY = r"(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|Feiertag)"
_WL_MEASURE_LABEL = (
    rf"(?:{_WL_MEASURE_DAY}(?:\s*(?:-|–|,|und|bis)\s*{_WL_MEASURE_DAY}){{0,6}},\s+)?"
    rf"Linien?\s+{_WL_DESC_LINE_TOKEN}"
    rf"(?:\s*(?:,|und|/|\+)\s*{_WL_DESC_LINE_TOKEN}){{0,20}}"
    r"(?:\s+\([^()]{1,40}\))?"
    r"(?:\s+in\s+(?:beiden\s+Richtungen|Richtung\s+(?:(?:St|Bhf|Hbf|Dr|Str)\.|[^:.;]){1,80}?))?\s*:\s"
)
_WL_MEASURE_START_RE: re.Pattern[str] = re.compile(
    rf"(?:^|(?<=[.!?:])\s+)(?={_WL_MEASURE_LABEL})"
)
_WL_MEASURE_JOIN_RE: re.Pattern[str] = re.compile(rf"[.!?]\s+(?={_WL_MEASURE_LABEL})")
# WL also sets the line as a heading of its own, the measure on the next
# line (``<u>Linie D</u><br>Umleitung …``), which reaches the summary as
# ``Linie D • Umleitung …``: the colon form above, once the bullet becomes
# the colon (``_wl_measure_labels_with_colon``).
_WL_BULLET_LABEL_RE: re.Pattern[str] = re.compile(
    rf"(?<=[.!?:]\s)(Linien?\s+{_WL_DESC_LINE_TOKEN}"
    rf"(?:\s*(?:,|und|/|\+)\s*{_WL_DESC_LINE_TOKEN}){{0,20}}"
    r"(?:\s+\([^()]{1,40}\))?)\s+•\s+"
)


# The cut behind a measure's label (``_truncate_summary_180``): what is left
# of the ``;``-joined measure has no colon yet.
_WL_BARE_MEASURE_LABEL_TAIL_RE: re.Pattern[str] = re.compile(
    rf";\s*(?:{_WL_MEASURE_DAY}[^;:]*?,\s+)?Linien?\s+{_WL_DESC_LINE_TOKEN}[^;:]*$"
)


def _wl_measure_labels_with_colon(summary: str) -> str:
    """``"Linie D • Umleitung …"`` → ``"Linie D: Umleitung …"`` (see above)."""
    return _WL_BULLET_LABEL_RE.sub(r"\1: ", summary)


# The sentence that only announces the list: ``… kommt es zu folgenden
# Verkehrsmaßnahmen.``, ``… treten bei der Linie 80A Maßnahmen in Kraft.``
# The title already names the reason.
_WL_ANNOUNCEMENT_RE: re.Pattern[str] = re.compile(
    r"\bfolgenden\s+\w*(?:maßnahmen|änderungen|einschränkungen|umleitungen)\b"
    r"|\bMaßnahmen\s+in\s+Kraft\b",
    re.IGNORECASE,
)
# A request to the reader in front of the measures (``Beachten Sie bitte
# die unterschiedlichen Maßnahmen werktags bzw. am Wochenende.``): the
# measures themselves say it.
_WL_REQUEST_SENTENCE_RE: re.Pattern[str] = re.compile(r"^(?:Bitte\s+)?[A-ZÄÖÜ]\w+en\s+Sie\b")
# A bare label left over before the measures: ``Maßnahmen:``.
_WL_LABEL_SENTENCE_RE: re.Pattern[str] = re.compile(r"^(?:\w+\s*){1,3}:$")


def _prefer_measures(summary: str) -> str:
    """Put a WL notice's per-line measures first — the display budget is 180.

    WL writes its full notices in one shape: an announcement, a period,
    then one measure per line. Published 2026-09-19 for the seven-line
    demonstration notice, the extract kept the announcement and nothing
    else::

        Wegen einer Demonstration im Bereich Schwarzenbergplatz und Ring
        kommt es zu folgenden Verkehrsmaßnahmen.

    ``folgenden`` — and then the 180 characters were spent. The measures
    (``Linie D: Derzeit kein Betrieb zwischen Börse und Quartier
    Belvedere. Linie 1: Umleitung …``) never reached the display, and 20
    of the 37 notices in the cache have that block.

    When a text carries a measure block, the sentences before it lose
    the announcement (the title names the reason) and the ``Zeitraum:``
    sentence (the item's own date line renders the period); a sentence
    with content of its own — ``Wegen Instandsetzungsarbeiten in der
    Röntgengasse wird die Linie 44A kurzgeführt.`` — stays. The measures
    are joined with ``;`` so the two-sentence extract downstream treats
    them as one and ``_truncate_summary_180`` cuts them with an ellipsis
    instead of stopping after the first line's measure. Texts without a
    measure block are returned unchanged.
    """
    match = _WL_MEASURE_START_RE.search(summary)
    if match is None:
        return summary
    head = summary[: match.start()].strip()
    measures = _WL_MEASURE_JOIN_RE.sub("; ", summary[match.end() :].strip())
    kept = [
        sentence
        for sentence in (part.strip() for part in _SENTENCE_SPLIT_RE.split(head))
        if sentence
        and not sentence.startswith("Zeitraum:")
        and not _WL_ANNOUNCEMENT_RE.search(sentence)
        and not _WL_REQUEST_SENTENCE_RE.match(sentence)
        and not _WL_LABEL_SENTENCE_RE.match(sentence)
    ]
    return " ".join([*kept, measures])


# --- Every line of a WL disruption, in short (operator decision 2026-10-15) --
#
# WL writes the long message of a disruption on several lines as one
# sentence per line. The summary keeps two sentences and 180 characters, so
# the feed showed only the first line's measure. On 2026-10-11 the gas leak
# in Brünner Straße read
#
#     30A/31A/36B: Gasrohrgebrechen
#     Die Linie 36B wird in Richtung Jedlesee, Bellgasse zwischen
#     Berzeliusgasse und Brünner Straße S über Brünner Straße umgeleitet.
#
# and said nothing of the detours of 30A and 31A. 70 WL texts since July
# lost the measure of at least one line this way; where the text labels
# each line ("Linie 37: … Linie 38: …"), the first label was stripped as
# well (``_strip_wl_description_line_prefix``), and "Betrieb nur zwischen
# Hohe Warte und Nußdorfer Straße U" stood without its line. Michael's
# card pick "Kurzform je Linie" (2026-10-15, "Am TV sieht man sonst nur,
# was mit einer von drei Linien passiert"): each line with its measure and
# its two end points, without direction and detour streets::
#
#     Umleitungen: 36B Berzeliusgasse – Brünner Straße S; 30A
#     Berzeliusgasse – Gschweidlgasse; 31A Berzeliusgasse – Großjedlersdorf,
#     Jochbergengasse.
#
# Lines with different measures name them each ("37: Betrieb nur Hohe
# Warte – Nußdorfer Straße U; 5: Umleitung …"). WL's expected end follows
# when it fits. It applies only where a line would go missing: a text
# whose shown part (first sentences, 180 cut) names every line stays as WL
# wrote it, and so does one with a measure the patterns below do not know; lines that do not fit the
# 180 characters give way whole, last first, behind "…".
_SHORT_LINE_MARKER_RE: re.Pattern[str] = re.compile(
    rf"(?:^|(?<=[.!?;])\s+)(?:Linien?\s+(?P<label>{_WL_DESC_LINE_TOKEN}"
    rf"(?:\s*(?:,|und|/|\+)\s*{_WL_DESC_LINE_TOKEN}){{0,20}})\s*:\s+"
    rf"|(?P<lb>Badner\s+Bahn)\s*:\s+"
    rf"|Die\s+(?:Züge\s+der\s+)?Linie\s+(?P<subject>{_WL_DESC_LINE_TOKEN})\s+(?=wird|werden|fährt))"
)
_SHORT_TAIL_RE: re.Pattern[str] = re.compile(
    r"\s+(?:Weichen Sie|Voraussichtlich bis|Voraussichtliche Dauer|Grund:)"
)
_SHORT_EXPECTED_END_RE: re.Pattern[str] = re.compile(r"Voraussichtlich bis [^.]*?\d{1,2}:\d{2} Uhr\.")
# A place ends before "über", "örtlich", "umgeleitet", "in Richtung", the
# sentence end or a clause of its own: "und" or a comma before a small word
# (WL names its stops with capitals: "Heinestraße, Taborstraße"). On
# 2026-05-10 the 1 "fährt nur zwischen Stefan-Fadinger-Platz und
# Radetzkyplatz und wird ab Kliebergasse über die Linien 18 und O
# umgeleitet", and the short form read "1: Betrieb nur Stefan-Fadinger-Platz
# – Radetzkyplatz und wird ab Kliebergasse …". What follows the clause goes
# the way of "Weiterfahrt …". WL's abbreviations ("Bhf.", "Str.", "St.",
# "Dr.-Adolf-…") and ordinals ("Zentralfriedhof, 4. Tor") are part of it.
_SHORT_PLACE = r"(?:Bhf\.|Str\.|St\.|Dr\.-?|\b\d{1,2}\.(?=\s)|[^.;])+?"
_SHORT_PLACE_END = (
    r"(?=\s+(?:über|örtlich|umgeleitet|in\s+Richtung)\b|(?:,|\s+und)\s+[a-zäöüß]|[.;]|$)"
)
_SHORT_MEASURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Kein Betrieb", re.compile(
        rf"\b(?:fährt\s+(?:derzeit\s+)?nicht|[Kk]ein\s+Betrieb)\s+zwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+"
        rf"(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}")),
    ("Betrieb nur", re.compile(
        rf"\b(?:fährt\s+(?:derzeit\s+)?nur|Betrieb\s+nur)\s+zwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+"
        rf"(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}")),
    ("Umleitung", re.compile(
        rf"^wird\b[^.;]*?\bzwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}"
        rf"[^;]*?\bumgeleitet\b")),
    ("Umleitung", re.compile(
        rf"^Umleitung\b[^.;]*?\bzwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}")),
)
# A measure without places: the line as a whole.
_SHORT_BARE_MEASURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Fahrtbehinderung", re.compile(r"^Fahrtbehinderung\b")),
    ("Unregelmäßige Intervalle", re.compile(r"^Unregelmäßige Intervalle\b")),
    ("Verspätungen", re.compile(r"^Verspätungen\b")),
    ("Kein Betrieb", re.compile(r"^(?:Kein Betrieb|Derzeit ist ein Betrieb nicht möglich)\s*(?:[.;]|$)")),
)
_SHORT_GROUP_LABEL = {"Umleitung": "Umleitungen"}
# WL's own name for its line LB (the title says "LB", the text "Badner Bahn:").
_SHORT_LINE_NAME = {"LB": "Badner Bahn"}


def _short_measure(body: str) -> tuple[str, str] | None:
    """``("Umleitung", "Berzeliusgasse – Gschweidlgasse")`` for one line's sentence, or ``None``."""
    for kind, pattern in _SHORT_MEASURES:
        match = pattern.search(body)
        if match is not None:
            a, b = match["a"].strip(" ,"), match["b"].strip(" ,")
            if not re.search(r"[A-Za-zÄÖÜäöüß]{2}", a) or not re.search(r"[A-Za-zÄÖÜäöüß]{2}", b):
                return None
            return kind, f"{a} – {b}"
    for kind, pattern in _SHORT_BARE_MEASURES:
        if pattern.match(body):
            return kind, ""
    return None


def _line_segments(text: str, title_lines: Sequence[str]) -> list[tuple[str, str]] | None:
    """``[(line label, its text), …]`` in WL's order, or ``None`` if a part belongs to no line.

    A text before the first label belongs to the one title line no label
    names: ``_post_filter_wl`` strips the first label (see above).
    """
    markers = list(_SHORT_LINE_MARKER_RE.finditer(text))
    if not markers:
        return None
    segments: list[tuple[str, str]] = []
    labels = [
        "/".join(re.split(r"\s*(?:,|und|/|\+)\s*", m["label"]))
        if m["label"]
        else "LB" if m["lb"] else str(m["subject"])
        for m in markers
    ]
    named = {line for label in labels for line in label.split("/")}
    lead = text[: markers[0].start()].strip()
    if lead:
        unnamed = [line for line in title_lines if line not in named]
        if len(unnamed) != 1:
            return None
        segments.append((unnamed[0], lead))
    for index, (marker, label) in enumerate(zip(markers, labels, strict=True)):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.start("subject") + len(label) if marker["subject"] else marker.end() : end].strip()
        segments.append((label, body))
    return segments


def _title_line_list(title: str) -> list[str]:
    """``["30A", "31A", "36B"]`` for "30A/31A/36B: Gasrohrgebrechen"."""
    prefix = re.match(r"^([^:]+):", title)
    return prefix[1].split("/") if prefix else []


def _every_line_in_short(item: FeedItem, summary: str, shown: str, title: str) -> str | None:
    """The short form of a WL disruption's lines when *shown* misses one, else ``None``.

    *summary* is the whole text, *shown* what the TV would show without
    the short form (sentences and the 180 cut applied).
    """
    if item.get("source") != "Wiener Linien" or item.get("category") != "Störung":
        return None
    if all(_text_names_line(shown, line) for line in _title_line_list(title)):
        return None
    return _lines_in_short(summary, title)


def _lines_in_short(summary: str, title: str) -> str | None:
    """*summary* of a WL disruption on several lines with every line in short (see above).

    ``None`` when a line's measure is not one of the known kinds.
    """
    title_lines = _title_line_list(title)
    if len(title_lines) < 2:
        return None
    tail = _SHORT_TAIL_RE.search(summary)
    measures = summary[: tail.start()] if tail else summary
    segments = _line_segments(measures.strip(), title_lines)
    if segments is None or len(segments) < 2:
        return None
    parsed: list[tuple[str, str, str]] = []
    for label, body in segments:
        short = _short_measure(body)
        if short is None:
            return None
        parsed.append((_SHORT_LINE_NAME.get(label, label), *short))
    if {line for label, _b in segments for line in label.split("/")} != set(title_lines):
        return None
    kinds = {kind for _l, kind, _p in parsed}
    if len(kinds) == 1:
        (kind,) = kinds
        head = f"{_SHORT_GROUP_LABEL.get(kind, kind)}: "
        parts = [f"{label} {place}".strip() for label, _k, place in parsed]
        separator = ", " if not any(place for _l, _k, place in parsed) else "; "
    else:
        head = ""
        parts = [f"{label}: {kind} {place}".strip() for label, kind, place in parsed]
        separator = "; "
    text = f"{head}{separator.join(parts)}."
    while len(text) > 180 and len(parts) > 1:
        parts.pop()
        text = f"{head}{separator.join(parts)} …"
    expected = _SHORT_EXPECTED_END_RE.search(summary[tail.start():] if tail else "")
    if expected and len(f"{text} {expected[0]}") <= 180:
        text = f"{text} {expected[0]}"
    return text


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


# WL's display boards show the S-Bahn logo as a glyph that reaches the data as
# "<": "Bhf. Hütteldorf / ÖBB-Ersatzbus für <80", published daily since
# 2026-09-12 with ``relatedLines`` "1". Tram line 1 does not serve Hütteldorf
# (operator, 2026-09-25); an ÖBB replacement bus replaces an ÖBB line, and 80
# is the S80, which starts at Hütteldorf. Only Vienna's S-Bahn numbers count.
_VIENNA_S_BAHN_NUMBERS: frozenset[str] = frozenset(
    {"1", "2", "3", "4", "7", "40", "45", "50", "60", "80"}
)
_OBB_REPLACEMENT_BUS_RE = re.compile(r"ÖBB-Ersatzbus\s+für\s+(?:<\s*|S\s?)?(\d{1,2})\b")


def _attribute_obb_replacement_bus(item: dict[str, Any]) -> dict[str, Any]:
    """``1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80`` → ``S80: ÖBB-Ersatzbus``.

    The title takes the S-Bahn line the text names instead of WL's line and
    says what it is; the place goes into the description (operator decision
    2026-09-25): ``S80: ÖBB-Ersatzbus`` over ``Bhf. Hütteldorf``. Whatever
    else WL's description says stays; only the "ÖBB-Ersatzbus für …" phrase
    the title now carries is taken out of it.
    """
    title = item.get("title")
    if not isinstance(title, str):
        return item
    desc = item.get("description")
    desc_text = desc if isinstance(desc, str) else ""
    match = _OBB_REPLACEMENT_BUS_RE.search(f"{title}\n{desc_text}")
    if match is None or match.group(1) not in _VIENNA_S_BAHN_NUMBERS:
        return item
    body = title.split(":", 1)[1] if ":" in title else title
    place = body.split("ÖBB-Ersatzbus", 1)[0].strip(" -–:/")
    rest = re.sub(r"\s+", " ", _OBB_REPLACEMENT_BUS_RE.sub(" ", desc_text)).strip(" -–:/")
    fixed = dict(item)
    fixed["title"] = f"S{match.group(1)}: ÖBB-Ersatzbus"
    fixed["description"] = rest or place
    return fixed


def _post_filter_wl(items: list[Any]) -> list[Any]:
    """Defence-in-depth: normalise / drop bad WL items loaded from cache.

    The WL cache is only refreshed periodically, so:

    1. Title-formatting fixes (e.g. newline collapse per Bug 12A) need
       to be re-applied at cache-read time.
    2. WL itself sometimes serves data that's truncated mid-sentence —
       ``Ersatzbus 41E hält gegenüber`` (with no location after
       ``gegenüber``) is meaningless. Such items are dropped.
    3. WL Störung items occasionally arrive without any
       ``relatedLines`` AND without a line code in the title — for
       example ``Verkehrsunfall Betrieb ab Nordbrücke``. The line
       prefix is the key signal for transit-line attribution; without
       it the user can't tell which line is affected, so we drop
       these too.
    4. Cached items can carry a stacked / non-canonical line prefix
       (``40: 40+41: Betrieb ab Gersthof``) when an older
       ``_ensure_line_prefix`` prepended ``relatedLines`` on top of the
       title's own ``40+41:`` block. The re-parse via
       ``_extract_prefix_lines`` unions the layers and re-emits a
       canonical ``40/41: …`` prefix so the user immediately sees
       which lines are affected.
    """
    from .providers.wl_lines import (
        _extract_prefix_lines,
        _strip_redundant_line_token,
    )

    out: list[Any] = []
    for original in items:
        if not isinstance(original, dict):
            out.append(original)
            continue
        item = _attribute_obb_replacement_bus(original)
        prefix_lines: list[str] = []
        title = item.get("title")
        if isinstance(title, str) and title:
            cleaned = re.sub(r"\s+", " ", title).strip()
            # Rebuild a stacked or non-canonical line prefix (e.g.
            # ``40: 40+41: Betrieb ab Gersthof`` →
            # ``40/41: Betrieb ab Gersthof``). Only touches items where
            # ``_extract_prefix_lines`` recovers ≥1 line tokens AND the
            # rebuilt form differs from the cached title — leaves bodies
            # without a prefix block untouched. The lines take WL's order
            # (``line_order_key``) like every other prefix, also in a title
            # cached before that order (``41: 40+41:`` → ``40/41:``).
            body, prefix_lines = _extract_prefix_lines(cleaned)
            if prefix_lines and body:
                canonical = "/".join(sorted(prefix_lines, key=line_order_key))
                rebuilt = f"{canonical}: {body}"
                if rebuilt != cleaned:
                    cleaned = rebuilt
            if cleaned != title:
                item = dict(item)
                item["title"] = cleaned
            # Drop items whose visible title ends with a preposition
            # that demands an object — the WL source is clearly
            # incomplete and the user gets no useful information.
            title_body = cleaned.split(":", 1)[-1].strip() if ":" in cleaned else cleaned
            if _INCOMPLETE_TITLE_TAIL_RE.search(title_body):
                continue
            # Drop WL Störung items without a recognisable line
            # prefix — WL didn't provide a line code and the user
            # can't disambiguate the affected line from the title
            # alone. ``prefix_lines`` was computed via
            # :func:`_extract_prefix_lines` above (with the strict
            # line-token gate) so a generic German word prefix like
            # ``Einstieg: Brünner Straße 31-31A`` or
            # ``Sperre Bahnsteig Richtung Siebenhirten`` (both real
            # cache items) is correctly classified as "no line
            # prefix" and dropped.
            category = item.get("category")
            if category == "Störung" and not prefix_lines:
                continue
        # Strip a redundant ``Linie 40:`` / ``40+41:`` prefix from the
        # description — the title already attributes the line(s).
        desc = item.get("description")
        if isinstance(desc, str) and desc:
            # Collapse repeated ``Linie X:`` segments BEFORE the leading
            # prefix is stripped — the comparison needs every segment to
            # still carry its own prefix.
            stripped = _tidy_wl_dangling_location(
                _strip_wl_description_line_prefix(
                    _collapse_repeated_wl_line_segments(desc)
                )
            )
            # Störung descriptions repeat the line code without the colon
            # the regexes above require (``"3A Netzänderung\nBetrieb ab
            # Riemergasse"``). The title already attributes the line, so
            # the repeat is noise — and leaving it in also defeats the
            # summary/title duplicate check in ``_format_item_content``,
            # which would surface the same sentence twice per item.
            stripped = _strip_redundant_line_token(stripped, prefix_lines)
            if stripped != desc:
                if item is original:
                    item = dict(item)
                item["description"] = stripped
        out.append(item)
    return out


def read_cache_wl() -> list[Any]:
    return _post_filter_wl(list(read_cache("wl") or []))


def _post_filter_oebb(items: list[Any]) -> list[Any]:
    """Re-apply the ÖBB relevance filter and re-derive titles for cached items.

    The cache is only refreshed by `update_oebb_cache.py`, so a filter
    update doesn't reach the feed until the next cache refresh. Without
    this defence-in-depth re-check the feed can carry items that the
    *current* spec considers irrelevant (e.g. Wien↔Distant routes that
    slipped through an older filter version).

    We also re-derive the title via ``_apply_route_title`` so a title-
    formatting improvement (such as the multi-route chain collapse or
    the affected-line-prefix tightening) reaches the feed even when the
    cache still holds an older rendering. The original title is left in
    place if the re-derived version is empty or identical.

    Items without a title are passed through unchanged so test fixtures
    and other generic dictionaries aren't accidentally dropped.
    """
    from .providers.oebb import (  # local import: avoids circular at module load
        _UPDATE_PREFIX_RE,
        _apply_route_title,
        _clean_title_keep_places,
        _is_relevant,
    )
    from .utils.stations import main_station_names

    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        title = str(item.get("title") or "")
        description = str(item.get("description") or "")
        if not title and not description:
            # Stub / metadata item — leave it alone.
            out.append(item)
            continue
        if _UPDATE_PREFIX_RE.match(title):
            # A cached title still carrying ÖBB's "Update N (…)" headline
            # predates the provider fix; clean it the way the provider now
            # does, so the feed repairs without waiting for a cache refresh.
            title = _clean_title_keep_places(title) or title
        if not _is_relevant(title, description):
            continue
        # Main stations as the directory shows them now ("St. Pölten Hbf",
        # ``main_station_names``), also in titles cached before 2026-11-02.
        rederived = main_station_names(_apply_route_title(title, description) or title)
        if rederived != item.get("title"):
            item = dict(item)
            item["title"] = rederived
        out.append(item)
    return out


def read_cache_oebb() -> list[Any]:
    return _post_filter_oebb(list(read_cache("oebb") or []))


def _baustellen_title_names_station(title: str, label: str) -> bool:
    """Return ``True`` if ``title`` already mentions a distinctive token of
    the station/line ``label``, so the prefix isn't doubled up (e.g. avoid
    ``Wien Floridsdorf: Umbau Bahnhofsvorplatz Floridsdorf`` or
    ``U2: … U2 …``). The generic ``Wien`` token (and other ≤4-char tokens)
    is ignored on purpose — except the short U-Bahn line labels ``U1``–``U6``,
    which are distinctive and matched as a whole word."""
    lowered = title.lower()
    for token in re.split(r"\W+", label.lower()):
        if not token:
            continue
        if len(token) > 4 and token in lowered:
            return True
        if re.fullmatch(r"u[1-6]", token) and re.search(rf"\b{token}\b", lowered):
            return True
    return False


def _post_filter_baustellen(items: list[Any]) -> list[Any]:
    """Defence-in-depth relevance gate plus ÖPNV title enrichment.

    ``update_baustellen_cache.py`` already drops non-ÖPNV-relevant sites at
    ingestion, but the on-disk cache (or the bundled fallback sample) may
    predate the current policy — so the gate is re-applied here, the same
    defence-in-depth contract as :func:`_post_filter_oebb`.

    An item is kept when its text says what changes for public transport
    (:func:`src.providers.baustellen.mentions_oepnv`; nearness to a station
    alone no longer counts, operator decision 2026-10-06). When the site lies
    next to a rail Bahnhof, its name is prefixed onto the title so the entry
    reads as a transit message at a glance — mirroring the line/route
    prefixes WL and ÖBB carry. The prefix is skipped when the title already
    names the station, keeping the headline compact.

    Before the prefix, :func:`_repair_baustellen_title` completes a last
    word the city's 100-character cap cut off — from the item's own
    description, and only when that is unambiguous — and drops the
    placeholder ``Unbenannte Verkehrsfläche`` from an endpoint list that
    also names a real place.

    Items carrying neither a title nor a description are treated as
    stubs/metadata and passed through unchanged.
    """
    from .providers.baustellen import mentions_oepnv, relevant_station
    from .utils.stations import display_name

    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        title = str(item.get("title") or "")
        description = str(item.get("description") or "")
        if not title and not description:
            # Stub / metadata item — leave it alone.
            out.append(item)
            continue
        repaired = _repair_baustellen_title(title, description)
        if repaired != title:
            item = dict(item)
            item["title"] = repaired
            title = repaired
        if not mentions_oepnv(f"{title}. {description}"):
            continue
        # Title prefix: the Bahnhof next to the site, the most concrete
        # locator. Line numbers are not guessed (see the provider module):
        # the effect leads the description instead. No U-Bahn prefix any
        # more: in this source the U-Bahn is the builder, and "U2/U5: …"
        # read like a U-Bahn disruption (operator decision 2026-10-06).
        station = relevant_station(item.get("location"))
        label = ""
        if station is not None:
            label = display_name(station) or station
        if label and not _baustellen_title_names_station(title, label):
            item = dict(item)
            item["title"] = f"{label}: {title}" if title else label
        out.append(item)
    return out


def read_cache_baustellen() -> list[Any]:
    return _post_filter_baustellen(list(read_cache("baustellen") or []))


# Stammstrecke feed events are now derived from the CSV ledger
# ``data/stats/stammstrecke_<YYYY>.csv`` rather than a JSON cache —
# see :mod:`src.feed.stammstrecke` for the operational contract
# (1-hour feed window, 6-hour episode lookback, 9-minute threshold).
# Replaced the JSON cache 2026-05-09 (PR follow-up to #1397) so the
# README dashboard and the RSS feed share a single source of truth.
def read_cache_stammstrecke() -> list[Any]:
    """Compute Stammstrecke feed events from the CSV ledger.

    Delegates to :func:`src.feed.stammstrecke.compute_stammstrecke_events`.
    A missing / unreadable / empty ledger naturally yields zero
    events; the feed build then omits the Stammstrecke entry rather
    than failing.
    """

    from .feed import stammstrecke as stammstrecke_events

    return cast(list[Any], stammstrecke_events.compute_stammstrecke_events())


DEFAULT_PROVIDERS: tuple[tuple[str, Any], ...] = (
    ("WL_ENABLE", read_cache_wl),
    ("OEBB_ENABLE", read_cache_oebb),
    ("BAUSTELLEN_ENABLE", read_cache_baustellen),
    # VOR is intentionally absent — VOR API access is scoped to the
    # S-Bahn-Stammstrecke delay monitor only (operator policy
    # 2026-05-11). Stammstrecke feed entries derive from the CSV
    # ledger via :mod:`src.feed.stammstrecke`, not from a VOR
    # disruption cache.
    ("STAMMSTRECKE_ENABLE", read_cache_stammstrecke),
)

PROVIDERS: list[tuple[str, Any]] = list(DEFAULT_PROVIDERS)

_PROVIDERS_INITIALIZED = False

# Ensure plugins are loaded exactly once per process startup AFTER env vars are configured
# and BEFORE the main provider registration loop.
load_provider_plugins()

def reset_module_state() -> None:
    """Test helper to reset the module-level initialization state."""
    global _PROVIDERS_INITIALIZED
    global PROVIDERS
    _PROVIDERS_INITIALIZED = False
    PROVIDERS.clear()
    PROVIDERS.extend(DEFAULT_PROVIDERS)

def init_providers() -> None:
    global _PROVIDERS_INITIALIZED
    if _PROVIDERS_INITIALIZED:
        return
    for env_name, loader in PROVIDERS:
        register_provider(env_name, loader, cache_key=resolve_provider_name(loader, env_name))
    _PROVIDERS_INITIALIZED = True


def _provider_display_name(fetch: Any, env: str | None = None) -> str:
    """Resolve the display name for a provider based on its loader or env var."""
    return resolve_provider_name(fetch, env)


def _detect_stale_caches(report: RunReport, now: datetime) -> list[str]:
    """Record warnings for provider caches older than the configured threshold."""

    if feed_config.CACHE_MAX_AGE_HOURS <= 0:
        return []

    threshold = timedelta(hours=feed_config.CACHE_MAX_AGE_HOURS)
    stale_messages: list[str] = []

    for spec in iter_providers():
        loader = spec.loader
        cache_name = getattr(loader, "_provider_cache_name", None)
        if not cache_name:
            continue

        modified_at = cache_modified_at(str(cache_name))
        if modified_at is None:
            continue

        if modified_at.tzinfo is None:
            modified_at = modified_at.replace(tzinfo=UTC)

        age = now - modified_at
        if age <= threshold:
            continue

        hours = age.total_seconds() / 3600
        message = (
            f"Cache {cache_name}: zuletzt vor {hours:.1f}h aktualisiert "
            f"(Schwelle {feed_config.CACHE_MAX_AGE_HOURS}h)"
        )
        report.add_warning(message)
        stale_messages.append(message)

    return stale_messages


def _provider_statuses() -> list[tuple[str, bool]]:
    """Return a list of (name, enabled) tuples for all registered providers."""
    return provider_statuses()


def _log_startup_summary(statuses: list[tuple[str, bool]]) -> None:
    """Log the active configuration and enabled providers at startup."""
    enabled = sorted(name for name, is_enabled in statuses if is_enabled)
    disabled = sorted(name for name, is_enabled in statuses if not is_enabled)

    enabled_display = ", ".join(enabled) if enabled else "keine"
    log.info(
        "Starte Feed-Bau: %s aktiv (Timeout global=%ss, MaxItems=%d, Worker=%s)",
        enabled_display,
        feed_config.PROVIDER_TIMEOUT,
        feed_config.MAX_ITEMS,
        feed_config.PROVIDER_MAX_WORKERS or "auto",
    )
    if disabled:
        log.info("Deaktivierte Provider: %s", ", ".join(disabled))


def _validate_configuration(statuses: list[tuple[str, bool]]) -> None:
    """Check the runtime configuration for common issues (e.g. no providers active)."""
    enabled_count = sum(1 for _, is_enabled in statuses if is_enabled)
    if not statuses:
        log.warning("Keine Provider registriert – es werden keine Items gesammelt.")
    elif enabled_count == 0:
        log.error(
            "Alle Provider deaktiviert – Feed bleibt leer, bitte Konfiguration prüfen."
        )

    if feed_config.MAX_ITEMS == 0:
        log.warning("MAX_ITEMS ist 0 – der Feed wird ohne Einträge erzeugt.")
    if feed_config.FEED_TTL == 0:
        log.warning(
            "FEED_TTL ist 0 – Clients werten den Feed unmittelbar als abgelaufen."
        )
    if feed_config.PROVIDER_TIMEOUT == 0 and enabled_count:
        log.warning(
            "PROVIDER_TIMEOUT ist 0 – Netzwerkprovider haben keine Zeit für Antworten."
        )
    if feed_config.MAX_ITEM_AGE_DAYS > feed_config.ABSOLUTE_MAX_AGE_DAYS:
        log.warning(
            "MAX_ITEM_AGE_DAYS (%s) übersteigt ABSOLUTE_MAX_AGE_DAYS (%s) – ältere Items "
            "werden dennoch durch den absoluten Grenzwert verworfen.",
            feed_config.MAX_ITEM_AGE_DAYS,
            feed_config.ABSOLUTE_MAX_AGE_DAYS,
        )

# ---------------- Provider tuning ----------------

def _provider_env_slug(name: str) -> str:
    """Convert a provider name into a slug suitable for environment variables."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").upper()).strip("_")
    return slug or "PROVIDER"


def _read_optional_non_negative_int(env_name: str) -> int | None:
    """Read a non-negative integer from an environment variable."""
    raw = os.getenv(env_name)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        value = int(stripped)
    except (TypeError, ValueError) as exc:
        # Security (Clear-Text-Logging Drift, src/utils/* round): the bound
        # ``exc`` text is rendered raw via ``%s`` — sanitise to defeat a
        # hostile env-injected value flowing through ``int()``'s error
        # message into the structured log line.
        #
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``raw`` was previously formatted via ``%r``; that conversion
        # leaves all 256 Variation Selectors (U+FE00-U+FE0F +
        # U+E0100-U+E01EF) in ``record.args`` and ``getMessage()``
        # verbatim. Route through ``sanitize_log_arg`` to strip the
        # canonical ``_INVISIBLE_DANGEROUS_RE`` union BEFORE the value
        # reaches caplog / non-SafeFormatter handlers.
        log.warning(
            "Ungültiger Wert für %s=%s – ignoriere Override (%s: %s)",
            env_name,
            sanitize_log_arg(raw),
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return None
    if value < 0:
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``raw`` is the operator-controlled per-provider env override
        # (``PROVIDER_<SLUG>_MAX_AGE_DAYS`` etc.). Pre-fix the WARNING
        # interpolated it via ``%r`` — Python's repr() escapes most
        # attack bytes but lets all 256 Variation Selectors
        # (U+FE00-U+FE0F + U+E0100-U+E01EF) through verbatim into
        # ``record.args[1]`` and ``record.getMessage()``. Route through
        # ``sanitize_log_arg`` so the canonical
        # ``_INVISIBLE_DANGEROUS_RE`` strips them BEFORE the value
        # lands in caplog / non-SafeFormatter handlers (mirrors the
        # sibling defence on line 374 that already routes ``str(exc)``
        # through the canonical helper).
        log.warning(
            "Negativer Wert für %s=%s – ignoriere Override",
            env_name,
            sanitize_log_arg(raw),
        )
        return None
    return value


def _resolve_provider_override(candidates: list[str]) -> int | None:
    """Helper to resolve the first valid integer from a list of env var candidates."""
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        value = _read_optional_non_negative_int(candidate)
        if value is not None:
            return value
    return None


def _provider_timeout_override(
    fetch: Any, env: str | None, provider_name: str
) -> int | None:
    """Determine if a specific timeout override exists for this provider."""
    candidates: list[str] = []
    custom_env = getattr(fetch, "_provider_timeout_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(provider_name)
    candidates.append(f"PROVIDER_TIMEOUT_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_TIMEOUT")
        candidates.append(f"PROVIDER_TIMEOUT_{base}")

    value = _resolve_provider_override(candidates)
    if value is None:
        return None
    # Security: clamp the per-provider override at the same Slowloris-defence
    # ceiling as the global PROVIDER_TIMEOUT. Without this cap an env override
    # such as PROVIDER_TIMEOUT_VOR=99999 would bypass feed_config.PROVIDER_TIMEOUT
    # (which is itself capped at MAX_PROVIDER_TIMEOUT) and let a sluggish or
    # attacker-controlled upstream peer stall a fetch for ~28 hours.
    return min(value, feed_config.MAX_PROVIDER_TIMEOUT)


def _provider_concurrency_key(fetch: Any, provider_name: str) -> str:
    """Return the key used to group providers for concurrency limits."""
    key = getattr(fetch, "_provider_concurrency_key", None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    return provider_name


def _provider_worker_limit(
    fetch: Any, env: str | None, provider_name: str, concurrency_key: str
) -> int | None:
    """Determine the maximum number of concurrent workers for this provider group."""
    candidates: list[str] = []
    custom_env = getattr(fetch, "_provider_max_workers_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(concurrency_key)
    candidates.append(f"PROVIDER_MAX_WORKERS_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_MAX_WORKERS")

    return _resolve_provider_override(candidates)


def _fetch_supports_timeout(fetch: Any) -> bool:
    """Check if the fetch callable accepts a 'timeout' argument."""
    try:
        signature = inspect.signature(fetch)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "timeout":
            return True
    return False


def _call_fetch_with_timeout(
    fetch: Any, timeout: int | float | None, supports_timeout: bool
) -> Any:
    """Invoke the fetch callable, passing the timeout if supported.

    Trusts the :func:`_fetch_supports_timeout` introspection result —
    pre-fix the ``except TypeError: return fetch()`` retry caught any
    TypeError raised from INSIDE the fetch body (e.g. an ``int(timeout)``
    conversion failure, a type-assertion error), then re-invoked the
    fetcher with no kwargs. The retry duplicated HTTP requests, side
    effects, quota debits, and the ``report.provider_started`` event
    sequence — masking the real internal error behind a doubled run.
    """
    if supports_timeout:
        return fetch(timeout=None if timeout is None else timeout)
    return fetch()


# Joins the words of a time line so a display never breaks it in two. A full
# NO-BREAK SPACE (U+00A0): the narrow one (U+202F) used until 2026-10-06 set
# the words so close on the TV that "[Ab Mi 07.10. bis 28.10.]" read almost
# as one word in the EasySignage preview of 06.10.
_NBSP = "\u00a0"
# Weekday abbreviations for the time line, Monday first like ``weekday()``.
_WEEKDAYS_DE: tuple[str, ...] = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
# Within this many days from today a date carries its weekday ("Mo 05.10.").
# Further out the weekday no longer helps anyone plan the next few days.
_TIME_LINE_WEEKDAY_DAYS = 6


def _time_line_day(when: datetime, today: date) -> str:
    """``when``'s day for the time line: "05.10.", "Mo 05.10.", "05.10.2027".

    The year appears only when it is not the current one, the weekday only
    for the coming week (see ``_TIME_LINE_WEEKDAY_DAYS``).
    """
    text = f"{when:%d.%m.}"
    if when.year != today.year:
        text += f"{when:%Y}"
    if 0 <= (when.date() - today).days <= _TIME_LINE_WEEKDAY_DAYS:
        text = f"{_WEEKDAYS_DE[when.weekday()]} {text}"
    return text


def _plausible_end(
    start_local: datetime | None, end_local: datetime | None, now_local: datetime
) -> datetime | None:
    """``end_local``, or ``None`` when it lies before the start or absurdly far out.

    "Absurdly far" is more than ``ABSOLUTE_MAX_AGE_DAYS`` (default 540) past
    today, or past the start when that is still ahead. Measured from the
    start alone, a long-running item lost a near, plausible end: "N8:
    Thaliastraße U" read "Seit 24.07.2024" on 2026-10-02 although WL named
    16.11.2026. (That end is WL's 11:11 expiry date, and since 2026-10-08
    the line leaves it out on purpose, see
    :func:`src.providers.wl_plausibility.end_unknown`; the rule stays for
    real ends.)
    """
    if end_local is None:
        return None
    # Compared in UTC: two datetimes in the same zone compare by wall clock
    # (see :func:`format_local_times`).
    if start_local is not None and _to_utc(end_local) < _to_utc(start_local):
        log.warning("Enddatum liegt vor Startdatum")
        return None
    anchor = max(start_local, now_local, key=_to_utc) if start_local is not None else now_local
    max_span_days = feed_config.ABSOLUTE_MAX_AGE_DAYS
    if (_to_utc(end_local) - _to_utc(anchor)).days > max_span_days:
        log.warning(
            "Enddatum liegt mehr als %s Tage nach Beginn bzw. heute. Setze Enddatum auf None.",
            max_span_days,
        )
        return None
    return end_local


def format_local_times(
    start: datetime | None,
    end: datetime | None,
    now: datetime | None = None,
    *,
    since: datetime | None = None,
) -> str:
    """The time line of an item, phrased for someone reading it today.

    Words are joined with NO-BREAK SPACE (U+00A0), never a plain space:
    the line must not wrap in the middle on a display.

    The display question is "does this apply now, and until when?", so the
    line says what matters for today instead of two full dates (operator
    decision 2026-10-02)::

        Heute                    running, ends today
        Bis Sa 03.10.            running, ends later (start is past, irrelevant)
        Seit 30.09.              running, no end
        Am So 04.10.             one day, not today
        Ab Mo 05.10. bis 11.11.  begins later
        Ab Mo 05.10.             begins later, no end
        Bis 16.11.               no start

    "Heute" is relative: the feed is rebuilt every 30 minutes, and an item
    that ended yesterday has left the feed by the first build after
    midnight. No clock time for an end: WL's own ends for incidents are
    often exactly one hour after the start, a default rather than a forecast.

    ``since`` is the begin of an unplanned disruption (see
    :func:`_incident_since`). When it lies earlier today, the line says
    when it began, so a reader can tell how old the incident is (operator
    wish 2026-10-03)::

        Seit 10:37               incident since 10:37 today, ends today or open

    An incident that began on an earlier day, or that its source expects to
    last beyond today, keeps the lines above.

    Days and clock times are Vienna's, the order of two moments is not:
    Python compares two datetimes in the same zone by their wall clock and
    ignores the offset. In the hour that repeats when summer time ends
    (25.10.2026, 02:00 to 03:00 twice), an accident at 02:26 summer time
    read as later than the build at 02:01 winter time, 35 minutes after it,
    and the line fell back to "Heute" (a real night shifted onto that date).
    Moments are therefore compared in UTC.
    """
    now_local = _to_utc(now).astimezone(_VIENNA_TZ) if now else datetime.now(_VIENNA_TZ)
    today = now_local.date()
    start_local = _to_utc(start).astimezone(_VIENNA_TZ) if isinstance(start, datetime) else None
    end_local = _to_utc(end).astimezone(_VIENNA_TZ) if isinstance(end, datetime) else None
    end_local = _plausible_end(start_local, end_local, now_local)
    since_local = _to_utc(since).astimezone(_VIENNA_TZ) if isinstance(since, datetime) else None
    if (
        since_local is not None
        and since_local.date() == today
        and _to_utc(since_local) <= _to_utc(now_local)
        and (end_local is None or end_local.date() == today)
    ):
        return f"Seit {since_local:%H:%M}".replace(" ", _NBSP)
    return _time_line_text(start_local, end_local, today).replace(" ", _NBSP)


def _scheduled_clock(when: datetime) -> bool:
    """Whether ``when`` sits on a full hour, the mark of a scheduled entry.

    WL's system switches pre-entered measures on at the full hour, a few
    seconds late ("12: Betrieb ab Franz-Josefs-Bahnhof" 04:00:16, "N41:
    Busse halten …" 01:00:15), and display tickers reused for a new
    incident keep such a start from days before ("10A: Fahrtbehinderung"
    with 09.07. 00:00:25 on 11.07.). An incident begins on a full hour about
    once in sixty; it then keeps its previous line.
    """
    return _to_utc(when).astimezone(_VIENNA_TZ).minute == 0


# Sources whose start is measured, not entered by hand: the Stammstrecke
# monitor (``src.feed.stammstrecke.EVENT_SOURCE``) dates an episode by the
# first delayed departure, and a departure at the full hour is no schedule.
_MEASURED_SOURCES: frozenset[str] = frozenset({"VOR/VAO"})


def _first_published(it: FeedItem, entry: dict[str, Any] | None) -> datetime | None:
    """The minute ÖBB first published the message ``it``, kept in ``entry``.

    ÖBB re-publishes a message under the same GUID with every update, and
    the ``pubDate`` moves with it: "Wien Floridsdorf ↔ Wien Praterstern"
    (Weichenstörung, 27.09.2026) came at 15:48, 17:26 and 20:07, as did 21
    of the 33 ÖBB disruptions cached from 12.09. to 02.10.2026. The earliest
    ``pubDate`` seen is stored in the item's state entry as
    ``first_published``. An entry from before that field starts from the
    earlier of ``pubDate`` and ``first_seen``: the build saw the message no
    earlier than ÖBB published it. ``None`` for other sources.
    """
    if it.get("source") != _OEBB_SOURCE:
        return None
    pub = _parse_datetime(it.get("pubDate"))
    if not isinstance(pub, datetime):
        return None
    earliest = _to_utc(pub)
    if entry is None:
        return earliest
    known = _parse_state_time(entry, "first_published")
    if known is None:
        known = _parse_state_time(entry, "first_seen")
    if known is not None and known < earliest:
        earliest = known
    entry["first_published"] = earliest.isoformat()
    return earliest


def _incident_since(
    it: FeedItem, starts_at: datetime | None, first_published: datetime | None = None
) -> datetime | None:
    """When the unplanned disruption ``it`` began, or ``None``.

    Every source files its disruptions under "Störung": WL ``trafficInfos``,
    ÖBB and the Stammstrecke monitor. Planned ones
    (:data:`_PLANNED_DISRUPTION_RE`) and reports of a disruption that is
    already over (:func:`_reports_past_disruption`) do not qualify.

    One WL item often bundles several WL messages about the incident.
    ``pubDate`` is the earliest of them, ``starts_at`` the latest
    (``wl_fetch`` keeps the latest so a reused stale ticker cannot date the
    item back). On 2026-10-03 "86A/87A/95A: Fahrtbehinderung wegen
    Rettungseinsatz" carried ``pubDate`` 10:37:00 — the minute WL entered
    the incident — and per-line tickers from 10:42:44 to 10:54:13 as
    ``starts_at``. The earliest time is the begin, as long as it is from the
    same Vienna day and not a scheduled full hour (:func:`_scheduled_clock`),
    the marks of the stale tickers seen since July. ÖBB's begin is the
    minute it first published the message, ``first_published``
    (:func:`_first_published`): its ``starts_at`` is the date the
    message's validity begins (00:00, since 2026-09-12), and its
    ``pubDate`` moves on with every update; a full hour counts there too.
    The Stammstrecke monitor's begin is the first delayed departure it measured
    (:data:`_MEASURED_SOURCES`).
    """
    if it.get("category") != "Störung" or not isinstance(starts_at, datetime):
        return None
    text = f"{it.get('title') or ''} {html_to_text(str(it.get('description') or ''))}"
    if _PLANNED_DISRUPTION_RE.search(text) or _reports_past_disruption(it, text):
        return None
    if it.get("source") in _MEASURED_SOURCES:
        return starts_at
    pub_date = _parse_datetime(it.get("pubDate"))
    if it.get("source") == _OEBB_SOURCE and isinstance(pub_date, datetime):
        # No full-hour rule here: it marks WL's pre-entered measures, and
        # ÖBB publishes by hand. The two ÖBB disruptions since July first
        # published on a full hour were real incidents ("Wien Meidling ↔ Wien
        # Liesing", Polizeieinsatz, 03.10.2026 20:00:01), and that one read
        # "Heute" while the line was closed.
        return first_published or pub_date
    pub_date = incident_begin(pub_date, _wl_incident_start(it))
    since = starts_at
    if (
        isinstance(pub_date, datetime)
        and pub_date <= starts_at
        and _to_utc(pub_date).astimezone(_VIENNA_TZ).date()
        == _to_utc(starts_at).astimezone(_VIENNA_TZ).date()
        and not _scheduled_clock(pub_date)
    ):
        since = pub_date
    return None if _scheduled_clock(since) else since


# ÖBB reports a disruption that is already over in the past tense: "Wegen
# eines Polizeieinsatzes waren in Mödling Bahnhof bis 19:55 Uhr keine Fahrten
# möglich", published 19:57. 60 of the 193 ÖBB disruptions cached since July
# are such reports or an "Aufhebung"; "Seit 19:57" would claim the opposite.
_PAST_DISRUPTION_RE: re.Pattern[str] = re.compile(
    r"\bwaren\b.{0,160}?\bbis\s+\d{1,2}[:.]\d{2}\s*Uhr"
)


def _reports_past_disruption(it: FeedItem, text: str) -> bool:
    """Whether ``it`` reports the end of a disruption rather than a running one."""
    return _is_all_clear(it) or _PAST_DISRUPTION_RE.search(text) is not None


def _time_line_text(start_local: datetime | None, end_local: datetime | None, today: date) -> str:
    """The words of :func:`format_local_times`, joined with plain spaces."""

    if start_local is not None and start_local.date() > today:
        first = _time_line_day(start_local, today)
        if end_local is None:
            return f"Ab {first}"
        if end_local.date() == start_local.date():
            return f"Am {first}"
        return f"Ab {first} bis {_time_line_day(end_local, today)}"
    if end_local is not None:
        if end_local.date() == today:
            return "Heute"
        if start_local is not None and start_local.date() == end_local.date():
            return f"Am {_time_line_day(end_local, today)}"
        return f"Bis {_time_line_day(end_local, today)}"
    if start_local is not None:
        if start_local.date() == today:
            return "Seit heute"
        return f"Seit {_time_line_day(start_local, today)}"
    return ""

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


def _parse_lines_from_title(title: str) -> list[str]:
    m = _LINE_PREFIX_RE.match(title or "")
    if not m:
        return []

    tokens: list[str] = []
    for raw in m.group(1).split("/"):
        token = raw.strip()
        if not token:
            continue
        normalized = token.upper()
        if _LINE_TOKEN_RE.match(normalized):
            tokens.append(normalized)
    return tokens


# ---------------- State (first_seen) ----------------


# Security: defense-in-depth byte-size cap on the on-disk state file.
# The file is a small JSON object (one entry per first_seen item, each
# ~80 bytes) — production state.json is ~40 KiB. The depth-bomb catch
# tuple ``except (FileNotFoundError, json.JSONDecodeError)`` covers the
# deeply-nested attack via ``RecursionError`` (re-raises into the broad
# ``except Exception`` below), but a wide-but-flat attack such as
# ``[0]*50_000_000`` slips past both handlers entirely:
#  * ``json.load`` does NOT raise ``RecursionError`` on a flat list
#    regardless of length.
#  * ``handle.read()`` (called internally by ``json.load(handle)``)
#    buffers the whole file before parsing — a 1 GiB file allocates a
#    1 GiB string plus ~5 GiB worth of object overhead.
#  * ``MemoryError`` is a ``BaseException`` subclass — it is NOT caught
#    by any ``except Exception`` handler, so the orchestrator crashes
#    BEFORE any provider runs and the feed-build cron leaves partial
#    state with no recovery path.
# Threat model mirrors ``MAX_CACHE_FILE_BYTES`` in ``src/utils/cache.py``:
# compromised CI runner / corrupted previous run / partial flush + power
# loss plants a multi-MiB-to-multi-GiB file at ``data/first_seen.json``.
# 50 MiB is ~1200x the production state file and bounds the worst-case
# parse cost well below any cron runner's standard 1 GiB cgroup limit.
MAX_STATE_FILE_BYTES = 50 * 1024 * 1024


def _load_state() -> dict[str, dict[str, Any]]:
    path = validate_path(feed_config.STATE_FILE, "STATE_PATH")
    try:
        lock_path = path.with_suffix(".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            with file_lock(lock_file, exclusive=False):
                # Security: byte-size cap (see MAX_STATE_FILE_BYTES)
                # defeats the wide-but-flat size-bomb attack that the
                # depth-bomb / json.JSONDecodeError catch below does NOT
                # cover. Open first, then ``os.fstat`` — closes the
                # TOCTOU between ``stat`` and ``open`` that lets a
                # parallel ``_save_state`` (atomic_write rename) or a
                # symlink swap bypass the cap.
                # ``read(MAX_STATE_FILE_BYTES + 1)`` defends against
                # zero-st_size special files.
                # Security (Path-Log Sibling Drift, sibling of PR #1456):
                # ``feed_config.STATE_FILE`` is operator-controlled via
                # the ``STATE_PATH`` environment variable. Pre-fix the
                # WARNING log lines below interpolated ``path`` verbatim
                # via the bare ``%s`` format spec; a hostile path name
                # (Trojan-Source RLO, zero-width, 8-bit C1, Tag block,
                # Variation Selectors, newline log-forgery, ANSI ESC)
                # flowed into the operator log + the public
                # ``docs/feed_health.json`` artefact + the GitHub-Issue
                # auto-submission. Post-fix the SHA-256 fingerprint
                # (truncated to 12 hex chars, Trojan-Source-clean,
                # CodeQL-recognised barrier) replaces the path bytes
                # at the interpolation boundary. Mirrors the canonical
                # fix shape pinned in :func:`src.utils.files.read_capped_json`.
                path_fingerprint = hashlib.sha256(
                    str(path).encode("utf-8", errors="replace")
                ).hexdigest()[:12]
                with path.open("rb") as f:
                    if os.fstat(f.fileno()).st_size > MAX_STATE_FILE_BYTES:
                        log.warning(
                            "State-Datei [path-sha256=%s] ist zu groß "
                            "(> %d Bytes); starte mit leerem State.",
                            path_fingerprint, MAX_STATE_FILE_BYTES,
                        )
                        return {}
                    raw_bytes = f.read(MAX_STATE_FILE_BYTES + 1)
                    if len(raw_bytes) > MAX_STATE_FILE_BYTES:
                        log.warning(
                            "State-Datei [path-sha256=%s] überschreitet "
                            "%d Bytes beim Lesen; starte mit leerem State.",
                            path_fingerprint, MAX_STATE_FILE_BYTES,
                        )
                        return {}
                    # Security (reader-side non-finite literal defence,
                    # symmetric to the Round 1488 writer-side
                    # ``allow_nan=False`` pin at ``_save_state``). A
                    # planted ``NaN`` / ``Infinity`` / ``-Infinity`` /
                    # ``1e1000`` in ``data/first_seen.json`` (compromised
                    # CI runner, parallel orchestrator atomic state swap,
                    # partial flush + power loss, hostile PR landing a
                    # tampered fixture) would otherwise propagate silently
                    # as ``float('nan')`` / ``float('inf')`` through the
                    # state dict — every ``first_seen`` comparison against
                    # ``retention_cutoff`` (``fs_utc < retention_cutoff``)
                    # silently misbehaves on NaN (``False`` for every
                    # comparison) AND the round-trip back to
                    # ``_save_state`` hits the ``allow_nan=False`` pin
                    # and crashes the cron mid-write. The hooks raise
                    # ``json.JSONDecodeError`` which the ``except
                    # (FileNotFoundError, json.JSONDecodeError)`` handler
                    # below catches — fall through to the empty-state
                    # recovery path, consistent with every other
                    # corrupt-state recovery in this loader.
                    data = json.loads(
                        raw_bytes,
                        parse_constant=_reject_non_finite_constant,
                        parse_float=_reject_non_finite_float,
                    )
        data = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        # Security (Clear-Text-Logging Drift): broad ``Exception`` catch
        # may surface third-party / custom-subclass exceptions whose
        # ``__str__`` carries control bytes.  Sanitise.
        log.warning(
            "State laden fehlgeschlagen (%s) – starte leer.",
            sanitize_log_arg(str(e)),
        )
        return {}

    retention_cutoff: datetime | None = None
    if feed_config.STATE_RETENTION_DAYS > 0:
        now_utc = _to_utc(datetime.now(UTC))
        retention_cutoff = now_utc - timedelta(days=feed_config.STATE_RETENTION_DAYS)

    out: dict[str, dict[str, Any]] = {}
    for ident, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            raw_first_seen = entry.get("first_seen", "")
            fs_dt = datetime.fromisoformat(str(raw_first_seen))
            if fs_dt.tzinfo is None:
                fs_dt = fs_dt.replace(tzinfo=UTC)
            fs_utc = _to_utc(fs_dt)
        except Exception:
            log.warning(
                "State-Eintrag %s hat unparsebares first_seen: %r", ident, entry.get("first_seen")
            )
            fs_dt = datetime.now(UTC)
            fs_utc = _to_utc(fs_dt)

        if retention_cutoff and fs_utc < retention_cutoff:
            log.debug(
                "State-Eintrag %s älter als %s Tage – entferne Eintrag.",
                ident,
                feed_config.STATE_RETENTION_DAYS,
            )
            continue

        new_entry = dict(entry)
        new_entry["first_seen"] = fs_utc.isoformat()
        out[ident] = new_entry
    return out


def _read_state_capped(path: Path) -> dict[str, dict[str, Any]]:
    """Read existing state under the byte-size cap, returning ``{}`` on
    any failure mode (missing/oversized/invalid).

    Security: open-then-fstat closes the TOCTOU between the cap check
    and ``open()`` that lets a parallel writer (atomic_write rename)
    or a symlink swap bypass the cap mid-merge. ``read(MAX + 1)``
    defends against zero-st_size special files (FIFOs, ``/dev/zero``).
    """
    # Security (Path-Log Sibling Drift, sibling of PR #1456): see
    # ``_load_state`` for the rationale of fingerprinting the path
    # bytes rather than interpolating them verbatim into the WARNING
    # log lines below.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        with path.open("rb") as f:
            if os.fstat(f.fileno()).st_size > MAX_STATE_FILE_BYTES:
                log.warning(
                    "State-Datei [path-sha256=%s] ist zu groß "
                    "(> %d Bytes); überschreibe State.",
                    path_fingerprint, MAX_STATE_FILE_BYTES,
                )
                return {}
            raw_bytes = f.read(MAX_STATE_FILE_BYTES + 1)
            if len(raw_bytes) > MAX_STATE_FILE_BYTES:
                log.warning(
                    "State-Datei [path-sha256=%s] überschreitet "
                    "%d Bytes; überschreibe State.",
                    path_fingerprint, MAX_STATE_FILE_BYTES,
                )
                return {}
            # Security: mirror the parse_constant + parse_float hooks
            # pinned at ``_load_state`` (Round 1503). The sibling reader
            # ``_read_state_capped`` reads the SAME ``data/first_seen.json``
            # state file on the save path (called from ``_save_state`` to
            # merge with existing state) and faces the identical
            # threat model — a planted NaN / Infinity / 1e1000 literal
            # propagates as ``float('nan')`` / ``float('inf')`` through
            # the merge logic and round-trip-crashes the writer pin
            # (Round 1485) at the next ``allow_nan=False`` serialisation.
            existing = json.loads(
                raw_bytes,
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
            return existing if isinstance(existing, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # Security (Clear-Text-Logging Drift): broad ``Exception`` catch
        # — sanitise the bound name before WARNING-level emission.
        log.warning(
            "State-Merge fehlgeschlagen (Lesefehler: %s) – überschreibe State.",
            sanitize_log_arg(str(exc)),
        )
        return {}


def _prune_expired_merged_state(
    merged_state: dict[str, dict[str, Any]], state: dict[str, dict[str, Any]]
) -> None:
    """Drop resurrected, retention-expired entries from ``merged_state`` in place.

    ``_load_state`` drops expired entries from the in-memory working set, but
    ``_save_state`` re-reads the raw on-disk file via ``_read_state_capped`` and
    merges it — so an entry this run no longer tracks (another run's items,
    parallel-writer leftovers) is otherwise resurrected and re-written every
    cycle, growing the file until it trips ``MAX_STATE_FILE_BYTES`` and the
    loader wipes *all* first_seen tracking at once. Prune only entries NOT in
    this run's ``state``: those are already within retention via ``_load_state``
    and must keep their freshly written first_seen for cross-run dedup /
    parallel-writer safety (a recent parallel-writer entry has a recent
    first_seen and is kept; unparseable first_seen -> kept).
    """
    if feed_config.STATE_RETENTION_DAYS <= 0:
        return
    retention_cutoff = _to_utc(datetime.now(UTC)) - timedelta(
        days=feed_config.STATE_RETENTION_DAYS
    )
    for ident in list(merged_state.keys()):
        if ident in state:
            continue
        entry = merged_state.get(ident)
        if not isinstance(entry, dict):
            continue
        fs_utc = _parse_first_seen(entry, None)
        if fs_utc is not None and fs_utc < retention_cutoff:
            merged_state.pop(ident, None)


def _write_merged_state(
    path: Path, state: dict[str, dict[str, Any]], deletions: set[str] | None
) -> None:
    """Merge ``state`` into the on-disk state file and atomically rewrite it.

    The caller MUST hold the exclusive state lock. Reads the existing file under
    the byte-size cap, layers this run's ``state`` on top, drops ``deletions``,
    prunes retention-expired survivors, then writes via ``atomic_write``.
    """
    # Safe merge: read existing state to avoid overwriting parallel updates.
    # Security: open-then-fstat closes the TOCTOU between the size cap and
    # ``open``. ``read(MAX + 1)`` defends against zero-st_size special files.
    # See _load_state.
    merged_state = _read_state_capped(path)
    merged_state.update(state)
    if deletions:
        for k in deletions:
            merged_state.pop(k, None)
    # Prune resurrected, retention-expired entries so the on-disk state file
    # cannot grow until it trips ``MAX_STATE_FILE_BYTES`` and the loader wipes
    # *all* first_seen tracking at once.
    _prune_expired_merged_state(merged_state, state)
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o600) as f:
        # Security (Trojan-Source / BiDi-Mark Drift Round 10):
        # ``ensure_ascii=True`` escapes every non-ASCII code point as a literal
        # ``\uXXXX`` sequence. The state dict's KEYS carry feed-item identities
        # computed by ``_identity_for_item`` — the WL/non-OEBB fallback branches
        # embed the raw provider title verbatim. A planted upstream title
        # carrying U+202E (RIGHT-TO-LEFT OVERRIDE) / zero-width / Unicode
        # line-separator / 8-bit C1 bytes would otherwise reach
        # ``data/first_seen.json`` — a file committed to ``main`` by
        # ``build-feed.yml`` — as raw UTF-8, triggering BiDi reversal in any
        # ``cat`` / ``less`` / GitHub web UI / IDE viewer. Mirrors the canonical
        # fix shape pinned in PR #1434 for ``_write_quarantine_file``. Forensic
        # intent is preserved (``json.loads`` recovers the original bytes from
        # the literal escape sequence).
        #
        # Security (Coordinate finite/range drift, committed-writer
        # defence-in-depth): ``allow_nan=False`` mirrors the canonical writer-side
        # pin (Round 1485, :func:`src.places.merge.write_stations`). A planted
        # ``NaN`` / ``Infinity`` literal in a previous-run ``data/first_seen.json``
        # survives the ``json.loads`` round-trip and re-writes verbatim without
        # the pin. ``ensure_ascii=True`` already blocks Trojan-Source primitives;
        # ``allow_nan=False`` closes the sibling RFC-8259 non-conformance drift.
        json.dump(merged_state, f, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)


def _save_state(state: dict[str, dict[str, Any]], deletions: set[str] | None = None) -> None:
    path = validate_path(feed_config.STATE_FILE, "STATE_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Separate Lock-Datei vermeidet Permission-Fehler unter Windows, wenn
    # atomic_write die Zieldatei austauscht. Die Lock-Datei wird bewusst NICHT
    # nach Gebrauch entfernt: andernfalls entstünde eine Race, in der Prozess A
    # die Datei nach dem Lock-Release unlinkt, während die parallel laufenden
    # Prozesse B und C den Pfad bereits geöffnet haben und auf separaten Inodes
    # locken — wodurch flock sich gegenseitig nicht mehr sieht. Eine persistente
    # Lock-Datei (~0 Bytes) kostet nichts und ist zwischen Läufen wiederverwendbar.
    lock_path = path.with_suffix(".lock")
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            with file_lock(lock_file, exclusive=True):
                _write_merged_state(path, state, deletions)
    except (OSError, TimeoutError) as exc:
        # Security: ``file_lock(..., exclusive=True)`` re-raises on
        # acquisition failure (timeout under contention, fcntl ENOLCK,
        # NFS hiccup, …). Skipping the state save here is the
        # fail-closed-but-recoverable choice: losing one update means
        # newly-arrived items get a stale ``first_seen`` next run, but
        # *overwriting* a parallel writer's update would corrupt the
        # cross-run dedup invariant for *every* item they tracked. The
        # next successful run reconciles via the merge step above.
        # Security (Clear-Text-Logging Drift): the bound exception (an
        # OSError or TimeoutError from the lock helper) may surface a
        # custom ``__str__`` carrying control bytes — sanitise.
        # Security (Path-Log Sibling Drift, sibling of PR #1456): see
        # ``_load_state`` for the rationale of fingerprinting the path
        # bytes rather than interpolating them verbatim.
        path_fingerprint = hashlib.sha256(
            str(path).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        log.warning(
            "State-Datei [path-sha256=%s] konnte nicht gesperrt werden "
            "(%s) – Update wird übersprungen, nächster Lauf merged frisch.",
            path_fingerprint,
            sanitize_log_arg(str(exc)),
        )


def _identity_for_item(item: FeedItem) -> str:
    """
    Stabile Identität unabhängig von Titel-Kosmetik.
      - Wenn Provider _identity liefert: diese bevorzugen.
      - ÖBB: GUID/Link (vom RSS stabil).
      - WL/sonstige: Quelle|Kategorie|Linienpräfix + Start-YYYY-MM-DD.
    """
    if item.get("_identity"):
        return str(item["_identity"])

    if "_calculated_identity" in item:
        return item["_calculated_identity"]

    title = item.get("title") or ""
    sa = item.get("starts_at")
    ea = item.get("ends_at")
    sa_str = _to_utc(sa).isoformat() if isinstance(sa, datetime) else "None"
    ea_str = _to_utc(ea).isoformat() if isinstance(ea, datetime) else "None"
    fuzzy_raw = f"{title}|{sa_str}|{ea_str}"
    # ``errors="surrogatepass"`` (here and the two ``json.dumps`` hashes below):
    # a lone surrogate (U+D800-U+DFFF, reachable via a ``\uD800`` escape in an
    # upstream JSON title) would otherwise raise ``UnicodeEncodeError`` at this
    # identity-hash encode — BEFORE ``_sanitize_text`` strips it from the
    # rendered title — aborting the whole build. ``surrogatepass`` lets the
    # hash encode the bytes deterministically; the hash value is unchanged for
    # every surrogate-free input (the normal case), so no first_seen churn.
    fuzzy_hash = hashlib.sha256(
        fuzzy_raw.encode("utf-8", "surrogatepass")
    ).hexdigest()

    result: str
    source = (item.get("source") or "").lower()
    category = (item.get("category") or "").lower()
    if "öbb" in source or "oebb" in source:
        guid_or_link = item.get("guid") or item.get("link")
        if guid_or_link:
            result = f"oebb|{guid_or_link}"
        else:
            result = f"oebb|F={fuzzy_hash}"
    else:
        lines = _parse_lines_from_title(title)
        lines_part = "L=" + "/".join(lines) if lines else "L="
        start_day = _ymd_or_none(sa)
        base = f"{source}|{category}|{lines_part}|D={start_day}"
        if source and category:
            if not lines:
                if item.get("title"):
                    result = f"{base}|T={item['title']}|F={fuzzy_hash}"
                else:
                    raw = json.dumps(item, sort_keys=True, default=str)
                    hashed = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
                    result = f"{base}|H={hashed}|F={fuzzy_hash}"
            else:
                result = f"{base}|F={fuzzy_hash}"
        # Fallback: Ohne Quelle/Kategorie Titel oder vollständigen Hash anhängen
        elif item.get("title"):
            result = f"{base}|T={item['title']}|F={fuzzy_hash}"
        else:
            raw = json.dumps(item, sort_keys=True, default=str)
            hashed = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
            result = f"{base}|H={hashed}|F={fuzzy_hash}"

    item["_calculated_identity"] = result
    return result

# ---------------- Pipeline ----------------


class _ProviderBuckets(NamedTuple):
    cache_fetchers: list[Any]
    network_fetchers: list[Any]
    provider_names: dict[Any, str]
    provider_envs: dict[Any, str | None]


def _categorize_providers(report: RunReport) -> _ProviderBuckets:
    """Walk PROVIDERS + plugin entrypoints and split enabled fetchers into
    cache-backed (sync) and network-backed (async) buckets, registering each
    with the report so disabled providers still appear in the health output.
    """
    cache_fetchers: list[Any] = []
    network_fetchers: list[Any] = []
    provider_names: dict[Any, str] = {}
    provider_envs: dict[Any, str | None] = {}

    provider_entries = list(PROVIDERS)
    providers_overridden = list(PROVIDERS) != list(DEFAULT_PROVIDERS)
    if provider_entries:
        if not providers_overridden:
            known_envs = {env for env, _ in provider_entries}
            for spec in iter_providers():
                if spec.env_var not in known_envs:
                    provider_entries.append((spec.env_var, spec.loader))
    else:
        provider_entries = [(spec.env_var, spec.loader) for spec in iter_providers()]

    for env, fetch in provider_entries:
        provider_name = _provider_display_name(fetch, env)
        enabled = bool(feed_config.get_bool_env(env, True))
        fetch_type = "cache" if getattr(fetch, "_provider_cache_name", None) else "network"
        report.register_provider(provider_name, enabled, fetch_type)
        if not enabled:
            continue
        provider_names[fetch] = provider_name
        provider_envs[fetch] = env
        if getattr(fetch, "_provider_cache_name", None):
            cache_fetchers.append(fetch)
        else:
            network_fetchers.append(fetch)

    return _ProviderBuckets(cache_fetchers, network_fetchers, provider_names, provider_envs)


def _report_empty_provider(
    fetch: Any,
    provider_name: str,
    report: RunReport,
    cache_alerts: dict[str, list[str]],
) -> None:
    """Record a provider that returned zero items.

    Two different things wear the same shape here, and conflating them is the
    defect this splits apart (audit 2026-09-12, Befund 6):

    * A cache-backed provider with nothing in it has a problem — the cache
      should hold data and does not. That stays a ``WARNING`` plus an entry in
      the run report's warning list, and the detail carries whatever the cache
      alerts say went wrong.
    * The Stammstrecke provider with nothing to report is the S-Bahn trunk
      line running normally. Most builds look like this. Logging it as a
      warning every 30 minutes made "working" indistinguishable from "broken"
      on the dashboard and buried the warnings that mean something.
      The Stadt-Wien-Baustellen are the same since 2026-10-06: the cache
      holds only sites with an effect on public transport, often none. A
      cache alert (file missing, unreadable, stale) still makes either one
      a warning.

    Which one a provider is, is declared at registration
    (``register_provider(..., empty_is_normal=True)``) rather than guessed
    here from its name, so a new provider has to state its own contract and
    the safe default applies until it does.

    The distinction survives into the run-report summary as a status of its
    own (``ok-empty`` vs ``empty``): a dashboard that only sees ``:empty``
    cannot tell "nothing to report" from "no data".
    """
    cache_name = getattr(fetch, "_provider_cache_name", None)
    alerts = cache_alerts.get(str(cache_name), []) if cache_name is not None else []
    # A cache alert (file missing, unreadable, too large) means no data, even
    # for a provider whose empty answer is normally healthy.
    if getattr(fetch, "_provider_empty_is_normal", False) and not alerts:
        log.info(
            "Provider '%s' meldet keine Vorfälle – Normalzustand, kein Fehler.",
            provider_name,
        )
        report.provider_success(
            provider_name,
            items=0,
            status="ok-empty",
            detail="Keine Vorfälle",
        )
        return

    log.warning(
        "Cache für Provider '%s' leer – generiere Feed ohne aktuelle Daten.",
        provider_name,
    )
    detail = "Keine aktuellen Daten"
    if alerts:
        detail = "; ".join(dict.fromkeys(alerts))
    report.provider_success(
        provider_name, items=0, status="empty", detail=detail
    )
    report.add_warning(f"Provider {provider_name}: {detail}")


def _run_cache_fetchers(
    cache_fetchers: list[Any],
    provider_names: dict[Any, str],
    items: list[FeedItem],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Sequentially invoke each cache-backed fetcher and feed its result through
    ``merge_result``. Cache fetchers are sync because they read from disk; no
    timeout / executor machinery applies."""
    for fetch in cache_fetchers:
        name = getattr(fetch, "__name__", str(fetch))
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        report.provider_started(provider_name)
        result: list[FeedItem] | None = None
        try:
            result = fetch()
        except Exception as exc:
            log.exception("%s fetch fehlgeschlagen: %s", name, exc)
            report.provider_error(provider_name, f"Fetch fehlgeschlagen: {exc}")
            continue
        if result is not None:
            merge_result(fetch, result, provider_name)


def _build_run_fetch(
    fetch: Any,
    effective_timeout: int | float,
    supports_timeout: bool,
    semaphore: BoundedSemaphore | None,
    provider_name: str,
) -> Any:
    """Wrap a network fetcher with semaphore-aware timeout accounting. The
    returned closure is what gets submitted to the ThreadPoolExecutor; it
    enforces both the per-call timeout and (when a concurrency limit applies)
    a semaphore acquisition timeout that subtracts wait time from the budget,
    preventing thread starvation under provider deadlock.
    """
    def _run_fetch() -> Any:
        timeout_arg = effective_timeout if effective_timeout >= 0 else None

        if semaphore is None:
            return _call_fetch_with_timeout(fetch, timeout_arg, supports_timeout)

        # Prevent thread starvation by enforcing timeout on semaphore acquisition.
        # If the provider is deadlocked/overloaded, we don't block the executor
        # worker forever.
        start_wait = perf_counter()

        # If effective_timeout < 0, fallback to global timeout + buffer to avoid infinite block
        sem_timeout = effective_timeout if effective_timeout >= 0 else (feed_config.PROVIDER_TIMEOUT + 5.0)

        acquired = semaphore.acquire(timeout=sem_timeout)
        if not acquired:
            raise TimeoutError(f"Semaphore acquisition timed out after {sem_timeout}s")

        # Subtract wait time from timeout
        try:
            elapsed = perf_counter() - start_wait
            remaining_timeout = timeout_arg - elapsed if timeout_arg is not None else None

            if remaining_timeout is not None and remaining_timeout <= 0:
                raise TimeoutError(
                    f"Semaphore acquisition took {elapsed:.2f}s, no realistic time left for fetch (threshold: <= 0s)"
                )

            return _call_fetch_with_timeout(fetch, remaining_timeout, supports_timeout)
        finally:
            semaphore.release()

    return _run_fetch


def _submit_network_fetches(
    executor: ThreadPoolExecutor,
    network_fetchers: list[Any],
    provider_names: dict[Any, str],
    provider_envs: dict[Any, str | None],
    report: RunReport,
) -> tuple[
    dict[Any, tuple[Any, str, int]],
    dict[Any, float | None],
    set[Any],
]:
    """Submit each network fetcher to the executor with its timeout/semaphore
    config, returning (futures-meta, deadlines, pending-set)."""
    futures: dict[Any, tuple[Any, str, int]] = {}
    deadlines: dict[Any, float | None] = {}
    pending: set[Any] = set()

    # Pre-compute the per-group worker limit across ALL fetchers BEFORE
    # the main submit loop. Pre-fix the loop only registered a
    # semaphore when the CURRENT fetcher's own per-provider env-limit
    # was set, so a sibling provider in the same ``concurrency_key``
    # group without its own env override ran unbounded — silently
    # defeating the operator-intended shared cap. If two group members
    # set different positive limits, the tighter one (``min``) wins so
    # the group respects every member's stated upper bound.
    group_limits: dict[str, int] = {}
    for fetch in network_fetchers:
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        env_name = provider_envs.get(fetch)
        concurrency_key = _provider_concurrency_key(fetch, provider_name)
        worker_limit = _provider_worker_limit(
            fetch, env_name, provider_name, concurrency_key
        )
        if worker_limit is not None and worker_limit > 0:
            current = group_limits.get(concurrency_key)
            group_limits[concurrency_key] = (
                min(current, worker_limit) if current is not None else worker_limit
            )
    semaphores: dict[str, BoundedSemaphore] = {
        key: BoundedSemaphore(limit) for key, limit in group_limits.items()
    }

    for fetch in network_fetchers:
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        env_name = provider_envs.get(fetch)
        timeout_override = _provider_timeout_override(fetch, env_name, provider_name)
        effective_timeout = (
            timeout_override if timeout_override is not None else feed_config.PROVIDER_TIMEOUT
        )
        concurrency_key = _provider_concurrency_key(fetch, provider_name)
        worker_limit = _provider_worker_limit(
            fetch, env_name, provider_name, concurrency_key
        )
        # Every member of a group with a positive limit picks up the
        # pre-computed shared semaphore — including members whose OWN
        # ``worker_limit`` resolved to ``None``.
        semaphore: BoundedSemaphore | None = semaphores.get(concurrency_key)
        if timeout_override is not None:
            log.debug(
                "Provider %s nutzt Timeout-Override von %ss",
                provider_name,
                timeout_override,
            )
        if worker_limit is not None and worker_limit > 0:
            log.debug(
                "Provider %s begrenzt Worker auf %s (Schlüssel %s)",
                provider_name,
                worker_limit,
                concurrency_key,
            )
        supports_timeout = _fetch_supports_timeout(fetch)

        if effective_timeout == 0:
            report.provider_started(provider_name)
            name = getattr(fetch, "__name__", str(fetch))
            log.error("%s fetch Timeout nach 0s", name)
            report.provider_error(provider_name, "Timeout nach 0s")
            continue

        run_fetch = _build_run_fetch(
            fetch, effective_timeout, supports_timeout, semaphore, provider_name
        )

        report.provider_started(provider_name)
        future = executor.submit(run_fetch)
        futures[future] = (fetch, provider_name, effective_timeout)
        pending.add(future)
        start_time = perf_counter()
        if effective_timeout > 0:
            deadlines[future] = start_time + effective_timeout
        else:
            deadlines[future] = None

    return futures, deadlines, pending


def _evict_expired_futures(
    pending: set[Any],
    futures: dict[Any, tuple[Any, str, int]],
    deadlines: dict[Any, float | None],
    cancelled_futures: set[Any],
    report: RunReport,
    now: float,
) -> None:
    """Per Apex Phase 1: poll deadlines on every loop turn so the busy-spin
    against real-time `perf_counter()` is bounded by the smallest remaining
    timeout. Mutates ``pending`` and ``cancelled_futures`` in place.
    """
    expired = [
        future for future in list(pending)
        if (deadline := deadlines.get(future)) is not None and now >= deadline
    ]
    for future in expired:
        pending.discard(future)
        fetch, provider_name, timeout_value = futures[future]
        name = getattr(fetch, "__name__", str(fetch))
        log.error("%s fetch Timeout nach %ss", name, timeout_value)
        report.provider_error(
            provider_name,
            f"Timeout nach {timeout_value}s",
        )
        future.cancel()
        cancelled_futures.add(future)


def _drain_completed_futures(
    futures: dict[Any, tuple[Any, str, int]],
    deadlines: dict[Any, float | None],
    pending: set[Any],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Apex-Phase-1 deadline-eviction loop: alternate eviction sweep + bounded
    `wait()` until ``pending`` drains. Result handling distinguishes Timeout,
    CancelledError, and generic Exception so the report has accurate per-
    provider error categories.
    """
    cancelled_futures: set[Any] = set()
    while pending:
        now = perf_counter()
        _evict_expired_futures(pending, futures, deadlines, cancelled_futures, report, now)

        if not pending:
            break

        wait_timeout: float | None = None
        remaining = [
            deadline - now
            for fut in pending
            if (deadline := deadlines.get(fut)) is not None
        ]
        if remaining:
            wait_timeout = max(min(remaining), 0.1)

        done, _ = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
        if not done:
            continue

        for future in done:
            pending.discard(future)
            if future in cancelled_futures:
                continue
            fetch, provider_name, _timeout_value = futures[future]
            name = getattr(fetch, "__name__", str(fetch))
            try:
                result = future.result()
            except (TimeoutError, requests.exceptions.Timeout) as exc:
                # Security (Clear-Text-Logging Drift): the timeout
                # exception text may include the upstream URL (sanitised
                # by ``request_safe`` already) but a non-request_safe
                # path (e.g. fetch() composing its own timeout) could
                # still surface raw upstream bytes — sanitise.
                sanitised = sanitize_log_arg(str(exc))
                log.error("%s fetch Timeout: %s", name, sanitised)
                report.provider_error(provider_name, f"Timeout: {sanitised}")
            except CancelledError:
                report.provider_error(provider_name, "Fetch abgebrochen")
            except Exception as exc:
                # Security (Clear-Text-Logging Drift): broad ``Exception``
                # — sanitise both log emission and the operator-facing
                # report-error surface.  ``log.exception`` interpolates
                # the bound exc via ``%s`` for the *message string*; the
                # traceback itself is rendered by the logging framework
                # via formatException (a repr-escaping path) and is
                # therefore safe.
                sanitised = sanitize_log_arg(str(exc))
                log.exception("%s fetch fehlgeschlagen: %s", name, sanitised)
                report.provider_error(
                    provider_name, f"Fetch fehlgeschlagen: {sanitised}"
                )
            else:
                # Isolate a single provider's merge failure: ``merge_result``
                # (and the report bookkeeping it calls) runs OUTSIDE the
                # ``future.result()`` try above, so an exception here would
                # otherwise propagate out of the drain loop and lose every
                # already-collected item from the other providers.
                try:
                    merge_result(fetch, result, provider_name)
                except Exception as exc:
                    sanitised = sanitize_log_arg(str(exc))
                    log.exception("%s merge fehlgeschlagen: %s", name, sanitised)
                    report.provider_error(
                        provider_name, f"Merge fehlgeschlagen: {sanitised}"
                    )


def _run_network_fetchers(
    network_fetchers: list[Any],
    provider_names: dict[Any, str],
    provider_envs: dict[Any, str | None],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Run all network fetchers concurrently in a ThreadPoolExecutor with
    deadline-eviction-style timeout enforcement (Apex Phase 1). Pending
    futures are cancelled on early exit to free worker threads immediately.
    """
    desired_workers = len(network_fetchers)
    if feed_config.PROVIDER_MAX_WORKERS > 0:
        if desired_workers > feed_config.PROVIDER_MAX_WORKERS:
            log.debug(
                "Begrenze Provider-Threads von %s auf %s",
                desired_workers,
                feed_config.PROVIDER_MAX_WORKERS,
            )
        desired_workers = min(desired_workers, feed_config.PROVIDER_MAX_WORKERS)

    pending: set[Any] = set()
    with ThreadPoolExecutor(max_workers=max(1, desired_workers)) as executor:
        try:
            futures, deadlines, pending = _submit_network_fetches(
                executor, network_fetchers, provider_names, provider_envs, report
            )
            _drain_completed_futures(futures, deadlines, pending, report, merge_result)
        finally:
            # Cancel remaining futures if we exit early or with exceptions.
            # executor.shutdown(wait=False, cancel_futures=True) is automatically called
            # by the context manager's __exit__, but we explicitly cancel pending futures
            # to free resources immediately.
            for future in pending:
                future.cancel()


def _collect_items(report: RunReport | None = None) -> list[FeedItem]:
    """Run all enabled providers and merge their items into a single list.

    This is the central data-collection orchestrator of the build. It:

    1. Initialises the provider registry (lazy-loads plugins on first call).
    2. Categorises providers via :func:`_categorize_providers` into:
       - **cache fetchers** — loaders whose ``_provider_cache_name``
         attribute marks them as disk-bound (read from a local cache);
         run sequentially since the bottleneck is disk I/O, not network.
       - **network fetchers** — real upstream HTTP fetches; run
         concurrently in a :class:`ThreadPoolExecutor`.
    3. Wires up a :func:`register_cache_alert_hook` so that warnings
       emitted by the cache layer (e.g. "cache for VOR is 6h stale")
       attach to the run report instead of just logging.
    4. Runs cache fetchers via :func:`_run_cache_fetchers` (synchronous).
    5. Runs network fetchers via :func:`_run_network_fetchers`, which
       wraps each loader in a per-future timeout, evicts stragglers via
       a deadline-eviction loop (Apex Phase 1), and merges results
       through :func:`_merge_result`.
    6. Provides per-provider exception isolation — a crash in one
       loader becomes an error entry on the run report, not an abort.
       This is the project's primary bulkhead: a hostile or unhealthy
       upstream cannot bring down the other providers' data.

    The function intentionally accepts a ``RunReport`` to keep the
    health-reporting side-effect explicit. Tests inject a fresh report
    per call; production paths receive one from
    :func:`_invoke_collect_items`.

    Args:
        report: Optional :class:`RunReport` to record provider
            successes, errors, and cache alerts into. If ``None``, a
            fresh report is constructed from
            :func:`provider_statuses` so the function is safe to call
            standalone (tests, ad-hoc invocations).

    Returns:
        A list of :class:`FeedItem` dictionaries, concatenated from
        every successful provider in the order they completed. The
        result is **not** deduplicated; callers (typically
        :func:`main`) run :func:`_dedupe_items` and
        :func:`deduplicate_fuzzy` afterwards.

    See Also:
        ``docs/architecture.md`` §1 for the full sequence diagram.
    """
    init_providers()
    if report is None:
        report = RunReport(provider_statuses())
    items: list[FeedItem] = []

    cache_alerts: defaultdict[str, list[str]] = defaultdict(list)
    seen_cache_alerts: set[tuple[str, str]] = set()
    alert_lock = Lock()

    def _cache_alert_handler(provider_key: str, message: str) -> None:
        normalized_key = str(provider_key or "").strip()
        normalized_message = clean_message(message)
        if not normalized_key or not normalized_message:
            return
        with alert_lock:
            cache_alerts[normalized_key].append(normalized_message)
            if report is not None:
                key = (normalized_key, normalized_message)
                if key not in seen_cache_alerts:
                    seen_cache_alerts.add(key)
                    report.add_warning(f"Cache {normalized_key}: {normalized_message}")

    unregister_cache_alert = register_cache_alert_hook(_cache_alert_handler)
    try:
        buckets = _categorize_providers(report)

        if not buckets.cache_fetchers and not buckets.network_fetchers:
            return []

        def _merge_result(fetch: Any, result: Any, provider_name: str) -> None:
            name = getattr(fetch, "__name__", str(fetch))
            if not isinstance(result, list):
                log.error("%s fetch gab keine Liste zurück: %r", name, result)
                report.provider_error(provider_name, "Ungültige Antwort (keine Liste)")
                return
            # Cast raw dicts to FeedItem for typing compliance after normalization
            _normalize_item_datetimes(result)
            typed_result = result
            items.extend(typed_result)
            count = len(result)
            if count == 0:
                _report_empty_provider(fetch, provider_name, report, cache_alerts)
            else:
                report.provider_success(provider_name, items=count)

        _run_cache_fetchers(
            buckets.cache_fetchers, buckets.provider_names, items, report, _merge_result
        )

        if buckets.network_fetchers:
            _run_network_fetchers(
                buckets.network_fetchers,
                buckets.provider_names,
                buckets.provider_envs,
                report,
                _merge_result,
            )

        return items
    finally:
        unregister_cache_alert()


def _invoke_collect_items(report: RunReport) -> list[FeedItem]:
    return _collect_items(report=report)


def _valid_until(item: FeedItem) -> datetime | None:
    """The moment *item* stops being valid, from its ``ends_at``.

    Stadt Wien delivers construction ends as a date ("2026-09-04Z"), read as
    Vienna midnight of that day. The time line takes such an end as the day
    ("Bis Fr 04.09."), so the works still apply on it; dropped at that
    midnight, every construction site left the feed at the start of its last
    day (all 13 that ran to their end between July and October 2026, one of
    them from place 3). Such an end counts until the next Vienna midnight.
    Every other end stands as given.
    """
    ends_at = item.get("ends_at")
    if not isinstance(ends_at, datetime):
        return None
    if not str(item.get("source") or "").strip().casefold().startswith("stadt wien"):
        return ends_at
    local = _to_utc(ends_at).astimezone(_VIENNA_TZ)
    if local.time() != time(0, 0):
        return ends_at
    next_day = local.date() + timedelta(days=1)
    return datetime(next_day.year, next_day.month, next_day.day, tzinfo=_VIENNA_TZ)


def _drop_after(item: FeedItem) -> datetime | None:
    """The moment (UTC) after which *item* leaves the feed, from :func:`_valid_until`.

    ``ENDS_AT_GRACE_MINUTES`` past the end, but never past the Vienna
    midnight that ends the day the time line names. Without that bound an
    end late in the day stayed over midnight with a date already over: on
    2026-10-20 at 00:00 Vienna the feed showed "Wien Hütteldorf ↔ Wien
    Hauptbahnhof … [Bis 19.10.]" (end 19.10. 23:59:59, Feed-Wächter issue
    #2044), and every ÖBB end of day and every Baustelle on its last day
    leaves the same way.
    """
    valid_until = _valid_until(item)
    if valid_until is None:
        return None
    graced = _to_utc(valid_until) + timedelta(minutes=feed_config.ENDS_AT_GRACE_MINUTES)
    ends_at = item.get("ends_at")
    if not isinstance(ends_at, datetime):
        return graced
    shown_day = _to_utc(ends_at).astimezone(_VIENNA_TZ).date() + timedelta(days=1)
    midnight = datetime(shown_day.year, shown_day.month, shown_day.day, tzinfo=_VIENNA_TZ)
    return min(graced, _to_utc(midnight))


def _drop_old_items(
    items: list[FeedItem],
    now: datetime,
    state: dict[str, dict[str, Any]],
) -> tuple[list[FeedItem], set[str]]:
    """Entferne Items, die nicht mehr gültig oder zu lange im Feed sind.

    Zwei Regeln:

    1. **Ungültig → sofort raus:** ein ``ends_at`` in der Vergangenheit
       (über die ``ENDS_AT_GRACE_MINUTES`` hinaus, höchstens bis zur
       Wiener Mitternacht nach dem Endtag, :func:`_drop_after`) bedeutet,
       die Störung ist behoben → die Meldung macht sofort Platz. Ein reines Enddatum der
       Stadt-Wien-Baustellen gilt bis zum Ende dieses Tages
       (:func:`_valid_until`).
    2. **Alter → FIFO nach ``first_seen``:** das Alter zählt ab dem
       Auftauchen im Feed (``first_seen`` aus dem State), NICHT ab dem
       Quell-Startdatum. So fällt ein aktiver Langläufer nicht wegen eines
       alten Startdatums heraus; ein noch nicht im State vermerktes Item ist
       brandneu und wird hier nie ausgemustert.
    """

    out: list[FeedItem] = []
    dropped: set[str] = set()
    now_utc = _to_utc(now)
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]

        ident, state_entry = _lookup_state(it, state)

        ends_at = it.get("ends_at")
        drop_after = _drop_after(it)
        if drop_after is not None and drop_after < now_utc:
            dropped.add(ident)
            continue

        # Age out by how long the item has been in the feed (first_seen) —
        # FIFO by appearance ("man kennt die Meldung schon"). The source
        # start date is deliberately NOT used, so an active long-runner that
        # only recently surfaced is not retired for an old upstream start;
        # an item not yet in the state is brand-new and never aged out here.
        first_seen_dt = _parse_first_seen(state_entry, None)
        age_days: float | None = (
            (now_utc - first_seen_dt).total_seconds() / 86400.0
            if first_seen_dt is not None
            else None
        )

        if age_days is not None:
            if age_days > feed_config.ABSOLUTE_MAX_AGE_DAYS:
                dropped.add(ident)
                continue
            if age_days > feed_config.MAX_ITEM_AGE_DAYS:
                if not isinstance(ends_at, datetime):
                    dropped.add(ident)
                    continue

        out.append(it)

    # Duplicate-identity guard: when two items share a state-key (e.g. a
    # duplicate-guid pair across providers / plugins) and one expired or
    # aged out while the other survives, the loop above added the shared
    # identity to ``dropped`` from the expired sibling AND will re-emit the
    # state entry from the survivor in ``_make_rss``. Pre-fix
    # ``_save_state`` then unconditionally pruned the freshly-written
    # survivor entry — silent perpetual churn (re-publish via fresh
    # pubDate, FIFO age retirement disabled). Subtract the survivors'
    # identities so ``dropped`` carries only items with no surviving
    # twin.
    if dropped:
        dropped -= {_lookup_state(it, state)[0] for it in out}
    return out, dropped


def _dedupe_key_for_item(
    it: FeedItem, *, warn_on_missing: bool = True
) -> tuple[str, bool]:
    """Return the deduplication key used for ``it`` and indicate fallback usage."""
    # Use explicit _identity if present
    if it.get("_identity"):
        return str(it.get("_identity")), False

    guid = it.get("guid")
    source = (it.get("source") or "").lower()

    if "öbb" in source or "oebb" in source:
        guid_or_link = guid or it.get("link")
        if guid_or_link:
            return f"oebb|{guid_or_link}", False

    if guid:
        return str(guid), False

    key = _identity_for_item(it)

    if warn_on_missing:
        log.warning(
            "Item ohne guid/_identity – Fallback-Schlüssel (_identity_for_item) %s",
            key,
        )
    return key, True


def _state_key_for_item(it: FeedItem) -> str:
    """Return the persistent ``first_seen`` state key for ``it``.

    Prefers the provider ``guid`` — stable across upstream title/description
    edits and the read-side title enrichment — so an item's ``first_seen``
    (and the age-based retirement that relies on it) survives those changes.
    Falls back to the content identity when no guid is present, so EVERY
    item, provider-independently, still gets a first_seen entry.
    """
    guid = it.get("guid")
    if guid:
        return str(guid)
    return _identity_for_item(it)


def _lookup_state(
    it: FeedItem, state: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any] | None]:
    """Return ``(stable_key, entry)`` for ``it``'s first_seen state.

    Prefers the guid-based key (:func:`_state_key_for_item`); on a miss it
    falls back to a legacy identity-keyed entry so first_seen migrates
    without a reset. Shared by the writer (:func:`_update_item_state`) and
    the age check (:func:`_drop_old_items`) so both stay consistent.
    """
    key = _state_key_for_item(it)
    entry = state.get(key)
    if entry is None:
        legacy = _identity_for_item(it)
        if legacy != key:
            entry = state.get(legacy)
    return key, entry


# A Wiener-Linien message that was absent from the provider data for longer
# than this before its new validity start is a new occurrence — see
# :func:`_restart_recurring_occurrences`. Four update cycles: a message that
# drops out of one or two fetches is still the same occurrence.
_OCCURRENCE_GAP = timedelta(hours=2)
# A planned measure (:data:`_PLANNED_DISRUPTION_RE`) pauses longer: WL issues
# "66A: Busse halten Salvatorianerplatz" every night for 04:40 to 01:00, a
# night line's stop only for the night. Under the two hours above it counted
# as new every morning: since 2026-09-26 such a measure stood in front in
# 370 of 387 German feed versions, 441 slots (66A alone 286), although it
# had been in the feed since 28.08. Unplanned incidents keep the two hours.
_PLANNED_OCCURRENCE_GAP = timedelta(hours=36)
# Set by :func:`_restart_recurring_occurrences` on an entry whose message
# begins a new occurrence; :func:`_update_item_state` writes its statistics
# row the first time the occurrence is shown and removes the mark.
_NEW_OCCURRENCE_KEY = "new_occurrence"


def _restart_recurring_occurrences(
    items: Sequence[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> int:
    """Give a recurring Wiener-Linien disruption its own ``first_seen``.

    The WL ``guid`` is category + topic + line set — no date. "94A:
    Verkehrsunfall" on 2026-09-25 therefore carried the guid of the accident
    on 2026-07-04 and inherited its ``first_seen``: the feed sorted a
    disruption that had started minutes earlier as 83 days old, behind every
    newer stop relocation, and it never reached the ``MAX_ITEMS`` slots
    (six live disruptions missing from the feed at once that afternoon).

    A WL ``pubDate`` is the start of the message's validity, and the provider
    drops messages before that start — this occurrence cannot have been seen
    earlier. An entry whose ``first_seen`` precedes the ``pubDate`` therefore
    stems from an earlier occurrence, *unless* the message was in the data
    right up to the new start: WL re-issues running measures ("Busse halten
    …") with a fresh validity window every day or few days, and those must
    keep their place ("man kennt die Meldung schon"). ``last_seen`` tells the
    two apart — stamped here on every build for every WL item with an entry:

    * last seen within ``_OCCURRENCE_GAP`` before the new start — within
      ``_PLANNED_OCCURRENCE_GAP`` for a planned measure, which WL re-issues
      after its nightly pause — → the same message continues,
      ``first_seen`` stays;
    * last seen earlier, or never (entries from before ``last_seen``
      existed) → a new occurrence, ``first_seen`` moves to the ``pubDate``.

    The entry is moved, not dropped: without an entry an item ranks as unseen
    in every build until it is emitted, which would rotate a batch of such
    items through the feed one ``MAX_ITEMS`` page at a time. Other providers
    are untouched — an ÖBB ``pubDate`` is the publication time of the latest
    update, not the start of an occurrence.

    Returns the number of entries moved.
    """
    now_utc = _to_utc(now)
    stamp = now_utc.isoformat()
    restarted = 0
    for it in items:
        if str(it.get("source") or "").strip().casefold() != "wiener linien":
            continue
        _, entry = _lookup_state(it, state)
        if entry is None:
            continue
        first_seen = _parse_first_seen(entry, None)
        pub = _parse_datetime(it.get("pubDate"))
        if first_seen is not None and isinstance(pub, datetime):
            pub_utc = _to_utc(pub)
            last_seen = _parse_state_time(entry, "last_seen")
            text = f"{it.get('title') or ''} {html_to_text(str(it.get('description') or ''))}"
            gap = _PLANNED_OCCURRENCE_GAP if _PLANNED_DISRUPTION_RE.search(text) else _OCCURRENCE_GAP
            if first_seen < pub_utc <= now_utc and (
                last_seen is None or last_seen < pub_utc - gap
            ):
                entry["first_seen"] = pub_utc.isoformat()
                # Counted in the statistics once it reaches the feed
                # (:func:`_update_item_state`), like a disruption never seen.
                entry[_NEW_OCCURRENCE_KEY] = True
                restarted += 1
        entry["last_seen"] = stamp
    if restarted:
        log.info(
            "%d wiederkehrende WL-Meldung(en) als neues Auftreten eingeordnet.",
            restarted,
        )
    return restarted


def _note_announced_starts(
    items: Sequence[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> int:
    """Remember the start of every item seen before it begins.

    The feed sorts by ``first_seen``, and an announcement is seen long before
    it applies: "S1/S2/S3/S4/S7: S-Bahn-Stammstrecke (Phase 2)" since 10.06.
    for 07.09.2026, the ÖBB closure "Wien Hauptbahnhof ↔ Gramatneusiedl"
    since 08.07. for 03.10.2026. On its first day each counted as months old
    and stood behind every newer notice: the Stammstrecke closure held none
    of the ten slots on 07.09., the ÖBB closure stood on place 49 on 04.10.

    For every item with a state entry whose ``starts_at`` lies in the future
    this stores that start as ``announced_start`` (a postponed start
    overwrites it). :func:`_sort_moment` then counts the item as new from
    that start, behind the current incidents (:func:`_is_current_incident`).
    The start is taken only while it is still ahead, so a measure WL re-issues every night
    with a fresh validity ("66A: Busse halten Salvatorianerplatz", starts at
    04:40 each morning, seen from 04:40) never gets one and keeps its place
    (:data:`_PLANNED_OCCURRENCE_GAP`).

    Returns the number of entries written.
    """
    now_utc = _to_utc(now)
    noted = 0
    for it in items:
        start = _parse_datetime(it.get("starts_at"))
        if not isinstance(start, datetime) or _to_utc(start) <= now_utc:
            continue
        _, entry = _lookup_state(it, state)
        if entry is None:
            continue
        value = _to_utc(start).isoformat()
        if entry.get("announced_start") != value:
            entry["announced_start"] = value
            noted += 1
    return noted


def _sort_moment(entry: dict[str, Any] | None, first_seen: datetime, now_utc: datetime) -> datetime:
    """The moment an item counts as new for the feed order.

    ``first_seen``, or the ``announced_start`` stored by
    :func:`_note_announced_starts` once it has come: an announced measure
    leads on the day it begins and then ages like any other item. Before
    its start :func:`_defer_upcoming_items` decides its place.
    """
    announced = _parse_state_time(entry, "announced_start")
    if announced is not None and first_seen < announced <= now_utc:
        return announced
    return first_seen


def _summarize_duplicates(items: Sequence[FeedItem]) -> list[DuplicateSummary]:
    groups: dict[str, list[FeedItem]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]
        key, _ = _dedupe_key_for_item(it, warn_on_missing=False)
        groups.setdefault(key, []).append(it)

    summaries: list[DuplicateSummary] = []
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        # Security (Trojan-Source canonical-floor scrub at the boundary):
        # ``key`` (derived from each item's ``guid`` / ``_identity`` /
        # ``link``) and each ``title`` are upstream-controlled — a
        # compromised provider / MITM / DNS hijack / poisoned cache
        # fallback can plant BiDi marks, Tag-block bytes, variation
        # selectors, or C0/C1 controls inside them. ``_sanitize_text``
        # strips the canonical-floor primitive family pinned by
        # ``_CONTROL_RE`` (the byte-exact mirror of
        # ``_INVISIBLE_DANGEROUS_RE``) so every downstream sink of
        # ``DuplicateSummary`` — ``docs/feed-health.md`` inline code
        # spans (via ``safe_markdown_codespan`` in
        # ``render_feed_health_markdown``), ``docs/feed-health.json``
        # (via ``_CONTROL_CHARS_RE`` scrub in
        # ``build_feed_health_payload``), and the operator-facing
        # lint-report stdout (line below) — inherits the canonical
        # floor in one place. The grouping above runs on the RAW key
        # so two items carrying the same poisoned guid still group
        # together; only the OUTPUT key handed to ``DuplicateSummary``
        # is scrubbed.
        sanitised_key = _sanitize_text(key)
        titles = tuple(
            _sanitize_text(str(entry.get("title") or "")) for entry in group[:3]
        )
        summaries.append(
            DuplicateSummary(
                dedupe_key=sanitised_key, count=len(group), titles=titles
            )
        )
    summaries.sort(key=lambda summary: summary.count, reverse=True)
    return summaries


def _count_new_items(
    items: Sequence[FeedItem],
    state: dict[str, dict[str, Any]],
) -> int:
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]
        # The state is persisted under the guid-preferring key scheme
        # (:func:`_state_key_for_item`, with a legacy-identity fallback) used
        # by both the writer and the age-out. Comparing the raw content
        # identity (:func:`_identity_for_item`) against guid-keyed state
        # counted every guid-bearing item as "new" on every run — use the
        # same lookup so an already-tracked item is recognised.
        _key, entry = _lookup_state(it, state)
        if entry is None:
            count += 1
    return count


def _dedupe_items(items: list[FeedItem]) -> list[FeedItem]:
    """
    Deduplicate items by identity/guid.

    When duplicates are found (same deduplication key), the "better" item is kept.
    Selection criteria for the "better" item:
    1. Later end date (indicates more up-to-date info on duration).
    2. More recent publication/start date.
    3. Longer description (indicates more detail).

    Args:
        items: List of item dictionaries.

    Returns:
        A list of unique item dictionaries.
    """

    def _recency_value(it: FeedItem) -> datetime:
        """Return a comparable timestamp describing how recent ``it`` is."""
        if "_calculated_recency" in it:
            return it["_calculated_recency"]

        candidates: list[datetime] = []
        for field_name in ("pubDate", "first_seen", "starts_at"):
            value = it.get(field_name)
            if isinstance(value, datetime):
                candidates.append(_to_utc(value))
            else:
                parsed = _parse_datetime(value)
                if isinstance(parsed, datetime):
                    candidates.append(_to_utc(parsed))

        if candidates:
            res = max(candidates)
        else:
            res = datetime.min.replace(tzinfo=UTC)

        it["_calculated_recency"] = res
        return res

    def _end_value(it: FeedItem) -> datetime:
        if "_calculated_end" in it:
            return it["_calculated_end"]

        ends = it.get("ends_at")
        if isinstance(ends, datetime):
            res = _to_utc(ends)
        else:
            res = datetime.min.replace(tzinfo=UTC)

        it["_calculated_end"] = res
        return res

    def _better(a: FeedItem, b: FeedItem) -> bool:
        """Return True if ``a`` is better than ``b`` according to recency and content."""

        a_end = _end_value(a)
        b_end = _end_value(b)
        if a_end > b_end:
            return True
        if a_end < b_end:
            return False

        # Bei gleichem Enddatum: Zuerst Aktualität, dann Länge
        if _recency_value(a) > _recency_value(b):
            return True
        if _recency_value(a) < _recency_value(b):
            return False

        a_len = len(a.get("description") or "")
        b_len = len(b.get("description") or "")
        return a_len > b_len

    seen: dict[str, int] = {}
    out: list[FeedItem] = []
    for it in items:
        key, _ = _dedupe_key_for_item(it)
        if key in seen:
            idx = seen[key]
            if _better(it, out[idx]):
                out[idx] = it
        else:
            seen[key] = len(out)
            out.append(it)
    return out

# Feed-ordering priority by category, applied as a tie-breaker when several
# items share the same ``first_seen``. Lower rank sorts first. ``Störung``
# (WL/ÖBB live disruptions AND the Stammstrecke delay/cancellation monitor,
# whose ``EVENT_CATEGORY`` is also ``"Störung"``) leads; ``Baustelle``
# (long-running construction) trails; every other category (e.g. WL
# ``Hinweis``) sits between. Keyed on the casefolded category so a provider
# emitting ``"störung"`` still matches. Operator policy (2026-05): disruptions
# matter more for the feed than construction.
_CATEGORY_FEED_RANK: dict[str, int] = {
    "störung": 0,
    "baustelle": 2,
}
_DEFAULT_CATEGORY_FEED_RANK = 1


def _category_feed_rank(item: FeedItem) -> int:
    """Return the feed-priority rank for *item*'s category (lower sorts first)."""
    category = item.get("category")
    if isinstance(category, str):
        return _CATEGORY_FEED_RANK.get(
            category.strip().casefold(), _DEFAULT_CATEGORY_FEED_RANK
        )
    return _DEFAULT_CATEGORY_FEED_RANK


_TOPIC_REASON_TAIL_RE = re.compile(r"\bwegen\s+(\S+)\s*$", re.IGNORECASE)


def _topic_budget_key(item: FeedItem) -> tuple[str, str] | None:
    """The (reason word, local day) an item is budgeted under, or ``None``.

    The reason word is the WL ticker's leading category word
    (``Demonstration Betrieb ab …``, see ``_CATEGORY_PREFIX_WORDS``) or the
    one after a trailing ``wegen`` (``Fahrtbehinderung wegen
    Demonstration``). The day is the Vienna date of ``starts_at``, falling
    back to ``pubDate``. Items without a reason word or without a date are
    never budgeted — the budget exists for the one shape that floods the
    feed: one event, one ticker per line, all on the same day.
    """
    body = _title_body(str(item.get("title") or ""))
    reason = _leading_category_word(body).casefold()
    if not reason:
        tail = _TOPIC_REASON_TAIL_RE.search(body)
        candidate = tail.group(1).casefold().strip(".,!") if tail else ""
        reason = candidate if candidate in _CATEGORY_PREFIX_WORDS else ""
    if not reason:
        return None
    when = _parse_datetime(item.get("starts_at")) or _parse_datetime(item.get("pubDate"))
    if not isinstance(when, datetime):
        return None
    return reason, _to_utc(when).astimezone(_VIENNA_TZ).date().isoformat()


def _apply_topic_budget(items: list[FeedItem], limit: int) -> list[FeedItem]:
    """Let no single reason-and-day fill more than *limit* of the leading slots.

    Published 2026-09-19: one demonstration, one Wiener-Linien ticker per
    line, and — sorted newest-first — the ten slots of the feed went to
    that one event; the 25/26/27 replacement service, every ÖBB notice and
    the two construction-site items directly below the cap were invisible.
    Over 300 published revisions the same shape recurred on two of eight
    days (a Veranstaltung on 17.09., the demonstration on 19.09.), each time
    with eight same-reason tickers in the top ten.

    This pass keeps the sorted order and moves every item beyond the
    *limit*-th of its ``_topic_budget_key`` behind all the others, in their
    original order. Nothing is dropped: with a larger ``MAX_ITEMS`` the
    deferred items still appear, just later. ``limit`` 0 disables the pass.
    Items without a key (no reason word, no date) are never deferred.
    """
    if limit <= 0:
        return items
    seen: dict[tuple[str, str], int] = {}
    kept: list[FeedItem] = []
    deferred: list[FeedItem] = []
    for item in items:
        key = _topic_budget_key(item)
        if key is None:
            kept.append(item)
            continue
        seen[key] = seen.get(key, 0) + 1
        (kept if seen[key] <= limit else deferred).append(item)
    if deferred:
        log.info(
            "Platzbudget: %d Item(s) hinter das Feld gestellt (höchstens %d je Ursache und Tag).",
            len(deferred),
            limit,
        )
    return kept + deferred


_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


def _repeated_route_title_key(item: FeedItem) -> str | None:
    """The key an ÖBB item shares with its other time windows, or ``None``."""
    if str(item.get("source") or "").strip().casefold() != "öbb":
        return None
    title = _WHITESPACE_RE.sub(" ", str(item.get("title") or "")).strip().casefold()
    return title or None


def _defer_repeated_route_titles(
    items: list[FeedItem], is_current: Callable[[FeedItem], bool] | None = None
) -> list[FeedItem]:
    """Give one slot to each ÖBB title, held by its earliest time window.

    ÖBB titles are the route and nothing else, so one route closed in three
    separate construction phases arrives as three items with one title::

        Wien Hauptbahnhof ↔ Gramatneusiedl    03.10. – 05.10.
        Wien Hauptbahnhof ↔ Gramatneusiedl    31.10. – 30.11.
        Wien Hauptbahnhof ↔ Gramatneusiedl    05.12. – 07.12.

    Each survives the dedupe passes (three GUIDs, three different texts)
    and the topic budget (a route title carries no reason word). On the
    displays the same line would stand three times and cost two other
    disruptions their slot.

    The items are not merged: the phases are different measures (single
    trains in October, no local trains at night later), and one text for
    all three would misstate two of them. Instead the item whose window
    starts first keeps its own place in the sorted order and the others
    move behind the field, in their original order — exactly like
    :func:`_apply_topic_budget`. Nothing is dropped: once the first phase
    has ended it leaves the feed and the next one holds the slot.

    A current incident (*is_current*, :func:`_is_current_incident`) holds
    the slot instead, the first one in the sorted order. ÖBB names a
    disruption by its route like a construction phase, and a phase that is
    running started earlier than any disruption today: an accident between
    Wien Hbf and Wien Westbahnhof would have gone behind the field next to
    the works there (11.09. to 01.11.2026), against the operator decision of
    2026-10-04 that current disruptions come first.
    """
    groups: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        key = _repeated_route_title_key(item)
        if key is not None:
            groups.setdefault(key, []).append(index)

    def window_start(index: int) -> tuple[datetime, int]:
        when = _parse_datetime(items[index].get("starts_at"))
        return (_to_utc(when) if isinstance(when, datetime) else _FAR_FUTURE, index)

    deferred_indices: set[int] = set()
    for members in groups.values():
        if len(members) > 1:
            current = [m for m in members if is_current is not None and is_current(items[m])]
            lead = current[0] if current else min(members, key=window_start)
            deferred_indices.update(m for m in members if m != lead)
    if not deferred_indices:
        return items
    log.info(
        "Gleiche ÖBB-Titel: %d weitere(s) Zeitfenster hinter das Feld gestellt.",
        len(deferred_indices),
    )
    kept = [item for index, item in enumerate(items) if index not in deferred_indices]
    deferred = [item for index, item in enumerate(items) if index in deferred_indices]
    return kept + deferred


# An all-clear title: "Aufhebung Verkehrseinschränkung: …" or "Aufhebung
# Streckenunterbrechung: …", optionally behind a line label ("REX 50: …") or
# a raw ÖBB update prefix ("Update 5 (25.09.2026 10:48) …") that a cleaning
# step missed. ``\b`` keeps "Aufhebungen" and similar words out.
_ALL_CLEAR_TITLE_RE = re.compile(
    r"^\s*(?:Update\s+\d+\s*\([^)]*\)\s*[:\-–]?\s*)?(?:[^:]{1,40}:\s*)?Aufhebung\b",
    re.IGNORECASE,
)


# Wiener Linien published two test messages on 2026-09-23 that reached the
# German feed for one cycle each: "71/72: Dies ist eine Testmeldung" ("Dies
# ist ein Test") and "62: F57f Test" ("F57 f Test"). On a display with ten
# slots each took the place of a real disruption (audit 2026-09-26).
#
# The filter audit of 2026-10-03 replayed every cached WL item since
# September 2025 and found three more shapes that WL's test entries take:
# the compounds "Testfall" ("38: Testfall", "40: Testfall", "72A: Testfall"
# with "Grund: Testfall im Bereich .") and "Testtext" ("WLB: Testtext GSC200
# Test für LB / WLB und Lauftext"), and a run of "Test" longer than five
# words ("Test Test Tes Test Test Test"). Neither compound is a word a real
# notice uses; "Testbetrieb" and "Testfahrt" stay out of the list.
_TEST_MESSAGE_RE = re.compile(
    r"\btest(?:meldung(?:en)?|f(?:a|ä)lle?|texte?)\b", re.IGNORECASE
)
_TEST_WORD_RE = re.compile(r"(?<![\w-])test(?![\w-])", re.IGNORECASE)
# "Tes" in "Test Test Tes Test" is a typo of the same word.
_TEST_RUN_WORD_RE = re.compile(r"^tes?t?$", re.IGNORECASE)
# A text this short that says "Test" is a test. A real message that mentions
# one says more ("Test-Fahrten der neuen Straßenbahn zwischen … und …"), and
# "Haltestelle" or "Testbetrieb" never match the word. Neither does a
# compound with a hyphen: "U2: Test-Fahrten neuer Züge" and "D:
# Covid-Test-Straße Haltestelle verlegt" are four words each and real
# (fund C, 2026-10-02); a bare "\b" took the hyphen for a word end.
MAX_TEST_TEXT_WORDS = 5


def _is_test_message(item: FeedItem) -> bool:
    """Whether ``item`` is a provider's test message rather than a disruption."""
    title = str(item.get("title") or "")
    text = html_to_text(str(item.get("description") or ""))
    if _TEST_MESSAGE_RE.search(title) or _TEST_MESSAGE_RE.search(text):
        return True
    return any(
        _TEST_WORD_RE.search(part) is not None
        and (len(part.split()) <= MAX_TEST_TEXT_WORDS or _only_test_words(part))
        for part in (title, text)
    )


def _only_test_words(text: str) -> bool:
    """Whether ``text`` says nothing but "Test", after an optional line prefix."""
    words = re.findall(r"[^\W\d_]+", text.split(":", 1)[-1])
    return bool(words) and all(_TEST_RUN_WORD_RE.match(w) for w in words)


def _drop_test_messages(items: list[FeedItem]) -> list[FeedItem]:
    """Remove providers' test messages before they can take a slot."""
    kept: list[FeedItem] = []
    for item in items:
        if _is_test_message(item):
            log.warning("Testmeldung verworfen: %s", sanitize_log_arg(str(item.get("title") or "")))
        else:
            kept.append(item)
    return kept


def _starts_after(item: FeedItem, last_day: date) -> bool:
    """Whether ``item`` begins on a Vienna day after *last_day*."""
    start = _parse_datetime(item.get("starts_at"))
    if not isinstance(start, datetime):
        return False
    return _to_utc(start).astimezone(_VIENNA_TZ).date() > last_day


def _preview_last_day(today: date, preview_days: int) -> date:
    """The last Vienna day whose announcements keep their normal place.

    *preview_days* after *today*, but on a Friday and a Saturday at least the
    coming Monday (operator decision 2026-10-03, "Wochenende mit"): a detour
    from Monday must stand in front on Friday, for anyone who sees the
    display only on workdays. ``preview_days`` 0 keeps today only.
    """
    last_day = today + timedelta(days=preview_days)
    if preview_days > 0 and today.weekday() in (4, 5):  # Friday, Saturday
        last_day = max(last_day, today + timedelta(days=7 - today.weekday()))
    return last_day


def _defer_upcoming_items(
    items: list[FeedItem], now: datetime, preview_days: int
) -> list[FeedItem]:
    """Let an announced item take only a slot nothing running needs.

    The feed sorts newest first, and an announcement is new the day it
    appears. On 2026-10-02 at 18:00 five of the ten slots held items that had
    not begun — "20A: Bauarbeiten" from 13.10., the R 40 closure from 31.10.
    — while "N71: Ersatzverkehr" and "62: ÖBB Bauarbeiten", both valid that
    evening, stood on places 11 and 12. On a display someone reads in
    passing, what applies now comes first.

    Every item that begins after the Vienna day *preview_days* from today
    moves behind the field, in its original order — after the items
    :func:`_apply_topic_budget` moved back, before the all-clears. Nothing is
    dropped: with fewer running items the announcement still fills a slot,
    and from *preview_days* before its start it holds its normal place, from
    Friday on for one that begins on Monday (:func:`_preview_last_day`). An
    item without a start is never deferred.
    """
    last_day = _preview_last_day(_to_utc(now).astimezone(_VIENNA_TZ).date(), preview_days)
    deferred = [item for item in items if _starts_after(item, last_day)]
    if not deferred:
        return items
    log.info(
        "Angekündigte Meldungen: %d hinter das Feld gestellt (Beginn nach %s).",
        len(deferred),
        last_day.strftime("%d.%m.%Y"),
    )
    return [item for item in items if not _starts_after(item, last_day)] + deferred


def _is_all_clear(item: FeedItem) -> bool:
    """Whether ``item`` reports the end of a disruption rather than one."""
    return bool(_ALL_CLEAR_TITLE_RE.match(str(item.get("title") or "")))


def _defer_all_clear_items(items: list[FeedItem]) -> list[FeedItem]:
    """Let an all-clear take only a slot nothing else needs.

    ÖBB publishes the end of a disruption as its own message ("Aufhebung
    Verkehrseinschränkung: St. Pölten Hauptbahnhof"). It is new, so the FIFO
    sort puts it on top, and on a display with ten slots it held one for about
    an hour, in place of a running disruption (audit 2026-09-25, A.2: three
    such items since August).

    Operator decision 2026-09-25: a running disruption matters more than an
    all-clear, but an all-clear is better than an empty slot. So every
    all-clear moves behind the whole field, in its original order — after the
    items :func:`_defer_repeated_route_titles` and :func:`_apply_topic_budget`
    moved back, too. Nothing is dropped: with fewer than ``MAX_ITEMS`` other
    items the all-clear still fills a slot.
    """
    deferred = [item for item in items if _is_all_clear(item)]
    if not deferred:
        return items
    log.info("Entwarnungen: %d hinter das Feld gestellt.", len(deferred))
    return [item for item in items if not _is_all_clear(item)] + deferred


def _initial_first_seen(item: FeedItem, now_utc: datetime) -> datetime:
    """The ``first_seen`` an item gets when the build sees it for the first time.

    Normally ``now``: the item has just appeared. A Wiener-Linien stop
    relocation or closure ("Haltestellenverlegung …", "Haltestellenauflassung
    …") starts from when WL published it (``pubDate``, WL's ``time.start``)
    instead. Since 2026-10-04 every such notice reaches the feed (operator
    decision "Alle aufnehmen"); before, the fetch dropped 24 of 37 (Update run
    of 2026-10-04 14:00 UTC).
    Counted as new, those 24 — some running since 2023 — would all have led
    the feed at once and filled its ten places. Each takes the place it
    would have had if the fetch had never dropped it: a notice published on
    28.09. for 05.10. ("26E/N20: Fultonstraße") sorts as seen on 28.09., and
    :func:`_defer_upcoming_items` still holds it back until the day before.
    A notice WL publishes from now on is new as before.
    """
    if item.get("source") != "Wiener Linien" or str(item.get("category") or "").casefold() != "hinweis":
        return now_utc
    published = _parse_datetime(item.get("pubDate"))
    if not isinstance(published, datetime):
        return now_utc
    published_utc = _to_utc(published)
    if published_utc >= now_utc:
        return now_utc
    if not STOP_NOTICE_LEAD_RE.match(html_to_text(str(item.get("description") or ""))[:200]):
        return now_utc
    return published_utc


# How long an unplanned disruption counts as current for the feed order —
# see :func:`_is_current_incident`.
_CURRENT_INCIDENT_WINDOW = timedelta(hours=24)


def _is_current_incident(
    item: FeedItem, entry: dict[str, Any] | None, first_seen: datetime, now_utc: datetime
) -> bool:
    """Whether ``item`` is an unplanned disruption that is happening now.

    Operator decision 2026-10-04: "Aktuelle Störungen sollen oberste
    Priorität haben. Vorangekündigte Baustellen sollen eine niedrigere
    Priorität haben." The FIFO sort alone let a fresh announcement or a
    notice newly let through the filter stand above running incidents
    (replayed builds, Vienna time): on 2026-10-03 at 15:01 "43A: Veranstaltung" stood above "9A:
    Rettungseinsatz", on 2026-10-04 at 16:30 "U6: Neue Donau, kein Halt"
    above four incidents (U1, 9, 18, 48A).

    An incident is what the time line shows as "[Seit hh:mm]"
    (:func:`_incident_since`: category "Störung", not planned, not reported
    as over) and has begun. It counts as current for
    ``_CURRENT_INCIDENT_WINDOW`` after it entered the feed: long-running
    items that read like incidents ("18: Haltestelle Stadionbrücke …
    aufgelassen" since 13.07., the daily "S80: ÖBB-Ersatzbus" since 25.09.)
    stay in the normal order.
    """
    if first_seen < now_utc - _CURRENT_INCIDENT_WINDOW:
        return False
    start = _parse_datetime(item.get("starts_at"))
    if not isinstance(start, datetime) or _to_utc(start) > now_utc:
        return False
    published = _first_published(item, dict(entry) if entry else None)
    return _incident_since(item, start, published) is not None


def _baustelle_start_cap(item: FeedItem, moment: datetime, now_utc: datetime) -> datetime:
    """A Stadt Wien Baustelle counts as new no later than on its start day.

    The Baustellen GUID carries the end date, and a site whose end has passed
    leaves the state. When the city extends a site, it returns with a new
    GUID and counted as brand-new: "Burggasse 67", running since 28.07., end
    moved from 02.10. to 30.10. and listed again on 08.10., stood on place 9
    as the newest item of the feed. Its start says what it is: works running
    since July. Like a re-issued WL measure, it keeps its place. A site seen
    before its start is unaffected (:func:`_note_announced_starts`).
    """
    if not _is_baustelle(item):
        return moment
    start = _parse_datetime(item.get("starts_at"))
    if not isinstance(start, datetime):
        return moment
    start_utc = _to_utc(start)
    if start_utc <= now_utc and start_utc < moment:
        return start_utc
    return moment


def _recency_sort_key(
    item: FeedItem, state: dict[str, dict[str, Any]], now_utc: datetime
) -> tuple[int, float, int, float, str]:
    """FIFO-by-``first_seen`` ordering for the feed: newest-appeared first.

    Current incidents (:func:`_is_current_incident`) come before everything
    else; within each of the two groups the rules below apply.

    Sorts by the persisted (guid-stable) ``first_seen`` descending. An item
    not yet in the state counts as just-appeared (``now``) so genuinely new
    disruptions lead. An announced measure counts from its start once that
    has come (:func:`_sort_moment`). No-longer-valid items are already removed upstream by
    :func:`_drop_old_items`, so the visible Top-N are the newest still-valid
    disruptions; older ones fall off the bottom as fresher ones arrive.

    Ties on ``first_seen`` (a batch of items that surfaced in the SAME build —
    e.g. a fresh set of construction sites that all get ``first_seen = now``)
    are broken, in order, by:

    1. **Category priority** (:func:`_category_feed_rank`) — live disruptions
       (``Störung``: WL/ÖBB plus the Stammstrecke delay/cancellation monitor)
       outrank long-running ``Baustelle`` items; everything else (e.g. WL
       ``Hinweis``) sits between. Operator policy: Störungen are more important
       for the feed than Baustellen.
    2. **``pubDate`` descending** — the source's own recency signal; the more
       recently published item leads. A missing/unparseable ``pubDate`` sorts
       last within its group (``-inf`` → ``+inf`` after negation).
    3. **guid** — final deterministic tiebreaker for a stable total order
       (otherwise items tying on all of the above would shuffle between builds).
    """
    _, entry = _lookup_state(item, state)
    first_seen = _parse_first_seen(entry, None) or _initial_first_seen(item, now_utc)
    first_seen = _sort_moment(entry, first_seen, now_utc)
    first_seen = _baustelle_start_cap(item, first_seen, now_utc)
    pub = _parse_datetime(item.get("pubDate"))
    pub_ts = pub.timestamp() if isinstance(pub, datetime) else float("-inf")
    # Clamp a future pubDate to now: a bogus future publication date must not
    # rank an item ahead of genuinely-current items in this pubDate tiebreaker.
    pub_ts = min(pub_ts, now_utc.timestamp())
    guid_val = item.get("guid")
    guid_str = str(guid_val) if guid_val else _identity_for_item(item)
    tier = 0 if _is_current_incident(item, entry, first_seen, now_utc) else 1
    return (tier, -first_seen.timestamp(), _category_feed_rank(item), -pub_ts, guid_str)


def _build_canonical_link(candidate: Any, ident: str) -> str:
    """Return a canonical link for ``ident`` with a stable fallback anchor."""

    if isinstance(candidate, str):
        normalized = candidate.strip()
        if normalized:
            return normalized

    slug_source = ident or ""
    slug = quote(slug_source, safe="")
    base = (feed_config.FEED_LINK or "").strip()
    if not base:
        return f"#meldung-{slug}" if slug else ""

    anchor_prefix = "meldung"
    base = base.rstrip("/")
    if slug:
        return f"{base}#{anchor_prefix}-{slug}"
    return base


def _resolve_item_link(candidate: Any, ident: str) -> str:
    """Return the per-item ``<link>`` value, enforcing HTTPS-only.

    Security: ``validate_http_url`` accepts both ``http`` and ``https``.
    Without an HTTPS-only pin at this boundary a future upstream
    regression (legitimate or attacker-injected) that returned
    ``http://`` URLs would publish plaintext ``<link>`` elements into
    ``docs/feed.xml``. Every subscriber's RSS reader follows the
    ``<link>`` click via a fresh request and many do NOT consult the
    HSTS preload list before the click — a plaintext URL is therefore
    a documented TLS-strip primitive on the subscriber base.

    Three-step resolution mirrors the canonical pattern used by
    ``validate_public_feed_url`` (which the ``feed_config.FEED_LINK``
    fallback is itself validated by):

    1. Build canonical link via ``_build_canonical_link``.
    2. Pass through ``validate_http_url`` (SSRF / syntax / scheme).
    3. Reject ``http://`` results — fall back to the HTTPS-pinned
       ``feed_config.FEED_LINK`` so the published feed never carries
       ``<link>http://...</link>``.

    Mirrors the HTTPS-only pin from the 2026-05-09 *Public Feed URL
    Allow-List Drift* round and the 2026-05-10 *HTTPS-only Provider
    URL Drift* rounds (PRs #1415 / #1416) for every other publishing
    surface.
    """
    raw_link = _build_canonical_link(candidate, ident)
    sanitized = validate_http_url(raw_link, check_dns=False) if raw_link else ""
    if sanitized and not sanitized.lower().startswith("https://"):
        log.warning(
            "Item %s carries plaintext http:// link; falling back to "
            "feed link to prevent TLS-strip on subscribers.",
            ident,
        )
        sanitized = ""
    if raw_link and not sanitized:
        log.warning(
            "Item %s has potentially unsafe/invalid link %r; falling back to feed link.",
            ident,
            raw_link,
        )
    return sanitized or feed_config.FEED_LINK


@dataclasses.dataclass(frozen=True, slots=True)
class _ItemSource:
    """Who an item comes from: its ``<source>`` tag and its link target.

    ``hosts`` are the domains an item link may point to; any other link
    (a dead catalogue page, another operator's site) is replaced by
    ``link``, the operator's own page for its current notices.
    """

    name_de: str
    name_en: str
    url: str
    hosts: tuple[str, ...]
    link: str


_OEBB_STRECKENINFO_URL = (
    "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&"
)
_WL_BETRIEBSINFO_URL = "https://www.wienerlinien.at/betriebsinfo"
_WIEN_BAUSTELLEN_URL = "https://www.wien.gv.at/verkehr/baustellen"

# Michael, 17.10.2026: "Meldungen von der ÖBB sollen zu ÖBB verlinken und eine
# Quellenangabe als Tag beinhalten. Meldungen der Wiener Linien sollen zu den
# Wiener Linien verlinken und eine Quellenangabe als Tag für die Wiener Linien
# haben. Das Selbe für Baustellenmeldungen der Stadt Wien u.s.w." So every
# item links to the operator it is about and carries an RSS ``<source>``
# naming where it comes from. The Stammstrecke monitor reports ÖBB S-Bahn
# trains, so it links to the ÖBB-Streckeninformation like the ÖBB notices
# (his yes 17.10.2026), while its tag names the data behind it, VOR/VAO.
# Keyed by the item's ``source`` field; an unknown source keeps its link and
# gets no tag.
_ITEM_SOURCES: dict[str, _ItemSource] = {
    "Wiener Linien": _ItemSource(
        "Wiener Linien",
        "Wiener Linien",
        _WL_BETRIEBSINFO_URL,
        ("wienerlinien.at",),
        _WL_BETRIEBSINFO_URL,
    ),
    "ÖBB": _ItemSource(
        "ÖBB", "ÖBB", _OEBB_STRECKENINFO_URL, ("oebb.at",), _OEBB_STRECKENINFO_URL
    ),
    "Stadt Wien – Baustellen": _ItemSource(
        "Stadt Wien",
        "City of Vienna",
        _WIEN_BAUSTELLEN_URL,
        ("wien.gv.at",),
        _WIEN_BAUSTELLEN_URL,
    ),
    "VOR/VAO": _ItemSource(
        "VOR/VAO",
        "VOR/VAO",
        "https://www.verkehrsauskunft.at/start",
        ("oebb.at",),
        _OEBB_STRECKENINFO_URL,
    ),
}


# Michael, 19.10.2026: the TV slides (EasySignage) shall all look the same
# grey; he does not like the red Wiener-Linien background. Without an image of
# its own, EasySignage shows the preview image (og:image) of the item link, and
# wienerlinien.at/betriebsinfo serves either a missing "image.png" (grey slide)
# or the red WL logo (red slide), at random. So every item of every source
# carries the same image of our own as ``<enclosure>`` and ``<media:content>``;
# under EasySignage's dark layer white reads as the grey he chose. The links
# stay with their operators (see ``_ITEM_SOURCES``).
# Michael, 23.10.2026: the grey gets a woodchip-wallpaper ("Raufaser") look in
# Full HD, in the same grey. The image is a near-white woodchip wall computed
# by scripts/make_feed_item_image.py (no photo); under the dark layer its
# median is the former grey, the chips show as soft shadows.
# ``_ITEM_IMAGE_BYTES`` is the file size RSS 2.0 asks for in ``length``;
# tests/test_item_image.py keeps it equal to the file in docs/assets/.
_ITEM_IMAGE_URL = "https://origamihase.github.io/wien-oepnv/assets/feed-item-raufaser.jpg"
_ITEM_IMAGE_BYTES = 281823
_ITEM_IMAGE_TYPE = "image/jpeg"
_ITEM_IMAGE_WIDTH = 1920
_ITEM_IMAGE_HEIGHT = 1080


def _add_item_image(item: ET.Element) -> None:
    """Attach the shared item image (see ``_ITEM_IMAGE_URL``)."""
    enclosure = ET.SubElement(item, "enclosure")
    enclosure.set("url", _ITEM_IMAGE_URL)
    enclosure.set("length", str(_ITEM_IMAGE_BYTES))
    enclosure.set("type", _ITEM_IMAGE_TYPE)
    media = ET.SubElement(item, f"{{{MEDIA_NS}}}content")
    media.set("url", _ITEM_IMAGE_URL)
    media.set("type", _ITEM_IMAGE_TYPE)
    media.set("medium", "image")
    media.set("width", str(_ITEM_IMAGE_WIDTH))
    media.set("height", str(_ITEM_IMAGE_HEIGHT))


def _shown_end(it: FeedItem, ends_at: object) -> datetime | None:
    """The end the item's time line names, or ``None``.

    WL's 11:11 expiry date is no end when the text names none of its own
    (:func:`wl_plausibility.end_unknown`, operator decision 2026-10-08). The
    time line leaves it out, and so does ``ext:ends_at``, which the website
    shows as "Bis": "48A: Neubaugasse, Neustiftgasse" read "[Seit 19.10.]" on
    the TV and "Bis: 08.10.2027, 11:11" on the website. The item keeps the
    date as its expiry inside the pipeline.
    """
    end = ends_at if isinstance(ends_at, datetime) else None
    if _is_wl_notice(it) and end_unknown(str(it.get("description") or ""), end):
        return None
    return end


def _item_source(it: Any) -> _ItemSource | None:
    """Return the :class:`_ItemSource` for item ``it`` or ``None`` when unknown."""
    source = it.get("source")
    return _ITEM_SOURCES.get(source) if isinstance(source, str) else None


def _link_on_hosts(link: str, hosts: tuple[str, ...]) -> bool:
    """Return whether ``link`` points to one of ``hosts`` or a subdomain."""
    host = (urlparse(link).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def _source_item_link(it: Any, ident: str) -> str:
    """Return the item ``<link>``: the resolved link while it stays with the
    item's operator, else the operator's own notices page."""
    link = _resolve_item_link(it.get("link"), ident)
    src = _item_source(it)
    if src is not None and not _link_on_hosts(link, src.hosts):
        return src.link
    return link


def _cdata_content(s: str) -> str:
    """Prepare a string for inclusion in a CDATA block, handling ']]>'."""
    return s.replace("]]>", "]]]]><![CDATA[>")


def _placeholder_collides_with_formatted(
    ph_content: str, ph_title: str, formatted: FormattedContent
) -> bool:
    """Return True if either placeholder appears in any text-bearing field
    of *formatted*.

    Security (CDATA placeholder collision drift closure):
    ``_emit_item`` injects ``ph_content`` / ``ph_title`` as the ``.text``
    of ``<content:encoded>`` / ``<title>`` ElementTree elements and
    later substitutes them via a global ``xml_str.replace(...)`` pass
    for CDATA-wrapped content. The downstream replace is element-agnostic
    — any occurrence of either placeholder ANYWHERE in the serialised
    XML gets replaced, including inside ``<link>`` / ``<guid>`` /
    ``<description>`` element text whose values come from upstream-
    controlled fields. Pre-fix the loop only verified absence in three
    of the eight ``FormattedContent`` text fields (``desc_html``,
    ``raw_desc``, ``title_out``); the remaining five (``link``, ``guid``,
    ``desc_text_truncated``, ``title_cdata``, ``desc_cdata``) leaked
    upstream-controlled spans into the placeholder-replacement target
    set without a collision check. An upstream item whose ``link`` or
    ``guid`` coincidentally matched the random placeholder pattern
    would corrupt the serialised RSS XML (CDATA injected into the
    wrong element, original element text consumed by the replacement).
    Practical exploitability requires predicting a 128-bit UID
    (astronomically low for a remote attacker), but the project's
    Zero-Trust upstream contract (AGENTS.md §3) demands defense-in-
    depth at every upstream-data boundary regardless of practical
    exploitability — a legitimate URL that happens to embed the
    placeholder pattern (highly unlikely yet possible) would silently
    corrupt the public feed. The check examines every text-bearing
    ``FormattedContent`` field, mirroring the ``isinstance`` Zero-Trust
    guards added at every other upstream JSON-parse site
    (``src/providers/vor.py``, ``src/providers/wl_fetch.py``).
    """
    text_fields = (
        formatted.link,
        formatted.guid,
        formatted.title_out,
        formatted.title_cdata,
        formatted.desc_html,
        formatted.desc_cdata,
        formatted.desc_text_truncated,
        formatted.raw_desc,
    )
    return any(ph_content in field or ph_title in field for field in text_fields)


class FormattedContent(NamedTuple):
    guid: str
    link: str
    title_cdata: str
    desc_text_truncated: str
    desc_cdata: str
    raw_desc: str
    title_out: str
    desc_html: str


def _update_item_state(it: FeedItem, now: datetime, state: dict[str, dict[str, Any]]) -> tuple[str, datetime]:
    ident, st = _lookup_state(it, state)
    is_strictly_new = not st
    if not st:
        st = {"first_seen": _initial_first_seen(it, _to_utc(now)).isoformat()}
    # A recurring WL disruption keeps its entry (the guid holds no date) but
    # begins a new occurrence (:func:`_restart_recurring_occurrences`). It is
    # a new disruption for the statistics too: "U6: Rettungseinsatz" on
    # 25.10. is not the one of 23.10. From 25.09. (when the restart came) to
    # 27.10.2026 1,060 such occurrences reached the feed uncounted, the old rule
    # counted 348 in the same replay of all 1,690 builds); the rows were added to
    # the ledger afterwards (data/stats/README.md).
    is_new_occurrence = is_strictly_new or bool(st.pop(_NEW_OCCURRENCE_KEY, False))
    state[ident] = st
    _note_announced_starts([it], state, now)
    # Legacy-key migration cleanup: ``_lookup_state`` returns the modern
    # guid-shaped key in ``ident``, but on a guid-key miss it falls back
    # to the legacy ``_identity_for_item`` entry to migrate forward. Pre-
    # fix we wrote the entry under the new guid key but left the legacy
    # entry on disk; ``STATE_RETENTION_DAYS`` (60d) bounded the bloat,
    # but every subsequent build kept comparing the same now-redundant
    # legacy key in ``_lookup_state``'s fallback path until the prune
    # eventually fired. Dropping the legacy entry once the migration has
    # written the new key keeps ``data/first_seen.json`` tidy and saves
    # the legacy-lookup cost on every cycle.
    legacy = _identity_for_item(it)
    if legacy != ident and legacy in state:
        state.pop(legacy, None)

    try:
        fs_dt = datetime.fromisoformat(st["first_seen"])
    except Exception:
        log.warning("first_seen Parsefehler: %r – fallback to now", st.get("first_seen"))
        fs_dt = _to_utc(now)
        st["first_seen"] = fs_dt.isoformat()

    # Stats: log the first appearance of each occurrence of a disruption
    # into ``data/stats/stoerungen_YYYY.csv``: a state-cache miss above
    # (neither ``ident`` nor ``guid`` had a prior entry) or an entry marked
    # as a new occurrence. Reruns of the same occurrence — a long-lived ÖBB
    # Streckeninformation that survives many feed builds — record a single
    # row. Best-effort: any I/O failure inside ``append_disruption_row`` is
    # logged and swallowed so the feed build itself never blocks on stats I/O.
    if is_new_occurrence:
        try:
            append_disruption_row(
                timestamp=_to_utc(now),
                provider=str(it.get("source") or "unbekannt"),
                location_name=extract_location_name(cast(dict[str, Any], it)),
            )
        except Exception as exc:  # pragma: no cover - defensive; writer is no-throw
            log.warning(
                "Disruption-Stats konnten nicht geschrieben werden: %s",
                sanitize_log_arg(str(exc)),
            )
    return ident, fs_dt


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
    behind the last sentence that fits (:func:`_last_sentence_end`); only
    when none does is it cut mid-sentence as before.
    """
    text = f"{_as_sentence(fragment)} {uncut_summary}"
    if len(text) > 180:
        end = _last_sentence_end(text, 180, start=len(fragment))
        if end is not None:
            text = text[:end]
    return _strip_trailing_directional_marker(_truncate_summary_180(text))


# A period between a word and a capitalised word, as candidates for a
# sentence end in :func:`_last_sentence_end`.
_LOOSE_SENTENCE_END_RE = re.compile(r"(?<=\w)\.(?=\s+[A-ZÄÖÜ])")
_WORD_BEFORE_PERIOD_RE = re.compile(r"(\w+)$")
# Abbreviations of three letters or more that WL texts put before a
# capitalised word ("Bahnhst bzw. Gerasdorf", "Wien Hbf. Richtung …").
_NOT_A_SENTENCE_END: frozenset[str] = frozenset({
    "bzw", "usw", "ggf", "ggü", "etc", "vgl", "sog", "evtl", "inkl", "hbf", "str",
})


def _last_sentence_end(text: str, limit: int, *, start: int = 0) -> int | None:
    """End (after the period) of the last sentence in ``text[start:limit]``.

    Looser than :data:`_SENTENCE_SPLIT_RE`: three letters before the period
    suffice ("aus.", "Uhr."), because this only picks where to cut an
    over-long text. Numbers ("17. Februar"), one- and two-letter
    abbreviations ("U.", "b.", "Nr.") and :data:`_NOT_A_SENTENCE_END` never
    end a sentence.
    """
    best: int | None = None
    for match in _LOOSE_SENTENCE_END_RE.finditer(text, start, limit):
        word = _WORD_BEFORE_PERIOD_RE.search(text, 0, match.start())
        if word is None:
            continue
        token = word.group(1)
        if len(token) < 3 or token.isdigit() or token.casefold() in _NOT_A_SENTENCE_END:
            continue
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


def _wl_incident_start(item: FeedItem) -> datetime | None:
    """The earliest start of *item*'s WL incident messages (``wl_fetch``), if it has one."""
    return _parse_datetime(item.get("_incident_start"))


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


def _as_sentence(text: str) -> str:
    """*text* with a full stop, unless it ends in one of its own ("… Linie 46!").

    Also closes a ticker's consequence in both branches of
    :func:`_finish_reason_title`; only the one with more text behind it
    used to, so the feed showed "Betrieb ab Schwedenplatz U." under one
    ticker and "Busse halten bei Haltestelle N71" under the next.
    """
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


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


def _text_names_line(text: str, line: str) -> bool:
    """Whether *text* speaks of *line* ("Linie 30A", "Linien 30A und 31A", "Linie 38:")."""
    return re.search(
        rf"\bLinien?\s+(?:[\w]+(?:,\s*|\s+und\s+|\s+sowie\s+))*{re.escape(line)}\b", text
    ) is not None


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


def _is_wl_notice(item: FeedItem) -> bool:
    return (
        str(item.get("source") or "").strip().casefold() == "wiener linien"
        and str(item.get("category") or "").strip().casefold() == "hinweis"
    )


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


def _is_baustelle(item: FeedItem) -> bool:
    return str(item.get("source") or "").strip().casefold().startswith("stadt wien")


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


# --- One message, one GUID while it runs (2026-10-06) ------------------------
#
# A merged item carries the GUID and the ``pubDate`` of its messages: the
# GUID of the earliest one or of the long message that says more
# (``_incident_entry``), of the survivor of ``deduplicate_fuzzy``; the
# earliest ``pubDate`` (``_span_group``). When the message whose GUID or
# time the item carries leaves the data before the others, the item takes
# the next one's, and the feed shows the same disruption as a new item under
# a new time. "43: Verkehrsunfall" on 2026-10-06: its display tickers (the
# first one from 00:03:28 Vienna time) ran out at 01:03, the long message
# (start 00:04) stood alone, and the rebuild at 01:14, without new data,
# showed it under the long message's GUID and "[Seit 00:04]" instead of
# "[Seit 00:03]". At 11:30 "1: Verkehrsunfall" lost its long message (start
# 10:40, resolved) and read "[Seit 10:43]" under the GUID of a ticker. On
# 05.10. at 01:30 "41/42: Verkehrsunfall" changed its GUID when WL replaced
# the incident by one follow-up per line, at 19:01 "9: Falschparker" when
# the 42 of "9/42" ended.
#
# So every build notes in the state entry of each item the messages it
# stands for (``members``: their GUIDs and WL's incident numbers, see
# ``member_guids``) and when (``members_seen``); a Wiener-Linien disruption
# also the earliest ``pubDate`` it carried (``earliest_published``). An item
# that was no item of a build within _OCCURRENCE_GAP under its own GUID
# takes the GUID of the item that shared one of its messages there, if it
# continues that item (``_continued_by``); an item continued so keeps that
# earliest ``pubDate``, the begin its time line showed. Nothing changes for
# an item whose messages stay; the first build after the change writes the
# notes and changes no GUID. An entry of several incidents counts the
# messages of the newest one only (``_merged_run``): a new incident still
# opens the feed under its own GUID, and the entry falls back to the older
# one's when it ends.


def _noted_recently(entry: dict[str, Any] | None, now_utc: datetime) -> datetime | None:
    """When a build within _OCCURRENCE_GAP last noted *entry*'s messages, if one did."""
    seen = _parse_state_time(entry, "members_seen")
    if seen is None or seen < now_utc - _OCCURRENCE_GAP:
        return None
    return seen


def _continues(entry: dict[str, Any] | None, item: FeedItem, now_utc: datetime) -> bool:
    """Whether *item* continues the item *entry* noted.

    It does if a build within _OCCURRENCE_GAP noted the entry and *item*'s
    message was published by then: WL gives a recurring disruption the GUID
    of the last one ("43: Verkehrsunfall"), and a new one keeps its own time.
    """
    seen = _noted_recently(entry, now_utc)
    published = _parse_datetime(item.get("pubDate"))
    return (
        seen is not None
        and isinstance(published, datetime)
        and _to_utc(published) <= seen
        and not _other_incident(entry, item)
    )


def _other_incident(entry: dict[str, Any] | None, item: FeedItem) -> bool:
    """Whether WL numbers *item* and the item *entry* noted as different incidents.

    Both carry incident numbers and share none: WL closed one disruption
    and opened another for the same lines. "13A/14A: Falschparker" on
    2026-10-06: ``I20261006-0030`` (start 17:22) ended, ``I20261006-0033``
    (start 17:44) took its GUID and showed "[Seit 17:22]". The new one
    keeps its own begin. Without a number on either side (display tickers)
    nothing says so.
    """
    members = entry.get("members") if isinstance(entry, dict) else None
    if not isinstance(members, list):
        return False
    noted = {m for m in members if isinstance(m, str) and m.startswith("wl:")}
    own = {m for m in member_guids(item) if m.startswith("wl:")}
    return bool(noted) and bool(own) and noted.isdisjoint(own)


def _continued_by(entry: dict[str, Any], item: FeedItem, own: dict[str, Any] | None = None) -> bool:
    """Whether *item*, which shares a message with the item *entry* noted, continues it.

    Never if *item* stood in the feed under its own GUID before that item
    did (*own*, its state entry: noted by a build since its ``first_seen``,
    which is older than the other's): it was there first, and the other
    took it up. The stops of the line 25 track works (in the feed since
    23.09.) became members of "25: Verspätungen" on 2026-10-09 at 09:36,
    before ``_with_unowned`` kept them out; when the delay ended they
    carried its GUID and begin and stood as a new disruption on up to
    place 2, and every later build carried them on again. An entry no
    build noted in this occurrence does not count: its ``first_seen`` may
    be the message's own start (``_restart_recurring_occurrences``), and
    WL dates a long message back ("60: Schadhafter PKW" on 2026-10-01,
    dated 18:48, came after the tickers of 18:50 and carries their GUID).

    It does if the message whose GUID *item* carries was in that item: its
    other messages ran out ("43: Verkehrsunfall"). Or if that message is no
    newer than the begin the item showed: WL enters the long message after
    its tickers and dates it back (``I20261006-0012-F01``, "1:
    Verkehrsunfall", created 10:54 with start 10:40, tickers from 10:43).
    A newer message is a new incident that took up a running one ("52:
    Rettungseinsatz" on 2026-10-05 with a ticker of 30.09.): it keeps its
    own GUID and opens the feed. So does one WL numbers as another incident
    (``_other_incident``).
    """
    if _other_incident(entry, item):
        return False
    first, owner_first = _parse_state_time(own, "first_seen"), _parse_state_time(entry, "first_seen")
    noted = _parse_state_time(own, "members_seen")
    if first is not None and owner_first is not None and noted is not None and first < owner_first and noted >= first:
        return False
    members = entry.get("members")
    if isinstance(members, list) and str(item.get("guid") or "") in members:
        return True
    begin = _parse_state_time(entry, "earliest_published")
    published = _parse_datetime(item.get("pubDate"))
    return begin is not None and isinstance(published, datetime) and _to_utc(published) <= begin


def _predecessors(state: dict[str, dict[str, Any]], now_utc: datetime) -> dict[str, list[str]]:
    """Message GUID → GUIDs of the items recent builds noted it in."""
    owners: dict[str, list[str]] = defaultdict(list)
    for guid, entry in state.items():
        members = entry.get("members") if isinstance(entry, dict) else None
        if not isinstance(members, list) or _noted_recently(entry, now_utc) is None:
            continue
        for member in members:
            if isinstance(member, str):
                owners[member].append(guid)
    return owners


def _carry_item_identity(
    items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> list[FeedItem]:
    """*items* with the GUID and begin they had while they run (see above)."""
    now_utc = _to_utc(now)
    owners = _predecessors(state, now_utc)
    taken = {str(item.get("guid")) for item in items if item.get("guid")}
    claims: list[tuple[int, int, str]] = []
    for index, item in enumerate(items):
        guid = str(item.get("guid") or "")
        if not guid or _noted_recently(state.get(guid), now_utc) is not None:
            continue
        shared: dict[str, int] = defaultdict(int)
        for member in member_guids(item):
            for owner in owners.get(member, ()):
                shared[owner] += 1
        claims.extend(
            (-count, index, owner)
            for owner, count in shared.items()
            if owner not in taken and _continued_by(state[owner], item, state.get(guid))
        )
    out = list(items)
    carried: set[int] = set()
    for _count, index, owner in sorted(claims):
        if index in carried or owner in taken:
            continue
        item = cast(FeedItem, dict(items[index]))
        item["_members"] = member_guids(item)  # its own message stays one of them
        log.info(
            "Meldung behält ihre GUID: %s (%s statt %s)",
            sanitize_log_arg(_rendered_title(item)),
            owner[:12],
            str(item.get("guid"))[:12],
        )
        item["guid"] = owner
        out[index] = item
        carried.add(index)
        taken.add(owner)
    for index, item in enumerate(out):
        if not _is_wl_disruption(item):
            continue
        entry = state.get(str(item.get("guid") or ""))
        earliest = incident_begin(_parse_state_time(entry, "earliest_published"), _wl_incident_start(item))
        published = _parse_datetime(item.get("pubDate"))
        if earliest is None or not isinstance(published, datetime) or not _continues(entry, item, now_utc):
            continue
        if earliest < _to_utc(published):
            moved = cast(FeedItem, dict(item))
            moved["pubDate"] = earliest
            out[index] = moved
    return out


def _remember_item_identity(
    items: Sequence[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> None:
    """Note in each item's state entry what ``_carry_item_identity`` reads back.

    Only entries that exist: the build creates them for what it shows.
    """
    now_utc = _to_utc(now)
    for item in items:
        guid = str(item.get("guid") or "")
        entry = state.get(guid) if guid else None
        if entry is None:
            continue
        published = _parse_datetime(item.get("pubDate"))
        if _is_wl_disruption(item) and isinstance(published, datetime):
            earliest = _to_utc(published)
            known = _parse_state_time(entry, "earliest_published")
            if known is not None and known < earliest and _continues(entry, item, now_utc):
                earliest = known
            entry["earliest_published"] = earliest.isoformat()
        entry["members"] = member_guids(item)
        entry["members_seen"] = now_utc.isoformat()


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
    found = _relocation_first(found, title_words)
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
