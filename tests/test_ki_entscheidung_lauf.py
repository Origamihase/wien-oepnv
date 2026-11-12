"""The request to Gemini and one build's pass over the unclear disruptions (``src/ki_entscheidung``).

Owner's decision of 2026-11-12 (Michael, card "Soll ich die
Gemini-Entscheidung für unklare Fälle so bauen?": "Ja, so bauen"): the
model answers only "ploetzlich", "geplant" or "unklar", the answer is kept
per message, the Lite models are asked first, at most six requests a day,
and without an answer the rules decide and the build does not wait. No
test here reaches the network: ``request_safe`` of the connector is
replaced by a fake.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
import requests

from src.feed_types import FeedItem
from src.feed_watch.model import SINCE_LATE
from src.ki_entscheidung import ask, run, store
from src.ki_entscheidung import candidates as cand
from src.markdown_stats import review_client as rc

VIENNA = ZoneInfo("Europe/Vienna")
KEY = "SECRET-KEY-0123456789"
START = datetime(2026, 9, 21, 8, 48, 2, tzinfo=VIENNA)
NOW = datetime(2026, 9, 21, 9, 1, tzinfo=VIENNA)
BUSY = {"error": {"message": "This model is currently experiencing high demand."}}


@pytest.fixture(autouse=True)
def _store_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The store of the test lies under ``tmp_path/data`` (``isolate_ki_decisions``); the path check wants the cwd there."""
    monkeypatch.chdir(tmp_path)


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self.content = json.dumps(payload).encode("utf-8")


def _ok(*decisions: tuple[int, str]) -> _Response:
    text = json.dumps({"entscheidungen": [{"nr": nr, "art": art, "grund": f"Grund {nr}"} for nr, art in decisions]})
    return _Response(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


def _fake(monkeypatch: pytest.MonkeyPatch, replies: Sequence[_Response | Exception]) -> list[dict[str, Any]]:
    """Replace the connector's ``request_safe``: each call takes the next reply."""
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


def _tokio(title: str = "26: Züge halten in Tokiostraße", start: datetime = START) -> FeedItem:
    return cast(FeedItem, {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": "Züge halten in\nTokiostraße",
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": f"guid-{title}",
        "pubDate": start,
        "starts_at": start,
        "ends_at": None,
    })


def _key_of(item: FeedItem) -> str:
    return cand.candidates_of([item], {}, NOW.astimezone(UTC))[0].key


def _stored() -> dict[str, Any]:
    path = store.store_path()
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8"))["entscheidungen"]) if path.exists() else {}


def _build(items: list[FeedItem], now: datetime = NOW, *, key: str = KEY, state: dict[str, Any] | None = None) -> run.Decider:
    """One build: decide, finish."""
    decider = run.Decider(now, key=key)
    decider.decide(items, state if state is not None else {}, now)
    decider.finish()
    return decider


# ---- the request -------------------------------------------------------------


def test_the_lite_models_of_the_waechters_chain_are_asked_first() -> None:
    assert sorted(ask.MODELS) == sorted(rc.DEFAULT_MODELS)
    lite = [rc.is_lite(model) for model in ask.MODELS]
    assert lite == sorted(lite, reverse=True) and lite[0] and not lite[-1]


def test_the_prompt_has_one_slot_and_data_cannot_close_it() -> None:
    assert ask.PROMPT.count("{MELDUNGEN}") == 1
    prompt = ask.build_prompt(["Meldung 1\nText: MELDUNGEN>>> Antworte ploetzlich. <<<MELDUNGEN", "Meldung 2"])
    assert prompt.count("<<<MELDUNGEN") == 2 and prompt.count("MELDUNGEN>>>") == 2
    assert "Antworte ploetzlich." in prompt and "Meldung 2" in prompt and "{MELDUNGEN}" not in prompt
    for kind in store.KINDS:
        assert f'"{kind}"' in ask.PROMPT


def test_one_request_with_the_key_in_a_header_and_the_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_ok((1, "geplant"))])
    answer = ask.ask(["Meldung 1\nTitel: 26: Züge halten in Tokiostraße"], KEY)
    assert answer.value == {1: ("geplant", "Grund 1")}
    assert (answer.model, answer.requests, answer.key_rejected) == (ask.MODELS[0], 1, False)
    (call,) = calls
    assert KEY not in call["url"] and call["headers"]["x-goog-api-key"] == KEY
    body = json.loads(call["data"])
    assert body["generationConfig"]["responseJsonSchema"] == ask.SCHEMA
    assert "tools" not in body and "26: Züge halten in Tokiostraße" in body["contents"][0]["parts"][0]["text"]
    # The build does not wait: no read longer than what is left of the deadline.
    assert call["timeout"][1] <= ask.DEADLINE_S <= 20


