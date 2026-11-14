"""Workflow und Gate des KI-Wächters: Modellkette, Eingaben eines Laufs von Hand,
Probelauf im Pull Request, Aufzeichnung eines überlasteten Tages.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import ki_waechter as kw

from ._support import REPO, _run_gate

# The chain of ki-waechter.yml (a test in test_gemini.py asks it through).
CHAIN = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"


def test_the_workflow_chain_names_the_four_models() -> None:
    """Flash first, the Lite models last; no budget hands the ask step a share."""
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    assert doc["env"]["KI_WAECHTER_MODEL"].split(",") == CHAIN.split(",")
    assert "KI_WAECHTER_MAX_REQUESTS" not in doc["env"]


def test_a_manual_trial_may_name_other_models_and_never_publishes(tmp_path: Path) -> None:
    """Input ``model`` of a manual run: checked by the gate, used by the ask step, no publish."""
    text = (REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert "model" in doc[True]["workflow_dispatch"]["inputs"]
    ask = next(s for s in doc["jobs"]["audit"]["steps"] if s.get("name") == "Gemini reads and compares")
    assert ask["env"]["MODEL_OVERRIDE"] == "${{ needs.gate.outputs.model }}"
    assert '--model "${MODEL_OVERRIDE:-${KI_WAECHTER_MODEL}}"' in ask["run"]
    assert doc["jobs"]["gate"]["outputs"]["model"] == "${{ steps.decide.outputs.model }}"
    lite = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    out = _run_gate(tmp_path / "lite", "workflow_dispatch", {"MODEL_INPUT": lite})
    assert out["run"] == "true" and out["model"] == lite and out["publish"] == "false" and out["revs"] == '["HEAD"]'
    assert "Probelauf" in (tmp_path / "lite" / "summary").read_text(encoding="utf-8")
    out = _run_gate(tmp_path / "rev", "workflow_dispatch", {"MODEL_INPUT": lite, "REV_INPUT": "001e47c665ce"})
    assert out["publish"] == "false" and out["revs"] == '["001e47c665ce"]'
    # The plain manual run publishes, as before, and names no model.
    out = _run_gate(tmp_path / "plain", "workflow_dispatch")
    assert out["publish"] == "true" and "model" not in out
    # Anything but model names of 3 to 60 characters (what ask_gemini takes)
    # is refused before the audit starts.
    for n, bad in enumerate(("gemini-3.8-flash;id", "a b", "Gemini", ",gemini-3.8-flash", "x,", "xy", "g" * 61)):
        with pytest.raises(subprocess.CalledProcessError):
            _run_gate(tmp_path / f"bad{n}", "workflow_dispatch", {"MODEL_INPUT": bad})


def test_workflow_gives_the_model_no_tools_and_the_key_to_one_step() -> None:
    text = (REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    steps = {s.get("name"): s for s in doc["jobs"]["audit"]["steps"]}
    ask = steps["Gemini reads and compares"]
    assert "ki_waechter.py ask" in ask["run"]
    assert text.count("secrets.GEMINI_API_KEY") == 1 and "GEMINI_API_KEY" in ask["env"]
    assert "--max-requests" not in ask["run"] and set(ask["env"]) == {"GEMINI_API_KEY", "MODEL_OVERRIDE"}
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text and "claude" not in text.lower()
    assert doc["permissions"] == {"contents": "read"}
    prepare = steps["Prepare the input"]["run"]
    # The history by depth, not by date (tests/test_ki_waechter/test_verlauf.py).
    assert "--depth=" in prepare and "--hours 24" in prepare


def test_the_gate_keeps_no_count_of_requests() -> None:
    """Michael 2026-11-05: "Zähle es bitte so wie Google. Wir brauchen keine
    eigene Zähltechnik." Until then the gate read the annotation
    "Gemini-Anfragen" of every audit job of the day through the API and
    refused runs against a budget of ten."""
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    assert "KI_WAECHTER_MAX_REQUESTS" not in doc["env"]
    gate = doc["jobs"]["gate"]
    assert gate["permissions"] == {"contents": "read"} and set(gate["outputs"]) == {"run", "revs", "publish", "model"}
    step = gate["steps"][0]
    assert "gh api" not in step["run"] and "Gemini-Anfragen" not in step["run"] and "GH_TOKEN" not in step["env"]
    assert not hasattr(kw, "MAX_REQUESTS_PER_AUDIT")


@pytest.mark.parametrize(
    ("event", "publish", "revs"),
    [
        # A pull request replays two days with known findings and the current
        # feed, without publishing.
        ("pull_request", "false", ["001e47c665cec9f2d1d0c0c0d912230bb7f3795d",
                                   "9e60b8de9dce438906b257e42a8b524e4f191f67", "HEAD"]),
        # The daily audit arrives as the dispatch of the Update cycle (since
        # 2026-11-05; ``workflow_run`` before) and publishes.
        ("repository_dispatch", "true", ["HEAD"]),
        # A plain manual run is the daily audit by hand.
        ("workflow_dispatch", "true", ["HEAD"]),
    ],
)
def test_the_gate_names_the_commits_and_whether_the_run_publishes(
    tmp_path: Path, event: str, publish: str, revs: list[str]
) -> None:
    out = _run_gate(tmp_path, event)
    assert out["run"] == "true" and out["publish"] == publish and json.loads(out["revs"]) == revs
    assert "model" not in out


def test_a_fork_gets_no_replay_and_an_unknown_event_fails(tmp_path: Path) -> None:
    out = _run_gate(tmp_path / "fork", "pull_request", {"PR_HEAD_REPO": "someone/wien-oepnv"})
    assert out == {"run": "false"}
    with pytest.raises(subprocess.CalledProcessError):
        _run_gate(tmp_path / "odd", "schedule")


def test_the_workflow_records_an_overloaded_day_and_stays_green() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8")
    record = workflow.split("- name: Record side-step outcome", 1)[1].split("- name:", 1)[0]
    assert "outcome=overloaded" in record and ".overloaded == true" in record
    fail = workflow.split("- name: Fail the run when the audit could not run", 1)[1]
    assert '"${PUBLISH}" == "true"' in fail and ".overloaded == true" in fail
    assert fail.index("exit 0") < fail.index("exit 1")
