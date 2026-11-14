# ruff: noqa: S603, S607
"""Antwort und Bericht: der Bericht kommt vom Skript, bleibt frei von Liquid,
die Antwort ist der letzte JSON-Block, ``verify`` ohne Antwort schlägt fehl.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import ki_waechter as kw
from src.utils.liquid import liquid_findings, liquid_safe

from ._support import (
    _answer,
)


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
