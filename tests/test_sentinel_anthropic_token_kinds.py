"""Anthropic credentials are recognised by shape, not by a list of kinds.

Both pattern lists, the log sanitiser (``src/utils/log_patterns_cloud_ai.py``)
and the secret scanner (``src/utils/secret_patterns/tokens_vcs_payment_ai.py``),
match ``sk-ant-<kind><NN>-<body>``: ``<kind>`` is 2 to 8 lowercase letters,
``<NN>`` two digits, ``<body>`` at least 32 characters of ``[A-Za-z0-9_-]``.
Until this change the kind was the alternation ``api|admin``; an OAuth token
(``oat01``) went through ``sanitize_log_message`` unchanged and the scanner
reported it under the generic entropy label only.

The test tokens are assembled from parts at run time, so no line of this file
has the shape the patterns look for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.utils.log_patterns_cloud_ai import _CLOUD_AI_TOKEN_PATTERNS
from src.utils.logging import sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS, scan_repository

_ANTHROPIC_LABEL = "Anthropic API Key gefunden"
_PREFIX = "sk-" + "ant-"
# 36 characters from the full body alphabet (letters, digits, "_" and "-").
_BODY = "Aa1Bb2_-Cc3Dd4" + "Ee5Ff6Gg7Hh8" + "Ii9Jj0Kk1L"
_KINDS = ("api03", "admin01", "oat01", "ort01", "sid01", "ab12", "abcdefgh99")


def _token(kind: str, body: str = _BODY) -> str:
    return _PREFIX + kind + "-" + body


def _scan_reasons(tmp_path: Path, text: str) -> list[str]:
    file_path = tmp_path / "config.py"
    file_path.write_text(f'VALUE = "{text}"\n', encoding="utf-8")
    return [finding.reason for finding in scan_repository(tmp_path, paths=[file_path])]


def test_body_fixture_meets_the_length_floor() -> None:
    assert len(_BODY) >= 32


@pytest.mark.parametrize("kind", _KINDS)
def test_log_sanitiser_masks_every_kind(kind: str) -> None:
    token = _token(kind)
    result = sanitize_log_message(f"request failed, credential {token} rejected")
    assert token not in result
    assert _BODY not in result
    # The kind and version stay readable; only the body is replaced.
    assert f"{_PREFIX}{kind}-***" in result


def test_oauth_token_is_masked_inside_a_url_path() -> None:
    token = _token("oat01")
    result = sanitize_log_message(f"GET /v1/audit/{token}/details -> 500")
    assert _BODY not in result
    assert f"{_PREFIX}oat01-***" in result


def test_masked_form_is_stable() -> None:
    once = sanitize_log_message(f"credential {_token('oat01')}")
    assert sanitize_log_message(once) == once


@pytest.mark.parametrize("kind", _KINDS)
def test_scanner_reports_every_kind_with_the_anthropic_label(
    tmp_path: Path, kind: str
) -> None:
    assert _ANTHROPIC_LABEL in _scan_reasons(tmp_path, _token(kind))


def _not_anthropic() -> list[str]:
    return [
        # Body below the 32-character floor.
        _token("oat01", "short"),
        _token("api03", _BODY[:31]),
        # No kind/version segment at all.
        _PREFIX + "eater",
        _PREFIX + "eater-" + _BODY,
        # Kind outside 2..8 lowercase letters.
        _token("a01"),
        _token("abcdefghi01"),
        _token("OAT01"),
        # Version is not exactly two digits.
        _token("oat1"),
        _token("oat"),
        # Prefix inside a longer identifier.
        "x" + _token("oat01"),
    ]


@pytest.mark.parametrize("text", _not_anthropic())
def test_anthropic_patterns_leave_other_shapes_alone(text: str) -> None:
    """Checked on the two Anthropic expressions themselves.

    Other, generic rules of the sanitiser and the scanner may still act on a
    long random-looking string; this test is about the Anthropic entries.
    """
    log_patterns = [
        re.compile(pattern)
        for pattern, _replacement in _CLOUD_AI_TOKEN_PATTERNS
        if _PREFIX in pattern
    ]
    scanner_patterns = [regex for regex, label in _KNOWN_TOKENS if label == _ANTHROPIC_LABEL]
    assert len(log_patterns) == 1
    assert len(scanner_patterns) == 1
    assert log_patterns[0].search(text) is None
    assert scanner_patterns[0].search(text) is None


@pytest.mark.parametrize("text", [_PREFIX + "eater", _token("oat01", "short")])
def test_log_sanitiser_keeps_short_lookalikes(text: str) -> None:
    line = f"module {text} loaded"
    assert sanitize_log_message(line) == line


@pytest.mark.parametrize("text", [_PREFIX + "eater", _token("oat01", "short")])
def test_scanner_does_not_label_short_lookalikes(tmp_path: Path, text: str) -> None:
    assert _ANTHROPIC_LABEL not in _scan_reasons(tmp_path, text)


def test_both_lists_use_the_same_expression() -> None:
    """The scanner expression and the sanitiser expression differ only in the capture group."""
    log_pattern = next(
        pattern for pattern, _replacement in _CLOUD_AI_TOKEN_PATTERNS if _PREFIX in pattern
    )
    scanner_pattern = next(
        regex.pattern for regex, label in _KNOWN_TOKENS if label == _ANTHROPIC_LABEL
    )
    assert log_pattern.replace("(sk-ant-[a-z]{2,8}[0-9]{2})", "sk-ant-[a-z]{2,8}[0-9]{2}") == (
        scanner_pattern
    )
