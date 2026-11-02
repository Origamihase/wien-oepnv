"""Readable sources of the dashboard script and stylesheet, in parts.

Since 2026-11-02 no file may have more than 700 lines. The dashboard's
stylesheet is kept as parts, ``docs/assets/site-css/teil-NN.css``; joined in
name order they are the whole source, and ``optimize_site_assets.py`` joins
them the same way into ``site.min.css``, the only stylesheet the page loads.
A single file instead of a folder is read as it is, so callers and tests can
still point at one file (the script ``docs/assets/site.js`` for now).
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.utils.files import read_capped_text

_REPO_ROOT = Path(__file__).resolve().parents[2]
# The script is still one file; it becomes modules in its own step
# (Michael 2026-11-02: "Echte Module").
SITE_JS_DIR = _REPO_ROOT / "docs" / "assets" / "site.js"
SITE_CSS_DIR = _REPO_ROOT / "docs" / "assets" / "site-css"


def source_parts(path: Path) -> list[Path]:
    """The part files behind *path*: its ``teil-*`` files in name order, or *path* itself."""
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file() and p.name.startswith("teil-"))
    return [path]


def source_exists(path: Path) -> bool:
    """True when *path* is a file or a folder with at least one part."""
    return path.is_file() or (path.is_dir() and bool(source_parts(path)))


def read_capped_source(
    path: Path,
    max_bytes: int,
    *,
    label: str,
    logger: logging.Logger | None = None,
) -> str | None:
    """Join the parts behind *path*, each and all together at most *max_bytes*.

    Mitigates: a planted oversized part (or many parts) exhausting memory,
    the same cap the single source file had. Returns ``None`` when a part is
    missing, unreadable or too large, or when the joined text exceeds
    *max_bytes*.
    """
    pieces: list[str] = []
    total = 0
    parts = source_parts(path)
    if not parts:
        return None
    for part in parts:
        content = read_capped_text(part, max_bytes, label=label, logger=logger)
        if content is None:
            return None
        total += len(content.encode("utf-8"))
        if total > max_bytes:
            if logger is not None:
                logger.warning("%s: parts together exceed %d bytes", label, max_bytes)
            return None
        pieces.append(content)
    return "".join(pieces)


# The sources are about 50 to 90 KB; 4 MiB is the cap the minifier uses.
_MAX_SOURCE_BYTES = 4 * 1024 * 1024


def _read_or_raise(path: Path, label: str) -> str:
    content = read_capped_source(path, _MAX_SOURCE_BYTES, label=label)
    if content is None:
        raise OSError(f"{label}: parts under {path} missing, unreadable or too large")
    return content


def read_site_js() -> str:
    """The whole dashboard script source (for tests and checks)."""
    return _read_or_raise(SITE_JS_DIR, "site.js")


def read_site_css() -> str:
    """The whole dashboard stylesheet source (for tests and checks)."""
    return _read_or_raise(SITE_CSS_DIR, "site.css")
