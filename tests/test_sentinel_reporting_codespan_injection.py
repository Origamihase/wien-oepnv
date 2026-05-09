"""Sentinel PoC: Markdown-injection at the Feed-Health / GitHub-Issue
inline-code-span and fenced-code-block sinks in
``src/feed/reporting.py``.

Threat model
------------

``report.feed_path`` is the POSIX form of ``OUT_PATH`` (env-controlled
via ``src.feed.config.resolve_env_path``); ``error_log_path`` is the
POSIX form of ``LOG_DIR`` / ``errors.log`` (env-controlled via the
same resolver). Both flow into the published artefacts:

  * ``docs/feed_health.md`` (a public artefact rendered by GitHub on
    the public repo browser).
  * The auto-submitted GitHub Issue body (visible to every repo
    watcher; the issue is opened on every failed feed run).

Pre-fix ``render_feed_health_markdown`` (line 531) and
``_GithubIssueReporter._build_body`` (lines 1012, 1063) interpolated
those env-controlled paths *verbatim* inside ``\\`…\\``` inline code
spans. A poisoned env override (leaked CI env, compromised secret
store, intentional misconfig) carrying a literal backtick in
``OUT_PATH`` / ``LOG_DIR`` closes the inline code span and lets the
remainder render as arbitrary Markdown / HTML.

Pre-fix ``_build_body`` (lines 1057-1059) wrapped
``RunReport.diagnostics_message()`` inside a ``\\`\\`\\`text … \\`\\`\\```
fenced code block. The diagnostics text contains ``f"Feed={feed_path}"``
without sanitation; a payload carrying ``\\n\\`\\`\\`\\n`` in
``OUT_PATH`` closes the fence mid-block and lets the remainder render
as Markdown.

The fix is to route ``feed_path`` and ``error_log_path`` (every
inline-code-span sink) through :func:`src.utils.text.safe_markdown_codespan`,
and to route the diagnostics text (the fenced-code-block sink) through
the same helper with a higher length cap. The helper:

  1. Strips C0/C1 controls + the canonical Trojan-Source / line-
     terminator union (BiDi marks, ZWSP family, line/paragraph
     separators, BOM).
  2. Collapses whitespace to a single space (eliminates embedded
     newlines that could break a fenced code block).
  3. Replaces literal backticks with apostrophes (the project-wide
     convention pinned by ``_sanitize_code_span``).
  4. Caps length.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import responses

from src.feed.reporting import (
    FeedHealthMetrics,
    RunReport,
    render_feed_health_markdown,
)


# ---- Shared fixtures -------------------------------------------------------


def _empty_metrics() -> FeedHealthMetrics:
    return FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )


def _make_report(feed_path: str | None = None) -> RunReport:
    report = RunReport(statuses=[("wl", True)])
    report.provider_success("wl", items=0)
    if feed_path is None:
        report.finish(build_successful=True)
    else:
        report.finish(build_successful=True, feed_path=Path(feed_path))
    return report


# ---- render_feed_health_markdown — feed_path inline-code-span --------------


def test_feed_health_markdown_feed_path_backtick_breaks_inline_code_span() -> None:
    """A backtick in ``feed_path`` MUST NOT break out of the inline code
    span at line 531. Pre-fix the literal backtick closed the span and
    let attacker-controlled Markdown render verbatim.
    """
    payload = "docs/feed`<script>alert(1)</script>`.xml"
    report = _make_report(feed_path=payload)
    markdown = render_feed_health_markdown(report, _empty_metrics())

    # Locate the bullet line that interpolates feed_path.
    bullet_line = next(
        line for line in markdown.splitlines() if "RSS-Datei" in line
    )
    # Post-fix: the backtick is replaced with an apostrophe so the
    # only ``\\``` characters left on the line are the opening and
    # closing fence of the inline code span (exactly two).
    assert bullet_line.count("`") == 2, (
        f"Inline code span MUST contain exactly two backticks "
        f"(opening + closing). Pre-fix the embedded backtick made it "
        f"four. Got: {bullet_line!r}"
    )
    # The safe form replaces backticks with apostrophes so the whole
    # literal stays inside one inline code span.
    assert "feed'<script>alert(1)</script>'.xml" in bullet_line


def test_feed_health_markdown_feed_path_newline_breaks_layout() -> None:
    """A newline in ``feed_path`` MUST NOT split the bullet-list item
    or smuggle a fake Markdown header.
    """
    payload = "docs/foo\n## INJECTED HEADER\n.xml"
    report = _make_report(feed_path=payload)
    markdown = render_feed_health_markdown(report, _empty_metrics())

    # Post-fix: the newline is whitespace-collapsed so the ``##`` can
    # never start a fresh line as a Markdown ATX header.
    assert re.search(r"^## INJECTED HEADER", markdown, re.MULTILINE) is None, (
        "Newline + ``##`` in feed_path landed an attacker-controlled H2 "
        "header in the rendered Markdown."
    )
    # The literal payload-with-newline MUST NOT survive; the value
    # collapses to a single line.
    assert payload not in markdown
    bullet_line = next(
        line for line in markdown.splitlines() if "RSS-Datei" in line
    )
    assert "\n" not in bullet_line


def test_feed_health_markdown_feed_path_bidi_marks_stripped() -> None:
    """Trojan-Source BiDi marks in ``feed_path`` MUST be stripped so
    the rendered Markdown cannot invert displayed text in feed-health
    viewers.
    """
    # U+202E RIGHT-TO-LEFT OVERRIDE — the canonical CVE-2021-42574
    # primitive. U+200B ZWSP. U+FEFF BOM.
    payload = "docs/feed‮​﻿.xml"
    report = _make_report(feed_path=payload)
    markdown = render_feed_health_markdown(report, _empty_metrics())

    assert "‮" not in markdown
    assert "​" not in markdown
    assert "﻿" not in markdown


# ---- _GithubIssueReporter._build_body — feed_path inline-code-span ---------


def _post_issue_capture_body(
    monkeypatch: pytest.MonkeyPatch,
    report: RunReport,
) -> str:
    """Stand up the auto-issue submitter and return the captured body.

    Mirrors the existing pattern from ``test_reporting_github.py`` —
    monkeypatches the SSRF guard, registers a responses URL, and
    triggers ``report.log_results()`` (which calls ``_submit_github_issue``).
    """
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")
    monkeypatch.setattr("src.utils.http.validate_http_url", lambda url, **kw: url)

    import sys
    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(sys.modules[module_name], "verify_response_ip", lambda _: None)

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        json={"html_url": "https://github.com/demo/repo/issues/1"},
        status=201,
    )
    report.log_results()
    assert len(responses.calls) == 1, "expected exactly one POST to GitHub"
    payload = json.loads(responses.calls[0].request.body)
    return str(payload["body"])


@responses.activate
def test_github_issue_body_feed_path_backtick_breaks_inline_code_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backtick in ``feed_path`` MUST NOT break out of the inline
    code span at ``_build_body`` line 1012.
    """
    payload = "docs/feed`<img src=x onerror=alert(1)>`.xml"
    report = _make_report(feed_path=payload)
    # Force log_results to submit by recording an error.
    report.add_error_message("test error to trigger issue submission")

    body = _post_issue_capture_body(monkeypatch, report)

    feed_line = next(
        line for line in body.splitlines() if "Feed-Datei" in line
    )
    # Post-fix the inline code span has exactly two backticks (open + close).
    assert feed_line.count("`") == 2, (
        f"GitHub Issue body Feed-Datei line MUST keep its inline code "
        f"span intact (exactly two backticks). Got: {feed_line!r}"
    )
    assert "feed'<img src=x onerror=alert(1)>'.xml" in feed_line


