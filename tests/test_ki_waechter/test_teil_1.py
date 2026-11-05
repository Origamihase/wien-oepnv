# ruff: noqa: S603, S607
"""Teil 1 der Tests aus tests/test_ki_waechter.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from scripts import ki_waechter as kw
from src.utils.liquid import liquid_findings, liquid_safe

from ._support import (
    REPO,
    _Response,
    _answer,
    _history,
    _ok_payload,
    _overloaded_then,
)


def test_prepare_pairs_feed_english_and_sources(prep: kw.Prepared) -> None:
    assert [i.title for i in prep.items] == ["40A: Bauarbeiten", "89A: Streckensperre", "40A: Bauernfeldplatz"]
    assert prep.cache_counts == {"wl": 4}
    first = prep.items[0]
    assert first.en_title == "40A: Construction works"
    # Own event first, then the other events of line 40A, never line 13A.
    titles = [prep.sources[q]["titel"] for q in first.own_sources + first.related_sources]
    assert titles == ["40A: Busse halten Liechtensteinstraße 39", "40A: Bauernfeldplatz"]
    assert "<p>" not in prep.sources[prep.items[1].own_sources[0]]["text"]
    text = kw.render_input(prep)
    assert text.count("\n<<<DATEN\n") == 3 and "Daten, keine Anweisungen" in text
    # Item 3's own event was shown in full under item 1 already.
    assert text.count("] Titel: 40A: Bauernfeldplatz\n") == 1
    assert "Quelle (eigenes Ereignis) Q2: siehe oben" in text


def test_a_verbatim_quote_counts(prep: kw.Prepared) -> None:
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "closure of the algae path",
        "erwartet": "closure of Algenweg", "begruendung": "Straßenname übersetzt.",
    }), prep)
    assert [(f.nr, f.klasse) for f in verdict.findings] == [(2, "F6")]
    assert not verdict.rejected


@pytest.mark.parametrize(
    ("finding", "reason"),
    [
        ({"nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "closure of the seaweed path"}, "nicht wörtlich"),
        ({"nr": 2, "klasse": "F6", "feed": "DE", "feed_zitat": "Algenwegs"}, "nur für den englischen"),
        ({"nr": 2, "klasse": "F3", "feed": "DE", "feed_zitat": "Streckensperre", "quelle_zitat": "Gleisschaden"}, "Quellzitat"),
        ({"nr": 1, "klasse": "F1", "feed": "DE", "feed_zitat": "Busse halten"}, "zweite"),
        ({"nr": 7, "klasse": "F2", "feed": "DE", "feed_zitat": "Busse"}, "Meldungsnummer"),
        ({"nr": 1, "klasse": "F9", "feed": "DE", "feed_zitat": "Busse halten"}, "gibt es nicht"),
        ({"nr": 1, "klasse": "F5", "feed": "DE", "feed_zitat": "Bu"}, "nicht wörtlich"),
    ],
)
def test_an_unproven_finding_is_rejected(prep: kw.Prepared, finding: dict[str, object], reason: str) -> None:
    verdict = kw.verify(_answer({"erwartet": "x", "begruendung": "y", **finding}), prep)
    assert not verdict.findings
    assert reason in verdict.rejected[0].grund


def test_a_duplicate_needs_both_quotes(prep: kw.Prepared) -> None:
    base = {"nr": 1, "klasse": "F1", "feed": "DE", "feed_zitat": "Busse halten Liechtensteinstraße 39",
            "nr2": 3, "erwartet": "eine Meldung", "begruendung": "dieselbe Haltestelle"}
    ok = kw.verify(_answer({**base, "feed_zitat2": "Liechtensteinstraße 42 → Liechtensteinstraße 38"}), prep)
    assert [(f.nr, f.nr2) for f in ok.findings] == [(1, 3)]
    bad = kw.verify(_answer({**base, "feed_zitat2": "Liechtensteinstraße 99"}), prep)
    assert not bad.findings and "zweiten Meldung" in bad.rejected[0].grund


def test_a_wrong_fact_needs_a_verbatim_source_quote(prep: kw.Prepared) -> None:
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F3", "feed": "DE", "feed_zitat": "Streckensperre des Algenwegs",
        "quelle_zitat": "angeordneten Streckensperre", "erwartet": "x", "begruendung": "y",
    }), prep)
    assert len(verdict.findings) == 1


def test_the_same_finding_counts_once(prep: kw.Prepared) -> None:
    one = {"nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path", "erwartet": "Algenweg", "begruendung": "b"}
    assert len(kw.verify(_answer(one, dict(one)), prep).findings) == 1


def test_model_text_is_scrubbed(prep: kw.Prepared) -> None:
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path",
        "erwartet": "Algenweg ![x](https://example.org/p.png)",
        "begruendung": "sk-ant-oat01-abcdefghijklmnop AIzaSyA1234567890abcdefghijk <script>",
    }), prep)
    finding = verdict.findings[0]
    assert "sk-ant" not in finding.begruendung and "AIza" not in finding.begruendung and "<" not in finding.begruendung
    assert "![" not in finding.erwartet and "](" not in finding.erwartet


def test_report_comes_from_the_script(prep: kw.Prepared) -> None:
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path", "erwartet": "Algenweg", "begruendung": "b",
    }, geprueft=(1, 2)), prep)
    report = kw.render_report(prep, verdict, datetime(2026, 10, 31, 14, 5, tzinfo=UTC), "m", "")
    assert report.startswith("# KI-Wächter 31.10.2026\n")
    assert "31.10.2026 15:05 Wiener Zeit, Commit abc1234, Modell m" in report
    assert "Feed gebaut 27.10.2026 16:00 bis 27.10.2026 16:00 Wiener Zeit, 1 Fassung, 3 verschiedene" in report
    assert "| 2 | 89A: Streckensperre | g2 | 27.10. 16:00 (zuletzt im Feed) | 89A: Streckensperre | Fund 1 |" in report
    assert "| 3 | 40A: Bauernfeldplatz | g3 | 27.10. 16:00 (zuletzt im Feed) | 40A: Bauernfeldplatz | nicht geprüft |" in report
    assert "Ergebnis: 1 Fund" in report


def test_the_report_keeps_liquid_out_of_the_pages_build(prep: kw.Prepared) -> None:
    """GitHub Pages runs Liquid over every Markdown file under docs/, front matter or not."""
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path", "erwartet": "Algenweg",
        "begruendung": "Vorlage {{ site.title }} und {% include x %} im Text",
    }, geprueft=(1, 2)), prep)
    report = kw.render_report(prep, verdict, datetime(2026, 10, 31, 14, 5, tzinfo=UTC), "m", "")
    assert "{{" not in report and "{%" not in report
    assert "{ { site.title }} und { % include x %}" in report
    assert liquid_safe("a {{b}} {%c%}") == "a { {b}} { %c%}"
    # Until 2026-11-01 a plain replacement turned "{{{" into "{ {{", which
    # Liquid still reads as an opener (src/utils/liquid.py).
    assert liquid_safe("{{{ x }}}") == "{ { { x }}}"
    assert liquid_safe("{{{{") == "{ { { {"
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path", "erwartet": "Algenweg",
        "begruendung": "Vorlage {{{ title }}} kaputt {{{",
    }, geprueft=(1, 2)), prep)
    report = kw.render_report(prep, verdict, datetime(2026, 10, 31, 14, 5, tzinfo=UTC), "m", "")
    assert "{{" not in report and "{%" not in report


def test_the_written_report_keeps_liquid_out_after_the_scrub(prep: kw.Prepared, tmp_path: Path) -> None:
    """The scrub removes zero-width and soft-hyphen characters; done after
    liquid_safe it joined "{" and "%" again (review of 2026-11-01, real
    Pages build failed on the written report)."""
    work = tmp_path / "w"
    work.mkdir()
    (work / "eingabe.json").write_text(json.dumps(kw.asdict(prep)), encoding="utf-8")
    answer = {
        "geprueft": [1, 2],
        "funde": [{
            "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path", "erwartet": "Algenweg",
            "begruendung": "Vorlage {\u200b% kaputt und {\u00ad{ site.title }}",
        }],
        "vermutungen": ["Hinweis {\ufeff% if"],
    }
    ok = {"ok": True, "model": "gemini-x", "text": json.dumps(answer, ensure_ascii=False)}
    (work / "antwort.json").write_text(json.dumps(ok, ensure_ascii=False), encoding="utf-8")
    report_path = tmp_path / "docs" / "archive" / "audits" / "ki-waechter-2026-11-02.md"
    assert kw.main(["verify", "--dir", str(work), "--report", str(report_path)]) == 0
    written = report_path.read_text(encoding="utf-8")
    assert "Vorlage { % kaputt und { { site.title }}" in written
    assert liquid_findings(written) == []


def test_the_answer_is_the_last_json_block() -> None:
    text = 'Vorher {"x": 1}\n```json\n{"funde": [1]}\n```\nund\n```json\n{"funde": [], "geprueft": [1]}\n```'
    assert kw.extract_answer(text) == {"funde": [], "geprueft": [1]}
    with pytest.raises(ValueError):
        kw.extract_answer("keine Antwort")


def test_verify_fails_when_the_model_gave_no_answer(prep: kw.Prepared, tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "eingabe.json").write_text(json.dumps(kw.asdict(prep)), encoding="utf-8")
    failed = {"ok": False, "model": "gemini-x", "error": "Gemini HTTP 403: key"}
    (work / "antwort.json").write_text(json.dumps(failed), encoding="utf-8")
    assert kw.main(["verify", "--dir", str(work)]) == 1
    ok = {"ok": True, "model": "gemini-x", "text": '{"geprueft": [1, 2, 3], "funde": [], "vermutungen": []}',
          "tokens": {"promptTokenCount": 900}}
    (work / "antwort.json").write_text(json.dumps(ok), encoding="utf-8")
    assert kw.main(["verify", "--dir", str(work)]) == 0
    result = json.loads((work / "ergebnis.json").read_text(encoding="utf-8"))
    assert result["funde"] == 0 and result["tokens"] == {"promptTokenCount": 900}
    report = (work / "bericht.md").read_text(encoding="utf-8")
    assert "Ergebnis: keine Funde" in report and "Modell gemini-x" in report


def test_every_item_text_of_24_hours_is_one_item(tmp_path: Path) -> None:
    _history(tmp_path)
    prep = kw.prepare(kw.git_snapshots(tmp_path, "HEAD", 24), "head", 24)
    assert len(prep.versions) == 3  # the version of 26.10. lies outside the window
    assert [(i.title, i.versions, i.variant, i.variants) for i in prep.items] == [
        ("40A: Bauarbeiten", [1], 1, 2),
        ("40A: Bauarbeiten", [2, 3], 2, 2),
        ("13A: Falschparker", [2], 1, 1),
    ]
    assert kw.together(prep, prep.items[0]) == []
    assert kw.together(prep, prep.items[2]) == [2]
    assert kw.visible(prep, prep.items[1]) == "27.10. 12:00 bis 27.10. 16:00 (zuletzt im Feed)"
    text = kw.render_input(prep)
    assert "3 Fassungen des Feeds, gebaut 27.10.2026 08:00 bis 27.10.2026 16:00 Wiener Zeit" in text
    assert "Textstand 2 von 2 dieser Meldung" in text


def test_a_duplicate_must_have_stood_in_the_feed_at_the_same_time(tmp_path: Path) -> None:
    _history(tmp_path)
    prep = kw.prepare(kw.git_snapshots(tmp_path, "HEAD", 24), "head", 24)
    base = {"klasse": "F1", "feed": "DE", "feed_zitat": "Busse halten Liechtensteinstraße 39",
            "feed_zitat2": "Busse halten Liechtensteinstraße 39 nicht", "erwartet": "x", "begruendung": "y"}
    together = kw.verify(_answer({**base, "nr": 2, "nr2": 3}), prep)
    assert [(f.nr, f.nr2) for f in together.findings] == [(2, 3)]
    apart = kw.verify(_answer({**base, "nr": 1, "nr2": 3}), prep)
    assert not apart.findings and "nie gleichzeitig" in apart.rejected[0].grund
    same_guid = kw.verify(_answer({**base, "nr": 1, "nr2": 2, "feed_zitat2": "Seit 14:00"}), prep)
    assert not same_guid.findings


def test_one_error_in_two_texts_of_an_item_counts_once(tmp_path: Path) -> None:
    _history(tmp_path)
    prep = kw.prepare(kw.git_snapshots(tmp_path, "HEAD", 24), "head", 24)
    one = {"klasse": "F5", "feed": "DE", "feed_zitat": "Busse halten Liechtensteinstraße", "erwartet": "x",
           "begruendung": "y"}
    verdict = kw.verify(_answer({**one, "nr": 1}, {**one, "nr": 2}), prep)
    assert len(verdict.findings) == 1


def test_prepare_command_reads_the_history(tmp_path: Path) -> None:
    _history(tmp_path)
    out = tmp_path / "out"
    assert kw.main(["prepare", "--root", str(tmp_path), "--rev", "HEAD", "--hours", "24", "--out", str(out)]) == 0
    data = json.loads((out / "eingabe.json").read_text(encoding="utf-8"))
    assert len(data["versions"]) == 3 and len(data["items"]) == 3
    with pytest.raises(ValueError):
        kw.git_snapshots(tmp_path, "--output=x", 24)


def test_gemini_gets_one_request_with_the_key_in_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        return _Response(200, _ok_payload('{"funde": []}'))

    monkeypatch.setattr(kw, "request_safe", fake_request)
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash", "SECRET-KEY")
    assert answer.ok and answer.text == '{"funde": []}'  # thought parts are not the answer
    assert answer.tokens == {"promptTokenCount": 30000, "candidatesTokenCount": 400, "thoughtsTokenCount": 9000}
    assert len(calls) == 1 and answer.requests == 1
    call = calls[0]
    assert call["url"].endswith("/models/gemini-3.8-flash:generateContent")
    assert "SECRET-KEY" not in call["url"] and call["headers"]["x-goog-api-key"] == "SECRET-KEY"
    body = json.loads(call["data"])
    assert body["generationConfig"]["responseJsonSchema"] == kw.ANSWER_SCHEMA
    assert "tools" not in body


CHAIN = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
# A 429 of the Gemini API with Google's error details (google.rpc.QuotaFailure, RetryInfo).
QUOTA_REPLY = {"error": {
    "code": 429, "status": "RESOURCE_EXHAUSTED",
    "message": "You exceeded your current quota, please check your plan and billing details.",
    "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            "quotaDimensions": {"model": "gemini-3.8-flash", "location": "global"}, "quotaValue": "20"}]},
        {"@type": "type.googleapis.com/google.rpc.Help", "links": [{"description": "Learn more", "url": "https://x"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "39s"},
    ],
}}


def test_a_quota_google_reports_as_reached_passes_the_model_over_for_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Michael 2026-11-05: "Zähle es bitte so wie Google. Wir brauchen keine eigene
    Zähltechnik." A 429 is Google's count: the model is out for this run, its
    figures (google.rpc.QuotaFailure, RetryInfo) stand in the error, the chain
    goes on, and nothing is sent twice to a spent model."""
    busy = {"error": {"message": "This model is currently experiencing high demand."}}
    waits: list[float] = []
    calls = _overloaded_then(monkeypatch, [_Response(429, QUOTA_REPLY)])
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", sleep=waits.append)
    assert not answer.ok and answer.overloaded and len(calls) == 1 and answer.requests == 1 and waits == []
    assert ("Kontingent laut Google erreicht: GenerateRequestsPerDayPerProjectPerModel-FreeTier = 20, "
            "Wartezeit 39s" in answer.error)
    # Without details the error still says whose count it is.
    assert kw.quota_details({"error": {"message": "Quota exceeded"}}) == "Kontingent laut Google erreicht"
    assert kw.quota_details("kein Objekt") == "Kontingent laut Google erreicht"
    # 3.8 Flash spent, 3.6 Flash overloaded: the second pass asks 3.6 alone, after a minute.
    calls = _overloaded_then(monkeypatch, [_Response(429, QUOTA_REPLY), _Response(503, busy),
                                           _Response(200, _ok_payload("{}"))])
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=waits.append)
    assert [c.split("/models/")[1].split(":")[0] for c in calls] == [
        "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.6-flash",
    ]
    assert answer.ok and answer.model == "gemini-3.6-flash" and waits == [60]
    assert answer.per_model == {"gemini-3.8-flash": 1, "gemini-3.6-flash": 2} and answer.requests == 3
    # Every model spent: one request each, no wait, and the day counts as overloaded (a note).
    calls = _overloaded_then(monkeypatch, [_Response(429, QUOTA_REPLY)] * 4)
    waits.clear()
    answer = kw.ask_gemini("Prompt", CHAIN, "k", sleep=waits.append)
    assert len(calls) == 4 and not answer.ok and answer.overloaded and waits == []
    # A too large input is never sent.
    calls = _overloaded_then(monkeypatch, [])
    big = kw.ask_gemini("x" * (kw.MAX_PROMPT_CHARS + 1), "gemini-3.8-flash", "k")
    assert not big.ok and "nicht gesendet" in big.error and calls == [] and big.requests == 0


