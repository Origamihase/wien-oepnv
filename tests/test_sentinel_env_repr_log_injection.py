"""Sentinel PoC: ``%r`` env-controlled log sinks let 256 canonical-dangerous
code points reach :attr:`logging.LogRecord.args` and :meth:`getMessage`
verbatim, bypassing the per-arg :func:`src.utils.logging.sanitize_log_arg`
contract pinned across ``src/`` and ``scripts/``.

The drift family the prior path-log rounds closed (PRs #1456, #1468,
#1475, #1473, #1472) all replaced ``%s`` interpolations of env- or
upstream-controlled strings with ``sanitize_log_arg`` before format-
interpolation. That defence pins the sanitisation BEFORE the value
lands in :attr:`record.args` — so consumers reading the record before
:class:`src.feed.logging_safe.SafeFormatter` runs (pytest's
``caplog``, custom handlers, JSON shippers, the GitHub-Actions live
log viewer's :class:`StreamHandler` adapter, etc.) all see safe text.

Seven sibling sites still interpolate env-controlled values via the
bare ``%r`` repr conversion. While Python's :func:`repr` escapes most
control bytes (``\\n``, ``\\x1b``, the BMP ``\\u`` band, the 8-bit C1
band), it leaves **256 code points** in :data:`unicodedata.category`
``Mn`` — the Variation Selector ranges :code:`U+FE00`–:code:`U+FE0F`
and :code:`U+E0100`–:code:`U+E01EF` — in the output verbatim. These
characters are zero-width invisible Unicode formatting controls that
the canonical
:data:`src.utils.logging._INVISIBLE_DANGEROUS_RE` (line 223 of
``src/utils/logging.py``) is explicitly built to strip. Sites covered
by this PoC:

  * ``scripts/update_station_directory.py``
      - L793  ``_parse_radius``       ``PLACES_RADIUS_M`` env override
      - L804  ``_parse_max_results``  ``PLACES_MAX_RESULTS`` env override
      - L817  ``_parse_float``        generic float env (``REQUEST_TIMEOUT_S``, ``MERGE_MAX_DIST_M``)
      - L827  ``_parse_int``          generic int env   (``REQUEST_MAX_RETRIES``)

  * ``scripts/update_baustellen_cache.py``
      - L352  ``_resolve_data_url``   ``BAUSTELLEN_DATA_URL`` env override
      - L626  ``main``                ``BAUSTELLEN_TIMEOUT``  env override

  * ``src/build_feed.py``
      - L378  ``_read_optional_non_negative_int``  per-provider
              env override (``PROVIDER_<SLUG>_MAX_AGE_DAYS`` etc.)

Threat model
============
An operator-controlled, leaked-CI-runner, or compromised-secret-store
env value carrying a Variation Selector lands in:

  * ``record.args[N]`` — the raw value pre-format. Read by any handler
    iterating ``record.args`` (custom ``RecordFactory``-style adapters,
    structured log emitters that inspect args separately from msg).
  * ``record.getMessage()`` — the formatted message including the
    ``'…\\ufe0e…'`` text from :func:`repr`, which preserves the
    invisible byte sequence verbatim (NOT as a backslash-escape, despite
    superficial appearances; see the PoC's
    ``test_repr_leaks_variation_selector_into_record_args`` baseline).
  * Pytest's ``caplog.text`` — caplog uses its own
    :class:`logging.Formatter` that does NOT route through
    :class:`SafeFormatter`. Any future test that asserts a clean log
    record sees the planted Variation Selector and silently passes,
    masking the leak.
  * The aggregated cron log captured in ``$log_dir/<script>.log`` and
    surfaced in the GitHub Actions UI when ``setup_script_logging`` is
    NOT installed (e.g. an early failure path that runs before
    :func:`configure_logging`).

Defence shape
=============
Mirrors the canonical contract from the prior path-log rounds: route
the env-controlled value through :func:`sanitize_log_arg` BEFORE
format-interpolation and switch ``%r`` to ``%s``. The helper strips
the canonical ``_INVISIBLE_DANGEROUS_RE`` union (including all 256
Variation Selectors) plus ANSI escape codes, redacts the secret-keys
union, and escapes ``\\n`` / ``\\r`` / ``\\t``.
"""
from __future__ import annotations