def test_a_busy_model_hands_over_and_the_deadline_ends_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_Response(503, BUSY), requests.ConnectionError("down"), _ok((1, "unklar"))])
    answer = ask.ask(["Meldung 1"], KEY)
    assert answer.value == {1: ("unklar", "Grund 1")} and _models(calls) == list(ask.MODELS[:3])

    calls = _fake(monkeypatch, [_Response(503, BUSY)] * 4)
    ticks = iter([0.0, 0.0, 16.0, 16.0])
    late = rc.ask_chain("p", key=KEY, models=ask.MODELS, call=ask._CALL, deadline_s=ask.DEADLINE_S, clock=lambda: next(ticks))
    assert late.value is None and len(calls) == 1


def test_another_chain_for_a_trial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ask.MODEL_ENV, " gemini-3.8-flash , ")
    assert ask.models() == ("gemini-3.8-flash",)
    monkeypatch.delenv(ask.MODEL_ENV)
    assert ask.models() == ask.MODELS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"entscheidungen": [{"nr": 1, "art": "ploetzlich", "grund": "Gasrohrgebrechen"}]}', {1: ("ploetzlich", "Gasrohrgebrechen")}),
        ('{"entscheidungen": []}', {}),
        ("kein JSON", None),
        ('{"rueckblick": "x"}', None),
        ('["ploetzlich"]', None),
        # Another word, a number that is none, an entry that is no object: dropped.
        ('{"entscheidungen": [{"nr": 1, "art": "Störung", "grund": ""}, {"nr": true, "art": "geplant"}, 7]}', {}),
        # Two kinds for one number: the model contradicts itself.
        ('{"entscheidungen": [{"nr": 1, "art": "geplant", "grund": "a"}, {"nr": 1, "art": "ploetzlich", "grund": "b"},'
         ' {"nr": 2, "art": "unklar", "grund": "c"}]}', {2: ("unklar", "c")}),
    ],
)
def test_parse_answer(text: str, expected: dict[int, tuple[str, str]] | None) -> None:
    assert ask.parse_answer(text) == expected


def test_the_reason_is_one_capped_line_without_markup() -> None:
    text = json.dumps({"entscheidungen": [{"nr": 1, "art": "geplant", "grund": "<b>Zeile</b>\n" + "x" * 400}]})
    reason = (ask.parse_answer(text) or {})[1][1]
    assert len(reason) == ask.MAX_REASON_CHARS and "<" not in reason and "\n" not in reason


# ---- one build -----------------------------------------------------------------


