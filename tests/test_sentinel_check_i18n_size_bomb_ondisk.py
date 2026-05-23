"""Sentinel PoC: size-bomb defence for the i18n coverage gate's on-disk
file reads.

Threat model
------------
``scripts/check_i18n_coverage.py`` is invoked from two pipelines:

  * The local pre-commit hook (``.pre-commit-config.yaml``), which runs
    on every commit that touches ``docs/site.html`` or
    ``docs/assets/site.js``.
  * The canonical CI gauntlet at ``scripts/run_static_checks.py``, which
    is itself invoked by ``.github/workflows/test.yml`` AND by the
    ``Run static checks`` step on every push / PR.

Pre-fix both file reads used the unsafe ``Path.read_text(encoding=
"utf-8")`` shape with NO byte-size cap. A planted multi-GiB
``docs/site.html`` or ``docs/assets/site.js`` (hostile PR replacing the
tracked source, compromised CI runner checkout, partial flush + power
loss mid-edit) buffered via ``read_text()`` allocates O(file_size) bytes
and raises :exc:`MemoryError`. ``MemoryError`` is a
:class:`BaseException` subclass — it is NOT caught by ``except OSError``
/ ``except ValueError`` / ``except Exception`` and propagates straight
out of ``main()`` past the pre-commit hook AND the static-checks gate,
crashing the entire CI pipeline.

The sibling script ``scripts/optimize_site_assets.py`` writes to the
exact same two files via :func:`atomic_write` and already routes its
reads through the canonical :func:`read_capped_text` defence helper
(``MAX_CSS_FILE_BYTES = 4 * 1024 * 1024``) — the i18n coverage gate is
the sibling-drift site that escaped the canonical inventory in the same
shape as the 2026-05-23 GeoNetz loader pair (``_load_geonetz_stops`` /
``extract_oebb_geonetz_stops``).

Post-fix both reads route through :func:`src.utils.files.read_capped_text`
with the same 4 MiB cap (``MAX_I18N_FILE_BYTES``), and the ``None``
return on cap violation is converted to a clean non-zero exit with a
clear operator message.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ============================================================================
# Precondition: the canonical cap constant exists
# ============================================================================


def test_precondition_i18n_size_cap_constant_exists() -> None:
    """Pin the canonical cap constant. If a future refactor renames or
    removes it, every regression test below would silently pass even on
    unfixed code — so we pin the precondition first."""
    from scripts import check_i18n_coverage as gate

    assert isinstance(gate.MAX_I18N_FILE_BYTES, int)
    assert gate.MAX_I18N_FILE_BYTES > 0
    # Cap must accommodate the largest legitimate committed source. Both
    # files were ~20-50 KiB at fix time; pinning ``>= 1 MiB`` leaves
    # generous headroom for legitimate growth while keeping the cap well
    # below any reasonable runner's cgroup memory limit.
    assert gate.MAX_I18N_FILE_BYTES >= 1_000_000


# ============================================================================
# PoC: a planted huge ``docs/site.html`` MUST be rejected
# ============================================================================


def test_i18n_gate_rejects_oversized_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pre-fix: ``HTML_PATH.read_text(encoding="utf-8")`` allocated
    O(file_size) bytes and raised ``MemoryError`` past every surrounding
    handler. Post-fix: ``read_capped_text`` rejects the file at the
    fstat-on-open size check and the gate returns 1 with a clear
    operator message — the pre-commit + static-checks pipeline surfaces
    the planted-huge-file shape instead of crashing."""
    from scripts import check_i18n_coverage as gate

    # Point the gate at our fixture paths and shrink the cap to a tiny
    # value so we don't have to materialise a multi-MiB test fixture.
    fake_html = tmp_path / "site.html"
    fake_js = tmp_path / "site.js"
    fake_html.write_bytes(b"x" * 2048)
    fake_js.write_text(
        'const I18N_EN = { "k": "v" };',
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "HTML_PATH", fake_html)
    monkeypatch.setattr(gate, "JS_PATH", fake_js)
    monkeypatch.setattr(gate, "MAX_I18N_FILE_BYTES", 1024, raising=False)

    rc = gate.main()
    captured = capsys.readouterr()

    # Post-fix contract: clean non-zero exit + a clear message — NOT a
    # MemoryError crash.
    assert rc == 1
    assert "cap" in captured.err.lower() or "unreadable" in captured.err.lower()


