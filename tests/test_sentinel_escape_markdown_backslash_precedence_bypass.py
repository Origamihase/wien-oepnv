"""Sentinel: backslash-precedence bypass of ``escape_markdown``.

The canonical Markdown escape helper
:func:`src.utils.text.escape_markdown` backslash-escapes every
character in the canonical inline-formatting set
``[]()*_`@<>#~`` (the ``#`` and ``~`` were added in the immediately-
prior Sentinel rounds). The helper does NOT escape the backslash
character itself, however, which re-opens every prior round of
inline-formatting defence via the CommonMark backslash-precedence
rule (`CommonMark 2.4 Backslash escapes
<https://spec.commonmark.org/0.31.2/#backslash-escapes>`_):

  > Any ASCII punctuation character may be backslash-escaped.

CommonMark parses the source left-to-right and consumes ``\\\\``
as a literal backslash. So when the input contains a literal
``\\`` immediately before a Markdown metacharacter, the helper
adds a second backslash before the metacharacter, but the FIRST
two backslashes combine into a literal ``\\`` and the second
backslash now sits before an UNESCAPED metacharacter.

Worked example (current helper, pre-fix):

  Input (8 chars)              : ``\\*EMPHASIS\\*``
  After ``html.escape``         : ``\\*EMPHASIS\\*`` (unchanged)
  After ``replace("*", "\\*")``: ``\\\\*EMPHASIS\\\\*``
  CommonMark parses
    ``\\\\``                    -> literal ``\\``
    ``*``                       -> emphasis OPEN
    ``EMPHASIS``                -> text
    ``\\\\``                    -> literal ``\\``
    ``*``                       -> emphasis CLOSE
  Rendered HTML                 : ``<em>EMPHASIS</em>``

The helper is consumed by the bullet-list interpolation in
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
``add_error_message``), which forward upstream / network-derived
text through :func:`src.feed.reporting.clean_message`.
``clean_message`` collapses whitespace and strips invisible
Trojan-Source primitives but does NOT strip ASCII backslash. A
compromised provider, MITM, hostile DNS response, or any of the
prior-round env-override leak surfaces can plant a warning / error
of the form ``"\\*CRITICAL\\* already handled"`` and have GFM
render ``<em>CRITICAL</em>`` on the public artefact and the auto-
submitted GitHub Issue body.

The attack shape is **complete bypass of every prior canonical-
escape round**:

  * ``\\*payload\\*``       -> ``<em>payload</em>``       (italic)
  * ``\\_payload\\_``       -> ``<em>payload</em>``       (italic via _)
  * ``\\*\\*payload\\*\\*`` -> ``<em><em>payload</em></em>`` (strong)
  * ``\\`payload\\```       -> ``<code>payload</code>``   (code span)

Each of these defeats the bypass-mitigation that was the explicit
charter of every prior ``escape_markdown`` widening round (#1471,
#1472, #1473, #1476, #1477) — the canonical helper's contract
guarantees inline-formatting suppression, and the backslash-
precedence rule defeats that contract on every metacharacter at
once.

Sinks
-----
1. ``docs/feed-health.md`` — committed to the repository by the
   ``update-cycle.yml`` cron job and rendered on the public GitHub
   Pages site + the GitHub web UI (both render CommonMark
   backslash escapes per spec).
2. ``submit_auto_issue`` GitHub Issue body — opened on every failed
   feed build, visible to every repo watcher via the notifications
   channel. Issue bodies render CommonMark.
3. ``docs/stations_validation_report.md`` — auto-committed by the
   ``update-stations.yml`` cron workflow. Same blast radius.

Severity: MEDIUM-HIGH. No JS execution (HTML metacharacters are
entity-encoded by the leading ``html.escape``). The attack is
visual deception + document-content tampering — same threat class
as the prior ``escape_markdown`` rounds, but **structurally re-
opens every one** of them simultaneously.

Fix shape
---------
Escape the backslash itself **first** in :func:`escape_markdown`,
**before** the rest of the canonical inline-formatting loop. The
sequence becomes:

  1. ``html.escape`` (entity-encodes ``&<>"'``).
  2. Replace ``\\``    with ``\\\\`` (escape backslash).
  3. Replace each char in ``[]()*_`@<>#~`` with ``\\<char>``.

Each ``\\`` in the input is now first doubled to ``\\\\``, and
each subsequent metacharacter is prefixed with one additional
``\\``. The CommonMark parse becomes:

  Input ``\\*``   ->   step 2: ``\\\\*``   ->   step 3: ``\\\\\\*``
  CommonMark parses ``\\\\``  -> literal ``\\``
                    ``\\*``    -> literal ``*``
  Rendered                   ->   ``\\*`` (literal)

Legitimate text containing ``\\`` (Windows paths ``C:\\Users\\foo``,
escape sequences in error messages, regex patterns) is visually
preserved on the rendered page — each ``\\`` renders as a single
literal backslash.

This mirrors the canonical fix shape of the prior Sentinel rounds:
widen the canonical helper's defence set, write PoC tests asserting
the post-fix invariant on every consumer, and pin the contract via
an inventory invariant so a future regression that drops ``\\``
from the defang set (or moves it after the per-char loop) is caught
at unit-test resolution.
"""