def test_an_overloaded_model_hands_over_and_is_asked_again_in_the_next_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy = {"error": {"message": "This model is currently experiencing high demand."}}
    calls = _overloaded_then(monkeypatch, [_Response(503, busy), _Response(503, busy), _Response(200, _ok_payload("{}"))])
    waits: list[float] = []
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=waits.append)
    # 3.8 overloaded, 3.6 at once, then 3.8 again after a minute.
    assert [c.split("/models/")[1] for c in calls] == [
        "gemini-3.8-flash:generateContent", "gemini-3.6-flash:generateContent", "gemini-3.8-flash:generateContent",
    ]
    assert answer.ok and answer.model == "gemini-3.8-flash" and answer.requests == 3 and waits == [60]
    # The fallback that answers is named in the answer.
    _overloaded_then(monkeypatch, [_Response(503, busy), _Response(200, _ok_payload("{}"))])
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=lambda s: None)
    assert answer.ok and answer.model == "gemini-3.6-flash" and answer.requests == 2
    # One model: two passes, one wait, then the run ends overloaded.
    _overloaded_then(monkeypatch, [_Response(503, busy), _Response(503, busy), _Response(200, _ok_payload("{}"))])
    waits = []
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", sleep=waits.append)
    assert not answer.ok and answer.overloaded and "HTTP 503" in answer.error
    assert answer.requests == 2 and waits == [60]
    # Never more than MAX_ROUNDS passes over the chain; no pass, no request.
    _overloaded_then(monkeypatch, [_Response(503, busy)] * 9)
    assert kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", sleep=lambda s: None).requests == kw.MAX_ROUNDS == 2
    calls = _overloaded_then(monkeypatch, [])
    none = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", max_rounds=0)
    assert not none.ok and not none.overloaded and none.requests == 0 and calls == []
    with pytest.raises(ValueError):
        kw.ask_gemini("Prompt", "gemini-3.8-flash,../x", "k")


