#!/usr/bin/env python3
"""Feed-Wächter: check the built German feed against the rules learned since September 2026.

Runs at the end of every Update cycle, after the feed is built and before it
is published (``.github/workflows/update-cycle.yml``, step "Feed-Wächter").
It reads ``docs/feed.xml``, the provider caches and the raw answers in
``data/raw/`` and reports every rule the run breaks. It never changes the feed
and never stops the publish: a finding is a note, and an error of the
watcher itself is only logged (exit code 0 in every case).

Why: most defects of the feed so far were found by an audit, hours to weeks
after they first showed on the TV. On 2026-10-09 a stop ticker without a
cause took over the GUID and start of a delay on line 25 and stood as a new
disruption from 12:01 to 20:30, up to place 2; ÖBB's second sentence ("what
still applies") was missing in 59 of 106 texts since September; "2/71:
Polizeieinsatz [Seit 13:08]" stood for an incident from 20:08. The watcher
catches such cases on the day they happen, and new ones when Wiener Linien,
ÖBB or the City of Vienna change their data in a way no audit foresaw.

Never red (operator decision 2026-10-10, Michael, for the Health check:
"Der Check soll nur auf rot, wenn der Abruf nicht funktioniert. Wenn die
Technik funktioniert gehört er auch auf grün."). A rule the feed breaks is
content, not a broken fetch: the watcher reports it as a ``::warning``
annotation in the job log and the job summary, in ``data/feed_watch.json``
(read by the Health check as a note) and, with ``FEED_WATCH_ISSUES=1``, as
one GitHub issue per broken rule. The issue is opened once (or reopened
when it was closed less than a day ago) and closes itself once the rule has
held in two runs in a row.

Every rule held on every good day of the feed history from 2026-09-01 to
2026-10-10 (1,975 builds) and was loud on the days of its defect; see
``docs/reference/feed_watch.md`` for the rules, the replay and the reasons behind
each threshold.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from logging import DEBUG, INFO
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.config import InvalidPathError, validate_path
from src.feed.logging_safe import setup_script_logging
from src.utils.files import read_capped_text
from src.utils.vao_version import load_state as load_vao_version

# The watcher's code lives in src/feed_watch/; every module-level name of the
# former single file stays reachable here (tests use ``scripts.feed_watch``).
from src.feed_watch.model import (
    LOGGER as LOGGER,
    VIENNA as VIENNA,
    MAX_FEED_BYTES as MAX_FEED_BYTES,
    MAX_STATE_BYTES as MAX_STATE_BYTES,
    MAX_SOURCE_BYTES as MAX_SOURCE_BYTES,
    DEFAULT_MAX_ITEMS as DEFAULT_MAX_ITEMS,
    BODY_CHAR_LIMIT as BODY_CHAR_LIMIT,
    SINCE_LATE as SINCE_LATE,
    EXPECTED_END_BEFORE_MESSAGE as EXPECTED_END_BEFORE_MESSAGE,
    PREVIOUS_MAX_GAP as PREVIOUS_MAX_GAP,
    OLD_AS_NEW as OLD_AS_NEW,
    FEED_MAX_AGE as FEED_MAX_AGE,
    HELD_RUNS_TO_CLOSE as HELD_RUNS_TO_CLOSE,
    REOPEN_WINDOW as REOPEN_WINDOW,
    SAMPLE_WINDOW as SAMPLE_WINDOW,
    MAX_SAMPLES as MAX_SAMPLES,
    MIN_REFERENCE_SAMPLES as MIN_REFERENCE_SAMPLES,
    SOURCE_DROP_FACTOR as SOURCE_DROP_FACTOR,
    SOURCE_DROP_MIN_REFERENCE as SOURCE_DROP_MIN_REFERENCE,
    DISCARD_SHARE_JUMP as DISCARD_SHARE_JUMP,
    EMPTY_CACHE_MIN_REFERENCE as EMPTY_CACHE_MIN_REFERENCE,
    SOURCE_STALE as SOURCE_STALE,
    VAO_VERSION_REPORT as VAO_VERSION_REPORT,
    FETCH_SOURCES as FETCH_SOURCES,
    SOURCE_LABELS as SOURCE_LABELS,
    _NBSP as _NBSP,
    _WEEKDAY as _WEEKDAY,
    _DAY as _DAY,
    _TIME_LINE_RE as _TIME_LINE_RE,
    _TIME_LINE_AT_END_RE as _TIME_LINE_AT_END_RE,
    _SINCE_CLOCK_RE as _SINCE_CLOCK_RE,
    _DATE_RE as _DATE_RE,
    _EXPECTED_UNTIL_RE as _EXPECTED_UNTIL_RE,
    _LINE_PREFIX_RE as _LINE_PREFIX_RE,
    _COURTESY as _COURTESY,
    _OEBB_COURTESY_RE as _OEBB_COURTESY_RE,
    _OEBB_DATE_PREFIX_RE as _OEBB_DATE_PREFIX_RE,
    _SENTENCE_SPLIT_RE as _SENTENCE_SPLIT_RE,
    _ABBREVIATION_END_RE as _ABBREVIATION_END_RE,
    _OEBB_PLACE_RE as _OEBB_PLACE_RE,
    _STATION_WORDS_RE as _STATION_WORDS_RE,
    Rule as Rule,
    RULES as RULES,
    Item as Item,
    Feed as Feed,
    Finding as Finding,
    SourceSample as SourceSample,
    _clean as _clean,
    _local as _local,
    _parse_rfc2822 as _parse_rfc2822,
    _parse_iso as _parse_iso,
    _local_name as _local_name,
    parse_feed as parse_feed,
    _normalize as _normalize,
    _line_keys as _line_keys,
    _vienna_clock as _vienna_clock,
    _since_clock as _since_clock,
    _is_current as _is_current,
)
from src.feed_watch.feed_rules import (
    check_duplicates as check_duplicates,
    check_count as check_count,
    check_old_as_new as check_old_as_new,
    check_since_too_early as check_since_too_early,
    _oebb_source_sentences as _oebb_source_sentences,
    _oebb_key as _oebb_key,
    _oebb_title_names as _oebb_title_names,
    check_oebb_second_sentence as check_oebb_second_sentence,
    check_time_line as check_time_line,
    _time_line_end as _time_line_end,
    check_past_end as check_past_end,
    check_length as check_length,
    check_courtesy as check_courtesy,
    check_feed_age as check_feed_age,
)
from src.feed_watch.sources import (
    _count_list as _count_list,
    read_sources as read_sources,
    _DISCARD_BASE as _DISCARD_BASE,
    _discard_share as _discard_share,
    check_sources as check_sources,
    parse_fetch_exit as parse_fetch_exit,
    _fmt_hours as _fmt_hours,
    PollSample as PollSample,
    check_fresh_answers as check_fresh_answers,
    check_vao_version as check_vao_version,
    load_poll_sample as load_poll_sample,
)
from src.feed_watch.evaluate import (
    Result as Result,
    evaluate as evaluate,
    _next_rule_state as _next_rule_state,
)
from src.feed_watch.issues import (
    ISSUE_LABEL as ISSUE_LABEL,
    ISSUE_TITLE_PREFIX as ISSUE_TITLE_PREFIX,
    IssueClient as IssueClient,
    issue_client_from_env as issue_client_from_env,
    _run_link as _run_link,
    _issue_body as _issue_body,
    sync_issues as sync_issues,
    _open_or_reopen as _open_or_reopen,
)
from src.feed_watch.state_io import (
    load_oebb_cache as load_oebb_cache,
    load_state as load_state,
    write_state as write_state,
    _report as _report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = REPO_ROOT / "docs" / "feed.xml"
DEFAULT_STATE = REPO_ROOT / "data" / "feed_watch.json"
DEFAULT_CACHE_DIR = REPO_ROOT / "cache"
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"


def run(args: argparse.Namespace, now: datetime) -> int:
    state_path = validate_path(args.state, "--state")
    content = read_capped_text(args.feed, MAX_FEED_BYTES, label="Feed", logger=LOGGER)
    if content is None:
        raise ValueError("feed missing, unreadable or too large")
    feed = parse_feed(content)
    try:
        from src.feed import config as feed_config

        max_items = int(feed_config.MAX_ITEMS) or DEFAULT_MAX_ITEMS
    except Exception:  # pragma: no cover - config import guard
        max_items = DEFAULT_MAX_ITEMS
    state = load_state(state_path)
    result = evaluate(
        feed,
        now=now,
        state=state,
        oebb_cache=load_oebb_cache(args.cache_dir),
        sources=read_sources(args.cache_dir, args.raw_dir),
        max_items=max_items,
        fetch_exit=parse_fetch_exit(os.getenv("FEED_WATCH_FETCH_EXIT")),
        poll=load_poll_sample(args.cache_dir / "stammstrecke" / "poll_status.json"),
        vao=load_vao_version(args.cache_dir / "stammstrecke" / "vao_version.json"),
    )
    _report(result.findings, now)
    # Written before the issue calls, so a step killed by its time limit still
    # leaves this run's state; an issue opened then is found again by title.
    write_state(state_path, result.state)
    client = None if args.no_issues else issue_client_from_env()
    if client is not None:
        sync_issues(client, result, now)
        write_state(state_path, result.state)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--no-issues", action="store_true", help="never touch GitHub issues")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_script_logging(DEBUG if args.verbose else INFO)
    now = datetime.now(UTC)
    try:
        return run(args, now)
    except (InvalidPathError, Exception) as exc:  # never block the publish
        LOGGER.error("Feed-Wächter konnte nicht prüfen: %s: %s", type(exc).__name__, _clean(exc))
        try:
            write_state(
                validate_path(args.state, "--state"),
                {**load_state(validate_path(args.state, "--state")), "updated": now.isoformat(),
                 "error": f"{type(exc).__name__}: {_clean(exc)}"[:300]},
            )
        except Exception as write_exc:  # pragma: no cover - last resort
            LOGGER.error("Feed-Wächter: Status nicht geschrieben: %s", _clean(write_exc))
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
