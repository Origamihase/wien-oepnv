"""Die zwei Hälften der Eingabe (Michael 2026-11-07, „ok“ zum Vorschlag vom 07.11. 00:24 Wiener Zeit).

Bis 2026-11-07 lasen beide Anfragen eines Prüflaufs die ganze Eingabe; am
06.11.2026 (Lauf 320, 102 Texte) übersahen beide Antworten dieselben fünf
Fehler. Seither liest jede Anfrage eine Hälfte des Tages: ``halves`` schneidet
die Eingabe zwischen zwei Fassungen, ``render_input`` gibt jede Hälfte für sich
(mit allen Quellen), ``ask_parts`` schickt die zweite Hälfte zuerst an das
Modell, das die erste las, und ``unite_answers`` vereinigt beide Antworten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import ki_waechter as kw
from src.ki_waechter.answers import Answer, ask_parts, describe_parts
from src.ki_waechter.inputs import FeedItem, Part, Prepared, Version, halves, render_input, together

from ._support import _Response, _history, _ok_payload, _overloaded_then

CHAIN = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
BUSY = {"error": {"code": 503, "message": "The model is overloaded. Please try again later.", "status": "UNAVAILABLE"}}


def _version(nr: int, hour: int) -> Version:
    return Version(nr, f"c{nr}", f"2026-11-05T{hour - 1:02d}:01:00+00:00", f"Thu, 05 Nov 2026 {hour:02d}:01:07 +0100", 6, 6)


def _item(nr: int, versions: list[int], sources: list[str] | None = None) -> FeedItem:
    return FeedItem(nr, f"g{nr}", f"{nr}A: Titel {nr}", f"Text {nr}.", f"{nr}A: Title {nr}", f"Text {nr}.",
                    versions=versions, own_sources=sources or [])


def _prep(items: list[FeedItem], count: int = 6) -> Prepared:
    sources = {"Q1": {"datei": "cache/wl_x/events.json", "titel": "1A: Titel 1", "text": "Quelle eins", "beginn": "", "ende": ""}}
    return Prepared("c0ffee", 24.0, [_version(nr, 14 + nr) for nr in range(1, count + 1)], {}, sources, items)


SIX = [_item(1, [1, 2, 3], ["Q1"]), _item(2, [1, 2, 3, 4, 5, 6], ["Q1"]), _item(3, [2]),
       _item(4, [4, 5]), _item(5, [5, 6]), _item(6, [6])]


def test_the_cut_leaves_the_larger_half_the_smallest() -> None:
    first, second = halves(_prep(SIX))
    # Cut after version 3: three texts before it, four from it on (text 2 stands in both halves).
    assert (first.first_version, first.last_version, first.items) == (1, 3, [1, 2, 3])
    assert (second.first_version, second.last_version, second.items) == (4, 6, [2, 4, 5, 6])
    assert (first.nr, first.count, second.nr, second.count) == (1, 2, 2, 2)
    # Among equal halves the cut nearest the middle.
    parts = halves(_prep([_item(1, [1]), _item(2, [1]), _item(3, [2]), _item(4, [6]), _item(5, [6]), _item(6, [6])]))
    assert [(p.first_version, p.last_version) for p in parts] == [(1, 3), (4, 6)]
    # One version, or no texts: no halves (the audit asks twice for the whole input, as before).
    assert halves(_prep([_item(1, [1])], count=1)) == []
    assert halves(_prep([])) == []


def test_each_half_is_rendered_on_its_own_with_the_days_numbers() -> None:
    prep = _prep(SIX)
    first, second = halves(prep)
    one, two = render_input(prep, first), render_input(prep, second)
    assert one.startswith("# Eingabe KI-Wächter (Commit c0ffee, Teil 1 von 2)\n")
    assert two.startswith("# Eingabe KI-Wächter (Commit c0ffee, Teil 2 von 2)\n")
    assert ("Dies ist Teil 1 von 2 des Tages: die Fassungen 1 bis 3, gebaut 05.11.2026 15:01 bis 05.11.2026 17:01 "
            "Wiener Zeit. Die Nummern der Meldungen gelten für den ganzen Tag (6 Meldungstexte); Nummern, die hier "
            "fehlen, stehen im anderen Teil.") in one
    assert "die Fassungen 4 bis 6, gebaut 05.11.2026 18:01 bis 05.11.2026 20:01 Wiener Zeit" in two
    # The whole day's versions stay in the first line; the texts of this half are counted.
    assert "6 Fassungen des Feeds, gebaut 05.11.2026 15:01 bis 05.11.2026 20:01 Wiener Zeit. Darin 3 verschiedene" in one
    assert "Darin 4 verschiedene Meldungstexte von 4 Meldungen." in two
    assert [f"## Meldung {nr}" in one for nr in range(1, 7)] == [True, True, True, False, False, False]
    assert [f"## Meldung {nr}" in two for nr in range(1, 7)] == [False, True, False, True, True, True]
    # "Gleichzeitig im Feed" names only texts of the same half, inside its versions.
    assert "Gleichzeitig im Feed: 1 (1A), 3 (3A)." in one and "Gleichzeitig im Feed: 4 (4A), 5 (5A), 6 (6A)." in two
    assert together(prep, prep.items[1]) == [1, 3, 4, 5, 6]
    # Every source stands in full once per half: the second half cannot look "oben" into the first.
    assert one.count("Q1 [cache/wl_x/events.json]") == 1 and "Quelle (eigenes Ereignis) Q1: siehe oben (1A: Titel 1)" in one
    assert two.count("Q1 [cache/wl_x/events.json]") == 1 and "siehe oben" not in two
    # Without a part the input is the whole day, as before.
    whole = render_input(prep)
    assert whole.startswith("# Eingabe KI-Wächter (Commit c0ffee)\n") and "Teil" not in whole.splitlines()[2]
    assert all(f"## Meldung {nr}" in whole for nr in range(1, 7))


def _finding(nr: int, klasse: str = "F6") -> dict[str, Any]:
    return {"nr": nr, "klasse": klasse, "feed": "EN", "feed_zitat": "x", "quelle_zitat": "", "feed_zitat2": "",
            "erwartet": "y", "begruendung": "z"}


def _text(*funde: dict[str, Any], geprueft: list[int]) -> str:
    return json.dumps({"geprueft": geprueft, "funde": list(funde), "vermutungen": []})


def _ok(model: str, text: str, seconds: float, requests: int = 1, **per_model: int) -> Answer:
    return Answer(True, model, text=text, tokens={"prompt": 100, "answer": 10}, seconds=seconds, requests=requests,
                  per_model=per_model or {model: requests}, answers=0)


class _Chain:
    """A fake ``ask_gemini``: one prepared answer per call, every call recorded."""

    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, prompt: str, model: str, key: str, sleep: Any, max_rounds: int, **kwargs: Any) -> Answer:
        self.calls.append((prompt, model, {"max_rounds": max_rounds, **kwargs}))
        return self.answers.pop(0)


def test_ask_parts_sends_each_half_once_and_unites_the_answers() -> None:
    chain = _Chain(
        _ok("gemini-3.6-flash", _text(_finding(2), geprueft=[1, 2, 3]), 100.0, 2, **{"gemini-3.8-flash": 1, "gemini-3.6-flash": 1}),
        _ok("gemini-3.6-flash", _text(_finding(2), _finding(5), geprueft=[2, 4, 5, 6]), 80.5),
    )
    united = ask_parts(["EINS", "ZWEI"], CHAIN, "k", chain, sleep=lambda _s: None, max_rounds=2, deadline_s=720.0)
    # The first half along the chain; the second first to the model that answered, then the rest of the chain,
    # with the seconds the first half left of the deadline.
    assert [(prompt, model) for prompt, model, _ in chain.calls] == [
        ("EINS", CHAIN), ("ZWEI", "gemini-3.6-flash,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite")]
    assert [opts for _, _, opts in chain.calls] == [{"max_rounds": 2, "deadline_s": 720.0}, {"max_rounds": 2, "deadline_s": 620.0}]
    assert united.ok and united.answers == 2 and united.model == "gemini-3.6-flash"
    assert (united.requests, united.per_model) == (3, {"gemini-3.8-flash": 1, "gemini-3.6-flash": 2})
    assert united.seconds == 180.5 and united.tokens == {"prompt": 200, "answer": 20}
    parsed = json.loads(united.text)
    assert parsed["geprueft"] == [1, 2, 3, 4, 5, 6] and [f["nr"] for f in parsed["funde"]] == [2, 5]
    assert united.parts == [
        {"teil": 1, "model": "gemini-3.6-flash", "ok": True, "requests": 2, "seconds": 100.0, "error": ""},
        {"teil": 2, "model": "gemini-3.6-flash", "ok": True, "requests": 1, "seconds": 80.5, "error": ""},
    ]


def test_ask_parts_without_the_second_answer_keeps_the_first_and_notes_the_half() -> None:
    busy = Answer(False, "gemini-3.1-flash-lite", error="Gemini 503: The model is overloaded.", requests=4,
                  per_model={"gemini-3.6-flash": 2, "gemini-3.1-flash-lite": 2}, seconds=300.0, overloaded=True,
                  attempts=["gemini-3.6-flash nach 3 s: 503"])
    first_text = _text(_finding(1), geprueft=[1, 2, 3])
    chain = _Chain(_ok("gemini-3.6-flash", first_text, 50.0), busy)
    united = ask_parts(["EINS", "ZWEI"], CHAIN, "k", chain, sleep=lambda _s: None)
    assert united.ok and united.answers == 1 and united.text == first_text
    assert (united.requests, united.seconds, united.attempts) == (5, 350.0, ["gemini-3.6-flash nach 3 s: 503"])
    assert united.parts[0]["ok"] is True
    assert united.parts[1] == {"teil": 2, "model": "", "ok": False, "requests": 4, "seconds": 300.0,
                               "error": "Gemini 503: The model is overloaded."}
    # No answer to the first half: the audit has none, the second half is not asked for.
    chain = _Chain(Answer(False, "gemini-3.1-flash-lite", error="HTTP 400", requests=1))
    failed = ask_parts(["EINS", "ZWEI"], CHAIN, "k", chain, sleep=lambda _s: None)
    assert not failed.ok and failed.answers == 0 and len(chain.calls) == 1
    assert failed.parts == [{"teil": 1, "model": "", "ok": False, "requests": 1, "seconds": 0.0, "error": "HTTP 400"}]
    # A single prompt (no halves): one request, one note.
    chain = _Chain(_ok("gemini-3.6-flash", first_text, 50.0))
    alone = ask_parts(["EINS"], CHAIN, "k", chain, sleep=lambda _s: None)
    assert alone.ok and alone.answers == 1 and len(chain.calls) == 1 and len(alone.parts) == 1


def test_an_answer_counts_only_the_texts_of_its_own_half() -> None:
    """A half shows the day's numbers with gaps; a model that lists them "lückenlos" names texts it never saw."""
    numbers = [[1, 2, 3], [3, 4, 5, 6]]
    both = _Chain(
        _ok("gemini-3.6-flash", _text(_finding(2), geprueft=[1, 2, 3, 4, 5, 6]), 10.0),
        _ok("gemini-3.6-flash", _text(_finding(5), geprueft=[1, 2, 3, 4, 6]), 10.0),
    )
    united = ask_parts(["EINS", "ZWEI"], CHAIN, "k", both, sleep=lambda _s: None, numbers=numbers)
    parsed = json.loads(united.text)
    # Text 5 stood only in the second half, and its answer left it out: not checked, whatever the first answer listed.
    assert parsed["geprueft"] == [1, 2, 3, 4, 6] and [f["nr"] for f in parsed["funde"]] == [2, 5]
    # The second half without an answer: its texts are not checked, although the first answer listed them.
    busy = Answer(False, "gemini-3.1-flash-lite", error="Gemini 503", requests=2, overloaded=True)
    alone = _Chain(_ok("gemini-3.6-flash", _text(_finding(2), geprueft=[1, 2, 3, 4, 5, 6]), 10.0), busy)
    kept = ask_parts(["EINS", "ZWEI"], CHAIN, "k", alone, sleep=lambda _s: None, numbers=numbers)
    parsed = json.loads(kept.text)
    assert kept.ok and kept.answers == 1 and parsed["geprueft"] == [1, 2, 3] and [f["nr"] for f in parsed["funde"]] == [2]
    # An answer without JSON stays as it came; ``verify`` reports it.
    odd = _Chain(_ok("gemini-3.6-flash", "kein JSON", 10.0), busy)
    assert ask_parts(["EINS", "ZWEI"], CHAIN, "k", odd, sleep=lambda _s: None, numbers=numbers).text == "kein JSON"