def test_the_lite_models_are_asked_when_both_flash_models_are_overloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Michael 2026-11-05, after five mornings of 503 on 3.8 and 3.6 Flash:
    "Baue bitte auch einen Modellwechsel mit den Lite-Modellen als Ergänzung."
    One request per model and pass, no wait inside a pass, the Lite models last."""
    busy = {"error": {"message": "This model is currently experiencing high demand."}}
    waits: list[float] = []
    bodies: dict[str, dict[str, Any]] = {}
    replies = [_Response(503, busy), _Response(503, busy), _Response(200, _ok_payload("{}"))]

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        bodies[url.split("/models/")[1].split(":")[0]] = json.loads(kwargs["data"])
        return replies.pop(0)

    monkeypatch.setattr(kw, "request_safe", fake_request)
    answer = kw.ask_gemini("Prompt", CHAIN, "k", sleep=waits.append)
    assert list(bodies) == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    assert answer.ok and answer.model == "gemini-3.5-flash-lite" and answer.requests == 3 and waits == []
    assert [a.split(" ")[0] for a in answer.attempts] == ["gemini-3.8-flash", "gemini-3.6-flash"]
    # A Lite model is asked to think at the top level (its default is
    # "minimal"; without thinking it found none of four known errors); a
    # Flash model keeps its default, the API refuses a config it does not know.
    assert bodies["gemini-3.5-flash-lite"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}
    assert "thinkingConfig" not in bodies["gemini-3.8-flash"]["generationConfig"]
    assert kw.is_lite("gemini-3.1-flash-lite") and not kw.is_lite("gemini-3.6-flash")
    # All four overloaded: once through the chain, a minute, once again, and
    # the day counts as overloaded (a note in the Health check).
    calls = _overloaded_then(monkeypatch, [_Response(503, busy)] * 9)
    answer = kw.ask_gemini("Prompt", CHAIN, "k", sleep=waits.append)
    assert len(calls) == 8 and waits == [60] and calls[3].split("/models/")[1].startswith("gemini-3.1-flash-lite:")
    assert not answer.ok and answer.overloaded and answer.requests == 8
    assert answer.per_model == dict.fromkeys(CHAIN.split(","), 2)


def test_a_dropped_connection_hands_over_like_an_overloaded_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-11-02: the connection died after 261 s while the model was still thinking."""
    calls: list[str] = []
    replies: list[object] = [requests.ConnectionError("Connection aborted."), _Response(200, _ok_payload("{}"))]

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append(url.split("/models/")[1])
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, _Response)
        return reply

    monkeypatch.setattr(kw, "request_safe", fake_request)
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=lambda s: None)
    assert calls == ["gemini-3.8-flash:generateContent", "gemini-3.6-flash:generateContent"]
    assert answer.ok and answer.model == "gemini-3.6-flash" and answer.requests == 2
    # Every request without an answer is logged with its model (02.11.2026:
    # the error of the first of two requests was not visible).
    assert len(answer.attempts) == 1 and answer.attempts[0].startswith("gemini-3.8-flash nach ")
    assert "ConnectionError" in answer.attempts[0]
    # Drops on every request: both passes with one wait between them; the
    # last error is the answer, and the day counts as overloaded.
    replies[:] = [requests.ConnectionError("x")] * 9
    calls.clear()
    waits: list[float] = []
    lost = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=waits.append)
    assert not lost.ok and lost.overloaded and "nicht erreichbar (ConnectionError)" in lost.error
    assert lost.requests == 4 and waits == [kw.OVERLOAD_WAIT_S]
    assert [a.split(" ")[0] for a in lost.attempts] == ["gemini-3.8-flash", "gemini-3.6-flash"] * 2
    # A timeout of the answer is not repeated: it already took ten minutes.
    replies[:] = [requests.ReadTimeout("x"), _Response(200, _ok_payload("{}"))]
    calls.clear()
    slow = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=lambda s: None)
    assert not slow.ok and "ReadTimeout" in slow.error and slow.requests == 1


