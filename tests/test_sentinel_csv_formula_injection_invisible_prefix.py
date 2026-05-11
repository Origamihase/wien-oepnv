"""Sentinel PoC: CSV formula-injection bypass via leading invisible /
BiDi / line-terminator characters at the
``src.utils.stats._sanitize_csv_text_field`` boundary.

The 2026-05-09 ``CSV Formula Injection (CWE-1236) at the Stats-Writer
Boundary`` round (``.jules/sentinel.md``) closed the canonical
formula-prefix surface (``=`` / ``+`` / ``-`` / ``@`` / ``\\t`` / ``\\r``)
by prepending a single quote (``'``) to any cell beginning with one of
:data:`_CSV_FORMULA_PREFIXES`. The CLEANER step in front of the
prefix check stripped only ASCII C0 controls + DEL
(``[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F]``) — narrower than the canonical
``src.utils.logging._INVISIBLE_DANGEROUS_RE`` set the BiDi-Mark Drift
family (Rounds 2-4) consolidated as the project-wide invisible /
Trojan-Source / line-terminator floor.

The resulting drift opened a **formula-injection bypass**:

  1. An attacker plants a description containing
     ``"| Haltestelle: \\u200b=cmd|'/c calc'!A1"`` upstream (compromised
     WL / VOR / ÖBB cache, MITM, DNS-hijack of a non-pinned upstream).
  2. ``src.utils.stats.extract_location_name`` matches the
     ``Haltestelle:`` regex, splits on ``,``, strips ASCII whitespace,
     and falls through to ``_normalise_location`` for unknown stops
     (the WL stop list is curated upstream so verbatim acceptance is
     the canonical behaviour). The leading ZWSP survives every step:
     ``str.strip()`` does not consider U+200B whitespace
     (``"\\u200b".isspace() is False``), and ``_normalise_location``
     splits/joins on ``str.split()`` which uses the same whitespace
     definition.
  3. ``src.build_feed._update_item_state`` calls
     ``append_disruption_row(location_name="\\u200b=cmd|…")``.
  4. The pre-fix sanitiser strips control chars (no match — ZWSP is
     U+200B, outside ``[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F]``), then
     ``.strip()`` (no match — ZWSP is not whitespace), then checks
     ``cleaned.startswith(_CSV_FORMULA_PREFIXES)`` — which returns
     ``False`` because the leading character is ZWSP, not ``=``. The
     defang step is skipped.
  5. ``data/stats/stoerungen_<YYYY>.csv`` lands the row
     ``…,\\u200b=cmd|'/c calc'!A1`` verbatim. The cycle pipeline commits
     the file via ``update-cycle.yml`` (IFTTT-triggered); the CSV is
     then a public artefact in the repository.
  6. An operator opens ``data/stats/stoerungen_2026.csv`` in Excel /
     LibreOffice Calc / Google Sheets to inspect indicators of
     compromise. Several spreadsheet engines collapse the leading
     invisible character before formula evaluation and execute the
     residual ``=cmd|'/c calc'!A1`` payload — full CWE-1236 RCE in
     the operator's spreadsheet, originally landed via a
     compromised-upstream chain of trust.

The same shape generalises across the canonical invisible-character
set: U+202E (RLO) lands a **Trojan-Source CSV** (the cell content is
visually reversed in the spreadsheet rendering, hiding the formula
from a reviewing analyst); U+FEFF (BOM) lands a byte-equality
disagreement (``len(s) == 4`` for ``"\\ufeff=cmd"`` but visually
identical to ``"=cmd"``); U+0085 (NEL) is a record separator in some
CSV / SIEM splitters that splits a single cell into multiple rows
downstream.

This PoC pins the sibling-regex sync rule for the CSV writer
boundary by mirroring the ``test_sentinel_http_url_chars_bidi_gap.py``
shape established by BiDi-Mark Drift Round 4:

  1. **Per-code-point regex match** — ``_CSV_CONTROL_CHARS_RE`` must
     match each of the 16+ canonical invisible / BiDi code points.
  2. **Per-code-point write-path PoC** — calling the public writer
     (``append_disruption_row`` / ``append_stammstrecke_row``) with a
     formula payload prefixed by an invisible character must produce
     a cell whose content does NOT begin with ``=`` / ``+`` / ``-`` /
     ``@`` / ``\\t`` / ``\\r``.
  3. **Inventory invariant** — every code point matched by the
     canonical ``src.utils.logging._INVISIBLE_DANGEROUS_RE`` must also
     match ``_CSV_CONTROL_CHARS_RE``. A future widening of the
     canonical regex (e.g. a Unicode 16 BiDi format control) fails
     this test until the CSV writer's regex is widened too.
  4. **Coverage-preserving regression** — every character the pre-fix
     ``_CSV_CONTROL_CHARS_RE`` matched must still match post-fix.
  5. **Whitespace-passthrough regression** — TAB / LF / CR / SPACE
     must still NOT be stripped from the body (they are required for
     legitimate cell content; ``csv`` quotes them automatically and
     leading TAB / CR are still defanged by the formula-prefix branch).
  6. **Safe-text regression** — legitimate German station / provider
     names (``"Wien Floridsdorf"``, ``"ÖBB"``) must round-trip
     byte-exactly post-fix; the widening must not over-reach.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import csv
import io

import pytest

from src.utils import logging as canonical_logging
from src.utils import stats as stats_utils


VIENNA_TZ = ZoneInfo("Europe/Vienna")


# Canonical invisible / BiDi / line-terminator code points the project's
# log sanitiser strips (see ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``)
# plus the C1 control U+0085 (NEL) the markdown sanitiser also covers
# (``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``). Each is a
# documented invisible / Trojan-Source / record-terminator primitive
# whose presence at the start of a CSV cell would let a residual formula
# prefix (``=`` / ``+`` / ``-`` / ``@`` / ``\t`` / ``\r``) survive the
# defang gate and reach a spreadsheet evaluator unprotected.
_INVISIBLE_PREFIX_CODE_POINTS: tuple[tuple[str, str], ...] = (
    ("؜", "ARABIC LETTER MARK (ALM)"),
    ("​", "ZERO WIDTH SPACE (ZWSP)"),
    ("‌", "ZERO WIDTH NON-JOINER (ZWNJ)"),
    ("‍", "ZERO WIDTH JOINER (ZWJ)"),
    ("‎", "LEFT-TO-RIGHT MARK (LRM)"),
    ("‏", "RIGHT-TO-LEFT MARK (RLM)"),
    (" ", "LINE SEPARATOR"),
    (" ", "PARAGRAPH SEPARATOR"),
    ("‪", "LEFT-TO-RIGHT EMBEDDING (LRE)"),
    ("‫", "RIGHT-TO-LEFT EMBEDDING (RLE)"),
    ("‬", "POP DIRECTIONAL FORMATTING (PDF)"),
    ("‭", "LEFT-TO-RIGHT OVERRIDE (LRO)"),
    ("‮", "RIGHT-TO-LEFT OVERRIDE (RLO)"),
    ("⁦", "LEFT-TO-RIGHT ISOLATE (LRI)"),
    ("⁧", "RIGHT-TO-LEFT ISOLATE (RLI)"),
    ("⁨", "FIRST STRONG ISOLATE (FSI)"),
    ("⁩", "POP DIRECTIONAL ISOLATE (PDI)"),
    ("﻿", "BYTE ORDER MARK (BOM)"),
    ("", "C1 NEXT LINE (NEL)"),
)

# Canonical formula prefixes the writer must defang. Mirrors
# :data:`src.utils.stats._CSV_FORMULA_PREFIXES`. Tests pin the exclusion
# at the cell boundary — the cell content must NOT start with any of
# these (an apostrophe is the OWASP-recommended defang and is acceptable).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _read_first_data_row(path: Path) -> list[str]:
    """Read *path* as a UTF-8 CSV and return the first data row (post-header).

    The stats writers emit a header on first creation and append data
    rows below; the test caller invokes the writer exactly once so the
    first data row is row index 1.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    assert rows, f"CSV file {path} is empty"
    assert len(rows) >= 2, f"CSV file {path} has no data row beyond the header"
    return rows[1]


