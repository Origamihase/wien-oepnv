"""Pages under ``docs/`` must not hand Liquid anything it would act on.

GitHub Pages builds ``docs/`` with Jekyll 3.10 and Liquid 4. With
``jekyll-optional-front-matter`` (part of the Pages build, it cannot be
turned off) every Markdown file is a page, with or without front matter, and
Liquid runs over it before Markdown does. A quoted ``{{ … }}`` is replaced by
its value, usually nothing: ``update-stations-${{ github.ref }}`` in
``archive/audits/audit-2026-09-13`` read ``update-stations-$`` on the website.
A ``{%`` that does not form a valid tag stops the whole build. On 2026-11-01
the paragraph in ``reference/ki_waechter.md`` that explained this quoted a
bare ``{%``; from the merge of #2092 at 08:43 Vienna time every
"pages build and deployment" run failed with "Tag '{%' was not properly
terminated", and the feed at the Pages address, which the TV reads, stayed at
08:30 while the update runs stayed green.

Rule: outside a ``raw`` block, a page Jekyll renders contains neither pair.
A hand-written page that shows one wraps it between two HTML comments
holding the ``raw`` and ``endraw`` tags (see ``how-to/test-api-secrets.md``):
GitHub's own view hides the comments, the website shows the text unchanged.
Generated reports put a space behind the first character instead
(``liquid_safe`` in ``scripts/ki_waechter.py``). Templates under
``_layouts/`` and ``_includes/`` use Liquid on purpose and are not pages.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Jekyll's default ``markdown_ext``.
_MARKDOWN_SUFFIXES = {".markdown", ".mkdown", ".mkdn", ".mkd", ".md"}
# Jekyll::Document::YAML_FRONT_MATTER_REGEXP, start only.
_FRONT_MATTER_RE = re.compile(rb"\A---\s*\r?\n")
# Liquid 4 accepts whitespace control (``{%-``) on both raw tags.
_RAW_BLOCK_RE = re.compile(r"\{%-?\s*raw\s*-?%\}.*?\{%-?\s*endraw\s*-?%\}", re.S)
_OPENER_RE = re.compile(r"\{[{%]")


def _excluded(rel: Path, excludes: list[str]) -> bool:
    # Jekyll skips every path part that starts with "_" or "." (the
    # templates under _layouts/ and _includes/ among them) and the
    # entries of ``exclude`` in _config.yml.
    if any(part.startswith(("_", ".")) for part in rel.parts):
        return True
    posix = rel.as_posix()
    return any(posix == entry or posix.startswith(entry.rstrip("/") + "/") for entry in excludes)


def rendered_pages() -> list[Path]:
    """Every file under ``docs/`` that Jekyll runs through Liquid."""
    config = yaml.safe_load((DOCS / "_config.yml").read_text(encoding="utf-8")) or {}
    excludes = [str(entry) for entry in config.get("exclude") or []]
    pages: list[Path] = []
    for path in sorted(DOCS.rglob("*")):
        if not path.is_file() or _excluded(path.relative_to(DOCS), excludes):
            continue
        if path.suffix.lower() in _MARKDOWN_SUFFIXES:
            pages.append(path)
            continue
        with path.open("rb") as handle:
            head = handle.read(64)
        if _FRONT_MATTER_RE.match(head):
            pages.append(path)
    return pages


def liquid_openers(text: str) -> list[int]:
    """Line numbers of each ``{{`` or ``{%`` outside a raw block."""
    # Keep the line breaks of a raw block so later line numbers stay right.
    outside = _RAW_BLOCK_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return [outside.count("\n", 0, m.start()) + 1 for m in _OPENER_RE.finditer(outside)]


def test_no_liquid_pair_outside_raw_in_rendered_pages() -> None:
    bad: list[str] = []
    for page in rendered_pages():
        text = page.read_text(encoding="utf-8")
        for line in liquid_openers(text):
            bad.append(f"{page.relative_to(ROOT)}:{line}")
    assert not bad, (
        "Liquid pair outside a raw block in a page GitHub Pages renders "
        "(wrap it between '<!-- {% raw %} -->' and '<!-- {% endraw %} -->' "
        "lines, see docs/development.md):\n" + "\n".join(bad)
    )


def test_page_list_matches_what_jekyll_renders() -> None:
    pages = {p.relative_to(DOCS).as_posix() for p in rendered_pages()}
    # Markdown without front matter counts (jekyll-optional-front-matter).
    assert "reference/ki_waechter.md" in pages
    assert "archive/audits/audit-2026-09-13.md" in pages
    assert "index.md" in pages
    # Templates and static files are not pages.
    assert "_layouts/default.html" not in pages
    assert "_includes/head-custom.html" not in pages
    assert "feed.xml" not in pages
    assert "site.html" not in pages


def test_the_2026_11_01_paragraph_is_caught() -> None:
    old = (
        "stillschweigend (so geschehen in `archive/audits/audit-2026-09-13`), ein `{%`\n"
        "mit unbekanntem Tag lässt den Pages-Build scheitern, und Website und Fernseher\n"
    )
    assert liquid_openers(old) == [1]
    assert liquid_openers("a\n`${{ secrets.API_URL }}`\n") == [2]
    assert liquid_openers("a\n{%- raw -%}\n{{ x }} {% y\n{%- endraw -%}\n{{ z }}\n") == [5]
    # A raw block that is never closed fails the build as well.
    assert liquid_openers("{% raw %}\n{{ x }}\n") == [1, 2]
    # The form liquid_safe writes is plain text for Liquid.
    assert liquid_openers("`{ { github.ref }}` und `{ %`") == []
