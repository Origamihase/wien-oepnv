"""Sentinel: Markdown injection at the ValidationReport.to_markdown() boundary.

The CLI subcommand ``python -m src.cli stations validate --output
docs/stations_validation_report.md`` (driven by the
``update-stations.yml`` cron workflow and the ``manual-full-refresh.yml``
workflow) regenerates the public ``docs/stations_validation_report.md``
artefact from ``data/stations.json``. The same workflow then
auto-commits the file via ``stefanzweifel/git-auto-commit-action`` so the
report is *publicly published* on github.com (and any GitHub Pages site
mirroring ``docs/``).

The data that flows into the report is fed by the cron-driven
``scripts/update_all_stations.py`` orchestrator, which fans out to the
external API surface of VOR / OEBB / Wiener Linien / Google Places /
OSM Overpass. A compromised upstream / DNS-hijack / MITM (or a
sufficiently lax fetch path that does not pin the host) can therefore
inject arbitrary ``name`` / ``bst_code`` / ``vor_id`` / ``alias``
strings into ``stations.json`` — which then flow VERBATIM into
``ValidationReport.to_markdown()``.

The pre-fix ``to_markdown()`` interpolates these strings into eight
distinct Markdown sinks (security warnings, provider issues, cross
station ID issues, geographic duplicates, alias issues, coordinate
anomalies, GTFS mismatches, naming issues) without any escaping. The
following CommonMark primitives all break out:

* **Backtick-in-bullet-text** — a ```xss``` token closes any inline
  code span the operator would otherwise see and lets ``<img
  src=x onerror=alert(1)>`` render as live HTML in the public artefact.
* **Square-bracket Markdown link** — ``[click here](javascript:alert(1))``
  renders as a clickable phishing link in the published report.
* **Asterisk emphasis** — ``*spoofed*`` injects bold/italic emphasis
  that the operator did not author.
* **HTML angle brackets** — although ``_UNSAFE_CHARS_RE`` in
  ``_find_security_issues`` flags angle brackets and the upstream
  ``_collect_blocking_issues`` gate aborts the commit when it does, that
  gate is ONLY active in ``scripts/update_all_stations.py``. The
  standalone CLI invocation (``python -m src.cli stations validate``)
  and any future code path that calls ``to_markdown()`` directly skips
  the gate entirely.

This sister round closes the renderer boundary in
``ValidationReport.to_markdown()`` with the same canonical
``escape_markdown(normalise_markdown_text(...))`` defence pattern that
``scripts/generate_markdown_stats.py`` and ``src/feed/reporting.py``
already apply at their renderer boundaries (see the 2026-05-09
"Markdown Injection Drift Round 2" sentinel journal entry).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.stations_validation import (
    AliasIssue,
    CoordinateIssue,
    CrossStationIDIssue,
    DuplicateGroup,
    GTFSIssue,
    NamingIssue,
    ProviderIssue,
    SecurityIssue,
    ValidationReport,
    validate_stations,
)


def _build_report(**overrides: object) -> ValidationReport:
    """Helper: empty ValidationReport with one issue category populated."""
    defaults: dict[str, object] = {
        "total_stations": 1,
        "duplicates": (),
        "alias_issues": (),
        "coordinate_issues": (),
        "gtfs_issues": (),
        "security_issues": (),
        "cross_station_id_issues": (),
        "provider_issues": (),
        "naming_issues": (),
        "gtfs_stop_count": 0,
    }
    defaults.update(overrides)
    return ValidationReport(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Backtick-in-bullet-text break-out
# ---------------------------------------------------------------------------


def test_security_issue_backtick_in_name_does_not_break_out_to_html() -> None:
    """A ``\\``…``\\``` token in ``name`` must not surface as a live inline
    code span (which would let an embedded ``<img>`` render as live HTML).

    Pre-fix the rendered Markdown contained an unescaped backtick.
    Post-fix the canonical ``escape_markdown`` helper backslash-escapes
    it so the bullet line carries a literal ``\\``` rather than opening
    an inline code span.
    """
    issue = SecurityIssue(
        identifier="bst:1",
        name="Wien Hbf `<img src=x onerror=alert(1)>`",
        reason="injected",
    )
    markdown = _build_report(security_issues=(issue,)).to_markdown()
    # The hostile backtick must be backslash-escaped (canonical
    # ``escape_markdown`` output). A bare backtick in the name field
    # would let GitHub's CommonMark renderer treat the embedded HTML as
    # an inline code span; the escape neutralises that.
    assert "`<img" not in markdown, (
        "Markdown injection: bare backtick survived in security-issue "
        "bullet — letting <img> render as live HTML in the public "
        "docs/stations_validation_report.md artefact"
    )


def test_alias_issue_backtick_in_reason_does_not_break_out_to_html() -> None:
    """The ``reason`` field on AliasIssue is the f-string output of
    ``f"missing required aliases: {missing_text}"`` where ``missing_text``
    is comma-joined from ``name``/``bst_code``/``vor_id``. A backtick in
    any of those source fields propagates into the rendered bullet.
    """
    issue = AliasIssue(
        identifier="bst:42",
        name="Normal",
        reason="missing required aliases: `xss`",
    )
    markdown = _build_report(alias_issues=(issue,)).to_markdown()
    assert "`xss`" not in markdown, (
        "Markdown injection: bare backtick survived in alias-issue "
        "reason field — letting embedded HTML render in the public "
        "docs/stations_validation_report.md artefact"
    )


# ---------------------------------------------------------------------------
# Markdown-link injection (phishing via [text](url))
# ---------------------------------------------------------------------------


def test_naming_issue_markdown_link_in_reason_does_not_render_as_link() -> None:
    """A ``[text](url)`` payload in the reason field would render as a
    clickable Markdown link in the public artefact — a usable phishing
    primitive against any operator skimming the report on github.com.
    """
    issue = NamingIssue(
        identifier="bst:7",
        name="Wien",
        reason="canonical name 'X' is not unique (also used by [click](https://evil.example))",
    )
    markdown = _build_report(naming_issues=(issue,)).to_markdown()
    # Post-fix the square brackets must be backslash-escaped so GitHub's
    # CommonMark renderer treats them as literal text.
    assert "[click](https://evil.example)" not in markdown, (
        "Markdown injection: bare [link](url) survived in naming-issue "
        "reason — letting a phishing link render in the public report"
    )


def test_provider_issue_markdown_link_in_name_does_not_render_as_link() -> None:
    """The ``name`` interpolated into the provider-issues bullet must
    not surface a ``[text](url)`` payload as a clickable link."""
    issue = ProviderIssue(
        identifier="bst:9",
        name="Wien [phish](https://evil.example)",
        reason="Need at least two VOR entries",
    )
    markdown = _build_report(provider_issues=(issue,)).to_markdown()
    assert "[phish](https://evil.example)" not in markdown, (
        "Markdown injection: bare [link](url) survived in provider-issue "
        "name — letting a phishing link render in the public report"
    )


# ---------------------------------------------------------------------------
# Asterisk emphasis (spoofed bold/italic)
# ---------------------------------------------------------------------------


def test_coordinate_issue_asterisk_emphasis_does_not_render_as_bold() -> None:
    """A ``*text*`` payload in any text field would render as italics in
    the public artefact, letting an upstream forge operator-facing
    emphasis it did not author."""
    issue = CoordinateIssue(
        identifier="bst:3",
        name="Wien *spoofed-bold*",
        reason="missing latitude",
    )
    markdown = _build_report(coordinate_issues=(issue,)).to_markdown()
    # Post-fix the asterisks must be backslash-escaped.
    assert "*spoofed-bold*" not in markdown, (
        "Markdown injection: bare asterisks survived in coordinate-issue "
        "name — spoofed bold/italic renders in the public report"
    )


# ---------------------------------------------------------------------------
# Cross-station ID issue: alias / colliding identifier / colliding name
# ---------------------------------------------------------------------------


def test_cross_station_id_issue_backtick_in_alias_does_not_break_out() -> None:
    """The cross-station-ID line interpolates four user-controlled fields:
    ``identifier``, ``name``, ``alias``, ``colliding_identifier``,
    ``colliding_name`` — every one is a Markdown injection sink.
    """
    issue = CrossStationIDIssue(
        identifier="bst:1",
        name="Wien Mitte",
        alias="Mitte `xss`",
        colliding_identifier="bst:2",
        colliding_name="Praterstern",
        colliding_field="bst_code",
    )
    markdown = _build_report(cross_station_id_issues=(issue,)).to_markdown()
    assert "Mitte `xss`" not in markdown, (
        "Markdown injection: bare backtick survived in cross-station-id "
        "alias — letting embedded HTML render in the public report"
    )


# ---------------------------------------------------------------------------
# Geographic duplicates: identifiers list joined with comma
# ---------------------------------------------------------------------------


def test_duplicate_group_backtick_in_identifier_does_not_break_out() -> None:
    """The duplicates section joins ``identifiers`` with ``", "`` and
    interpolates the joined string verbatim. A backtick in any identifier
    breaks out of any surrounding inline code span the operator would
    otherwise read as a single grouped identifier list."""
    group = DuplicateGroup(
        latitude=48.2,
        longitude=16.4,
        identifiers=("bst:1", "code:`xss`"),
        names=("A", "B"),
    )
    markdown = _build_report(duplicates=(group,)).to_markdown()
    assert "code:`xss`" not in markdown, (
        "Markdown injection: bare backtick survived in geographic-"
        "duplicates identifier list — letting embedded HTML render"
    )


# ---------------------------------------------------------------------------
# GTFS mismatch: vor_id field
# ---------------------------------------------------------------------------


def test_gtfs_issue_backtick_in_vor_id_does_not_break_out() -> None:
    """The GTFS-mismatch line surfaces ``vor_id`` verbatim. A backtick in
    that field (legitimate VOR ids are 9 digits — anything else is an
    upstream-injection signal) breaks out of any surrounding inline
    code span."""
    issue = GTFSIssue(
        identifier="bst:5",
        name="Normal",
        vor_id="12345`xss`",
    )
    markdown = _build_report(gtfs_issues=(issue,)).to_markdown()
    assert "12345`xss`" not in markdown, (
        "Markdown injection: bare backtick survived in GTFS-issue "
        "vor_id — letting embedded HTML render in the public report"
    )


# ---------------------------------------------------------------------------
# End-to-end: validate_stations on a stations.json with hostile entries
# ---------------------------------------------------------------------------


def test_end_to_end_hostile_stations_json_does_not_inject_markdown(
    tmp_path: Path,
) -> None:
    """A stations.json crafted by a compromised upstream (or a
    sufficiently lax fetch path) carries a hostile name. After running
    ``validate_stations`` and rendering ``to_markdown()`` the published
    report MUST not contain raw Markdown / HTML break-out primitives.

    The test stations have intentional duplicate coordinates so the
    duplicate-group section fires and embeds the hostile name. (The
    ``_find_security_issues`` gate would also fire on the angle brackets
    in the payload — which is fine; the test is checking the renderer
    boundary, not the upstream gate.)
    """
    stations = [
        {
            "bst_id": 1,
            "bst_code": "A1",
            "name": "Wien `<img src=x onerror=alert(1)>`",
            "aliases": ["Wien `<img src=x onerror=alert(1)>`", "A1"],
            "latitude": 48.2,
            "longitude": 16.4,
            "source": "oebb",
            "in_vienna": True,
            "pendler": False,
        },
        {
            "bst_id": 2,
            "bst_code": "B2",
            "name": "Wien Normal",
            "aliases": ["Wien Normal", "B2"],
            "latitude": 48.2,
            "longitude": 16.4,
            "source": "wl",
            "in_vienna": True,
            "pendler": False,
        },
    ]
    stations_file = tmp_path / "stations.json"
    stations_file.write_text(json.dumps(stations), encoding="utf-8")

    report = validate_stations(stations_file)
    markdown = report.to_markdown()

    # The raw payload must NOT appear unescaped in the rendered Markdown.
    # The backtick is the load-bearing primitive (it opens an inline code
    # span that would render the embedded ``<img onerror>`` as live HTML
    # on github.com / GitHub Pages).
    assert "`<img" not in markdown, (
        "End-to-end Markdown injection: a hostile name in stations.json "
        "surfaced as a live inline code span in the rendered report"
    )
    # Defence-in-depth: HTML angle brackets must also be escaped (the
    # canonical ``escape_markdown`` chains html.escape first).
    assert "<img src=x onerror=alert(1)>" not in markdown, (
        "End-to-end HTML injection: angle brackets in stations.json "
        "surfaced as raw HTML in the rendered report"
    )


# ---------------------------------------------------------------------------
# Inventory invariant: pin every text-interpolation sink in to_markdown()
# ---------------------------------------------------------------------------


def test_to_markdown_sink_inventory_is_pinned() -> None:
    """Inventory invariant: a future refactor that adds a NEW
    text-interpolation sink to ``to_markdown()`` without going through
    the canonical ``escape_markdown`` re-opens this Markdown-injection
    family. The test counts the literal sink count today; bumping it
    requires explicitly re-validating that the new sink is sanitised.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "utils" / "stations_validation.py"
    text = src.read_text(encoding="utf-8")
    # Each of the eight issue categories renders one bullet line with
    # f-string interpolation of issue fields. The exact count is not
    # the security gate (escape_markdown is); the count is the audit
    # trip-wire that catches a NEW sink added without escaping.
    # The lat/longitude geographic-duplicates sink is excluded from the
    # inventory: ``DuplicateGroup.latitude`` / ``longitude`` are typed
    # ``float`` and validated by ``_extract_float`` (rejects NaN / inf /
    # non-numeric), so numeric formatting via ``:.5f`` is safe by
    # construction. Only the ``identifiers`` sublist on that line carries
    # operator-controlled text and is sanitised via ``_safe`` per element.
    sink_markers = (
        "f\"- {sec_issue.identifier}",
        "f\"- {provider_issue.identifier}",
        "f\"- {cross_issue.identifier}",
        "f\"- {alias_issue.identifier}",
        "f\"- {coordinate_issue.identifier}",
        "f\"- {gtfs_issue.identifier}",
        "f\"- {naming_issue.identifier}",
    )
    found = [m for m in sink_markers if m in text]
    # Post-fix, every legacy raw-interpolation marker has been replaced
    # by a sanitised variant — none should remain. A future refactor
    # that re-introduces one of these patterns trips this invariant.
    assert not found, (
        "to_markdown() inventory regression: found unsanitised raw "
        "interpolation sink(s): "
        + ", ".join(found)
        + ". Wrap operator-controlled fields with escape_markdown() + "
        "normalise_markdown_text() before interpolation."
    )
