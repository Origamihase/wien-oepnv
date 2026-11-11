"""The year switch under "Aktuelle Meldungen" on ``docs/site.html``.

Owner decision 2026-11-10 (Michael): "the year can be chosen on the website
under the heading *Aktuelle Meldungen*". A finished year has its own page
``docs/statistik-<year>.md``; ``docs/stats-summary.json`` names those years
in ``archive_years`` and the page builds one link per year from it.

Three halves are pinned here: the generator writes the field in one fixed
order, the markup carries a row that stays hidden until JavaScript fills
it, and ``renderYearSwitch`` (run in Node, skipped when Node is not
installed) only turns checked numbers into links. The summary is fetched
JSON, so the last half is the one that matters for safety.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts import check_i18n_coverage as gate
from scripts.generate_markdown_stats import (
    SUMMARY_FILENAME,
    SUMMARY_SCHEMA_VERSION,
    aggregate_ausfaelle,
    aggregate_stammstrecke,
    aggregate_stoerungen,
    build_stats_summary,
    write_stats_summary,
)
from src.utils.site_sources import read_site_css, read_site_js
from tests.test_site_feed_tiles import _block

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node")

VIENNA_TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2027, 1, 2, 0, 10, tzinfo=VIENNA_TZ)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _REPO_ROOT / "docs"
_SITE_HTML = _DOCS / "site.html"


# ----- the generator: one fixed order ---------------------------------


def _summary(archive_years: Sequence[int] | None = None) -> dict[str, object]:
    """A summary of an empty year; ``None`` leaves the argument out."""
    if archive_years is None:
        return build_stats_summary(
            year=2027,
            generated_at=NOW,
            stammstrecke_rows=[],
            stammstrecke=aggregate_stammstrecke([]),
            stoerungen=aggregate_stoerungen([]),
            ausfaelle=aggregate_ausfaelle([]),
            window_rows=[],
            all_window_rows=[],
            window_days=30,
        )
    return build_stats_summary(
        year=2027,
        generated_at=NOW,
        stammstrecke_rows=[],
        stammstrecke=aggregate_stammstrecke([]),
        stoerungen=aggregate_stoerungen([]),
        ausfaelle=aggregate_ausfaelle([]),
        window_rows=[],
        all_window_rows=[],
        window_days=30,
        archive_years=archive_years,
    )


def test_summary_without_years_carries_an_empty_list() -> None:
    assert _summary()["archive_years"] == []


def test_summary_lists_years_newest_first_and_once() -> None:
    summary = _summary(archive_years=[2025, 2026, 2025, 2024, 2026])
    assert summary["archive_years"] == [2026, 2025, 2024]
    # Any sequence of years will do; the caller globs them from ``docs/``.
    assert _summary(archive_years=(2026,))["archive_years"] == [2026]


def test_field_is_additive_and_the_schema_version_stays() -> None:
    """A bump would make every page still open in a browser refuse the new
    document; the page reads a missing field as "no finished year"."""
    assert SUMMARY_SCHEMA_VERSION == 1
    with_years = _summary(archive_years=[2026])
    without = _summary()
    assert with_years["schema_version"] == without["schema_version"] == 1
    assert {k: v for k, v in with_years.items() if k != "archive_years"} == {
        k: v for k, v in without.items() if k != "archive_years"
    }


def test_written_file_carries_the_years_as_numbers(tmp_path: Path) -> None:
    out = tmp_path / SUMMARY_FILENAME
    write_stats_summary(_summary(archive_years=[2025, 2026]), output_path=out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["archive_years"] == [2026, 2025]
    assert all(type(year) is int for year in payload["archive_years"])


def test_committed_summary_is_fine_with_and_without_the_field() -> None:
    """The committed file is written by the update cycle and gets the field
    with the first run after this change; both states must pass."""
    payload = json.loads((_DOCS / SUMMARY_FILENAME).read_text(encoding="utf-8"))
    years = payload.get("archive_years", [])
    assert isinstance(years, list)
    assert all(type(year) is int for year in years)
    assert years == sorted(set(years), reverse=True)


# ----- the markup: a row that is hidden until it is filled ------------


def _html() -> str:
    return _SITE_HTML.read_text(encoding="utf-8")


def _nav_tag(html: str) -> str:
    match = re.search(r'<nav\b[^>]*\bid="year-switch"[^>]*>', html)
    assert match is not None, "site.html lost the year switch"
    return match.group(0)


def test_row_is_hidden_and_labelled_in_the_markup() -> None:
    tag = _nav_tag(_html())
    assert 'class="year-switch"' in tag
    assert re.search(r"\shidden(\s|>)", tag), "the row must start hidden"
    assert 'aria-label="Statistik nach Jahr"' in tag
    assert 'data-i18n-aria-label="year-switch-aria"' in tag
    # CSP ``style-src 'self'``: no inline style on the row.
    assert "style=" not in tag


def test_row_sits_between_the_first_sub_line_and_the_coverage_notice() -> None:
    html = _html()
    feed = html[html.index('<section id="feed"') :]
    feed = feed[: feed.index("</section>")]
    sub = feed.index('data-i18n="feed-sub-stammstrecke-html"')
    nav = feed.index('id="year-switch"')
    notice = feed.index('id="stammstrecke-coverage"')
    assert feed.index('id="feed-title"') < sub < nav < notice
    assert feed.count('id="year-switch"') == 1


def test_row_has_a_visible_label_and_an_empty_list() -> None:
    html = _html()
    start = html.index(_nav_tag(html))
    body = html[start : html.index("</nav>", start)]
    assert (
        '<span class="year-switch__label" data-i18n="year-switch-label">'
        "Statistik nach Jahr:</span>"
    ) in body
    # JavaScript fills the list; the file ships it empty.
    assert '<ul class="year-switch__list" id="year-switch-list"></ul>' in body
    assert "<a" not in body


def test_static_texts_are_translated() -> None:
    en = _block(read_site_js(), "const I18N_EN = {")
    assert '"year-switch-aria": "Statistics by year"' in en
    assert '"year-switch-label": "Statistics by year:"' in en


def test_link_text_exists_in_both_languages() -> None:
    """The gate discovers DE/EN pairs by name; this pins that the pair of the
    year switch is one of them and that the English text says the page behind
    the link is in German (the year pages are German only)."""
    pairs = gate._locale_pairs(read_site_js())
    assert "YEAR_TEXT" in pairs
    de_body, en_body = pairs["YEAR_TEXT"]
    assert '"year-link-title"' in de_body and '"year-link-title"' in en_body
    assert "abgeschlossenes Jahr" in de_body
    assert "page in German" in en_body
    assert gate._locale_pair_errors(read_site_js()) == []


def test_css_keeps_the_hidden_row_out_of_the_layout() -> None:
    """``.year-switch`` sets ``display: flex``, which would override the
    ``hidden`` attribute without the rule below (same as ``.coverage-notice``)."""
    css = read_site_css()
    assert ".year-switch[hidden] { display: none; }" in css
    rule = _block(css, "\na.chip {")
    assert "text-decoration: none;" in rule
    # The source dot needs ``data-filter``; the year links carry none.
    assert '.chip[data-filter]:not([data-filter="all"])::before' in css
    assert "data-filter" not in _block(read_site_js(), "function renderYearSwitch(summary) {")


def test_css_lets_the_row_wrap_on_a_phone() -> None:
    """Without the wrap a row of many years is wider than a phone, and the
    whole page scrolls sideways."""
    css = read_site_css()
    row = _block(css, "\n.year-switch {")
    chips = _block(css, "\n.year-switch__list {")
    assert "flex-wrap: wrap;" in row and "flex-wrap: wrap;" in chips
    # The chips are list items; they carry no bullet.
    assert "list-style: none;" in chips


def test_shipped_bundles_carry_the_year_switch() -> None:
    """Visitors run the bundles, not the parts (staleness itself is pinned by
    ``test_shipped_bundle_is_in_step_with_the_source``)."""
    assets = _DOCS / "assets"
    js = (assets / "site.min.js").read_text(encoding="utf-8")
    css = (assets / "site.min.css").read_text(encoding="utf-8")
    assert "#year-switch-list" in js and "archive_years" in js
    assert ".year-switch[hidden]" in css


# ----- the script: only checked numbers become links ------------------


def test_switch_is_drawn_with_every_fetch_and_after_a_language_switch() -> None:
    js = read_site_js()
    load = _block(js, "async function loadStammstrecke(signal) {")
    # Stammstrecke is the section that loads on every cycle.
    assert "renderYearSwitch(summary);" in load
    assert load.index("await fetchSummary(signal)") < load.index("renderYearSwitch(summary);")
    # The document is kept: the language switch draws from it, and without
    # it the row would vanish until the refetch has answered.
    assert "lastSummary = summary;" in load
    assert load.index("await fetchSummary(signal)") < load.index("lastSummary = summary;")
    switch = _block(js, "function attachLangSwitch() {")
    assert "renderYearSwitch(lastSummary);" in switch
    assert switch.index("applyTranslationsToDom(lang);") < switch.index("renderYearSwitch(lastSummary);")


def test_switch_builds_no_markup_from_strings() -> None:
    js = read_site_js()
    for start in ("function archiveYears(summary) {", "function renderYearSwitch(summary) {"):
        block = _block(js, start)
        assert "innerHTML" not in block and "insertAdjacentHTML" not in block


def _function(js: str, signature: str) -> str:
    """The function that starts with ``signature`` (ending in its opening
    brace). ``_block`` takes the first brace after the start, which for
    ``el(tag, opts = {}, ...)`` is the default value, not the body."""
    begin = js.index(signature)
    depth = 0
    for pos in range(begin + len(signature) - 1, len(js)):
        if js[pos] == "{":
            depth += 1
        elif js[pos] == "}":
            depth -= 1
            if depth == 0:
                return js[begin : pos + 1]
    raise AssertionError(f"unbalanced function {signature!r}")


def _const(js: str, name: str) -> str:
    match = re.search(rf"const {name} = \d+;", js)
    assert match is not None, f"site.js must declare {name}"
    return match.group(0)


_DOM = """
class Node {
  constructor(tag) {
    this.tag = tag; this.attrs = {}; this.children = [];
    this.className = ""; this.textContent = ""; this.hidden = false;
    this.dataset = {};
  }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  append(...items) {
    for (const item of items) {
      if (item.tag === "#fragment") this.children.push(...item.children);
      else this.children.push(item);
    }
  }
  replaceChildren() { this.children = []; }
}
const document = {
  createElement: (tag) => new Node(tag),
  createDocumentFragment: () => new Node("#fragment"),
  createTextNode: () => new Node("#text"),
};
// The row starts VISIBLE with a stale entry, so "hidden" and "emptied" in
// the result are the function's doing.
const nav = new Node("nav");
const list = new Node("ul");
list.append(new Node("li"));
const $ = (sel) => (sel === "#year-switch" ? nav : sel === "#year-switch-list" ? list : null);
"""

_REPORT = """
const links = list.children.map((li) => {
  const a = li.children[0];
  return {
    wrapper: li.tag, tag: a.tag, text: a.textContent, cls: a.className,
    href: a.attrs.href, current: a.attrs["aria-current"] ?? null,
    title: a.attrs.title ?? null, label: a.attrs["aria-label"] ?? null,
    hreflang: a.attrs.hreflang ?? null,
  };
});
process.stdout.write(JSON.stringify({ hidden: nav.hidden, links }));
"""


def _draw(summary: object) -> str:
    """One call of ``renderYearSwitch`` as a step of ``_run``."""
    return f"renderYearSwitch({json.dumps(summary)});"


def _switch(*summaries: object, lang: str = "de") -> dict[str, object]:
    """Run ``renderYearSwitch`` once per summary, in order, and return the
    state of the row after the last one."""
    return _run([_draw(summary) for summary in summaries], lang=lang)


def _run(steps: Sequence[str], *, lang: str = "de") -> dict[str, object]:
    """Run the JavaScript ``steps`` in order (``LANG`` is the language
    ``getLang`` answers, so a step may switch it) and return the state of the
    row after the last one."""
    js = read_site_js()
    parts = [
        _DOM,
        f"let LANG = {json.dumps(lang)};",
        "const getLang = () => LANG;",
        _function(js, "function el(tag, opts = {}, ...children) {"),
        _block(js, "function clear(node) {"),
        _block(js, "const YEAR_TEXT_DE = {"),
        _block(js, "const YEAR_TEXT_EN = {"),
        _block(js, "function yearText(key) {"),
        _const(js, "ARCHIVE_YEAR_MIN"),
        _const(js, "ARCHIVE_YEAR_MAX"),
        _const(js, "ARCHIVE_YEAR_LIMIT"),
        _block(js, "function archiveYears(summary) {"),
        _block(js, "function renderYearSwitch(summary) {"),
    ]
    program = "\n".join(parts) + "\n" + "\n".join(steps) + _REPORT
    assert NODE is not None
    # The program is assembled from site.js of this checkout, no outside input.
    argv = [NODE, "-e", program]
    out = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)  # noqa: S603
    result = json.loads(out.stdout)
    assert isinstance(result, dict)
    return result


def _links(result: dict[str, object]) -> list[dict[str, object]]:
    links = result["links"]
    assert isinstance(links, list)
    return links


def _years(result: dict[str, object]) -> list[object]:
    return [link["text"] for link in _links(result)]


@needs_node
def test_hidden_without_finished_years() -> None:
    for summary in (
        {"year": 2026},  # a document written before the field existed
        {"year": 2026, "archive_years": []},
        {"year": 2026, "archive_years": None},
        {},
        None,  # language switch before the first document arrived
    ):
        result = _switch(summary)
        assert result == {"hidden": True, "links": []}, summary


@needs_node
def test_hidden_for_garbage() -> None:
    for garbage in (
        "2026",
        2026,
        {"0": 2026, "length": 1},
        True,
        ["2026", 2026.5, None, True, [2026], {"year": 2026}, -2026, 0],
        ['2026"><script>alert(1)</script>', "javascript:alert(1)", "../../x"],
    ):
        result = _switch({"year": 2027, "archive_years": garbage})
        assert result == {"hidden": True, "links": []}, garbage
    # Without a usable running year there is nothing to compare against.
    for year in (None, "2027", 2027.5, [2027]):
        result = _switch({"year": year, "archive_years": [2026]})
        assert result == {"hidden": True, "links": []}, year


@needs_node
def test_running_year_first_then_finished_years_newest_first() -> None:
    result = _switch({"year": 2027, "archive_years": [2026, 2025]})
    assert result["hidden"] is False
    current, newer, older = _links(result)
    assert current == {
        "wrapper": "li", "tag": "a", "text": "2027", "cls": "chip is-active",
        # The first of the three statistics sections of the running year
        # (review of 2026-11-10; it pointed at the second, #stammstrecke).
        "href": "#stoerungen", "current": "true",
        "title": None, "label": None, "hreflang": None,
    }
    assert newer == {
        "wrapper": "li", "tag": "a", "text": "2026", "cls": "chip",
        "href": "statistik-2026.html", "current": None,
        "title": "Statistik 2026 – abgeschlossenes Jahr",
        "label": "Statistik 2026 – abgeschlossenes Jahr",
        "hreflang": "de",
    }
    assert older["href"] == "statistik-2025.html" and older["text"] == "2025"


@needs_node
def test_order_and_duplicates_do_not_depend_on_the_document() -> None:
    result = _switch({"year": 2027, "archive_years": [2024, 2026, 2024, 2025, 2026]})
    assert _years(result) == ["2027", "2026", "2025", "2024"]


@needs_node
def test_bad_entries_are_ignored_and_good_ones_kept() -> None:
    mixed = ["2026", 2026, None, 1999, 2101, 2027, 2028, 2025.5, 2025, {"y": 2024}]
    result = _switch({"year": 2027, "archive_years": mixed})
    # 2027 is the running year itself, 2028 lies after it: neither has a page.
    assert _years(result) == ["2027", "2026", "2025"]
    edges = _switch({"year": 2101, "archive_years": [1999, 2000, 2100, 2101]})
    assert _years(edges) == ["2101", "2100", "2000"]
    # The upper bound holds on its own, not only through the running year.
    late = _switch({"year": 2200, "archive_years": [2100, 2101, 2199]})
    assert _years(late) == ["2200", "2100"]


@needs_node
def test_at_most_thirty_finished_years() -> None:
    """Beyond thirty finished years the row says that it leaves years out:
    one more chip leads to ``statistik.html``, which names every year
    (review of 2026-11-10: no silent caps)."""
    result = _switch({"year": 2100, "archive_years": list(range(2000, 2100))})
    links = _links(result)
    assert len(links) == 32
    assert _years(result)[:2] == ["2100", "2099"] and _years(result)[30] == "2070"
    assert links[-1] == {
        "wrapper": "li", "tag": "a", "text": "ältere Jahre", "cls": "chip",
        "href": "statistik.html", "current": None,
        "title": "ältere Jahre – Liste aller Jahre auf der Statistikseite",
        "label": "ältere Jahre – Liste aller Jahre auf der Statistikseite",
        "hreflang": "de",
    }


@needs_node
def test_the_first_cut_year_and_the_extra_chip() -> None:
    """With one page per year from 2026, 2057 is the first year with more
    finished years (31) than chips; 2056 shows all thirty without the extra
    chip."""
    full = _switch({"year": 2056, "archive_years": list(range(2026, 2056))})
    assert len(_links(full)) == 31 and _years(full)[-1] == "2026"
    cut = _switch({"year": 2057, "archive_years": list(range(2026, 2057))})
    links = _links(cut)
    assert len(links) == 32 and _years(cut)[30] == "2027"
    assert [link["text"] for link in links[1:31]] == [str(y) for y in range(2056, 2026, -1)]
    assert links[-1]["href"] == "statistik.html" and links[-1]["text"] == "ältere Jahre"
    english = _links(_switch({"year": 2057, "archive_years": list(range(2026, 2057))}, lang="en"))[-1]
    assert english["text"] == "earlier years"
    assert english["label"] == "earlier years – list of all years on the statistics page (page in German)"
    # WCAG 2.5.3: the accessible name starts with the visible text.
    assert str(english["label"]).startswith(str(english["text"]))


@needs_node
def test_english_link_says_the_page_is_in_german() -> None:
    result = _switch({"year": 2027, "archive_years": [2026]}, lang="en")
    current, archive = _links(result)
    assert current["title"] is None and current["label"] is None
    assert archive["title"] == "Statistics 2026 – finished year (page in German)"
    assert archive["label"] == archive["title"]
    # WCAG 2.5.3: the accessible name contains the visible text.
    assert str(archive["text"]) in str(archive["label"])
    assert archive["href"] == "statistik-2026.html" and archive["hreflang"] == "de"


@needs_node
def test_row_is_hidden_again_and_emptied() -> None:
    shown = {"year": 2027, "archive_years": [2026]}
    assert _switch(shown, {"year": 2027}) == {"hidden": True, "links": []}
    assert _switch(shown, {"year": 2027, "archive_years": ["x"]}) == {"hidden": True, "links": []}
    # Drawn twice (every refresh, every language switch): no doubled links.
    assert _years(_switch(shown, shown)) == ["2027", "2026"]


@needs_node
def test_hidden_row_is_shown_once_a_finished_year_arrives() -> None:
    """The markup ships the row hidden (the stub above starts visible), so
    showing it is the function's job: first a document without a finished
    year, then one with."""
    result = _switch({"year": 2027}, {"year": 2027, "archive_years": [2026]})
    assert result["hidden"] is False
    assert _years(result) == ["2027", "2026"]


@needs_node
def test_same_years_leave_the_row_untouched() -> None:
    """Every 5-minute refresh draws the row again. Rebuilding it took the
    keyboard focus off a chip and dropped it on <body> (review of
    2026-11-10), so a draw with the same years, running year and language
    keeps the drawn nodes; a change of any of them still redraws."""
    shown = {"year": 2027, "archive_years": [2026]}
    mark = "list.children[1].children[0].textContent = 'kept';"
    kept = _run([_draw(shown), mark, _draw(shown)])
    assert _years(kept) == ["2027", "kept"]
    for change in (
        [f"LANG = {json.dumps('en')};", _draw(shown)],
        [_draw({"year": 2027, "archive_years": [2026, 2025]})],
        [_draw({"year": 2028, "archive_years": [2026]})],
    ):
        redrawn = _run([_draw(shown), mark, *change])
        assert "kept" not in _years(redrawn) and "2026" in _years(redrawn), change
    # Hidden and shown again with the same years: the row is filled anew.
    again = _run([_draw(shown), _draw({"year": 2027}), _draw(shown)])
    assert again == _switch(shown)