import logging
from unittest import mock

import pytest


# Canonical attack-byte inventory. Every primitive must be stripped from
# every captured-record args[*] AND getMessage() at every site below.
# The first eight are the Trojan-Source / log-injection / ANSI / Cf-format
# primitives where Python's ``repr`` ALREADY escapes the byte — keeping
# them in the inventory acts as a regression-guard that the new fix did
# not regress the prior defence. The two Variation Selectors are the
# code points where ``repr`` passes the byte through verbatim — these
# are the unique-to-this-round exploit vectors.
_PRIMITIVES: list[tuple[str, str]] = [
    ("‮", "U+202E RIGHT-TO-LEFT OVERRIDE"),
    ("​", "U+200B ZERO WIDTH SPACE"),
    ("­", "U+00AD SOFT HYPHEN"),
    ("", "U+009B 8-bit CSI"),
    ("", "U+009D 8-bit OSC"),
    ("", "U+001B ESC (ANSI prefix)"),
    ("\n", "U+000A newline (record terminator)"),
    ("\r", "U+000D carriage return"),
    ("\U000e0020", "U+E0020 Unicode Tag SPACE"),
    # Variation Selectors — the unique-to-this-round bypass: Python
    # ``repr`` treats these as printable (Unicode category ``Mn``) and
    # passes them through unescaped.
    ("︎", "U+FE0E VARIATION SELECTOR-15 (text style)"),
    ("️", "U+FE0F VARIATION SELECTOR-16 (emoji style)"),
    ("\U000e0100", "U+E0100 VARIATION SELECTOR-17"),
    ("\U000e01ef", "U+E01EF VARIATION SELECTOR-256"),
]


def _assert_primitive_absent_from_record_state(
    caplog: pytest.LogCaptureFixture,
    primitive: str,
    primitive_label: str,
    site_label: str,
) -> None:
    """Assert no captured log record carries the primitive verbatim in
    either ``record.args`` or ``record.getMessage()``.

    Both paths matter:
    * ``record.args[N]`` — what pre-formatter consumers iterate over.
    * ``record.getMessage()`` — what caplog's text-based assertions /
      :class:`logging.Formatter` consumers see.
    """
    for record in caplog.records:
        for arg in record.args or ():
            assert primitive not in str(arg), (
                f"{primitive_label} ({primitive!r}) leaked through "
                f"{site_label} into record.args: {arg!r}"
            )
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"{site_label} into getMessage(): {message!r}"
        )


# ============================================================================
# Baseline: prove the bypass — Python ``repr`` lets Variation Selectors
# pass through verbatim into a LogRecord's args and getMessage().
# ============================================================================