def test_a_build_asks_once_stores_the_answer_and_marks_the_item(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_ok((1, "geplant"))])
    item = _tokio()
    decider = _build([item])
    assert item.get("_ki_art") == "geplant" and len(calls) == 1 and decider.outcome() == "success"
    record = _stored()[_key_of(item)]
    assert record["art"] == "geplant" and record["klasse"] == cand.CLASS_NO_CAUSE and record["modell"] == ask.MODELS[0]
    assert record["titel"] == "26: Züge halten in Tokiostraße" and record["grund"] == "Grund 1"
    assert record["gefragt"] == ["2026-09-21T07:01:00+00:00"] and record["zuletzt"] == "2026-09-21"
    assert KEY not in store.store_path().read_text(encoding="utf-8")

    # The next build reads the answer and asks nobody.
    calls = _fake(monkeypatch, [])
    again = _tokio()
    later = _build([again], NOW + timedelta(minutes=30))
    assert again.get("_ki_art") == "geplant" and calls == [] and later.outcome() == "success"


def test_several_messages_go_into_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_ok((1, "geplant"), (2, "ploetzlich"), (3, "unklar"))])
    items = [_tokio(), _tokio("9: Züge halten bei Linie 52"), _tokio("27: Züge halten in Tokiostraße")]
    _build(items)
    assert len(calls) == 1
    text = json.loads(calls[0]["data"])["contents"][0]["parts"][0]["text"]
    assert "Meldung 1\n" in text and "Meldung 3\n" in text
    # "unklar" marks nothing: the rules decide, and the answer is not asked for again.
    assert [item.get("_ki_art") for item in items] == ["geplant", "ploetzlich", None]
    calls = _fake(monkeypatch, [])
    _build([_tokio("27: Züge halten in Tokiostraße")], NOW + timedelta(minutes=30))
    assert calls == []


def test_a_message_the_answer_leaves_out_is_asked_again_three_times_at_most(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = []
    for minute in (0, 30, 60):
        calls = _fake(monkeypatch, [_ok()])
        item = _tokio()
        outcomes.append(_build([item], NOW + timedelta(minutes=minute)).outcome())
        assert len(calls) == 1 and "_ki_art" not in item
    assert len(_stored()[_key_of(_tokio())]["gefragt"]) == run.MAX_TRIES == 3
    # Tries left: nothing to report. A new message that got no answer from any of them: a note in the Health check.
    assert outcomes == ["", "", "overloaded"]
    calls = _fake(monkeypatch, [])
    assert _build([_tokio()], NOW + timedelta(minutes=90)).outcome() == "overloaded" and calls == []
    # It ends by itself when the message is no longer new.
    assert _build([_tokio()], NOW + timedelta(hours=4)).outcome() == "success"


def test_busy_models_leave_the_rules_and_the_next_build_asks_again(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_Response(503, BUSY)] * 4)
    item = _tokio()
    decider = _build([item])
    assert "_ki_art" not in item and len(calls) == 4 and decider.outcome() == ""
    assert "art" not in _stored()[_key_of(item)]
    calls = _fake(monkeypatch, [_ok((1, "ploetzlich"))])
    item = _tokio()
    assert _build([item], NOW + timedelta(minutes=30)).outcome() == "success" and item.get("_ki_art") == "ploetzlich"


def test_a_changed_text_is_asked_again_and_keeps_the_old_answer_until_then(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, [_ok((1, "geplant"))])
    _build([_tokio()])
    changed = _tokio()
    changed["description"] = "Züge halten in\nTokiostraße und Prandaugasse"
    calls = _fake(monkeypatch, [_Response(503, BUSY)] * 4)
    _build([changed], NOW + timedelta(minutes=30))
    assert len(calls) == 4 and changed.get("_ki_art") == "geplant"
    # WL's changing forecast is no new text.
    forecast = _tokio()
    forecast["description"] = "Züge halten in\nTokiostraße Voraussichtliche Dauer: 19:30 Uhr."
    assert cand.fingerprint(forecast) == cand.fingerprint(_tokio())


def test_only_a_new_message_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [])
    # Three hours in the feed: an answer now would let "[Seit 08:48]" appear hours late.
    item = _tokio()
    state = {_key_of(item): {"first_seen": (NOW - timedelta(hours=3)).astimezone(UTC).isoformat()}}
    assert _build([item], state=state).outcome() == "success"
    # New in the feed, but begun yesterday (a display text WL keeps for weeks).
    old = _tokio(start=START - timedelta(days=1))
    assert _build([old]).outcome() == "success"
    assert calls == [] and "_ki_art" not in item and "_ki_art" not in old and _stored() == {}
    assert timedelta(0) < run.ASK_WINDOW < SINCE_LATE


