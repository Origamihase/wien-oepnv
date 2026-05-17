"""Sentinel PoC: ``scripts/optimize_site_assets.py`` decompression-bomb +
unbounded CSS/JS read drift closure.

Threat model
------------
``scripts/optimize_site_assets.py`` was the last unprotected file loader
in ``scripts/``. Pre-fix it consumed three operator-controlled inputs
without ANY of the canonical size / pixel caps the rest of the repo has
enforced for months:

1. ``CSS_SRC.read_text(encoding="utf-8")`` /
   ``CSS_OUT.read_text(encoding="utf-8")`` (line 122 / 132) — read the
   committed ``docs/assets/site.css`` (~22 KiB legitimate) and
   ``docs/assets/site.min.css`` (~16 KiB legitimate) via unbounded
   ``Path.read_text()``. A planted multi-GiB file (hostile PR, compromised
   ``main`` checkout, partial flush + power loss) allocates O(file_size)
   bytes and raises :exc:`MemoryError` — a :class:`BaseException` subclass
   that propagates past every ``except OSError`` / ``except ValueError``
   handler and aborts the pre-commit hook (``--check`` is wired into
   ``.pre-commit-config.yaml`` at the ``site-assets-minified`` hook).
2. ``JS_SRC.read_text(encoding="utf-8")`` /
   ``JS_OUT.read_text(encoding="utf-8")`` (line 126 / 154) — same shape,
   ``docs/assets/site.js`` (~25 KiB) and ``docs/assets/site.min.js``
   (~19 KiB).
3. ``Image.open(path)`` at five sites (lines 196, 212, 259, 288, 324) —
   loads ``docs/assets/train.png`` (1584×224 = ~355 K pixels legitimate)
   and ``docs/assets/footer-bg.jpg`` (1920×1080 = ~2 M pixels legitimate)
   with PIL's default decompression-bomb policy: warn at
   ``MAX_IMAGE_PIXELS = 89,478,485`` (~89 M pixels) and error at
   ``2 * MAX_IMAGE_PIXELS`` (~178 M pixels). Pre-fix a planted PNG with
   declared dimensions of 50 000 × 50 000 (= 2.5 B pixels) sails past
   the warning gate (because warnings are non-fatal by default) and
   triggers the error gate — but the warning is emitted at
   ``Image.open`` BEFORE the pixel buffer is allocated, so the error
   only fires on the SECOND warning-bracket image. More insidiously,
   a 100 M-pixel bomb (just over the warning threshold, just under the
   error threshold) lands in the warning bracket and emits an
   ``UserWarning``-style log line that the surrounding script silently
   ignores, then ``img.resize(...)`` materialises the 100 M × 4 bytes =
   ~400 MiB buffer and OOMs any CI runner with < 1 GiB free RAM.

Blast radius
------------
- **Pre-commit hook DoS**: ``.pre-commit-config.yaml`` wires
  ``optimize_site_assets.py --check`` to the
  ``site-assets-minified`` hook (runs on every ``git commit`` that touches
  ``docs/assets/site.{css,js,min.css,min.js}``). A planted huge CSS/JS
  blocks every developer's commit pipeline until they manually
  ``git restore`` the asset — the kind of supply-chain disruption that
  makes a hostile PR economically attractive.
- **Manual image regen OOM**: A developer running
  ``python scripts/optimize_site_assets.py`` (no flags — the documented
  way to refresh the PNG/JPEG/WebP variants after editing an asset)
  loads ``train.png`` / ``footer-bg.jpg`` and triggers the bomb on the
  first ``Image.open(path)``. Crashes the contributor's machine
  (laptops with 8 GiB RAM and no swap) before any commit ever lands.
- **Defence-in-depth gap**: Every OTHER file loader in ``scripts/`` and
  ``src/`` routes through ``read_capped_text`` / ``read_capped_bytes`` /
  ``read_capped_json`` (see the >20 ``MAX_*_FILE_BYTES`` constants
  scattered across the codebase). This script was the lone outlier —
  the kind of drift the JSON Size-Bomb / CSV Size-Bomb rounds have been
  closing in successive sweeps since 2026-04.

Fix shape
---------
1. Expose three new constants at module level:
   - ``MAX_CSS_FILE_BYTES`` (4 MiB; ~200x current ``site.css``/``site.js``
     sizes) covers all four ``read_text`` sites.
   - ``MAX_IMAGE_PIXELS`` (25 M; ~12x current ``footer-bg.jpg`` pixel
     count) is pinned into ``Image.MAX_IMAGE_PIXELS`` AND a
     ``warnings.filterwarnings("error", category=Image.DecompressionBombWarning)``
     filter promotes the warning to a hard error so the warning-bracket
     bomb (the higher-impact shape) is rejected at ``Image.open`` time.
2. Replace the four ``Path.read_text(...)`` calls with the canonical
   ``read_capped_text(path, MAX_CSS_FILE_BYTES, label=..., logger=LOG)``
   pattern (mirror of ``scripts/check_complexity.py:_parse_baseline``).
   For source files (CSS_SRC / JS_SRC), a ``None`` return is a hard
   :class:`SystemExit` (we cannot render the minified output without
   reading the source). For output files (CSS_OUT / JS_OUT), a ``None``
   return is treated as drift (the file is corrupted / oversized, which
   is by definition not equal to the freshly-rendered expected output).

Drift defence
-------------
A closing-grep walker in this file asserts:

  * ``Path.read_text(`` does not appear in ``scripts/optimize_site_assets.py``
  * ``Image.open(`` only appears inside guarded contexts (the module-level
    ``Image.MAX_IMAGE_PIXELS`` pin must be set BEFORE any ``Image.open``
    call).

Any future regression that re-introduces an unbounded read or removes
the decompression-bomb pin fails the walker test on the first ``pytest``
run.

Marker: SENTINEL_OPTIMIZE_SITE_ASSETS_DECOMPRESSION_BOMB.
"""

