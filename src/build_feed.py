from __future__ import annotations

import dataclasses
import json
import os
import sys
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import Callable
from datetime import datetime, UTC
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from .feed_types import FeedItem
from . import ki_entscheidung
from .feed import config as feed_config
# ``as`` re-exports explicitly: tests read the tables from ``build_feed``.
from .feed.glossary import (
    _FIELD_LABEL_EN as _FIELD_LABEL_EN,
    _GLOSSARY_BASE as _GLOSSARY_BASE,
    _GLOSSARY_BY_CATEGORY as _GLOSSARY_BY_CATEGORY,
    _GLOSSARY_BY_SOURCE as _GLOSSARY_BY_SOURCE,
)
from .feed.merge import deduplicate_fuzzy
from .feed.logging import configure_logging
from .feed.providers import load_provider_plugins, provider_statuses
from .feed.reporting import DuplicateSummary, FeedHealthMetrics, RunReport
from .utils.files import atomic_write
from .utils.locking import file_lock
from .utils.logging import sanitize_log_arg

# The parts below were moved verbatim to src/feed_build/ (700-line rule,
# Etappe 8). Every name src/, scripts/ or the tests read stays importable here;
# a test patches a name in the module whose code reads it.
from .feed_build.base import (
    MEDIA_NS, log, validate_path, _VIENNA_TZ, _SENTENCE_SPLIT_RE, _to_utc, _fmt_rfc2822, _CONTROL_RE,
    _sanitize_text, _ymd_or_none, _normalize_item_datetimes,
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
    _section_collisions, _split_reason_title, _CATEGORY_PREFIX_WORDS, _TITLE_REASON_WORDS,
)
from .feed_build.oebb_summary import (
    _oebb_upcoming_dates, _oebb_long_date_lists_in_words, _oebb_what_does_not_run_first,
    _oebb_section_labels_cased, _OEBB_LABELLED_SENTENCE_RE, _oebb_section_label_en,
)
from .feed_build.translator import (
    _TRANSLATION_STATE, _TRANSLATION_MODEL_REVISION, _MarianTranslator, _get_translation_pipeline,
    _record_translation_status,
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
    _oebb_cannot_run_parts, _render_oebb_follow_up, _WL_SENTENCE_SPLIT_RE, _RELOCATION_CLOSED_LINE_RE,
)
from .feed_build.translate_run import (
    _render_label_record, _render_record_table, _oebb_cause_en, _render_oebb_sentence, _render_wl_sentence,
    _render_wl_because_sentence, _translate_text_attempt,
)
from .feed_build.translate_titles import _translate_text, _translate_title_attempt
from .feed_build.wl_measures import (
    _WL_DESC_LINIE_PREFIX_RE, _strip_wl_description_line_prefix, _WL_DESC_LINE_SEGMENT_SPLIT_RE,
    _collapse_repeated_wl_line_segments, _tidy_wl_dangling_location, _wl_measure_labels_with_colon,
    _prefer_measures, _lines_in_short,
)
from .feed_build.cache_filters import (
    _attribute_obb_replacement_bus, _post_filter_wl, _post_filter_oebb, _post_filter_baustellen,
)
from .feed_build.providers_setup import (
    reset_module_state, init_providers, _detect_stale_caches, _provider_statuses, _log_startup_summary,
    _validate_configuration, _read_optional_non_negative_int, _provider_timeout_override, _call_fetch_with_timeout,
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
    _is_all_clear,
)
from .feed_build.summary_text import (
    _strip_trailing_directional_marker, _reason_only_summary, _drop_passed_expected_end, _expected_end_sentence,
    _WL_REFERRAL_BOILERPLATE_RE, _truncate_summary_180, _compose_description,
)
from .feed_build.title_words import (
    _leading_category_word, _separate_reason_word, _reason_and_fragment, _last_sentence_end,
    _short_title_collisions, _is_wl_ticker,
)
from .feed_build.item_history import (
    _OCCURRENCE_GAP, _restart_recurring_occurrences, _note_announced_starts, _count_new_items, _initial_first_seen,
    _carry_item_identity, _remember_item_identity,
)
from .feed_build.item_meta import (
    _sort_moment, _CURRENT_INCIDENT_WINDOW, _is_current_incident, _recency_sort_key, _build_canonical_link,
    _ITEM_IMAGE_URL, _ITEM_IMAGE_BYTES, _ITEM_IMAGE_WIDTH, _ITEM_IMAGE_HEIGHT,
    _placeholder_collides_with_formatted, FormattedContent, _update_item_state,
)
from .feed_build.filters import (
    _valid_until, _drop_after, _drop_old_items, _summarize_duplicates, _dedupe_items, _topic_budget_key,
    _apply_topic_budget, _defer_repeated_route_titles, _is_test_message, _drop_test_messages, _preview_last_day,
    _starts_after, _defer_upcoming_items, _defer_all_clear_items,
)
from .feed_build.oebb_lead import (
    _oebb_lead_section_end, _end_with_oebb_lead_section, _with_second_sentence, _oebb_sentence_without_place,
    _oebb_first_two_sentences,
)
from .feed_build.translation_epoch import (
    _TRANSLATION_CACHE_EPOCH, _VERBATIM_FIELDS_KEY, _SOURCE_DIGEST_KEY, _source_digest, _record_source_digest,
    _evict_stale_translations,
)
from .feed_build.translation_cache import (
    _TIME_WORDS_DE_TO_EN, _translate_time_line_en, _cached_translation_defect, _oebb_section_label_defect,
    _oebb_cannot_run_defect, _cached_translation, _apply_lang_overlay,
)
from .feed_build.item_format import (
    _strip_summary_category_prefix, _summary_is_title_without_reason, _format_item_content,
    _escape_description_markup,
)
from .feed_build.wl_tickers import _shared_openings, _union_prefix
from .feed_build.wl_ticker_text import _title_cause
from .feed_build.wl_joins import _split_by_cause, _merge_wl_ticker_clusters
from .feed_build.wl_notices import _drop_baustellen_twins, _absorb_works_tickers, _yield_notices_to_incidents
from .feed_build.rss import _item_geometry, _emit_item, _channel_metadata, _make_rss
from .feed_build.isolation import build_isolating
# Since 2026-11-14 (not verbatim): the report of ``lint`` and the blocks of
# ``main`` that write feed, state and health report, as functions.
from .feed_build.lint_report import print_lint_report
from .feed_build.outputs import (
    health_metrics_from, write_health_outputs, write_feed, write_en_mirror, save_state_or_warn,
    record_successful_build,
)


