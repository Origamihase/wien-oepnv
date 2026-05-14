"""Sentinel: Markdown ATX-heading injection via ``escape_markdown``.

The canonical Markdown escape helper
:func:`src.utils.text.escape_markdown` previously escaped only
``[]()*_`@<>``. The ``#`` character was missing from the escape set,
so a hostile string starting with ``# ...`` survived the helper
unchanged. The helper is consumed by
:func:`src.feed.reporting.render_feed_health_markdown` /
:func:`src.feed.reporting._build_github_issue_body` via the
bullet-list interpolation::

    lines.append(f"- {escape_markdown(warning)}")
    lines.append(f"- {escape_markdown(error)}")

CommonMark and GFM parse ``- # heading`` as a list item whose
content is an ATX heading — ``<ul><li><h1>heading</h1></li></ul>``.
A hostile warning / error / exception message therefore lands a
fresh ``<h1>`` … ``<h6>`` inside the public ``docs/feed-health.md``
artefact and the auto-submitted GitHub Issue body.

Threat model
------------
The warning / error strings originate from every provider's error
path (``provider_error``, ``add_warning``, ``add_error_message``),
which forward upstream / network-derived text through
:func:`src.feed.reporting.clean_message`. ``clean_message`` collapses
whitespace and strips invisible Trojan-Source primitives but does
NOT strip ASCII ``#``. A compromised provider, MITM, or hostile DNS
response can therefore plant a warning / error string that begins
with ``# evil heading payload`` and have it render as a heading.

Sinks
-----
1. ``docs/feed-health.md`` — committed to the repository by the
   ``update-cycle.yml`` cron job and rendered on the public GitHub
   Pages site. Heading injection corrupts the document outline,
   manipulates GitHub's auto-generated anchors, and produces a
   misleading large-font heading inside a bullet list.
2. ``submit_auto_issue`` GitHub Issue body — opened on every failed
   feed build, visible to every repo watcher via the notifications
   channel. Same heading-injection blast radius.

Severity: MEDIUM. No JS execution (HTML is entity-encoded), no
phishing (links are already escaped via ``[]()``). The attack is
visual deception + document-structure manipulation + anchor
spoofing — the same threat class as PR #1473 / #1472 / #1471 etc.
which closed sibling Markdown / clear-text-logging drifts.

Fix shape
---------
Add ``#`` to the escape set in :func:`escape_markdown`. Mirrors the
existing canonical pattern (``\\[``, ``\\]``, ``\\(``, ``\\)``,
``\\*``, ``\\_``, ``\\``\\``, ``\\@``, ``\\<``, ``\\>``). The
backslash-escaped ``\\#`` renders as literal ``#`` in CommonMark /
GFM so legitimate text containing ``#`` (e.g. "C# code",
"issue #123") is unchanged on the rendered page.
"""

from __future__ import annotations

import pytest

from src.feed.reporting import (
    FeedHealthMetrics,
    RunReport,
    render_feed_health_markdown,
)
from src.utils.text import escape_markdown, escape_markdown_cell


# ---------------------------------------------------------------------------
# Unit-level PoCs on the canonical helper.
# ---------------------------------------------------------------------------


def test_escape_markdown_escapes_hash_at_start() -> None:
    """A leading ``#`` must be backslash-escaped.

    Pre-fix: ``escape_markdown("# heading")`` returns ``"# heading"``
    (the ``#`` survives), so a downstream ``f"- {…}"`` interpolation
    produces ``"- # heading"`` which renders as
    ``<ul><li><h1>heading</h1></li></ul>``.
    """
    result = escape_markdown("# heading")
    assert result == r"\# heading", (
        f"ATX heading marker must be escaped (got {result!r})"
    )


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_escape_markdown_escapes_all_heading_levels(level: int) -> None:
    """All six ATX heading levels (h1..h6) must be defanged.

    A hostile warning ``"### evil"`` would render as ``<h3>`` inside
    a list item pre-fix. Escaping the first ``#`` is sufficient to
    break the ATX-heading parse, but we backslash-escape every
    ``#`` for defense in depth (the rendered output is identical —
    CommonMark renders ``\\#`` as literal ``#``).
    """
    payload = "#" * level + " evil"
    result = escape_markdown(payload)
    expected = ("\\#" * level) + " evil"
    assert result == expected, (
        f"ATX heading at level {level} must be escaped: "
        f"expected {expected!r}, got {result!r}"
    )


def test_escape_markdown_escapes_hash_anywhere() -> None:
    """Defense in depth: every ``#`` must be escaped, not just at start.

    CommonMark only treats ``#`` as a heading marker at line start,
    but escaping every occurrence keeps the helper's contract
    uniform — callers cannot accidentally re-expose the heading
    injection by concatenating text that places a previously-mid-
    string ``#`` at line start (e.g. ``f"\\n{escape_markdown(x)}"``).
    """
    result = escape_markdown("foo # bar # baz")
    assert result == r"foo \# bar \# baz", (
        f"Every '#' must be escaped (got {result!r})"
    )


def test_escape_markdown_preserves_legitimate_text() -> None:
    """The fix must NOT change rendering of legitimate text.

    ``\\#`` renders as literal ``#`` in CommonMark / GFM, so the
    operator-facing artefact's visible output stays unchanged.
    """
    # Issue references, C#, hashtag-like content all still escape.
    assert escape_markdown("issue #123") == r"issue \#123"
    assert escape_markdown("C# code") == r"C\# code"
    assert escape_markdown("no hash here") == "no hash here"


