# ruff: noqa: S603, S607
"""KI-Wächter (scripts/ki_waechter.py): input, Gemini call, mechanical check of every finding, report.

Operator decision 2026-10-31 (Michael): an AI watches the feed and reports,
it never rewrites; "Falsche Funde stören mich am meisten" (2026-10-30). So a
finding counts only when its quotes stand verbatim in the feed and, for a
wrong fact, in the source; a duplicate only when both items stood in the feed
at the same time. The same day: every item text of the last 24 hours, and
Gemini through the free API tier. These tests keep that gate closed.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import ki_waechter as kw
from src.utils.liquid import liquid_findings, liquid_safe

REPO = Path(__file__).resolve().parents[1]

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
<lastBuildDate>Tue, 27 Oct 2026 16:00:59 +0100</lastBuildDate>
{items}
</channel></rss>
"""
_ITEM = "<item><title>{t}</title><description>{d}</description><guid>{g}</guid></item>"


_DE_DEFAULT = [
        ("40A: Bauarbeiten", "Busse halten Liechtensteinstraße 39. [Heute]", "g1"),
        ("89A: Streckensperre", "Wegen einer Streckensperre des Algenwegs wird die Linie 89A umgeleitet.", "g2"),
        ("40A: Bauernfeldplatz", "Liechtensteinstraße 42 → Liechtensteinstraße 38.", "g3"),
]
_EN_DEFAULT = [
        ("40A: Construction works", "Buses stop at Liechtensteinstraße 39. [Today]", "g1"),
        ("89A: Line closure", "Due to a line closure of the algae path, line 89A is diverted.", "g2"),
        ("40A: Bauernfeldplatz", "Liechtensteinstraße 42 → Liechtensteinstraße 38.", "g3"),
]


def _write_feeds(root: Path, de: list[tuple[str, str, str]], en: list[tuple[str, str, str]],
                 built: str = "Tue, 27 Oct 2026 16:00:59 +0100") -> None:
    (root / "docs").mkdir(exist_ok=True)
    for name, rows in (("feed.xml", de), ("feed.en.xml", en)):
        items = "\n".join(_ITEM.format(t=t, d=d, g=g) for t, d, g in rows)
        feed = _FEED.format(items=items).replace("Tue, 27 Oct 2026 16:00:59 +0100", built)
        (root / "docs" / name).write_text(feed, encoding="utf-8")


def _write_repo(root: Path) -> None:
    _write_feeds(root, _DE_DEFAULT, _EN_DEFAULT)
    (root / "cache" / "wl_x").mkdir(parents=True)
    events = [
        {"guid": "g1", "title": "40A: Busse halten Liechtensteinstraße 39",
         "description": "Bauarbeiten Busse halten Liechtensteinstraße 39"},
        {"guid": "g2", "title": "89A: Streckensperre",
         "description": "<p>Wegen einer angeordneten Streckensperre des Algenwegs wird die Linie 89A umgeleitet.</p>"},
        {"guid": "g3", "title": "40A: Bauernfeldplatz",
         "description": "Haltestellenverlegung der Linie 40A Von: Liechtensteinstraße 42 Nach: Liechtensteinstraße 38"},
        {"guid": "g9", "title": "13A: Falschparker", "description": "Fahrtbehinderung"},
    ]
    (root / "cache" / "wl_x" / "events.json").write_text(json.dumps(events), encoding="utf-8")


@pytest.fixture()
def prep(tmp_path: Path) -> kw.Prepared:
    _write_repo(tmp_path)
    read, listing = kw.working_tree_reader(tmp_path)
    return kw.prepare([kw.Snapshot("abc1234", "", read, listing)], "abc1234")


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


def _answer(*funde: dict[str, object], geprueft: tuple[int, ...] = (1, 2, 3)) -> dict[str, object]:
    return {"geprueft": list(geprueft), "funde": list(funde), "vermutungen": []}


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


# --- 24 hours of feed versions from the git history --------------------------------


def _git(root: Path, *args: str, when: str = "") -> None:
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), **env})


