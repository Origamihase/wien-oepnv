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
    One call of the Gemini API with ``scripts/ki_waechter_prompt.md`` and
    the input, no tools, the answer in a fixed JSON schema with findings of
    the closed classes F1 to F6.
``verify``
    Checks every finding mechanically before it counts: the quoted feed text
    must stand verbatim in that item, a source quote verbatim in the item's
    source events, a duplicate must have stood in the feed at the same time,
    the class must fit the feed. A finding that fails stays visible under
    "Nicht belegt" but is no finding. Michael on 2026-10-30: "Falsche Funde
    stören mich am meisten." Writes the audit file
    ``docs/archive/audits/ki-waechter-YYYY-MM-DD.md`` and a small result JSON
    for the workflow (issue, side-step record).

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
from dataclasses import asdict, dataclass, field

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ki_waechter.audit import Verdict, extract_answer, render_report, verify
from src.ki_waechter.inputs import (
    CLASSES,
    GIT,
    VIENNA,
    FeedItem,
    Prepared,
    Snapshot,
    Version,
    free_text,
    git_snapshots,
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
# Free-tier limits (Michael 2026-10-31: "sorge auch hier dafür, dass das
# API-Limit nicht überschritten wird"). A quota answer (429) is never
# repeated. Only an overloaded model (500, 502, 503, 504; on 2026-10-31 all
# ten requests to Gemini 3.8 Flash got "503 ... high demand" within an hour)
# hands over to the fallback model at once and is asked again after a minute,
# and only as often as ``--max-requests`` allows: the gate of
# ``ki-waechter.yml`` hands each audit its share of the daily budget
# ``KI_WAECHTER_MAX_REQUESTS`` and counts the requests of the day from the
# annotation ``Gemini-Anfragen`` every audit leaves. The input is capped below
# the token-per-minute limit; a larger input is not sent (red run) instead of
# being cut silently.
# German feed text runs at about 3.5 characters per token; 24 hours of
# 2026-10-31 were 94,000 characters (about 30,000 tokens).
MAX_PROMPT_CHARS = 450_000
MAX_REQUESTS_PER_AUDIT = 3
OVERLOAD_WAIT_S = (60, 180)
# No new request (wait included) after this many seconds: the last one (read
# timeout 600 s) still ends inside the step limit of 25 minutes in
# ``ki-waechter.yml``, so the annotation ``Gemini-Anfragen`` is always written.
NO_NEW_REQUEST_AFTER_S = 720
OVERLOAD_STATUS = frozenset({500, 502, 503, 504})
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


@dataclass
class Answer:
    ok: bool
    model: str
    text: str = ""
    error: str = ""
    tokens: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    requests: int = 0
    # One line per request that got no answer: model, seconds, what happened.
    attempts: list[str] = field(default_factory=list)
    # No answer only because Google was busy: the last request ended in an
    # overload (500/502/503/504), a dropped connection or the quota (429).
    # The Health check shows such a day as a note and turns red only after
    # 30 hours without a clean audit (Michael 2026-11-04, card "Ja, so
    # bauen"); a rejected key, a missing model or a broken answer stay red at
    # once.
    overloaded: bool = False


def build_prompt(input_text: str, prompt_template: str) -> str:
    """The rules with the input in place of ``{EINGABE}``."""
    if "{EINGABE}" not in prompt_template:
        raise ValueError("Prompt ohne {EINGABE}")
    return prompt_template.replace("{EINGABE}", input_text)


def gemini_request(prompt: str) -> dict[str, Any]:
    """One generateContent call: the prompt, JSON out in the answer schema, no tools."""
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": ANSWER_SCHEMA,
        },
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
    max_requests: int = 1,
    sleep: Callable[[float], None] = time.sleep,
) -> Answer:
    """Send the prompt; again only while the model is overloaded and ``max_requests`` allows.

    ``model`` may name fallbacks, comma-separated (``gemini-3.8-flash,gemini-3.6-flash``):
    an overloaded model hands over to the next one at once (each model has its
    own free-tier quota), a model already asked gets the next request only
    after a wait. The key travels in a header, never in the URL or a log line.
    """
    models = [m.strip() for m in model.split(",") if m.strip()]
    if not models or not all(re.fullmatch(r"[a-z0-9.-]{3,60}", m) for m in models):
        raise ValueError("ungültiger Modellname")
    if len(prompt) > MAX_PROMPT_CHARS:
        return Answer(False, models[0], error=(
            f"Eingabe zu groß ({len(prompt)} Zeichen, Grenze {MAX_PROMPT_CHARS}); nicht gesendet"
        ))
    body = json.dumps(gemini_request(prompt), ensure_ascii=True, allow_nan=False)
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    started = time.monotonic()
    limit = max(0, min(max_requests, MAX_REQUESTS_PER_AUDIT))
    sent = 0
    attempts: list[str] = []
    busy = False
    answer = Answer(False, models[0], error="kein Anfragebudget")
    # total=0: the session itself never repeats a request; this loop counts every one.
    # keepalive: the answer can take minutes without a byte on the wire.
    with session_with_retries(
        "wien-oepnv-ki-waechter", timeout=GEMINI_TIMEOUT, keepalive=True, total=0,
    ) as session:
        while sent < limit:
            current = models[sent % len(models)]
            if sent:
                rounds = sent // len(models)
                wait = OVERLOAD_WAIT_S[min(rounds, len(OVERLOAD_WAIT_S)) - 1] if sent % len(models) == 0 else 0
                if time.monotonic() - started + wait > NO_NEW_REQUEST_AFTER_S:
                    print(f"Gemini: nach {time.monotonic() - started:.0f} s keine weitere Anfrage")
                    break
                print(f"Gemini überlastet oder Verbindung abgebrochen, {sent + 1}. von höchstens {limit} "
                      f"Anfragen an {current}" + (f" in {wait} s" if wait else ""))
                if wait:
                    sleep(wait)
            sent += 1
            sent_at = time.monotonic()
            try:
                response = request_safe(
                    session, GEMINI_URL.format(model=current), method="POST", data=body, headers=headers,
                    timeout=GEMINI_TIMEOUT, max_bytes=MAX_ANSWER_BYTES,
                    allowed_content_types=("application/json",), raise_for_status=False,
                )
            except requests.ConnectionError as exc:
                # A dropped connection (2026-11-02: after 261 s, the model still
                # thinking) is handled like an overloaded model: next model,
                # within the share; the request counts, it may have reached Google.
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
            busy = response.status_code in OVERLOAD_STATUS or response.status_code == 429
            if not answer.ok:
                attempts.append(_attempt(current, sent_at, answer.error))
            if response.status_code not in OVERLOAD_STATUS:
                break
    answer.attempts = attempts
    answer.overloaded = busy and not answer.ok
    answer.requests = sent
    answer.seconds = round(time.monotonic() - started, 1)
    return answer


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
        hint = " (Kontingent erschöpft, kein neuer Versuch)" if response.status_code == 429 else ""
        return Answer(False, model, error=f"Gemini HTTP {response.status_code}{hint}: {message or 'ohne Text'}")
    return gemini_answer(payload, model)



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
        answer = ask_gemini(build_prompt(input_text, template), args.model, key, args.max_requests)
    _write_json(work / "antwort.json", asdict(answer))
    # The gate of the next runs reads this annotation to count the day's requests.
    print(f"::notice title=Gemini-Anfragen::{answer.requests}")
    if not answer.ok:
        if answer.overloaded:
            # Exit 1 all the same: no answer, nothing to verify. The workflow
            # reads ``overloaded`` from antwort.json and records the day as
            # "overloaded" instead of "failure".
            print(f"::warning title=KI-Wächter::Gemini überlastet, keine Antwort: {answer.error}")
        else:
            print(f"::error title=KI-Wächter::{answer.error}")
        return 1
    print(f"Gemini {answer.model}: {answer.requests} Anfrage(n), {answer.seconds} s, Tokens {answer.tokens}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    prep = _prep_from_json(Path(args.dir) / "eingabe.json")
    raw = read_capped_json(Path(args.dir) / "antwort.json", label="ki_waechter")
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        print("::error title=KI-Wächter::keine Antwort der KI")
        return 1
    answer = extract_answer(str(raw.get("text") or ""))
    verdict = verify(answer, prep)
    now = datetime.now(UTC)
    report = render_report(prep, verdict, now, str(raw.get("model") or "?"), args.run_url)
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
    a.add_argument("--max-requests", type=int, default=1,
                   help=f"Anteil dieses Laufs am Tagesbudget (höchstens {MAX_REQUESTS_PER_AUDIT})")
    v = sub.add_parser("verify", help="Antwort der KI prüfen und Bericht schreiben")
    v.add_argument("--dir", required=True)
    v.add_argument("--report", default="")
    v.add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    commands = {"prepare": _cmd_prepare, "ask": _cmd_ask, "verify": _cmd_verify}
    return commands[args.cmd](args)


# Public names of this script: its own entry points and the names other
# files and tests read through it. The code lives in src/ (700-line limit,
# 2026-11-02); listing the imports marks them as deliberate re-exports.
__all__ = [
    "asdict",
    "ask_gemini",
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
    "together",
    "visible",
    "working_tree_reader",
]


if __name__ == "__main__":
    sys.exit(main())
