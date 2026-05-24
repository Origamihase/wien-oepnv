#!/usr/bin/env python3
"""Generate ``docs/llms.txt`` for the GitHub Pages site.

``llms.txt`` (https://llmstxt.org/) is the emerging companion to
``robots.txt`` / ``sitemap.xml``: where the sitemap enumerates every URL,
``llms.txt`` hands a Large Language Model a *curated*, Markdown-formatted
map of the most information-dense pages so it can grasp the project in one
cheap fetch instead of rendering the whole site.

The file is intentionally hand-curated (not a dump of all 60+ doc URLs):
the top-level guides and feeds carry static descriptions, while the API
reference and how-to sections are generated from each page's existing
front matter so new pages appear automatically. URLs are produced via the
sitemap generator's ``_to_url`` so an ``llms.txt`` link can never drift
from its ``sitemap.xml`` counterpart, and the base URL passes the same
GitHub-host pin (:func:`scripts.generate_sitemap._base_url`).

Output is deterministic (stable ordering, no timestamps) so the daily
``seo-guard`` run only commits a change when the docs actually change.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reuse the sitemap generator's URL helpers so ``llms.txt`` links are
# byte-identical to their ``sitemap.xml`` counterparts and inherit the
# SITE_BASE_URL host pin without a second validation copy.
from scripts.generate_sitemap import (  # noqa: E402
    DOCS_DIR,
    _base_url,
    _to_url,
)
from src.utils.files import atomic_write  # noqa: E402

_INTRO = (
    "Der Wien ÖPNV Feed bündelt Störungs- und Baustellenmeldungen von "
    "Wiener Linien, ÖBB und VOR zu einem konsolidierten RSS-Feed, ergänzt "
    "um Dokumentation und reproduzierbare Open-Data-Workflows. Die "
    "folgenden Seiten sind die informationsdichtesten Einstiegspunkte."
)

# (path under docs/, curated description). The title is read from each
# page's front matter / first H1 so it stays in sync with the document.
_GUIDE_PAGES: tuple[tuple[str, str], ...] = (
    (
        "architecture.md",
        "Architekturüberblick: wie Caches, Provider, Feed-Build und "
        "GitHub-Actions-Workflows zusammenhängen.",
    ),
    (
        "development.md",
        "Entwickler-Handbuch: Setup, CLI, Konfiguration, Provider-Logik, "
        "Tests und CI-Pipelines.",
    ),
    (
        "statistik.md",
        "Automatisch erzeugte Jahresstatistik der Störungs- und "
        "Baustellenmeldungen im Wiener ÖPNV.",
    ),
)

# (path under docs/, title, description) for non-Markdown artefacts that
# have neither front matter nor an H1 to derive a title from.
_FEED_PAGES: tuple[tuple[str, str, str], ...] = (
    (
        "feed.xml",
        "RSS-Feed (Deutsch)",
        "Konsolidierter RSS-Feed mit Störungs- und Baustellenmeldungen "
        "für den Wiener ÖPNV (WL/ÖBB/VOR).",
    ),
    (
        "feed.en.xml",
        "RSS feed (English)",
        "English-language consolidated disruption and construction feed "
        "for Vienna public transport.",
    ),
)

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FRONT_MATTER_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
_H1_RE = re.compile(r"^#\s+(.+)$")


def _front_matter(text: str) -> dict[str, str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        field = _FRONT_MATTER_LINE_RE.match(line.strip())
        if field:
            data[field.group(1)] = field.group(2).strip().strip("\"'")
    return data


def _page_meta(path: Path) -> tuple[str, str | None]:
    """Return ``(title, description)`` from front matter, falling back to
    the first H1 for the title and ``None`` for a missing description."""
    text = path.read_text(encoding="utf-8")
    front = _front_matter(text)
    title = front.get("title")
    if not title:
        for line in text.splitlines():
            heading = _H1_RE.match(line.strip())
            if heading:
                title = heading.group(1).strip()
                break
    return (title or path.stem), (front.get("description") or None)


def _link(title: str, url: str, description: str | None) -> str:
    return f"- [{title}]({url}): {description}" if description else f"- [{title}]({url})"


def _doc_entry(path: Path, base_url: str, description: str | None = None) -> str:
    title, front_desc = _page_meta(path)
    return _link(title, _to_url(path, base_url), description or front_desc)


def build_llms_txt(base_url: str) -> str:
    title, description = _page_meta(DOCS_DIR / "index.md")
    lines: list[str] = [f"# {title}", ""]
    if description:
        lines += [f"> {description}", ""]
    lines += [_INTRO, ""]

    lines += ["## Dokumentation", ""]
    for rel, guide_desc in _GUIDE_PAGES:
        lines.append(_doc_entry(DOCS_DIR / rel, base_url, guide_desc))
    lines.append(
        _link(
            "Live-Dashboard",
            _to_url(DOCS_DIR / "site.html", base_url),
            "Interaktives Dashboard mit Live-Störungen, "
            "Stammstrecken-Monitor und Jahresstatistik.",
        )
    )

    for heading, subdir in (("API-Referenz", "reference"), ("How-to", "how-to")):
        pages = sorted((DOCS_DIR / subdir).glob("*.md"))
        if not pages:
            continue
        lines += ["", f"## {heading}", ""]
        lines += [_doc_entry(page, base_url) for page in pages]

    lines += ["", "## Feeds & Daten", ""]
    for rel, feed_title, feed_desc in _FEED_PAGES:
        lines.append(_link(feed_title, _to_url(DOCS_DIR / rel, base_url), feed_desc))

    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> None:
    content = build_llms_txt(_base_url())
    target = DOCS_DIR / "llms.txt"
    with atomic_write(target, mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(content)


if __name__ == "__main__":
    main()
