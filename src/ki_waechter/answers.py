"""The model's answers: one per half of the input, united into one (``scripts/ki_waechter.py``).

Since 2026-11-05 every audit sends two requests. Until 2026-11-07 both carried
the whole input and the second went to the model that answered, once
(``ask_twice`` in the script, kept for an input of one version). Since
2026-11-07 each request reads one half of the day
(:func:`src.ki_waechter.inputs.halves`): the same two requests, about half
the texts per answer. Measured on 07.11.2026 on the input of 06.11. (commit
6929f27, 52 versions, 102 texts, Gemini 3.6 Flash): the halves (runs 328 and
329) and the whole input twice (run 330) found the same two errors, the
halves with 81,287 instead of 130,242 prompt tokens per audit; Michael took
them for that saving (07.11.2026, 17:50 Wiener Zeit). The findings of both
answers are united as before (:func:`unite_answers`).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.utils.serialize import scrub_trojan_source_primitives

from .audit import extract_answer
from .inputs import Part


@dataclass
class Answer:
    ok: bool
    model: str
    text: str = ""
    error: str = ""
    tokens: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    # Requests sent, in all and per model (for the reader of the run; nothing
    # counts them against a budget since 2026-11-05, Google counts).
    requests: int = 0
    per_model: dict[str, int] = field(default_factory=dict)
    # One line per request that got no answer: model, seconds, what happened.
    attempts: list[str] = field(default_factory=list)
    # No answer only because Google was busy: the last request ended in an
    # overload (500/502/503/504), a dropped connection or the quota (429).
    # The Health check shows such a day as a note and turns red only after
    # 30 hours without a clean audit (Michael 2026-11-04, card "Ja, so
    # bauen"); a rejected key, a missing model or a broken answer stay red at
    # once.
    overloaded: bool = False
    # Answers united in ``text``: 2 when both halves (or, without halves,
    # both requests to the same model) answered, 1 when only the first did,
    # 0 without an answer.
    answers: int = 0
    # One note per half of the input (:func:`ask_parts`): which model
    # answered it, or why it got no answer. Empty when the input was not cut.
    parts: list[dict[str, Any]] = field(default_factory=list)


def ask_parts(
    prompts: Sequence[str],
    model: str,
    key: str,
    ask: Callable[..., Answer],
    *,
    sleep: Callable[[float], None] = time.sleep,
    max_rounds: int = 2,
    deadline_s: float = 720.0,
) -> Answer:
    """One answer per prompt (a half of the input each), united; *ask* is the chain client.

    The first half goes along the chain *model* names (``ask(prompt, chain,
    key, sleep, max_rounds, deadline_s=…)``); without an answer the audit has
    none, as before. The second half goes first to the model that answered,
    then to the rest of the chain, so that both halves are read by the same
    model whenever it is available and a busy model does not leave half of
    the day unread. No request starts after *deadline_s* seconds from the
    first request (the step limit of the workflow): the second half gets the
    seconds the first left over. An unread half is noted in ``parts`` and
    shows in the report as texts not checked.
    """
    models = [m.strip() for m in model.split(",") if m.strip()]
    first = ask(prompts[0], model, key, sleep, max_rounds, deadline_s=deadline_s)
    first.answers = 1 if first.ok else 0
    first.parts = [_note(1, first)]
    if not first.ok or len(prompts) < 2:
        return first
    if first.seconds > deadline_s:
        print(f"Gemini: erste Antwort erst nach {first.seconds:.0f} s, keine Anfrage für Teil 2")
        first.parts.append({"teil": 2, "model": "", "ok": False, "requests": 0, "seconds": 0.0,
                            "error": f"keine Anfrage (erste Antwort nach {first.seconds:.0f} s)"})
        return first
    chain = ",".join([first.model, *(m for m in models if m != first.model)])
    print(f"Gemini: Anfrage für Teil 2 an {chain}", flush=True)
    second = ask(prompts[1], chain, key, sleep, max_rounds, deadline_s=round(deadline_s - first.seconds, 1))
    united = unite_answers(first, second)
    united.parts = [first.parts[0], _note(2, second)]
    return united


def _note(nr: int, answer: Answer) -> dict[str, Any]:
    return {"teil": nr, "model": answer.model if answer.ok else "", "ok": answer.ok, "requests": answer.requests,
            "seconds": answer.seconds, "error": "" if answer.ok else answer.error[:160]}


def describe_parts(notes: Sequence[Mapping[str, Any]], parts: Sequence[Part]) -> str:
    """The report's words for the halves: ``, zwei Antworten, je eine Hälfte der Fassungen (1–20: 56 Texte, …)``."""
    if not notes:
        return ""
    by_nr = {part.nr: part for part in parts}
    pieces = []
    for note in notes:
        nr = note.get("teil")
        part = by_nr.get(nr if isinstance(nr, int) else -1)
        if part:
            versions = str(part.first_version) if part.first_version == part.last_version else f"{part.first_version}–{part.last_version}"
            span = f"{versions}: {len(part.items)} {'Text' if len(part.items) == 1 else 'Texte'}"
        else:
            span = f"Teil {nr}"
        if note.get("ok"):
            pieces.append(f"{span}, {note.get('model') or '?'}")
        else:
            pieces.append(f"{span}, ohne Antwort ({str(note.get('error') or 'keine Anfrage')[:80]})")
    answered = sum(1 for note in notes if note.get("ok"))
    head = "zwei Antworten" if answered == 2 else "eine Antwort"
    return f", {head}, je eine Hälfte der Fassungen ({'; '.join(pieces)})"


