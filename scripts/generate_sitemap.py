#!/usr/bin/env python3
"""Generate a sitemap.xml for the GitHub Pages site."""

from __future__ import annotations

import contextlib
import datetime as _dt
import logging
import os
import re
# Bandit B404: subprocess is used to run ``git log`` against the repo on
# a trusted internal path. No user input flows into the command.
import subprocess  # nosec B404
import sys
import time
# Bandit B405: ElementTree is used for XML *generation* (sitemap output),
# not for parsing untrusted input.
import xml.etree.ElementTree as ET  # nosec B405
from pathlib import Path
from collections.abc import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.utils.files import atomic_write
    from src.utils.http import validate_public_feed_url
except ModuleNotFoundError:
    # Fallback if src is not a package or run differently
    from utils.files import atomic_write  # type: ignore[no-redef]
    from utils.http import validate_public_feed_url  # type: ignore[no-redef]

DOCS_DIR = REPO_ROOT / "docs"
DEFAULT_BASE_URL = "https://origamihase.github.io/wien-oepnv"

INCLUDE_EXTENSIONS = {".md", ".html", ".xml", ".json", ".pdf", ".txt"}
EXCLUDED_FILES = {"sitemap.xml"}
EXCLUDED_DIRS = {"_includes"}
# Security: byte-exact mirror of ``src/utils/http.py:_UNSAFE_URL_CHARS``.
# The pre-fix regex (``[\s\x00-\x1f\x7f]``) was the documented bucket-(b)
# deferred sibling from the 2026-05-10 BiDi-Mark Drift Round 6 round —
# functionally redundant because the canonical regex inside
# ``validate_public_feed_url`` already rejects BiDi / zero-width /
# structural-injection chars at the second-layer check. The journal
# explicitly named the structural risk: a future PR that adds a callsite
# of ``_UNSAFE_URL_CHARS`` in this module without the second-layer gate
# would re-enable the BiDi/zero-width issue. Widening the regex to the
# canonical set closes that risk and pins the inventory invariant
# (``test_sentinel_sitemap_unsafe_chars_canonical_drift.py``).
# 2026-05-14 "Tag-Character / Variation-Selector Drift (Sitemap
# Sibling)": widened in lockstep with
# ``src/utils/http.py:_UNSAFE_URL_CHARS`` to cover the BMP
# Variation Selectors (U+FE00..U+FE0F), the Unicode Tag block
# (U+E0000..U+E007F), and the supplementary Variation Selectors
# (U+E0100..U+E01EF). The 2026-05-11 Round-11 canonical-floor
# widening ("Tag-Character / Variation-Selector Drift") updated
# every other sanitiser site but missed this
# sibling — the source-file comment ("byte-exact mirror") quietly
# diverged. A planted ``SITE_BASE_URL`` carrying Tag-character /
# Variation-Selector bytes is byte-distinct but visually identical
# to a legitimate URL; its presence in the public sitemap is a
# steganography / prompt-injection / cache-key-collision primitive
# against every search engine and LLM-driven downstream service
# that consumes the sitemap. Inventory invariant pinned by
# ``tests/test_sentinel_sitemap_tag_chars_variation_selectors_drift.py``.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Pre-fix a planted feed/sitemap URL carrying any zero-width Format
# primitive in a path segment would pass the validator unmodified -
# the bytes are visually identical to a legitimate URL but distinct
# for cache-key / GUID-collision shapes (and a prompt-injection
# smuggling primitive against every search engine and LLM-driven
# downstream service that consumes the sitemap). The U+2060..U+2069
# range folds in the existing BiDi-isolate band; reserved U+2065
# has no defined meaning so the additive strip is safe.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with
# ``src/utils/http.py:_UNSAFE_URL_CHARS`` to cover the remaining 13
# Unicode Cf-class bands (44 code points): U+00AD SOFT HYPHEN,
# U+0600..U+0605 Arabic prefix marks, U+06DD, U+070F, U+0890..U+0891,
# U+08E2, U+206A..U+206F deprecated BiDi controls (folds the existing
# U+2060..U+2069 band into U+2060..U+206F), U+FFF9..U+FFFB INTERLINEAR
# ANNOTATION, U+110BD/U+110CD KAITHI, U+13430..U+13438 EGYPTIAN
# HIEROGLYPH, U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and U+1D173..U+1D17A
# MUSICAL SYMBOL formatting. SOFT HYPHEN especially is the most
# impactful omission: it renders zero-width unconditionally in every
# search-engine crawler / RSS reader / browser, so a planted
# ``SITE_BASE_URL`` carrying SOFT HYPHEN is byte-distinct but
# visually-identical to a legitimate URL - a steganography /
# prompt-injection / cache-key-collision primitive against every
# search-engine and LLM-driven downstream service.
_UNSAFE_URL_CHARS = re.compile(
    r"[\s\x00-\x1f\x7f-\x9f<>\"\\^`{|}"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u202a-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)

logger = logging.getLogger(__name__)