def test_cmd_ask_names_the_texts_of_each_half(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _history(tmp_path)
    prep = kw.prepare(kw.git_snapshots(tmp_path, "HEAD", 24), "head", 24)
    work = tmp_path / "w"
    work.mkdir()
    (work / "eingabe.json").write_text(json.dumps(kw.asdict(prep)), encoding="utf-8")
    (work / "eingabe.md").write_text(render_input(prep), encoding="utf-8")
    seen: dict[str, Any] = {}

    def fake(prompts: list[str], model: str, key: str, ask: Any, **kwargs: Any) -> Answer:
        seen.update(kwargs, prompts=len(prompts))
        return Answer(True, "gemini-3.6-flash", text=_text(geprueft=[1]))

    monkeypatch.setattr(kw, "ask_parts", fake)
    monkeypatch.setenv(kw.GEMINI_KEY_ENV, "k")
    assert kw.main(["ask", "--dir", str(work), "--model", CHAIN]) == 0
    assert seen["prompts"] == 2 and seen["numbers"] == [part.items for part in halves(prep)]


def test_ask_parts_sends_no_second_request_after_the_deadline(capsys: pytest.CaptureFixture[str]) -> None:
    chain = _Chain(_ok("gemini-3.6-flash", _text(geprueft=[1]), 721.0))
    late = ask_parts(["EINS", "ZWEI"], CHAIN, "k", chain, sleep=lambda _s: None, deadline_s=720.0)
    assert late.ok and late.answers == 1 and len(chain.calls) == 1
    assert late.parts[1] == {"teil": 2, "model": "", "ok": False, "requests": 0, "seconds": 0.0,
                             "error": "keine Anfrage (erste Antwort nach 721 s)"}
    assert "keine Anfrage für Teil 2" in capsys.readouterr().out
    # The worst case stays inside the step limit of 25 minutes: the last request of either half starts
    # within NO_NEW_REQUEST_AFTER_S of the first and may take connect and read timeout.
    connect, read = kw.GEMINI_TIMEOUT
    assert kw.NO_NEW_REQUEST_AFTER_S + connect + read + 60 <= 25 * 60


def test_ask_gemini_starts_no_further_request_past_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _text(geprueft=[1])
    calls = _overloaded_then(monkeypatch, [_Response(503, BUSY), _Response(200, _ok_payload(text))])
    spent = kw.ask_gemini("p", "gemini-a,gemini-b", "k", sleep=lambda _s: None, max_rounds=2, deadline_s=0.0)
    assert not spent.ok and spent.overloaded and len(calls) == 1
    calls = _overloaded_then(monkeypatch, [_Response(503, BUSY), _Response(200, _ok_payload(text))])
    fresh = kw.ask_gemini("p", "gemini-a,gemini-b", "k", sleep=lambda _s: None, max_rounds=2)
    assert fresh.ok and fresh.model == "gemini-b" and len(calls) == 2


def test_describe_parts_names_each_half_and_its_model() -> None:
    parts = [Part(1, 2, 1, 20, list(range(1, 57))), Part(2, 2, 21, 52, [6, 8, 10, *range(57, 103)])]
    notes: list[dict[str, Any]] = [
        {"teil": 1, "model": "gemini-3.6-flash", "ok": True, "requests": 1, "seconds": 90.0, "error": ""},
        {"teil": 2, "model": "gemini-3.8-flash", "ok": True, "requests": 2, "seconds": 70.0, "error": ""},
    ]
    assert describe_parts(notes, parts) == (", zwei Antworten, je eine Hälfte der Fassungen "
                                            "(1–20: 56 Texte, gemini-3.6-flash; 21–52: 49 Texte, gemini-3.8-flash)")
    notes[1] = {"teil": 2, "model": "", "ok": False, "requests": 4, "seconds": 300.0, "error": "Gemini 503: busy"}
    assert describe_parts(notes, parts) == (", eine Antwort, je eine Hälfte der Fassungen "
                                            "(1–20: 56 Texte, gemini-3.6-flash; 21–52: 49 Texte, ohne Antwort (Gemini 503: busy))")
    one = [Part(1, 2, 1, 1, [1]), Part(2, 2, 2, 3, [2, 3])]
    assert describe_parts(notes, one) == (", eine Antwort, je eine Hälfte der Fassungen "
                                          "(1: 1 Text, gemini-3.6-flash; 2–3: 2 Texte, ohne Antwort (Gemini 503: busy))")
    assert describe_parts([], parts) == ""
    assert describe_parts([{"teil": 3, "ok": False}], parts) == (
        ", eine Antwort, je eine Hälfte der Fassungen (Teil 3, ohne Antwort (keine Anfrage))")


def test_prepare_writes_each_half_and_ask_and_verify_read_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _history(tmp_path)
    work = tmp_path / "w"
    assert kw.main(["prepare", "--root", str(tmp_path), "--rev", "HEAD", "--hours", "24", "--out", str(work)]) == 0
    # Three versions, texts 1 (version 1), 2 (versions 2 and 3) and 3 (version 2): the cut after version 1.
    assert "Teil 1 von 2 des Tages: die Fassungen 1 bis 1" in (work / "eingabe-teil1.md").read_text(encoding="utf-8")
    two = (work / "eingabe-teil2.md").read_text(encoding="utf-8")
    assert "Teil 2 von 2 des Tages: die Fassungen 2 bis 3" in two and "## Meldung 1" not in two and "## Meldung 3" in two
    assert "Teil" not in (work / "eingabe.md").read_text(encoding="utf-8").splitlines()[0]
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    answers = (_text(geprueft=[1]), _text(_finding(3, "F5"), geprueft=[2, 3]))
    calls = _overloaded_then(monkeypatch, [_Response(200, _ok_payload(text)) for text in answers])
    assert kw.main(["ask", "--dir", str(work), "--model", "gemini-3.1-flash-lite"]) == 0
    assert len(calls) == 2
    written = json.loads((work / "antwort.json").read_text(encoding="utf-8"))
    assert written["ok"] is True and written["answers"] == 2 and written["requests"] == 2
    assert [note["teil"] for note in written["parts"]] == [1, 2] and all(note["ok"] for note in written["parts"])
    assert json.loads(written["text"])["geprueft"] == [1, 2, 3]
    out = capsys.readouterr().out
    assert "KI-Wächter: zwei Hälften, 1 und 2 Texte" in out and "2 Anfrage(n), 2 Antwort(en) je eine Hälfte" in out
    assert kw.main(["verify", "--dir", str(work)]) == 0
    report = (work / "bericht.md").read_text(encoding="utf-8")
    assert ("Modell gemini-3.1-flash-lite, zwei Antworten, je eine Hälfte der Fassungen "
            "(1: 1 Text, gemini-3.1-flash-lite; 2–3: 2 Texte, gemini-3.1-flash-lite)") in report
    assert json.loads((work / "ergebnis.json").read_text(encoding="utf-8"))["antworten"] == 2


def test_ask_without_halves_asks_twice_for_the_whole_input(
    prep: Prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A working-tree input has one version: no halves, the second request reads the whole input again."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    work = tmp_path / "w"
    work.mkdir()
    (work / "eingabe.md").write_text(render_input(prep), encoding="utf-8")
    (work / "eingabe.json").write_text(json.dumps(kw.asdict(prep)), encoding="utf-8")
    assert halves(prep) == [] and kw._halves_of(work) == []
    _overloaded_then(monkeypatch, [_Response(200, _ok_payload(_text(geprueft=[1, 2, 3]))) for _ in range(2)])
    assert kw.main(["ask", "--dir", str(work), "--model", "gemini-3.1-flash-lite"]) == 0
    written = json.loads((work / "antwort.json").read_text(encoding="utf-8"))
    assert written["answers"] == 2 and written["parts"] == []
    assert "zwei Hälften" not in capsys.readouterr().out
    # Without eingabe.json the ask step still runs on the whole input.
    (work / "eingabe.json").unlink()
    assert kw._halves_of(work) == []
