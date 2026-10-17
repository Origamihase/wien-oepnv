"""Pages under ``docs/`` must not link relatively out of the published site.

GitHub Pages publishes only ``docs/``. A Markdown link such as
``[README](../README.md)`` works in the repository view but points to
``https://origamihase.github.io/README.md`` on the website, which is a 404;
a bare directory link (``reference/``) has no index page there either. The
live link check of 17.10.2026 found eleven such links in
``docs/development.md`` and ``docs/architecture.md``. Files outside
``docs/`` are linked with their full GitHub address instead.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
_LINK_RE = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def _published_markdown() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if "_includes" not in p.parts)


def test_no_relative_link_leaves_the_published_site() -> None:
    bad: list[str] = []
    for page in _published_markdown():
        text = page.read_text(encoding="utf-8")
        # Fenced code blocks are not rendered as links.
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in _LINK_RE.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("/"):
                continue
            resolved = (page.parent / target).resolve()
            rel = page.relative_to(ROOT)
            if not resolved.is_relative_to(DOCS):
                bad.append(f"{rel}: {target} (outside docs/)")
            elif resolved.is_dir() and not any((resolved / name).exists() for name in ("index.md", "index.html", "README.md")):
                bad.append(f"{rel}: {target} (directory without index page)")
    assert not bad, "\n".join(bad)