def test_no_new_request_late_in_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """No request (wait included) starts after NO_NEW_REQUEST_AFTER_S."""
    busy = {"error": {"message": "high demand"}}

    def run(after_first: float, model: str) -> tuple[list[str], kw.Answer]:
        times = [0.0, after_first]
        monkeypatch.setattr(kw, "time", SimpleNamespace(monotonic=lambda: times.pop(0) if len(times) > 1 else times[0]))
        calls = _overloaded_then(monkeypatch, [_Response(503, busy), _Response(200, _ok_payload("{}"))])
        return calls, kw.ask_gemini("Prompt", model, "k", sleep=lambda s: None)

    # One model: the second request would wait 60 s, 690 + 60 > 720.
    calls, answer = run(kw.NO_NEW_REQUEST_AFTER_S - 30.0, "gemini-3.8-flash")
    assert len(calls) == 1 and not answer.ok and answer.requests == 1
    # The fallback model is asked at once: 690 + 0 is still inside.
    calls, answer = run(kw.NO_NEW_REQUEST_AFTER_S - 30.0, "gemini-3.8-flash,gemini-3.6-flash")
    assert len(calls) == 2 and answer.ok and answer.requests == 2
    # Past the limit: no fallback either.
    calls, answer = run(kw.NO_NEW_REQUEST_AFTER_S + 1.0, "gemini-3.8-flash,gemini-3.6-flash")
    assert len(calls) == 1 and not answer.ok


