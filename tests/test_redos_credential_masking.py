"""Regression tests for two catastrophic-backtracking (ReDoS) findings in
the credential-masking paths.

1. ``src/utils/logging.py`` — ``sanitize_log_message`` ran a sweep of
   credential-masking patterns whose key-name affixes were unbounded
   (``[a-z0-9_.\\-]*`` / ``[-a-zA-Z0-9_]*`` around each keyword, plus the
   unbounded URL-scheme class ``[a-z0-9+.-]+://``). A hostile log argument
   made of a long run of those character-class bytes with no terminating
   ``://`` / ``[:=]`` / ``:`` caused O(n^2) backtracking — ~75 s for 100 KB,
   and growing — freezing the whole log pipeline (the function runs on every
   operator log line and the public ``docs/feed_health.json``).

   Fix: every key-name affix is length-bounded (``{0,64}``), the URL scheme
   class is bounded + possessive (``{1,64}+``), and the function caps the
   input length it sweeps (``_MAX_SANITIZE_INPUT_CHARS``) so the worst case
   is constant regardless of input size.

2. ``src/utils/secret_scanner.py`` — ``_SENSITIVE_ASSIGN_RE`` wrapped its
   keyword group in unbounded ``[a-z0-9_.-]*`` affixes; a committed line
   holding ``token`` + a long ``[a-z0-9_.-]`` run + no trailing ``[:=]``
   backtracked O(n^2) (~36 s for 10 KB), stalling the CI secret gate so
   sibling files never got scanned.

   Fix: the affixes are length-bounded (``{0,64}``); ``re``/``finditer``
   still advance across the whole input, so a real key preceded by a long
   prefix is still found — only the per-position look-ahead is capped.

The timing assertions use deliberately generous thresholds: the *fixed*
code runs these in well under a second, the *broken* code took tens of
seconds to minutes, so a multi-second ceiling is a stable DoS-regression
guard that tolerates slow CI runners without flaking.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.utils import secret_scanner as ss
from src.utils.logging import (
    _MAX_SANITIZE_INPUT_CHARS,
    sanitize_log_message,
)


# --------------------------------------------------------------------------
# 1. logging.sanitize_log_message
# --------------------------------------------------------------------------


def _timed(fn: Callable[..., Any], *args: Any) -> float:
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


def test_sanitize_log_message_redos_inputs_are_bounded() -> None:
    """Hostile inputs of several shapes must each finish fast (was O(n^2))."""
    hostile = [
        "-" + "a" * 200_000 + ".b",          # no ://, :, =  (scheme/affix run)
        "x" + "a" * 200_000 + ":",           # trailing colon (header shape)
        "token=" + "a" * 200_000,            # real key + long unquoted value
        "apikey." * 30_000,                  # keyword-dense run
    ]
    for payload in hostile:
        elapsed = _timed(sanitize_log_message, payload)
        assert elapsed < 5.0, (
            f"sanitize_log_message took {elapsed:.2f}s on a "
            f"{len(payload)}-char hostile input — ReDoS regression "
            f"(fixed code runs this in <1s; broken code took minutes)."
        )


def test_sanitize_log_message_is_size_capped() -> None:
    """Worst-case cost is constant: a 1 MB input is no slower than 8 KB."""
    elapsed = _timed(sanitize_log_message, "-" + "a" * 1_000_000 + ".b")
    assert elapsed < 5.0, f"1 MB input not bounded: {elapsed:.2f}s"

    # Inputs longer than the cap are truncated (before masking, so nothing
    # past the cap can leak); shorter inputs are passed through untouched.
    long_in = "A" * (_MAX_SANITIZE_INPUT_CHARS + 500) + " tail"
    out = sanitize_log_message(long_in)
    assert out.endswith("...[truncated]")
    assert len(out) <= _MAX_SANITIZE_INPUT_CHARS + len(" ...[truncated]")
    assert "tail" not in out  # the tail past the cap is dropped, not emitted


def test_sanitize_log_message_still_redacts_after_hardening() -> None:
    """The ReDoS fix must not regress credential detection."""
    cases = {
        "postgres://user:secret@db:5432/x": "postgres://***@db:5432/x",
        "api_key=ABCDEF123456": "api_key=***",
    }
    for raw, expected in cases.items():
        assert sanitize_log_message(raw) == expected, raw

    # Header / SAML / CSRF forms (the bounded-affix patterns) still mask.
    for raw in (
        "X-Api-Key: sk-live-9999",
        "Authorization: Bearer abcdef123456789",
        "X-CSRF: abc123def456ghi789jkl012mno345",
        "SAMLArt: AAQAACK4Gj1uFBjQqwbeQk5jeSrXgQ",
    ):
        masked = sanitize_log_message(raw)
        assert masked.endswith("***"), f"not masked: {raw!r} -> {masked!r}"


# --------------------------------------------------------------------------
# 2. secret_scanner._scan_content
# --------------------------------------------------------------------------


def test_secret_scanner_redos_input_is_linear() -> None:
    """A long key-char run with no ``[:=]`` must not backtrack quadratically.

    The scanner is (correctly) linear in file size — no length cap, since a
    file scanner must read whole files. At 30 KB the fixed code runs in ~1.5 s
    while the broken O(n^2) code would take ~5 minutes (36 s at 10 KB × 9), so
    the multi-second ceiling cleanly separates fixed from regressed.
    """
    elapsed = _timed(ss._scan_content, "token" + "a" * 30_000 + "=")
    assert elapsed < 5.0, (
        f"_scan_content took {elapsed:.2f}s on a 30 KB hostile input — "
        f"ReDoS regression (fixed: ~1.5s; broken: minutes)."
    )


def test_secret_scanner_still_detects_after_hardening() -> None:
    """Bounding the affixes must not gut detection of real assignments."""
    for probe in (
        'api_key = "AKIAIOSFODNN7EXAMPLE"',
        "password=supersecretvalue123",
        "my-client-secret: hunter2longvalue",
    ):
        assert ss._scan_content(probe), f"missed secret in: {probe!r}"

    # A real keyword preceded by a long (but <64-char) prefix is still found,
    # confirming the bound did not break the deep-keyword case.
    assert ss._scan_content("x" * 40 + "_api_key = somevalue123")