from __future__ import annotations

import re

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


def test_escape_markdown_escapes_lone_backslash() -> None:
    """A literal backslash in the input must itself be backslash-
    escaped.

    Pre-fix: ``escape_markdown("\\\\")`` returns the input unchanged
    (single backslash). When interpolated as ``"- \\\\*"`` and parsed
    by CommonMark, the ``\\\\`` becomes a literal ``\\`` and the
    following ``*`` is now unescaped, opening an emphasis delimiter.

    Post-fix: each ``\\`` in the input is doubled, so the helper
    returns ``"\\\\\\\\"`` (two-char string of two backslashes).
    CommonMark parses ``\\\\\\\\`` as ``\\\\`` -> literal ``\\``,
    rendering a single backslash on the page.
    """
    result = escape_markdown("\\")
    assert result == r"\\", (
        f"escape_markdown dropped a literal backslash (got {result!r})"
    )


def test_escape_markdown_escapes_backslash_then_asterisk() -> None:
    """The canonical attack: ``\\*payload\\*`` must NOT render as
    emphasis.

    Pre-fix: ``escape_markdown("\\\\*payload\\\\*")`` returns
    ``"\\\\\\\\*payload\\\\\\\\*"`` (each ``*`` prefixed with one
    backslash). CommonMark parses the ``\\\\\\\\`` runs as ``\\\\`` ->
    literal ``\\``, then the next ``*`` is an UNESCAPED emphasis
    delimiter. With two unescaped ``*`` chars, an emphasis span
    opens and closes -> ``<em>payload</em>``.

    Post-fix: the leading backslash escape doubles each ``\\``
    BEFORE the metacharacter loop runs, so the output is
    ``"\\\\\\\\\\\\*payload\\\\\\\\\\\\*"`` (three backslashes before
    each ``*``). CommonMark parses ``\\\\\\\\\\\\*`` as ``\\\\`` ->
    literal ``\\``, ``\\*`` -> literal ``*`` — no emphasis span.
    """
    payload = "\\*payload\\*"
    result = escape_markdown(payload)

    # Pre-fix would produce ``\\\\*payload\\\\*`` (two backslashes
    # before each ``*``); the CommonMark parse pair-collapses the
    # backslashes and the ``*`` becomes an unescaped emphasis
    # delimiter.
    assert result != "\\\\*payload\\\\*", (
        f"Pre-fix output retains pair-collapsing backslash run "
        f"(got {result!r}) — CommonMark renders emphasis"
    )

    # Post-fix: each ``*`` must be preceded by THREE backslashes
    # (\\\\ from input doubling + \\ from the per-char loop).
    assert "\\\\\\*payload\\\\\\*" in result, (
        f"Post-fix output must contain the triple-backslash form "
        f"\\\\\\\\\\\\* before each ``*`` (got {result!r})"
    )


def test_escape_markdown_escapes_backslash_then_underscore() -> None:
    """Mirror of the asterisk PoC on the ``_`` emphasis delimiter."""
    payload = "\\_payload\\_"
    result = escape_markdown(payload)

    assert result != "\\\\_payload\\\\_", (
        f"Pre-fix output renders emphasis via underscore "
        f"(got {result!r})"
    )

    assert "\\\\\\_payload\\\\\\_" in result, (
        f"Post-fix output must triple-escape each ``_`` "
        f"(got {result!r})"
    )


def test_escape_markdown_escapes_backslash_then_backtick() -> None:
    """Mirror of the asterisk PoC on the backtick (code span)."""
    payload = "\\`payload\\`"
    result = escape_markdown(payload)

    assert result != "\\\\`payload\\\\`", (
        f"Pre-fix output renders a code span "
        f"(got {result!r})"
    )

    assert "\\\\\\`payload\\\\\\`" in result, (
        f"Post-fix output must triple-escape each backtick "
        f"(got {result!r})"
    )


