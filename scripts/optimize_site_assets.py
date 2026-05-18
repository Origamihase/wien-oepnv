#!/usr/bin/env python3
"""Optimise the static assets served from ``docs/`` for GitHub Pages.

This script produces minified copies of the dashboard's hand-maintained
``site.css`` / ``site.js`` sources and re-encodes the two large binary
assets (``train.png`` and ``footer-bg.jpg``) so the production payload
shrinks without altering the visual result.

Run it whenever ``docs/assets/site.css``, ``docs/assets/site.js`` or one
of the two image originals changes::

    python scripts/optimize_site_assets.py

Outputs (always overwritten):

* ``docs/assets/site.min.css``      — minified CSS referenced by ``docs/site.html``
* ``docs/assets/site.min.js``       — minified JS referenced by ``docs/site.html``
* ``docs/assets/train.png``         — re-encoded in place (downscaled + palette)
* ``docs/assets/footer-bg.jpg``     — re-encoded in place (downscaled + progressive)

The CSS/JS minification uses pure-Python (``rcssmin`` / ``rjsmin``) and
runs everywhere. Image optimisation requires ``pngquant``, ``optipng``
and ``jpegoptim`` on ``PATH``; if any is missing the binary is left
untouched and a warning is printed (Pillow handles the resize step on
its own).

The script is idempotent: subsequent runs against an unchanged source
tree produce byte-identical output (modulo the embedded "saved by"
markers from the image tools, which are intentionally stripped via
``--strip``/``--strip-all``).
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess  # nosec B404
import sys
import warnings
from pathlib import Path

try:
    import rcssmin
    import rjsmin
except ImportError as exc:  # pragma: no cover - exercised only when deps missing
    sys.stderr.write(
        f"Missing minifier dependency: {exc}.\n"
        "Install with: pip install rcssmin rjsmin Pillow\n"
    )
    raise SystemExit(2) from exc

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        f"Missing imaging dependency: {exc}.\n"
        "Install with: pip install Pillow\n"
    )
    raise SystemExit(2) from exc

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Project-wide sanitising logging — see
# ``tests/test_sentinel_preflight_basicconfig_drift.py`` for the invariant
# that forbids ``logging.basicConfig`` in scripts/. ``setup_script_logging``
# installs the same ``SafeFormatter`` the production feed builder uses, so
# hostile log content (e.g. an attacker-controlled URL in a subprocess error)
# can't bypass scrubbing on the way to stderr.
from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.utils.files import read_capped_text  # noqa: E402

# Pillow 10 renamed the resampling enum; expose a stable alias so the
# downscale step works on both modern (``Image.Resampling.LANCZOS``)
# and legacy (``_LANCZOS``) installations.
_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS

LOG = logging.getLogger("optimize_site_assets")

# Security: per-loader byte cap for the four CSS/JS file reads
# (``CSS_SRC``/``JS_SRC`` sources + ``CSS_OUT``/``JS_OUT`` minified
# outputs). Pre-fix every site used the unsafe ``Path.read_text(
# encoding="utf-8")`` shape with NO size cap — a planted multi-GiB
# CSS/JS file (hostile PR, compromised ``main`` checkout, partial flush
# + power loss) buffered via ``read_text()`` allocates O(file_size)
# bytes and raises :exc:`MemoryError`. ``MemoryError`` is a
# :class:`BaseException` subclass that propagates past every
# ``except OSError`` / ``except ValueError`` handler in the pre-commit
# pipeline and aborts the ``site-assets-minified`` hook
# (``.pre-commit-config.yaml`` invokes ``optimize_site_assets.py
# --check`` on every commit that touches the dashboard assets). The
# 4-MiB cap is ~200x the largest legitimate asset (``site.js`` at
# ~25 KiB) — generous enough to absorb any future hand-edit while still
# rejecting GiB-sized planted attacks. Mirrors
# ``scripts/check_complexity.py:MAX_BASELINE_FILE_BYTES`` (the closest
# canonical sibling — both are repo-internal small text files that
# would never legitimately exceed 1 MiB).
MAX_CSS_FILE_BYTES = 4 * 1024 * 1024

# Security: decompression-bomb cap for ``PIL.Image.open()`` calls in
# the image optimisation flow. The two committed assets are tiny by
# any standard (``train.png`` at 1584×224 = ~355 K pixels,
# ``footer-bg.jpg`` at 1920×1080 = ~2 M pixels). PIL's default
# ``MAX_IMAGE_PIXELS`` is ~89 M pixels (= ~358 MiB at RGBA 8-bit) — a
# ~40x overshoot of the legitimate ceiling and the default policy
# emits a non-fatal :class:`DecompressionBombWarning` at that threshold
# (only escalating to :class:`DecompressionBombError` at 2 × ~89 M =
# ~178 M pixels). A hostile PR replacing one of the committed assets
# with a small file that DECLARES dimensions of 50 000 × 50 000
# pixels (= 2.5 B pixels, ~10 GiB if decoded) would:
#   * Sail past the warning (non-fatal by default).
#   * Trigger the error gate at the second open call — but only AFTER
#     ``img.resize()`` materialises the 100-M-pixel buffer for the
#     first warning-bracket bomb and OOMs the host.
# Pinning ``Image.MAX_IMAGE_PIXELS`` to 25 M (~12x the largest
# legitimate asset) AND promoting :class:`DecompressionBombWarning` to
# an error closes both shapes — every bomb declaring more pixels than
# the cap is rejected at the very first ``Image.open()`` call (the
# header parse runs the bomb check before any pixel buffer is
# allocated).
MAX_IMAGE_PIXELS = 25_000_000

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
warnings.filterwarnings("error", category=Image.DecompressionBombWarning)


def _read_capped_or_die(path: Path, label: str) -> str:
    """Read *path* via :func:`read_capped_text`, exiting on cap violation.

    Used for the SOURCE assets (``CSS_SRC`` / ``JS_SRC``) where a
    cap-violation return of ``None`` cannot be recovered from — the
    minified output cannot be rendered without the source. The exit is
    deliberately loud (non-zero) so the pre-commit hook surfaces the
    planted-huge-file shape instead of silently rendering an empty
    output bundle.
    """
    content = read_capped_text(
        path, MAX_CSS_FILE_BYTES, label=label, logger=LOG
    )
    if content is None:
        raise SystemExit(
            f"{label} at {path} exceeds the {MAX_CSS_FILE_BYTES}-byte cap or "
            "is unreadable; refusing to process."
        )
    return content


def _read_capped_or_empty(path: Path, label: str) -> str:
    """Read *path* via :func:`read_capped_text`, returning ``""`` on
    missing / oversized / unreadable.

    Used for the OUTPUT assets (``CSS_OUT`` / ``JS_OUT``) where a
    cap-violation simply means the committed minified bundle is corrupt
    or oversized — equivalent to drift from the freshly-rendered
    expected output. In check-mode the comparison reports drift; in
    regen-mode the oversized file is overwritten with the smaller
    correct output.
    """
    if not path.exists():
        return ""
    content = read_capped_text(
        path, MAX_CSS_FILE_BYTES, label=label, logger=LOG
    )
    return content if content is not None else ""


ASSETS_DIR = REPO_ROOT / "docs" / "assets"

CSS_SRC = ASSETS_DIR / "site.css"
CSS_OUT = ASSETS_DIR / "site.min.css"
JS_SRC = ASSETS_DIR / "site.js"
JS_OUT = ASSETS_DIR / "site.min.js"

TRAIN_PNG = ASSETS_DIR / "train.png"
TRAIN_WEBP = ASSETS_DIR / "train.webp"
TRAIN_SMALL_WEBP = ASSETS_DIR / "train-small.webp"
FOOTER_JPG = ASSETS_DIR / "footer-bg.jpg"
FOOTER_WEBP = ASSETS_DIR / "footer-bg.webp"

HEADER_COMMENT = "/* Wien ÖPNV – Live-Dashboard | MIT License */\n"

# Display target: ``.trainline`` is at most 2.5rem (40px) tall, so even
# at 3x DPR a 224px-tall source comfortably exceeds the pixel grid. We
# halve the original 3168x448 sprite to 1584x224 — same aspect ratio
# (≈7.07:1), one quarter of the original area.
TRAIN_TARGET_SIZE = (1584, 224)

# Narrow-viewport variant served via ``<picture><source media="(max-width: 799px)">``.
# Below 800 CSS px the trainline strip falls to the ``clamp(...)`` minimum
# of 1.5rem (24 CSS px); at 3x DPR that's a 72-device-px-tall source. The
# 108 px target gives ~50% headroom while still bringing the file down
# from ~57 KiB to ~12 KiB. Same aspect ratio (≈7.07:1) as the main
# sprite so the rendered geometry is byte-identical to ``train.webp``.
TRAIN_SMALL_TARGET_SIZE = (768, 108)

# ``footer-bg.jpg`` is rendered behind a 78–94 % dark overlay, so a
# 1920x1080 base with aggressive JPEG quantisation is visually
# indistinguishable from the 2732x1536 original at the supported
# viewport sizes (the rendered region never exceeds 100vw × footer-h).
FOOTER_TARGET_SIZE = (1920, 1080)
FOOTER_JPEG_QUALITY = 72

# Lossless WebP for ``train.webp``: the source is a tight palette PNG
# whose every pixel matters once the train scrolls into view, and
# Pillow's lossless mode still beats the PNG by ~15 %. The JPEG-backed
# ``footer-bg.webp`` is lossy at a quality bracket that matches the
# JPEG visually (the 78–94 % overlay erases any residual artefacts).
# Both variants are served via ``<picture>``/``image-set()`` with the
# original file as the universal fallback — no behaviour change for
# browsers without WebP support.
FOOTER_WEBP_QUALITY = 75

# ``train-small.webp`` is rendered at 24 CSS px tall on narrow viewports
# (1.5rem floor of the ``clamp(1.5rem, 4vw, 2.5rem)`` height). The
# downscale from 1584×224 to 768×108 inevitably anti-aliases the tight
# palette, leaving few exact pixel matches — which makes lossless WebP
# fall back to almost-pass-through encoding. Lossy at q=90 is visually
# indistinguishable at 24 px tall and shrinks the file ~4× versus the
# lossless variant (~13 KiB vs ~55 KiB at the same dimensions).
TRAIN_SMALL_WEBP_QUALITY = 90
WEBP_ENCODE_METHOD = 6  # slowest/best compression; runs once per source change


def _render_min_css() -> str:
    # rcssmin/rjsmin ship without type stubs; ``str()`` makes the
    # Any -> str boundary explicit so strict mypy is happy without an
    # inline type-ignore.
    return HEADER_COMMENT + str(
        rcssmin.cssmin(_read_capped_or_die(CSS_SRC, "site.css"))
    )


def _render_min_js() -> str:
    return HEADER_COMMENT + str(
        rjsmin.jsmin(_read_capped_or_die(JS_SRC, "site.js"))
    )


def _minify_css(check_only: bool = False) -> bool:
    """Regenerate ``site.min.css``. In check mode return False on drift."""
    expected = _render_min_css()
    current = _read_capped_or_empty(CSS_OUT, "site.min.css")
    if check_only:
        ok = current == expected
        if not ok:
            LOG.error(
                "site.min.css is out of date — run "
                "`python scripts/optimize_site_assets.py --skip-images`",
            )
        return ok
    CSS_OUT.write_text(expected, encoding="utf-8")
    LOG.info(
        "CSS: %d -> %d bytes (%.1f%% smaller)",
        CSS_SRC.stat().st_size,
        len(expected),
        100 * (1 - len(expected) / max(CSS_SRC.stat().st_size, 1)),
    )
    return True


def _minify_js(check_only: bool = False) -> bool:
    """Regenerate ``site.min.js``. In check mode return False on drift."""
    expected = _render_min_js()
    current = _read_capped_or_empty(JS_OUT, "site.min.js")
    if check_only:
        ok = current == expected
        if not ok:
            LOG.error(
                "site.min.js is out of date — run "
                "`python scripts/optimize_site_assets.py --skip-images`",
            )
        return ok
    JS_OUT.write_text(expected, encoding="utf-8")
    LOG.info(
        "JS:  %d -> %d bytes (%.1f%% smaller)",
        JS_SRC.stat().st_size,
        len(expected),
        100 * (1 - len(expected) / max(JS_SRC.stat().st_size, 1)),
    )
    return True


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _run(cmd: list[str], *, tolerate: tuple[int, ...] = ()) -> None:
    """Run an external optimiser, treating ``tolerate``d exit codes as success.

    pngquant in particular exits non-zero (codes 98/99) when it cannot
    improve a file further — perfectly normal on follow-up runs once
    the palette has already been minimised. Other failures still abort
    the script.
    """
    LOG.debug("running: %s", " ".join(cmd))
    # ``cmd`` is always a hard-coded argv list (an optimiser binary +
    # flag pairs + an absolute path under ASSETS_DIR), never user
    # input — no shell, no untrusted args.
    result = subprocess.run(cmd, check=False)  # nosec B603  # noqa: S603
    if result.returncode != 0 and result.returncode not in tolerate:
        raise subprocess.CalledProcessError(result.returncode, cmd)


def _needs_downscale(path: Path, target: tuple[int, int]) -> bool:
    """True if ``path`` is wider than ``target[0]`` (cheap natural-size check)."""
    with Image.open(path) as img:
        return img.size[0] > target[0]


def _optimise_train_png() -> None:
    if not TRAIN_PNG.exists():
        LOG.warning("Skipping train.png (file missing).")
        return
    before = TRAIN_PNG.stat().st_size

    # Re-encode through Pillow only when downscaling is actually needed.
    # ``pngquant`` is mostly deterministic on an unchanged palette PNG,
    # but Pillow's save() pass introduces small variance on every call
    # (zlib stream rewrite). Skipping it when the dimensions already
    # match the target keeps the script idempotent on follow-up runs.
    if _needs_downscale(TRAIN_PNG, TRAIN_TARGET_SIZE):
        with Image.open(TRAIN_PNG) as img:
            resized = img.resize(TRAIN_TARGET_SIZE, _LANCZOS)
            resized.save(TRAIN_PNG, format="PNG", optimize=True)
        LOG.debug("train.png resized to %s.", TRAIN_TARGET_SIZE)

    # Aggressive palette quantisation — RGBA train sprite collapses to
    # a tight palette without visible banding at display size. Using a
    # single ``--quality`` value (rather than a min-max range) makes
    # pngquant idempotent on follow-up runs: once the palette is at
    # the target quality it refuses to recompress further. Exit codes
    # 98/99 are tolerated for the same reason (``--skip-if-larger``
    # triggered or "could not be optimised further" — both expected on
    # an already-optimised input, and both leave the file untouched).
    if _have("pngquant"):
        _run([
            "pngquant",
            "--quality", "65",
            "--speed", "1",
            "--strip",
            "--skip-if-larger",
            "--force",
            "--output", str(TRAIN_PNG),
            str(TRAIN_PNG),
        ], tolerate=(98, 99))
    else:
        LOG.warning("pngquant not on PATH; skipping palette quantisation.")

    if _have("optipng"):
        # ``-fix`` lets optipng quietly rewrite the tRNS chunk that
        # pngquant emits with reduced alpha precision — without it,
        # optipng treats the warning as fatal and exits non-zero.
        _run(["optipng", "-o7", "-strip", "all", "-fix", "-quiet", str(TRAIN_PNG)])
    else:
        LOG.warning("optipng not on PATH; skipping deflate re-encode.")

    after = TRAIN_PNG.stat().st_size
    LOG.info(
        "train.png: %d -> %d bytes (saved %d, %.1f%%)",
        before, after, max(before - after, 0),
        100 * (1 - after / max(before, 1)),
    )

    # Sibling WebP for browsers that announce ``image/webp`` via Accept.
    # ``docs/site.html`` serves it through ``<picture><source>`` with the
    # PNG as fallback, so the visual result is unchanged on engines
    # without WebP. Lossless mode preserves the palette PNG exactly
    # while still shaving ~15 % off the wire.
    with Image.open(TRAIN_PNG) as png:
        # Palette PNGs save as palette WebPs by default, which the libwebp
        # encoder cannot really compress. Promote to RGBA so the lossless
        # transforms have room to work.
        rgba = png.convert("RGBA") if png.mode == "P" else png
        rgba.save(
            TRAIN_WEBP,
            format="WEBP",
            lossless=True,
            method=WEBP_ENCODE_METHOD,
        )

        # Narrow-viewport sibling. The ``<picture><source media>`` order in
        # ``docs/site.html`` is:
        #     1. small WebP via ``media="(max-width: 799px)"``
        #     2. main WebP (this file, unconditional)
        #     3. PNG (universal fallback)
        # which keeps the main sprite as the desktop / DPR-safe source
        # while saving ~40+ KiB on every mobile load. Lossy q=90 is used
        # here (the main variant is lossless) because the resize
        # introduces anti-aliasing the palette PNG cannot represent —
        # see ``TRAIN_SMALL_WEBP_QUALITY`` for the full reasoning.
        small = rgba.resize(TRAIN_SMALL_TARGET_SIZE, _LANCZOS)
        small.save(
            TRAIN_SMALL_WEBP,
            format="WEBP",
            quality=TRAIN_SMALL_WEBP_QUALITY,
            method=WEBP_ENCODE_METHOD,
        )
    LOG.info(
        "train.webp: %d bytes (%.1f%% of train.png)",
        TRAIN_WEBP.stat().st_size,
        100 * TRAIN_WEBP.stat().st_size / max(after, 1),
    )
    LOG.info(
        "train-small.webp: %d bytes (%.1f%% of train.webp)",
        TRAIN_SMALL_WEBP.stat().st_size,
        100 * TRAIN_SMALL_WEBP.stat().st_size / max(TRAIN_WEBP.stat().st_size, 1),
    )


def _optimise_footer_jpg() -> None:
    if not FOOTER_JPG.exists():
        LOG.warning("Skipping footer-bg.jpg (file missing).")
        return
    before = FOOTER_JPG.stat().st_size

    # Same idempotency guard as for the PNG: a JPEG decode/re-encode
    # cycle is technically lossy, so we only do it when downscaling
    # is required. Already-correctly-sized files go straight to the
    # lossless ``jpegoptim`` pass.
    if _needs_downscale(FOOTER_JPG, FOOTER_TARGET_SIZE):
        with Image.open(FOOTER_JPG) as img:
            resized = img.resize(FOOTER_TARGET_SIZE, _LANCZOS)
            if resized.mode != "RGB":
                resized = resized.convert("RGB")
            resized.save(
                FOOTER_JPG,
                format="JPEG",
                quality=FOOTER_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
        LOG.debug("footer-bg.jpg resized to %s.", FOOTER_TARGET_SIZE)

    if _have("jpegoptim"):
        _run([
            "jpegoptim",
            f"--max={FOOTER_JPEG_QUALITY}",
            "--strip-all",
            "--all-progressive",
            "--quiet",
            "--force",
            str(FOOTER_JPG),
        ])
    else:
        LOG.warning("jpegoptim not on PATH; skipping metadata strip.")

    after = FOOTER_JPG.stat().st_size
    LOG.info(
        "footer-bg.jpg: %d -> %d bytes (saved %d, %.1f%%)",
        before, after, max(before - after, 0),
        100 * (1 - after / max(before, 1)),
    )

    # Sibling WebP for the CSS ``image-set()`` declaration. The dark
    # gradient overlay masks subtle WebP artefacts so a relatively
    # aggressive quality value still matches the JPEG visually.
    with Image.open(FOOTER_JPG) as jpg:
        rgb = jpg.convert("RGB") if jpg.mode != "RGB" else jpg
        rgb.save(
            FOOTER_WEBP,
            format="WEBP",
            quality=FOOTER_WEBP_QUALITY,
            method=WEBP_ENCODE_METHOD,
        )
    LOG.info(
        "footer-bg.webp: %d bytes (%.1f%% of footer-bg.jpg)",
        FOOTER_WEBP.stat().st_size,
        100 * FOOTER_WEBP.stat().st_size / max(after, 1),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Only (re)generate the minified CSS/JS bundles.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify the committed minified bundles match what would be "
            "generated from the current sources; exit non-zero on drift. "
            "Always implies --skip-images (image optimisation is lossy "
            "and not reproducible across tool versions)."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Emit debug-level logs.",
    )
    args = parser.parse_args(argv)

    setup_script_logging(logging.DEBUG if args.verbose else logging.INFO)

    if args.check:
        css_ok = _minify_css(check_only=True)
        js_ok = _minify_js(check_only=True)
        return 0 if (css_ok and js_ok) else 1

    _minify_css()
    _minify_js()

    if not args.skip_images:
        _optimise_train_png()
        _optimise_footer_jpg()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