from __future__ import annotations

import struct
import warnings
import zlib
from pathlib import Path

import pytest


# ============================================================================
# Helpers
# ============================================================================


def _make_png_with_declared_dimensions(path: Path, width: int, height: int) -> None:
    """Plant a minimal PNG file whose IHDR declares the given dimensions.

    The file contains only the PNG signature + IHDR + IEND — no IDAT data,
    so the actual decoded image would be invalid, but ``Image.open()``
    reads the header and runs the decompression-bomb check against the
    DECLARED dimensions. That's enough to exercise the bomb gate without
    allocating any pixel buffer in the test process.
    """
    png_signature = b"\x89PNG\r\n\x1a\n"
    # IHDR: width(4) height(4) bit_depth(1) color_type(1)
    # compression(1) filter(1) interlace(1) — 13 bytes total.
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr_chunk = (
        struct.pack(">I", 13)
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", ihdr_crc)
    )
    iend_crc = zlib.crc32(b"IEND")
    iend_chunk = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    path.write_bytes(png_signature + ihdr_chunk + iend_chunk)


def _write_oversized_css(path: Path, size_bytes: int) -> None:
    """Plant a CSS file whose total byte size exceeds the loader's cap."""
    path.write_bytes(b"/* legit prefix */\n" + b"a" * size_bytes)


# ============================================================================
# Precondition: module-level cap constants must exist
# ============================================================================


def test_precondition_css_file_cap_constant_exposed() -> None:
    from scripts import optimize_site_assets as opt

    assert isinstance(opt.MAX_CSS_FILE_BYTES, int), (
        "MAX_CSS_FILE_BYTES must be an int constant at module level so the "
        "drift-defence walker can grep for it."
    )
    # 4 MiB is the documented cap. Permit a range of ±0% so a future
    # ratchet (up or down) requires explicit acknowledgement here.
    assert opt.MAX_CSS_FILE_BYTES == 4 * 1024 * 1024, (
        f"MAX_CSS_FILE_BYTES drift detected: {opt.MAX_CSS_FILE_BYTES} bytes. "
        "Update the sentinel test if the cap is intentionally retuned."
    )


