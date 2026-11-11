# ruff: noqa: S603, S607
"""The dry run of the yearly Baustellen review: ``scripts/jahresrueckblick_probe.py`` and its workflow.

Owner's decision of 2026-11-10 (Michael), point 4. The probe shows what
the prompt, Gemini and the guard do, without storing or publishing
anything, and it must never turn a pull request red because Gemini is
busy. The workflow gives the key to the one step that asks and to no fork.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import jahresrueckblick_probe as probe
from src.health_check.workflow_secrets import WORKFLOW_SECRETS
from src.markdown_stats.baustellen_year import load_sites
from src.markdown_stats.review_client import ReviewAnswer
from src.markdown_stats.year_review import build_prompt
from src.utils import stats
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import BAUSTELLEN_HEADER

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "jahresrueckblick-probe.yml"
YEAR = 2026
ROWS = [
    ["2026-05-24T00:52:59+02:00", "So", "00", "g1", "Eßlinger Hauptstraße 96", "2026-05-25", "2026-09-06", "22",
     "Rohrlegung", "Der Busverkehr der Linie 26A wird über den Kreisverkehr bei der Gartenheimstraße umgeleitet."],
    ["2026-10-08T14:30:57+02:00", "Do", "14", "g2", "Burggasse 67", "2026-07-28", "2026-10-30", "7",
     "Straßenbau", "Die Haltestelle wird von Burggasse ONr.67 nach Burggasse ONr. 69 verlegt."],
]
GOOD = (
    "Im Jahr 2026 sind 2 Baustellen mit Folgen für den öffentlichen Verkehr erfasst. "
    "Eine lag in Neubau, eine in der Donaustadt. "
    "In der Burggasse wurde eine Haltestelle verlegt. "
    "In der Eßlinger Hauptstraße wurde der Busverkehr der Linie 26A über einen Kreisverkehr umgeleitet."
)


@pytest.fixture(autouse=True)
def _no_step_summary_of_the_test_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """In CI the test step has a ``GITHUB_STEP_SUMMARY`` of its own; the probe must not write its reports there.

    Review of 2026-11-10: with the variable set, the tests below appended nine reports to that file.
    """
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _ledger(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(BAUSTELLEN_HEADER)
    writer.writerows(ROWS)
    path = folder / f"baustellen_{YEAR}.csv"
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path


def _answering(text: str, model: str = "gemini-3.8-flash") -> probe.Ask:
    def ask(prompt: str) -> ReviewAnswer | None:
        return ReviewAnswer(text=text, model=model)

    return ask


def _run(tmp_path: Path, capsys: pytest.CaptureFixture[str], ask: probe.Ask | None, *extra: str) -> str:
    assert probe.main(["--year", str(YEAR), "--stats-dir", str(tmp_path), *extra], ask=ask) == 0
    return capsys.readouterr().out


# ---- the script ----------------------------------------------------------------


def test_an_accepted_text_is_reported_and_nothing_is_stored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    before = ledger.read_bytes()
    summary = tmp_path / "summary" / "step.md"
    summary.parent.mkdir()
    summary.write_text("vorher\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    prompts: list[str] = []

    def ask(prompt: str) -> ReviewAnswer | None:
        prompts.append(prompt)
        return ReviewAnswer(text=f"{GOOD}\n", model="gemini-3.6-flash")

    out = _run(tmp_path, capsys, ask)
    sites = load_sites(YEAR, stats_dir=tmp_path)
    assert prompts == [build_prompt(sites, year=YEAR)]
    assert out.rstrip("\n").split("\n") == [
        "## Jahresrückblick Baustellen 2026 – Probelauf",
        "",
        "Dieser Lauf speichert nichts und ändert keine Seite.",
        "",
        "- Baustellen im Ledger: 2",
        f"- Eingabe an Gemini: {len(prompts[0])} Zeichen",
        "- Modell: gemini-3.6-flash",
        f"- Text: {len(GOOD)} Zeichen",
        "- Urteil: angenommen",
        "",
        "Text des Modells:",
        "",
        f"> {GOOD}",
    ]
    # The same report is appended to the run's summary.
    assert summary.read_text(encoding="utf-8") == "vorher\n" + out
    # Nothing is stored: the folder holds the ledger as it was, and no review file.
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["baustellen_2026.csv", "summary"]
    assert ledger.read_bytes() == before
    assert not stats.DEFAULT_STATS_DIR.exists()


def test_a_rejected_text_is_reported_with_the_reasons(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ledger(tmp_path)
    bad = GOOD + " Bitte meiden Sie im Jänner die **Zieglergasse** und die Linie 13A."
    out = _run(tmp_path, capsys, _answering(bad))
    assert "- Urteil: verworfen" in out
    lines = out.split("\n")
    at = lines.index("Gründe der Prüfung:")
    assert lines[at + 2:at + 7] == [
        "- Formatierungszeichen im Text: \\*",
        "- Zahl oder Kennung nicht in den Daten: 13A",
        "- Straßenname nicht in den Daten: Zieglergasse",
        "- Monat außerhalb aller Zeiträume: Jänner",
        "- Ich-Form, Anrede oder Rat im Text: bitte",
    ]
    # The model's text is shown, escaped for the summary.
    assert "die \\*\\*Zieglergasse\\*\\* und die Linie 13A." in out and "**Zieglergasse**" not in out


def test_markup_of_the_model_cannot_act_in_the_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ledger(tmp_path)
    out = _run(tmp_path, capsys, _answering("<script>alert(1)</script>\n\n# Titel [x](https://example.org)",
                                             model="gemini<img src=x>\nzweite Zeile"))
    assert "<script>" not in out and "<img" not in out and "](" not in out
    assert not any(line.startswith("# ") for line in out.split("\n"))
    assert "- Urteil: verworfen" in out and "\nzweite Zeile" not in out


@pytest.mark.parametrize("failure", ["none", "raises"])
def test_no_answer_is_a_result_not_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    _ledger(tmp_path)

    def ask(prompt: str) -> ReviewAnswer | None:
        if failure == "raises":
            raise RuntimeError("Gemini überlastet")
        return None

    out = _run(tmp_path, capsys, ask)
    assert "- Urteil: keine Antwort von Gemini (Einzelheiten im Protokoll des Laufs)" in out
    assert "Text des Modells" not in out


def test_without_a_key_the_probe_says_so_and_asks_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def forbidden(*args: object, **kwargs: object) -> ReviewAnswer | None:
        raise AssertionError("no request without a key")

    monkeypatch.setattr(probe, "ask_review", forbidden)
    out = _run(tmp_path, capsys, None)
    assert "- Baustellen im Ledger: 2" in out and "- Eingabe an Gemini: " in out
    assert out.rstrip().endswith("- Urteil: nicht gefragt (GEMINI_API_KEY ist leer)")
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    assert "nicht gefragt (GEMINI_API_KEY ist leer)" in _run(tmp_path, capsys, None)


def test_the_key_of_the_environment_reaches_the_client_and_no_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger(tmp_path)
    keys: list[str] = []

    def fake_ask_review(prompt: str, *, key: str) -> ReviewAnswer | None:
        keys.append(key)
        return ReviewAnswer(text=GOOD, model="gemini-3.8-flash")

    monkeypatch.setattr(probe, "ask_review", fake_ask_review)
    monkeypatch.setenv("GEMINI_API_KEY", " SECRET-KEY-0123456789 ")
    out = _run(tmp_path, capsys, None)
    assert keys == ["SECRET-KEY-0123456789"]
    assert "- Urteil: angenommen" in out and "SECRET-KEY" not in out


def test_a_given_ask_comes_before_the_key_of_the_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> ReviewAnswer | None:
        raise AssertionError("the caller's ask is the one to use")

    monkeypatch.setattr(probe, "ask_review", forbidden)
    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-KEY-0123456789")
    assert "- Urteil: angenommen" in _run(tmp_path, capsys, _answering(GOOD))


def test_a_year_without_rows_is_not_asked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def forbidden(prompt: str) -> ReviewAnswer | None:
        raise AssertionError("nothing to ask about")

    out = _run(tmp_path, capsys, forbidden)
    assert out.rstrip().split("\n")[-2:] == [
        "- Baustellen im Ledger: 0",
        "- Urteil: nicht gefragt (das Jahr hat keine Baustellen im Ledger)",
    ]


def test_the_default_year_is_the_running_vienna_year(capsys: pytest.CaptureFixture[str]) -> None:
    before = datetime.now(VIENNA_TZ).year
    assert probe.main([], ask=None) == 0
    after = datetime.now(VIENNA_TZ).year
    title = capsys.readouterr().out.split("\n")[0]
    assert title in {f"## Jahresrückblick Baustellen {year} – Probelauf" for year in (before, after)}


@pytest.mark.parametrize(
    ("moment", "year"),
    [
        (datetime(2026, 12, 31, 22, 59, 59, tzinfo=UTC), 2026),   # 23:59:59 in Vienna
        (datetime(2026, 12, 31, 23, 0, 0, tzinfo=UTC), 2027),     # midnight in Vienna, still 2026 in UTC
        (datetime(2027, 1, 7, 23, 30, 0, tzinfo=UTC), 2027),      # 08.01. in Vienna, still 07.01. in UTC
    ],
)
def test_the_default_year_changes_at_vienna_midnight(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, moment: datetime, year: int
) -> None:
    class _Clock:
        """Stands in for ``datetime`` in the script, which only asks it for the time."""

        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            return moment.astimezone(tz)

    monkeypatch.setattr(probe, "datetime", _Clock)
    assert probe.main([], ask=None) == 0
    assert capsys.readouterr().out.split("\n")[0] == f"## Jahresrückblick Baustellen {year} – Probelauf"


@pytest.mark.parametrize("argv", [["--year", "heuer"], ["--year", "1999"], ["--year", "2101"], ["--jahr", "2026"],
                                   ["--year"], ["2026"]])
def test_only_wrong_arguments_fail(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        probe.main(argv)
    assert raised.value.code == 2
    capsys.readouterr()


@pytest.mark.parametrize("year", [2000, 2100])
def test_the_bounds_of_the_plausible_years_are_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], year: int
) -> None:
    """Finding 69 of the review of 2026-11-10: ``<`` in place of ``<=`` at ``_year`` passed every test."""
    assert probe.main(["--year", str(year), "--stats-dir", str(tmp_path)], ask=None) == 0
    assert capsys.readouterr().out.split("\n")[0] == f"## Jahresrückblick Baustellen {year} – Probelauf"


@pytest.mark.parametrize("value", [None, ""])
def test_outside_a_workflow_run_no_summary_file_is_opened(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """Without ``GITHUB_STEP_SUMMARY`` (unset or empty) the report goes to stdout only."""
    if value is not None:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", value)
    _ledger(tmp_path / "stats")
    monkeypatch.chdir(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("no summary file outside a workflow run")

    monkeypatch.setattr(probe, "open", forbidden, raising=False)
    assert "- Urteil: angenommen" in _run(tmp_path / "stats", capsys, _answering(GOOD))
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["stats"]


def test_the_probe_starts_as_a_script_from_the_repository(tmp_path: Path) -> None:
    """``python scripts/jahresrueckblick_probe.py`` finds ``src`` without ``PYTHONPATH``, as the workflow starts it."""
    _ledger(tmp_path)
    env = {name: value for name, value in os.environ.items()
           if name not in {"PYTHONPATH", "GEMINI_API_KEY", "GITHUB_STEP_SUMMARY", "JAHRESRUECKBLICK_MODEL"}}
    result = subprocess.run(
        [sys.executable, "scripts/jahresrueckblick_probe.py", "--year", str(YEAR), "--stats-dir", str(tmp_path)],
        cwd=REPO, env=env, capture_output=True, text=True, check=False, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("- Urteil: nicht gefragt (GEMINI_API_KEY ist leer)")
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["baustellen_2026.csv"]


def test_a_summary_that_cannot_be_written_does_not_fail_the_probe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger(tmp_path)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "keine" / "mappe" / "step.md"))
    assert "- Urteil: angenommen" in _run(tmp_path, capsys, _answering(GOOD))


# ---- the workflow ---------------------------------------------------------------


def test_the_health_check_names_both_uses_of_the_key() -> None:
    (secret,) = [entry for entry in WORKFLOW_SECRETS if entry.name == "GEMINI_API_KEY"]
    assert secret.kind == "tooling"
    assert secret.purpose == "KI-Wächter und Jahresrückblick der Baustellen (Gemini API, Gratis-Kontingent)"


def _doc() -> dict[Any, Any]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _steps() -> list[dict[str, Any]]:
    jobs = _doc()["jobs"]
    assert list(jobs) == ["probe"]
    steps: list[dict[str, Any]] = jobs["probe"]["steps"]
    return steps


def test_the_workflow_runs_by_hand_and_on_pull_requests_that_touch_the_package() -> None:
    doc = _doc()
    triggers = doc[True]  # YAML reads the key ``on`` as a boolean
    assert set(triggers) == {"workflow_dispatch", "pull_request"}
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"year"}
    paths = triggers["pull_request"]["paths"]
    assert paths == [
        "src/markdown_stats/baustellen_year.py",
        "src/markdown_stats/year_review.py",
        "src/markdown_stats/review_client.py",
        "scripts/jahresrueckblick_probe.py",
        ".github/workflows/jahresrueckblick-probe.yml",
    ]
    assert all((REPO / path).is_file() for path in paths)
    assert doc["permissions"] == {"contents": "read"}
    assert doc["concurrency"] == {"group": "jahresrueckblick-probe-${{ github.ref }}", "cancel-in-progress": True}
    job = doc["jobs"]["probe"]
    assert job["timeout-minutes"] == 10 and job["permissions"] == {"contents": "read"}


def test_only_the_asking_step_sees_the_key() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("secrets.GEMINI_API_KEY") == 1 and text.count("secrets.") == 1
    steps = _steps()
    holders = [step for step in steps if "GEMINI_API_KEY" in (step.get("env") or {})]
    (ask,) = holders
    assert ask is steps[-1] and ask["name"] == "Gemini writes, the guard checks"
    assert ask["env"] == {"GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}", "YEAR": "${{ steps.decide.outputs.year }}"}
    assert ask["run"] == 'python scripts/jahresrueckblick_probe.py ${YEAR:+--year "${YEAR}"}'
    # No job-wide or workflow-wide environment carries it, and nothing is published.
    assert "GEMINI" not in str(_doc().get("env", "")) and "publish_to_main" not in text and "git push" not in text
    assert "GEMINI_API_KEY" not in _doc()["jobs"]["probe"].get("env", {})


def test_every_step_after_the_decision_waits_for_it() -> None:
    steps = _steps()
    assert steps[0]["id"] == "decide" and "if" not in steps[0]
    for step in steps[1:]:
        assert str(step["if"]).startswith("steps.decide.outputs.ask == 'true'"), step["name"]


def test_checkout_python_and_dependencies_are_set_up_like_in_the_ki_waechter() -> None:
    waechter = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    theirs = {step["name"]: step for step in waechter["jobs"]["audit"]["steps"]}
    ours = {str(step["name"]): step for step in _steps()}
    for name in (
        "Check out repository (attempt 1/2)",
        "Check out repository (attempt 2/2 — runs only if attempt 1 hit checkout#2351)",
        "Set up Python",
        "Install dependencies",
    ):
        assert ours[name]["uses"] == theirs[name]["uses"], name
        assert ours[name].get("with") == theirs[name].get("with"), name
    assert ours["Install dependencies"]["uses"] == "./.github/actions/install-deps"
    assert ours["Check out repository (attempt 1/2)"]["continue-on-error"] is True


def _decide(tmp_path: Path, event: str, **extra: str) -> tuple[dict[str, str], str]:
    """Run the first step's script for *event*; its outputs and what it wrote into the summary."""
    script = str(_steps()[0]["run"])
    tmp_path.mkdir(parents=True, exist_ok=True)
    output, summary = tmp_path / "out", tmp_path / "summary"
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "EVENT": event,
        "YEAR_INPUT": "",
        "OWNER_REPO": "o/r",
        "PR_HEAD_REPO": "o/r" if event == "pull_request" else "",
        **extra,
    }
    subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True)
    output.touch()
    summary.touch()
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    return outputs, summary.read_text(encoding="utf-8")