def unite_answers(first: Answer, second: Answer) -> Answer:
    """One answer from two: every finding of either, every text either checked, both lists of hypotheses.

    The requests, models and failed attempts of both are kept. A second
    answer that did not come (refusal, overload, quota) or carries no JSON
    leaves the first answer's text as it is.
    """
    united = Answer(
        True, first.model, text=first.text, tokens=dict(first.tokens),
        seconds=round(first.seconds + second.seconds, 1), requests=first.requests + second.requests,
        per_model=dict(first.per_model), attempts=first.attempts + second.attempts, answers=1,
    )
    for name, count in second.per_model.items():
        united.per_model[name] = united.per_model.get(name, 0) + count
    if not second.ok:
        print(f"Gemini: zweite Anfrage ohne Antwort ({second.error[:120]}); die erste Antwort gilt")
        return united
    try:
        parsed = (extract_answer(first.text), extract_answer(second.text))
    except ValueError as exc:
        print(f"Gemini: eine Antwort ohne JSON ({exc}); die erste Antwort gilt")
        return united
    # Model text, scrubbed like every writer of it (zero-width and BiDi characters).
    united.text = json.dumps(scrub_trojan_source_primitives(_union(*parsed)), ensure_ascii=False, allow_nan=False)
    for name, count in second.tokens.items():
        united.tokens[name] = united.tokens.get(name, 0) + count
    united.answers = 2
    return united


def _finding_key(raw: Mapping[str, Any]) -> tuple[object, ...]:
    """What makes two findings the same: item, class and feed (``verify`` keys the same way, by GUID).

    The quote is no part of it: the two answers quote one error at different
    lengths, and the prompt asks for one finding per item and class (several
    places of one class in one text are one finding with the first quote). A
    duplicate (F1) named from either of its two items is one finding.
    """
    nr = raw.get("nr")
    klasse = str(raw.get("klasse") or "").upper()
    if klasse == "F1":
        nr2 = raw.get("nr2")
        return ("F1", frozenset(n if isinstance(n, int) else None for n in (nr, nr2)))
    return (nr if isinstance(nr, int) else None, klasse, str(raw.get("feed") or "").upper())


def _union(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    """The two answers as one, in the answer's own shape; the first answer's entries come first."""
    findings: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for raw in [*(first.get("funde") or []), *(second.get("funde") or [])]:
        if isinstance(raw, dict) and _finding_key(raw) not in seen:
            seen.add(_finding_key(raw))
            findings.append(dict(raw))
    checked = sorted({n for part in (first, second) for n in (part.get("geprueft") or []) if isinstance(n, int)})
    hypotheses: list[str] = []
    named: set[str] = set()
    for part in (first, second):
        for hypothesis in part.get("vermutungen") or []:
            text = str(hypothesis).strip()
            # Both answers wonder about the same item ("Meldung 34: …"): one hypothesis, the first wording.
            match = _HYPOTHESIS_ITEM_RE.match(text)
            key = f"Meldung {match.group(1)}" if match else text
            if text and key not in named:
                named.add(key)
                hypotheses.append(text)
    return {"geprueft": checked, "funde": findings, "vermutungen": hypotheses}


_HYPOTHESIS_ITEM_RE = re.compile(r"Meldung\s+(\d+)\b")
