"""Regression tests for ``scripts/check_i18n_coverage.py``.

The gate exists to catch the exact regression that left the dashboard
partially untranslated: a German UI string was added to ``site.html``
without its English counterpart in ``site.js``'s ``I18N_EN`` dict, and
the EN locale silently showed the German source. These tests pin the
detection contract so the gate keeps failing on missing translations
no matter how future maintainers reshape the HTML / JS.

The tests load the script in-process via ``importlib`` and exercise
its public helpers directly. The previous subprocess-based fixture
collided with pytest-cov's ``branch=True`` setting (the sub-Python
processes wrote statement-mode coverage data that ``cov.combine()``
could not merge), so the suite now stays inside one Python process.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_i18n_coverage.py"


def _load_gate() -> ModuleType:
    """Import ``scripts/check_i18n_coverage.py`` as a module so the
    tests can exercise its functions in-process."""
    spec = importlib.util.spec_from_file_location(
        "check_i18n_coverage", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_i18n_coverage"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _write_fixture(
    tmp_path: Path, html: str, js: str
) -> tuple[Path, Path]:
    docs = tmp_path / "docs"
    docs.mkdir()
    html_path = docs / "site.html"
    html_path.write_text(html, encoding="utf-8")
    assets = docs / "assets"
    assets.mkdir()
    js_path = assets / "site.js"
    js_path.write_text(js, encoding="utf-8")
    return html_path, js_path


def _run_gate_against(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    html_path: Path,
    js_path: Path,
) -> tuple[int, str, str]:
    """Invoke ``gate.main()`` with the path globals pointing at the
    fixture and return ``(exit_code, stdout, stderr)``."""
    monkeypatch.setattr(gate, "HTML_PATH", html_path)
    monkeypatch.setattr(gate, "JS_PATH", js_path)
    capsys.readouterr()  # drain any earlier output
    exit_code = gate.main()
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


# --- Pure-helper tests ------------------------------------------------


def test_extract_html_keys_picks_up_every_attribute_variant() -> None:
    html = (
        '<div data-i18n="text-key">x</div>'
        '<div data-i18n-aria-label="aria-key">y</div>'
        '<button data-i18n-title="title-key">z</button>'
        '<meta data-i18n-content="content-key" content="x">'
        '<a data-i18n-href="feed-href" data-href-de="a" data-href-en="b">r</a>'
    )
    keys = gate._extract_html_keys(html)
    assert keys == {
        "text-key",
        "aria-key",
        "title-key",
        "content-key",
        "feed-href",
    }


def test_extract_html_keys_excludes_boolean_marker() -> None:
    """``data-i18n-html="1"`` is a boolean opt-in for innerHTML
    rewriting, not a translation key. The extractor must NOT register
    ``"1"`` as a key."""
    html = '<p data-i18n="lead" data-i18n-html="1">x</p>'
    assert gate._extract_html_keys(html) == {"lead"}


def test_extract_js_keys_parses_inline_dict() -> None:
    js = (
        "const I18N_EN = {\n"
        '  "hello": "Hello",\n'
        '  "lead": "Welcome",\n'
        "};\n"
    )
    entries = gate._extract_js_keys(js)
    assert set(entries.keys()) == {"hello", "lead"}


def test_extract_js_keys_handles_nested_braces() -> None:
    """The dict-block extractor counts braces, so a value containing
    inline ``{ … }`` (e.g. a template string with placeholders) must
    not truncate the block early."""
    js = (
        "const I18N_EN = {\n"
        '  "hello": "Hello",\n'
        '  "lead": "Welcome ${name}",\n'  # contains ``{`` inside value
        '  "extra": "ok",\n'
        "};\n"
    )
    entries = gate._extract_js_keys(js)
    assert set(entries.keys()) == {"hello", "lead", "extra"}


def test_value_is_empty_detects_double_and_single_quoted_empty() -> None:
    assert gate._value_is_empty('""')
    assert gate._value_is_empty("''")
    assert gate._value_is_empty(' "" ')
    assert not gate._value_is_empty('"hi"')
    assert not gate._value_is_empty("'world'")


def test_js_source_minus_dict_strips_the_block() -> None:
    js = (
        "const HEADER = 'pre';\n"
        "const I18N_EN = {\n"
        '  "hello": "Hello",\n'
        "};\n"
        "function go(){ statusText('hello'); }\n"
    )
    stripped = gate._js_source_minus_dict(js)
    # The dict block is removed.
    assert '"hello": "Hello"' not in stripped
    # Surrounding code is preserved (used by the orphan detector).
    assert "statusText('hello')" in stripped
    assert "HEADER = 'pre'" in stripped


# --- End-to-end ``main()`` tests via monkeypatch ----------------------


def test_gate_passes_when_every_key_is_translated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path, js_path = _write_fixture(
        tmp_path,
        '<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        'const I18N_EN = {\n  "hello": "Hello",\n};\n',
    )
    exit_code, stdout, stderr = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 0, stderr
    assert "passed" in stdout


def test_gate_fails_on_missing_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path, js_path = _write_fixture(
        tmp_path,
        (
            '<html><body>'
            '<h2 data-i18n="hello">Hallo</h2>'
            '<p data-i18n="world">Welt</p>'
            '</body></html>'
        ),
        'const I18N_EN = {\n  "hello": "Hello",\n};\n',
    )
    exit_code, _, stderr = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 1
    assert "world" in stderr
    assert "no matching" in stderr.lower() or "missing" in stderr.lower()


def test_gate_fails_on_empty_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path, js_path = _write_fixture(
        tmp_path,
        '<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        'const I18N_EN = {\n  "hello": "",\n};\n',
    )
    exit_code, _, stderr = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 1
    assert "empty" in stderr.lower()
    assert "hello" in stderr


def test_gate_treats_data_i18n_html_marker_as_non_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path, js_path = _write_fixture(
        tmp_path,
        (
            '<html><body>'
            '<p data-i18n="lead-html" data-i18n-html="1">Hallo</p>'
            '</body></html>'
        ),
        'const I18N_EN = {\n  "lead-html": "Hello world",\n};\n',
    )
    exit_code, stdout, stderr = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 0, stderr
    assert '"1"' not in stderr
    assert "passed" in stdout


def test_gate_recognises_every_attribute_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path, js_path = _write_fixture(
        tmp_path,
        (
            '<html><body>'
            '<div data-i18n-aria-label="aria-key">x</div>'
            '<button data-i18n-title="title-key">x</button>'
            '<meta data-i18n-content="content-key" content="x">'
            '</body></html>'
        ),
        (
            'const I18N_EN = {\n'
            '  "aria-key": "A",\n'
            '  "title-key": "T",\n'
            '  "content-key": "C",\n'
            '};\n'
        ),
    )
    exit_code, _, stderr = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 0, stderr


def test_gate_skips_orphan_when_key_is_consumed_programmatically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A key that appears in ``I18N_EN`` AND somewhere else in the JS
    source as a string literal (e.g. ``statusText("status-ok")``) is
    NOT an orphan."""
    html_path, js_path = _write_fixture(
        tmp_path,
        '<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        (
            'const I18N_EN = {\n'
            '  "hello": "Hello",\n'
            '  "status-ok": "Live feed updated.",\n'
            '};\n'
            'function setStatus() { statusText("status-ok"); }\n'
        ),
    )
    exit_code, stdout, _ = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 0
    # ``status-ok`` is consumed by the function literal — not an
    # orphan — and must not appear in the dead-code note.
    assert "status-ok" not in stdout