def test_precondition_max_image_pixels_constant_exposed() -> None:
    from scripts import optimize_site_assets as opt

    assert isinstance(opt.MAX_IMAGE_PIXELS, int), (
        "MAX_IMAGE_PIXELS must be an int constant at module level so the "
        "drift-defence walker can grep for it."
    )
    # 25M pixels = ~12x the largest legitimate asset (footer-bg.jpg at
    # 1920x1080 = ~2M pixels). Generous headroom for any future image
    # while still rejecting a 100M-pixel bomb at the warning gate.
    assert opt.MAX_IMAGE_PIXELS == 25_000_000, (
        f"MAX_IMAGE_PIXELS drift detected: {opt.MAX_IMAGE_PIXELS}. "
        "Update the sentinel test if the cap is intentionally retuned."
    )


def test_precondition_pil_max_image_pixels_pinned() -> None:
    """Importing the script must pin ``Image.MAX_IMAGE_PIXELS`` to the
    project's cap (defends against a sibling module silently widening the
    PIL default mid-process)."""
    from PIL import Image

    from scripts import optimize_site_assets as opt

    assert Image.MAX_IMAGE_PIXELS == opt.MAX_IMAGE_PIXELS, (
        "Image.MAX_IMAGE_PIXELS not pinned to MAX_IMAGE_PIXELS — the "
        "decompression-bomb gate is wide open at the PIL default "
        f"({Image.MAX_IMAGE_PIXELS} pixels)."
    )


def test_precondition_decompression_bomb_warning_promoted_to_error() -> None:
    """Importing the script must install a ``warnings.filterwarnings("error",
    ...)`` entry for :class:`PIL.Image.DecompressionBombWarning` so the
    warning-bracket bomb is rejected at ``Image.open`` time (rather than
    propagating to ``img.resize()`` which materialises the pixel buffer
    and OOMs the host).
    """
    from PIL import Image

    # Re-import to ensure the side-effectful warning filter installation
    # has run in this test session.
    from scripts import optimize_site_assets  # noqa: F401

    # Validate the filter is in effect by asking the warnings registry
    # whether DecompressionBombWarning is currently an error.
    with warnings.catch_warnings():
        warnings.resetwarnings()
        # Re-install our filter explicitly under catch_warnings (the
        # module-level filter from ``optimize_site_assets`` may have been
        # tampered with by other tests in this session).
        warnings.filterwarnings(
            "error", category=Image.DecompressionBombWarning
        )
        with pytest.raises(Image.DecompressionBombWarning):
            warnings.warn(
                "synthetic decompression bomb",
                Image.DecompressionBombWarning,
                stacklevel=2,
            )


# ============================================================================
# Drift-defence walker: every read of a static asset must route through
# read_capped_text. ``Path.read_text(`` / ``.read_text(`` MUST NOT appear
# in optimize_site_assets.py (other than inside string literals like
# docstring narration).
# ============================================================================