def test_i18n_gate_rejects_oversized_js(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sibling shape to the HTML test: an oversized JS file must be
    rejected with the same clean-exit contract. Pre-fix
    ``JS_PATH.read_text(encoding="utf-8")`` ran after the HTML read and
    therefore propagated ``MemoryError`` from the second buffer site
    even when the HTML was clean."""
    from scripts import check_i18n_coverage as gate

    fake_html = tmp_path / "site.html"
    fake_js = tmp_path / "site.js"
    # Small valid HTML so it passes the first cap check; huge JS so the
    # second cap check fires.
    fake_html.write_text(
        '<div data-i18n="k">x</div>',
        encoding="utf-8",
    )
    fake_js.write_bytes(b"x" * 2048)
    monkeypatch.setattr(gate, "HTML_PATH", fake_html)
    monkeypatch.setattr(gate, "JS_PATH", fake_js)
    monkeypatch.setattr(gate, "MAX_I18N_FILE_BYTES", 1024, raising=False)

    rc = gate.main()
    captured = capsys.readouterr()

    assert rc == 1
    assert "cap" in captured.err.lower() or "unreadable" in captured.err.lower()


def test_i18n_gate_normal_files_unaffected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sanity check: the size cap must not affect legitimate
    well-formed files. Pinned alongside the rejection tests so a future
    tightening that accidentally rejects valid input fails loudly."""
    from scripts import check_i18n_coverage as gate

    fake_html = tmp_path / "site.html"
    fake_js = tmp_path / "site.js"
    fake_html.write_text(
        '<div data-i18n="hello">Hallo</div>',
        encoding="utf-8",
    )
    fake_js.write_text(
        'const I18N_EN = { "hello": "Hello" };',
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "HTML_PATH", fake_html)
    monkeypatch.setattr(gate, "JS_PATH", fake_js)
    # Keep the production cap — a legitimate file is well under 4 MiB.

    rc = gate.main()
    captured = capsys.readouterr()

    assert rc == 0
    assert "passed" in captured.out


# ============================================================================
# Static-source invariant — catch a future refactor that re-introduces
# the unbounded ``path.read_text()`` shape
# ============================================================================


def test_main_routes_through_read_capped_text() -> None:
    """Pin the fix-shape invariant via the source itself. A future
    refactor that replaces ``read_capped_text`` with a bare
    ``path.read_text(...)`` would silently regress the MemoryError
    defence; this test fails loudly on that drift.

    Uses :mod:`ast` to inspect the function body (excluding the
    docstring) so the test does not collide with any narrative reference
    to the pre-fix unbounded shape inside a future docstring.
    """
    from scripts.check_i18n_coverage import main

    source = inspect.getsource(main)
    module = ast.parse(source)
    func_def = module.body[0]
    assert isinstance(func_def, ast.FunctionDef)

    call_names: set[str] = set()
    for node in ast.walk(func_def):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                call_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                call_names.add(func.attr)

    assert "read_capped_text" in call_names, (
        "main() must route both file reads through read_capped_text — "
        "the canonical defence helper enforces the TOCTOU-safe byte-size "
        "cap and avoids MemoryError propagation."
    )
    assert "read_text" not in call_names, (
        "main() must NOT call .read_text() directly — the unbounded "
        "shape allocates O(file_size) bytes and propagates MemoryError "
        "past the pre-commit + static-checks pipelines."
    )
    assert "read_bytes" not in call_names, (
        "main() must NOT call .read_bytes() directly either — the same "
        "MemoryError shape applies to the raw-bytes read path."
    )
