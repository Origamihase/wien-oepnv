"""Pages under ``docs/`` must not hand Liquid anything it would act on.

GitHub Pages builds ``docs/`` with Jekyll 3.10 and Liquid 4.0.4. With
``jekyll-optional-front-matter`` (on unless ``_config.yml`` turns it off)
every Markdown file is a page, with or without front matter, and Liquid
runs over it before Markdown does. A quoted ``{{ … }}`` is replaced by its
value, usually nothing: ``update-stations-${{ github.ref }}`` in
``archive/audits/audit-2026-09-13`` read ``update-stations-$`` on the
website. A ``{%`` that does not form a valid tag stops the whole build. On
2026-11-01 the paragraph in ``reference/ki_waechter.md`` that explained
this quoted a bare ``{%``; from the merge of #2092 at 08:43 Vienna time no
"pages build and deployment" run succeeded (22 failed, 4 cancelled), and
the feed at the Pages address, which the TV reads, stayed at 08:30 while
the update runs stayed green.

Rule: outside a ``raw`` block, a page Jekyll renders contains neither
pair. A hand-written page that shows one wraps the whole block between two
lines ``<!-- {% raw %} -->`` and ``<!-- {% endraw %} -->``, each with a
blank line before and after (see ``how-to/test-api-secrets.md``). Generated
pages go through ``liquid_safe``. The checker and the page list live in
``src/utils/liquid.py``; this file pins them to what Liquid 4.0.4 and
Jekyll 3.10 do, case by case as replayed with the ``github-pages`` gem 232
in the review of 2026-11-01.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.utils.liquid import (
    STOPS_BUILD_RAW_OPEN,
    STOPS_BUILD_UNTERMINATED,
    TEMPLATE_DIRS,
    liquid_findings,
    liquid_safe,
    liquid_safe_outside_raw,
    neutralise,
    raw_marker_findings,
    rendered_pages,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
# Keys of docs/_config.yml that do not change which files Jekyll renders.
# include, collections, markdown_ext, optional_front_matter,
# require_front_matter and the like do: rendered_pages() has to learn them
# first.
_KNOWN_CONFIG_KEYS = {"theme", "lang", "title", "description", "exclude"}


def _config() -> dict[str, object]:
    loaded = yaml.safe_load((DOCS / "_config.yml").read_text(encoding="utf-8")) or {}
    assert isinstance(loaded, dict)
    return loaded


def _pages() -> list[Path]:
    excludes = _config().get("exclude") or []
    assert isinstance(excludes, list)
    return rendered_pages(DOCS, [str(entry) for entry in excludes])


def test_no_liquid_pair_outside_raw_in_rendered_pages() -> None:
    bad: list[str] = []
    for page in _pages():
        text = page.read_text(encoding="utf-8-sig")
        for finding in liquid_findings(text):
            bad.append(f"{page.relative_to(ROOT)}:{finding.line}: {finding.reason}")
    assert not bad, (
        "Liquid would act on a page GitHub Pages renders. Wrap the block between "
        "'<!-- {% raw %} -->' and '<!-- {% endraw %} -->' lines (blank line before and "
        "after each), see docs/development.md, 'Liquid in docs/':\n" + "\n".join(bad)
    )


def test_raw_markers_stand_alone_between_blank_lines() -> None:
    bad: list[str] = []
    for page in _pages():
        if page.suffix.lower() != ".md":
            continue
        for finding in raw_marker_findings(page.read_text(encoding="utf-8-sig")):
            bad.append(f"{page.relative_to(ROOT)}:{finding.line}: {finding.reason}")
    assert not bad, "\n".join(bad)


def test_templates_hold_nothing_that_stops_the_build() -> None:
    """Layouts and includes use Liquid on purpose, so only what fails every
    build is checked there: a tag that never closes, a raw block left open
    (a '{%' in an HTML comment of head-custom.html stopped the replayed
    build on 2026-11-01)."""
    stops = {STOPS_BUILD_UNTERMINATED, STOPS_BUILD_RAW_OPEN}
    bad = [
        f"{path.relative_to(ROOT)}:{finding.line}: {finding.reason}"
        for folder in ("_layouts", "_includes")
        for path in sorted((DOCS / folder).rglob("*"))
        if path.is_file()
        for finding in liquid_findings(path.read_text(encoding="utf-8-sig"), front_matter=False)
        if finding.reason in stops
    ]
    assert not bad, "\n".join(bad)


def test_front_matter_is_a_mapping() -> None:
    """Jekyll stops the build on front matter that is a string or a list
    ("Invalid YAML front matter"), Liquid or not."""
    bad: list[str] = []
    for page in _pages():
        text = page.read_text(encoding="utf-8-sig")
        if not text.startswith("---"):
            continue
        block = text.split("\n---", 1)[0].removeprefix("---")
        loaded = yaml.safe_load(block)
        if loaded is not None and not isinstance(loaded, dict):
            bad.append(str(page.relative_to(ROOT)))
    assert not bad, bad


def test_config_holds_no_key_the_page_list_does_not_model() -> None:
    unknown = set(_config()) - _KNOWN_CONFIG_KEYS
    assert not unknown, (
        f"docs/_config.yml sets {sorted(unknown)}; teach rendered_pages() in "
        "src/utils/liquid.py what Jekyll renders with it, then add the key here"
    )


def test_docs_holds_no_symlink() -> None:
    # Jekyll follows a symlink that stays inside docs/; os.walk does not.
    links = [
        str(Path(directory, name).relative_to(ROOT))
        for directory, dirs, files in os.walk(DOCS)
        for name in dirs + files
        if Path(directory, name).is_symlink()
    ]
    assert not links, links


def test_page_list_matches_what_jekyll_renders(tmp_path: Path) -> None:
    pages = {p.relative_to(DOCS).as_posix() for p in _pages()}
    # Markdown without front matter counts (jekyll-optional-front-matter).
    assert {"reference/ki_waechter.md", "archive/audits/audit-2026-09-13.md", "index.md"} <= pages
    # Templates and static files are not pages.
    for static in ("_layouts/default.html", "_includes/head-custom.html", "feed.xml", "feed.en.xml", "site.html"):
        assert static not in pages

    # Files Jekyll 3.10 renders although their names start with "_" or "."
    # (the posts collection, the default ``include: [.htaccess]``), and
    # front matter as Jekyll detects it (first line, any length).
    root = tmp_path / "docs"
    files = {
        "_posts/2026-11-01-notiz.md": "---\ntitle: Notiz\n---\nText\n",
        ".htaccess": "---\n---\nx\n",
        "UPPER.MD": "x\n",
        "plain.html": "<p>x</p>\n",
        "matter.html": "---\n---\n<p>x</p>\n",
        "long.html": "---" + " " * 70 + "\n---\n<p>x</p>\n",
        "crlf.txt": "---\r\n---\r\nx\r\n",
        "bom.html": "\ufeff---\n---\n<p>x</p>\n",
        "_layouts/page.html": "{{ content }}\n",
        "_config.yml": "theme: x\n",
        # Posts need no front matter and may have any extension, at any depth.
        "_posts/2025-10-03-notiz.html": "<p>{% x</p>\n",
        "_posts/sub/2025-10-06-nested.txt": "A ${{ x }} B\n",
        "deep/_posts/2025-10-16-deep.txt": "C ${{ x }} D\n",
        "sub/.htaccess": "---\n---\nx\n",
        "notes": "---\n---\nx\n",
        "notes.d/a.md": "x\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    found = {p.relative_to(root).as_posix() for p in rendered_pages(root)}
    assert found == {
        "_posts/2026-11-01-notiz.md", ".htaccess", "UPPER.MD", "matter.html", "long.html", "crlf.txt",
        "_posts/2025-10-03-notiz.html", "_posts/sub/2025-10-06-nested.txt", "deep/_posts/2025-10-16-deep.txt",
        "sub/.htaccess", "notes", "notes.d/a.md",
    }
    # Jekyll's default include [.htaccess] wins over exclude; "notes/" names a
    # directory, so the file "notes" stays.
    kept = {p.relative_to(root).as_posix() for p in rendered_pages(root, [".htaccess", "sub/.htaccess", "notes/", "notes.d"])}
    assert kept == found - {"notes.d/a.md"}
    assert TEMPLATE_DIRS == {"_layouts", "_includes", "_sass", "_data"}


# Each case was built as a page with the github-pages gem 232. Flagged: the
# build failed, the text changed, or the project rule (plain raw tags only)
# rejects it ("endraw -%}" renders like the plain form).
_LIQUID_ACTS = [
    "ein `{%` ohne Tag",
    "ein `{{` allein",
    "a {{ x }} b",
    "a {{ x } b",
    "a {{- x -}} b",
    "{% comment %}weg{% endcomment %}",
    "{% raw %}nie geschlossen",
    "{% raw foo %}x{% endraw %}",
    "A\n\n{%- raw -%}\n{{ x }}\n{% endraw %}\n\nB\n",
    "{% raw %}x{%- endraw -%}",
    "{% raw %}x{%- endraw %} danach",
    "{{% raw %}x{% endraw %}",
    "<!-- {{% raw %} -->\n\n{{ x }}\n\n<!-- {% endraw %} -->",
    # Ruby's \s is ASCII only: a no-break or thin space is no space there.
    "{%\u00a0raw %}{{ x }}{% endraw %}",
    "{% raw\u00a0%}{{ x }}{% endraw %}",
    "{% raw %}{{ x }}{%\u2009endraw %}",
    "{% raw %}a{% endraw foo %} {{ x }} {% raw %}b{% endraw %}",
    "{% raw %}a{% endraw -%}\nZ",
    "{{{ x }}}",
    "{% if x %}y{% endif %}",
    # Text before an inner tag of a raw block comes out twice (Raw#parse):
    # "{{{%a%}", a table with a third column, a paragraph said twice.
    "{% raw %}{{%a%}{% endraw %}",
    "{% raw %}{%{%a%}{% endraw %}",
    "<!-- {% raw %} -->\n\n`{{% x %}`\n\n<!-- {% endraw %} -->\n",
    "<!-- {% raw %} -->\n\n| Zeichen | Wirkung |\n| --- | --- |\n| `{{` | öffnet `{{ x }}` |\n"
    "| `{%` | öffnet `{% if x %}` |\n\n<!-- {% endraw %} -->\n",
    "<!-- {% raw %} -->\n\nEin `{{` oder `{%` öffnet ein Paar, z. B. `{% if x %}`.\n\n<!-- {% endraw %} -->\n",
]
_LIQUID_LEAVES = [
    "{% raw %}{{ x }} {% y{% endraw %}",
    "{%raw%}{{ x }}{%endraw%}",
    "{% raw %}\r\n{{ x }}\r\n{% endraw %}\r\n",
    "<!-- {% raw %} -->\n\n| a | `${{ x }}` |\n| --- | --- |\n\n<!-- {% endraw %} -->\n",
    # The ki_waechter.md paragraph: a bare "{%" inside the block runs up to
    # the "%}" of the closing tag, and Liquid still closes the block there.
    "<!-- {% raw %} -->\n\nein `{%` ohne Tag\n\n<!-- {% endraw %} -->\n",
    "{% raw %}Beispiel: `{%- endraw -%}`{% endraw %}",
    "`{ {` und `{ %` (liquid_safe)",
    "x }} %} } %",
    "---\ntitle: \"{{ x }} {% y\"\n---\nText\n",
]


@pytest.mark.parametrize("text", _LIQUID_ACTS)
def test_checker_flags_what_liquid_acts_on(text: str) -> None:
    assert liquid_findings(text), text


@pytest.mark.parametrize("text", _LIQUID_LEAVES)
def test_checker_passes_what_liquid_leaves_alone(text: str) -> None:
    assert liquid_findings(text) == [], text


def test_line_numbers_count_from_the_file_start() -> None:
    text = "---\nt: 1\n---\na\n{% raw %}\n{{ x }}\n{% endraw %}\n{{ y }}\n"
    assert [f.line for f in liquid_findings(text)] == [8]
    assert [f.line for f in liquid_findings("a\n{% raw %}\nb\n")] == [2]


def test_the_2026_11_01_paragraph_is_caught() -> None:
    old = (
        "stillschweigend (so geschehen in `archive/audits/audit-2026-09-13`), ein `{%`\n"
        "mit unbekanntem Tag lässt den Pages-Build scheitern, und Website und Fernseher\n"
    )
    assert [f.line for f in liquid_findings(old)] == [1]


@given(st.text(alphabet="{%}- rawend\n`x", max_size=60))
def test_liquid_safe_leaves_no_pair(text: str) -> None:
    assert liquid_findings(liquid_safe(text), front_matter=False) == []


def test_liquid_safe_breaks_runs_of_braces() -> None:
    # The plain replacement used until 2026-11-01 turned "{{{" into "{ {{".
    assert liquid_safe("a {{b}} {%c%}") == "a { {b}} { %c%}"
    assert liquid_safe("{{{ x }}}") == "{ { { x }}}"
    assert liquid_safe("{{{{") == "{ { { {"
    assert liquid_safe("{{%") == "{ { %"


def test_raw_marker_layout() -> None:
    good = "a\n\n<!-- {% raw %} -->\n\n| t |\n\n<!-- {% endraw %} -->\n"
    assert raw_marker_findings(good) == []
    tight = "a\n\n<!-- {% raw %} -->\n| t |\n\n<!-- {% endraw %} -->\n"
    assert [f.line for f in raw_marker_findings(tight)] == [3]
    inline = "a `x` {% raw %}{{ y }}{% endraw %}\n"
    assert [f.line for f in raw_marker_findings(inline)] == [1]
    # Indented, a top-level marker becomes a visible code block.
    indented = "- a\n\n  <!-- {% raw %} -->\n\n  `${{ x }}`\n\n  <!-- {% endraw %} -->\n"
    found = raw_marker_findings(indented)
    assert [(f.line, f.reason) for f in found] == [
        (3, "raw marker line must not be indented (wrap a whole list instead)"),
        (7, "raw marker line must not be indented (wrap a whole list instead)"),
    ]
    stray = "x <!-- {% endraw %} --> y\n"
    assert raw_marker_findings(stray)[0].reason == "raw tag outside a line of its own '<!-- {% endraw %} -->'"


@given(st.text(alphabet="{%}- rawend\n`x<!>", max_size=80))
def test_liquid_safe_outside_raw_leaves_no_pair(text: str) -> None:
    assert liquid_findings(liquid_safe_outside_raw(text)) == []


def test_neutralise_keeps_intact_raw_blocks(tmp_path: Path) -> None:
    """A correctly wrapped example stays byte for byte; only the pair the
    warning names is broken up (review of 2026-11-01)."""
    root = tmp_path / "docs"
    root.mkdir()
    page = root / "mixed.md"
    block = "<!-- {% raw %} -->\n\n`${{ github.ref }}`\n\n<!-- {% endraw %} -->\n"
    page.write_text(f"# Mixed\n\n{block}\nhostile {{% here\n", encoding="utf-8")
    assert neutralise([str(page)], root) == [f"{page}:9"]
    assert page.read_text(encoding="utf-8") == f"# Mixed\n\n{block}\nhostile {{ % here\n"


def test_neutralise_follows_a_symlinked_page_and_survives_a_bad_path(tmp_path: Path) -> None:
    """Jekyll renders notiz.md -> notiz.txt as a Markdown page; a path that
    cannot be read is skipped, the others are still rewritten."""
    root = tmp_path / "docs"
    root.mkdir()
    (root / "notiz.txt").write_text("Notiz: {% ohne Tag\n", encoding="utf-8")
    (root / "notiz.md").symlink_to("notiz.txt")
    (root / "latin1.md").write_bytes("Gr\xfc\xdfe {{ x }}\n".encode("latin-1"))
    (root / "later.md").write_text("{{ y }}\n", encoding="utf-8")
    paths = [str(root / n) for n in ("notiz.md", "latin1.md", "later.md")]
    assert neutralise(paths, root) == [f"{root / 'notiz.md'}:1", f"{root / 'later.md'}:1"]
    assert (root / "notiz.txt").read_text(encoding="utf-8") == "Notiz: { % ohne Tag\n"


def test_neutralise_rewrites_only_pages_with_a_pair(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "bad.md").write_text("---\ntitle: \"{{ t }}\"\n---\nName {% x und {{{ y }}}\n", encoding="utf-8")
    (root / "good.md").write_text("<!-- {% raw %} -->\n\n{{ x }}\n\n<!-- {% endraw %} -->\n", encoding="utf-8")
    (root / "feed.xml").write_text("<rss>{{ x }}</rss>\n", encoding="utf-8")
    (tmp_path / "outside.md").write_text("{{ x }}\n", encoding="utf-8")
    paths = [str(root / n) for n in ("bad.md", "good.md", "feed.xml")] + [str(tmp_path / "outside.md")]
    changed = neutralise(paths, root)
    assert changed == [f"{root / 'bad.md'}:4"]
    assert (root / "bad.md").read_text(encoding="utf-8") == "---\ntitle: \"{{ t }}\"\n---\nName { % x und { { { y }}}\n"
    assert "{{ x }}" in (root / "good.md").read_text(encoding="utf-8")
    assert (root / "feed.xml").read_text(encoding="utf-8") == "<rss>{{ x }}</rss>\n"
    assert (tmp_path / "outside.md").read_text(encoding="utf-8") == "{{ x }}\n"


def test_pull_requests_build_the_site_like_pages() -> None:
    """Michael, 2026-11-01, "Beides bauen": a pull request that touches docs/
    builds the site with the action of the Pages run, and delivers nothing."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/pages-build-check.yml").read_text(encoding="utf-8"))
    triggers = workflow[True]  # PyYAML reads the key "on" as True
    assert "docs/**" in triggers["pull_request"]["paths"]
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["build"]["steps"]
    builds = [step for step in steps if str(step.get("uses", "")).startswith("actions/jekyll-build-pages@")]
    assert len(builds) == 1
    assert builds[0]["with"] == {"source": "./docs", "destination": "./_site"}
    assert not any("deploy" in str(step.get("uses", "")) for step in steps)