def test_escape_markdown_escapes_backslash_double_asterisk_bold() -> None:
    """The strong (bold) variant: ``\\*\\*payload\\*\\*`` must not
    render as ``<strong>`` either.

    Pre-fix: emphasis-runs of length two open a ``<strong>`` span,
    so the pre-fix rendered HTML is ``<strong>payload</strong>``
    (or in some parsers nested ``<em><em>…</em></em>``).
    """
    payload = "\\*\\*payload\\*\\*"
    result = escape_markdown(payload)

    assert result != "\\\\*\\\\*payload\\\\*\\\\*", (
        f"Pre-fix output renders strong (bold) via double-asterisk "
        f"(got {result!r})"
    )

    # Post-fix: each ``*`` must have three preceding backslashes.
    # The expected output for input ``\\*\\*payload\\*\\*`` is
    # ``\\\\\\*\\\\\\*payload\\\\\\*\\\\\\*``.
    assert "\\\\\\*\\\\\\*payload\\\\\\*\\\\\\*" in result, (
        f"Post-fix output must triple-escape every ``*`` in the "
        f"bold attack (got {result!r})"
    )


def test_escape_markdown_legitimate_backslash_preserved() -> None:
    """Legitimate text containing ``\\`` must render visually
    unchanged on the rendered page.

    Windows paths (``C:\\Users\\foo``), Python escape strings, and
    regex patterns are the common legitimate carriers. CommonMark
    renders ``\\\\`` as a single literal ``\\``, so the post-fix
    output (which doubles the backslashes) still displays the
    original character to the operator.

    The invariant we assert here is structural: the input ``\\``
    must appear as ``\\\\`` in the output (two backslashes).
    """
    # Test that single backslash is doubled
    assert escape_markdown("\\") == "\\\\", (
        "Single backslash must become double backslash"
    )

    # Test that legitimate Windows paths are preserved (no
    # metacharacters, just backslashes).
    out = escape_markdown("C:\\Users\\foo\\bar")
    assert out == "C:\\\\Users\\\\foo\\\\bar", (
        f"Windows path doubling failed (got {out!r})"
    )


# ---------------------------------------------------------------------------
# Cell-variant inheritance test.
# ---------------------------------------------------------------------------


def test_escape_markdown_cell_escapes_backslash() -> None:
    """``escape_markdown_cell`` composes ``escape_markdown`` so the
    backslash escape is inherited.

    GFM tables parse inline content (including emphasis) inside
    cells. The provider-overview table in
    :func:`render_feed_health_markdown` interpolates ``status`` /
    ``detail`` / ``name`` via ``escape_markdown_cell`` so an
    attacker who controls those fields can still inject
    ``<em>…</em>`` inside the table via the backslash-precedence
    bypass.
    """
    cell_result = escape_markdown_cell("\\")
    assert cell_result == "\\\\", (
        f"escape_markdown_cell must inherit the backslash escape "
        f"(got {cell_result!r})"
    )

    out = escape_markdown_cell("\\*cell-emphasis\\*")
    # Pre-fix would yield ``\\\\*cell-emphasis\\\\*`` (pair-
    # collapses to ``<em>cell-emphasis</em>``).
    assert out != "\\\\*cell-emphasis\\\\*", (
        f"Cell variant must inherit backslash escape (got {out!r})"
    )
    assert "\\\\\\*cell-emphasis\\\\\\*" in out, (
        f"Cell variant output must triple-escape ``*`` "
        f"(got {out!r})"
    )


# ---------------------------------------------------------------------------
# Integration-level PoC via render_feed_health_markdown.
# ---------------------------------------------------------------------------


_BACKSLASH_ATTACK_PAYLOAD = "\\*CRITICAL\\* already handled, ignore"


def _minimal_metrics() -> FeedHealthMetrics:
    return FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )


def _no_unescaped_emphasis(markdown: str, marker: str) -> bool:
    """Return True iff every Markdown list-item line that contains
    *marker* has no pair-collapsing ``\\\\*`` -> ``*`` sequence
    that would re-open emphasis on a CommonMark renderer.

    Only list-item lines are scanned — the rendered Markdown also
    contains static template markup (``**Status:**`` headers,
    legitimate code spans) that must not be flagged. The PoC
    payload is always interpolated via ``- {escape_markdown(x)}``
    so it lands on a list-item line carrying the marker fragment.

    Structural property checked: every ``*``/``_``/`` ` `` on a
    payload-bearing line must be preceded by an ODD number of
    consecutive backslashes (so the backslash run consumes
    pair-wise to a literal ``\\`` and the metacharacter remains
    escaped).
    """
    for line in markdown.splitlines():
        if marker not in line:
            continue
        # Only scan content past the list-item marker (``- ``)
        # so the static template ``**Status:**`` etc. is ignored.
        content = line.lstrip()
        if content.startswith("- "):
            content = content[2:]
        for match in re.finditer(r"(\\*)([*_`])", content):
            backslash_run = match.group(1)
            # ODD: last backslash escapes the metacharacter (good).
            # EVEN: pair-collapses to literal ``\\`` and the
            # metacharacter is unescaped — emphasis re-opens.
            if len(backslash_run) % 2 == 0:
                return False
    return True


