#!/usr/bin/env python3
"""KI-Entscheidung, Probelauf: put the measured unclear disruptions to Gemini and show its answers.

Owner's decision (Michael, 2026-11-12, card "Soll ich die
Gemini-Entscheidung für unklare Fälle so bauen?": "Ja, so bauen"): where
the rules leave open whether a disruption came suddenly or is a planned
measure, Gemini's answer decides (``src/ki_entscheidung``). In the feed
build that happens about three times a week, so a change to the prompt,
the classes or the connector would show its effect only days later. This
script is the dry run: it reads the unclear messages measured in the
replay of 20.09. to 12.11.2026 (``data/samples/ki_entscheidung_faelle.json``,
each with the other messages of its lines at that moment), asks exactly
like the build (the same prompt, the same chain, four messages a
request) and prints what the rules show today, what a reader of the
source expects and what Gemini answers.

    python scripts/ki_entscheidung_probe.py [--passes 2 --pause 30]

A request no model answered (Gemini busy) is put again in the next pass,
as the next build would put it.

It stores nothing: no answer reaches ``data/ki_entscheidungen.json`` and no
feed changes. The report goes to stdout and, in a workflow, to the step
summary. The key comes from ``GEMINI_API_KEY``; without it the script says
so and asks nothing.

Exit code 0 unless the arguments are wrong: a busy Gemini or an answer
that differs from the expectation is a result to read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from logging import getLogger
from pathlib import Path
from typing import Any, cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.logging_safe import setup_script_logging
from src.feed_build.base import _VIENNA_TZ, _to_utc
from src.feed_types import FeedItem
from src.ki_entscheidung import ask as asking
from src.ki_entscheidung.candidates import Candidate, candidates_of, describe, rule_begin
from src.ki_entscheidung.run import MAX_PER_REQUEST
from src.ki_entscheidung.store import KINDS
from src.ki_waechter.inputs import free_text
from src.markdown_stats.review_client import GEMINI_KEY_ENV, ChainAnswer
from src.utils.files import read_capped_json
from src.utils.text import escape_markdown

LOGGER = getLogger("ki_entscheidung_probe")

DEFAULT_CASES = Path("data/samples/ki_entscheidung_faelle.json")
MAX_CASES_BYTES = 2 * 1024 * 1024
# The line the replay of old against new reads the answers from.
ANSWERS_MARK = "ANTWORTEN-JSON: "
_TIME_FIELDS = ("pubDate", "starts_at", "ends_at", "_incident_start")

Ask = Callable[[Sequence[str]], ChainAnswer[dict[int, tuple[str, str]]]]


def _item(raw: object) -> FeedItem | None:
    """A message of the cases file as the feed chain holds it (times as ``datetime``)."""
    if not isinstance(raw, dict):
        return None
    item: dict[str, Any] = dict(raw)
    for name in _TIME_FIELDS:
        value = item.get(name)
        if isinstance(value, str):
            try:
                item[name] = datetime.fromisoformat(value)
            except ValueError:
                return None
    return cast(FeedItem, item)


def load_cases(path: Path) -> list[tuple[dict[str, Any], Candidate, list[FeedItem]]]:
    """The cases of *path* that the rules still leave unclear: (case, candidate, all messages of that moment)."""
    data = read_capped_json(path, MAX_CASES_BYTES, label="ki_entscheidung_faelle", logger=LOGGER)
    raw_cases = data.get("faelle") if isinstance(data, dict) else None
    cases: list[tuple[dict[str, Any], Candidate, list[FeedItem]]] = []
    for raw in raw_cases if isinstance(raw_cases, list) else []:
        if not isinstance(raw, dict):
            continue
        item = _item(raw.get("meldung"))
        try:
            now = _to_utc(datetime.fromisoformat(str(raw.get("jetzt"))))
        except ValueError:
            continue
        if item is None:
            continue
        neighbours = [other for other in map(_item, raw.get("nachbarn") or []) if other is not None]
        items = [item, *neighbours]
        found = [candidate for candidate in candidates_of(items, {}, now) if candidate.item is item]
        if found:
            cases.append((raw, found[0], items))
    return cases


def _ask_from_env() -> Ask | None:
    """The real request with the environment's key; ``None`` without a key."""
    key = asking.api_key()
    if not key:
        return None
    return lambda descriptions: asking.ask(descriptions, key)


def _rule_line(candidate: Candidate) -> str:
    """What the rules alone show for *candidate*: its begin, or no begin."""
    begin = rule_begin(candidate)
    return f"[Seit {begin.astimezone(_VIENNA_TZ):%H:%M}]" if begin is not None else "ohne Beginn"