def test_repr_leaks_variation_selector_into_record_args(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``%r`` formatting leaves U+FE0E in ``record.args[0]`` verbatim.

    This is the BYPASS that motivates the fix. ``repr('a\\ufe0eb')``
    returns ``"'a\\ufe0eb'"`` (8 codepoints: quote, a, U+FE0E, b, quote
    — the VS-15 character itself, NOT the six-byte ``\\ufe0e`` escape).
    """
    logger = logging.getLogger("sentinel.repr.bypass")
    caplog.set_level(logging.WARNING, logger="sentinel.repr.bypass")
    poisoned = "evil︎burger"
    logger.warning("Invalid env=%r", poisoned)

    def _args_contain_vs15(record: logging.LogRecord) -> bool:
        if record.args is None:
            return False
        args_tuple: tuple[object, ...] = (
            record.args if isinstance(record.args, tuple) else (record.args,)
        )
        return any("︎" in str(arg) for arg in args_tuple)

    assert any(_args_contain_vs15(record) for record in caplog.records), (
        "Baseline regression: ``%r`` is supposed to leak U+FE0E into "
        "record.args[0]. If this assertion fails, Python's repr() "
        "behaviour changed and the rest of this test file is stale."
    )
    assert any(
        "︎" in record.getMessage() for record in caplog.records
    ), (
        "Baseline regression: ``%r`` is supposed to leak U+FE0E into "
        "record.getMessage(). If this assertion fails, Python's repr() "
        "behaviour changed and the rest of this test file is stale."
    )


# ============================================================================
# Variation-Selector inventory: enumerate ALL 256 code points and confirm
# Python's ``repr`` passes each one through verbatim. Pins the threat
# model that motivated the fix.
# ============================================================================


def test_variation_selector_range_inventory_leaks_through_repr() -> None:
    """All 256 code points in U+FE00-U+FE0F + U+E0100-U+E01EF leak via ``%r``.

    Acts as the structural pin for the threat model — any future Python
    release that starts escaping Variation Selectors in :func:`repr`
    would fail this assertion and the fix becomes redundant.
    """
    variation_selectors = list(range(0xFE00, 0xFE10)) + list(range(0xE0100, 0xE01F0))
    leaked: list[int] = []
    for cp in variation_selectors:
        ch = chr(cp)
        if ch in repr(ch):
            leaked.append(cp)

    assert len(leaked) == 256, (
        f"Expected ALL 256 Variation Selectors to leak through repr(); "
        f"observed {len(leaked)} leaks. Python's repr() behaviour has "
        f"changed — re-audit the fix and ensure the threat model still "
        f"holds for the remaining vectors."
    )


# ============================================================================
# scripts/update_station_directory.py — four ``_parse_*`` sites (L793, L804, L817, L827)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_update_station_directory_parse_radius_strips_primitive(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L793: ``_parse_radius`` rejects non-integer ``PLACES_RADIUS_M``."""
    from scripts.update_station_directory import _parse_radius

    poisoned = f"abc{primitive}def"
    caplog.set_level(logging.WARNING)
    result = _parse_radius(poisoned)
    assert result == 2500
    _assert_primitive_absent_from_record_state(
        caplog, primitive, primitive_label, "update_station_directory:_parse_radius"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_update_station_directory_parse_max_results_strips_primitive(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L804: ``_parse_max_results`` rejects non-integer ``PLACES_MAX_RESULTS``."""
    from scripts.update_station_directory import _parse_max_results

    poisoned = f"abc{primitive}def"
    caplog.set_level(logging.WARNING)
    result = _parse_max_results(poisoned)
    assert result == 20
    _assert_primitive_absent_from_record_state(
        caplog,
        primitive,
        primitive_label,
        "update_station_directory:_parse_max_results",
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_update_station_directory_parse_float_strips_primitive(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L817: ``_parse_float`` rejects non-numeric env values."""
    from scripts.update_station_directory import _parse_float

    poisoned = f"abc{primitive}def"
    caplog.set_level(logging.WARNING)
    result = _parse_float(poisoned, key="REQUEST_TIMEOUT_S", default=25.0)
    assert result == 25.0
    _assert_primitive_absent_from_record_state(
        caplog, primitive, primitive_label, "update_station_directory:_parse_float"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_update_station_directory_parse_int_strips_primitive(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L827: ``_parse_int`` rejects non-integer env values."""
    from scripts.update_station_directory import _parse_int

    poisoned = f"abc{primitive}def"
    caplog.set_level(logging.WARNING)
    result = _parse_int(poisoned, key="REQUEST_MAX_RETRIES", default=4)
    assert result == 4
    _assert_primitive_absent_from_record_state(
        caplog, primitive, primitive_label, "update_station_directory:_parse_int"
    )


# ============================================================================
# scripts/update_baustellen_cache.py — two env-override sites (L352, L626)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_baustellen_resolve_data_url_strips_primitive(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L352: ``_resolve_data_url`` rejects unknown-host ``BAUSTELLEN_DATA_URL``."""
    from scripts.update_baustellen_cache import (
        DEFAULT_DATA_URL,
        _resolve_data_url,
    )

    # Construct an https URL pointing at a non-allowlisted host that
    # carries the primitive — the host check fails and the WARNING fires.
    poisoned = f"https://attacker.example.com/poisoned{primitive}path.geojson"
    caplog.set_level(logging.WARNING)
    result = _resolve_data_url(poisoned)
    assert result == DEFAULT_DATA_URL
    _assert_primitive_absent_from_record_state(
        caplog, primitive, primitive_label, "update_baustellen_cache:_resolve_data_url"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_baustellen_main_timeout_strips_primitive(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L626: ``main`` rejects non-integer ``BAUSTELLEN_TIMEOUT``.

    We exercise the WARNING path by setting an invalid timeout env value
    and stubbing the network/cache calls so ``main()`` short-circuits
    before any real I/O.
    """
    import scripts.update_baustellen_cache as ubc

    poisoned = f"abc{primitive}def"
    caplog.set_level(logging.WARNING)

    # Wire up a minimal stub: ``_fetch_remote`` returns None so
    # ``_load_fallback`` runs, but we also stub that to return None.
    # Both calls return None => ``main`` exits before
    # ``write_cache`` / ``serialize_for_cache`` ever runs.
    with (
        mock.patch.dict(
            "os.environ",
            {
                "BAUSTELLEN_TIMEOUT": poisoned,
                "BAUSTELLEN_FALLBACK_PATH": "",
                "BAUSTELLEN_DATA_URL": "",
            },
            clear=False,
        ),
        mock.patch.object(ubc, "_fetch_remote", return_value=None),
        mock.patch.object(ubc, "_load_fallback", return_value=None),
        mock.patch.object(ubc, "configure_logging"),
    ):
        rc = ubc.main()
    assert rc == 1
    _assert_primitive_absent_from_record_state(
        caplog, primitive, primitive_label, "update_baustellen_cache:main(timeout)"
    )


# ============================================================================
# src/build_feed.py — single env-override site (L378)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_build_feed_read_negative_int_strips_primitive(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L378: ``_read_optional_non_negative_int`` rejects negative env override.

    The function is called with an env var name; the WARNING at L378
    fires when ``int(raw)`` succeeds but the value is negative. We use
    a hand-crafted env value ``-1`` prefixed with a primitive that
    sneaks past ``int()`` via the leading sign trick — but ``int()``
    rejects any prefix outside ``\\s+``/``+-``/digits, so we instead
    craft a value containing the primitive that ``int()`` accepts via
    surrounding whitespace.

    The test invokes the function directly with a poisoned env value;
    when ``int()`` rejects the parse (most primitives), L370's WARNING
    fires; when it accepts (none of the canonical primitives are
    integer-valid), L378 fires. Either way the primitive flows through
    the log line and is asserted absent post-fix.
    """
    from src.build_feed import _read_optional_non_negative_int

    poisoned = f"abc{primitive}def"
    monkeypatch.setenv("PROVIDER_FOO_MAX_AGE_DAYS", poisoned)
    caplog.set_level(logging.WARNING)
    _read_optional_non_negative_int("PROVIDER_FOO_MAX_AGE_DAYS")
    _assert_primitive_absent_from_record_state(
        caplog,
        primitive,
        primitive_label,
        "build_feed:_read_optional_non_negative_int",
    )


# ============================================================================
# Inventory invariant: grep the canonical source files for any remaining
# ``%r`` interpolations of env-controlled raw values that bypass the
# ``sanitize_log_arg`` contract. Fails on regression / new drift.
# ============================================================================


def test_inventory_no_env_repr_log_drift_in_canonical_sites() -> None:
    """No env-controlled ``raw``/``text``/``timeout_raw`` is logged via ``%r``.

    Mirrors the closing-checklist shape of the prior path-log rounds.
    A future ``logger.warning('... %r', env_value)`` drift fails this
    test on the first pytest run.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    sites = [
        repo_root / "scripts" / "update_station_directory.py",
        repo_root / "scripts" / "update_baustellen_cache.py",
        repo_root / "src" / "build_feed.py",
    ]
    # ``%r`` formatting of env-controlled values is the drift this round
    # closes. The canonical pattern is ``%s`` + ``sanitize_log_arg``.
    # Acceptable: ``%r`` on hardcoded probe queries (e.g.
    # ``PROBE_QUERY`` constants in verify scripts), ``%r`` on
    # exception-type names, ``%r`` on canonical ``type(exc).__name__``.
    # The grep below targets the specific names ``raw`` / ``text`` /
    # ``*_raw`` that the prior round documented as the env-flow shape.
    drift_patterns = [
        ", raw)",
        ", raw,",
        ", text)",
        ", text,",
        ", timeout_raw)",
        ", timeout_raw,",
    ]
    for site in sites:
        source = site.read_text(encoding="utf-8")
        for line_no, line in enumerate(source.splitlines(), start=1):
            if "%r" not in line:
                continue
            # Allow ``sanitize_log_arg(raw)`` — that's the canonical fix.
            if "sanitize_log_arg" in line:
                continue
            # Ignore docstrings / comments.
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                continue
            for pat in drift_patterns:
                if pat in line:
                    pytest.fail(
                        f"{site.name}:{line_no} drifted from the canonical "
                        f"``%s + sanitize_log_arg`` contract — uses ``%r`` "
                        f"on an env-controlled value: {line.strip()!r}"
                    )


# ============================================================================
# Positive: the fix preserves operator-correlation. After the fix, the
# log line still carries the (sanitised) env value so an operator can
# distinguish "bad PLACES_RADIUS_M" from "bad PLACES_MAX_RESULTS".
# ============================================================================


def test_fix_preserves_safe_ascii_correlation_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Safe ASCII payloads still appear verbatim in the log line.

    The fix uses :func:`sanitize_log_arg` which is byte-preserving on
    benign ASCII — the operator can still see ``Invalid PLACES_RADIUS_M=
    twentyfive`` instead of just ``Invalid PLACES_RADIUS_M=...``.
    """
    from scripts.update_station_directory import _parse_radius

    benign = "twentyfive"  # not a number, but no attack bytes
    caplog.set_level(logging.WARNING)
    result = _parse_radius(benign)
    assert result == 2500
    assert any(
        benign in record.getMessage() for record in caplog.records
    ), (
        f"Benign correlation token missing from log line; "
        f"records={[r.getMessage() for r in caplog.records]!r}"
    )


def test_fix_strips_ansi_escape_from_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ANSI CSI sequences are stripped at the args level by ``sanitize_log_arg``.

    Defence-in-depth complement to ``repr``'s ``\\x1b`` escaping: even
    if a future refactor re-introduces ``%s`` formatting somewhere, the
    canonical helper neutralises the ESC byte before it lands in args.
    """
    from scripts.update_station_directory import _parse_radius

    # CSI red ANSI sequence + colour reset
    poisoned = "abc\x1b[31mDANGER\x1b[0m"
    caplog.set_level(logging.WARNING)
    _parse_radius(poisoned)
    for record in caplog.records:
        message = record.getMessage()
        assert "\x1b" not in message, (
            f"ANSI ESC byte leaked into log message: {message!r}"
        )
        for arg in record.args or ():
            assert "\x1b" not in str(arg), (
                f"ANSI ESC byte leaked into record.args: {arg!r}"
            )


def test_fix_strips_newline_log_forgery_from_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Newline / log-forgery primitives are stripped at the args level.

    A planted ``raw='2500\\n[CRIT] FAKE LOG ENTRY'`` cannot inject a
    second log entry after the fix — ``sanitize_log_arg`` escapes the
    newline to ``\\n`` (literal backslash-n) before format-interpolation.
    """
    from scripts.update_station_directory import _parse_radius

    poisoned = "abc\n[CRIT] FAKE LOG ENTRY: cron pipeline compromised"
    caplog.set_level(logging.WARNING)
    _parse_radius(poisoned)
    for record in caplog.records:
        message = record.getMessage()
        assert "\n[CRIT]" not in message, (
            f"Log forgery sequence leaked into message: {message!r}"
        )
        for arg in record.args or ():
            assert "\n" not in str(arg), (
                f"Newline leaked into record.args: {arg!r}"
            )