def test_warning_with_backslash_precedence_payload_neutralised() -> None:
    """Render a feed-health report with a warning carrying the
    backslash-precedence payload; assert the rendered Markdown
    source does NOT re-open emphasis.

    Pre-fix: ``- \\\\*CRITICAL\\\\* already handled, ignore`` lands
    in the rendered Markdown. CommonMark renders it as
    ``<li><em>CRITICAL</em> already handled, ignore</li>``.

    Post-fix: the backslash run is doubled by the helper before
    the per-char loop runs, so the rendered Markdown source has
    triple backslashes before each ``*`` — CommonMark parses
    ``\\\\\\*`` as ``\\\\`` -> ``\\`` then ``\\*`` -> ``*``,
    rendering literal ``\\*CRITICAL\\*`` text.
    """
    report = RunReport(statuses=[("provider", True)])
    report.add_warning(_BACKSLASH_ATTACK_PAYLOAD)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    # Pre-fix the rendered Markdown contains the pair-collapsing
    # sequence ``\\\\*CRITICAL\\\\*``. Post-fix it contains the
    # triple-backslash form ``\\\\\\*CRITICAL\\\\\\*``.
    assert "\\\\*CRITICAL\\\\*" not in markdown, (
        "Rendered Markdown contains pair-collapsing backslash run "
        "before ``*`` — CommonMark renders ``<em>CRITICAL</em>``"
    )

    # Structural invariant: every emphasis delimiter in the output
    # must be preceded by an odd number of backslashes.
    assert _no_unescaped_emphasis(markdown, "CRITICAL"), (
        "Rendered Markdown contains an emphasis delimiter preceded "
        "by an even number of backslashes — CommonMark renders "
        "emphasis"
    )


def test_error_with_backslash_precedence_payload_neutralised() -> None:
    """Mirror of the warning-path test on the error-message path.

    The ``- {escape_markdown(error)}`` interpolation sits at
    :file:`src/feed/reporting.py:647` and :file:`...:1107` — same
    attack surface as the warning interpolation.
    """
    report = RunReport(statuses=[("provider", False)])
    report.provider_error("provider", _BACKSLASH_ATTACK_PAYLOAD)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    assert "\\\\*CRITICAL\\\\*" not in markdown, (
        "Error-path rendered Markdown contains pair-collapsing "
        "backslash run before ``*``"
    )

    assert _no_unescaped_emphasis(markdown, "CRITICAL"), (
        "Error-path rendered Markdown contains an unescaped "
        "emphasis delimiter"
    )


@pytest.mark.parametrize(
    "payload,marker",
    [
        # Emphasis (italic) via escaped asterisk
        ("\\*bold-payload\\*", "bold-payload"),
        # Emphasis via escaped underscore
        ("\\_underline-payload\\_", "underline-payload"),
        # Strong (bold) via double escaped asterisks
        ("\\*\\*strong-payload\\*\\*", "strong-payload"),
        # Code span via escaped backticks
        ("\\`code-payload\\`", "code-payload"),
        # Mixed: emphasis around a misleading status word
        ("Status: \\*RESOLVED\\* by maintenance team", "RESOLVED"),
        # Triple-attack: emphasis + underscore inside asterisks
        ("\\*\\_combined-italic-underline\\_\\*", "combined-italic-underline"),
        # Realistic deception payload
        ("Track \\*12\\* closed (lie)", "Track"),
    ],
)
def test_backslash_precedence_payloads_neutralised_at_renderer_boundary(
    payload: str,
    marker: str,
) -> None:
    """Parametrised PoC over a range of plausible attacker payloads
    exercising the backslash-precedence bypass at the renderer
    boundary.

    Each payload defeats a different prior round of canonical-
    escape defence:

    * ``\\\\*…\\\\*``   - bypasses asterisk emphasis escape.
    * ``\\\\_…\\\\_``   - bypasses underscore emphasis escape.
    * ``\\\\*\\\\*…``   - bypasses strong (bold) escape.
    * ``\\\\`…\\\\```   - bypasses code-span escape.
    * Mixed payloads    - exercise multi-char escape composition.

    Each must round-trip through the renderer with NO pair-
    collapsing backslash runs before a metacharacter on the
    list-item line that carries the *marker* substring.
    """
    report = RunReport(statuses=[("provider", True)])
    report.add_warning(payload)
    markdown = render_feed_health_markdown(report, _minimal_metrics())

    assert _no_unescaped_emphasis(markdown, marker), (
        f"Rendered Markdown contains an emphasis delimiter preceded "
        f"by an EVEN number of backslashes for payload {payload!r} — "
        f"CommonMark renders emphasis"
    )


