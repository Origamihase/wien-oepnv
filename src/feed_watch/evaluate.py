"""Feed-Wächter: one run (all findings and the state for the next run).

Moved verbatim from ``scripts/feed_watch.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.feed_watch.feed_rules import (
    check_count,
    check_courtesy,
    check_duplicates,
    check_feed_age,
    check_length,
    check_oebb_second_sentence,
    check_old_as_new,
    check_past_end,
    check_since_too_early,
    check_time_line,
)
from src.feed_watch.model import (
    DEFAULT_MAX_ITEMS,
    HELD_RUNS_TO_CLOSE,
    MAX_SAMPLES,
    RULES,
    SAMPLE_WINDOW,
    Feed,
    Finding,
    SourceSample,
    _parse_iso,
)
from src.feed_watch.sources import PollSample, check_fresh_answers, check_sources, check_vao_version
from src.utils.vao_version import VersionState

# --- one run ---------------------------------------------------------------------


@dataclass
class Result:
    findings: list[Finding]
    state: dict[str, Any]


def evaluate(
    feed: Feed,
    *,
    now: datetime,
    state: Mapping[str, Any],
    oebb_cache: Mapping[str, str],
    sources: SourceSample | None,
    max_items: int = DEFAULT_MAX_ITEMS,
    fetch_exit: Mapping[str, int] | None = None,
    poll: PollSample | None = None,
    vao: VersionState | None = None,
) -> Result:
    """Every finding of this run and the state for the next one. Pure: no I/O."""
    built = feed.built or now
    items = feed.items
    findings: list[Finding] = []
    findings += check_duplicates(items)
    findings += check_count(items, max_items)
    findings += check_old_as_new(items)
    previous = state.get("previous_feed")
    flagged = state.get("seit_zu_frueh")
    since_findings, keep = check_since_too_early(
        items,
        built,
        previous if isinstance(previous, Mapping) else None,
        flagged if isinstance(flagged, list) else [],
    )
    findings += since_findings
    findings += check_oebb_second_sentence(items, oebb_cache)
    findings += check_time_line(items)
    findings += check_past_end(items, built)
    findings += check_length(items)
    findings += check_courtesy(items)
    findings += check_feed_age(feed.built, now)

    history = state.get("samples")
    samples: list[Mapping[str, Any]] = (
        [entry for entry in history if isinstance(entry, Mapping)] if isinstance(history, list) else []
    )
    if sources is not None:
        findings += check_sources(sources, samples, now)
        samples.append(
            {"t": now.isoformat(), "raw": sources.raw, "discarded": sources.discarded, "cache": sources.cache}
        )
    fresh_findings, fetch_state = check_fresh_answers(fetch_exit, poll, state.get("fetch"), now)
    findings += fresh_findings
    findings += check_vao_version(vao, now)
    kept = [
        dict(entry)
        for entry in samples
        if (moment := _parse_iso(entry.get("t"))) is not None and now - moment <= SAMPLE_WINDOW
    ][-MAX_SAMPLES:]

    rules_state = _next_rule_state(state.get("rules"), findings, now)
    new_state: dict[str, Any] = {
        "version": 1,
        "description": (
            "Feed-Wächter (scripts/feed_watch.py): the rules the last Update run broke, "
            "with the state the next run needs. A finding is a note; the feed is never "
            "changed. See docs/reference/feed_watch.md."
        ),
        "updated": now.isoformat(),
        "built": feed.built.isoformat() if feed.built else None,
        "findings": [
            {
                "rule": f.rule_id,
                "title": RULES[f.rule_id].title,
                "rank": RULES[f.rule_id].rank,
                "subject": f.subject,
                "text": f.text,
            }
            for f in findings
        ],
        "rules": rules_state,
        "previous_feed": {
            "built": built.isoformat(),
            "items": [{"title": item.title, "time_line": item.time_line or ""} for item in items],
        },
        "seit_zu_frueh": keep,
        "samples": kept,
        "fetch": fetch_state,
    }
    return Result(findings, new_state)


def _next_rule_state(previous: object, findings: Sequence[Finding], now: datetime) -> dict[str, Any]:
    """Per broken rule: since when, how many runs it has held since, and its issue.

    A rule leaves the state once it has held in :data:`HELD_RUNS_TO_CLOSE`
    runs in a row and has no open issue; with one, :func:`sync_issues`
    removes it when it closes the issue.
    """
    old = previous if isinstance(previous, Mapping) else {}
    broken = {f.rule_id for f in findings}
    out: dict[str, Any] = {}
    for rule_id in RULES:
        raw = old.get(rule_id)
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        if rule_id in broken:
            entry.setdefault("since", now.isoformat())
            entry["last"] = now.isoformat()
            entry["held_runs"] = 0
        elif entry:
            entry["held_runs"] = int(entry.get("held_runs") or 0) + 1
            if entry["held_runs"] >= HELD_RUNS_TO_CLOSE and not entry.get("issue"):
                continue
        if entry:
            out[rule_id] = entry
    return out
