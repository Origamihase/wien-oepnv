"""The one Gemini request behind the yearly Baustellen review.

Owner's decision (Michael, 2026-11-10 17:44 Europe/Vienna), point 4: the
year's Baustellen are summarised by Gemini, his explicit exception to "an
AI only watches and reports, never rewrites" (2026-10-31), which stays for
the feed's texts. This module asks and makes the answer one clean paragraph
(:func:`clean_review`); ``src/markdown_stats/year_review.py`` builds the
prompt and decides with a fixed guard whether the answer is published.

The request is the KI-Wächter's (``scripts/ki_waechter.py``): the same
endpoint, the key in the header ``x-goog-api-key`` (never in the URL or a
log line), one ``user`` content without tools, JSON out in a fixed schema,
the Lite models asked for the top thinking level, the same safety settings
and the same cap on the answer's size. ``tests/test_review_client.py``
keeps the two in step.

What differs, and why: the Wächter has a workflow of its own and may wait
ten minutes for an answer and pass the chain twice. This call runs inside
the statistics step of the Update cycle (the run shortly after Vienna
midnight), which must not hold up the feed, and the ``cycle`` job ends
after ten minutes. So: one pass over the chain, no waiting between models,
at most 90 seconds per answer and no new request after
:data:`DEFAULT_DEADLINE_S`. A year's review can wait a day: the dashboard
run of the next day asks again while the review is missing.

No request counter (owner 2026-11-05: "Zähle es bitte so wie Google. Wir
brauchen keine eigene Zähltechnik."): a model whose quota Google reports
as reached answers 429 and the next one is asked.

The pass over the chain is not tied to the review (:func:`ask_chain`): a
second caller names its own answer schema, parser, log label and time
budget in a :class:`JsonCall`. ``src/ki_entscheidung`` (owner 2026-11-12)
asks through it; :func:`ask_review` is this module's own call.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Generic, TypeVar

import requests

from src.ki_waechter.inputs import _SECRET_RE, free_text
from src.utils.files import loads_finite
from src.utils.http import request_safe, session_with_retries
from src.utils.logging import sanitize_log_arg
from src.utils.text import normalise_markdown_text

LOGGER = logging.getLogger("generate_markdown_stats.review_client")

GEMINI_URL: Final = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_KEY_ENV: Final = "GEMINI_API_KEY"
# Optional other chain for this call, comma-separated, for a trial.
MODEL_ENV: Final = "JAHRESRUECKBLICK_MODEL"
# The chain of ``KI_WAECHTER_MODEL`` in .github/workflows/ki-waechter.yml,
# in its order: the two Flash models of the free tier, then the two Lite
# models. A test compares this tuple with that workflow, so the yearly call
# cannot keep a retired model after the daily audit moved on.
DEFAULT_MODELS: Final[tuple[str, ...]] = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
# What a model name may look like (``ask_gemini`` and the gate of
# ki-waechter.yml use the same shape): the name becomes part of the URL.
_MODEL_NAME_RE: Final = re.compile(r"[a-z0-9.-]{3,60}")
LITE_MARK: Final = "-lite"
LITE_THINKING_LEVEL: Final = "high"
MAX_ANSWER_BYTES: Final = 2_000_000
# A prompt above the Wächter's bound (below the free tier's input tokens a
# minute) is not sent. ``year_review.build_prompt`` stays far below it.
MAX_REQUEST_CHARS: Final = 450_000
CONNECT_TIMEOUT_S: Final = 10.0
MAX_READ_TIMEOUT_S: Final = 90.0
# No new request after this many seconds from the first; the read timeout
# of each request is cut to what is left. With the connect timeout the call
# ends after about two minutes at the latest.
DEFAULT_DEADLINE_S: Final = 110.0
# A request that could wait less than this for its answer is not sent: a
# thinking model rarely answers a year of texts faster, and an abandoned
# request still counts in Google's quota.
MIN_REQUEST_S: Final = 15.0
# The Wächter's overload codes (a test keeps the two sets equal). The hand-over
# below takes every 5xx, not only these: a 501 or 529 names a server that did
# not answer just as well.
OVERLOAD_STATUS: Final = frozenset({500, 502, 503, 504})
QUOTA_STATUS: Final = 429
# A model Google has retired answers 404 (or 400 for a name it no longer
# takes); the next model of the chain may still exist. A rejected key comes
# as 400 too, with the reason below, and ends the pass (_key_rejected).
RETIRED_STATUS: Final = frozenset({400, 404})
KEY_REJECTED_REASON: Final = "API_KEY_INVALID"
HANDOVER_STATUS: Final = OVERLOAD_STATUS | RETIRED_STATUS | {QUOTA_STATUS}
MAX_ERROR_CHARS: Final = 200
# The answer's shape, enforced by the API. It shapes the answer and proves
# nothing: ``year_review.check_review`` decides.
REVIEW_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {"rueckblick": {"type": "string"}},
    "required": ["rueckblick"],
}
_SAFETY_CATEGORIES: Final = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
# What clean_review keeps of an answer: more than the guard of
# ``year_review.check_review`` accepts, so an overlong answer is rejected for
# its length and not cut to fit.
MAX_CLEAN_CHARS: Final = 4000


T = TypeVar("T")


@dataclass(frozen=True)
class JsonCall(Generic[T]):
    """What one caller of :func:`ask_chain` wants: its schema, its parser, its log label and its patience.

    ``parse`` turns the model's JSON text into the caller's value (``None``:
    not usable, ``missing`` says why in the log line). ``min_request_s`` is
    the least time a request must be able to wait for its answer to be sent
    at all, ``max_read_s`` the longest one model is waited for (a caller
    with a short deadline keeps room for the next model that way),
    ``lite_thinking`` the thinking level asked of a Lite model (``None``:
    the model's own default).
    """

    label: str
    user_agent: str
    schema: Mapping[str, Any]
    parse: Callable[[str], T | None]
    missing: str
    min_request_s: float = MIN_REQUEST_S
    max_read_s: float = MAX_READ_TIMEOUT_S
    lite_thinking: str | None = LITE_THINKING_LEVEL


@dataclass(frozen=True)
class ChainAnswer(Generic[T]):
    """What one pass over the chain gave: the value and its model, or neither.

    ``requests`` counts the requests sent, ``key_rejected`` says that Google
    refused the key itself (401, 403 or a 400 naming it), which no later
    request can cure.
    """

    value: T | None
    model: str
    requests: int
    key_rejected: bool


@dataclass(frozen=True)
class ReviewAnswer:
    """The model's text (the value of ``rueckblick``, unchecked) and the model that wrote it."""

    text: str
    model: str


def clean_review(text: object) -> str:
    """The model's answer as one paragraph: whitespace collapsed, no control characters, no credentials.

    Anything that is no string gives an empty text. Control, zero-width
    and BiDi characters are removed, every whitespace run (line breaks
    included) becomes one space, and whatever looks like a credential is
    replaced by ``[entfernt]`` with the Wächter's pattern; the brackets
    then fail the guard, so such a text is never published. Markup is left
    as it is for the same reason: the guard rejects it, nothing hides it.
    """
    if not isinstance(text, str):
        return ""
    return _SECRET_RE.sub("[entfernt]", normalise_markdown_text(text, max_len=MAX_CLEAN_CHARS))


def _valid_models(names: Sequence[str]) -> tuple[str, ...]:
    """The names that look like model names, in their order."""
    return tuple(name for name in (n.strip() for n in names) if _MODEL_NAME_RE.fullmatch(name))


def models_from_env() -> tuple[str, ...]:
    """The chain to ask: ``JAHRESRUECKBLICK_MODEL`` when set and valid, else :data:`DEFAULT_MODELS`.

    The variable is a comma-separated list of model names. One name that
    is no model name discards the whole value (with a log line), so a typo
    cannot shorten the chain unnoticed.
    """
    raw = os.environ.get(MODEL_ENV, "").strip()
    if not raw:
        return DEFAULT_MODELS
    names = [name.strip() for name in raw.split(",") if name.strip()]
    valid = _valid_models(names)
    if not valid or len(valid) != len(names):
        LOGGER.warning(
            "Jahresrückblick: %s nennt keinen gültigen Modellnamen, es gilt die übliche Modellkette.",
            MODEL_ENV,
        )
        return DEFAULT_MODELS
    return valid


def is_lite(model: str) -> bool:
    """Whether *model* belongs to the Lite family (asked to think, :data:`LITE_THINKING_LEVEL`)."""
    return LITE_MARK in model


def review_request(
    prompt: str,
    model: str = "",
    *,
    schema: Mapping[str, Any] = REVIEW_SCHEMA,
    lite_thinking: str | None = LITE_THINKING_LEVEL,
) -> dict[str, Any]:
    """One generateContent body: the prompt, JSON out in *schema*, no tools.

    The shape of ``gemini_request`` in ``scripts/ki_waechter.py`` with this
    call's schema. The safety settings are open for the same reason as
    there: the city's texts name accidents and closures, and a safety block
    would end the call without an answer; what is published is decided by
    the guard, not by these settings.
    """
    generation: dict[str, Any] = {
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }
    if is_lite(model) and lite_thinking is not None:
        generation["thinkingConfig"] = {"thinkingLevel": lite_thinking}
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation,
        "safetySettings": [
            {"category": category, "threshold": "BLOCK_NONE"} for category in _SAFETY_CATEGORIES
        ],
    }