def run_probe(
    path: Path = DEFAULT_CASES,
    *,
    ask: Ask | None = None,
    clock: Callable[[], float] = time.monotonic,
    passes: int = 1,
    pause_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """The probe's report as Markdown; *ask* defaults to the real request.

    A request that no model answered is put again in the next of *passes*
    passes, *pause_s* seconds later, as the next build would put it.
    """
    cases = load_cases(path)
    lines = [
        "## KI-Entscheidung – Probelauf",
        "",
        "Dieser Lauf speichert nichts und ändert keinen Feed.",
        "",
        f"- Unklare Meldungen in der Fallsammlung: {len(cases)}",
    ]
    if not cases:
        return "\n".join([*lines, "- Ergebnis: nicht gefragt (keine Meldung der Sammlung ist nach den Regeln unklar)", ""])
    asker = ask if ask is not None else _ask_from_env()
    if asker is None:
        return "\n".join([*lines, f"- Ergebnis: nicht gefragt ({GEMINI_KEY_ENV} ist leer)", ""])
    answers: dict[int, tuple[str, str, str]] = {}
    waiting = list(range(0, len(cases), MAX_PER_REQUEST))
    for turn in range(1, max(1, passes) + 1):
        if turn > 1 and waiting:
            sleep(pause_s)
        unanswered: list[int] = []
        for first in waiting:
            batch = cases[first:first + MAX_PER_REQUEST]
            started = clock()
            try:
                answer = asker([describe(number, candidate, items) for number, (_, candidate, items) in enumerate(batch, 1)])
            except Exception as exc:  # a probe reports, it does not fail
                LOGGER.warning("Probelauf: Anfrage gescheitert (%s).", type(exc).__name__)
                answer = ChainAnswer(None, "", 0, False)
            model = escape_markdown(free_text(answer.model)[:60]) or "keine Antwort"
            lines.append(
                f"- Anfrage für Meldung {first + 1} bis {first + len(batch)}{f' ({turn}. Durchgang)' if turn > 1 else ''}: "
                f"{model}, {clock() - started:.1f} s, {answer.requests} Anfrage(n) entlang der Kette"
            )
            usable = {
                number: (kind, reason)
                for number, (kind, reason) in (answer.value or {}).items()
                if 1 <= number <= len(batch) and kind in KINDS
            }
            for number, (kind, reason) in usable.items():
                answers[first + number] = (kind, reason, answer.model)
            if not usable:
                unanswered.append(first)
        waiting = unanswered
    agree = sum(
        1 for number, (case, _, _) in enumerate(cases, 1)
        if case.get("erwartet") and number in answers and answers[number][0] == case["erwartet"]
    )
    expected = sum(1 for case, _, _ in cases if case.get("erwartet"))
    lines += [
        f"- Antworten: {len(answers)} von {len(cases)}",
        f"- Wie erwartet: {agree} von {expected} Meldungen mit einer Erwartung",
        "",
        "| Nr. | Meldung | Klasse | Regeln allein | Erwartet | Gemini | Grund |",
        "|---|---|---|---|---|---|---|",
    ]
    record = []
    for number, (case, candidate, _) in enumerate(cases, 1):
        kind, reason, model = answers.get(number, ("keine Antwort", "", ""))
        title = str(candidate.item.get("title") or "")
        lines.append(
            f"| {number} | {escape_markdown(title)} | {candidate.kind} | {_rule_line(candidate)} | "
            f"{escape_markdown(str(case.get('erwartet') or '–'))} | {kind} | {escape_markdown(reason)} |"
        )
        if number in answers:
            record.append({"abdruck": candidate.print, "titel": title, "art": kind, "grund": reason, "modell": model})
    lines += ["", ANSWERS_MARK + json.dumps(record, ensure_ascii=True, allow_nan=False), ""]
    return "\n".join(lines)


def _write_summary(report: str) -> None:
    """Append *report* to the step summary of a workflow run; nothing outside one."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    except OSError as exc:
        # The summary is a convenience; stdout already carries the report.
        LOGGER.debug("Probelauf: Step-Summary nicht schreibbar (%s).", type(exc).__name__)


def main(argv: Sequence[str] | None = None, *, ask: Ask | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES,
        help="Fallsammlung (Standard: data/samples/ki_entscheidung_faelle.json)",
    )
    parser.add_argument("--passes", type=int, default=1, help="Durchgänge: ein unbeantworteter Block wird erneut gefragt (Standard: 1)")
    parser.add_argument("--pause", type=float, default=0.0, help="Sekunden zwischen zwei Durchgängen (Standard: 0)")
    args = parser.parse_args(argv)
    if not args.cases.is_file():
        parser.error(f"Fallsammlung nicht gefunden: {args.cases}")
    setup_script_logging()
    report = run_probe(args.cases, ask=ask, passes=args.passes, pause_s=args.pause)
    print(report)
    _write_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
