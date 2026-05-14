"""Sentinel: GFM strikethrough injection via ``escape_markdown``.

The canonical Markdown escape helper
:func:`src.utils.text.escape_markdown` escapes ``[]()*_`@<>#`` (the
``#`` was added in the immediately-prior Sentinel round), but the
``~`` character is missing from the escape set. GitHub Flavored
Markdown (GFM) parses the bigram ``~~text~~`` as strikethrough
(``<del>text</del>``) — the GFM strikethrough extension is enabled
by every renderer in the data path:

* The GitHub web UI rendering of ``docs/feed-health.md`` (GFM-
  rendered on every navigate to the file on github.com).
* GitHub Pages serving ``docs/feed-health.md`` with the default
  Jekyll / kramdown_GFM input mode.
* The auto-submitted GitHub Issue body rendered by GitHub's own
  GFM renderer — strikethrough always renders for Issues and PRs.
* The ``docs/stations_validation_report.md`` artefact (rendered the
  same way as ``docs/feed-health.md``).

A hostile warning / error / exception message carrying the bigram
``~~payload~~`` therefore lands strike-through inline content
inside every list item / table cell that flows through
:func:`escape_markdown` or :func:`escape_markdown_cell`. The helper
is consumed by the bullet-list interpolation in
:func:`src.feed.reporting.render_feed_health_markdown` and
:func:`src.feed.reporting._build_github_issue_body`::

    lines.append(f"- {escape_markdown(warning)}")
    lines.append(f"- {escape_markdown(error)}")

and by every ``_safe_md`` callsite in
:func:`src.utils.stations_validation.ValidationReport.to_markdown`.

Threat model
------------
Warning / error / exception strings originate from every provider's
error path (``provider_error``, ``add_warning``,
``add_error_message``), which forward upstream / network-derived text
through :func:`src.feed.reporting.clean_message`. ``clean_message``
collapses whitespace and strips invisible Trojan-Source primitives
but does NOT strip ASCII ``~``. A compromised provider, MITM,
hostile DNS response, or any of the prior-round env-override leak
surfaces can plant a warning / error containing
``~~CRITICAL~~ — bereits behoben`` and have GFM render
``<del>CRITICAL</del>`` on the public artefact and the
auto-submitted GitHub Issue body.

The attack shape is **visual deception of the operator**: the
attacker uses strikethrough to imply that a flagged problem is
already resolved, suppress severity sentinels, or fake an
acknowledgement / correction inline. The same operator who is
expected to triage off the rendered report cannot distinguish
struck-through text from text that was never struck through —
strikethrough is an active misinformation primitive on the report.

Sinks
-----
1. ``docs/feed-health.md`` — committed to the repository by the
   ``update-cycle.yml`` cron job and rendered on the public GitHub
   Pages site + the GitHub web UI (both render GFM strikethrough).
2. ``submit_auto_issue`` GitHub Issue body — opened on every failed
   feed build, visible to every repo watcher via the notifications
   channel. Strikethrough renders inline.
3. ``docs/stations_validation_report.md`` — auto-committed by the
   ``update-stations.yml`` cron workflow. Same blast radius.

Severity: MEDIUM. No JS execution (HTML metacharacters are entity-
encoded by the leading ``html.escape``), no phishing (``[]()`` are
already backslash-escaped). The attack is purely visual deception
of the operator + document-content tampering — the same threat
class as the immediately-prior ATX-heading-injection round and the
2026-05-09 Markdown-injection drift rounds.

Fix shape
---------
Add ``~`` to the escape set in :func:`escape_markdown`. The
backslash-escape ``\\~`` renders as literal ``~`` in CommonMark and
GFM (``~`` is ASCII punctuation per CommonMark 2.4, so backslash
escapes apply). Legitimate text containing ``~`` (transit field
abbreviations, file paths like ``~/foo``, approximation symbols)
is visually unchanged on the rendered page — ``\\~`` becomes
``~``.

This mirrors the canonical fix shape of the prior Sentinel round
(``#`` heading-injection): widen the canonical helper's escape
set, write PoC tests asserting the post-fix invariant on every
consumer, and pin the contract via an inventory walker so a
future regression that drops ``~`` from the defang set is caught
at unit-test resolution.
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


def test_escape_markdown_escapes_tilde_pair() -> None:
    """``~~payload~~`` (GFM strikethrough) must be defanged.

    Pre-fix: ``escape_markdown("~~payload~~")`` returns the string
    unchanged. A downstream ``f"- {…}"`` interpolation then produces
    ``"- ~~payload~~"`` which GFM renders as
    ``<ul><li><del>payload</del></li></ul>``.

    Post-fix: each ``~`` is backslash-escaped, so the rendered
    Markdown source is ``- \\~\\~payload\\~\\~`` — GFM sees four
    literal tildes (CommonMark escapes ``~`` as ASCII punctuation)
    and does NOT open a strikethrough span. The visible output on
    GitHub is the literal text ``~~payload~~``.
    """
    result = escape_markdown("~~payload~~")
    assert result == r"\~\~payload\~\~", (
        f"GFM strikethrough delimiters must be backslash-escaped (got {result!r})"
    )
    # Defence-in-depth invariant: the unescaped bigram MUST NOT survive.
    assert "~~" not in result, (
        f"Bigram '~~' survived escape_markdown (got {result!r}) — GFM "
        f"renders this as <del>…</del>"
    )


def test_escape_markdown_escapes_single_tilde() -> None:
    """Every ``~`` is backslash-escaped, even outside the
    strikethrough bigram.

    Defense in depth: CommonMark / GFM strikethrough requires *two*
    tildes, but escaping every occurrence keeps the helper's
    contract uniform — callers cannot accidentally re-open the
    injection by concatenating two adjacent escape_markdown outputs
    where each had one bare ``~`` at the boundary.

    Example:: ``escape_markdown("~end")`` + ``escape_markdown("start~")``
    yields ``"~endstart~"`` if tildes survive (which forms a
    strikethrough across the concat boundary), but ``"\\~end\\start~"``
    if every tilde is escaped — the concat-boundary attack is shut
    by per-character escaping at the canonical helper.
    """
    result = escape_markdown("a~b")
    assert result == r"a\~b", (
        f"Every '~' must be backslash-escaped (got {result!r})"
    )


def test_escape_markdown_escapes_subscript_like_payload() -> None:
    """Single-tilde subscript-like payloads (kramdown / pandoc
    extensions some renderers honour) must be defanged.

    GitHub's GFM renderer does NOT honour single-tilde subscript,
    but kramdown / pandoc / several Markdown-it plugins do.
    ``docs/feed-health.md`` is served via GitHub Pages — the
    default kramdown_GFM input mode does not include subscript,
    but a future ``_config.yml`` change or a third-party rebuild
    pipeline could re-open the injection. Escaping every ``~``
    closes both the GFM-bigram path and the single-tilde
    subscript path in one cut.
    """
    result = escape_markdown("H~2~O")
    # Each '~' is independently escaped — defends both two-tilde and
    # single-tilde syntaxes uniformly.
    assert result == r"H\~2\~O", (
        f"Subscript-like single-tilde delimiters must be escaped "
        f"(got {result!r})"
    )


def test_escape_markdown_preserves_legitimate_text_with_tilde() -> None:
    """The fix must NOT change the rendered output of legitimate
    text that contains ``~`` (path abbreviations, approximation
    symbols).

    ``\\~`` renders as literal ``~`` in CommonMark / GFM (since
    ``~`` is ASCII punctuation per CommonMark 2.4), so the visible
    output on the rendered page is unchanged.
    """
    # Path abbreviation (~/foo, common in shell traces interpolated
    # into provider error messages).
    assert escape_markdown("~/foo") == r"\~/foo"
    # Approximation symbol (~5 minutes).
    assert escape_markdown("~5") == r"\~5"
    # Text without tildes is unchanged.
    assert escape_markdown("no tilde here") == "no tilde here"


def test_escape_markdown_cell_escapes_tilde_pair() -> None:
    """``escape_markdown_cell`` composes ``escape_markdown`` so it
    inherits the tilde fix automatically.

    Strikethrough renders inside GFM table cells (cells parse
    inline-only content, and ``~~…~~`` is inline). The provider-
    overview table in :func:`render_feed_health_markdown` and
    :func:`_build_github_issue_body` interpolates ``status`` /
    ``detail`` / ``name`` fields via this helper, so an attacker
    can plant strike-through text directly in the table.
    """
    result = escape_markdown_cell("~~table-strikethrough~~")
    assert "~~" not in result, (
        f"Bigram '~~' survived escape_markdown_cell (got {result!r})"
    )
    # The composed helper retains the original cell-specific escape
    # (the pipe character) and inherits the new tilde escape.
    assert r"\~\~" in result, (
        f"Backslash-escaped tildes missing from cell output (got {result!r})"
    )


# ---------------------------------------------------------------------------
# Integration-level PoC via render_feed_health_markdown.
# ---------------------------------------------------------------------------


_STRIKETHROUGH_ATTACK_PAYLOAD = "~~CRITICAL~~ already handled, ignore"


def _minimal_metrics() -> FeedHealthMetrics:
    return FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )


def test_warning_with_strikethrough_payload_neutralised() -> None:
    """Render a feed-health report with a warning carrying the
    strikethrough bigram; assert the rendered Markdown source
    defangs the bigram.

    Pre-fix: the rendered Markdown contains the raw
    ``- ~~CRITICAL~~ already handled, ignore`` line, which GFM
    parses as ``<li><del>CRITICAL</del> already handled, ignore</li>``
    — visual misinformation injected into the public artefact.

    Post-fix: every ``~`` is backslash-escaped at the renderer
    boundary, so the literal text ``~~CRITICAL~~`` appears on the
    rendered page and the operator sees the attack payload as
    plain text, not as a struck-through directive.
    """
    report = RunReport(statuses=[("provider", True)])
    report.add_warning(_STRIKETHROUGH_ATTACK_PAYLOAD)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    # Must NOT contain the raw ``~~...~~`` bigram that GFM parses
    # as strikethrough.
    assert "~~CRITICAL~~" not in markdown, (
        "Rendered Markdown contains unescaped '~~CRITICAL~~' which "
        "GFM renders as <del>CRITICAL</del>"
    )

    # Must contain the backslash-escaped form.
    assert r"\~\~CRITICAL\~\~" in markdown, (
        "Rendered Markdown must contain backslash-escaped tildes "
        "(\\~\\~CRITICAL\\~\\~)"
    )


def test_error_with_strikethrough_payload_neutralised() -> None:
    """Mirror of the warning-path test on the error-message path.

    The ``- {escape_markdown(error)}`` interpolation sits at
    :file:`src/feed/reporting.py:647` and :file:`...:1107` — same
    attack-surface shape as the warning interpolation.
    """
    report = RunReport(statuses=[("provider", False)])
    report.provider_error("provider", _STRIKETHROUGH_ATTACK_PAYLOAD)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    assert "~~CRITICAL~~" not in markdown, (
        "Error-path rendered Markdown contains unescaped strikethrough "
        "bigram"
    )
    assert r"\~\~CRITICAL\~\~" in markdown, (
        "Error-path rendered Markdown must contain backslash-escaped "
        "tildes"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "~~bold strikethrough~~",
        "Update: ~~broken~~ working again (lie)",
        "[~~Old link text~~](https://example.com)",
        "Track ~~12~~ closed",
        "Mehrere ~~tildes~~ in ~~einer~~ Zeile",
    ],
)
def test_strikethrough_payloads_neutralised_at_renderer_boundary(
    payload: str,
) -> None:
    """Parametrised PoC over a range of plausible attacker payloads.

    Each payload exercises a different deception scenario:

    * "bold strikethrough" — vanilla strikethrough.
    * "broken → working again" — misinformation about service state.
    * "Old link text" — strikethrough wrapped around link-like
      text; pre-fix the brackets are also escaped, but the
      strikethrough is not.
    * "Track 12 closed" — strikethrough crosses a numeric token,
      deceiving the operator about which track is closed.
    * "Mehrere tildes in einer Zeile" — German content with
      multiple strikethrough runs in one line.

    Each must round-trip through the renderer with NO unescaped
    ``~~`` bigrams.
    """
    report = RunReport(statuses=[("provider", True)])
    report.add_warning(payload)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    assert "~~" not in markdown, (
        f"Rendered Markdown contains unescaped '~~' for payload "
        f"{payload!r} — GFM strikethrough renders inline"
    )


# ---------------------------------------------------------------------------
# Audit walker: the canonical inline escape MUST cover '~'.
# ---------------------------------------------------------------------------


def test_canonical_escape_set_includes_tilde() -> None:
    """Inventory invariant: the canonical Markdown-escape helper
    must defang ``~`` as part of the structural / inline-formatting
    metacharacter set.

    The full minimum set after this round is::

        [ ] ( ) * _ ` @ < > # ~

    Each character has a documented attack shape at the bullet-
    list / table-cell / paragraph boundary; ``~`` is the GFM
    strikethrough delimiter (``~~text~~`` → ``<del>text</del>``).

    The defang mechanism is backslash escape (``\\~``) — CommonMark
    backslash-escapes apply to ASCII punctuation, and ``~`` is in
    the punctuation set per CommonMark 2.4. ``\\~`` renders as
    literal ``~`` on every CommonMark / GFM renderer.

    Removal of ``~`` from the defang set fails the invariant.
    """
    result = escape_markdown("~")
    assert result == r"\~", (
        f"Canonical Markdown-escape helper dropped '~' from the "
        f"defang set (got {result!r})"
    )

    # Inventory: every char in the canonical full set must produce
    # either a backslash-escaped or HTML-entity-encoded form.
    html_entity_chars = {"<": "&lt;", ">": "&gt;"}
    required = set("[]()*_`@<>#~")
    for ch in required:
        out = escape_markdown(ch)
        if ch in html_entity_chars:
            assert html_entity_chars[ch] in out, (
                f"Canonical Markdown-escape helper dropped {ch!r} from "
                f"the defang set (got {out!r})"
            )
        else:
            assert "\\" + ch in out, (
                f"Canonical Markdown-escape helper dropped {ch!r} from "
                f"the defang set (got {out!r})"
            )


def test_canonical_escape_set_cell_includes_tilde() -> None:
    """The cell variant inherits the canonical inline contract by
    composition — the inventory walker applies to ``escape_markdown_cell``
    too.

    GFM tables parse inline content (including strikethrough) inside
    cells. The provider-overview table in ``render_feed_health_markdown``
    interpolates ``status`` / ``detail`` / ``name`` via
    ``escape_markdown_cell`` so a struck-through provider name like
    ``~~Wiener Linien~~`` would render with ``<del>`` inside the table.
    """
    result = escape_markdown_cell("~")
    assert result == r"\~", (
        f"Cell variant must inherit the '~' escape from "
        f"escape_markdown (got {result!r})"
    )