def test_the_gemini_session_keeps_its_connection_alive() -> None:
    import socket

    from src.utils.http import KEEPALIVE_SOCKET_OPTIONS, SafeDNSAdapter, session_with_retries

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in KEEPALIVE_SOCKET_OPTIONS
    with session_with_retries("t", keepalive=True, total=0) as session:
        adapter = session.get_adapter("https://generativelanguage.googleapis.com")
        assert isinstance(adapter, SafeDNSAdapter)
        assert adapter.poolmanager.connection_pool_kw["socket_options"] == list(KEEPALIVE_SOCKET_OPTIONS)
    with session_with_retries("t") as session:
        adapter = session.get_adapter("https://example.org")
        assert isinstance(adapter, SafeDNSAdapter)
        assert "socket_options" not in adapter.poolmanager.connection_pool_kw
    source = (REPO / "scripts/ki_waechter.py").read_text(encoding="utf-8")
    assert "keepalive=True" in source


@pytest.mark.parametrize("keepalive", [True, False])
def test_the_socket_request_safe_opens_carries_the_keepalive(
    monkeypatch: pytest.MonkeyPatch, keepalive: bool
) -> None:
    # The socket the Gemini request really uses: request_safe sends HTTPS
    # through a pinned adapter, not through the session's own. Until
    # 2026-11-02 the keepalive reached only the session's adapter, and the
    # socket of the Wächter's request had TCP_NODELAY alone.
    import socket

    import src.utils.http as http
    import src.utils.http_safety.sessions as http_sessions
    from tests._conftest.http_bindings import setattr_all

    options: list[tuple[int, ...]] = []

    class _Socket(socket.socket):
        # Never connected: the TLS handshake on it fails with OSError.
        def setsockopt(self, *option: Any) -> None:
            options.append(option)
            super().setsockopt(*option)

    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(http, "_resolve_target_ip", lambda parsed, url: "142.250.185.106")
    setattr_all(monkeypatch, "_resolve_hostname_safe", lambda host: [(0, 0, 0, "", ("142.250.185.106", 443))])
    opened: list[socket.socket] = []

    def _connect(*args: Any, **kwargs: Any) -> socket.socket:
        opened.append(_Socket())
        return opened[-1]

    monkeypatch.setattr(socket, "create_connection", _connect)
    # The cache lives in src/utils/http_safety/sessions.py (its only users).
    monkeypatch.setattr(http_sessions, "_HTTP_SESSION_CACHE", type(http._HTTP_SESSION_CACHE)())
    with http.session_with_retries("t", timeout=(10.0, 600.0), keepalive=keepalive, total=0) as session:
        with pytest.raises(requests.RequestException):
            http.request_safe(
                session, "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent",
                method="POST", data=b"{}", timeout=(10.0, 600.0), raise_for_status=False,
            )
    for sock in opened:
        sock.close()
    assert options, "no socket was opened"
    assert ((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in options) is keepalive


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (_Response(403, {"error": {"message": "API key not valid"}}), "HTTP 403: API key not valid"),
        (_Response(200, _ok_payload('{"funde": [', reason="MAX_TOKENS")), "brach ab (MAX_TOKENS)"),
        (_Response(200, {"promptFeedback": {"blockReason": "SAFETY"}}), "keine Antwort (SAFETY)"),
    ],
)
def test_gemini_without_an_answer_is_a_failure(monkeypatch: pytest.MonkeyPatch, reply: _Response, error: str) -> None:
    monkeypatch.setattr(kw, "request_safe", lambda *a, **k: reply)
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k")
    assert not answer.ok and error in answer.error


