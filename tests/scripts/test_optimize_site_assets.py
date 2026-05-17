"""Tests for ``scripts/optimize_site_assets.py``.

The image-optimisation half (pngquant/optipng/jpegoptim) lives behind
``shutil.which`` guards in the script, so we don't exercise it here —
the tests focus on the CSS/JS pipeline, which is pure-Python and runs
deterministically across platforms.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import optimize_site_assets as opt


@pytest.fixture(autouse=True)
def _isolate_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the asset paths into a tmp tree so we never touch real files."""
    assets = tmp_path / "assets"
    assets.mkdir()
    css_src = assets / "site.css"
    js_src = assets / "site.js"
    css_out = assets / "site.min.css"
    js_out = assets / "site.min.js"
    monkeypatch.setattr(opt, "ASSETS_DIR", assets)
    monkeypatch.setattr(opt, "CSS_SRC", css_src)
    monkeypatch.setattr(opt, "JS_SRC", js_src)
    monkeypatch.setattr(opt, "CSS_OUT", css_out)
    monkeypatch.setattr(opt, "JS_OUT", js_out)
    return assets


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_minify_css_strips_comments_and_whitespace(_isolate_assets: Path) -> None:
    _write(
        opt.CSS_SRC,
        """
        /* this comment is stripped */
        .foo {
            color: red;
            background: blue;
        }
        """,
    )
    # JS pipeline runs unconditionally inside ``main``; provide a stub
    # so the test isolates the CSS path without exploding on missing src.
    _write(opt.JS_SRC, "var unused = 0;\n")
    rc = opt.main(["--skip-images"])
    assert rc == 0
    out = opt.CSS_OUT.read_text(encoding="utf-8")
    # Header comment is preserved; the inner one is gone.
    assert out.startswith(opt.HEADER_COMMENT)
    assert "this comment is stripped" not in out
    # Selector body collapsed to a single line.
    assert ".foo{color:red;background:blue}" in out
    # Output strictly shorter than source.
    assert len(out) < len(opt.CSS_SRC.read_text(encoding="utf-8"))


def test_minify_js_strips_comments_and_whitespace(_isolate_assets: Path) -> None:
    _write(opt.CSS_SRC, ".unused{color:red}\n")
    _write(
        opt.JS_SRC,
        """
        // strip me
        /* and me */
        "use strict";
        function add(a, b) {
            return a + b;
        }
        """,
    )
    rc = opt.main(["--skip-images"])
    assert rc == 0
    out = opt.JS_OUT.read_text(encoding="utf-8")
    assert out.startswith(opt.HEADER_COMMENT)
    assert "strip me" not in out
    assert "and me" not in out
    # rjsmin preserves the "use strict" directive.
    assert '"use strict"' in out
    assert "function add(a,b)" in out


def test_check_mode_passes_when_in_sync(_isolate_assets: Path) -> None:
    _write(opt.CSS_SRC, ".x{color:red}\n")
    _write(opt.JS_SRC, "var x = 1;\n")
    # Generate the canonical minified bundles, then verify --check is happy.
    assert opt.main(["--skip-images"]) == 0
    assert opt.main(["--check"]) == 0


def test_check_mode_fails_on_drift(
    _isolate_assets: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(opt.CSS_SRC, ".x{color:red}\n")
    _write(opt.JS_SRC, "var x = 1;\n")
    assert opt.main(["--skip-images"]) == 0

    # Mutate the JS source but leave the committed .min.js stale.
    _write(opt.JS_SRC, "var x = 2;\n")
    caplog.set_level("ERROR", logger=opt.LOG.name)
    assert opt.main(["--check"]) == 1
    assert any("out of date" in r.getMessage() for r in caplog.records)


def test_check_mode_reports_css_drift_independently(
    _isolate_assets: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(opt.CSS_SRC, ".x{color:red}\n")
    _write(opt.JS_SRC, "var x = 1;\n")
    assert opt.main(["--skip-images"]) == 0

    _write(opt.CSS_SRC, ".x{color:blue}\n")
    caplog.set_level("ERROR", logger=opt.LOG.name)
    assert opt.main(["--check"]) == 1
    # Both the CSS-specific reason AND the generic "out of date"
    # message land in the log — assert on the CSS path explicitly so
    # a future regression that silently drops the per-bundle message
    # would be caught.
    assert any("site.min.css" in r.getMessage() for r in caplog.records)


def test_render_helpers_are_pure(_isolate_assets: Path) -> None:
    """``_render_min_*`` must not touch the filesystem on its own."""
    _write(opt.CSS_SRC, ".x{color:red}\n")
    _write(opt.JS_SRC, "var x = 1;\n")
    assert not opt.CSS_OUT.exists()
    assert not opt.JS_OUT.exists()
    css = opt._render_min_css()
    js = opt._render_min_js()
    assert not opt.CSS_OUT.exists()
    assert not opt.JS_OUT.exists()
    assert ".x{color:red}" in css
    assert "var x=1" in js or "var x = 1" in js  # rjsmin keeps the var-decl spacing