# ============================================================================
# (1) Per-code-point regex match
# ============================================================================


@pytest.mark.parametrize(
    "code_point,label",
    _INVISIBLE_PREFIX_CODE_POINTS,
    ids=[label for _, label in _INVISIBLE_PREFIX_CODE_POINTS],
)
def test_csv_control_chars_regex_matches_invisible_prefix(
    code_point: str, label: str
) -> None:
    """Pre-fix: ``_CSV_CONTROL_CHARS_RE`` only covered ASCII C0 + DEL,
    so a CSV cell prefixed with ``code_point`` slipped past the
    formula-prefix defang gate. Post-fix: the regex matches the code
    point and the sanitiser strips it before the prefix check.
    """
    assert stats_utils._CSV_CONTROL_CHARS_RE.search(code_point) is not None, (
        f"_CSV_CONTROL_CHARS_RE must match {label} ({hex(ord(code_point))}); "
        "see .jules/sentinel.md (CSV Formula Injection — Invisible Prefix "
        "Bypass) for the full list of code points the writer must strip."
    )


# ============================================================================
# (2) Per-code-point write-path PoC — disruption ledger
# ============================================================================


@pytest.mark.parametrize(
    "code_point,label",
    _INVISIBLE_PREFIX_CODE_POINTS,
    ids=[label for _, label in _INVISIBLE_PREFIX_CODE_POINTS],
)
def test_append_disruption_row_neutralises_invisible_prefix_provider(
    tmp_path: Path, code_point: str, label: str
) -> None:
    """End-to-end PoC: ``append_disruption_row(provider=<invisible>=cmd…)``
    must NOT land a formula-prefixed cell in ``stoerungen_YYYY.csv``.

    Pre-fix path: ``provider="\\u200b=cmd|'/c calc'!A1"`` survives the
    sanitiser unchanged because the C0/DEL regex did not match the
    ZWSP, ``str.strip()`` does not consider ZWSP whitespace, and the
    formula-prefix check inspects the still-leading ZWSP rather than
    the residual ``=``. The cell lands as ``\\u200b=cmd|'/c calc'!A1``
    in the public ``data/stats/stoerungen_YYYY.csv`` artefact and an
    operator opening it in Excel triggers CWE-1236.
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    payload = f"{code_point}=cmd|'/c calc'!A1"
    stats_utils.append_disruption_row(
        timestamp=when,
        provider=payload,
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    written_provider = _read_first_data_row(tmp_path / "stoerungen_2026.csv")[3]
    assert not written_provider.startswith(_FORMULA_PREFIXES), (
        f"Formula prefix leaked into provider cell after stripping "
        f"{label} ({hex(ord(code_point))}): {written_provider!r}. The "
        "writer must strip the invisible prefix BEFORE the formula-prefix "
        "defang gate so the gate inspects the visible content."
    )
    # Also pin: the invisible prefix itself must be gone — operators
    # scanning the CSV must see what the spreadsheet would render, not
    # a hidden character that fools the eye but trips the formula
    # evaluator.
    assert code_point not in written_provider, (
        f"Invisible code point {label} ({hex(ord(code_point))}) survived "
        f"into provider cell: {written_provider!r}. The sanitiser must "
        "strip every invisible / BiDi / line-terminator character so the "
        "cell content matches what a spreadsheet renders."
    )


@pytest.mark.parametrize(
    "code_point,label",
    _INVISIBLE_PREFIX_CODE_POINTS,
    ids=[label for _, label in _INVISIBLE_PREFIX_CODE_POINTS],
)
def test_append_disruption_row_neutralises_invisible_prefix_location(
    tmp_path: Path, code_point: str, label: str
) -> None:
    """End-to-end PoC: ``append_disruption_row(location_name=<invisible>=cmd…)``
    must NOT land a formula-prefixed cell.

    The ``location_name`` field is the highest-blast-radius surface
    because :func:`extract_location_name` accepts WL ``Haltestelle:``
    values verbatim when they are not in the heavy-rail directory
    (curated-upstream policy — see the ``extract_location_name``
    docstring). A compromised WL upstream that injects an invisible-
    prefixed formula payload lands directly in this cell pre-fix.
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    payload = f"{code_point}=cmd|'/c calc'!A1"
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name=payload,
        stats_dir=tmp_path,
    )
    written_location = _read_first_data_row(tmp_path / "stoerungen_2026.csv")[4]
    assert not written_location.startswith(_FORMULA_PREFIXES), (
        f"Formula prefix leaked into location cell after stripping "
        f"{label} ({hex(ord(code_point))}): {written_location!r}."
    )
    assert code_point not in written_location, (
        f"Invisible code point {label} ({hex(ord(code_point))}) survived "
        f"into location cell: {written_location!r}."
    )


