"""Coverage for the ``llms.txt`` generator.

Structure is asserted against the real ``docs/`` tree (the file the
``seo-guard`` workflow publishes) plus unit checks for the front-matter
parsing that drives the API-reference / how-to sections.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts import generate_llms_txt

BASE = "https://forker.github.io/base"


def test_starts_with_h1_then_summary_blockquote() -> None:
    lines = generate_llms_txt.build_llms_txt(BASE).splitlines()
    assert lines[0].startswith("# ")
    assert any(line.startswith("> ") for line in lines[:6])


def test_contains_curated_sections() -> None:
    out = generate_llms_txt.build_llms_txt(BASE)
    assert "## Dokumentation" in out
    assert "## API-Referenz" in out
    assert "## Feeds & Daten" in out


def test_links_use_base_url_and_resolved_extensions() -> None:
    out = generate_llms_txt.build_llms_txt(BASE)
    # Markdown docs are mapped to their published .html URL, feeds stay .xml.
    assert f"({BASE}/reference/trip.html)" in out
    assert f"({BASE}/feed.xml)" in out
    # No link may point at a raw .md source (every link goes through _to_url).
    assert re.search(r"]\([^)]*\.md\)", out) is None


def test_output_is_deterministic() -> None:
    assert generate_llms_txt.build_llms_txt(BASE) == generate_llms_txt.build_llms_txt(BASE)


def test_front_matter_parses_values_with_colons() -> None:
    parsed = generate_llms_txt._front_matter(
        '---\ntitle: "GET /trip"\ndescription: Ratio a:b\n---\nbody\n'
    )
    assert parsed["title"] == "GET /trip"
    assert parsed["description"] == "Ratio a:b"


def test_front_matter_absent_returns_empty() -> None:
    assert generate_llms_txt._front_matter("# Heading\n\nbody\n") == {}


def test_page_meta_falls_back_to_first_h1(tmp_path: Path) -> None:
    page = tmp_path / "x.md"
    page.write_text("# Title Here\n\nbody\n", encoding="utf-8")
    title, description = generate_llms_txt._page_meta(page)
    assert title == "Title Here"
    assert description is None
