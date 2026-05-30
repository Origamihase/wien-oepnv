"""Hybrid + CI-lockdown path policy for operator and CI tools.

* CI tool (``src/cli.py stations validate --output``) is locked to the repo's
  ALLOWED_ROOTS via ``validate_path`` — covered in ``tests/test_cli.py``.
* Operator tools warn (but still write) when their output path escapes
  ALLOWED_ROOTS, via ``warn_if_outside_allowed_roots``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared helpers (src/feed/config.py) — the core every operator tool wires in.
# ---------------------------------------------------------------------------
def test_is_within_allowed_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.feed.config import is_within_allowed_roots

    monkeypatch.chdir(tmp_path)
    assert is_within_allowed_roots(Path("data/x.json")) is True
    assert is_within_allowed_roots(Path("docs/y.md")) is True
    assert is_within_allowed_roots(Path("/etc/passwd")) is False
    assert is_within_allowed_roots(tmp_path / "outside.json") is False


def test_warn_if_outside_allowed_roots_warns_and_returns_resolved(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from src.feed.config import warn_if_outside_allowed_roots

    logger = logging.getLogger("test.guardrail.outside")
    target = tmp_path / "x.json"
    with caplog.at_level(logging.WARNING, logger="test.guardrail.outside"):
        resolved = warn_if_outside_allowed_roots(target, logger=logger, label="--out")
    # The write still proceeds: the resolved path is returned, not rejected.
    assert resolved == target.resolve()
    assert any("outside the repository" in r.getMessage() for r in caplog.records)


def test_warn_if_outside_allowed_roots_silent_inside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from src.feed.config import warn_if_outside_allowed_roots

    monkeypatch.chdir(tmp_path)
    logger = logging.getLogger("test.guardrail.inside")
    with caplog.at_level(logging.WARNING, logger="test.guardrail.inside"):
        warn_if_outside_allowed_roots(Path("data/ok.json"), logger=logger, label="--out")
    assert not any("outside the repository" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Operator tool: configuration wizard OUT_PATH (and any kind="path" field).
# ---------------------------------------------------------------------------
def test_config_option_path_warns_outside_but_keeps_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.utils.configuration_wizard import ConfigOption

    option = ConfigOption(key="OUT_PATH", label="Output", help="...", kind="path")
    with caplog.at_level(logging.WARNING, logger="src.utils.configuration_wizard"):
        result = option.normalize("/tmp/outside.xml")
    # Value preserved (operator flexibility) ...
    assert result == "/tmp/outside.xml"
    # ... but the out-of-tree target is surfaced, tagged with the field key.
    assert any(
        "OUT_PATH" in r.getMessage() and "outside" in r.getMessage()
        for r in caplog.records
    )


def test_config_option_path_silent_for_in_tree(caplog: pytest.LogCaptureFixture) -> None:
    from src.utils.configuration_wizard import ConfigOption

    option = ConfigOption(key="OUT_PATH", label="Output", help="...", kind="path")
    with caplog.at_level(logging.WARNING, logger="src.utils.configuration_wizard"):
        result = option.normalize("data/feed.xml")
    assert result == "data/feed.xml"
    assert not any("outside" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Operator tool: sync_hafas_profile --output (via _write_profile).
# ---------------------------------------------------------------------------
def test_sync_hafas_write_profile_warns_outside_but_writes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import sync_hafas_profile

    out = tmp_path / "profile.json"
    with caplog.at_level(logging.WARNING, logger="places.hafas.sync"):
        sync_hafas_profile._write_profile({"value": 1}, out)
    assert out.exists()  # operator workflow preserved
    assert any("outside the repository" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Operator tool: fetch_google_places_stations --dump-new (via _dump_changes).
# ---------------------------------------------------------------------------
def test_fetch_google_dump_changes_warns_outside_but_writes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import fetch_google_places_stations

    out = tmp_path / "dump.json"
    with caplog.at_level(logging.WARNING, logger="places.cli"):
        fetch_google_places_stations._dump_changes(out, [], [])
    assert out.exists()
    assert any("outside the repository" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Operator tool: configure_feed --env-file (also fixes the absolute bypass).
# ---------------------------------------------------------------------------
def test_configure_feed_env_file_warns_outside_but_writes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Load via spec_from_file_location (not ``from scripts import configure_feed``)
    # so mypy does not statically follow into the module's pre-existing type debt.
    import importlib.util
    from typing import Any, cast

    script = Path(__file__).resolve().parents[1] / "scripts" / "configure_feed.py"
    spec = importlib.util.spec_from_file_location("configure_feed", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    outside = tmp_path / "feed.env"
    with caplog.at_level(logging.WARNING, logger="configure_feed"):
        rc = cast(Any, module).main(["--env-file", str(outside), "--accept-defaults"])
    assert rc == 0
    assert outside.exists()  # absolute out-of-tree path still written
    assert any(
        "--env-file" in r.getMessage() and "outside" in r.getMessage()
        for r in caplog.records
    )