# ============================================================================
# (2b) Per-code-point write-path PoC — Stammstrecke ledger
# ============================================================================


@pytest.mark.parametrize(
    "code_point,label",
    _INVISIBLE_PREFIX_CODE_POINTS,
    ids=[label for _, label in _INVISIBLE_PREFIX_CODE_POINTS],
)
def test_append_stammstrecke_row_neutralises_invisible_prefix_direction(
    tmp_path: Path, code_point: str, label: str
) -> None:
    """End-to-end PoC: ``append_stammstrecke_row(direction=<invisible>=cmd…)``
    must NOT land a formula-prefixed cell.

    The ``direction`` field flows from the canonical station directory
    (``data/stations.json``) via ``display_name`` in
    ``scripts/update_stammstrecke_status.py``. A poisoned directory
    entry whose display name carries an invisible-prefixed formula
    payload lands here pre-fix.
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    payload = f"{code_point}=cmd|'/c calc'!A1"
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction=payload,
        delay_minutes=5.5,
        stats_dir=tmp_path,
    )
    written_direction = _read_first_data_row(tmp_path / "stammstrecke_2026.csv")[3]
    assert not written_direction.startswith(_FORMULA_PREFIXES), (
        f"Formula prefix leaked into direction cell after stripping "
        f"{label} ({hex(ord(code_point))}): {written_direction!r}."
    )
    assert code_point not in written_direction, (
        f"Invisible code point {label} ({hex(ord(code_point))}) survived "
        f"into direction cell: {written_direction!r}."
    )


# ============================================================================
# (3) Inventory invariant — companion-regex sync with the canonical sanitiser
# ============================================================================


def test_csv_control_chars_regex_covers_canonical_invisible_dangerous_set() -> None:
    """Inventory invariant: every character that
    :data:`src.utils.logging._INVISIBLE_DANGEROUS_RE` matches MUST also
    match :data:`src.utils.stats._CSV_CONTROL_CHARS_RE`.

    A regression here means the two regexes have drifted apart — either
    the CSV-writer regex was narrowed or the canonical log sanitiser
    was widened without a matching update at the CSV write boundary.
    Both shapes leak a planted CSV cell carrying the newly-listed code
    point past the formula-prefix defang gate and into the public
    ``data/stats/*.csv`` artefact.

    Mirrors the inventory tests
    ``test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set``
    (``stations_validation``, BiDi-Mark Drift Round 3) and
    ``test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set``
    (``http``, BiDi-Mark Drift Round 4). Together they programmatically
    pin the companion-regex sync rule for every defence boundary that
    sees adversarial text — any future widening of
    ``_INVISIBLE_DANGEROUS_RE`` (e.g. a Unicode 16 BiDi format control)
    fails ALL three inventory tests until ALL three boundaries are
    widened too.
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    csv_regex = stats_utils._CSV_CONTROL_CHARS_RE

    canonical_code_points: list[int] = []
    for cp in range(0x110000):  # full Unicode BMP + supplementary planes
        if canonical.fullmatch(chr(cp)):
            canonical_code_points.append(cp)

    assert canonical_code_points, (
        "Canonical _INVISIBLE_DANGEROUS_RE matches nothing — likely a "
        "regression in the canonical regex itself"
    )

    missing: list[int] = [
        cp for cp in canonical_code_points if not csv_regex.fullmatch(chr(cp))
    ]

    assert not missing, (
        "_CSV_CONTROL_CHARS_RE is narrower than _INVISIBLE_DANGEROUS_RE; "
        f"missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" …" if len(missing) > 20 else "")
        + "\nThe two regexes must stay in sync: any code point covered "
        "by the canonical log sanitiser must also be flagged by the "
        "CSV writer. See .jules/sentinel.md (CSV Formula Injection — "
        "Invisible Prefix Bypass) for the closing rule."
    )


# ============================================================================
# (4) Coverage-preserving regression — pre-fix matches still match post-fix
# ============================================================================


def test_csv_control_chars_regex_preserves_existing_coverage() -> None:
    """Regression: every character ``_CSV_CONTROL_CHARS_RE`` matched
    pre-fix must still match post-fix. The widening MUST be additive.

    Covers ASCII C0 controls (``\\x00-\\x08``, ``\\x0B-\\x0C``,
    ``\\x0E-\\x1F``) and DEL (``\\x7F``). TAB (``\\x09``), LF (``\\x0A``),
    and CR (``\\x0D``) are intentionally NOT matched (the body cannot
    legitimately carry record separators but ``csv`` already
    QUOTE_MINIMAL-wraps embedded newlines, and embedded TAB is benign
    for the default ``,`` delimiter — see the regex docstring).
    """
    pre_fix_must_match = (
        "\x00", "\x01", "\x02", "\x03", "\x04", "\x05", "\x06", "\x07",
        "\x08",  # 0x09 (TAB) excluded
        "\x0b", "\x0c",  # 0x0a (LF) and 0x0d (CR) excluded
        "\x0e", "\x0f", "\x10", "\x11", "\x12", "\x13", "\x14", "\x15",
        "\x16", "\x17", "\x18", "\x19", "\x1a", "\x1b", "\x1c", "\x1d",
        "\x1e", "\x1f", "\x7f",
    )
    for cp in pre_fix_must_match:
        assert stats_utils._CSV_CONTROL_CHARS_RE.search(cp) is not None, (
            f"Existing coverage must be preserved: {hex(ord(cp))} "
            "must still match _CSV_CONTROL_CHARS_RE after the widening."
        )


# ============================================================================
# (5) Whitespace-passthrough regression — TAB / LF / CR / SPACE not stripped
# ============================================================================


def test_csv_control_chars_regex_does_not_match_readable_whitespace() -> None:
    """Regression: TAB / LF / CR / SPACE must NOT be matched by the
    widened regex.

    ``csv`` already QUOTE_MINIMAL-wraps fields containing newlines, so
    embedded LF / CR is harmless mid-cell. Embedded TAB is benign for
    the default ``,`` delimiter. *Leading* TAB / CR are still defanged
    by the formula-prefix branch in ``_sanitize_csv_text_field`` — they
    are listed in :data:`_CSV_FORMULA_PREFIXES`. SPACE is preserved
    because it is the universal field separator inside cell content
    (e.g. ``"Wien Floridsdorf"``).
    """
    must_not_match = (" ", "\t", "\n", "\r")
    for cp in must_not_match:
        assert stats_utils._CSV_CONTROL_CHARS_RE.search(cp) is None, (
            f"Widened _CSV_CONTROL_CHARS_RE must NOT match readable "
            f"whitespace {cp!r} ({hex(ord(cp))}); body whitespace is "
            "required for legitimate cell content."
        )


# ============================================================================
# (6) Safe-text regression — legitimate content round-trips byte-exactly
# ============================================================================


def test_append_disruption_row_safe_text_roundtrips_after_widening(
    tmp_path: Path,
) -> None:
    """Regression: legitimate German provider / location strings must
    round-trip byte-exactly post-fix. The widening must not over-reach
    and strip characters that legitimately appear in station names.
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    row = _read_first_data_row(tmp_path / "stoerungen_2026.csv")
    assert row[3] == "ÖBB"
    assert row[4] == "Wien Floridsdorf"


def test_append_stammstrecke_row_safe_text_roundtrips_after_widening(
    tmp_path: Path,
) -> None:
    """Regression: legitimate Stammstrecke direction labels round-trip
    byte-exactly post-fix.
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Floridsdorf",
        delay_minutes=5.5,
        stats_dir=tmp_path,
    )
    row = _read_first_data_row(tmp_path / "stammstrecke_2026.csv")
    assert row[3] == "Floridsdorf"
    assert row[4] == "5.50"


# ============================================================================
# (7) End-to-end CSV inspection — invisible prefix removed from raw bytes
# ============================================================================


def test_csv_file_bytes_carry_no_invisible_prefix(tmp_path: Path) -> None:
    """The on-disk CSV file must NOT contain any invisible / BiDi /
    line-terminator code points carried over from the upstream payload.

    Threat model: the CSV file is committed to the repository via the
    IFTTT-triggered ``update-cycle.yml`` workflow and is therefore a
    public artefact.
    A row that carries an invisible character may render identically
    to a benign row in a quick visual review (or in a SIEM dashboard's
    line-summary column) but exhibit different behaviour downstream
    (formula evaluation, BiDi inversion, byte-equality disagreement).
    The CSV write boundary is the canonical place to enforce that
    on-disk bytes match what an analyst sees on screen — defense in
    depth for every downstream consumer.
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    # Plant one of each invisible primitive in both writer fields.
    payload = "​‮﻿؜=cmd"
    stats_utils.append_disruption_row(
        timestamp=when,
        provider=payload,
        location_name=payload,
        stats_dir=tmp_path,
    )
    raw = (tmp_path / "stoerungen_2026.csv").read_bytes()
    text = raw.decode("utf-8")
    # None of the canonical invisible code points must survive the
    # write boundary.
    for cp_int, name in (
        (0x200B, "ZWSP"),
        (0x202E, "RLO"),
        (0xFEFF, "BOM"),
        (0x061C, "ALM"),
    ):
        assert chr(cp_int) not in text, (
            f"{name} (U+{cp_int:04X}) survived into the on-disk CSV "
            f"bytes: {text!r}. The CSV write boundary must strip every "
            "invisible / BiDi / line-terminator code point so the file "
            "renders the same way regardless of viewer."
        )


# ============================================================================
# (8) Upstream-chain PoC — full bypass path is closed at the writer
# ============================================================================


def test_extract_location_name_then_writer_closes_upstream_bypass(
    tmp_path: Path,
) -> None:
    """End-to-end attack-chain PoC: a planted upstream description with
    an invisible-prefixed formula payload travels through
    :func:`extract_location_name` and then through
    :func:`append_disruption_row` — the resulting CSV cell must NOT
    begin with a formula prefix.

    Walks the same code path the production cron pipeline takes:

      compromised upstream → ``Haltestelle:`` regex extraction →
      ``_normalise_location`` (no invisible-char strip) →
      ``append_disruption_row`` → ``_sanitize_csv_text_field``.

    Pre-fix the chain landed ``\\u200b=cmd…`` in the CSV verbatim; the
    fix at ``_sanitize_csv_text_field`` is the chain's terminal defence
    so this test pins that the fix closes the **full** chain, not just
    the unit-test boundary.
    """
    payload = "​=cmd|'/c calc'!A1"
    poisoned_item = {
        "title": "U6: Verspätung",
        "description": f"… | Haltestelle: {payload} ",
    }
    location_name = stats_utils.extract_location_name(poisoned_item)
    # Pre-fix the extracted value carries the invisible prefix verbatim
    # because ``_normalise_location`` does not strip non-whitespace
    # invisible characters; the sanity assertion documents that the
    # bypass surface upstream of the writer is real and the writer is
    # the chain's only canonical defence.
    assert location_name.startswith("​") or "=cmd" in location_name, (
        "Sanity: the upstream chain extracted the planted payload — "
        "the writer is the canonical defence boundary."
    )

    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name=location_name,
        stats_dir=tmp_path,
    )
    cell = _read_first_data_row(tmp_path / "stoerungen_2026.csv")[4]
    assert not cell.startswith(_FORMULA_PREFIXES), (
        f"Full upstream chain must be closed at the writer: cell {cell!r}."
    )
    assert "​" not in cell, (
        f"Invisible prefix survived end-to-end: cell {cell!r}."
    )


# ============================================================================
# (9) Round-trip via csv.reader confirms parsed cell stays defanged
# ============================================================================


def test_csv_reader_roundtrip_preserves_defang(tmp_path: Path) -> None:
    """Round-trip sanity: the formula payload remains defanged when the
    CSV is read back via :class:`csv.reader`.

    Operators (and downstream tooling like
    :mod:`scripts.generate_markdown_stats`) re-read the CSV via
    :class:`csv.reader`; the defanging apostrophe must persist through
    the read so the cell content matches the rendered Markdown table.
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    payload = "‮=HYPERLINK(\"http://attacker\",\"click\")"
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name=payload,
        stats_dir=tmp_path,
    )
    raw = (tmp_path / "stoerungen_2026.csv").read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    assert len(rows) >= 2
    cell = rows[1][4]
    assert not cell.startswith(_FORMULA_PREFIXES), (
        f"csv.reader round-trip must preserve the defang: cell {cell!r}."
    )
    assert "HYPERLINK" in cell, (
        "Sanitiser must defang, not silently drop, attacker-controlled "
        "payloads — operators need the indicator-of-compromise signal."
    )