def _is_valid_base_url(candidate: str) -> bool:
    """Validate the base URL to prevent sitemap injection via env overrides.

    Security: ``SITE_BASE_URL`` is interpolated into every ``<loc>`` element
    of the published sitemap (and into ``robots.txt``'s ``Sitemap:``
    directive). Search engines treat any URL that survives this check as
    authoritative for the site, so an attacker-controlled host (env override
    via leaked CI env, compromised secret store, intentional misconfig)
    would let a malicious origin claim canonical ranking for our content
    and redirect every search-engine click to a phishing target.

    Delegating to ``validate_public_feed_url`` shares the GitHub-host pin
    with the FEED_LINK / PAGES_BASE_URL surfaces (see ``src.feed.config``)
    so a future fourth publishing URL inherits the pin without having to
    remember to add it. ``check_dns=False`` because the URL is embedded,
    not fetched — DNS state at sitemap generation time is irrelevant to
    whether the URL is a safe target for embedding.
    """
    if _UNSAFE_URL_CHARS.search(candidate):
        return False
    return validate_public_feed_url(candidate, check_dns=False) is not None


def _base_url() -> str:
    raw = os.getenv("SITE_BASE_URL", DEFAULT_BASE_URL)
    candidate = raw.strip()
    if not candidate or not _is_valid_base_url(candidate):
        if raw.strip() and raw.strip() != DEFAULT_BASE_URL:
            logger.warning(
                "SITE_BASE_URL %r is not a known GitHub host; "
                "falling back to default base URL.",
                raw,
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


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Best-effort teardown of a streamed ``git log`` child after early break."""
    if proc.stdout is not None:
        with contextlib.suppress(OSError):
            proc.stdout.close()
    with contextlib.suppress(OSError):
        proc.terminate()
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _git_lastmod_map(paths: list[Path]) -> dict[Path, str]:
    """Map each tracked path to its newest committer timestamp in one ``git`` call.

    Performance: the previous implementation spawned one ``git log -1``
    subprocess *per file* — an N+1 pattern that scaled linearly with the
    docs tree (≈215 ms / 61 files here). This walks the history once with
    ``--name-only`` and a sentinel-prefixed (``\\x01``) format, streaming
    stdout and stopping the moment every requested path has been seen.
    Because ``git log`` emits commits newest-first, the first sighting of a
    path is its most recent commit — identical semantics to ``git log -1``
    but bounded by the depth of the oldest "newest commit" among the inputs
    instead of the sum of every per-file walk (≈18x faster, ≈12 ms here).

    Paths outside the repository (e.g. a patched ``DOCS_DIR`` pointing at a
    pytest ``tmp_path``) are skipped here and fall back to filesystem mtime
    in :func:`_resolve_lastmod` — matching the pre-batch behaviour where
    ``git log`` simply returned nothing for an untracked path.
    """
    rel_by_name: dict[str, Path] = {}
    for path in paths:
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            continue
        rel_by_name[rel] = path
    if not rel_by_name:
        return {}

    result: dict[Path, str] = {}
    remaining = set(rel_by_name)
    # Hard ceiling so an uncommitted path (which ``git log`` never emits and
    # so never clears from ``remaining``) can only ever cost a bounded full
    # history walk rather than hanging the workflow.
    deadline = time.monotonic() + 30.0
    # ``core.quotepath=false`` keeps non-ASCII paths byte-literal so the
    # name lookup matches; ``--no-renames`` pins each commit to the literal
    # path regardless of the caller's ``diff.renames`` config.
    # Bandit B603/B607: static command list on a trusted internal path; the
    # only variable inputs are repo-relative docs paths derived from rglob.
    proc = subprocess.Popen(  # nosec B603, B607
        [
            "git", "-c", "core.quotepath=false", "log", "--no-renames",
            "--format=%x01%cI", "--name-only", "--", *rel_by_name,
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    stdout = proc.stdout
    if stdout is None:  # pragma: no cover - PIPE always yields a stream
        _terminate(proc)
        return result
    current = ""
    try:
        for raw in stdout:
            line = raw.rstrip("\n")
            if line.startswith("\x01"):
                current = line[1:]
            elif line and current:
                target = rel_by_name.get(line)
                if target is not None and target not in result:
                    result[target] = current
                    remaining.discard(line)
            if not remaining or time.monotonic() > deadline:
                break
    finally:
        _terminate(proc)
    return result


def _resolve_lastmod(path: Path, git_iso: str | None) -> str:
    """Resolve a ``<lastmod>`` date: git commit timestamp, else file mtime.

    A future-dated value (clock skew, rebased commit) is clamped to today so
    the published sitemap never advertises a modification date that has not
    happened yet.
    """
    timestamp: _dt.datetime | None = None
    if git_iso:
        with contextlib.suppress(ValueError):
            timestamp = _dt.datetime.fromisoformat(git_iso.replace("Z", "+00:00"))
    if timestamp is None:
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
    paths = list(_iter_paths())
    lastmod_map = _git_lastmod_map(paths)
    entries: list[tuple[str, str, str | None]] = []
    for path in paths:
        url = _to_url(path, base_url)
        lastmod = _resolve_lastmod(path, lastmod_map.get(path))
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
