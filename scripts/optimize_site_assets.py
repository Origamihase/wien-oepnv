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

# Pillow 10 renamed the resampling enum; expose a stable alias so the
# downscale step works on both modern (``Image.Resampling.LANCZOS``)
# and legacy (``_LANCZOS``) installations.
_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS

LOG = logging.getLogger("optimize_site_assets")

ASSETS_DIR = REPO_ROOT / "docs" / "assets"

CSS_SRC = ASSETS_DIR / "site.css"
CSS_OUT = ASSETS_DIR / "site.min.css"
JS_SRC = ASSETS_DIR / "site.js"
JS_OUT = ASSETS_DIR / "site.min.js"

TRAIN_PNG = ASSETS_DIR / "train.png"
TRAIN_WEBP = ASSETS_DIR / "train.webp"
FOOTER_JPG = ASSETS_DIR / "footer-bg.jpg"
FOOTER_WEBP = ASSETS_DIR / "footer-bg.webp"

HEADER_COMMENT = "/* Wien ÖPNV – Live-Dashboard | MIT License */\n"

# Display target: ``.trainline`` is at most 2.5rem (40px) tall, so even
# at 3x DPR a 224px-tall source comfortably exceeds the pixel grid. We
# halve the original 3168x448 sprite to 1584x224 — same aspect ratio
# (≈7.07:1), one quarter of the original area.
TRAIN_TARGET_SIZE = (1584, 224)

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
WEBP_ENCODE_METHOD = 6  # slowest/best compression; runs once per source change


def _render_min_css() -> str:
    # rcssmin/rjsmin ship without type stubs; ``str()`` makes the
    # Any -> str boundary explicit so strict mypy is happy without an
    # inline type-ignore.
    return HEADER_COMMENT + str(rcssmin.cssmin(CSS_SRC.read_text(encoding="utf-8")))


def _render_min_js() -> str:
    return HEADER_COMMENT + str(rjsmin.jsmin(JS_SRC.read_text(encoding="utf-8")))


def _minify_css(check_only: bool = False) -> bool:
    """Regenerate ``site.min.css``. In check mode return False on drift."""
    expected = _render_min_css()
    current = CSS_OUT.read_text(encoding="utf-8") if CSS_OUT.exists() else ""
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
    current = JS_OUT.read_text(encoding="utf-8") if JS_OUT.exists() else ""
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
    LOG.info(
        "train.webp: %d bytes (%.1f%% of train.png)",
        TRAIN_WEBP.stat().st_size,
        100 * TRAIN_WEBP.stat().st_size / max(after, 1),
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
