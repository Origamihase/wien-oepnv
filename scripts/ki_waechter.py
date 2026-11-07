#!/usr/bin/env python3
"""KI-Wächter: a language model reads the feed next to its source texts and reports.

The daily display audit inside the project, the successor of the Jules audits
(Jules accepts no new sessions after 2026-11-11). Operator decision
2026-10-31 (Michael, on the question whether a small AI should rewrite the
source texts): the rules stay the feed chain; an AI comes in only as a
watcher that compares source text and feed text and reports, never rewrites.
"Einen Jules-Ersatz (Wächter-KI) im Projekt selbst. Achte bitte weiterhin auf
höchste Qualität und Effizienz." The same day: every item text of the last
24 hours, not only the current ten ("Ein Review der Meldungen der letzten 24h
wäre besser"), and Gemini through the free tier of the Gemini API with the
secret ``GEMINI_API_KEY`` (decision card; his Claude subscription runs only
when needed).

Three steps, only the middle one is the model:

``prepare``
    Reads every version of ``docs/feed.xml`` and ``docs/feed.en.xml`` that
    was committed in the 24 hours before ``--rev`` (each Update cycle commits
    one) and the ``cache/*/events.json`` of the same commits. Each distinct
    item text becomes one numbered item with the time it stood in the feed,
    the items it stood next to, and the source events of its lines. Header
    numbers, GUIDs and the item table come from here, never from the model
    (the Jules audits invented or shifted GUIDs and counts).
``ask``
    Two calls of the Gemini API with ``scripts/ki_waechter_prompt.md`` and
    the input, no tools, the answer in a fixed JSON schema with findings of
    the closed classes F1 to F6. Since 2026-11-07 each call reads one half of
    the day (``halves`` in ``src/ki_waechter/inputs.py``, ``ask_parts`` in
    ``src/ki_waechter/answers.py``) and the findings of both answers are
    united; from 2026-11-05 to 06.11. both calls read the whole input
    (``ask_twice``, Michael's pick "Beides"), which an input of one version
    still gets.
``verify``
    Checks every finding mechanically before it counts: the quoted feed text
    must stand verbatim in that item, a source quote verbatim in the item's
    source events, a duplicate must have stood in the feed at the same time,
    the class must fit the feed. A finding that fails stays visible under
    "Nicht belegt" but is no finding. Michael on 2026-10-30: "Falsche Funde
    stören mich am meisten." Writes the audit file
    ``docs/archive/audits/ki-waechter-YYYY-MM-DD.md`` and a small result JSON
    for the workflow (issue, side-step record).

``dispatch``
    The last step of a successful Update cycle on ``main``: starts the
    daily audit (``repository_dispatch``) when it is due, see
    ``src/ki_waechter/dispatch.py``. Until 2026-11-05 the watcher's own gate
    job started on every tick to find out.

Never changes the feed. See ``docs/reference/ki_waechter.md``.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ki_waechter import dispatch as ki_dispatch
from src.ki_waechter.answers import Answer, ask_parts, describe_parts, unite_answers
from src.ki_waechter.audit import Verdict, extract_answer, render_report, verify
from src.ki_waechter.inputs import (
    CLASSES,
    GIT,
    VIENNA,
    FeedItem,
    Part,
    Prepared,
    Snapshot,
    Version,
    free_text,
    git_snapshots,
    halves,
    prepare,
    render_input,
    together,
    visible,
    working_tree_reader,
)
from src.utils.files import loads_finite, read_capped_json, read_capped_text
from src.utils.http import request_safe, session_with_retries
# GitHub Pages renders the report under docs/ with Liquid (src/utils/liquid.py).
from src.utils.liquid import liquid_safe
from src.utils.serialize import scrub_trojan_source_primitives


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = REPO_ROOT / "scripts" / "ki_waechter_prompt.md"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_KEY_ENV = "GEMINI_API_KEY"
# One answer of a thinking model can take minutes.
GEMINI_TIMEOUT = (10.0, 600.0)
# Google counts, the audit does not (Michael 2026-11-05: "Zähle es bitte so
# wie Google. Wir brauchen keine eigene Zähltechnik."). The Gemini API
# enforces its free-tier limits per project and model (requests a minute,
# input tokens a minute, requests a day from 0:00 Pacific time; in this
# project on 2026-11-05: Flash 20 a day and 5 a minute, Lite 500 a day and
# 15 a minute, 250,000 input tokens a minute each) and answers 429
# RESOURCE_EXHAUSTED when one is reached; the error names the quota and its
# value (google.rpc.QuotaFailure) and a wait (google.rpc.RetryInfo). No call
# of the API reports the usage or the limits (aistudio.google.com/docs/
# rate-limits, 2026-11-05: they "can be viewed in Google AI Studio"). So a
# model that answers 429 is passed over for the rest of the run, Google's
# figures stand in the log, and the chain goes on. Until 2026-11-05 the gate
# of ``ki-waechter.yml`` counted the requests of the day from an annotation
# of every audit against a budget of ten. Michael 2026-10-31: "sorge auch
# hier dafür, dass das API-Limit nicht überschritten wird": Google refuses a
# request at the limit, and a key without billing cannot cost anything.
# An overloaded model (500, 502, 503, 504; on 2026-10-31 all ten requests to
# Gemini 3.8 Flash got "503 ... high demand" within an hour) or a dropped
# connection hands over to the next model of the chain at once; the chain is
# asked at most MAX_ROUNDS times, with OVERLOAD_WAIT_S in between.
# The input is capped below the token-per-minute limit; a larger input is not
# sent (red run) instead of being cut silently. German feed text runs at
# about 3.5 characters per token; 24 hours of 2026-10-31 were 94,000
# characters (about 30,000 tokens).
MAX_PROMPT_CHARS = 450_000
# Passes over the chain in one audit: every model once per pass, an
# overloaded one again in the next pass. Two passes over the chain of four
# are eight requests at most; from 01.11. to 05.11.2026 an audit sent three
# (3.8 Flash, 3.6 Flash, 3.8 Flash again after a minute).
MAX_ROUNDS = 2
OVERLOAD_WAIT_S = 60
# A Lite model is one whose name carries ``-lite`` (``gemini-3.5-flash-lite``).
LITE_MARK = "-lite"
# The Lite models think "minimal" by default, the Flash models "medium"
# (https://aistudio.google.com/docs/thinking, 2026-11-05); Gemini 3.1
# Flash-Lite without thinking read the feed of 27.10.2026 in three seconds
# (trial of 2026-11-05) and found none of its four known errors. The audit asks the Lite models for the top level and
# leaves the Flash models at their default.
LITE_THINKING_LEVEL = "high"
# Two answers per audit (Michael 2026-11-05, card "Wie soll der KI-Wächter
# weniger falsche Funde liefern?", pick "Beides"): one request does not give
# every finding (27.10.2026: three Lite runs with two to three findings each,
# six distinct and all proven in all). Until 2026-11-07 both requests read the
# whole input and the second went to the model that answered, once
# (``ask_twice``, kept for an input of one version); since then each request
# reads one half of the day (``halves``, ``ask_parts`` in
# src/ki_waechter/answers.py): measured on the input of 06.11.2026 (102
# texts, 3.6 Flash), halves and whole input find the same errors, the halves
# with about 38 % fewer prompt tokens (Michael 07.11.2026). No request starts
# after NO_NEW_REQUEST_AFTER_S, so the step limit of 25 minutes holds.
ANSWERS_PER_AUDIT = 2
# No new request (wait included) after this many seconds: the last one (read
# timeout 600 s) still ends inside the step limit of 25 minutes in
# ``ki-waechter.yml``.
NO_NEW_REQUEST_AFTER_S = 720
OVERLOAD_STATUS = frozenset({500, 502, 503, 504})
QUOTA_STATUS = 429
MAX_ANSWER_BYTES = 2_000_000
# The answer's shape, enforced by the API (structured output). The script
# still checks every field: the schema shapes the answer, it proves nothing.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "geprueft": {"type": "array", "items": {"type": "integer"}},
        "funde": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nr": {"type": "integer"},
                    "klasse": {"type": "string", "enum": ["F1", "F2", "F3", "F4", "F5", "F6"]},
                    "feed": {"type": "string", "enum": ["DE", "EN"]},
                    "feed_zitat": {"type": "string"},
                    "quelle_zitat": {"type": "string"},
                    "nr2": {"type": "integer"},
                    "feed_zitat2": {"type": "string"},
                    "erwartet": {"type": "string"},
                    "begruendung": {"type": "string"},
                },
                "required": ["nr", "klasse", "feed", "feed_zitat", "erwartet", "begruendung"],
            },
        },
        "vermutungen": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["geprueft", "funde", "vermutungen"],
}


# --- asking the model ---------------------------------------------------------------


def build_prompt(input_text: str, prompt_template: str) -> str:
    """The rules with the input in place of ``{EINGABE}``."""
    if "{EINGABE}" not in prompt_template:
        raise ValueError("Prompt ohne {EINGABE}")
    return prompt_template.replace("{EINGABE}", input_text)


def is_lite(model: str) -> bool:
    """Whether *model* belongs to the Lite family (asked to think, :data:`LITE_THINKING_LEVEL`)."""
    return LITE_MARK in model


def gemini_request(prompt: str, model: str = "") -> dict[str, Any]:
    """One generateContent call: the prompt, JSON out in the answer schema, no tools.

    A Lite model is asked for :data:`LITE_THINKING_LEVEL`; a Flash model keeps
    its default (the API refuses a thinking config a model does not know).
    """
    generation: dict[str, Any] = {
        "responseMimeType": "application/json",
        "responseJsonSchema": ANSWER_SCHEMA,
    }
    if is_lite(model):
        generation["thinkingConfig"] = {"thinkingLevel": LITE_THINKING_LEVEL}
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation,
        # Operator texts name accidents, police and rescue operations; a
        # safety block would end the audit without an answer.
        "safetySettings": [
            {"category": c, "threshold": "BLOCK_NONE"}
            for c in (
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ],
    }


def gemini_answer(payload: Mapping[str, Any], model: str) -> Answer:
    """The text of a generateContent response, or why there is none."""
    candidates = payload.get("candidates") or []
    usage = payload.get("usageMetadata") or {}
    tokens = {
        k: int(usage[k]) for k in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount")
        if isinstance(usage.get(k), int)
    }
    if not candidates or not isinstance(candidates[0], dict):
        block = (payload.get("promptFeedback") or {}).get("blockReason") or "keine Antwort"
        return Answer(False, model, error=f"Gemini lieferte keine Antwort ({block})", tokens=tokens)
    first = candidates[0]
    reason = str(first.get("finishReason") or "")
    parts = (first.get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if reason != "STOP" or not text.strip():
        return Answer(False, model, error=f"Gemini brach ab ({reason or 'ohne Grund'})", tokens=tokens)
    return Answer(True, model, text=text, tokens=tokens)


def ask_gemini(
    prompt: str,
    model: str,
    key: str,
    sleep: Callable[[float], None] = time.sleep,
    max_rounds: int = MAX_ROUNDS,
    deadline_s: float = NO_NEW_REQUEST_AFTER_S,
) -> Answer:
    """Send the prompt along the chain until a model answers.

    ``model`` may name a chain, comma-separated
    (``gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite``).
    An overloaded model (5xx, a dropped connection) hands over to the next one
    at once and is asked again in the next pass over the chain, after
    :data:`OVERLOAD_WAIT_S`; ``max_rounds`` passes at most. A model whose
    quota Google reports as reached (429) is passed over for the rest of the
    run: Google counts, the audit does not. Any other refusal (a rejected
    key, an unknown model, a timeout) ends the run. No request after the
    first starts later than *deadline_s* seconds after the call began (the
    second half of the input gets what the first left of
    :data:`NO_NEW_REQUEST_AFTER_S`). The key travels in a header, never in
    the URL or a log line.
    """
    models = [m.strip() for m in model.split(",") if m.strip()]
    if not models or not all(re.fullmatch(r"[a-z0-9.-]{3,60}", m) for m in models):
        raise ValueError("ungültiger Modellname")
    if len(prompt) > MAX_PROMPT_CHARS:
        return Answer(False, models[0], error=(
            f"Eingabe zu groß ({len(prompt)} Zeichen, Grenze {MAX_PROMPT_CHARS}); nicht gesendet"
        ))
    bodies = {
        lite: json.dumps(gemini_request(prompt, LITE_MARK if lite else ""), ensure_ascii=True, allow_nan=False)
        for lite in (False, True)
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    started = time.monotonic()
    sent: dict[str, int] = {}
    # Models whose quota Google reported as reached (429): not asked again in this run.
    spent: set[str] = set()
    attempts: list[str] = []
    busy = False
    answer = Answer(False, models[0], error="keine Anfrage gesendet")
    # total=0: the session itself never repeats a request; this loop sends every one.
    # keepalive: the answer can take minutes without a byte on the wire.
    with session_with_retries(
        "wien-oepnv-ki-waechter", timeout=GEMINI_TIMEOUT, keepalive=True, total=0,
    ) as session:
        waited_round = 0
        for round_no, current in ((r, m) for r in range(max(0, max_rounds)) for m in models):
            if current in spent:
                continue
            total = sum(sent.values())
            if total:
                # A new pass over the chain begins with a wait; inside a pass
                # the next model is asked at once.
                wait = OVERLOAD_WAIT_S if round_no > waited_round else 0
                if time.monotonic() - started + wait > deadline_s:
                    print(f"Gemini: nach {time.monotonic() - started:.0f} s keine weitere Anfrage")
                    break
                print(f"Gemini ohne Antwort (überlastet, Kontingent erreicht oder Verbindung abgebrochen), "
                      f"{total + 1}. Anfrage, an {current}"
                      + (f" in {wait} s" if wait else ""))
                if wait:
                    sleep(wait)
                    waited_round = round_no
            sent[current] = sent.get(current, 0) + 1
            sent_at = time.monotonic()
            try:
                response = request_safe(
                    session, GEMINI_URL.format(model=current), method="POST", data=bodies[is_lite(current)],
                    headers=headers, timeout=GEMINI_TIMEOUT, max_bytes=MAX_ANSWER_BYTES,
                    allowed_content_types=("application/json",), raise_for_status=False,
                )
            except requests.ConnectionError as exc:
                # A dropped connection (2026-11-02: after 261 s, the model still
                # thinking) is handled like an overloaded model: next model; the
                # request may have reached Google.
                answer = Answer(False, current, error=f"Gemini nicht erreichbar ({type(exc).__name__})")
                attempts.append(_attempt(current, sent_at, answer.error))
                busy = True
                continue
            except (requests.RequestException, ValueError) as exc:
                answer = Answer(False, current, error=f"Gemini nicht erreichbar ({type(exc).__name__})")
                attempts.append(_attempt(current, sent_at, answer.error))
                # Ten minutes without an answer is Google being slow, too.
                busy = isinstance(exc, requests.Timeout)
                break
            answer = _answer_from(response, current)
            status = response.status_code
            busy = status in OVERLOAD_STATUS or status == QUOTA_STATUS
            if not answer.ok:
                attempts.append(_attempt(current, sent_at, answer.error))
            if status == QUOTA_STATUS:
                spent.add(current)
            elif status not in OVERLOAD_STATUS:
                break
    answer.attempts = attempts
    answer.overloaded = busy and not answer.ok
    answer.per_model = sent
    answer.requests = sum(sent.values())
    answer.seconds = round(time.monotonic() - started, 1)
    return answer


def ask_twice(
    prompt: str,
    model: str,
    key: str,
    sleep: Callable[[float], None] = time.sleep,
    max_rounds: int = MAX_ROUNDS,
    answers: int = ANSWERS_PER_AUDIT,
) -> Answer:
    """The chain's answer, united with a second answer of the same model (see :data:`ANSWERS_PER_AUDIT`)."""
    first = ask_gemini(prompt, model, key, sleep, max_rounds)
    if not first.ok:
        return first
    first.answers = 1
    if answers < 2:
        return first
    if first.seconds > NO_NEW_REQUEST_AFTER_S:
        print(f"Gemini: erste Antwort erst nach {first.seconds:.0f} s, keine zweite Anfrage")
        return first
    print(f"Gemini: zweite Anfrage an {first.model}", flush=True)
    return unite_answers(first, ask_gemini(prompt, first.model, key, sleep, max_rounds=1))


