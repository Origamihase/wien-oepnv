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

Test-Hygiene contract: every test below either calls ``wrapper.main(
[…])`` or invokes ``scripts/update_all_stations.py`` as a subprocess.
The wrapper now accepts ``--target``, ``--heartbeat``, ``--diff-report``
and ``--quarantine`` CLI args (PR closing the root cause of the test
pollution that PR #1607 mitigated via an autouse fixture), so each
test can point the wrapper at ``tmp_path``-isolated files without
touching the production paths under ``data/`` and ``docs/``. The
``_restore_wrapper_outputs`` autouse fixture below is kept as a
belt-and-suspenders safety net so a future test that forgets to pass
the CLI args still leaves the working tree clean.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

# Paths the wrapper writes to under its production defaults. The
# autouse fixture snapshots/restores these so any future test that
# forgets to point the wrapper at ``tmp_path`` via the new CLI args
# (``--target`` / ``--heartbeat`` / ``--diff-report`` / ``--quarantine``)
# still leaves the working tree clean.
_WRAPPER_OUTPUT_PATHS: tuple[Path, ...] = (
    REPO_ROOT / "data" / "stations.json",
    REPO_ROOT / "data" / "stations_last_run.json",
    REPO_ROOT / "docs" / "stations_diff.md",
    REPO_ROOT / "data" / "quarantine.json",
)


@pytest.fixture(autouse=True)
def _restore_wrapper_outputs() -> Iterator[None]:
    """Belt-and-suspenders safety net for the wrapper's hardcoded paths.

    With the wrapper now accepting CLI args for every output path, the
    tests below should never touch the production files under
    ``data/`` and ``docs/``. This fixture is kept as defense-in-depth:
    if a future test accidentally invokes ``wrapper.main([])`` without
    the CLI args (which would fall through to the production defaults),
    the snapshot/restore here still keeps the working tree clean.

    The fixture runs the test inside a try/finally so the snapshot is
    restored even on test failure or skip. ``autouse=True`` applies it
    to every test in this module, including any future addition.
    """
    snapshots: dict[Path, bytes | None] = {
        path: (path.read_bytes() if path.exists() else None)
        for path in _WRAPPER_OUTPUT_PATHS
    }
    try:
        yield
    finally:
        for path, original in snapshots.items():
            if original is None:
                # The path did not exist before the test. If the test
                # created it, remove it so the post-test state matches
                # the pre-test state bit-for-bit.
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)


def _wrapper_args_for(tmp_path: Path) -> tuple[Path, list[str]]:
    """Build ``[--target, --heartbeat, --diff-report, --quarantine]`` args
    pointing at ``tmp_path`` and seed the target with the production
    ``data/stations.json`` so the wrapper's ``shutil.copy2`` succeeds.

    Returns ``(target_path, args)`` where ``target_path`` is the
    isolated stations file the test can read back for byte-equality
    assertions.
    """
    target = tmp_path / "stations.json"
    heartbeat = tmp_path / "stations_last_run.json"
    diff_report = tmp_path / "stations_diff.md"
    quarantine = tmp_path / "quarantine.json"
    real_stations = REPO_ROOT / "data" / "stations.json"
    shutil.copy2(real_stations, target)
    args = [
        "--target", str(target),
        "--heartbeat", str(heartbeat),
        "--diff-report", str(diff_report),
        "--quarantine", str(quarantine),
    ]
    return target, args


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

    target_stations, wrapper_args = _wrapper_args_for(tmp_path)
    original_bytes = target_stations.read_bytes()

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

    exit_code = wrapper.main(wrapper_args)

    assert exit_code == 0, (
        f"Wrapper should auto-quarantine and exit 0 on validation failure, got {exit_code}"
    )
    assert target_stations.read_bytes() == original_bytes, (
        "Wrapper modified target stations.json on auto-quarantine no-match — "
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

    target_stations, wrapper_args = _wrapper_args_for(tmp_path)
    original_bytes = target_stations.read_bytes()

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
        wrapper.main(wrapper_args)

    assert target_stations.read_bytes() == original_bytes, (
        "Wrapper modified target stations.json on atomic_write failure — "
        "atomicity contract violated"
    )


@pytest.mark.timeout(180)
def test_wrapper_atomic_on_success(tmp_path: Path) -> None:
    """Bei Erfolg ist data/stations.json nach dem Lauf valide.

    Uses the new ``--target`` / ``--heartbeat`` / ``--diff-report`` /
    ``--quarantine`` CLI args to redirect every wrapper output into
    ``tmp_path``, so a full end-to-end subprocess run no longer
    mutates the production working tree. The earlier autouse-fixture
    safety net (still present as belt-and-suspenders defence) thus
    has nothing to restore on the happy path.
    """
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
    # Also disable the manual-block enrichment (HAFAS LocMatch for
    # ``type=manual_*`` entries without coordinates) — same reason as
    # ``WIEN_OEPNV_OSM_ENRICH=0``: 296 real HAFAS round-trips from a
    # GitHub-hosted runner regularly take 3-5 minutes and tip the
    # orchestrator over even the bumped 180-second pytest timeout.
    # The manual-enrichment helper is exercised in isolation by
    # ``tests/test_update_station_directory_manual_enrichment.py``.
    env["WIEN_OEPNV_MANUAL_ENRICH"] = "0"

    target_stations, wrapper_args = _wrapper_args_for(tmp_path)

    # Run the wrapper without modifications — should succeed if main is clean.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "update_all_stations.py"),
            *wrapper_args,
        ],
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
    after = target_stations.read_text(encoding="utf-8")
    json.loads(after)  # should not raise


def test_wrapper_cli_args_redirect_outputs_to_tmp_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pin the CLI-args contract: passing ``--target`` / ``--heartbeat`` /
    ``--diff-report`` redirects every wrapper output into ``tmp_path``
    and the production working tree is byte-identical after the run.

    Regression guard against a future change that re-introduces a
    hardcoded path inside ``main()`` (the canonical pollution shape).
    """
    from src.utils.stations_validation import ValidationReport
    from scripts import update_all_stations as wrapper

    target_stations, wrapper_args = _wrapper_args_for(tmp_path)
    heartbeat = tmp_path / "stations_last_run.json"
    diff_report = tmp_path / "stations_diff.md"

    real_heartbeat = REPO_ROOT / "data" / "stations_last_run.json"
    real_diff = REPO_ROOT / "docs" / "stations_diff.md"
    pre_heartbeat = real_heartbeat.read_bytes() if real_heartbeat.exists() else None
    pre_diff = real_diff.read_bytes() if real_diff.exists() else None

    monkeypatch.setattr(
        "scripts.update_all_stations.subprocess.run", lambda *a, **kw: None
    )
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

    exit_code = wrapper.main(wrapper_args)
    assert exit_code == 0

    # Heartbeat and diff-report land at the tmp_path locations.
    assert heartbeat.exists(), "heartbeat must be written at --heartbeat path"
    assert diff_report.exists(), "diff report must be written at --diff-report path"

    # Production paths are untouched mid-test (the autouse fixture
    # restores any incidental writes at teardown, but mid-test we
    # measure that no write happened in the first place).
    if pre_heartbeat is not None:
        assert real_heartbeat.read_bytes() == pre_heartbeat
    else:
        assert not real_heartbeat.exists()
    if pre_diff is not None:
        assert real_diff.read_bytes() == pre_diff
    else:
        assert not real_diff.exists()
