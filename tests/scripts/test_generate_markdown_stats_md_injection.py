"""Markdown-injection PoCs for ``scripts/generate_markdown_stats.py``.

Sentinel sibling-drift round to the 2026-05-09 CSV formula injection
fix: the dashboard renderer interpolates the same operator-/upstream-
influenced ``direction``, ``provider``, and ``location_name`` fields
into Markdown table cells, ``**bold**`` headers, and ``` `…` `` `` code-
span labels with **no escaping**. Defending the CSV write boundary
does not cover this path — the render path must defend itself.

Threat model:

* The ``data/stats/*.csv`` ledgers are append-only and live next to
  every cron-run cache file. Any path-traversal / TOCTOU primitive
  that lands a write into the repo root (the wider Sentinel audit
  trail enumerates several rounds of these) plants a row whose
  ``direction`` / ``provider`` / ``location_name`` is verbatim
  attacker-controlled.
* Historical rows committed before the 2026-05-09 formula sanitiser
  landed remain on disk and may carry Markdown-meaningful characters
  (``|``, ``<``, `` ` ``, ``[``, ``*``) that survive the writer-side
  defang (which only neutralises *formula* prefixes).
* The dashboard ``docs/statistik.md`` is rendered on every cron tick
  and committed to the repository — it is rendered by GitHub on the
  public repo browser and by every operator's local Markdown viewer.

Each test below interpolates a payload that would either (a) break
the table layout, (b) inject an unintended Markdown link / image, or
(c) close the inline-code span and leak rendering control. The fix
is to apply :func:`src.utils.text.escape_markdown_cell` /
:func:`src.utils.text.escape_markdown` (and a backtick-stripping
helper for the code-span sites) at every sink — exactly the pattern
``src/feed/reporting.py`` already uses for the analogous Feed Health
report.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_markdown_stats as script  # noqa: E402

VIENNA_TZ = ZoneInfo("Europe/Vienna")


# ---- Pipe-injection in Markdown table cells -------------------------------


def test_directions_section_escapes_pipe_in_direction() -> None:
    """A ``|`` in *direction* must not break out of the table cell.

    Pre-fix the row rendered as ``| Foo | INJECTED | extra | 1 |`` —
    that's *five* pipe-separated cells in a header that promises two,
    which mis-renders the table on every Markdown engine. Post-fix the
    pipe is backslash-escaped (``\\|``) so the row stays a 2-column row.
    """
    agg = script.StammstreckeAggregate(
        by_direction={"Floridsdorf | INJECTED | extra": 1},
        total_observations=1,
    )
    body = "\n".join(script._format_directions_section(agg))

    # Locate the data row. The header pipes are part of the layout so
    # we search by the (non-Markdown-meaningful) count token.
    data_lines = [
        line for line in body.splitlines()
        if line.startswith("|") and "1" in line and "Anzahl" not in line and "---" not in line
    ]
    assert data_lines, f"could not find direction data row in body:\n{body}"
    row = data_lines[0]
    # Every data row in a 2-column table has *exactly* 3 unescaped pipes
    # (start, between cells, end). More means the cell broke out.
    pipe_count = row.count("|") - row.count(r"\|")
    assert pipe_count == 3, (
        f"unescaped pipes in direction cell broke the table layout: "
        f"row={row!r}, unescaped pipe count={pipe_count}"
    )


def test_providers_section_escapes_pipe_in_provider() -> None:
    """A ``|`` in *provider* must not break out of the provider table cell."""
    agg = script.StoerungAggregate(
        by_provider={"ÖBB | INJECTED | extra": 1},
        total_disruptions=1,
    )
    body = "\n".join(script._format_providers_section(agg))

    data_lines = [
        line for line in body.splitlines()
        if line.startswith("|") and "1" in line and "Anzahl" not in line and "---" not in line
    ]
    assert data_lines, f"could not find provider data row in body:\n{body}"
    row = data_lines[0]
    pipe_count = row.count("|") - row.count(r"\|")
    assert pipe_count == 3, (
        f"unescaped pipes in provider cell broke the table layout: "
        f"row={row!r}, unescaped pipe count={pipe_count}"
    )


# ---- HTML / Markdown injection in **{loc}** bold header --------------------


def test_render_top_locations_neutralises_html_in_bold_header() -> None:
    """HTML in *location_name* must not flow unescaped into ``**…**``.

    Pre-fix ``f"**{loc}**"`` interpolated ``<script>alert(1)</script>``
    verbatim *outside* any code block — GFM happily renders inline
    HTML in that context, turning the dashboard into an XSS-amplifier
    on any static-site renderer that follows the spec literally
    (GitHub's renderer additionally sanitises script tags but every
    operator's local IDE / Markdown viewer has its own policy).
    Post-fix ``html.escape`` turns ``<`` / ``>`` into HTML entities so
    no tag is reconstructable in the bold-header position.

    The test scopes its assertion to the bold-header lines only —
    the bar-chart label is rendered inside `` `…` `` *inside a fenced
    code block* where CommonMark guarantees HTML-entity encoding on
    output, so a literal ``<script>`` substring inside that span is
    safe by spec.
    """
    payload = "<script>alert(1)</script>"
    agg = script.StoerungAggregate(
        by_location=Counter({payload: 1}),
        by_location_hour={payload: {7: 1}},
        total_disruptions=1,
    )
    body = "\n".join(script.render_top_locations(agg, top_n=1))
    bold_lines = [line for line in body.splitlines() if line.startswith("**") and line.endswith("**")]
    assert bold_lines, f"expected at least one bold-header line, got body:\n{body}"
    for line in bold_lines:
        assert "<script>" not in line, (
            f"<script> tag survived into bold-header line:\n{line}"
        )


def test_render_top_locations_neutralises_link_in_bold_header() -> None:
    """A Markdown link in *location_name* must not become a clickable link."""
    payload = "Karlsplatz [click](http://attacker.example/leak)"
    agg = script.StoerungAggregate(
        by_location=Counter({payload: 1}),
        by_location_hour={payload: {7: 1}},
        total_disruptions=1,
    )
    body = "\n".join(script.render_top_locations(agg, top_n=1))
    # The unescaped ``[click](http://…)`` Markdown-link syntax must not
    # appear — GitHub renders that as a hyperlink, turning the
    # dashboard into a phishing surface.
    assert "[click](http://attacker.example/leak)" not in body, (
        f"unescaped Markdown link survived into bold header:\n{body}"
    )


# ---- Backtick in location code-span label ---------------------------------


def test_render_top_locations_replaces_backtick_in_codespan_label() -> None:
    """A backtick in *location_name* must not close the inline code span.

    The hotspot bar chart wraps the label in `` `…` `` inside a fenced
    code block. A literal backtick in the label closes the inline code
    early, leaking subsequent ``│`` separators and the bar glyphs as
    plain text. CommonMark code spans treat backslashes as literal, so
    the only safe defence is *replacement* (apostrophe is the
    project-wide convention — see ``src/feed/reporting.py``
    ``_sanitize_code_span``).
    """
    payload = "Foo`evil`bar"
    agg = script.StoerungAggregate(
        by_location=Counter({payload: 1}),
        by_location_hour={payload: {7: 1}},
        total_disruptions=1,
    )
    body = "\n".join(script.render_top_locations(agg, top_n=1))
    # Pre-fix the code-span line was `` `Foo`evil`bar               ` `` —
    # i.e. the label contained the *literal* backtick, prematurely
    # closing the code span. Post-fix backticks are replaced with
    # apostrophes so the label is `` `Foo'evil'bar` `` which keeps the
    # code span intact.
    assert "`Foo`evil`bar" not in body, (
        f"raw backtick survived into code-span label and broke "
        f"out of the inline code wrapping:\n{body}"
    )


# ---- Newline injection breaks the table row -------------------------------


def test_directions_section_neutralises_newline_in_direction() -> None:
    """An embedded newline in *direction* must not split the table row.

    ``csv.reader`` happily parses a quoted multi-line cell; a planted
    row with a newline-bearing direction otherwise injects arbitrary
    Markdown (a header, a fenced code-block fence, …) on the
    subsequent line.
    """
    payload = "Foo\n## INJECTED HEADER"
    agg = script.StammstreckeAggregate(
        by_direction={payload: 1},
        total_observations=1,
    )
    body = "\n".join(script._format_directions_section(agg))
    # The injected ``## INJECTED HEADER`` line must not appear at the
    # start of any rendered line — otherwise it becomes a real
    # second-level header in the dashboard.
    for line in body.splitlines():
        assert not line.startswith("## INJECTED HEADER"), (
            f"newline-injected Markdown header survived rendering:\n{body}"
        )


# ---- End-to-end: poisoned CSV produces safe Markdown ----------------------


def test_render_markdown_neutralises_poisoned_csv_row(tmp_path: Path) -> None:
    """Smoking-gun PoC: a poisoned CSV row produces a safe dashboard.

    Plants a single ``stoerungen_2026.csv`` row whose ``provider``
    field carries the cartesian-product Markdown payload and renders
    the full dashboard. Pre-fix the rendered Markdown contains a
    usable ``[click](…)`` link, an unescaped ``<img>`` / ``<script>``
    HTML attribute surface, and a broken table layout. Post-fix none
    of those substrings appear in the rendered output.
    """
    payload = "ÖBB | <img src=x onerror=alert(1)> | [click](http://attacker.example/leak)"
    csv_path = tmp_path / "stoerungen_2026.csv"
    header = ",".join(("timestamp", "weekday", "hour", "provider", "location_name"))
    row = ",".join(
        (
            "2026-05-04T07:30:00+02:00",
            "Mo",
            "07",
            f'"{payload}"',  # CSV-quote the field so embedded ``|`` survives intact
            "Karlsplatz",
        )
    )
    csv_path.write_text(header + "\n" + row + "\n", encoding="utf-8")

    sm_rows, st_rows = script.collect_year_data(2026, stats_dir=tmp_path)
    assert len(st_rows) == 1, "PoC fixture row should round-trip"
    assert payload in st_rows[0].provider, "PoC: payload should reach the renderer"

    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        stammstrecke=script.aggregate_stammstrecke(sm_rows),
        stoerungen=script.aggregate_stoerungen(st_rows),
    )

    # 1. Markdown link syntax must not survive.
    assert "[click](http://attacker.example/leak)" not in md, (
        "Markdown link survived dashboard rendering"
    )
    # 2. HTML tags must be defanged (HTML-escape converts ``<`` / ``>``
    #    to ``&lt;`` / ``&gt;``).
    assert "<img " not in md, "HTML <img> tag survived dashboard rendering"
    # 3. Pipe characters in payload must not multiply table cells —
    #    every data row in a 2-column table has exactly 3 unescaped pipes.
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        if "Anzahl" in line or "---" in line:
            continue
        if "Quelle" in line:
            continue
        unescaped_pipes = line.count("|") - line.count(r"\|")
        if " | " not in line:
            continue
        # Provider table rows: 2 columns => 3 unescaped pipes.
        # Stammstrecke / direction rows: 2 columns => 3 unescaped pipes.
        # Summary table rows: 2 columns => 3 unescaped pipes.
        assert unescaped_pipes <= 3, (
            f"poisoned CSV row broke a 2-column table layout: row={line!r}"
        )