def test_escape_markdown_cell_escapes_hash() -> None:
    """``escape_markdown_cell`` composes ``escape_markdown`` so it
    inherits the fix automatically.

    GFM table cells parse inline content only (no block-level
    headings), so the hash injection is not directly exploitable
    via table cells. But the cell helper documents itself as a
    superset of the inline escape contract, and the inventory
    walker test below relies on the same character set being
    escaped at both call sites.
    """
    assert escape_markdown_cell("# in cell") == r"\# in cell"


# ---------------------------------------------------------------------------
# Integration-level PoC via render_feed_health_markdown.
# ---------------------------------------------------------------------------


_HEADING_ATTACK_PAYLOAD = "# EVIL HEADING from hostile upstream"


def _minimal_metrics() -> FeedHealthMetrics:
    return FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )


def test_warning_starting_with_hash_does_not_become_heading() -> None:
    """Render a feed-health report with a warning carrying a hash
    payload; assert the rendered Markdown source neutralises the
    heading marker.

    Pre-fix, the rendered Markdown contained the substring
    ``- # EVIL HEADING …`` verbatim, which CommonMark / GFM parses
    as a list item containing an ATX-1 heading. Post-fix, the ``#``
    is backslash-escaped (``- \\# EVIL HEADING …``), which renders
    as a plain bullet item with literal ``#`` text.
    """
    report = RunReport(statuses=[("provider", True)])
    report.add_warning(_HEADING_ATTACK_PAYLOAD)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    # Must NOT contain the raw ``- # …`` form that CommonMark parses
    # as ``<li><h1>…</h1></li>``.
    assert (
        f"- {_HEADING_ATTACK_PAYLOAD}" not in markdown
    ), (
        "Markdown source contains unescaped '- # ...' which renders "
        "as an embedded heading"
    )

    # Must contain the backslash-escaped form. The leading ``#`` of
    # the payload becomes ``\#`` so the line is ``- \# EVIL …``.
    expected = "- \\" + _HEADING_ATTACK_PAYLOAD
    assert expected in markdown, (
        "Markdown source must contain the backslash-escaped form "
        f"(expected {expected!r} in output)"
    )


def test_error_starting_with_hash_does_not_become_heading() -> None:
    """Sibling assertion on the error rendering path
    (``render_feed_health_markdown`` line 647)."""
    report = RunReport(statuses=[("provider", True)])
    report.add_error_message(_HEADING_ATTACK_PAYLOAD)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    assert (
        f"- {_HEADING_ATTACK_PAYLOAD}" not in markdown
    ), (
        "Markdown source contains unescaped '- # ...' which renders "
        "as an embedded heading"
    )
    expected = "- \\" + _HEADING_ATTACK_PAYLOAD
    assert expected in markdown


@pytest.mark.parametrize(
    "payload",
    [
        "# Level 1",
        "## Level 2",
        "### Level 3",
        "#### Level 4",
        "##### Level 5",
        "###### Level 6",
    ],
)
def test_all_heading_levels_neutralised_at_renderer_boundary(payload: str) -> None:
    """Every ATX heading depth (h1..h6) reachable inside a list
    item must be defanged at the renderer boundary."""
    report = RunReport(statuses=[("provider", True)])
    report.add_warning(payload)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    # The raw ``- # ...`` / ``- ## ...`` / ... form would create a
    # heading inside the list item.
    assert f"- {payload}" not in markdown


# ---------------------------------------------------------------------------
# Audit walker: the canonical inline escape MUST cover '#'.
# ---------------------------------------------------------------------------


def test_canonical_escape_set_includes_hash() -> None:
    """Inventory invariant: the canonical Markdown-escape helper
    must defang the full set of structural metacharacters that can
    appear at the START of a bullet-list item's content and
    promote that content to a block-level structure.

    The minimum set is::

        [ ] ( ) * _ ` @ < > #

    Bracket/paren = link/image syntax; ``*``/``_`` = emphasis;
    ``` ` ``` = inline code; ``@`` = mention; ``<``/``>`` = HTML
    autolink + blockquote marker; ``#`` = ATX heading marker.

    The defang mechanism is either:
      * backslash escape (``\\X``) — applied by the for-loop, OR
      * HTML-entity encoding (``&lt;`` / ``&gt;``) — applied by the
        leading ``html.escape`` call for ``<`` and ``>``.

    Both renderings produce literal characters in HTML output, so
    either is a sufficient defence. Removal of any element from the
    defang set fails the invariant.
    """
    # ``<`` and ``>`` are HTML-entity-encoded by ``html.escape``; the
    # rest are backslash-escaped by the for-loop.
    html_entity_chars = {"<": "&lt;", ">": "&gt;"}
    required = set("[]()*_`@<>#")
    for ch in required:
        out = escape_markdown(ch)
        if ch in html_entity_chars:
            assert html_entity_chars[ch] in out, (
                f"Canonical Markdown-escape helper dropped {ch!r} from the "
                f"defang set (got {out!r})"
            )
        else:
            assert "\\" + ch in out, (
                f"Canonical Markdown-escape helper dropped {ch!r} from the "
                f"defang set (got {out!r})"
            )
