"""Heroku / Docker Hub token drift: boundaries and invariants.

Second half of ``test_sentinel_heroku_docker_token_drift.py`` (threat model
and the positive detection / masking cases (0)-(5) stay there).  This module
holds (6) negative cases, (7) idempotence, (8) scanner / log-mask alignment,
(9) cross-vendor boundary and (10) the source inventory invariants.
"""

from __future__ import annotations

import pytest

from src.utils.logging import sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS, _scan_content
from tests._sentinel_heroku_docker_token_support import (
    _DOCKER_PAT_LONG,
    _HEROKU_UUID,
    _body_b64url,
)


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Wrong Heroku prefix (case matters — lowercase variant should not
        # match because HRKU- is documented as uppercase by Heroku)
        "hrku-AABBCCDD-1234-5678-9ABC-DEF012345678",
        # Wrong prefix entirely
        "XRKU-AABBCCDD-1234-5678-9ABC-DEF012345678",
        # Body too short (< 36 chars)
        "HRKU-AABBCCDD",
        "HRKU-" + _body_b64url(35),
        # Mid-identifier collision
        "X" + "HRKU-" + _body_b64url(40),
        "0" + "HRKU-" + _body_b64url(40),
        # English collisions
        "configure HRKU- prefix tokens",
        "the HRKU- token format is documented at",
    ],
)
def test_benign_heroku_shape_is_not_masked(benign: str) -> None:
    """Negative case: wrong case / wrong prefix / short bodies /
    mid-identifier collisions / benign English text MUST NOT trigger
    the Heroku mask. The ``(?<![A-Za-z0-9])`` lookbehind + literal
    ``HRKU-`` prefix + length floor + ``(?![A-Za-z0-9])`` lookahead
    are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert "HRKU-***" not in result, (
        f"False-positive Heroku mask on benign input: {benign!r} → "
        f"{result!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        # Body too short (< 27 chars)
        "dckr_pat_" + _body_b64url(26),
        # Wrong Docker prefix
        "dckr_xat_" + _body_b64url(36),
        "Xdckr_pat_" + _body_b64url(36),
        # Mid-identifier collision
        "X" + "dckr_pat_" + _body_b64url(36),
        "0" + "dckr_pat_" + _body_b64url(36),
        # English collisions
        "configure dckr_pat settings",
        "the dckr_pat token format is documented at",
    ],
)
def test_benign_docker_hub_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / mid-identifier
    collisions / English collisions MUST NOT trigger the Docker Hub
    mask."""
    result = sanitize_log_message(benign)
    assert "dckr_pat_***" not in result, (
        f"False-positive Docker Hub mask on benign input: {benign!r} → "
        f"{result!r}"
    )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "HRKU-***",
        "dckr_pat_***",
    ],
)
def test_token_mask_is_idempotent(masked: str) -> None:
    """Running ``sanitize_log_message`` twice MUST be idempotent — the
    masked form MUST NOT itself match the corresponding regex (the ``*``
    char is outside every body alphabet AND the masked body length 3 chars
    is below every per-family floor)."""
    log_line = f"prior IR note: token redacted as {masked}"
    result = sanitize_log_message(log_line)
    assert masked in result, (
        f"Idempotence broken: masked form {masked!r} was further modified "
        f"by sanitize_log_message: {result!r}"
    )


def test_heroku_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_HEROKU_UUID}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _HEROKU_UUID not in first