def test_ask_without_a_key_fails_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert kw.main(["ask", "--dir", str(tmp_path), "--model", "gemini-3.8-flash"]) == 1
    answer = json.loads((tmp_path / "antwort.json").read_text(encoding="utf-8"))
    assert answer["ok"] is False and "GEMINI_API_KEY ist leer" in answer["error"] and answer["requests"] == 0
    out = capsys.readouterr().out
    # The count stays a bare integer (until 2026-11-05 the gate parsed it with jq).
    assert "::notice title=Gemini-Anfragen::0\n" in out and "Gemini-Modelle" not in out


def test_ask_notes_the_requests_as_a_plain_number_and_per_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Two annotations for the reader of the run: the count as a bare integer, then the models."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    (tmp_path / "eingabe.md").write_text("## Meldung 1\nINHALT\n", encoding="utf-8")
    asked: list[str] = []

    def fake_ask(prompt: str, model: str, key: str) -> kw.Answer:
        asked.append(model)
        assert key == "k" and prompt.rstrip().endswith("INHALT")
        return kw.Answer(True, "gemini-3.6-flash", text="{}", requests=3,
                         per_model={"gemini-3.8-flash": 2, "gemini-3.6-flash": 1})

    monkeypatch.setattr(kw, "ask_gemini", fake_ask)
    assert kw.main(["ask", "--dir", str(tmp_path), "--model", CHAIN]) == 0
    assert asked == [CHAIN]
    out = capsys.readouterr().out
    assert "::notice title=Gemini-Anfragen::3\n" in out
    assert "::notice title=Gemini-Modelle::gemini-3.8-flash 2, gemini-3.6-flash 1\n" in out