def _payload_of(response: requests.Response) -> Mapping[str, Any] | None:
    """The response body as a JSON object, or ``None``."""
    try:
        payload = loads_finite(response.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _error_text(payload: Mapping[str, Any] | None) -> str:
    """Google's error message, one line, without credentials, capped."""
    error = (payload or {}).get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return free_text(message)[:MAX_ERROR_CHARS] or "ohne Text"


def _candidate_text(payload: Mapping[str, Any]) -> tuple[str, str]:
    """(finish reason, joined text of the non-thought parts) of the first candidate."""
    candidates = payload.get("candidates")
    first = candidates[0] if isinstance(candidates, list) and candidates else None
    if not isinstance(first, dict):
        feedback = payload.get("promptFeedback")
        block = feedback.get("blockReason") if isinstance(feedback, dict) else None
        return (f"keine Antwort ({free_text(block)[:40] or 'ohne Grund'})", "")
    content = first.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    text = "".join(
        str(part.get("text") or "")
        for part in (parts if isinstance(parts, list) else [])
        if isinstance(part, dict) and not part.get("thought")
    )
    return (str(first.get("finishReason") or ""), text)


def _review_of(text: str) -> str | None:
    """The value of ``rueckblick`` in the model's JSON answer, or ``None``."""
    try:
        answer = loads_finite(text)
    except (ValueError, RecursionError):
        return None
    value = answer.get("rueckblick") if isinstance(answer, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _answer_from(response: requests.Response, model: str, call: JsonCall[T]) -> T | None:
    """The caller's value in a generateContent response; ``None`` (and a log line) without one."""
    payload = _payload_of(response)
    if response.status_code != 200:
        LOGGER.warning(
            "%s: Gemini %s antwortet HTTP %d: %s",
            call.label,
            sanitize_log_arg(model),
            response.status_code,
            sanitize_log_arg(_error_text(payload)),
        )
        return None
    if payload is None:
        LOGGER.warning("%s: Antwort von %s ist kein JSON-Objekt.", call.label, sanitize_log_arg(model))
        return None
    reason, text = _candidate_text(payload)
    if reason != "STOP" or not text.strip():
        LOGGER.warning(
            "%s: %s brach ab (%s).",
            call.label,
            sanitize_log_arg(model),
            sanitize_log_arg(free_text(reason)[:60] or "ohne Grund"),
        )
        return None
    value = call.parse(text)
    if value is None:
        LOGGER.warning("%s: Antwort von %s %s.", call.label, sanitize_log_arg(model), call.missing)
    return value


def _key_rejected(response: requests.Response) -> bool:
    """Whether Google refused the key itself: an error whose details carry :data:`KEY_REJECTED_REASON`.

    The Gemini API answers an invalid key with HTTP 400, the status of a
    retired model name. Until the review of 2026-11-10 such a key went to
    all four models of the chain, four requests and four log lines a run.
    """
    error = (_payload_of(response) or {}).get("error")
    details = error.get("details") if isinstance(error, dict) else None
    return any(
        isinstance(detail, dict) and detail.get("reason") == KEY_REJECTED_REASON
        for detail in (details if isinstance(details, list) else [])
    )


def _ask_one(
    session: requests.Session,
    model: str,
    body: str,
    key: str,
    remaining: float,
    call: JsonCall[T],
) -> tuple[T | None, bool, bool]:
    """One request to *model*: (value, whether the next model may be asked, whether the key was refused).

    Hands over on a server error (every 5xx), a retired model, a reached
    quota, a dropped connection (before the answer or while its body is
    read) and a timeout, and on an answer that ``request_safe`` refuses
    with ``ValueError``: it checks the content type before it returns, so
    an error page of Google's front end (a 502 or 503 as ``text/html``, or
    without a type) arrives here as that error, its status unseen
    (reproduced in the review of 2026-11-10; until then such a 5xx ended
    the pass). An answer above the size cap comes the same way and hands
    over too; the chain and the deadline bound it. A refusal that the next
    model would repeat (a rejected key, whether as 401, 403 or as a 400 that
    :func:`_key_rejected` recognises; a 200 whose body is no JSON; an answer
    cut off by the model) and any other transport error end the pass.
    """
    try:
        response = request_safe(
            session,
            GEMINI_URL.format(model=model),
            method="POST",
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            timeout=(min(CONNECT_TIMEOUT_S, remaining), min(call.max_read_s, remaining)),
            max_bytes=MAX_ANSWER_BYTES,
            allowed_content_types=("application/json",),
            raise_for_status=False,
        )
    # ChunkedEncodingError: the connection dropped while the body was read;
    # requests does not file it under ConnectionError.
    except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
        LOGGER.warning(
            "%s: Gemini %s nicht erreichbar (%s).",
            call.label,
            sanitize_log_arg(model),
            type(exc).__name__,
        )
        return None, True, False
    except (requests.RequestException, ValueError) as exc:
        # The class only: the message of a transport error can carry the URL.
        LOGGER.warning(
            "%s: Anfrage an %s gescheitert (%s).",
            call.label,
            sanitize_log_arg(model),
            type(exc).__name__,
        )
        # A plain ValueError is request_safe's own refusal (see the docstring).
        # Some errors of requests are ValueErrors as well (InvalidHeader,
        # InvalidURL); they are transport errors and end the pass.
        return None, not isinstance(exc, requests.RequestException), False
    value = _answer_from(response, model, call)
    status = response.status_code
    rejected = status in (401, 403) or _key_rejected(response)
    hand_over = status in HANDOVER_STATUS or 500 <= status <= 599
    return value, value is None and hand_over and not rejected, rejected


def _ask_chain(
    prompt: str,
    key: str,
    chain: Sequence[str],
    deadline_s: float,
    clock: Callable[[], float],
    call: JsonCall[T],
) -> ChainAnswer[T]:
    bodies = {
        lite: json.dumps(
            review_request(prompt, LITE_MARK if lite else "", schema=call.schema, lite_thinking=call.lite_thinking),
            ensure_ascii=True,
            allow_nan=False,
        )
        for lite in (False, True)
    }
    started = clock()
    sent = 0
    # total=0: the session never repeats a request; the loop sends every one.
    with session_with_retries(
        call.user_agent,
        timeout=(CONNECT_TIMEOUT_S, MAX_READ_TIMEOUT_S),
        total=0,
    ) as session:
        for model in chain:
            remaining = deadline_s - (clock() - started)
            if remaining < call.min_request_s:
                LOGGER.warning(
                    "%s: nach %d s keine weitere Anfrage (ab %s nicht gefragt).",
                    call.label,
                    int(clock() - started),
                    sanitize_log_arg(model),
                )
                break
            sent += 1
            one = _ask_one(session, model, bodies[is_lite(model)], key, remaining, call)
            value, hand_over, rejected = one
            if value is not None or not hand_over:
                return ChainAnswer(value, model if value is not None else "", sent, rejected)
    return ChainAnswer(None, "", sent, False)


def ask_chain(
    prompt: str,
    *,
    key: str,
    models: Sequence[str],
    call: JsonCall[T],
    deadline_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> ChainAnswer[T]:
    """Send *prompt* along *models* once for *call*; the first model whose answer ``call.parse`` accepts wins.

    Each model is asked at most once, in order, without a wait in between:
    a server error (every 5xx, also one that arrives as an error page
    instead of JSON), a reached quota (429), a retired model (404, 400
    without a rejected key), a dropped connection or a timeout hands over
    to the next one, as long as at least ``call.min_request_s`` of
    *deadline_s* are left (measured with *clock* from the first request).
    Every request waits at most ``min(call.max_read_s, seconds left)`` for
    its answer and as long for its connection (ten seconds at most).
    An answer counts only with ``finishReason`` ``STOP`` and a value from
    ``call.parse``.

    Never raises: without an answer the value is ``None`` and the log names
    the HTTP status or the class of the error under ``call.label`` (Google's
    error text capped at 200 characters and cleaned like the Wächter's). The
    key travels in a header, never in the URL or a log line.
    """
    try:
        chain = _valid_models(models)
        if not chain:
            LOGGER.warning("%s: kein gültiger Modellname, keine Anfrage.", call.label)
            return ChainAnswer(None, "", 0, False)
        if not key.strip():
            LOGGER.warning("%s: %s ist leer, keine Anfrage.", call.label, GEMINI_KEY_ENV)
            return ChainAnswer(None, "", 0, False)
        if len(prompt) > MAX_REQUEST_CHARS:
            LOGGER.warning(
                "%s: Eingabe zu groß (%d Zeichen, Grenze %d); nicht gesendet.",
                call.label,
                len(prompt),
                MAX_REQUEST_CHARS,
            )
            return ChainAnswer(None, "", 0, False)
        return _ask_chain(prompt, key.strip(), chain, deadline_s, clock, call)
    except Exception as exc:  # the caller's step must not fail on this call
        LOGGER.warning("%s: Anfrage abgebrochen (%s).", call.label, type(exc).__name__)
        return ChainAnswer(None, "", 0, False)


_REVIEW_CALL: Final[JsonCall[str]] = JsonCall(
    label="Jahresrückblick",
    user_agent="wien-oepnv-jahresrueckblick",
    schema=REVIEW_SCHEMA,
    parse=_review_of,
    missing="enthält keinen Text unter „rueckblick“",
)


def ask_review(
    prompt: str,
    *,
    key: str,
    models: Sequence[str] | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
    clock: Callable[[], float] = time.monotonic,
) -> ReviewAnswer | None:
    """Send *prompt* along the chain once; the first model that answers wins.

    *models* defaults to :func:`models_from_env`. The pass is
    :func:`ask_chain` with this module's schema: an answer counts only with
    a non-empty string under ``rueckblick``; at least :data:`MIN_REQUEST_S`
    of *deadline_s* must be left for a further request.

    Never raises: without an answer the result is ``None``. Why so short:
    see the module docstring.
    """
    try:
        chain = models if models is not None else models_from_env()
    except Exception as exc:  # the statistics step must not fail on this call
        LOGGER.warning("Jahresrückblick: Anfrage abgebrochen (%s).", type(exc).__name__)
        return None
    answer = ask_chain(prompt, key=key, models=chain, call=_REVIEW_CALL, deadline_s=deadline_s, clock=clock)
    return ReviewAnswer(text=answer.value, model=answer.model) if answer.value is not None else None