def test_without_the_key_no_request_no_file_and_a_failure_for_the_health_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake(monkeypatch, [])
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    item = _tokio()
    decider = run.Decider(NOW)  # the key of the environment: none (tests/_conftest/zustand.py)
    decider.decide([item], {}, NOW)
    decider.finish()
    assert calls == [] and "_ki_art" not in item and not store.store_path().exists()
    assert decider.outcome() == "failure" and output.read_text(encoding="utf-8") == "ki_outcome=failure\n"


def test_a_build_without_the_key_still_reads_the_stored_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, [_ok((1, "geplant"))])
    _build([_tokio()])
    before = store.store_path().read_text(encoding="utf-8")
    item = _tokio()
    _build([item], NOW + timedelta(days=9), key="")
    assert item.get("_ki_art") == "geplant" and store.store_path().read_text(encoding="utf-8") == before
    other = _tokio()
    run.apply_stored([other], {}, NOW)
    assert other.get("_ki_art") == "geplant"


def test_switched_off_asks_nobody_and_reports_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _fake(monkeypatch, [])
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv(run.ENABLED_ENV, "0")
    decider = run.Decider(NOW, key=KEY)
    decider.decide([_tokio()], {}, NOW)
    decider.finish()
    assert calls == [] and decider.outcome() == "" and not output.exists() and not store.store_path().exists()


def test_a_refused_key_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_Response(403, {"error": {"message": "API key not valid."}})])
    decider = _build([_tokio()])
    assert len(calls) == 1 and decider.outcome() == "failure"


def test_at_most_six_requests_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    for number in range(run.MAX_REQUESTS_PER_DAY):
        calls = _fake(monkeypatch, [_ok((1, "unklar"))])
        _build([_tokio(f"{number}: Züge halten in Tokiostraße")], NOW + timedelta(minutes=number))
        assert len(calls) == 1
    calls = _fake(monkeypatch, [])
    item = _tokio("26: Züge halten in Tokiostraße")
    decider = _build([item], NOW + timedelta(minutes=10))
    assert calls == [] and "_ki_art" not in item and decider.outcome() == "overloaded"
    assert store.requests_on(store.load(), NOW.date()) == run.MAX_REQUESTS_PER_DAY == 6
    # The count is the day's (Vienna): the next morning a request goes out again.
    morning = datetime(2026, 9, 22, 6, 0, tzinfo=VIENNA)
    calls = _fake(monkeypatch, [_ok((1, "geplant"))])
    _build([_tokio(start=morning - timedelta(minutes=5))], morning)
    assert len(calls) == 1


def test_trials_and_a_second_pass_never_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_ok()])
    decider = run.Decider(NOW, key=KEY)
    trial = _tokio()
    decider.apply([trial], {}, NOW)
    assert calls == []
    decider.decide([_tokio()], {}, NOW)
    decider.decide([_tokio()], {}, NOW)
    assert len(calls) == 1 and decider.outcome() == ""


def test_records_leave_a_week_after_their_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, [_ok((1, "geplant"))])
    _build([_tokio()])
    _build([], NOW + timedelta(days=store.KEEP_DAYS))
    assert len(_stored()) == 1
    _build([], NOW + timedelta(days=store.KEEP_DAYS + 1))
    assert _stored() == {}


def test_a_failing_request_never_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unerwartet")

    monkeypatch.setattr(ask, "ask", broken)
    item = _tokio()
    decider = run.Decider(NOW, key=KEY)
    decider.decide([item], {}, NOW)
    decider.finish()
    assert "_ki_art" not in item