def test_the_prompt_ends_with_the_input() -> None:
    template = (REPO / "scripts/ki_waechter_prompt.md").read_text(encoding="utf-8")
    prompt = kw.build_prompt("## Meldung 1\nINHALT", template)
    assert prompt.rstrip().endswith("## Meldung 1\nINHALT") and "{EINGABE}" not in prompt
    with pytest.raises(ValueError):
        kw.build_prompt("x", "ohne Platzhalter")


def test_prompt_names_its_input_and_not_the_backtest_cases() -> None:
    prompt = (REPO / "scripts/ki_waechter_prompt.md").read_text(encoding="utf-8")
    assert prompt.count("{EINGABE}") == 1
    for klasse in kw.CLASSES:
        assert f"- {klasse} " in prompt
    # The pull-request run replays 27.10. and 30.10.2026; the prompt must
    # not hand the model those answers.
    for case in ("Algenweg", "algae", "All Saints", "Allerheiligen", "Liechtensteinstraße"):
        assert case not in prompt


def test_the_last_request_ends_inside_the_step_limit() -> None:
    """The annotation is written only when the script ends by itself (2026-11-02)."""
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    step = next(s for s in doc["jobs"]["audit"]["steps"] if s.get("name") == "Gemini reads and compares")
    connect, read = kw.GEMINI_TIMEOUT
    worst = kw.NO_NEW_REQUEST_AFTER_S + connect + read
    assert worst + 60 <= step["timeout-minutes"] * 60
    assert step["timeout-minutes"] + 3 <= doc["jobs"]["audit"]["timeout-minutes"]


