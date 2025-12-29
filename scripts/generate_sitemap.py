#!/usr/bin/env python3
"""Generate a sitemap.xml for the GitHub Pages site."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.utils.files import atomic_write
except ModuleNotFoundError:
    # Fallback if src is not a package or run differently
    from utils.files import atomic_write  # type: ignore

DOCS_DIR = REPO_ROOT / "docs"
DEFAULT_BASE_URL = "https://origamihase.github.io/wien-oepnv"

INCLUDE_EXTENSIONS = {".md", ".html", ".xml", ".json", ".pdf", ".txt"}
EXCLUDED_FILES = {"sitemap.xml"}
EXCLUDED_DIRS = {"_includes"}
_UNSAFE_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")

logger = logging.getLogger(__name__)


def _is_valid_base_url(candidate: str) -> bool:
    """Validate the base URL to prevent sitemap injection via env overrides."""
    if _UNSAFE_URL_CHARS.search(candidate):
        return False
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    # Disallow embedded credentials to avoid leaking secrets into sitemap URLs.
    if parsed.username or parsed.password:
        return False
    return True


def _base_url() -> str:
    raw = os.getenv("SITE_BASE_URL", DEFAULT_BASE_URL)
    candidate = raw.strip()
    if not candidate or not _is_valid_base_url(candidate):
        if raw.strip() and raw.strip() != DEFAULT_BASE_URL:
            logger.warning(
                "Invalid SITE_BASE_URL provided; falling back to default base URL."
            )
        return DEFAULT_BASE_URL.rstrip("/")
    return candidate.rstrip("/")


def _should_include(path: Path) -> bool:
    if path.name in EXCLUDED_FILES:
        return False
    if path.suffix.lower() not in INCLUDE_EXTENSIONS:
        return False
    rel_parts = path.relative_to(DOCS_DIR).parts[:-1]
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return False
    if any(part.startswith(".") for part in path.relative_to(DOCS_DIR).parts):
        return False
    return True


def _to_url(path: Path, base_url: str) -> str:
    rel = path.relative_to(DOCS_DIR)
    if rel.suffix.lower() == ".md":
        if rel == Path("index.md"):
            return f"{base_url}/"
        rel = rel.with_suffix(".html")
    rel_url = "/".join(rel.parts)
    return f"{base_url}/{rel_url}"


def _last_modified(path: Path) -> str:
    try:
        output = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "--", str(path)],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        output = ""
    if output:
        try:
            timestamp = _dt.datetime.fromisoformat(output.replace("Z", "+00:00"))
        except ValueError:
            timestamp = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc)
    else:
        timestamp = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc)
    lastmod_date = timestamp.date()
    today = _dt.date.today()
    if lastmod_date > today:
        lastmod_date = today
    return lastmod_date.isoformat()


def _changefreq(path: Path) -> Optional[str]:
    if path.name == "feed.xml":
        return "hourly"
    if path.suffix.lower() == ".md":
        if path.name == "index.md":
            return "daily"
        return "weekly"
    if path.suffix.lower() == ".pdf":
        return "yearly"
    return None


def _iter_paths() -> Iterable[Path]:
    for file_path in DOCS_DIR.rglob("*"):
        if file_path.is_file() and _should_include(file_path):
            yield file_path


def _collect_entries(base_url: str) -> List[Tuple[str, str, Optional[str]]]:
    entries: List[Tuple[str, str, Optional[str]]] = []
    for path in _iter_paths():
        url = _to_url(path, base_url)
        lastmod = _last_modified(path)
        changefreq = _changefreq(path)
        entries.append((url, lastmod, changefreq))
    entries.sort(key=lambda item: item[0])
    return entries


def _build_xml(entries: Iterable[Tuple[str, str, Optional[str]]]) -> str:
    lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">",
    ]
    for url, lastmod, changefreq in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{url}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        if changefreq:
            lines.append(f"    <changefreq>{changefreq}</changefreq>")
        lines.append("  </url>")
    lines.append("</urlset>")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    base_url = _base_url()
    entries = _collect_entries(base_url)
    sitemap = _build_xml(entries)
    target = DOCS_DIR / "sitemap.xml"
    with atomic_write(target, mode="w", encoding="utf-8", permissions=0o644) as f:
        f.write(sitemap)


if __name__ == "__main__":
    main()