def _attempt(model: str, sent_at: float, error: str) -> str:
    """One log line for a request without an answer (02.11.2026: which model, after how long)."""
    line = f"{model} nach {time.monotonic() - sent_at:.0f} s: {error[:120]}"
    print(f"Gemini-Versuch ohne Antwort: {line}", flush=True)
    return line


def _answer_from(response: requests.Response, model: str) -> Answer:
    try:
        payload = loads_finite(response.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        payload = {}
    if response.status_code != 200 or not isinstance(payload, dict):
        message = ""
        if isinstance(payload, dict):
            message = free_text((payload.get("error") or {}).get("message"))[:200]
        hint = f" ({quota_details(payload)})" if response.status_code == QUOTA_STATUS else ""
        return Answer(False, model, error=f"Gemini HTTP {response.status_code}{hint}: {message or 'ohne Text'}")
    return gemini_answer(payload, model)


def quota_details(payload: object) -> str:
    """Google's own figures from a 429: the quota reached, its value, the wait.

    The error details carry ``google.rpc.QuotaFailure`` (``violations`` with
    ``quotaId``, ``quotaMetric``, ``quotaValue``) and ``google.rpc.RetryInfo``
    (``retryDelay``); whatever is missing is left out.
    """
    parts: list[str] = []
    details = (payload.get("error") or {}).get("details") if isinstance(payload, dict) else None
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type") or "").rsplit(".", 1)[-1]
        if kind == "QuotaFailure":
            for violation in detail.get("violations") or []:
                if isinstance(violation, dict):
                    quota = free_text(violation.get("quotaId") or violation.get("quotaMetric") or "Kontingent")[:80]
                    parts.append(f"{quota} = {free_text(violation.get('quotaValue') or '?')[:20]}")
        elif kind == "RetryInfo" and detail.get("retryDelay"):
            parts.append(f"Wartezeit {free_text(detail.get('retryDelay'))[:20]}")
    return "Kontingent laut Google erreicht" + (": " + ", ".join(parts) if parts else "")



# --- command line ------------------------------------------------------------------


def _prep_from_json(path: Path) -> Prepared:
    data = read_capped_json(path, label="ki_waechter")
    if not isinstance(data, dict):
        raise ValueError(f"{path} fehlt oder ist unlesbar")
    data["items"] = [FeedItem(**item) for item in data["items"]]
    data["versions"] = [Version(**version) for version in data["versions"]]
    return Prepared(**data)


def _cmd_prepare(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.rev:
        snapshots = git_snapshots(root, args.rev, args.hours)
        commit = subprocess.run(  # nosec B603
            [GIT, "rev-parse", "--short", f"{args.rev}^{{commit}}"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()
    else:
        read, listing = working_tree_reader(root)
        snapshots = [Snapshot("Arbeitsstand", "", read, listing)]
        commit = subprocess.run(  # nosec B603
            [GIT, "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True
        ).stdout.strip() or "?"
    prep = prepare(snapshots, commit, args.hours if args.rev else 0.0)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eingabe.md").write_text(scrub_trojan_source_primitives(render_input(prep)), encoding="utf-8")
    _write_json(out / "eingabe.json", asdict(prep))
    # The halves as the model gets them (``ask`` renders them again from eingabe.json), for the reader of the run.
    for part in halves(prep):
        (out / f"eingabe-teil{part.nr}.md").write_text(
            scrub_trojan_source_primitives(render_input(prep, part)), encoding="utf-8"
        )
    print(f"KI-Wächter: {prep.count_items} Meldungstexte aus {len(prep.versions)} Fassungen vorbereitet (Commit {commit})")
    return 0


def _write_json(path: Path, data: object) -> None:
    clean = scrub_trojan_source_primitives(data)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")


def _cmd_ask(args: argparse.Namespace) -> int:
    work = Path(args.dir)
    key = os.environ.get(GEMINI_KEY_ENV, "").strip()
    if not key:
        answer = Answer(False, args.model, error=f"{GEMINI_KEY_ENV} ist leer")
    else:
        input_text = read_capped_text(work / "eingabe.md", label="ki_waechter")
        template = read_capped_text(Path(args.prompt), label="ki_waechter")
        if input_text is None or template is None:
            raise ValueError("Eingabe oder Prompt fehlt")
        parts = _halves_of(work)
        if parts:
            print(f"KI-Wächter: zwei Hälften, {' und '.join(str(len(part.items)) for part, _ in parts)} Texte")
            answer = ask_parts(
                [build_prompt(text, template) for _, text in parts], args.model, key, ask_gemini,
                max_rounds=MAX_ROUNDS, deadline_s=NO_NEW_REQUEST_AFTER_S,
            )
        else:
            answer = ask_twice(build_prompt(input_text, template), args.model, key)
    _write_json(work / "antwort.json", asdict(answer))
    # For the reader of the run: how many requests, to which models. Nothing
    # counts them (until 2026-11-05 the gate read the first annotation).
    print(f"::notice title=Gemini-Anfragen::{answer.requests}")
    if answer.per_model:
        print("::notice title=Gemini-Modelle::" + ", ".join(f"{m} {n}" for m, n in answer.per_model.items()))
    if not answer.ok:
        if answer.overloaded:
            # Exit 1 all the same: no answer, nothing to verify. The workflow
            # reads ``overloaded`` from antwort.json and records the day as
            # "overloaded" instead of "failure".
            print(f"::warning title=KI-Wächter::Gemini überlastet, keine Antwort: {answer.error}")
        else:
            print(f"::error title=KI-Wächter::{answer.error}")
        return 1
    print(f"Gemini {answer.model}: {answer.requests} Anfrage(n), {answer.answers} Antwort(en)"
          + (" je eine Hälfte" if answer.parts else "") + f", {answer.seconds} s, Tokens {answer.tokens}")
    return 0


def _halves_of(work: Path) -> list[tuple[Part, str]]:
    """The two halves of the prepared input with their rendered text; empty without ``eingabe.json`` or halves."""
    try:
        prep = _prep_from_json(work / "eingabe.json")
    except (ValueError, TypeError, KeyError):
        return []
    return [(part, render_input(prep, part)) for part in halves(prep)]


def _cmd_verify(args: argparse.Namespace) -> int:
    prep = _prep_from_json(Path(args.dir) / "eingabe.json")
    raw = read_capped_json(Path(args.dir) / "antwort.json", label="ki_waechter")
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        print("::error title=KI-Wächter::keine Antwort der KI")
        return 1
    answer = extract_answer(str(raw.get("text") or ""))
    verdict = verify(answer, prep)
    now = datetime.now(UTC)
    model = str(raw.get("model") or "?")
    answers = raw.get("answers") if isinstance(raw.get("answers"), int) else 0
    notes = raw.get("parts") if isinstance(raw.get("parts"), list) else []
    if notes:
        # The reader of the report sees which half each answer read, and a half without an answer.
        model += describe_parts([n for n in notes if isinstance(n, dict)], halves(prep))
    elif answers == 2:
        # The reader of the report sees why a run lists more than one request's findings.
        model += ", zwei Antworten vereinigt"
    report = render_report(prep, verdict, now, model, args.run_url)
    target = Path(args.report) if args.report else Path(args.dir) / "bericht.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    # The scrub drops zero-width and BiDi characters, which can join a "{" and
    # a "%" that render_report kept apart: liquid_safe comes last (review of
    # 2026-11-01, "{\u200b% x" in a model answer).
    target.write_text(liquid_safe(scrub_trojan_source_primitives(report)), encoding="utf-8")
    summary = {
        "funde": len(verdict.findings),
        "verworfen": len(verdict.rejected),
        "geprueft": len(verdict.checked),
        "meldungen": prep.count_items,
        "fassungen": len(prep.versions),
        "titel": [f"{f.klasse} Meldung {f.nr}: {CLASSES[f.klasse]}" for f in verdict.findings],
        "tokens": raw.get("tokens") or {},
        "antworten": answers,
    }
    _write_json(Path(args.dir) / "ergebnis.json", summary)
    print(report)
    if len(verdict.checked) < prep.count_items:
        print(f"::warning title=KI-Wächter::nur {len(verdict.checked)} von {prep.count_items} Meldungstexten geprüft")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="Eingabe für die KI schreiben")
    p.add_argument("--root", default=str(REPO_ROOT))
    p.add_argument("--rev", default="", help="Commit, dessen Feed geprüft wird (leer: Arbeitsstand)")
    p.add_argument("--hours", type=float, default=24.0, help="mit --rev: alle Fassungen dieser Stunden davor")
    p.add_argument("--out", required=True)
    a = sub.add_parser("ask", help=f"Eingabe an Gemini schicken (Schlüssel in {GEMINI_KEY_ENV})")
    a.add_argument("--dir", required=True)
    a.add_argument("--model", required=True)
    a.add_argument("--prompt", default=str(PROMPT_FILE))
    v = sub.add_parser("verify", help="Antwort der KI prüfen und Bericht schreiben")
    v.add_argument("--dir", required=True)
    v.add_argument("--report", default="")
    v.add_argument("--run-url", default="")
    d = sub.add_parser("dispatch", help="den täglichen Prüflauf starten, wenn er fällig ist (letzter Schritt des Update-Laufs)")
    d.add_argument("--record", default=str(ki_dispatch.RECORD), help="Aufzeichnung im Checkout")
    args = parser.parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "prepare": _cmd_prepare,
        "ask": _cmd_ask,
        "verify": _cmd_verify,
        "dispatch": lambda a: ki_dispatch.main(["--record", a.record]),
    }
    return commands[args.cmd](args)


# Public names of this script: its own entry points and the names other
# files and tests read through it. The code lives in src/ (700-line limit,
# 2026-11-02); listing the imports marks them as deliberate re-exports.
__all__ = [
    "Answer",
    "asdict",
    "ask_gemini",
    "ask_parts",
    "ask_twice",
    "build_prompt",
    "git_snapshots",
    "main",
    "prepare",
    "render_input",
    "render_report",
    "verify",
    "CLASSES",
    "VIENNA",
    "FeedItem",
    "Prepared",
    "Snapshot",
    "Verdict",
    "Version",
    "extract_answer",
    "halves",
    "together",
    "unite_answers",
    "visible",
    "working_tree_reader",
]


if __name__ == "__main__":
    sys.exit(main())
