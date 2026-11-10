"""Coverage for the ``llms.txt`` generator.

Structure is asserted against the real ``docs/`` tree (the file the
``seo-guard`` workflow publishes) plus unit checks for the front-matter
parsing that drives the API-reference / how-to sections.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import generate_llms_txt, generate_sitemap

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


# ----- pages of finished years ----------------------------------------
#
# Owner decision 2026-11-10: a finished year gets its own page
# ``docs/statistik-<year>.md`` on 1 January. The generator lists those pages
# after the running statistics page. The tests build a small ``docs/`` tree
# of their own, because the real one gains a year page every January.


def _docs_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    docs = tmp_path / "docs"
    (docs / "reference").mkdir(parents=True)
    (docs / "index.md").write_text(
        "---\ntitle: Projekt\ndescription: Kurzbeschreibung\n---\n", encoding="utf-8"
    )
    (docs / "architecture.md").write_text("# Architektur\n", encoding="utf-8")
    (docs / "development.md").write_text("# Entwicklung\n", encoding="utf-8")
    (docs / "statistik.md").write_text("# Wien ÖPNV — Statistik 2028\n", encoding="utf-8")
    (docs / "reference" / "trip.md").write_text("# GET /trip\n", encoding="utf-8")
    # ``_to_url`` lives in the sitemap generator and resolves against its own
    # ``DOCS_DIR``, so both modules have to look at the tree.
    monkeypatch.setattr(generate_llms_txt, "DOCS_DIR", docs)
    monkeypatch.setattr(generate_sitemap, "DOCS_DIR", docs)
    return docs


def _year_page(docs: Path, name: str, year: int) -> None:
    (docs / name).write_text(f"# Wien ÖPNV — Statistik {year}\n", encoding="utf-8")


def test_without_year_pages_nothing_is_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs_tree(tmp_path, monkeypatch)
    out = generate_llms_txt.build_llms_txt(BASE)
    assert "abgeschlossenes Jahr" not in out
    documentation = out.split("## Dokumentation\n\n")[1].split("\n\n")[0].splitlines()
    assert [line.split("](")[1].split(")")[0] for line in documentation] == [
        f"{BASE}/architecture.html",
        f"{BASE}/development.html",
        f"{BASE}/statistik.html",
        f"{BASE}/site.html",
    ]


def test_year_pages_follow_the_running_statistics_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _docs_tree(tmp_path, monkeypatch)
    before = generate_llms_txt.build_llms_txt(BASE)
    _year_page(docs, "statistik-2026.md", 2026)
    _year_page(docs, "statistik-2027.md", 2027)
    out = generate_llms_txt.build_llms_txt(BASE)

    year_lines = [
        f"- [Wien ÖPNV — Statistik 2027]({BASE}/statistik-2027.html): "
        "Statistik 2027: abgeschlossenes Jahr",
        f"- [Wien ÖPNV — Statistik 2026]({BASE}/statistik-2026.html): "
        "Statistik 2026: abgeschlossenes Jahr",
    ]
    lines = out.splitlines()
    running = next(i for i, line in enumerate(lines) if f"({BASE}/statistik.html)" in line)
    assert lines[running + 1 : running + 3] == year_lines
    assert f"({BASE}/site.html)" in lines[running + 3]
    # The year lines are the only difference to the output without year pages.
    assert [line for line in lines if line not in year_lines] == before.splitlines()
    assert out.endswith("\n") and not out.endswith("\n\n")


def test_only_exact_year_page_names_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _docs_tree(tmp_path, monkeypatch)
    before = generate_llms_txt.build_llms_txt(BASE)
    for name in (
        "statistik-2026-alt.md",
        "statistik-26.md",
        "statistik-20266.md",
        "statistik-abcd.md",
        "statistik-2026.html",
        "statistik-2026.md.bak",
    ):
        _year_page(docs, name, 2026)
    (docs / "statistik-2025.md").mkdir()  # a folder is not a page
    (docs / "reference" / "statistik-2024.md").write_text("# Tief\n", encoding="utf-8")
    out = generate_llms_txt.build_llms_txt(BASE)
    assert "abgeschlossenes Jahr" not in out
    # The page under ``reference/`` is an ordinary reference page there.
    assert [line for line in out.splitlines() if "statistik-2024" not in line] == (
        before.splitlines()
    )


def test_year_page_without_a_readable_heading_is_listed_under_its_file_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty page or one that is not UTF-8 has no H1 to take the title
    from; the generator must not fail on it, and the link stays correct."""
    docs = _docs_tree(tmp_path, monkeypatch)
    line = f"- [statistik-2026]({BASE}/statistik-2026.html): Statistik 2026: abgeschlossenes Jahr"
    for content in (b"", b"# \xff\xfe kaputt\n", b"Text ohne \xc3\x9cberschrift\n"):
        (docs / "statistik-2026.md").write_bytes(content)
        assert line in generate_llms_txt.build_llms_txt(BASE).splitlines(), content


def test_description_names_the_year_as_the_file_name_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name pattern takes any four digits; the description must not
    shorten them ("Statistik 26" next to the link statistik-0026.html)."""
    docs = _docs_tree(tmp_path, monkeypatch)
    _year_page(docs, "statistik-0026.md", 26)
    out = generate_llms_txt.build_llms_txt(BASE)
    assert f"({BASE}/statistik-0026.html): Statistik 0026: abgeschlossenes Jahr" in out


def test_real_tree_lists_exactly_its_year_pages() -> None:
    """Holds today (no year page) and after every 1 January."""
    out = generate_llms_txt.build_llms_txt(BASE)
    expected = sorted(
        (
            int(path.stem.removeprefix("statistik-"))
            for path in generate_sitemap.DOCS_DIR.glob("statistik-[0-9][0-9][0-9][0-9].md")
        ),
        reverse=True,
    )
    listed = [int(year) for year in re.findall(r"\): Statistik (\d{4}): abgeschlossenes Jahr", out)]
    assert listed == expected
