"""The Gemini request of the yearly Baustellen review (``src/markdown_stats/review_client.py``).

Owner's decision of 2026-11-10 (Michael), point 4. The request is the
KI-Wächter's with another schema, and it runs inside the statistics step
of the Update cycle: one pass over the chain of models, a short deadline,
and never an exception. No test here reaches the network: ``request_safe``
of the module is replaced by a fake.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from scripts import ki_waechter as kw
from src.markdown_stats import review_client as rc
from src.markdown_stats.review_client import ReviewAnswer
from src.utils.http import session_with_retries

REPO = Path(__file__).resolve().parents[1]
KEY = "SECRET-KEY-0123456789"
REVIEW = "Im Jahr 2026 sind 4 Baustellen erfasst."
BUSY = {"error": {"message": "This model is currently experiencing high demand."}}


@pytest.fixture(autouse=True)
def _usual_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests ask the default chain, whatever ``JAHRESRUECKBLICK_MODEL`` the caller's shell holds."""
    monkeypatch.delenv(rc.MODEL_ENV, raising=False)


class _Response:
    """A response as ``request_safe`` returns it (the convention of tests/test_ki_waechter/_support.py)."""

    def __init__(self, status: int, payload: object, raw: bytes | None = None) -> None:
        self.status_code = status
        self.content = raw if raw is not None else json.dumps(payload).encode("utf-8")


def _ok(text: str = json.dumps({"rueckblick": REVIEW}), reason: str = "STOP") -> _Response:
    return _Response(200, {
        "candidates": [{"content": {"parts": [{"text": "nachgedacht", "thought": True}, {"text": text}]},
                        "finishReason": reason}],
        "usageMetadata": {"promptTokenCount": 16000, "candidatesTokenCount": 150},
    })


def _fake(monkeypatch: pytest.MonkeyPatch, replies: Sequence[_Response | Exception]) -> list[dict[str, Any]]:
    """Replace ``request_safe``: each call takes the next reply (an exception is raised)."""
    queue = list(replies)
    calls: list[dict[str, Any]] = []

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(rc, "request_safe", fake_request)
    return calls


def _models(calls: list[dict[str, Any]]) -> list[str]:
    return [call["url"].split("/models/")[1].split(":")[0] for call in calls]


# ---- the chain and the request's shape ------------------------------------------


def test_the_default_chain_is_the_one_of_the_ki_waechter_workflow() -> None:
    """The yearly call must not keep a retired model after the daily audit moved on."""
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    assert list(rc.DEFAULT_MODELS) == doc["env"]["KI_WAECHTER_MODEL"].split(",")
    assert len(rc.DEFAULT_MODELS) == 4


def test_the_request_is_the_waechters_with_another_schema() -> None:
    assert rc.GEMINI_URL == kw.GEMINI_URL and rc.GEMINI_KEY_ENV == kw.GEMINI_KEY_ENV == "GEMINI_API_KEY"
    assert (rc.LITE_MARK, rc.LITE_THINKING_LEVEL) == (kw.LITE_MARK, kw.LITE_THINKING_LEVEL)
    assert rc.MAX_ANSWER_BYTES == kw.MAX_ANSWER_BYTES and rc.MAX_REQUEST_CHARS == kw.MAX_PROMPT_CHARS
    assert rc.OVERLOAD_STATUS == kw.OVERLOAD_STATUS and rc.QUOTA_STATUS == kw.QUOTA_STATUS
    assert rc.REVIEW_SCHEMA == {
        "type": "object",
        "properties": {"rueckblick": {"type": "string"}},
        "required": ["rueckblick"],
    }
    for model in ("gemini-3.8-flash", "gemini-3.5-flash-lite"):
        ours, theirs = rc.review_request("Prompt", model), kw.gemini_request("Prompt", model)
        our_schema = ours["generationConfig"].pop("responseJsonSchema")
        their_schema = theirs["generationConfig"].pop("responseJsonSchema")
        assert (our_schema, their_schema) == (rc.REVIEW_SCHEMA, kw.ANSWER_SCHEMA)
        assert ours == theirs
    assert "tools" not in rc.review_request("Prompt") and "systemInstruction" not in rc.review_request("Prompt")


