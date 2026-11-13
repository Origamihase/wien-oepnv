"""The yearly Baustellen ledger: the hook in ``scripts/update_baustellen_cache.py``.

When ``main()`` calls the writer, with which sites, and that a failure of
the ledger never changes the run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache
from src.utils import stats as stats_utils
from src.utils import stats_baustellen
from tests._baustellen_ledger_support import (
    _FEATURES,
    LEDGER_LOGGER,
    SCRIPT_LOGGER,
    _hook_at_now,
    _ledger,
    _live_answer,
    _no_live_answer,
    _raise_runtime_error,
    _real_cache,
    _real_ledgers,
    _Run,
    _written_ledger,
)
from tests.test_update_baustellen_cache._support import SAMPLE_PATH, _site


# --- the hook in scripts/update_baustellen_cache.py --------------------------


@pytest.mark.parametrize(
    ("fallback_layers", "exit_code"),
    # Exit 3: one layer came from its last good answer. Its sites are still
    # the city's own (not the demo sample), so they are entered as well.
    [([], 0), (["BAUSTELLENLINOGD"], 3)],
    ids=["live", "one-layer-from-its-last-good-answer"],
)
def test_hook_gets_the_sites_of_the_city_after_the_cache_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fallback_layers: list[str], exit_code: int
) -> None:
    run = _Run(monkeypatch)
    _live_answer(monkeypatch, _FEATURES)
    monkeypatch.setattr(update_baustellen_cache, "_fallback_layers", fallback_layers)

    assert update_baustellen_cache.main() == exit_code

    assert run.order == ["cache", "ledger"]
    args, kwargs = run.ledger[0]
    # Exactly what went into the cache: the sites that passed the transit rule.
    assert len(args) == 1 and args[0] is run.cached[0]
    assert kwargs == {}
    assert sorted(item["title"] for item in args[0]) == ["Gasse 1", "Hauptstraße"]
    assert len(_written_ledger(tmp_path)) == 2


def test_live_answer_ends_in_the_ledger_and_changes_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The real writer behind the real ``main()``: cache, exit code and log as before."""
    target = _real_cache(monkeypatch, tmp_path)
    _live_answer(monkeypatch, _FEATURES)
    _hook_at_now(monkeypatch)  # both ticks in one year, whenever the test runs
    before = _real_ledgers()
    caplog.set_level(logging.INFO)

    assert update_baustellen_cache.main() == 0
    assert update_baustellen_cache.main() == 0  # the next tick adds nothing

    records = _written_ledger(tmp_path)
    by_title = {r["title"]: r for r in records}
    assert sorted(by_title) == ["Gasse 1", "Hauptstraße"] and len(records) == 2
    # ``_site``: OBJEKT_BEGINN "2026-09-30Z" is Vienna midnight of 1 October.
    assert (by_title["Gasse 1"]["starts_at"], by_title["Gasse 1"]["ends_at"]) == ("2026-10-01", "2026-11-15")
    assert by_title["Gasse 1"]["description"] == "Die Haltestelle der Linie 13A wird verlegt."
    cached = json.loads(target.read_text(encoding="utf-8"))
    assert sorted(item["guid"] for item in cached) == sorted(r["guid"] for r in records)
    assert _real_ledgers() == before
    script_lines = [r.getMessage() for r in caplog.records if r.name == SCRIPT_LOGGER]
    assert script_lines.count("Baustellen: Cache mit 2 Einträgen aktualisiert.") == 2
    assert [r for r in caplog.records if r.name in (LEDGER_LOGGER, "utils.stats")] == []


@pytest.mark.parametrize(
    ("cache_exists", "fallback", "exit_code", "cache_writes"),
    [
        # Exit 2: the bundled sample is written to an empty checkout's cache;
        # its two sites from 2025 are no answer of the city.
        (False, str(SAMPLE_PATH), 2, 1),
        (True, str(SAMPLE_PATH), 1, 0),
        # Inside the repository (a path outside falls back to the bundled
        # sample), and not there: only looked up, never written.
        (False, "data/samples/gibt_es_nicht_baustellen.geojson", 1, 0),
    ],
    ids=["demo-sample", "cache-kept", "no-sample"],
)
def test_hook_is_not_called_without_a_live_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cache_exists: bool, fallback: str, exit_code: int, cache_writes: int
) -> None:
    run = _Run(monkeypatch)
    _no_live_answer(monkeypatch, cache_exists=cache_exists, fallback=fallback)

    assert update_baustellen_cache.main() == exit_code
    assert len(run.cached) == cache_writes
    assert run.ledger == []
    assert not (tmp_path / "stats").exists()


def test_demo_sample_refused_by_the_cache_guard_is_not_entered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``write_cache`` refuses the sample (``DataDegradationError``): exit 1, no ledger call."""
    target = _real_cache(monkeypatch, tmp_path)
    target.write_text(json.dumps([{"guid": f"old-{i}"} for i in range(30)]), encoding="utf-8")
    calls: list[Any] = []
    monkeypatch.setattr(update_baustellen_cache, "append_baustellen_rows", calls.append)
    _no_live_answer(monkeypatch, cache_exists=False, fallback=str(SAMPLE_PATH))
    assert update_baustellen_cache.main() == 1
    assert calls == []


@pytest.mark.parametrize(
    ("features", "exit_code", "order"),
    [
        # The city delivers no site at all: an outage, the cache is kept.
        ([], 1, []),
        # Sites delivered, none with a transit effect: nothing to enter.
        ([_site("Gasse 9", "Die Fahrbahn wird eingeengt.")], 0, ["cache", "ledger"]),
    ],
    ids=["no-site", "empty-selection"],
)
def test_nothing_to_enter_leaves_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, features: list[dict[str, Any]], exit_code: int, order: list[str]
) -> None:
    run = _Run(monkeypatch)
    _live_answer(monkeypatch, features)

    assert update_baustellen_cache.main() == exit_code
    assert run.order == order
    assert not (tmp_path / "stats").exists()


@pytest.mark.parametrize("failure", ["folder", "writer", "header"])
def test_a_ledger_failure_never_changes_the_run(
    failure: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = _real_cache(monkeypatch, tmp_path)
    _live_answer(monkeypatch, _FEATURES)
    _hook_at_now(monkeypatch)
    if failure == "folder":
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(stats_utils, "DEFAULT_STATS_DIR", blocker / "stats")
    elif failure == "writer":
        monkeypatch.setattr(stats_baustellen, "_append_row", _raise_runtime_error)
    else:
        (tmp_path / "stats").mkdir()
        _ledger(tmp_path / "stats").write_text("fremd\n", encoding="utf-8")
    caplog.set_level(logging.INFO)

    assert update_baustellen_cache.main() == 0

    assert len(json.loads(target.read_text(encoding="utf-8"))) == 2
    script_lines = [r.getMessage() for r in caplog.records if r.name == SCRIPT_LOGGER]
    assert script_lines[-1] == "Baustellen: Cache mit 2 Einträgen aktualisiert."
    # The failure is said once, by the statistics loggers, at WARNING.
    said = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(said) == 1 and said[0].name in (LEDGER_LOGGER, "utils.stats")
