"""The year's Baustellen in words: the prompt, the guard and the stored review.

Owner's decision (Michael, 2026-11-10 17:44 Europe/Vienna), point 4: "The
year's Baustellen (roadworks) are summarised by Gemini." It is his explicit
exception to "an AI only watches and reports, never rewrites" (2026-10-31),
which stays for the feed's texts: this is the first text of a model the project
publishes, and only on the page of a finished year.

Three parts:

* :func:`build_prompt` gives the model the year's sites
  (``src/markdown_stats/baustellen_year.py``) as data, framed like the
  KI-Wächter's input, and a block of facts the script counted itself.
* :func:`check_review` is the guard. The Wächter proves a finding by its
  verbatim quote (``src/ki_waechter/audit.py``); prose has no quote, so the
  guard checks what can be checked mechanically: every number, date, line,
  street, district and month the text names must stand in the data, a
  count of sites must be one the script counted, and closed lists stand
  for tone and language. A text that fails is not published; count and
  list of the page come from the ledger either way.
* :func:`ensure_review` asks once per call when no accepted text is stored
  and keeps an accepted one in ``data/stats/baustellen_rueckblick_<Jahr>.json``
  next to the year's ledgers; the page is rendered from both.

No request counter (owner 2026-11-05: "Zähle es bitte so wie Google. Wir
brauchen keine eigene Zähltechnik."): the caller asks at most once per
dashboard run while a year has no accepted review.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from src.ki_waechter.inputs import free_text
from src.markdown_stats.baustellen_year import LEDGER_KIND, BaustelleSite
from src.markdown_stats.review_client import GEMINI_KEY_ENV, ReviewAnswer, ask_review, clean_review
from src.markdown_stats.rows import LOGGER
from src.markdown_stats.year_review_guard import (
    _ORDINARY_WORDS,
    _VOICE_MARKERS,
    MAX_REVIEW_CHARS,
    MAX_SENTENCES,
    MIN_GERMAN_SHARE,
    MIN_REVIEW_CHARS,
    MIN_SENTENCES,
    STREET_SUFFIXES,
    _count_reasons,
    _digit_tokens,
    _district_reasons,
    _judgement_reasons,
    _language_reasons,
    _length_reasons,
    _markup_reasons,
    _month_reasons,
    _number_reasons,
    _street_reasons,
    _voice_reasons,
    check_review,
    count_sentences,
)
from src.markdown_stats.year_review_prompt import (
    DATA_CLOSE,
    DATA_OPEN,
    DATA_SENTENCE,
    MAX_PROMPT_CHARS,
    build_prompt,
    source_text,
)
from src.utils.files import atomic_write, read_capped_json
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives
from src.utils.stats import stats_path, to_vienna

__all__ = [
    "DATA_CLOSE",
    "DATA_OPEN",
    "DATA_SENTENCE",
    "MAX_PROMPT_CHARS",
    "MAX_REVIEW_CHARS",
    "MAX_REVIEW_FILE_BYTES",
    "MAX_SENTENCES",
    "MIN_GERMAN_SHARE",
    "MIN_REVIEW_CHARS",
    "MIN_SENTENCES",
    "REVIEW_FILE_STEM",
    "STREET_SUFFIXES",
    "ReviewOutcome",
    "_ORDINARY_WORDS",
    "_VOICE_MARKERS",
    "_count_reasons",
    "_digit_tokens",
    "_district_reasons",
    "_judgement_reasons",
    "_language_reasons",
    "_length_reasons",
    "_markup_reasons",
    "_month_reasons",
    "_number_reasons",
    "_street_reasons",
    "_voice_reasons",
    "build_prompt",
    "check_review",
    "count_sentences",
    "ensure_review",
    "load_review",
    "reset_dropped_review_log",
    "review_path",
    "source_text",
]

MAX_REVIEW_FILE_BYTES: Final = 64 * 1024
REVIEW_FILE_STEM: Final = "baustellen_rueckblick"


# ---- the stored review ----------------------------------------------------


@dataclass(frozen=True)
class ReviewOutcome:
    """What :func:`ensure_review` did: the accepted text (or none), whether Gemini was asked, and why."""

    text: str | None
    asked: bool
    reason: str


def review_path(year: int, *, stats_dir: Path | None = None) -> Path:
    """``baustellen_rueckblick_<year>.json`` in the statistics folder (looked up at call time)."""
    ledger = stats_path(LEDGER_KIND, year, base_dir=stats_dir)
    return ledger.with_name(f"{REVIEW_FILE_STEM}_{year:04d}.json")


# (file, year, reason) of each dropped stored review logged in this run (one process), which reads
# it up to four times: the same WARNING stood four times in a log (review of 2026-11-10).
_LOGGED_DROPS: Final[set[tuple[str, int, str]]] = set()


def reset_dropped_review_log() -> None:
    """Start a new run in the same process: the next read logs each dropped review again."""
    _LOGGED_DROPS.clear()


def _warn_dropped(path: Path, year: int, message: str) -> None:
    if (str(path), year, message) not in _LOGGED_DROPS:
        _LOGGED_DROPS.add((str(path), year, message))
        LOGGER.warning("Baustellen-Rückblick %d: %s", year, message)


def load_review(year: int, sites: Sequence[BaustelleSite], *, stats_dir: Path | None = None) -> str | None:
    """The stored review of *year*, if it still holds against *sites*.

    ``None`` when the file is missing, oversized, no JSON object with a
    string ``text``, or when the text no longer passes
    :func:`check_review`: the ledger of a finished year can still change
    (late rows, corrections), and a review that names a site no longer in it
    must not stay on the page. A dropped text gets one WARNING a run with
    the reasons; until the review of 2026-11-10 the log held a count or none.
    """
    path = review_path(year, stats_dir=stats_dir)
    data = read_capped_json(path, max_bytes=MAX_REVIEW_FILE_BYTES, label="Baustellen-Rückblick", logger=LOGGER)
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text:
        return None
    if clean_review(text) != text:
        _warn_dropped(path, year, "der gespeicherte Text ist nicht in der Form, in der er gespeichert wird "
                                  "(Leerraum oder Steuerzeichen), und wird nicht verwendet.")
        return None
    reasons = check_review(text, sites, year=year)
    if reasons:
        _warn_dropped(path, year, "der gespeicherte Text passt nicht mehr zu den Daten oder zur Prüfung: "
                                  f"{sanitize_log_arg('; '.join(reasons))}.")
        return None
    return text


def _store(path: Path, *, year: int, now: datetime, model: str, text: str, sites: int) -> None:
    created = to_vienna(now).replace(microsecond=0).isoformat()
    payload = scrub_trojan_source_primitives({"year": year, "created_at": created, "model": model, "text": text, "sites": sites})
    with atomic_write(path) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _ask_and_check(
    year: int, sites: Sequence[BaustelleSite], prompt: str, *, now: datetime, stats_dir: Path | None,
    ask: Callable[[str], ReviewAnswer | None],
) -> ReviewOutcome:
    """One request, the guard, and the file on acceptance."""
    try:
        answer = ask(prompt)
    except Exception as exc:  # whatever the client or a stand-in raises: no review today
        LOGGER.warning("Baustellen-Rückblick %d: Anfrage gescheitert (%s).", year, type(exc).__name__)
        return ReviewOutcome(None, True, f"Anfrage gescheitert ({type(exc).__name__})")
    if answer is None:
        LOGGER.warning("Baustellen-Rückblick %d: keine Antwort von Gemini.", year)
        return ReviewOutcome(None, True, "keine Antwort von Gemini")
    model = free_text(answer.model)[:60] or "?"
    text = clean_review(answer.text)
    reasons = check_review(text, sites, year=year)
    if reasons:
        LOGGER.warning(
            "Baustellen-Rückblick %d von %s verworfen: %s. Text: %s",
            year, sanitize_log_arg(model), sanitize_log_arg("; ".join(reasons)), sanitize_log_arg(free_text(text)),
        )
        return ReviewOutcome(None, True, "verworfen: " + "; ".join(reasons))
    try:
        _store(review_path(year, stats_dir=stats_dir), year=year, now=now, model=model, text=text, sites=len(sites))
    except OSError as exc:
        LOGGER.warning("Baustellen-Rückblick %d angenommen, aber nicht gespeichert (%s).", year, type(exc).__name__)
        return ReviewOutcome(text, True, f"angenommen ({model}), nicht gespeichert ({type(exc).__name__})")
    LOGGER.info("Baustellen-Rückblick %d von %s angenommen und gespeichert.", year, sanitize_log_arg(model))
    return ReviewOutcome(text, True, f"angenommen ({model})")


def ensure_review(
    year: int, sites: Sequence[BaustelleSite], *, now: datetime, stats_dir: Path | None = None, key: str | None = None,
    ask: Callable[[str], ReviewAnswer | None] | None = None, deadline_s: float | None = None,
) -> ReviewOutcome:
    """The review of *year*: the stored one, or one new request to Gemini.

    * A stored text that still passes the guard is returned, nothing is
      asked.
    * Without *sites* there is nothing to review.
    * The key is *key*, or the environment's ``GEMINI_API_KEY``; without
      one nothing is asked and the reason says so.
    * Otherwise the prompt goes out once through *ask* (default:
      :func:`src.markdown_stats.review_client.ask_review` with that key).
      The answer is cleaned (:func:`clean_review`) and checked
      (:func:`check_review`); an accepted text is stored atomically with
      the year, the time (*now*, Vienna), the model and the number of
      sites.
    * *deadline_s* is what is left of one time budget for all years of a
      run, handed to ``ask_review`` (nothing is sent with fewer than
      ``MIN_REQUEST_S`` left): until the review of 2026-11-10 each year
      had a full deadline, and two years reached the step's limit.

    It never raises: the statistics step and the year page do not depend
    on a review. Every failure is an outcome with a reason and one WARNING
    line; of a rejected text the log holds the reasons and at most 400
    cleaned characters.
    """
    asked = False
    try:
        stored = load_review(year, sites, stats_dir=stats_dir)
        if stored is not None:
            return ReviewOutcome(stored, False, "gespeicherter Rückblick")
        if not sites:
            return ReviewOutcome(None, False, "keine Baustellen im Jahr")
        secret = (key if key is not None else os.environ.get(GEMINI_KEY_ENV, "")).strip()
        if not secret:
            LOGGER.warning("Baustellen-Rückblick %d: %s ist leer, keine Anfrage.", year, GEMINI_KEY_ENV)
            return ReviewOutcome(None, False, f"{GEMINI_KEY_ENV} ist leer, keine Anfrage")

        def default_ask(prompt: str) -> ReviewAnswer | None:
            if deadline_s is None:
                return ask_review(prompt, key=secret)
            return ask_review(prompt, key=secret, deadline_s=deadline_s)

        prompt = build_prompt(sites, year=year)
        asked = True
        return _ask_and_check(year, sites, prompt, now=now, stats_dir=stats_dir, ask=ask or default_ask)
    except Exception as exc:  # see the docstring: never raises
        LOGGER.warning("Baustellen-Rückblick %d: abgebrochen (%s).", year, type(exc).__name__)
        return ReviewOutcome(None, asked, f"abgebrochen ({type(exc).__name__})")