def _history(root: Path) -> None:
    """Four Update cycles: one older than 24 h, then 40A changes its time line and 13A comes and goes."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.org")
    _git(root, "config", "user.name", "t")
    _write_repo(root)
    a40 = ("40A: Bauarbeiten", "Busse halten Liechtensteinstraße 39. [Heute]", "g1")
    a40b = ("40A: Bauarbeiten", "Busse halten Liechtensteinstraße 39. [Seit 14:00]", "g1")
    b13 = ("13A: Falschparker", "Busse halten Liechtensteinstraße 39 nicht.", "g9")
    old = ("U6: Alt", "Text von vorgestern.", "g0")
    steps = [
        ("2026-10-26T10:00:00+01:00", [old], "Mon, 26 Oct 2026 10:00:00 +0100"),
        ("2026-10-27T08:00:00+01:00", [a40], "Tue, 27 Oct 2026 08:00:00 +0100"),
        ("2026-10-27T12:00:00+01:00", [a40b, b13], "Tue, 27 Oct 2026 12:00:00 +0100"),
        ("2026-10-27T16:00:00+01:00", [a40b], "Tue, 27 Oct 2026 16:00:00 +0100"),
    ]
    for when, rows, built in steps:
        _write_feeds(root, rows, rows, built)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "chore: update cycle", when=when)


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


# --- the Gemini call -----------------------------------------------------------------


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self.content = json.dumps(payload).encode("utf-8")


def _ok_payload(text: str, reason: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": "nachgedacht", "thought": True}, {"text": text}]},
                        "finishReason": reason}],
        "usageMetadata": {"promptTokenCount": 30000, "candidatesTokenCount": 400, "thoughtsTokenCount": 9000},
    }


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


def test_the_limit_is_never_retried_and_a_too_large_input_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        return _Response(429, {"error": {"message": "Quota exceeded"}})

    monkeypatch.setattr(kw, "request_safe", fake_request)
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", max_requests=3, sleep=lambda s: None)
    assert not answer.ok and "Kontingent erschöpft, kein neuer Versuch" in answer.error
    assert len(calls) == 1 and answer.requests == 1
    big = kw.ask_gemini("x" * (kw.MAX_PROMPT_CHARS + 1), "gemini-3.8-flash", "k", max_requests=3)
    assert not big.ok and "nicht gesendet" in big.error and len(calls) == 1 and big.requests == 0


def _overloaded_then(monkeypatch: pytest.MonkeyPatch, replies: list[_Response]) -> list[str]:
    calls: list[str] = []

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        return replies.pop(0)

    monkeypatch.setattr(kw, "request_safe", fake_request)
    return calls


def test_an_overloaded_model_hands_over_and_is_asked_again_within_the_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy = {"error": {"message": "This model is currently experiencing high demand."}}
    calls = _overloaded_then(monkeypatch, [_Response(503, busy), _Response(503, busy), _Response(200, _ok_payload("{}"))])
    waits: list[float] = []
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", max_requests=3, sleep=waits.append)
    # 3.8 overloaded, 3.6 at once, then 3.8 again after a minute.
    assert [c.split("/models/")[1] for c in calls] == [
        "gemini-3.8-flash:generateContent", "gemini-3.6-flash:generateContent", "gemini-3.8-flash:generateContent",
    ]
    assert answer.ok and answer.model == "gemini-3.8-flash" and answer.requests == 3 and waits == [60]
    # The fallback that answers is named in the answer.
    _overloaded_then(monkeypatch, [_Response(503, busy), _Response(200, _ok_payload("{}"))])
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash,gemini-3.6-flash", "k", max_requests=3, sleep=lambda s: None)
    assert answer.ok and answer.model == "gemini-3.6-flash" and answer.requests == 2
    # One model: a share of two ends after the second overloaded answer.
    _overloaded_then(monkeypatch, [_Response(503, busy), _Response(503, busy), _Response(200, _ok_payload("{}"))])
    waits = []
    answer = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", max_requests=2, sleep=waits.append)
    assert not answer.ok and "HTTP 503" in answer.error and answer.requests == 2 and waits == [60]
    # Never more than three, whatever the share; no share, no request.
    _overloaded_then(monkeypatch, [_Response(503, busy)] * 5)
    assert kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", max_requests=9, sleep=lambda s: None).requests == 3
    calls = _overloaded_then(monkeypatch, [])
    none = kw.ask_gemini("Prompt", "gemini-3.8-flash", "k", max_requests=0)
    assert not none.ok and none.requests == 0 and calls == []
    with pytest.raises(ValueError):
        kw.ask_gemini("Prompt", "gemini-3.8-flash,../x", "k")


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


def test_ask_without_a_key_fails_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert kw.main(["ask", "--dir", str(tmp_path), "--model", "gemini-3.8-flash"]) == 1
    answer = json.loads((tmp_path / "antwort.json").read_text(encoding="utf-8"))
    assert answer["ok"] is False and "GEMINI_API_KEY ist leer" in answer["error"] and answer["requests"] == 0


def test_the_prompt_ends_with_the_input() -> None:
    template = (REPO / "scripts/ki_waechter_prompt.md").read_text(encoding="utf-8")
    prompt = kw.build_prompt("## Meldung 1\nINHALT", template)
    assert prompt.rstrip().endswith("## Meldung 1\nINHALT") and "{EINGABE}" not in prompt
    with pytest.raises(ValueError):
        kw.build_prompt("x", "ohne Platzhalter")


def test_workflow_gives_the_model_no_tools_and_the_key_to_one_step() -> None:
    text = (REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    steps = {s.get("name"): s for s in doc["jobs"]["audit"]["steps"]}
    ask = steps["Gemini reads and compares"]
    assert "ki_waechter.py ask" in ask["run"]
    assert text.count("secrets.GEMINI_API_KEY") == 1 and "GEMINI_API_KEY" in ask["env"]
    assert "--max-requests" in ask["run"] and ask["env"]["MAX_REQUESTS"] == "${{ needs.gate.outputs.per_job }}"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text and "claude" not in text.lower()
    assert doc["permissions"] == {"contents": "read"}
    prepare = steps["Prepare the input"]["run"]
    assert "--shallow-since" in prepare and "--hours 24" in prepare


def test_prompt_names_its_input_and_not_the_backtest_cases() -> None:
    prompt = (REPO / "scripts/ki_waechter_prompt.md").read_text(encoding="utf-8")
    assert prompt.count("{EINGABE}") == 1
    for klasse in kw.CLASSES:
        assert f"- {klasse} " in prompt
    # The pull-request run replays 27.10. and 30.10.2026; the prompt must
    # not hand the model those answers.
    for case in ("Algenweg", "algae", "All Saints", "Allerheiligen", "Liechtensteinstraße"):
        assert case not in prompt


# --- the daily request budget of the gate ------------------------------------------


def _run_gate(
    tmp_path: Path, event: str, jobs: list[str], extra: dict[str, str] | None = None, record: str = ""
) -> dict[str, str]:
    """Run the gate script with a fake ``gh``: one earlier run today with these audit jobs.

    Each job is "<id> <status> <conclusion> <annotation>"; the annotation
    "-" means the job left none.
    """
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    script = doc["jobs"]["gate"]["steps"][0]["run"]
    fake = tmp_path / "bin"
    fake.mkdir(parents=True)
    lines = "\n".join(" ".join(j.split()[:3]) for j in jobs)
    notes = "".join(
        f'  *"/check-runs/{j.split()[0]}/annotations"*) echo "{"" if j.split()[3] == "-" else j.split()[3]}" ;;\n'
        for j in jobs
    )
    (tmp_path / "record.json").write_text(record, encoding="utf-8")
    (fake / "gh").write_text(
        "#!/bin/bash\n"
        'case "$2" in\n'
        f'  *"/contents/data/side_steps/ki-waechter.json"*) cat "{tmp_path / "record.json"}" ;;\n'
        '  *"/runs?created="*) echo 11 ;;\n'
        f'  *"/runs/11/jobs?"*) printf "{lines}\\n" ;;\n'
        + notes
        + "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake / "gh").chmod(0o755)
    output, summary = tmp_path / "out", tmp_path / "summary"
    env = {
        "PATH": f"{fake}:/usr/bin:/bin:/usr/local/bin",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "EVENT": event,
        "OWNER_REPO": "o/r",
        "PR_HEAD_REPO": "o/r",
        "RUN_ID": "99",
        "REV_INPUT": "",
        "KI_WAECHTER_MAX_REQUESTS": str(doc["env"]["KI_WAECHTER_MAX_REQUESTS"]),
        "MAX_PER_AUDIT": doc["jobs"]["gate"]["steps"][0]["env"]["MAX_PER_AUDIT"],
        "KI_WAECHTER_START_HOUR": "0",
        "KI_WAECHTER_RETRY_HOURS": doc["env"]["KI_WAECHTER_RETRY_HOURS"],
        **(extra or {}),
    }
    subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True)
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


def test_the_gate_counts_the_requests_of_the_day() -> None:
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    assert doc["env"]["KI_WAECHTER_MAX_REQUESTS"] == "10"
    assert doc["jobs"]["gate"]["steps"][0]["env"]["MAX_PER_AUDIT"] == str(kw.MAX_REQUESTS_PER_AUDIT)
    assert doc["jobs"]["gate"]["permissions"] == {"actions": "read", "contents": "read"}
    # A re-run keeps its run's creation time: jobs count by their own start,
    # runs are listed a month back and kept when touched since midnight.
    script = doc["jobs"]["gate"]["steps"][0]["run"]
    assert 'select(.id != ${RUN_ID} and .updated_at >= \\"${since}\\")' in script
    assert "jobs?filter=all&per_page=100" in script
    assert '.started_at >= \\"${since}\\"' in script
    assert "midnight - 31 * 86400" in script and "--paginate" in script


@pytest.mark.parametrize(
    ("event", "jobs", "per_job"),
    [
        # Pull requests and manual runs count up to 10 - 3 = 7: the last three
        # requests of the day stay with the daily audit (2026-11-01).
        # Nothing used yet: a replay gets 7 // 3 = 2 per audit.
        ("pull_request", [], "2"),
        # Annotations 1 + 2 + 1 = 4 used; a replay gets (7 - 4) // 3 = 1 per audit.
        ("pull_request", ["1 completed success 1", "2 completed failure 2", "3 completed success 1"], "1"),
        # 4 + one still running (3) = 7; (7 - 7) // 3 = 0: no replay.
        ("pull_request", ["1 completed success 1", "2 completed failure 2", "3 completed success 1",
                          "4 in_progress null -"], None),
        # A finished audit without annotation counts one, a cancelled one three: 4 used.
        ("workflow_dispatch", ["1 completed success -", "2 cancelled cancelled -"], "3"),
        # 6 used: a manual run still gets one; 7 used: none, the daily audit keeps three.
        ("workflow_dispatch", ["1 completed success 3", "2 completed success 3"], "1"),
        ("workflow_dispatch", ["1 completed success 3", "2 completed success 3", "3 completed success 1"], None),
        # 01.11.2026: the trial runs used 9 and the daily audit got one request.
        # Now they stop at 7 and the daily audit keeps all three: first model,
        # fallback model, one more try.
        ("workflow_run", ["1 completed success 3", "2 completed success 3", "3 completed success 1"], "3"),
        ("workflow_run", ["1 completed success 3", "2 completed success 3", "3 completed success 3"], "1"),
        ("workflow_run", ["1 completed success 3", "2 completed success 3", "3 completed success 3",
                          "4 completed success 1"], None),
    ],
)
def test_each_audit_gets_its_share_of_the_daily_budget(
    tmp_path: Path, event: str, jobs: list[str], per_job: str | None
) -> None:
    cycle = {"CYCLE_CONCLUSION": "success", "CYCLE_BRANCH": "main"}
    out = _run_gate(tmp_path, event, jobs, cycle)
    if per_job is None:
        assert out["run"] == "false"
        assert "Kein Aufruf" in (tmp_path / "summary").read_text(encoding="utf-8")
    else:
        assert out["run"] == "true" and out["per_job"] == per_job


def _record(outcome: str, hours_ago: float, success_hours_ago: float | None = None) -> str:
    now = datetime.now(UTC)
    entry: dict[str, str] = {"outcome": outcome, "at": (now - timedelta(hours=hours_ago)).isoformat()}
    if success_hours_ago is not None:
        entry["last_success"] = (now - timedelta(hours=success_hours_ago)).isoformat()
    return json.dumps({"steps": {"KI-Wächter": entry}})


def test_the_daily_audit_starts_with_the_quota_day_and_tries_again_after_a_failure(tmp_path: Path) -> None:
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    assert doc["env"]["KI_WAECHTER_START_HOUR"] == "9" and doc["env"]["KI_WAECHTER_RETRY_HOURS"] == "2"
    cycle = {"CYCLE_CONCLUSION": "success", "CYCLE_BRANCH": "main"}
    hour = datetime.now(UTC).astimezone(kw.VIENNA).hour
    if hour < 3:  # "today" in Vienna must still hold three hours back
        pytest.skip("too close to midnight in Vienna")
    # Not yet today: runs.
    assert _run_gate(tmp_path / "a", "workflow_run", [], cycle, _record("success", 30, 30))["run"] == "true"
    # Succeeded today: done for the day.
    assert _run_gate(tmp_path / "b", "workflow_run", [], cycle, _record("success", 0.5, 0.5))["run"] == "false"
    # Failed an hour ago: waits; failed three hours ago: tries again.
    assert _run_gate(tmp_path / "c", "workflow_run", [], cycle, _record("failure", 1, 30))["run"] == "false"
    assert _run_gate(tmp_path / "d", "workflow_run", [], cycle, _record("failure", 2.5, 30))["run"] == "true"
    # Before the start hour nothing runs.
    late = {**cycle, "KI_WAECHTER_START_HOUR": "24"}
    assert _run_gate(tmp_path / "e", "workflow_run", [], late, _record("success", 30, 30))["run"] == "false"