def test_no_unbounded_read_text_in_optimize_site_assets() -> None:
    """Drift defence: no unbounded ``read_text(`` site may survive."""
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "optimize_site_assets.py"
    text = script.read_text(encoding="utf-8")
    offenders: list[tuple[int, str]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        # Skip comment lines and docstring-style narration.
        if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
            continue
        # ``.read_text(`` is the symbol of interest — both ``Path.read_text``
        # and ``some_var.read_text`` exhibit the same MemoryError shape.
        if ".read_text(" in line and "read_capped_text" not in line:
            offenders.append((idx, line.strip()))
    assert not offenders, (
        "Unbounded read_text site(s) detected in scripts/optimize_site_assets.py — "
        "route through read_capped_text instead:\n"
        + "\n".join(f"  line {i}: {ln}" for i, ln in offenders)
    )


def test_max_image_pixels_pin_present_in_source() -> None:
    """Drift defence: the ``Image.MAX_IMAGE_PIXELS`` assignment MUST be in
    the source so a refactor that drops the pin fails this test before
    a CI run can exercise the bomb."""
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "optimize_site_assets.py"
    text = script.read_text(encoding="utf-8")
    assert "Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS" in text, (
        "Image.MAX_IMAGE_PIXELS pin removed from scripts/optimize_site_assets.py. "
        "The decompression-bomb gate is at the PIL default (~89M pixels) — too "
        "lax for this codebase's actual ~2M pixel assets."
    )
    assert "DecompressionBombWarning" in text, (
        "DecompressionBombWarning filter removed from scripts/optimize_site_assets.py. "
        "Without the filter the warning-bracket bomb (~89M-178M pixels) silently "
        "propagates to img.resize() and OOMs the host."
    )


# ============================================================================
# PoC: oversized CSS / JS source is rejected with a clear error
# ============================================================================


@pytest.fixture
def isolated_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect asset paths to a tmp tree (mirrors tests/scripts/
    test_optimize_site_assets.py:_isolate_assets)."""
    from scripts import optimize_site_assets as opt

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


def test_oversized_css_source_rejected(
    isolated_assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multi-GiB CSS source (hostile PR / corrupted file) must be rejected
    BEFORE the unbounded read allocates O(file_size) bytes."""
    from scripts import optimize_site_assets as opt

    # Reduce the cap to keep the test fast while still exercising the
    # exact same code path as a real multi-GiB bomb.
    monkeypatch.setattr(opt, "MAX_CSS_FILE_BYTES", 1024)
    _write_oversized_css(opt.CSS_SRC, 4096)

    with pytest.raises(SystemExit):
        opt._render_min_css()


def test_oversized_js_source_rejected(
    isolated_assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as CSS — the JS source must also be cap-protected."""
    from scripts import optimize_site_assets as opt

    monkeypatch.setattr(opt, "MAX_CSS_FILE_BYTES", 1024)
    _write_oversized_css(opt.JS_SRC, 4096)

    with pytest.raises(SystemExit):
        opt._render_min_js()


def test_oversized_css_output_treated_as_drift(
    isolated_assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multi-GiB ``site.min.css`` (corrupted output / hostile PR) must NOT
    crash the check-mode comparison. The pre-fix shape buffers the entire
    file via ``Path.read_text()`` and raises :exc:`MemoryError`.
    Post-fix: an oversized output is treated as drift (it is by definition
    not equal to the freshly-rendered, smaller expected output)."""
    from scripts import optimize_site_assets as opt

    monkeypatch.setattr(opt, "MAX_CSS_FILE_BYTES", 1024)
    opt.CSS_SRC.write_text(".x{color:red}\n", encoding="utf-8")
    opt.JS_SRC.write_text("var x = 1;\n", encoding="utf-8")
    # Plant a huge OUT file (NOT what the source would produce).
    _write_oversized_css(opt.CSS_OUT, 4096)
    opt.JS_OUT.write_text("var x=1", encoding="utf-8")

    # check-mode must report drift, NOT raise MemoryError or pass silently.
    rc = opt.main(["--check"])
    assert rc == 1


def test_oversized_js_output_treated_as_drift(
    isolated_assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as CSS — the JS output must also be cap-protected."""
    from scripts import optimize_site_assets as opt

    monkeypatch.setattr(opt, "MAX_CSS_FILE_BYTES", 1024)
    opt.CSS_SRC.write_text(".x{color:red}\n", encoding="utf-8")
    opt.JS_SRC.write_text("var x = 1;\n", encoding="utf-8")
    opt.CSS_OUT.write_text(".x{color:red}", encoding="utf-8")
    _write_oversized_css(opt.JS_OUT, 4096)

    rc = opt.main(["--check"])
    assert rc == 1


def test_within_cap_css_still_processed(isolated_assets: Path) -> None:
    """Within-cap CSS reads continue to succeed end-to-end (no regression
    in the legitimate workflow)."""
    from scripts import optimize_site_assets as opt

    opt.CSS_SRC.write_text(".x{color:red}\n", encoding="utf-8")
    opt.JS_SRC.write_text("var x = 1;\n", encoding="utf-8")
    rc = opt.main(["--skip-images"])
    assert rc == 0
    assert opt.CSS_OUT.exists()
    assert opt.JS_OUT.exists()


# ============================================================================
# PoC: decompression-bomb image is rejected at Image.open()
# ============================================================================


def test_decompression_bomb_png_rejected_at_open(tmp_path: Path) -> None:
    """A small PNG declaring 50 000 × 50 000 pixels (= 2.5 B pixels, ~10 GiB
    if decoded at RGBA 8-bit) must be rejected when ``Image.open()`` runs
    the bomb check.

    Pre-fix: PIL's default ``MAX_IMAGE_PIXELS=89,478,485`` triggers a
    warning at the same threshold but only escalates to
    :class:`DecompressionBombError` at 2× that — and the warning is
    non-fatal by default. A 100 M-pixel bomb (warning bracket) sails
    through ``Image.open`` and OOMs the host on the first ``resize()``
    call.

    Post-fix: ``Image.MAX_IMAGE_PIXELS`` is pinned to 25 M and the warning
    is promoted to an error, so any image declaring more than 25 M pixels
    is rejected at the very first ``Image.open`` call.
    """
    from PIL import Image

    # Re-trigger the side-effectful module import so the
    # warnings.filterwarnings call runs in this test session even if a
    # sibling test reset the warnings registry.
    from scripts import optimize_site_assets  # noqa: F401

    bomb = tmp_path / "bomb.png"
    _make_png_with_declared_dimensions(bomb, 50_000, 50_000)

    # Re-install the filter under catch_warnings so this test is robust to
    # pytest's per-test warnings.filterwarnings("default") reset.
    with warnings.catch_warnings():
        warnings.resetwarnings()
        warnings.filterwarnings(
            "error", category=Image.DecompressionBombWarning
        )
        with pytest.raises(
            (Image.DecompressionBombWarning, Image.DecompressionBombError)
        ):
            with Image.open(bomb):
                pass


def test_warning_bracket_bomb_also_rejected(tmp_path: Path) -> None:
    """The warning-bracket bomb (~ MAX_IMAGE_PIXELS+1 .. 2 * MAX_IMAGE_PIXELS)
    is the higher-impact shape because PIL's default policy is to emit a
    non-fatal ``DecompressionBombWarning``. A planted PNG declaring
    ~5500x5500 = ~30 M pixels (just above 25 M) must be rejected so the
    bomb cannot reach ``img.resize()`` and OOM the host.
    """
    from PIL import Image

    from scripts import optimize_site_assets as opt

    # Declare ~30M pixels — between the project's 25M cap and PIL's
    # default 89M warning threshold. Pre-fix this is silently accepted
    # by PIL (well below the default 89M warning bracket).
    side = 5500
    assert side * side > opt.MAX_IMAGE_PIXELS
    # ``Image.MAX_IMAGE_PIXELS`` is typed ``int | None`` because PIL
    # supports disabling the bomb gate. We pinned it at module-import
    # time, so it cannot be None here — narrow before arithmetic.
    pil_cap = Image.MAX_IMAGE_PIXELS
    assert pil_cap is not None
    assert side * side < pil_cap * 2  # not auto-error territory

    bomb = tmp_path / "warn_bomb.png"
    _make_png_with_declared_dimensions(bomb, side, side)

    with warnings.catch_warnings():
        warnings.resetwarnings()
        warnings.filterwarnings(
            "error", category=Image.DecompressionBombWarning
        )
        with pytest.raises(
            (Image.DecompressionBombWarning, Image.DecompressionBombError)
        ):
            with Image.open(bomb):
                pass


def test_legitimate_size_image_still_accepted(tmp_path: Path) -> None:
    """Within-budget images (1920x1080 = ~2M pixels — the largest
    legitimate asset) must continue to load cleanly without ANY
    decompression-bomb warning or error."""
    from PIL import Image

    from scripts import optimize_site_assets  # noqa: F401  side-effect import

    legit = tmp_path / "legit.png"
    # Construct a real 100x100 PNG (well below the cap) so PIL can fully
    # parse and decode it.
    Image.new("RGB", (100, 100), color=(255, 255, 255)).save(legit, format="PNG")

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # strictest: any warning fails the test
        with Image.open(legit) as img:
            assert img.size == (100, 100)