@responses.activate
def test_github_issue_body_error_log_path_backtick_breaks_inline_code_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backtick in ``error_log_path`` (env-controlled via ``LOG_DIR``)
    MUST NOT break out of the inline code span at ``_build_body``
    line 1063.
    """
    # We cannot simply set LOG_DIR via env here because the path is
    # imported at module load time. Instead we monkeypatch the imported
    # reference inside reporting so the test exercises the real sink.
    poisoned_path = Path("log`<script>alert('xss')</script>`/errors.log")
    import src.feed.reporting as reporting_module
    monkeypatch.setattr(reporting_module, "error_log_path", poisoned_path)

    report = _make_report()
    report.add_error_message("test error to trigger issue submission")

    body = _post_issue_capture_body(monkeypatch, report)

    log_line = next(
        line for line in body.splitlines() if "Logdatei" in line
    )
    # Post-fix the inline code span has exactly two backticks
    # (the opening + closing fence) — no embedded backtick from the
    # poisoned path closes the span early.
    assert log_line.count("`") == 2, (
        f"GitHub Issue body Logdatei line MUST keep its inline code "
        f"span intact. Got: {log_line!r}"
    )
    assert "<script>alert('xss')</script>" in log_line, (
        "Sanity check: the safe form preserves the literal text inside "
        "the inline code span (rendered as code, not as HTML)."
    )
    # The two backticks belong to the inline code span; no markdown
    # link / bold / header escapes the span.
    assert "**" not in log_line.replace("**Logdatei**", "")


@responses.activate
def test_github_issue_body_feed_path_fence_break_via_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newline + triple-backtick in ``feed_path`` MUST NOT close the
    diagnostics fenced code block at ``_build_body`` lines 1057-1059.
    """
    # Newline + ``` on its own line → CommonMark closes the open
    # fence. Anything after renders as Markdown.
    payload = "docs/feed.xml\n```\n# INJECTED H1\n```"
    report = _make_report(feed_path=payload)
    report.add_error_message("test error to trigger issue submission")

    body = _post_issue_capture_body(monkeypatch, report)

    # Post-fix: the diagnostics block is wrapped in ``\\``\\``\\``text`` /
    # ``\\``\\``\\``` and contains a single line of (collapsed-whitespace
    # + apostrophe-replaced) text. No ``\\``\\``\\``` sits on its own line
    # *inside* the fence, so the fence stays open until the explicit
    # closing fence emitted by ``_build_body``.
    fence_count = body.count("```")
    assert fence_count == 2, (
        f"Diagnostics fenced code block MUST emit exactly two ``\\``\\``\\``` "
        f"fences (open + close). Pre-fix the newline+triple-backtick in "
        f"feed_path injected extra fences. Got fence_count={fence_count}."
    )
    # No fresh-line H1 / H2 may appear; payload's ``# INJECTED H1`` MUST
    # be on the SAME line as the surrounding diagnostics text.
    for line in body.splitlines():
        # A standalone ATX-H1 header from the payload is the failure
        # signature.
        assert not line.lstrip().startswith("# INJECTED H1"), (
            f"Standalone H1 escaped the diagnostics fence: {line!r}"
        )