__all__ = [
    "RunReport", "feed_config", "MEDIA_NS", "_ARTICLE_BEFORE_STREET_RE", "_CATEGORY_PREFIX_WORDS", "_CONTROL_RE",
    "_DATE_ENTITY_RE", "_DISPLAY_TITLE_TARGET", "_ENTITY_PLACEHOLDER_FORMAT", "_ENTITY_PLACEHOLDER_RE",
    "_GLOSSARY_PLACEHOLDER_FORMAT", "_LABEL_RECORD_RE", "_LINE_ENTITY_RE", "_MIN_LABELS_FOR_RECORD",
    "_MarianTranslator", "_OEBB_LABELLED_SENTENCE_RE", "_PLACEHOLDER_NONCE", "_RELOCATION_CLOSED_LINE_RE",
    "_RESIDUAL_PLACEHOLDER_RE", "_SENTENCE_SPLIT_RE", "_STREET_SUFFIX_RE", "_TITLE_REASON_WORDS",
    "_TRAM_LETTER_LINE_RE", "_TRANSLATION_MODEL_REVISION", "_TRANSLATION_STATE", "_UNMASK_PLACEHOLDER_RE",
    "_VIENNA_TZ", "_WL_SENTENCE_SPLIT_RE", "_agreeing_article", "_apply_domain_glossary",
    "_capitalise_glossary_after_stop", "_capitalise_sentence_start", "_capitalise_title_body", "_cause_due_to",
    "_compact_baustellen_section", "_compact_line_prefix", "_declined_station_names", "_display_title",
    "_display_title_parts", "_drop_article_before_line", "_drop_article_before_street", "_drop_unpaired_quote",
    "_english_weekdays_capitalised", "_entities_dropped_by_translation", "_fix_glossary_articles", "_fmt_rfc2822",
    "_from_short_placeholders", "_get_translation_pipeline", "_has_short_placeholder_shape",
    "_is_non_translatable_content", "_join_record", "_leftover_placeholder", "_mark_house_numbers",
    "_mask_entities", "_norm_metadata", "_normalise_for_translation", "_normalise_placeholder_debris",
    "_normalize_item_datetimes", "_oebb_cannot_run_parts", "_oebb_cause_en", "_oebb_long_date_lists_in_words",
    "_oebb_section_label_en", "_oebb_section_labels_cased", "_oebb_upcoming_dates",
    "_oebb_what_does_not_run_first", "_ordnungsnummer_as_nr", "_placeholder_nonce", "_record_translation_status",
    "_redirected_towards", "_render_label_record", "_render_oebb_follow_up", "_render_oebb_sentence",
    "_render_record_table", "_render_wl_because_sentence", "_render_wl_sentence", "_repair_baustellen_title",
    "_resolve_glossary", "_respace_glued_placeholders", "_sanitize_text", "_section_collisions",
    "_shorten_wl_sentence_title", "_split_label_record", "_split_reason_title", "_station_base_forms",
    "_station_entity_names", "_street_names_nominative", "_to_short_placeholders", "_to_utc", "_translate_text",
    "_translate_text_attempt", "_translate_title_attempt", "_unmask_entities", "_ymd_or_none", "validate_path",
    "_FIELD_LABEL_EN", "_GLOSSARY_BASE", "_GLOSSARY_BY_CATEGORY", "_GLOSSARY_BY_SOURCE", "json", "file_lock",
    "_WL_DESC_LINIE_PREFIX_RE", "_strip_wl_description_line_prefix", "_WL_DESC_LINE_SEGMENT_SPLIT_RE",
    "_collapse_repeated_wl_line_segments", "_tidy_wl_dangling_location", "_wl_measure_labels_with_colon",
    "_prefer_measures", "_lines_in_short", "_attribute_obb_replacement_bus", "_post_filter_wl",
    "_post_filter_oebb", "_post_filter_baustellen", "reset_module_state", "_detect_stale_caches",
    "_provider_statuses", "_read_optional_non_negative_int", "_provider_timeout_override",
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
    "_defer_all_clear_items", "_oebb_lead_section_end", "_end_with_oebb_lead_section", "_with_second_sentence",
    "_oebb_sentence_without_place", "_oebb_first_two_sentences", "_TRANSLATION_CACHE_EPOCH",
    "_VERBATIM_FIELDS_KEY", "_SOURCE_DIGEST_KEY", "_source_digest", "_record_source_digest",
    "_evict_stale_translations", "_TIME_WORDS_DE_TO_EN", "_translate_time_line_en", "_cached_translation_defect",
    "_oebb_section_label_defect", "_oebb_cannot_run_defect", "_cached_translation", "_apply_lang_overlay",
    "_strip_summary_category_prefix", "_summary_is_title_without_reason", "_format_item_content",
    "_escape_description_markup", "_shared_openings", "_union_prefix", "_title_cause", "_split_by_cause",
    "_merge_wl_ticker_clusters", "_drop_baustellen_twins", "_absorb_works_tickers", "_yield_notices_to_incidents",
    "ET", "_item_geometry", "_emit_item", "_channel_metadata", "_make_rss", "atomic_write", "save_state_or_warn",
]