def test_the_decision_reads_only_the_environment() -> None:
    step = _steps()[0]
    assert "${{" not in str(step["run"])
    assert step["env"] == {
        "EVENT": "${{ github.event_name }}",
        "YEAR_INPUT": "${{ inputs.year }}",
        "OWNER_REPO": "${{ github.repository }}",
        "PR_HEAD_REPO": "${{ github.event.pull_request.head.repo.full_name }}",
    }


def test_a_fork_pull_request_is_not_asked(tmp_path: Path) -> None:
    """A fork gets no secrets (the same check as the gate of ki-waechter.yml)."""
    outputs, summary = _decide(tmp_path / "fork", "pull_request", PR_HEAD_REPO="someone/wien-oepnv")
    assert outputs == {"ask": "false"}
    assert summary == "Jahresrückblick-Probe: Pull Request aus einem Fork, keine Anfrage an Gemini.\n"
    outputs, summary = _decide(tmp_path / "own", "pull_request")
    assert outputs == {"year": "", "ask": "true"} and summary == ""


def test_a_manual_run_takes_a_year_of_four_digits(tmp_path: Path) -> None:
    assert _decide(tmp_path / "plain", "workflow_dispatch")[0] == {"year": "", "ask": "true"}
    assert _decide(tmp_path / "year", "workflow_dispatch", YEAR_INPUT="2026")[0] == {"year": "2026", "ask": "true"}
    for number, bad in enumerate(("26", "20266", "2026; id", "$(id)", "--stats-dir /", "zweitausend")):
        with pytest.raises(subprocess.CalledProcessError):
            _decide(tmp_path / f"bad{number}", "workflow_dispatch", YEAR_INPUT=bad)