# ---- Inventory invariant ---------------------------------------------------


def test_inline_code_span_sinks_inventory_pinned() -> None:
    """Inventory invariant: the three operator-controlled inline-code-
    span sinks in ``src/feed/reporting.py`` MUST route through a
    backtick-stripping sanitiser.

    A future refactor that introduces a fourth ``\\`…\\``` inline code
    span sourced from an env-controlled string (``OUT_PATH``,
    ``LOG_DIR``, ``FEED_HEALTH_PATH``, …) without going through
    :func:`src.utils.text.safe_markdown_codespan` re-opens the
    Markdown-injection vector. This test fails on any drift away from
    the canonical helper for the audited sinks.
    """
    reporting_src = Path("src/feed/reporting.py").read_text(encoding="utf-8")

    # The three audited sinks must reference safe_markdown_codespan
    # in their immediate textual neighbourhood.
    assert "safe_markdown_codespan(report.feed_path" in reporting_src or \
        "safe_markdown_codespan(\n            report.feed_path" in reporting_src or \
        "safe_markdown_codespan(_path)" in reporting_src or \
        "safe_path = safe_markdown_codespan" in reporting_src, (
        "feed_path inline-code-span sinks must route through "
        "safe_markdown_codespan."
    )
    assert "safe_markdown_codespan(str(error_log_path)" in reporting_src or \
        "safe_markdown_codespan(error_log" in reporting_src or \
        "safe_log_path = safe_markdown_codespan" in reporting_src, (
        "error_log_path inline-code-span sink must route through "
        "safe_markdown_codespan."
    )
