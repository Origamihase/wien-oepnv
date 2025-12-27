#!/usr/bin/env python3
"""Generate a sitemap.xml for the GitHub Pages site."""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
DEFAULT_BASE_URL = "https://origamihase.github.io/wien-oepnv"

INCLUDE_EXTENSIONS = {".md", ".html", ".xml", ".json", ".pdf", ".txt"}
EXCLUDED_FILES = {"sitemap.xml"}
EXCLUDED_DIRS = {"_includes"}


def _base_url() -> str:
    url = os.getenv("SITE_BASE_URL", DEFAULT_BASE_URL).strip()
    return url.rstrip("/")


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
        ).strip()
    except subprocess.CalledProcessError:
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
    target.write_text(sitemap, encoding="utf-8")


if __name__ == "__main__":
    main()
