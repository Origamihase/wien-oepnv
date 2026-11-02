"""The dashboard sources live in parts (700-line limit since 2026-11-02).

The parts ``docs/assets/site-css/teil-NN.css`` joined in name order are the
whole stylesheet (the script follows as modules); ``optimize_site_assets.py``
builds ``site.min.css`` from that join. These tests pin the join and its size cap.
"""

from __future__ import annotations

from pathlib import Path

from src.utils import site_sources


def test_css_parts_exist_in_order_and_the_single_file_is_gone() -> None:
    folder = site_sources.SITE_CSS_DIR
    parts = site_sources.source_parts(folder)
    assert len(parts) >= 2
    assert [p.name for p in parts] == [f"teil-{n:02d}.css" for n in range(1, len(parts) + 1)]
    assert not (folder.parent / "site.css").exists()


def test_the_script_is_one_closure_across_the_parts() -> None:
    js = site_sources.read_site_js()
    assert js.lstrip().startswith("/*")
    assert "(() => {" in js
    assert js.rstrip().endswith("})();")


def test_join_is_in_name_order(tmp_path: Path) -> None:
    (tmp_path / "teil-02.js").write_text("b\n", encoding="utf-8")
    (tmp_path / "teil-01.js").write_text("a\n", encoding="utf-8")
    (tmp_path / "notiz.txt").write_text("x\n", encoding="utf-8")
    assert site_sources.read_capped_source(tmp_path, 100, label="t") == "a\nb\n"


def test_single_file_is_read_as_it_is(tmp_path: Path) -> None:
    single = tmp_path / "site.js"
    single.write_text("var x = 1;\n", encoding="utf-8")
    assert site_sources.read_capped_source(single, 100, label="t") == "var x = 1;\n"


def test_cap_holds_for_the_parts_together(tmp_path: Path) -> None:
    (tmp_path / "teil-01.css").write_text("a" * 60, encoding="utf-8")
    (tmp_path / "teil-02.css").write_text("b" * 60, encoding="utf-8")
    assert site_sources.read_capped_source(tmp_path, 100, label="t") is None
    assert site_sources.read_capped_source(tmp_path, 200, label="t") == "a" * 60 + "b" * 60


def test_empty_folder_reads_as_missing(tmp_path: Path) -> None:
    assert site_sources.read_capped_source(tmp_path, 100, label="t") is None
    assert not site_sources.source_exists(tmp_path)
