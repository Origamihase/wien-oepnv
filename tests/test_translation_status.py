"""A translation model that cannot load must be noticed (decision 2026-11-01).

Dependabot #2114 (``transformers`` 5) removed the pipeline task the feed used.
The load failed, the EN feed fell back to German for every new item, and the
only trace was a WARNING line: the PR check "build" and the tests stayed green.
Since 2026-11-01 every build that needs the model writes
``data/translation_status.json`` and the Health check shows it as
``Übersetzung``: a note for one failed build, red from the second in a row
(Michael, card "Soll ich den Umstieg auf transformers 5 als eigenen PR
bauen?": "Ja, Umstieg bauen").

Mutations checked against this file (each one caught, by the test named):

* a build that did not need the model overwrites the record → ``test_a_build_without_the_model_writes_nothing``.
* the failure counter does not count up or reset → ``test_failed_builds_count_up_and_reset``.
* the Health check is red after one failed build → ``test_one_failed_build_is_a_note``.
* the Health check stays green after two → ``test_two_failed_builds_in_a_row_are_red``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import scripts.health_check as health_check
import src.build_feed as build_feed

NOW = datetime(2026, 11, 1, 13, 30, tzinfo=UTC)


@pytest.fixture
def status_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # validate_path accepts the cwd under pytest
    (tmp_path / "data").mkdir()
    path = tmp_path / "data" / "translation_status.json"
    monkeypatch.setenv("TRANSLATION_STATUS_PATH", str(path))
    return path


def _state(monkeypatch: pytest.MonkeyPatch, *, pipeline: Any, failed: bool, error: str | None) -> None:
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "pipeline", pipeline)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "load_failed", failed)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "error", error)


def _record(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_a_build_without_the_model_writes_nothing(
    status_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_path.write_text('{"loaded": false, "failed_builds": 1}', encoding="utf-8")
    _state(monkeypatch, pipeline=None, failed=False, error=None)
    build_feed._record_translation_status(NOW)
    assert _record(status_path) == {"loaded": False, "failed_builds": 1}


def test_failed_builds_count_up_and_reset(status_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    error = "ImportError: The task translation_de_to_en has been removed"
    _state(monkeypatch, pipeline=None, failed=True, error=error)
    build_feed._record_translation_status(NOW)
    first = _record(status_path)
    assert (first["loaded"], first["failed_builds"], first["error"]) == (False, 1, error)
    build_feed._record_translation_status(NOW)
    assert _record(status_path)["failed_builds"] == 2

    _state(monkeypatch, pipeline=object(), failed=False, error=None)
    build_feed._record_translation_status(NOW)
    ok = _record(status_path)
    assert (ok["loaded"], ok["failed_builds"], ok["error"]) == (True, 0, None)
    assert ok["revision"] == build_feed._TRANSLATION_MODEL_REVISION
    assert ok["updated"] == "2026-11-01T13:30:00+00:00"


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: dict[str, Any] | None) -> Any:
    path = tmp_path / "translation_status.json"
    if record is not None:
        path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(health_check, "TRANSLATION_STATUS", path)
    return health_check.check_translation(NOW)


def test_a_loaded_model_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _check(tmp_path, monkeypatch, {"updated": "2026-11-01T13:00:00+00:00", "loaded": True, "failed_builds": 0})
    assert (check.ok, check.note) == (True, False)
    assert check.summary.startswith("OK")


def test_one_failed_build_is_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _check(tmp_path, monkeypatch, {"updated": "2026-11-01T13:00:00+00:00", "loaded": False,
                                           "failed_builds": 1, "error": "torch missing"})
    assert (check.ok, check.note) == (True, True)
    assert check.detail == "torch missing"


def test_two_failed_builds_in_a_row_are_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _check(tmp_path, monkeypatch, {"updated": "2026-11-01T13:00:00+00:00", "loaded": False,
                                           "failed_builds": 2, "error": "ImportError: gone"})
    assert check.ok is False
    assert "2 Builds in Folge" in check.summary
    assert check.detail == "ImportError: gone"


def test_a_missing_record_is_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _check(tmp_path, monkeypatch, None)
    assert (check.ok, check.note) == (True, True)