def test_one_request_with_the_key_in_a_header(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    calls = _fake(monkeypatch, [_ok()])
    with caplog.at_level(logging.DEBUG):
        answer = rc.ask_review("Der Prompt", key=KEY)
    # Thought parts are not the answer; the text is the value of "rueckblick".
    assert answer == ReviewAnswer(text=REVIEW, model="gemini-3.8-flash")
    (call,) = calls
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert KEY not in call["url"] and KEY not in call["data"]
    assert call["headers"] == {"Content-Type": "application/json", "x-goog-api-key": KEY}
    assert call["method"] == "POST" and call["raise_for_status"] is False
    assert call["allowed_content_types"] == ("application/json",)
    assert call["max_bytes"] == rc.MAX_ANSWER_BYTES == 2_000_000
    assert call["timeout"] == (10.0, 90.0)
    body = json.loads(call["data"])
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Der Prompt"}]}]
    assert body["generationConfig"] == {"responseMimeType": "application/json", "responseJsonSchema": rc.REVIEW_SCHEMA}
    assert KEY not in caplog.text


def test_the_session_never_repeats_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def spy(user_agent: str, **kwargs: Any) -> requests.Session:
        seen.append((user_agent, kwargs))
        return session_with_retries(user_agent, **kwargs)

    monkeypatch.setattr(rc, "session_with_retries", spy)
    _fake(monkeypatch, [_ok()])
    assert rc.ask_review("Prompt", key=KEY) is not None
    assert seen == [("wien-oepnv-jahresrueckblick", {"timeout": (10.0, 90.0), "total": 0})]


def test_a_lite_model_is_asked_to_think(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_Response(503, BUSY), _ok()])
    answer = rc.ask_review("Prompt", key=KEY, models=["gemini-3.8-flash", "gemini-3.1-flash-lite"])
    assert answer is not None and answer.model == "gemini-3.1-flash-lite"
    flash, lite = (json.loads(call["data"])["generationConfig"] for call in calls)
    assert "thinkingConfig" not in flash and lite["thinkingConfig"] == {"thinkingLevel": "high"}


# ---- hand-over along the chain -------------------------------------------------------


# 501 and 529: every 5xx hands over, not only the Wächter's four codes (review_client.OVERLOAD_STATUS).
@pytest.mark.parametrize("status", [500, 501, 502, 503, 504, 529, 599, 429, 404, 400])
def test_an_overloaded_spent_or_retired_model_hands_over(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    calls = _fake(monkeypatch, [_Response(status, BUSY), _ok()])
    answer = rc.ask_review("Prompt", key=KEY)
    assert answer == ReviewAnswer(text=REVIEW, model="gemini-3.6-flash")
    assert _models(calls) == ["gemini-3.8-flash", "gemini-3.6-flash"]


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectionError("Verbindung abgebrochen"),
        requests.ReadTimeout("keine Antwort"),
        requests.ConnectTimeout("kein Verbindungsaufbau"),
        requests.Timeout("Zeit abgelaufen"),
        # For requests a failed TLS handshake is a connection error as well.
        requests.exceptions.SSLError("certificate verify failed"),
        # A connection that drops while the body is read is no ConnectionError for requests.
        requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead"),
    ],
)
def test_a_dropped_connection_or_a_timeout_hands_over(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    calls = _fake(monkeypatch, [error, _ok()])
    answer = rc.ask_review("Prompt", key=KEY)
    assert answer is not None and answer.model == "gemini-3.6-flash" and len(calls) == 2


def test_the_chain_is_passed_once_in_its_order_without_a_second_round(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _fake(monkeypatch, [_Response(503, BUSY), requests.ReadTimeout("x"), _Response(429, BUSY),
                                _Response(404, BUSY), _ok()])
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY, clock=lambda: 0.0) is None
    assert _models(calls) == list(rc.DEFAULT_MODELS) and slept == []
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "Jahresrückblick: Gemini gemini-3.8-flash antwortet HTTP 503: This model is currently experiencing high demand.",
        "Jahresrückblick: Gemini gemini-3.6-flash nicht erreichbar (ReadTimeout).",
        "Jahresrückblick: Gemini gemini-3.5-flash-lite antwortet HTTP 429: This model is currently experiencing high demand.",
        "Jahresrückblick: Gemini gemini-3.1-flash-lite antwortet HTTP 404: This model is currently experiencing high demand.",
    ]