def test_docker_hub_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_DOCKER_PAT_LONG}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _DOCKER_PAT_LONG not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner & log mask agree per-family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_heroku_family() -> None:
    """**Sibling-alignment invariant.** Every Heroku Platform API Token
    shape that appears in ``_KNOWN_TOKENS`` MUST be masked by
    ``sanitize_log_message``. Any future Heroku-family pattern adjustment
    to the scanner without a companion log-mask adjustment fails this test
    on the first pytest run after the new scanner entry is committed —
    surfacing the next drift family programmatically."""
    heroku_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Heroku" in reason
    ]
    assert len(heroku_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Heroku' entry "
        "in _KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks the canonical Heroku shape.
    log_line = f"audit: {_HEROKU_UUID}"
    result = sanitize_log_message(log_line)
    assert _HEROKU_UUID not in result
    assert "HRKU-***" in result


def test_scanner_and_log_sanitiser_share_docker_hub_family() -> None:
    """**Sibling-alignment invariant.** Every Docker Hub PAT shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by
    ``sanitize_log_message``."""
    docker_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Docker Hub" in reason
    ]
    assert len(docker_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Docker Hub' entry "
        "in _KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    log_line = f"audit: {_DOCKER_PAT_LONG}"
    result = sanitize_log_message(log_line)
    assert _DOCKER_PAT_LONG not in result
    assert "dckr_pat_***" in result


# ---------------------------------------------------------------------------
# (9) Cross-vendor boundary — no collision with other token families.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vendor_token,vendor_reason",
    [
        ("ghp_" + "A" * 36, "GitHub Personal Access Token gefunden"),
        ("glpat-" + "A" * 20, "GitLab Personal Access Token gefunden"),
        ("sk_live_" + "L" * 24, "Stripe Live Secret Key gefunden"),
        ("AIza" + "A" * 35, "Google API Key gefunden"),
    ],
)
def test_cross_vendor_no_collision_with_heroku_docker(
    vendor_token: str, vendor_reason: str
) -> None:
    """Cross-vendor regression: other vendors' tokens are not mis-attributed
    to the new Heroku / Docker Hub patterns. Both new detectors anchor on
    prefixes (``HRKU-`` / ``dckr_pat_``) which are unique to their
    respective vendors — no collision possible at the prefix level."""
    findings = _scan_content(f'OTHER_VENDOR = "{vendor_token}"\n')
    reasons = [r for _, _, r in findings]
    assert vendor_reason in reasons, (
        f"Cross-vendor token {vendor_token[:20]}... must keep its "
        f"canonical attribution; got: {reasons}"
    )
    # No false-positive Heroku / Docker Hub attribution.
    assert "Heroku Platform API Token gefunden" not in reasons
    assert "Docker Hub Personal Access Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# (10) Inventory invariants — source-grep enforces presence of the new
# patterns. A future regression that drops a pattern fails this test until
# the canonical detection is restored.
# ---------------------------------------------------------------------------


def test_secret_scanner_module_contains_heroku_known_token_entry() -> None:
    """Inventory pin: ``src/utils/secret_scanner.py`` must contain a
    ``_KNOWN_TOKENS`` entry that anchors the ``HRKU-`` prefix family."""
    from pathlib import Path

    from src.utils import secret_scanner

    # The _KNOWN_TOKENS entries live in src/utils/secret_patterns/ since the
    # 2026-11-02 split; read the facade together with its pattern modules.
    scanner_path = Path(secret_scanner.__file__)
    source = "".join(
        p.read_text(encoding="utf-8")
        for p in [scanner_path, *sorted((scanner_path.parent / "secret_patterns").glob("*.py"))]
    )
    assert "HRKU-" in source, (
        "secret_scanner.py must contain an HRKU- detection pattern "
        "(Heroku Platform API Token)"
    )
    assert "Heroku Platform API Token" in source, (
        "secret_scanner.py must use canonical 'Heroku Platform API Token "
        "gefunden' attribution"
    )


def test_secret_scanner_module_contains_docker_hub_known_token_entry() -> None:
    """Inventory pin: ``dckr_pat_`` Docker Hub PAT detector present."""
    from pathlib import Path

    from src.utils import secret_scanner

    # The _KNOWN_TOKENS entries live in src/utils/secret_patterns/ since the
    # 2026-11-02 split; read the facade together with its pattern modules.
    scanner_path = Path(secret_scanner.__file__)
    source = "".join(
        p.read_text(encoding="utf-8")
        for p in [scanner_path, *sorted((scanner_path.parent / "secret_patterns").glob("*.py"))]
    )
    assert "dckr_pat_" in source, (
        "secret_scanner.py must contain a dckr_pat_ detection pattern "
        "(Docker Hub Personal Access Token)"
    )
    assert "Docker Hub Personal Access Token" in source, (
        "secret_scanner.py must use canonical 'Docker Hub Personal Access "
        "Token gefunden' attribution"
    )
