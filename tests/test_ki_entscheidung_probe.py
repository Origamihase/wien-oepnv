"""The dry run of the Gemini decision and its collection of cases (``scripts/ki_entscheidung_probe.py``).

Owner's decision of 2026-11-12 (Michael, "Ja, so bauen"). The collection
``data/samples/ki_entscheidung_faelle.json`` holds every disruption the
rules left unclear in the replay of 20.09. to 12.11.2026, at the first
state in which the feed build would have asked about it, with the other
messages of its lines. The probe puts them to Gemini like the build and
stores nothing. No test here reaches the network: the request is a fake.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import ki_entscheidung_probe as probe
from src.ki_entscheidung import candidates as cand
from src.ki_entscheidung import ask, run, store
from src.markdown_stats.review_client import ChainAnswer

REPO = Path(__file__).resolve().parents[1]
CASES = REPO / "data/samples/ki_entscheidung_faelle.json"
WORKFLOW = REPO / ".github/workflows/ki-entscheidung-probe.yml"
Answer = ChainAnswer[dict[int, tuple[str, str]]]


def _raw() -> list[dict[str, Any]]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["faelle"]
    assert isinstance(cases, list)
    return cases


def _as_expected(descriptions: Sequence[str], offset: list[int]) -> Answer:
    """Answer each message with what the collection expects (``offset`` counts the messages asked so far)."""
    raw = _raw()
    value = {number: (raw[offset[0] + number - 1]["erwartet"], f"Grund {offset[0] + number}")
             for number in range(1, len(descriptions) + 1)}
    offset[0] += len(descriptions)
    return ChainAnswer(value, "gemini-3.5-flash-lite", 1, False)


# ---- the collection ----------------------------------------------------------


def test_every_case_is_still_unclear_for_the_rules_and_in_its_class() -> None:
    """A rule change that decides one of them must take it out of the collection (and say so)."""
    raw = _raw()
    loaded = probe.load_cases(CASES)
    assert len(loaded) == len(raw) == 20
    assert [candidate.kind for _, candidate, _ in loaded] == [case["klasse"] for case in raw]
    assert Counter(case["klasse"] for case in raw) == {
        cand.CLASS_NO_CAUSE: 14, cand.CLASS_WORKS_WORD: 2, cand.CLASS_OEBB_DAY: 4,
    }
    assert {case["erwartet"] for case in raw} == set(store.KINDS)
    assert all(case["warum"] and case["meldung"]["category"] == "Störung" for case in raw)


def test_the_build_would_have_asked_about_every_case_at_its_moment() -> None:
    """Each case is new at its moment: in the feed and begun within the window of the requests."""
    for case, candidate, _ in probe.load_cases(CASES):
        begin = cand.sudden_begin(candidate)
        now = datetime.fromisoformat(case["jetzt"])
        assert begin is not None and now - begin <= run.ASK_WINDOW, candidate.item["title"]


def test_the_rules_alone_show_what_the_feed_showed_then() -> None:
    """The time line the replay rendered for the case: "[Seit hh:mm]" exactly where the rules give a begin."""
    for case, candidate, _ in probe.load_cases(CASES):
        shown = (case["feed_damals"] or "").replace(" ", " ")
        if not shown:  # not among the feed's items at that moment
            continue
        line = probe._rule_line(candidate)
        assert (line in shown) is line.startswith("[Seit"), candidate.item["title"]


def test_the_model_reads_the_works_notice_beside_the_display_text() -> None:
    by_title = {str(candidate.item["title"]): cand.describe(1, candidate, items) for _, candidate, items in probe.load_cases(CASES)}
    tokio = by_title["26: Züge halten in Tokiostraße"]
    works = "(Hinweis, Beginn Mo 14.09.2026 00:00, Ende Do 31.12.2026 23:59): 25/26/27: Gleisbauarbeiten"
    assert f"Andere Meldung derselben Linie {works}" in tokio
    assert "Andere Meldung derselben Linie (Störung, Beginn So 18.10.2026 21:16" in by_title["9: Züge halten bei Linie 52"]
    assert "Andere Meldungen derselben Linie: keine" in by_title["48A: Gasrohrgebrechen"]


# ---- the probe ---------------------------------------------------------------


def test_the_report_shows_rules_expectation_and_answer_and_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # the store of the test lies under tmp_path/data (``isolate_ki_decisions``)
    offset = [0]
    sizes: list[int] = []

    def fake(descriptions: Sequence[str]) -> Answer:
        sizes.append(len(descriptions))
        return _as_expected(descriptions, offset)

    report = probe.run_probe(CASES, ask=fake, clock=lambda: 0.0)
    assert sizes == [run.MAX_PER_REQUEST] * 5 == [4] * 5
    assert "- Unklare Meldungen in der Fallsammlung: 20" in report and "- Antworten: 20 von 20" in report
    assert "- Wie erwartet: 20 von 20 Meldungen mit einer Erwartung" in report
    assert "| 3 | 26: Züge halten in Tokiostraße | ohne-ursache | [Seit 08:48] | geplant | geplant | Grund 3 |" in report
    assert "| 7 | 48A: Gasrohrgebrechen | bauwort | ohne Beginn | ploetzlich | ploetzlich | Grund 7 |" in report
    (line,) = [line for line in report.splitlines() if line.startswith(probe.ANSWERS_MARK)]
    answers = json.loads(line[len(probe.ANSWERS_MARK):])
    assert len(answers) == 20 and set(answers[0]) == {"abdruck", "titel", "art", "grund", "modell"}
    assert not store.store_path().exists()


def test_without_the_key_the_probe_says_so_and_asks_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert probe.main([]) == 0
    assert capsys.readouterr().out.rstrip().endswith("- Ergebnis: nicht gefragt (GEMINI_API_KEY ist leer)")


def test_a_failing_or_silent_gemini_is_a_result_not_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    calls = [0]

    def fake(descriptions: Sequence[str]) -> Answer:
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("unerwartet")
        # A number outside the batch and a word that is no answer are dropped.
        return ChainAnswer({1: ("geplant", "a|b"), 99: ("geplant", ""), 2: ("vielleicht", "")}, "gemini-3.8-flash", 3, False)

    assert probe.main([], ask=fake) == 0
    text = summary.read_text(encoding="utf-8")
    assert "- Anfrage für Meldung 1 bis 4: keine Antwort" in text and "- Antworten: 4 von 20" in text
    title = "71E: Ersatzverkehr Busse halten Simmeringer Hauptstraße ggü. 197"
    assert f"| 1 | {title} | ohne-ursache | ohne Beginn | geplant | keine Antwort |  |" in text
    assert "| 9 | 6: Rettungseinsatz züge halten Favoritenstraße 113 | ohne-ursache | [Seit 07:22] | ploetzlich | geplant |" in text


def test_a_cases_file_that_does_not_exist_is_a_wrong_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        probe.main(["--cases", str(tmp_path / "fehlt.json")])
    assert stopped.value.code == 2 and "Fallsammlung nicht gefunden" in capsys.readouterr().err


def test_a_block_no_model_answered_is_put_again_in_the_next_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    asked: list[str] = []
    pauses: list[float] = []

    def fake(descriptions: Sequence[str]) -> Answer:
        asked.append(descriptions[0].splitlines()[2])
        if len(asked) in (2, 3):  # busy for the second and the third block of the first pass
            return ChainAnswer(None, "", 4, False)
        return ChainAnswer({number: ("unklar", "") for number in range(1, len(descriptions) + 1)}, "gemini-3.8-flash", 1, False)

    report = probe.run_probe(CASES, ask=fake, clock=lambda: 0.0, passes=2, pause_s=30.0, sleep=pauses.append)
    assert len(asked) == 7 and asked[5:] == asked[1:3] and pauses == [30.0]
    assert "- Anfrage für Meldung 5 bis 8 (2. Durchgang): gemini-3.8-flash" in report and "- Antworten: 20 von 20" in report
    # Everything answered in the first pass: no pause, no second pass.
    asked.clear()
    pauses.clear()
    probe.run_probe(CASES, ask=lambda d: ChainAnswer({1: ("unklar", "")}, "m", 1, False), passes=3, pause_s=30.0, sleep=pauses.append)
    assert pauses == []


def test_a_collection_without_an_unclear_case_is_reported(tmp_path: Path) -> None:
    empty = tmp_path / "data" / "faelle.json"
    empty.parent.mkdir()
    empty.write_text('{"faelle": [{"jetzt": "kein Datum"}, 7]}', encoding="utf-8")
    assert probe.run_probe(empty, ask=lambda descriptions: ChainAnswer(None, "", 0, False)).rstrip().endswith(
        "- Ergebnis: nicht gefragt (keine Meldung der Sammlung ist nach den Regeln unklar)"
    )


@pytest.mark.parametrize("text", ["{kein JSON", '{"faelle": {"1": {}}}', "[]", None])
def test_a_collection_that_cannot_be_read_stops_the_probe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str | None
) -> None:
    """A broken file must not read like "no unclear case": no report, exit code 2."""
    broken = tmp_path / "data" / "faelle.json"
    broken.parent.mkdir()
    if text is not None:
        broken.write_text(text, encoding="utf-8")
    with pytest.raises(probe.CasesUnreadable):
        probe.run_probe(broken, ask=lambda descriptions: ChainAnswer(None, "", 0, False))
    if text is not None:  # the missing file is caught before, by its own message
        with pytest.raises(SystemExit) as stopped:
            probe.main(["--cases", str(broken)], ask=lambda descriptions: ChainAnswer(None, "", 0, False))
        assert stopped.value.code == 2 and "Fallsammlung nicht lesbar" in capsys.readouterr().err


# ---- the workflow ------------------------------------------------------------


def test_the_workflow_runs_by_hand_and_on_branch_pushes_that_touch_the_package() -> None:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = doc[True]  # YAML reads the key ``on`` as a boolean
    assert set(triggers) == {"workflow_dispatch", "push"}
    assert triggers["push"]["branches-ignore"] == ["main"]
    paths = triggers["push"]["paths"]
    assert paths == [
        "src/ki_entscheidung/**",
        "src/markdown_stats/review_client.py",
        "scripts/ki_entscheidung_probe.py",
        "data/samples/ki_entscheidung_faelle.json",
        ".github/workflows/ki-entscheidung-probe.yml",
    ]
    assert all((REPO / path.removesuffix("/**")).exists() for path in paths)
    assert doc["permissions"] == {"contents": "read"}


def test_only_the_asking_step_of_the_probe_has_the_key() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    steps = yaml.safe_load(text)["jobs"]["probe"]["steps"]
    assert text.count("secrets.GEMINI_API_KEY") == 1 and text.count("secrets.") == 1
    (holder,) = [step for step in steps if "GEMINI_API_KEY" in (step.get("env") or {})]
    assert holder["run"] == "python scripts/ki_entscheidung_probe.py --passes 2 --pause 30"
    assert holder["env"] == {"GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}"}
    # Five blocks of at most DEADLINE_S each, all of them twice, fit the step's limit.
    assert 2 * 5 * (ask.DEADLINE_S + 5) + 30 < holder["timeout-minutes"] * 60