def test_only_a_busy_gemini_counts_as_overloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Michael 2026-11-04 ("Ja, so bauen"): 503, a dropped connection, a timeout
    and the quota are a note in the Health check; a rejected key or a missing
    model stay red at once."""
    busy = {"error": {"message": "This model is currently experiencing high demand."}}
    _overloaded_then(monkeypatch, [_Response(503, busy)] * 9)
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=lambda s: None)
    assert not answer.ok and answer.overloaded
    _overloaded_then(monkeypatch, [_Response(429, {"error": {"message": "Quota exceeded"}})])
    assert kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", sleep=lambda s: None).overloaded
    for status in (400, 401, 403, 404):
        _overloaded_then(monkeypatch, [_Response(status, {"error": {"message": "API key not valid"}})])
        refused = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", sleep=lambda s: None)
        assert not refused.ok and not refused.overloaded, status
    # The last request decides: overloaded first, then a rejected key.
    _overloaded_then(monkeypatch, [_Response(503, busy), _Response(403, {"error": {"message": "denied"}})])
    mixed = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", sleep=lambda s: None)
    assert not mixed.ok and not mixed.overloaded
    _overloaded_then(monkeypatch, [_Response(200, _ok_payload("{}"))])
    assert not kw.ask_gemini("Prompt", "gemini-3.8-flash", "k").overloaded
    for exc in (requests.ConnectionError("x"), requests.ReadTimeout("x")):
        def fake_request(session: object, url: str, _exc: Exception = exc, **kwargs: Any) -> _Response:
            raise _exc

        monkeypatch.setattr(kw, "request_safe", fake_request)
        assert kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", max_rounds=1).overloaded
    # Nothing sent (too large, no pass) is no overload.
    assert not kw.ask_gemini("x" * (kw.MAX_PROMPT_CHARS + 1), "gemini-3.8-flash", "k").overloaded
    assert not kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", max_rounds=0).overloaded
