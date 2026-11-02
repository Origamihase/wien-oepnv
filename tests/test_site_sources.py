"""The dashboard sources live in parts (700-line limit since 2026-11-02).

The parts ``docs/assets/site-css/teil-NN.css`` joined in name order are the
whole stylesheet, the modules ``docs/assets/site-js/teil-NN-<name>.js`` the
whole script; ``optimize_site_assets.py`` builds ``site.min.css`` and
``site.min.js`` from these joins. These tests pin the joins, the module
contract and the size cap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.utils import site_sources


def test_css_parts_exist_in_order_and_the_single_file_is_gone() -> None:
    folder = site_sources.SITE_CSS_DIR
    parts = site_sources.source_parts(folder)
    assert len(parts) >= 2
    assert [p.name for p in parts] == [f"teil-{n:02d}.css" for n in range(1, len(parts) + 1)]
    assert not (folder.parent / "site.css").exists()


_MODULES = ["grundlagen", "sprache", "diagramme", "feed", "statistik", "live-kachel", "wetter", "start"]


def test_script_modules_exist_in_order_and_the_single_file_is_gone() -> None:
    folder = site_sources.SITE_JS_DIR
    parts = site_sources.source_parts(folder)
    assert [p.name for p in parts] == [f"teil-{n:02d}-{m}.js" for n, m in enumerate(_MODULES, 1)]
    assert not (folder.parent / "site.js").exists()


def test_each_module_is_its_own_closure_on_the_shared_namespace() -> None:
    """Michael 2026-11-02: "Echte Module". Each file is one strict closure;
    it takes what it needs from WienOepnv at its start and adds what later
    modules use at its end. Only the first module creates the namespace."""
    parts = site_sources.source_parts(site_sources.SITE_JS_DIR)
    for n, part in enumerate(parts):
        js = part.read_text(encoding="utf-8")
        assert js.lstrip().startswith("/*"), part.name
        assert '"use strict";' in js, part.name
        assert js.rstrip().endswith("})();"), part.name
        assert js.count("\n(() => {\n") == 1, part.name
        assert ("const WienOepnv = {};" in js) == (n == 0), part.name
        assert "window.WienOepnv" not in js, part.name
        if 0 < n:
            assert "} = WienOepnv;" in js, part.name
        if n < len(parts) - 1:
            assert "Object.assign(WienOepnv, {" in js, part.name


def test_modules_only_use_names_of_earlier_modules() -> None:
    """A module reads the namespace once, when it runs, so every name it takes
    must come from a module before it."""
    import re

    parts = site_sources.source_parts(site_sources.SITE_JS_DIR)
    provided: set[str] = set()
    for part in parts:
        js = part.read_text(encoding="utf-8")
        taken = re.search(r"const \{([^}]*)\} = WienOepnv;", js)
        if taken:
            names = {n.strip() for n in taken.group(1).split(",") if n.strip()}
            missing = names - provided
            assert not missing, f"{part.name} takes {sorted(missing)} before any module adds it"
        added = re.search(r"Object\.assign\(WienOepnv, \{([^}]*)\}\);", js)
        if added:
            provided |= {n.strip() for n in added.group(1).split(",") if n.strip()}


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


_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="needs node")
def test_formatters_and_texts_follow_the_language_switch() -> None:
    """The one behaviour the module split had to rebuild: the other modules
    keep the formatter objects of the Sprache module, and ``setLang`` swaps
    the Intl formatter behind them (before, the closure reassigned ``let``
    variables)."""
    sprache = (site_sources.SITE_JS_DIR / "teil-02-sprache.js").read_text(encoding="utf-8")
    program = (
        "const store = {};\n"
        "globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };\n"
        "const WienOepnv = { $: () => null, FEED_URL_DE: 'feed.xml', FEED_URL_EN: 'feed.en.xml',"
        " LANG_STORAGE_KEY: 'wienoepnv:lang' };\n"
        + sprache
        + "\nconst W = WienOepnv; const out = [];\n"
        "const snap = () => out.push([W.getLang(), W.nfInt.format(12345.5), W.nf1.format(2.25),"
        " W.currentFeedUrl(), W.ct('tile-na'), W.rtf.format(-1, 'day')]);\n"
        "snap(); W.setLang('en'); snap(); W.setLang('de'); snap();\n"
        "out.push(store['wienoepnv:lang']);\n"
        "process.stdout.write(JSON.stringify(out));"
    )
    assert _NODE is not None
    # The program is assembled from this checkout's module, no outside input.
    argv = [_NODE, "-e", program]
    run = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)  # noqa: S603
    de, en, de_again, stored = json.loads(run.stdout)
    assert de[0] == "de" and en[0] == "en" and de_again == de
    assert de[1] == "12\u00a0345,5" and en[1] == "12,345.5"  # de-AT groups with a no-break space
    assert de[2] == "2,3" and en[2] == "2.3"
    assert de[3] == "feed.xml" and en[3] == "feed.en.xml"
    assert de[5] == "gestern" and en[5] == "yesterday"
    assert stored == "de"
