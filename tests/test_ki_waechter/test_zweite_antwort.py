# ruff: noqa: S603, S607
"""Die zweite Antwort je Prüflauf (Michael 2026-11-05, Karte „Wie soll der KI-Wächter weniger falsche Funde liefern?“, „Beides“).

Eine Anfrage bringt nicht jeden Fund (27.10.2026: drei Läufe mit zwei bis drei
Funden, zusammen sechs verschiedene). ``ask_twice`` fragt deshalb das Modell,
das geantwortet hat, einmal mehr und vereinigt die Funde beider Antworten
(``unite_answers``): höchstens eine Anfrage mehr, an dasselbe Modell, keine
nach einer ersten Antwort jenseits von ``NO_NEW_REQUEST_AFTER_S``; eine
verweigerte oder leere zweite Antwort lässt die erste gelten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import ki_waechter as kw

from ._support import _Response, _ok_payload, _overloaded_then

CHAIN = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
BUSY = {"error": {"code": 503, "message": "The model is overloaded. Please try again later.", "status": "UNAVAILABLE"}}
QUOTA = {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}


def _finding(nr: int, klasse: str = "F6", feed: str = "EN", quote: str = "Line closure") -> dict[str, Any]:
    return {"nr": nr, "klasse": klasse, "feed": feed, "feed_zitat": quote, "quelle_zitat": "",
            "feed_zitat2": "", "erwartet": "y", "begruendung": "z"}


def _text(*funde: dict[str, Any], geprueft: list[int], vermutungen: list[str] | None = None) -> str:
    return json.dumps({"geprueft": geprueft, "funde": list(funde), "vermutungen": vermutungen or []})


FIRST = _text(_finding(2), _finding(3, "F5", "DE", "DieHaltestelle"), geprueft=[1, 2, 3], vermutungen=["Meldung 4: x"])
SECOND = _text(_finding(2, "f6", "en", "Line  closure"), _finding(5), geprueft=[3, 4, 5], vermutungen=[" Meldung 4: x ", "Meldung 6: y"])


def test_the_model_that_answered_is_asked_once_more_and_the_findings_are_united(monkeypatch: pytest.MonkeyPatch) -> None:
    """3.8 Flash overloaded, 3.6 Flash answers: the second request goes to 3.6 Flash, nothing else."""
    _overloaded_then(monkeypatch, [_Response(200, _ok_payload("{}"))])
    single = kw.ask_gemini("Prompt", "gemini-3.6-flash", "k")
    calls = _overloaded_then(monkeypatch, [_Response(503, BUSY), _Response(200, _ok_payload(FIRST)),
                                           _Response(200, _ok_payload(SECOND))])
    answer = kw.ask_twice("Prompt", CHAIN, "k", sleep=lambda s: None)
    assert [url.split("/models/")[1].split(":")[0] for url in calls] == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.6-flash"]
    assert answer.ok and answer.model == "gemini-3.6-flash" and answer.answers == 2
    assert answer.requests == 3 and answer.per_model == {"gemini-3.8-flash": 1, "gemini-3.6-flash": 2}
    assert len(answer.attempts) == 1 and "gemini-3.8-flash" in answer.attempts[0] and not answer.overloaded
    united = kw.extract_answer(answer.text)
    # Every finding of either answer once (item, class and feed, up to case), the first answer's first.
    assert [(f["nr"], f["klasse"]) for f in united["funde"]] == [(2, "F6"), (3, "F5"), (5, "F6")]
    assert united["geprueft"] == [1, 2, 3, 4, 5]
    assert united["vermutungen"] == ["Meldung 4: x", "Meldung 6: y"]
    assert answer.tokens == {name: 2 * count for name, count in single.tokens.items()}


@pytest.mark.parametrize("second", [
    _Response(503, BUSY),
    _Response(429, QUOTA),
    _Response(200, _ok_payload('{"funde": [', reason="MAX_TOKENS")),
    _Response(200, _ok_payload("Ich habe nichts gefunden.")),
])
def test_a_refused_or_empty_second_answer_leaves_the_first(monkeypatch: pytest.MonkeyPatch, second: _Response) -> None:
    calls = _overloaded_then(monkeypatch, [_Response(200, _ok_payload(FIRST)), second])
    answer = kw.ask_twice("Prompt", CHAIN, "k", sleep=lambda s: None)
    # One request more at most: no third request, no pass over the chain for the second answer.
    assert len(calls) == 2 and answer.requests == 2 and answer.per_model == {"gemini-3.8-flash": 2}
    assert answer.ok and answer.answers == 1 and answer.text == FIRST and not answer.overloaded
    assert kw.extract_answer(answer.text) == json.loads(FIRST)


def test_no_second_request_after_a_slow_first_answer_or_without_a_first(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, dict[str, Any]]] = []
    reply = kw.Answer(True, "gemini-3.1-flash-lite", text=FIRST, requests=4, seconds=0.0,
                      per_model={"gemini-3.8-flash": 1, "gemini-3.6-flash": 1, "gemini-3.5-flash-lite": 1, "gemini-3.1-flash-lite": 1})

    def fake_ask(prompt: str, model: str, key: str, sleep: Any = None, max_rounds: int = kw.MAX_ROUNDS) -> kw.Answer:
        asked.append((model, {"max_rounds": max_rounds}))
        return kw.Answer(**{**kw.asdict(reply), "per_model": dict(reply.per_model)})

    monkeypatch.setattr(kw, "ask_gemini", fake_ask)
    # The first answer took longer than NO_NEW_REQUEST_AFTER_S: the step limit leaves no room for a second request.
    reply.seconds = kw.NO_NEW_REQUEST_AFTER_S + 0.1
    answer = kw.ask_twice("Prompt", CHAIN, "k")
    assert asked == [(CHAIN, {"max_rounds": kw.MAX_ROUNDS})] and answer.answers == 1 and answer.requests == 4
    # Up to the limit the second request follows, to the model that answered, in one pass (one request at most).
    reply.seconds = float(kw.NO_NEW_REQUEST_AFTER_S)
    asked.clear()
    answer = kw.ask_twice("Prompt", CHAIN, "k")
    assert asked == [(CHAIN, {"max_rounds": kw.MAX_ROUNDS}), ("gemini-3.1-flash-lite", {"max_rounds": 1})]
    assert answer.answers == 2 and answer.requests == 8 and answer.per_model["gemini-3.1-flash-lite"] == 2
    # One answer asked for: one request.
    asked.clear()
    assert kw.ask_twice("Prompt", CHAIN, "k", answers=1).answers == 1 and len(asked) == 1
    # No first answer: nothing to unite, the chain's result stands as it is.
    reply.ok, reply.error, reply.overloaded = False, "Gemini HTTP 503", True
    asked.clear()
    answer = kw.ask_twice("Prompt", CHAIN, "k")
    assert len(asked) == 1 and not answer.ok and answer.overloaded and answer.answers == 0


def test_the_second_request_keeps_the_last_request_inside_the_step_limit() -> None:
    """The worst case stays: a first answer at the limit plus one request of connect and read timeout."""
    connect, read = kw.GEMINI_TIMEOUT
    assert kw.ANSWERS_PER_AUDIT == 2
    worst_with_second = kw.NO_NEW_REQUEST_AFTER_S + connect + read
    assert worst_with_second + 60 <= 25 * 60


def test_union_keys_findings_like_verify_and_drops_junk() -> None:
    first = {"funde": [_finding(1, quote="a  b"), "kein dict", _finding(1, "F3", "DE", "q")],
             "geprueft": [2, "3", 1], "vermutungen": ["x", ""]}
    second = {"funde": [_finding(1, "f6", "en", "a b"), _finding(1, "F3", "DE", "q2")],
              "geprueft": [1, 4], "vermutungen": [" x", "y"]}
    united = kw._union(first, second)
    # The quote is no part of the key: the second answer's other quote of the item's F3 is the same finding
    # (the two answers quote one error at different lengths); the first answer's quote stays.
    assert [(f["klasse"], f["feed_zitat"]) for f in united["funde"]] == [("F6", "a  b"), ("F3", "q")]
    assert united["geprueft"] == [1, 2, 4] and united["vermutungen"] == ["x", "y"]
    assert kw._union({}, {}) == {"geprueft": [], "funde": [], "vermutungen": []}
    # Both answers wonder about the same item in other words: one hypothesis, the first answer's wording.
    both = kw._union({"vermutungen": ["Meldung 34: Keine Quelle im Cache."]},
                     {"vermutungen": ["Meldung 34: Keine Quelle für diese Störung.", "Meldung 7: x"]})
    assert both["vermutungen"] == ["Meldung 34: Keine Quelle im Cache.", "Meldung 7: x"]


def test_ask_writes_both_answers_and_verify_names_them(
    prep: kw.Prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    work = tmp_path / "w"
    work.mkdir()
    (work / "eingabe.md").write_text("## Meldung 1\nINHALT\n", encoding="utf-8")
    (work / "eingabe.json").write_text(json.dumps(kw.asdict(prep)), encoding="utf-8")
    both = ('{"geprueft": [1, 2], "funde": [], "vermutungen": []}', '{"geprueft": [2, 3], "funde": [], "vermutungen": []}')
    _overloaded_then(monkeypatch, [_Response(200, _ok_payload(text)) for text in both])
    assert kw.main(["ask", "--dir", str(work), "--model", "gemini-3.1-flash-lite"]) == 0
    written = json.loads((work / "antwort.json").read_text(encoding="utf-8"))
    assert written["ok"] is True and written["answers"] == 2 and written["requests"] == 2
    assert written["per_model"] == {"gemini-3.1-flash-lite": 2}
    out = capsys.readouterr().out
    assert "::notice title=Gemini-Anfragen::2\n" in out and "2 Anfrage(n), 2 Antwort(en)" in out
    assert kw.main(["verify", "--dir", str(work)]) == 0
    report = (work / "bericht.md").read_text(encoding="utf-8")
    assert "Modell gemini-3.1-flash-lite, zwei Antworten vereinigt" in report
    result = json.loads((work / "ergebnis.json").read_text(encoding="utf-8"))
    assert result["antworten"] == 2 and result["geprueft"] == 3
    # One answer: the report names the model alone.
    written["answers"] = 1
    (work / "antwort.json").write_text(json.dumps(written), encoding="utf-8")
    assert kw.main(["verify", "--dir", str(work)]) == 0
    assert "zwei Antworten" not in (work / "bericht.md").read_text(encoding="utf-8")
    assert json.loads((work / "ergebnis.json").read_text(encoding="utf-8"))["antworten"] == 1