@pytest.mark.parametrize(
    "error",
    [
        # What request_safe raises for a 502/503 error page of Google's front end: it checks the
        # content type before it returns, also with raise_for_status=False (src/utils/http.py).
        ValueError("Invalid Content-Type: text/html (expected ('application/json',))"),
        ValueError("Content-Type header missing, but validation required"),
        ValueError("Response too large"),                 # the answer above the size cap
    ],
)
def test_an_answer_the_transport_refuses_hands_over(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    """Review of 2026-11-10: a 503 that came as an HTML page ended the pass after the first model."""
    calls = _fake(monkeypatch, [error, _ok()])
    with caplog.at_level(logging.WARNING):
        answer = rc.ask_review("Prompt", key=KEY)
    assert answer == ReviewAnswer(text=REVIEW, model="gemini-3.6-flash")
    assert _models(calls) == ["gemini-3.8-flash", "gemini-3.6-flash"]
    # The class only: the message is not logged.
    assert [record.getMessage() for record in caplog.records] == [
        "Jahresrückblick: Anfrage an gemini-3.8-flash gescheitert (ValueError)."
    ]


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_ends_the_pass(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    calls = _fake(monkeypatch, [_Response(status, {"error": {"message": "API key not valid."}}), _ok()])
    assert rc.ask_review("Prompt", key=KEY) is None
    assert len(calls) == 1


@pytest.mark.parametrize("status", [302, 418, 499, 600])
def test_a_status_next_to_the_server_errors_ends_the_pass(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    calls = _fake(monkeypatch, [_Response(status, BUSY), _ok()])
    assert rc.ask_review("Prompt", key=KEY) is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        requests.TooManyRedirects("Exceeded 10 redirects"),
        # Errors of requests that are ValueErrors as well stay transport errors.
        requests.exceptions.InvalidHeader("x"),
        requests.exceptions.InvalidURL("x"),
    ],
)
def test_another_transport_error_ends_the_pass(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    calls = _fake(monkeypatch, [error, _ok()])
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY) is None
    assert len(calls) == 1
    # The class only: a transport error's message can carry the URL.
    assert caplog.records[-1].getMessage() == (
        f"Jahresrückblick: Anfrage an gemini-3.8-flash gescheitert ({type(error).__name__})."
    )


# ---- the deadline -------------------------------------------------------------------


class _Clock:
    """A clock the fake request moves forward."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_read_timeout_is_cut_to_what_the_deadline_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    calls: list[dict[str, Any]] = []
    took = [60.0, 30.0, 10.0, 5.0]

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        clock.now += took[len(calls) - 1]
        return _Response(503, BUSY)

    monkeypatch.setattr(rc, "request_safe", fake_request)
    assert rc.ask_review("Prompt", key=KEY, clock=clock) is None
    # 110 s in all: 110 left (cut to 90), then 50, then 20; with 10 s left no request is sent.
    assert [call["timeout"] for call in calls] == [(10.0, 90.0), (10.0, 50.0), (10.0, 20.0)]
    assert _models(calls) == list(rc.DEFAULT_MODELS[:3])


def test_no_new_request_after_the_deadline(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    calls: list[str] = []

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        clock.now += 100.0
        raise requests.ReadTimeout("keine Antwort")

    monkeypatch.setattr(rc, "request_safe", fake_request)
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY, clock=clock) is None
    assert len(calls) == 1
    assert caplog.records[-1].getMessage() == (
        "Jahresrückblick: nach 100 s keine weitere Anfrage (ab gemini-3.6-flash nicht gefragt)."
    )
    # A caller's deadline counts the same way.
    calls.clear()
    clock.now = 0.0
    assert rc.ask_review("Prompt", key=KEY, clock=clock, deadline_s=300.0) is None
    assert len(calls) == 3
    assert rc.DEFAULT_DEADLINE_S == 110.0 and rc.MIN_REQUEST_S == 15.0


# ---- answers that are none ---------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        _Response(200, None, raw=b"kein JSON"),
        _Response(200, None, raw=b"\xff\xfe"),
        _Response(200, ["eine", "Liste"]),
        _Response(200, None, raw=b"[" * 50_000),
        _Response(200, {"candidates": []}),
        _Response(200, {"candidates": ["kein Objekt"]}),
        _Response(200, {"promptFeedback": {"blockReason": "SAFETY"}}),
        _Response(200, {"candidates": [{"finishReason": "STOP"}]}),
        _Response(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": "keine Liste"}}]}),
        _ok(reason="MAX_TOKENS"),
        _ok(reason="SAFETY"),
        _ok(reason=""),
        _ok(text="   "),
        _ok(text="kein JSON"),
        _ok(text='["rueckblick"]'),
        _ok(text='{"anders": "Text"}'),
        _ok(text='{"rueckblick": 5}'),
        _ok(text='{"rueckblick": "  "}'),
        _ok(text='{"rueckblick": NaN}'),
        _Response(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps({"rueckblick": REVIEW}), "thought": True}]}}]}),
    ],
    ids=["bad-json", "no-utf8", "list", "deep", "no-candidates", "candidate-no-object", "blocked", "no-content",
         "parts-no-list", "max-tokens", "safety", "no-reason", "blank", "text-no-json", "text-list", "other-field",
         "no-string", "blank-string", "nan", "only-thoughts"],
)
def test_an_answer_without_a_usable_text_is_none_and_ends_the_pass(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, reply: _Response
) -> None:
    calls = _fake(monkeypatch, [reply, _ok()])
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY) is None
    assert len(calls) == 1
    assert len(caplog.records) == 1 and caplog.records[0].levelno == logging.WARNING


def test_a_wrong_finish_reason_is_named_in_the_log(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _fake(monkeypatch, [_ok(reason="MAX_TOKENS")])
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY) is None
    assert caplog.records[0].getMessage() == "Jahresrückblick: gemini-3.8-flash brach ab (MAX_TOKENS)."


def test_the_error_text_of_google_is_capped_and_cleaned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    leaked = "AIza" + "D" * 35
    message = f"Bad key {leaked} <b>fett</b>\nzweite Zeile " + "x" * 500
    _fake(monkeypatch, [_Response(403, {"error": {"message": message}})])
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY) is None
    logged = caplog.records[0].getMessage()
    assert logged.startswith("Jahresrückblick: Gemini gemini-3.8-flash antwortet HTTP 403: Bad key [entfernt] fett zweite Zeile x")
    assert leaked not in logged and "\n" not in logged
    assert len(logged.split("HTTP 403: ", 1)[1]) <= 200
    # An error without a message still names the status.
    caplog.clear()
    _fake(monkeypatch, [_Response(403, None, raw=b"<html>Forbidden</html>")])
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY) is None
    assert caplog.records[0].getMessage() == "Jahresrückblick: Gemini gemini-3.8-flash antwortet HTTP 403: ohne Text"


# ---- what is never sent -----------------------------------------------------------------


def test_an_oversized_prompt_is_not_sent(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    calls = _fake(monkeypatch, [_ok()])
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("x" * (rc.MAX_REQUEST_CHARS + 1), key=KEY) is None
    assert calls == [] and "nicht gesendet" in caplog.text
    assert rc.ask_review("x" * rc.MAX_REQUEST_CHARS, key=KEY) is not None


@pytest.mark.parametrize("key", ["", "   "])
def test_without_a_key_nothing_is_sent(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    calls = _fake(monkeypatch, [_ok()])
    assert rc.ask_review("Prompt", key=key) is None and calls == []


def test_only_model_names_reach_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_Response(503, BUSY), _ok()])
    names = ["gemini-3.8-flash", "../v1/secret", "Gemini", "a b", "x" * 61, "", " gemini-3.6-flash "]
    answer = rc.ask_review("Prompt", key=KEY, models=names)
    assert answer is not None and _models(calls) == ["gemini-3.8-flash", "gemini-3.6-flash"]
    # No valid name at all: nothing is sent.
    calls = _fake(monkeypatch, [_ok()])
    assert rc.ask_review("Prompt", key=KEY, models=["../x", "ab"]) is None and calls == []
    assert rc.ask_review("Prompt", key=KEY, models=[]) is None and calls == []


# ---- never an exception ----------------------------------------------------------------


@pytest.mark.parametrize("error", [RuntimeError("unerwartet"), KeyError("x"), MemoryError(), OSError("Platte")])
def test_ask_review_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    calls = _fake(monkeypatch, [error, _ok()])
    with caplog.at_level(logging.WARNING):
        assert rc.ask_review("Prompt", key=KEY) is None
    assert len(calls) == 1
    assert caplog.records[-1].getMessage() == f"Jahresrückblick: Anfrage abgebrochen ({type(error).__name__})."


def test_a_session_that_cannot_be_built_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> requests.Session:
        raise RuntimeError("kein Adapter")

    monkeypatch.setattr(rc, "session_with_retries", broken)
    assert rc.ask_review("Prompt", key=KEY) is None


# ---- the chain from the environment --------------------------------------------------------


def test_the_chain_comes_from_the_environment_when_it_names_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAHRESRUECKBLICK_MODEL", raising=False)
    assert rc.models_from_env() == rc.DEFAULT_MODELS
    monkeypatch.setenv("JAHRESRUECKBLICK_MODEL", "   ")
    assert rc.models_from_env() == rc.DEFAULT_MODELS
    monkeypatch.setenv("JAHRESRUECKBLICK_MODEL", " gemini-3.5-flash-lite , gemini-3.1-flash-lite,")
    assert rc.models_from_env() == ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
    calls = _fake(monkeypatch, [_ok()])
    answer = rc.ask_review("Prompt", key=KEY)
    assert answer is not None and answer.model == "gemini-3.5-flash-lite" and len(calls) == 1
    # An explicit chain wins over the environment.
    calls = _fake(monkeypatch, [_ok()])
    answer = rc.ask_review("Prompt", key=KEY, models=("gemini-3.6-flash",))
    assert answer is not None and answer.model == "gemini-3.6-flash" and len(calls) == 1


@pytest.mark.parametrize(
    "value",
    ["Gemini-3.8-Flash", "gemini-3.8-flash;id", "gemini-3.8-flash,a b", "ab", "g" * 61, "gemini-3.8-flash,../x", ",,"],
)
def test_a_value_that_is_no_list_of_model_names_is_discarded_whole(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    """The same names the gate of ki-waechter.yml and ``ask_gemini`` take: 3 to 60 of ``a-z 0-9 . -``."""
    monkeypatch.setenv("JAHRESRUECKBLICK_MODEL", value)
    with caplog.at_level(logging.WARNING):
        assert rc.models_from_env() == rc.DEFAULT_MODELS
    if value.strip(","):
        assert "JAHRESRUECKBLICK_MODEL nennt keinen gültigen Modellnamen" in caplog.text
        assert value not in caplog.text