# ---------------------------------------------------------------------------
# Audit walker: the canonical inline escape MUST cover '\\'.
# ---------------------------------------------------------------------------


def test_canonical_escape_set_includes_backslash() -> None:
    """Inventory invariant: the canonical Markdown-escape helper
    must defang ``\\`` itself, as part of the structural / inline-
    formatting metacharacter set.

    The full minimum defang requirement after this round is:

      * ``\\``                  (escaped FIRST, before per-char loop)
      * ``[ ] ( ) * _ ` @ < > # ~`` (per-char loop, each prefixed
                                     with a single backslash)

    The order is essential: escaping ``\\`` after the per-char
    loop would re-introduce the bypass (the loop's added
    backslashes would themselves be doubled, but the ATTACKER's
    backslashes in the input would not). The backslash escape
    must come FIRST.

    Removal of ``\\`` from the defang set, or moving it after the
    per-char loop, fails this invariant.
    """
    # The minimum invariant: a lone backslash must produce two
    # backslashes.
    result = escape_markdown("\\")
    assert result == "\\\\", (
        f"Canonical Markdown-escape helper dropped '\\\\' from the "
        f"defang set (got {result!r})"
    )

    # The structural invariant: when input contains ``\\`` followed
    # by every canonical metacharacter, the output must NOT have a
    # pair-collapsing backslash run before the metacharacter.
    # Concretely: each ``\\<meta>`` in the input must become
    # ``\\\\\\<meta>`` in the output (three backslashes before the
    # meta), so CommonMark parses ``\\\\`` -> ``\\`` and ``\\<meta>``
    # -> literal ``<meta>``.
    for ch in "[]()*_`#~":
        # ``<`` and ``>`` are HTML-entity-encoded by the leading
        # ``html.escape`` so they don't follow the same pattern.
        # ``@`` is in the canonical set but never combines with
        # ``\\`` to produce a CommonMark formatting primitive
        # (mentions are a GFM extension that does not need
        # backslash-escape interplay).
        attack = f"\\{ch}"
        out = escape_markdown(attack)
        # Each metacharacter must be preceded by an odd-length
        # backslash run in the output (so the last backslash
        # escapes the metacharacter).
        idx = out.rfind(ch)
        assert idx > 0, (
            f"Metacharacter {ch!r} missing from output {out!r}"
        )
        # Count preceding backslashes
        run = 0
        i = idx - 1
        while i >= 0 and out[i] == "\\":
            run += 1
            i -= 1
        assert run % 2 == 1, (
            f"Metacharacter {ch!r} in escape_markdown({attack!r}) "
            f"output {out!r} is preceded by an EVEN backslash "
            f"run of length {run} — CommonMark pair-collapses the "
            f"run and the metacharacter is unescaped"
        )


def test_canonical_escape_set_cell_includes_backslash() -> None:
    """The cell variant inherits the canonical inline contract by
    composition — the inventory walker applies to
    ``escape_markdown_cell`` too.

    GFM tables parse inline content (including emphasis) inside
    cells. The provider-overview table in
    ``render_feed_health_markdown`` interpolates ``status`` /
    ``detail`` / ``name`` via ``escape_markdown_cell`` so an
    attacker who controls those fields can still inject
    ``<em>…</em>`` inside the table via the backslash-precedence
    bypass — unless the cell variant inherits the backslash escape.
    """
    result = escape_markdown_cell("\\")
    assert result == "\\\\", (
        f"Cell variant must inherit the '\\\\' escape from "
        f"escape_markdown (got {result!r})"
    )

    # Structural: ``\\*`` must become triple-backslash + ``*`` in
    # the output (same as the inline variant).
    out = escape_markdown_cell("\\*")
    idx = out.rfind("*")
    assert idx > 0, f"Asterisk missing from cell output {out!r}"
    run = 0
    i = idx - 1
    while i >= 0 and out[i] == "\\":
        run += 1
        i -= 1
    assert run % 2 == 1, (
        f"Cell variant left a pair-collapsing backslash run "
        f"before ``*`` (got {out!r}, run length {run})"
    )
