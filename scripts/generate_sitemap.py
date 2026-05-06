#!/usr/bin/env python3
"""Generate a sitemap.xml for the GitHub Pages site."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import subprocess  # nosec B404 - used for running git internally
import sys
import xml.etree.ElementTree as ET  # nosec B405 - used for XML generation, not parsing untrusted input
from pathlib import Path
from collections.abc import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.utils.files import atomic_write
    from src.utils.http import validate_http_url
except ModuleNotFoundError:
    # Fallback if src is not a package or run differently
    from utils.files import atomic_write  # type: ignore[no-redef]
    from utils.http import validate_http_url  # type: ignore[no-redef]

DOCS_DIR = REPO_ROOT / "docs"
DEFAULT_BASE_URL = "https://origamihase.github.io/wien-oepnv"

INCLUDE_EXTENSIONS = {".md", ".html", ".xml", ".json", ".pdf", ".txt"}
EXCLUDED_FILES = {"sitemap.xml"}
EXCLUDED_DIRS = {"_includes"}
_UNSAFE_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")

logger = logging.getLogger(__name__)


def _is_valid_base_url(candidate: str) -> bool:
    """Validate the base URL to prevent sitemap injection via env overrides.

    Security: ``SITE_BASE_URL`` is interpolated into every ``<loc>`` element
    of the published sitemap (and into ``robots.txt``'s ``Sitemap:``
    directive). Any host that survives this check is taken as authoritative
    by every search engine that crawls the site, so the validation must
    reject the same classes of values that ``validate_http_url`` rejects
    elsewhere in the project: IP literals, reserved/internal TLDs
    (``.local``, ``.internal``, ``.test``, ``.example``, ``.localhost`` …),
    DNS-rebinding wildcards (``nip.io`` and friends), embedded credentials,
    and non-http(s) schemes. ``check_dns=False`` because we don't talk to
    the URL, we embed it — DNS state at sitemap generation time is
    irrelevant to whether the URL is a safe target for embedding.
    """
    if _UNSAFE_URL_CHARS.search(candidate):
        return False
    # Delegating to validate_http_url consolidates the URL-safety policy
    # (TLDs, IP literals, credentials, scheme, length cap) with the
    # provider/HTTP layers; previously a localhost/internal-TLD override
    # would silently land in the published sitemap.
    return validate_http_url(candidate, check_dns=False) is not None


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
            shell=False,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()  # nosec B603, B607 - git execution on trusted internal path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        output = ""
    if output:
        try:
            timestamp = _dt.datetime.fromisoformat(output.replace("Z", "+00:00"))
        except ValueError:
            timestamp = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.UTC)
    else:
        timestamp = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.UTC)
    lastmod_date = timestamp.date()
    today = _dt.date.today()
    if lastmod_date > today:
        lastmod_date = today
    return lastmod_date.isoformat()


def _changefreq(path: Path) -> str | None:
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


def _collect_entries(base_url: str) -> list[tuple[str, str, str | None]]:
    entries: list[tuple[str, str, str | None]] = []
    for path in _iter_paths():
        url = _to_url(path, base_url)
        lastmod = _last_modified(path)
        changefreq = _changefreq(path)
        entries.append((url, lastmod, changefreq))
    entries.sort(key=lambda item: item[0])
    return entries


def _build_xml(entries: Iterable[tuple[str, str, str | None]]) -> str:
    ET.register_namespace("", "http://www.sitemaps.org/schemas/sitemap/0.9")
    urlset = ET.Element("{http://www.sitemaps.org/schemas/sitemap/0.9}urlset")

    for url, lastmod, changefreq in entries:
        url_elem = ET.SubElement(urlset, "{http://www.sitemaps.org/schemas/sitemap/0.9}url")
        ET.SubElement(url_elem, "{http://www.sitemaps.org/schemas/sitemap/0.9}loc").text = url
        ET.SubElement(url_elem, "{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod").text = lastmod
        if changefreq:
            ET.SubElement(url_elem, "{http://www.sitemaps.org/schemas/sitemap/0.9}changefreq").text = changefreq

    if hasattr(ET, "indent"):
        ET.indent(urlset, space="  ", level=0)

    # Serialize to string with XML declaration
    xml_str = ET.tostring(urlset, encoding="unicode", xml_declaration=True)
    return xml_str


def main() -> None:
    base_url = _base_url()
    entries = _collect_entries(base_url)
    sitemap = _build_xml(entries)
    target = DOCS_DIR / "sitemap.xml"
    with atomic_write(target, mode="w", encoding="utf-8", permissions=0o644) as f:
        f.write(sitemap)


if __name__ == "__main__":
    main()
