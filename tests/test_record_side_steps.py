"""``scripts/record_side_steps.py``: the record the Health check reads.

Operator decision 2026-10-28 (Michael): a side step that cannot run turns the
Health check red, "denn ein Fehler sollte auffallen". The record has to keep a
failure visible until the step works again, and must not lose a step that did
not run in this tick.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.health_check as hc
from scripts import record_side_steps as rec

REPO = Path(__file__).resolve().parents[1]
_T0 = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)


def test_success_and_failure_are_recorded() -> None:
    state = rec.update({}, {"Statistik": "success", "Linienprüfung": "failure"}, now=_T0, url="u")
    assert state["updated"] == "2026-10-28T11:00:00+00:00"
    assert state["steps"]["Statistik"] == {
        "outcome": "success", "at": "2026-10-28T11:00:00+00:00",
        "last_success": "2026-10-28T11:00:00+00:00", "run": "u",
    }
    assert state["steps"]["Linienprüfung"] == {
        "outcome": "failure", "at": "2026-10-28T11:00:00+00:00", "run": "u",
    }


def test_a_failure_keeps_the_last_success() -> None:
    first = rec.update({}, {"Statistik": "success"}, now=_T0, url="")
    later = rec.update(first, {"Statistik": "failure"}, now=_T0 + timedelta(minutes=30), url="")
    entry = later["steps"]["Statistik"]
    assert entry["outcome"] == "failure"
    assert entry["last_success"] == "2026-10-28T11:00:00+00:00"


@pytest.mark.parametrize("outcome", ["skipped", "cancelled", ""])
def test_a_step_that_did_not_run_keeps_its_record(outcome: str) -> None:
    failed = rec.update({}, {"Feed-Wächter": "failure"}, now=_T0, url="")
    later = rec.update(failed, {"Feed-Wächter": outcome}, now=_T0 + timedelta(minutes=30), url="")
    assert later["steps"]["Feed-Wächter"]["outcome"] == "failure"
    assert later["updated"] == "2026-10-28T11:30:00+00:00"


def test_a_malformed_pair_is_rejected() -> None:
    with pytest.raises(ValueError):
        rec.parse_outcomes(["Statistik"])


def test_the_link_names_this_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Origamihase/wien-oepnv")
    monkeypatch.setenv("GITHUB_RUN_ID", "40768232528")
    assert rec.run_url() == "https://github.com/Origamihase/wien-oepnv/actions/runs/40768232528"
    monkeypatch.setenv("GITHUB_RUN_ID", "x")
    assert rec.run_url() == ""


def test_the_written_record_turns_the_health_check_red_and_green_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorder's file as the Health check reads it, end to end."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    state = Path("data/side_steps/update-cycle.json")

    def run(*pairs: str) -> None:
        assert rec.main(["--state", str(state), *pairs]) == 0

    monkeypatch.setattr(hc, "SIDE_STEP_DIR", tmp_path / "data" / "side_steps")
    monkeypatch.setattr(hc, "SIDE_STEP_RECORDS", [("update-cycle.json", "Update-Lauf", 4.0)])
    run("Statistik=success", "Linienprüfung=failure", "Feed-Wächter=success")
    now = datetime.now(UTC)
    check = hc.check_side_steps(now)
    assert not check.ok
    assert "Linienprüfung (Update-Lauf) gescheitert" in check.summary
    run("Statistik=success", "Linienprüfung=success", "Feed-Wächter=skipped")
    assert hc.check_side_steps(datetime.now(UTC)).ok
    assert json.loads(state.read_text(encoding="utf-8"))["steps"]["Feed-Wächter"]["outcome"] == "success"


def test_the_workflows_record_every_side_step() -> None:
    """Each ``continue-on-error`` side step reaches the recorder by its ``outcome``."""
    cycle = (REPO / ".github/workflows/update-cycle.yml").read_text(encoding="utf-8")
    for step_id, label in (("stats", "Statistik"), ("lines", "Linienprüfung"), ("watch", "Feed-Wächter")):
        assert f"id: {step_id}\n" in cycle
        assert f"${{{{ steps.{step_id}.outcome }}}}" in cycle
        assert f'"{label}=$' in cycle
    assert "--state data/side_steps/update-cycle.json" in cycle
    assert cycle.index("Record side-step outcomes") < cycle.index("Validate and publish cycle outputs")
    stations = (REPO / ".github/workflows/update-stations.yml").read_text(encoding="utf-8")
    assert "id: hafas_lines\n" in stations
    assert "${{ steps.hafas_lines.outcome }}" in stations
    assert "--state data/side_steps/update-stations.json" in stations
    assert stations.index("Record side-step outcomes") < stations.index("Commit and push changes")
    # seo-guard.yml (Architektur-Review 2026-10-31, Fund B): the metadata step
    # fails on an empty or rejected REPO_ADMIN_TOKEN, its outcome is recorded
    # before the publish and rides the same commit.
    seo = (REPO / ".github/workflows/seo-guard.yml").read_text(encoding="utf-8")
    assert "id: metadata\n" in seo
    assert "${{ steps.metadata.outcome }}" in seo
    assert "--state data/side_steps/seo-guard.json" in seo
    assert seo.index("Record side-step outcomes") < seo.index("Publish sitemap.xml and llms.txt")
    assert "data/side_steps/seo-guard.json" in seo.split("Publish sitemap.xml and llms.txt", 1)[1].split("- name:", 1)[0]
    metadata_step = seo.split("id: metadata", 1)[1].split("- name:", 1)[0]
    assert 'if [[ -z "${ADMIN_TOKEN:-}" ]]' in metadata_step and "exit 1" in metadata_step
    assert "curl -fsS" in metadata_step
    files = {name for name, _workflow, _age in hc.SIDE_STEP_RECORDS}
    assert files == {"update-cycle.json", "update-stations.json", "seo-guard.json", "ki-waechter.json"}
    watcher = (REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8")
    assert "--state data/side_steps/ki-waechter.json" in watcher
    assert '"KI-Wächter=${outcome}"' in watcher
    assert watcher.index("Record side-step outcome") < watcher.index("Publish the audit file")