def test_gate_flags_genuine_orphan_as_note_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An I18N_EN key that is referenced nowhere else is reported as a
    note (informational), but the gate still exits 0."""
    html_path, js_path = _write_fixture(
        tmp_path,
        '<html><body><h2 data-i18n="hello">Hallo</h2></body></html>',
        (
            'const I18N_EN = {\n'
            '  "hello": "Hello",\n'
            '  "really-dead": "Unused",\n'
            '};\n'
        ),
    )
    exit_code, stdout, _ = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 0
    assert "really-dead" in stdout
    assert "ote" in stdout  # matches "Note" or "note"


@pytest.mark.parametrize(
    "missing_attr",
    [
        "data-i18n",
        "data-i18n-aria-label",
        "data-i18n-title",
        "data-i18n-content",
    ],
)
def test_gate_catches_drift_in_every_attribute_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_attr: str,
) -> None:
    """Each of the four attribute variants must surface a missing
    translation independently."""
    html_path, js_path = _write_fixture(
        tmp_path,
        f'<html><body><div {missing_attr}="orphan-key">x</div></body></html>',
        'const I18N_EN = {};\n',
    )
    exit_code, _, stderr = _run_gate_against(
        monkeypatch, capsys, html_path, js_path
    )
    assert exit_code == 1, (
        f"Gate did not catch drift on {missing_attr}: stderr={stderr}"
    )
    assert "orphan-key" in stderr


def test_gate_passes_on_real_repo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Smoke test: the real ``docs/site.html`` + ``docs/assets/site.js``
    must satisfy the gate on every CI run. Runs ``main()`` against
    the on-disk paths (no monkeypatch) so the test exercises the
    actual repo state."""
    capsys.readouterr()
    exit_code = gate.main()
    captured = capsys.readouterr()
    assert exit_code == 0, (
        f"i18n gate failed on the real repo:\n"
        f"stdout: {captured.out}\nstderr: {captured.err}"
    )


# Sanity: the module export surface stayed stable across the
# subprocess→in-process refactor.
def test_module_exports_expected_callables() -> None:
    for name in (
        "_extract_html_keys",
        "_extract_js_keys",
        "_extract_js_dict_block",
        "_value_is_empty",
        "_js_source_minus_dict",
        "main",
        "HTML_PATH",
        "JS_PATH",
    ):
        assert hasattr(gate, name), f"gate.{name} missing"
