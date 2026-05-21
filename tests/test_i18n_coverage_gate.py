# ruff: noqa: S603
"""Regression tests for ``scripts/check_i18n_coverage.py``.

The gate exists to catch the exact regression that left the dashboard
partially untranslated: a German UI string was added to ``site.html``
without its English counterpart in ``site.js``'s ``I18N_EN`` dict, and
the EN locale silently showed the German source. These tests pin the
detection contract so the gate keeps failing on missing translations
no matter how future maintainers reshape the HTML / JS.

The file-level ``# ruff: noqa: S603`` mirrors the established
convention (see ``tests/test_provider_plugins.py``): every
``subprocess.run`` here invokes either the real gate script or a
fixture copy whose path is built from a hard-coded ``Path`` literal,
never from user input.
"""
from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "check_i18n_coverage.py"


def _run_gate(fixture_root: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the *copied* gate inside ``fixture_root`` so the script's
    ``REPO_ROOT`` derivation (``Path(__file__).resolve().parents[1]``)
    resolves to the fixture, not the real repo."""
    return subprocess.run(  # nosec B603
        [
            sys.executable,
            str(fixture_root / "scripts" / "check_i18n_coverage.py"),
        ],
        cwd=fixture_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _make_repo_fixture(
    tmp_path: Path,
    html: str,
    js: str,
) -> Path:
    """Lay out a minimal repo fixture with the two files the gate
    inspects, then symlink the gate script so it runs against the
    fixture instead of the real repo."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "site.html").write_text(html, encoding="utf-8")
    assets = docs / "assets"
    assets.mkdir()
    (assets / "site.js").write_text(js, encoding="utf-8")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    # Copy the gate so ``REPO_ROOT`` inside the script resolves to the
    # fixture, not the real repo.
    (scripts_dir / "check_i18n_coverage.py").write_bytes(GATE.read_bytes())
    return tmp_path


def test_gate_passes_when_every_key_is_translated(tmp_path: Path) -> None:
    fixture = _make_repo_fixture(
        tmp_path,
        html='<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        js='const I18N_EN = {\n  "hello": "Hello",\n};\n',
    )
    result = _run_gate(fixture)
    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout


def test_gate_fails_on_missing_translation(tmp_path: Path) -> None:
    fixture = _make_repo_fixture(
        tmp_path,
        html='<html><body><h2 data-i18n="hello">Hallo</h2><p data-i18n="world">Welt</p></body></html>',
        js='const I18N_EN = {\n  "hello": "Hello",\n};\n',
    )
    result = _run_gate(fixture)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "world" in result.stderr
    assert "missing" in result.stderr.lower() or "no matching" in result.stderr.lower()


def test_gate_fails_on_empty_translation(tmp_path: Path) -> None:
    fixture = _make_repo_fixture(
        tmp_path,
        html='<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        js='const I18N_EN = {\n  "hello": "",\n};\n',
    )
    result = _run_gate(fixture)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "empty" in result.stderr.lower()
    assert "hello" in result.stderr


def test_gate_treats_data_i18n_html_marker_as_non_key(tmp_path: Path) -> None:
    """``data-i18n-html="1"`` is a boolean marker, not a translation
    key. The gate must not try to look up ``"1"`` in I18N_EN."""
    fixture = _make_repo_fixture(
        tmp_path,
        html='<html><body><p data-i18n="lead-html" data-i18n-html="1">Hallo</p></body></html>',
        js='const I18N_EN = {\n  "lead-html": "Hello world",\n};\n',
    )
    result = _run_gate(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"1"' not in result.stderr


def test_gate_recognises_aria_label_title_content_variants(tmp_path: Path) -> None:
    fixture = _make_repo_fixture(
        tmp_path,
        html=(
            '<html><body>'
            '<div data-i18n-aria-label="aria-key">x</div>'
            '<button data-i18n-title="title-key">x</button>'
            '<meta data-i18n-content="content-key" content="x">'
            '</body></html>'
        ),
        js=(
            'const I18N_EN = {\n'
            '  "aria-key": "A",\n'
            '  "title-key": "T",\n'
            '  "content-key": "C",\n'
            '};\n'
        ),
    )
    result = _run_gate(fixture)
    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_skips_orphan_when_key_is_consumed_programmatically(
    tmp_path: Path,
) -> None:
    """A key that appears in ``I18N_EN`` AND somewhere else in the JS
    source as a string literal (e.g. ``statusText("status-ok")``) is
    NOT an orphan."""
    fixture = _make_repo_fixture(
        tmp_path,
        html='<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        js=(
            'const I18N_EN = {\n'
            '  "hello": "Hello",\n'
            '  "status-ok": "Live feed updated.",\n'
            '};\n'
            'function setStatus() { statusText("status-ok"); }\n'
        ),
    )
    result = _run_gate(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    # ``status-ok`` is consumed by the function literal — not an
    # orphan — and must not appear in the dead-code note.
    assert "status-ok" not in result.stdout


def test_gate_flags_genuine_orphan_as_note_only(tmp_path: Path) -> None:
    """An I18N_EN key that is referenced nowhere else is reported as a
    note (informational), but the gate still exits 0."""
    fixture = _make_repo_fixture(
        tmp_path,
        html='<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        js=(
            'const I18N_EN = {\n'
            '  "hello": "Hello",\n'
            '  "really-dead": "Unused",\n'
            '};\n'
        ),
    )
    result = _run_gate(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "really-dead" in result.stdout
    assert "Note" in result.stdout or "note" in result.stdout


def test_gate_passes_on_real_repo() -> None:
    """Smoke test: the real ``docs/site.html`` + ``docs/assets/site.js``
    must satisfy the gate on every CI run."""
    result = subprocess.run(  # nosec B603
        [sys.executable, str(GATE)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"i18n gate failed on the real repo:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.parametrize(
    "missing_attr",
    ["data-i18n", "data-i18n-aria-label", "data-i18n-title", "data-i18n-content"],
)
def test_gate_catches_drift_in_every_attribute_variant(
    tmp_path: Path, missing_attr: str
) -> None:
    """Each of the four attribute variants must surface a missing
    translation independently."""
    fixture = _make_repo_fixture(
        tmp_path,
        html=f'<html><body><div {missing_attr}="orphan-key">x</div></body></html>',
        js='const I18N_EN = {};\n',
    )
    result = _run_gate(fixture)
    assert result.returncode == 1, (
        f"Gate did not catch drift on {missing_attr}; "
        f"stdout={result.stdout}, stderr={result.stderr}"
    )
    assert "orphan-key" in result.stderr