def refresh_from_env() -> None:
    """Refresh configuration values and reload provider plugins."""
    feed_config.refresh_from_env()
    load_provider_plugins(force=True)


def _invoke_collect_items(report: RunReport) -> list[FeedItem]:
    return _collect_items(report=report)


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
        # The same chain as ``main`` (``_assemble_feed_items``), so lint counts
        # like a build. No single-item trials and no rendering: a chain that
        # fails on an item ends lint with exit code 2. ``state`` is
        # only read back by ``_count_new_items`` and never saved here.
        stats = FeedAssemblyStats()
        deduped_items = _assemble_feed_items(items, state, now, stats=stats)
        duplicate_summaries = stats.duplicate_summaries
        new_items_count = _count_new_items(deduped_items, state)
        missing_guid_items = [it for it in deduped_items if not it.get("guid")]

        metrics = health_metrics_from(raw_count, stats, new_items_count)
        exit_code = print_lint_report(metrics, duplicate_summaries, stale_cache_messages, missing_guid_items, report)

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
    filter_duration: float = 0.0
    dedupe_duration: float = 0.0


def _assemble_feed_items(
    items: list[FeedItem],
    state: dict[str, Any],
    now: datetime,
    *,
    stats: FeedAssemblyStats | None = None,
    decide: Callable[[list[FeedItem], dict[str, Any], datetime], None] = ki_entscheidung.apply_stored,
) -> list[FeedItem]:
    """Turn the collected provider items into the ordered feed list.

    This is the whole feed chain between fetching and writing: test notices
    out, age filter, dedupe, WL merges, Baustellen twins, identity carry-over
    and the ordering rules. ``main`` calls it, and so must every replay or
    probe that rebuilds a past feed state from cached data, so a probe can
    never compute a different feed than the real build (older probes that
    copied the steps by hand missed the twin step). Fetching, RSS rendering,
    file writes and ``_save_state`` stay with ``main`` (since 2026-11-14 it
    calls ``src/feed_build/outputs.py`` for the writes), and so does asking
    Gemini: ``decide`` only reads stored answers unless ``main`` passes its own.

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
    items, _ = _drop_old_items(items, now, state)
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
    # An announcement that begins after the preview day stays its own item
    # until then — see ``_may_merge``; the same day as ``_defer_upcoming_items``.
    preview_last_day = _preview_last_day(
        _to_utc(now).astimezone(_VIENNA_TZ).date(), feed_config.UPCOMING_PREVIEW_DAYS
    )
    fuzzy_deduped = cast(
        list[FeedItem],
        deduplicate_fuzzy(
            cast(list[dict[str, Any]], deduped),
            announced=lambda it: _starts_after(cast(FeedItem, it), preview_last_day),
        ),
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
    # Sudden or planned, where the rules cannot tell (``src.ki_entscheidung``).
    decide(items, state, now)
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
    # place (tier 0 of the sort key), then a closure on its closure day
    # (tier 1), of several the earliest window.
    items = _defer_repeated_route_titles(
        items, is_current=lambda it: sort_keys[id(it)][0] == 0, leads=lambda it: sort_keys[id(it)][0] == 1,
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

        ki = ki_entscheidung.Decider(now)

        def _chain(its: list[FeedItem], st: dict[str, Any], counted: bool = True) -> list[FeedItem]:
            nonlocal new_items_count
            ordered = _assemble_feed_items(
                its, st, now, stats=stats if counted else FeedAssemblyStats(), decide=ki.decide if counted else ki.apply
            )
            # Before the rendering: it enters new items into the state.
            fresh = _count_new_items(ordered, st)
            new_items_count = fresh if counted else new_items_count
            return ordered

        def _fresh_stats() -> None:
            nonlocal stats, new_items_count
            stats, new_items_count = FeedAssemblyStats(), 0

        # German feed (primary) is built first so the public ``feed.xml``
        # is always refreshed regardless of any translation-pipeline
        # issues encountered for the EN variant. An item on which the chain
        # or the rendering fails leaves this run (``build_isolating``).
        items, rss_de, rss_duration = build_isolating(
            items, state, assemble=_chain, render=lambda its, st: _make_rss(its, now, st, lang="de"),
            reload_state=_load_state, reset=_fresh_stats, report=report,
            trial_assemble=lambda its, st: _chain(its, st, counted=False),
        )
        health_metrics = health_metrics_from(raw_count, stats, new_items_count)

        out_path = write_feed(rss_de)
        write_en_mirror(out_path, items, now, state)

        _record_translation_status(now)
        _remember_item_identity(items, state, now)
        ki.finish()

        save_state_or_warn(state, report)

        record_successful_build(
            report, out_path, items, raw_count=raw_count, stats=stats, collect_duration=collect_duration,
            rss_duration=rss_duration, total_duration=perf_counter() - job_start,
        )
        write_health_outputs(report, health_metrics, health_path, health_json_path)
        report.log_results()
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        # Security (Clear-Text-Logging Drift): outer-most pipeline catch.
        log.exception("Feed-Bau fehlgeschlagen: %s", sanitize_log_arg(str(exc)))
        report.record_exception(exc)
        if health_metrics is None:
            # Simplified fallback using pre-initialized counters (Task 3A);
            # ``stats`` holds what the feed chain counted before it failed.
            health_metrics = health_metrics_from(raw_count, stats, new_items_count)
        report.finish(build_successful=False)
        write_health_outputs(report, health_metrics, health_path, health_json_path)
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
