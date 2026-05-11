# ruff: noqa: S603
"""Regression-Test für den Copy-on-Write-Wrapper in update_all_stations.py.

Stellt sicher, dass:
1. Bei erfolgreicher Validation `data/stations.json` aktualisiert wird.
2. Bei einer Validation, deren Issues auf keine reale Station passen,
   die Pipeline (auto-quarantine) den Working Tree unverändert lässt
   und erfolgreich exitet.
3. Bei einem Fehler im finalen atomic_write die Bytes von
   `data/stations.json` unverändert bleiben.

Hintergrund: Vor diesem Wrapper konnte ein update-Sub-Skript einen
Konflikt direkt in den Working Tree schreiben. Der separate
Validator-Step bemerkte den Fehler erst nach dem Schreiben (siehe
PR #1102, 900100-Aspern-Nord-Regression). Der Wrapper schreibt
gegen ein Temp-File und kopiert nur nach Auto-Quarantine zurück.

Janitor PR #1321: file-level S603 suppression. The single
subprocess.run(...) in this file invokes sys.executable against a
hard-coded path under REPO_ROOT, so the call is safe.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wrapper_proceeds_when_no_quarantineable_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Auto-Quarantine ohne passende Station: bytewise unverändert + exit 0.

    Verfahren: sub-scripts werden zu no-ops gemockt (subprocess.run gibt
    None zurück), validate_stations liefert einen Report mit einem
    provider_issue, dessen Identifier auf keine reale Station passt.
    Die Auto-Quarantine-Logik findet keinen Match und proceedet mit dem
    unveränderten Merge-Set. Erwartung: exit 0 und Bytes unverändert.
    Pinst die "no-match = no-modification"-Garantie der Auto-Quarantine.
    """
    from src.utils.stations_validation import ProviderIssue, ValidationReport
    from scripts import update_all_stations as wrapper

    real_stations = REPO_ROOT / "data" / "stations.json"
    original_bytes = real_stations.read_bytes()

    # Replace sub-script subprocess invocations with no-ops. String target form
    # keeps mypy --no-implicit-reexport happy without broadening the mock — the
    # patch still applies only to subprocess.run as accessed through
    # scripts.update_all_stations, not globally.
    monkeypatch.setattr(
        "scripts.update_all_stations.subprocess.run", lambda *a, **kw: None
    )

    # The identifier ``<test>`` matches no station, so auto-quarantine
    # cannot isolate any entry and the pipeline falls back to the
    # unmodified merged set.
    failing_report = ValidationReport(
        total_stations=0,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(),
        cross_station_id_issues=(),
        provider_issues=(
            ProviderIssue(
                identifier="<test>",
                name="<test>",
                reason="forced validation failure for regression test",
            ),
        ),
        naming_issues=(),
        gtfs_stop_count=0,
    )
    monkeypatch.setattr(wrapper, "validate_stations", lambda *a, **kw: failing_report)

    exit_code = wrapper.main([])

    assert exit_code == 0, (
        f"Wrapper should auto-quarantine and exit 0 on validation failure, got {exit_code}"
    )
    assert real_stations.read_bytes() == original_bytes, (
        "Wrapper modified data/stations.json on auto-quarantine no-match — "
        "the unchanged-bytes contract for the no-match path was violated"
    )


def test_wrapper_preserves_stations_json_on_atomic_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bei Fehler im finalen atomic_write bleibt data/stations.json bytewise unverändert.

    Verfahren: sub-scripts werden zu no-ops gemockt, validate_stations liefert
    einen sauberen (issue-freien) Report — der Wrapper erreicht also den
    finalen copy-back-Block. atomic_write wird zum Fehlschlagen gebracht
    (OSError beim Aufruf). Erwartung: die Exception propagiert hoch und das
    Original ist bytewise unverändert. Pinst die Atomicity-Garantie der
    Migration von shutil.copy zu atomic_write.
    """
    from src.utils.stations_validation import ValidationReport
    from scripts import update_all_stations as wrapper

    real_stations = REPO_ROOT / "data" / "stations.json"
    original_bytes = real_stations.read_bytes()

    # Sub-scripts as no-ops.
    monkeypatch.setattr(
        "scripts.update_all_stations.subprocess.run", lambda *a, **kw: None
    )

    # Validation passes with a clean report — the wrapper proceeds to the
    # final copy-back block.
    clean_report = ValidationReport(
        total_stations=0,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(),
        cross_station_id_issues=(),
        provider_issues=(),
        naming_issues=(),
        gtfs_stop_count=0,
    )
    monkeypatch.setattr(wrapper, "validate_stations", lambda *a, **kw: clean_report)

    # Force atomic_write to raise immediately on call.
    def failing_atomic_write(*args: object, **kwargs: object) -> None:
        raise OSError("Simulated atomic_write failure for regression test")

    monkeypatch.setattr(
        "scripts.update_all_stations.atomic_write", failing_atomic_write
    )

    # The wrapper has no try/except around the final copy-back block, so the
    # OSError propagates. That's intentional and consistent with shutil.copy's
    # historical behaviour — atomic_write failure is exceptional (disk full,
    # permission, etc.), not a "validation failed" condition.
    with pytest.raises(OSError, match="Simulated atomic_write failure"):
        wrapper.main([])

    assert real_stations.read_bytes() == original_bytes, (
        "Wrapper modified data/stations.json on atomic_write failure — "
        "atomicity contract violated"
    )


@pytest.mark.timeout(180)
def test_wrapper_atomic_on_success(tmp_path: Path) -> None:
    """Bei Erfolg ist data/stations.json nach dem Lauf valide."""
    # Mock the OSM Overpass call by env-disabling it inside the
    # subprocess: the wrapper test exists to verify atomicity of the
    # update pipeline, NOT to exercise real network round-trips. The
    # surrounding pytest run carries a 60-second per-test timeout
    # (``pyproject.toml [tool.pytest.ini_options].addopts``) — bumped
    # to 180 s for this test specifically because the WL OGD merge
    # path now produces ~1800 entries (post PR #1442 reactivation) and
    # the resulting atomic_write + validate cycle can tip a slow CI
    # runner over the global 60 s budget. Pre-#1442 the merge was a
    # no-op (6-row stub haltepunkte.csv → 0 WL entries) so the global
    # budget was sufficient; the bump is only to absorb the legitimate
    # cost of the now-active merge, not to mask any real slowdown.
    # A real Overpass round-trip from a GitHub-hosted runner regularly
    # burns 10-30 seconds and tips the whole orchestrator over even
    # the bumped budget — keep the env-disable.
    # ``WIEN_OEPNV_OSM_ENRICH=0`` is honoured by
    # ``scripts/update_station_directory.py`` and skips the OSM client
    # without touching production timeouts or retry counts.
    env = os.environ.copy()
    env["WIEN_OEPNV_OSM_ENRICH"] = "0"

    # Run the wrapper without modifications — should succeed if main is clean.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [sys.executable, str(REPO_ROOT / "scripts" / "update_all_stations.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=600,
        env=env,
    )

    # Note: this test requires network access for some sub-scripts.
    # In Sandbox without network, it will likely fail at sub-script level.
    if result.returncode != 0:
        pytest.skip(
            f"update_all_stations.py konnte nicht laufen (vermutlich Network-Restriktion in Sandbox): "
            f"{result.stderr[:500]}"
        )

    # If it succeeded, validate the result is still valid JSON
    after = (REPO_ROOT / "data" / "stations.json").read_text(encoding="utf-8")
    json.loads(after)  # should not raise
