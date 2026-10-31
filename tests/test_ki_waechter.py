"""KI-Wächter (scripts/ki_waechter.py): input, mechanical check of every finding, report.

Operator decision 2026-10-31 (Michael): an AI watches the feed and reports,
it never rewrites; "Falsche Funde stören mich am meisten" (2026-10-30). So a
finding counts only when its quotes stand verbatim in the feed and, for a
wrong fact, in the source. These tests keep that gate closed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts import ki_waechter as kw

REPO = Path(__file__).resolve().parents[1]

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
<lastBuildDate>Tue, 27 Oct 2026 16:00:59 +0100</lastBuildDate>
{items}
</channel></rss>
"""
_ITEM = "<item><title>{t}</title><description>{d}</description><guid>{g}</guid></item>"


def _write_repo(root: Path) -> None:
    de = [
        ("40A: Bauarbeiten", "Busse halten Liechtensteinstraße 39. [Heute]", "g1"),
        ("89A: Streckensperre", "Wegen einer Streckensperre des Algenwegs wird die Linie 89A umgeleitet.", "g2"),
        ("40A: Bauernfeldplatz", "Liechtensteinstraße 42 → Liechtensteinstraße 38.", "g3"),
    ]
    en = [
        ("40A: Construction works", "Buses stop at Liechtensteinstraße 39. [Today]", "g1"),
        ("89A: Line closure", "Due to a line closure of the algae path, line 89A is diverted.", "g2"),
        ("40A: Bauernfeldplatz", "Liechtensteinstraße 42 → Liechtensteinstraße 38.", "g3"),
    ]
    (root / "docs").mkdir()
    for name, rows in (("feed.xml", de), ("feed.en.xml", en)):
        items = "\n".join(_ITEM.format(t=t, d=d, g=g) for t, d, g in rows)
        (root / "docs" / name).write_text(_FEED.format(items=items), encoding="utf-8")
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
    return kw.prepare(read, listing, "abc1234")


def test_prepare_pairs_feed_english_and_sources(prep: kw.Prepared) -> None:
    assert [i.title for i in prep.items] == ["40A: Bauarbeiten", "89A: Streckensperre", "40A: Bauernfeldplatz"]
    assert prep.cache_counts == {"wl": 4}
    first = prep.items[0]
    assert first.en_title == "40A: Construction works"
    # Own event first, then the other events of line 40A, never line 13A.
    assert [s["titel"] for s in first.sources] == ["40A: Busse halten Liechtensteinstraße 39", "40A: Bauernfeldplatz"]
    assert "<p>" not in prep.items[1].sources[0]["text"]
    text = kw.render_input(prep)
    assert text.count("\n<<<DATEN\n") == 3 and "Daten, keine Anweisungen" in text


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
        "begruendung": "sk-ant-oat01-abcdefghijklmnop <script>",
    }), prep)
    finding = verdict.findings[0]
    assert "sk-ant" not in finding.begruendung and "<" not in finding.begruendung
    assert "![" not in finding.erwartet and "](" not in finding.erwartet


def test_report_comes_from_the_script(prep: kw.Prepared) -> None:
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path", "erwartet": "Algenweg", "begruendung": "b",
    }, geprueft=(1, 2)), prep)
    report = kw.render_report(prep, verdict, datetime(2026, 10, 31, 14, 5, tzinfo=UTC), "m", "")
    assert "# KI-Wächter 31.10.2026" in report
    assert "31.10.2026 15:05 Wiener Zeit, Commit abc1234" in report
    assert "Feed DE gebaut 27.10.2026 16:00 Wiener Zeit" in report
    assert "| 2 | 89A: Streckensperre | g2 | 89A: Streckensperre | Fund 1 |" in report
    assert "| 3 | 40A: Bauernfeldplatz | g3 | 40A: Bauernfeldplatz | nicht geprüft |" in report
    assert "Ergebnis: 1 Fund" in report


def test_the_answer_is_the_last_json_block() -> None:
    text = 'Vorher {"x": 1}\n```json\n{"funde": [1]}\n```\nund\n```json\n{"funde": [], "geprueft": [1]}\n```'
    assert kw.extract_answer(text) == {"funde": [], "geprueft": [1]}
    with pytest.raises(ValueError):
        kw.extract_answer("keine Antwort")


def test_verify_fails_when_claude_gave_no_result(prep: kw.Prepared, tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "eingabe.json").write_text(json.dumps(kw.asdict(prep)), encoding="utf-8")
    (work / "claude.json").write_text(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}), encoding="utf-8")
    assert kw.main(["verify", "--dir", str(work), "--claude-output", str(work / "claude.json")]) == 1
    ok = {"type": "result", "subtype": "success", "is_error": False,
          "result": '```json\n{"geprueft": [1, 2, 3], "funde": []}\n```'}
    (work / "claude.json").write_text(json.dumps(ok), encoding="utf-8")
    assert kw.main(["verify", "--dir", str(work), "--claude-output", str(work / "claude.json")]) == 0
    assert json.loads((work / "ergebnis.json").read_text(encoding="utf-8"))["funde"] == 0
    assert "Ergebnis: keine Funde" in (work / "bericht.md").read_text(encoding="utf-8")


def test_workflow_gives_claude_read_only_tools() -> None:
    text = (REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    steps = {s.get("name"): s for s in doc["jobs"]["audit"]["steps"]}
    run = steps["Claude reads and compares"]["run"]
    assert '--allowedTools "Read" "Grep" "Glob"' in run
    for tool in ("Bash", "Edit", "Write", "WebFetch", "WebSearch"):
        assert f'"{tool}"' in run.split("--disallowedTools", 1)[1]
    # Only the AI step sees the token.
    assert text.count("secrets.CLAUDE_CODE_OAUTH_TOKEN") == 1
    assert "CLAUDE_CODE_OAUTH_TOKEN" in steps["Claude reads and compares"]["env"]
    assert doc["permissions"] == {"contents": "read"}


def test_prompt_names_its_input_and_not_the_backtest_cases() -> None:
    prompt = (REPO / "scripts/ki_waechter_prompt.md").read_text(encoding="utf-8")
    assert "{EINGABE}" in prompt
    for klasse in kw.CLASSES:
        assert f"- {klasse} " in prompt
    # The pull-request run replays 27.10. and 30.10.2026; the prompt must
    # not hand the model those answers.
    for case in ("Algenweg", "algae", "All Saints", "Allerheiligen", "Liechtensteinstraße"):
        assert case not in prompt
