"""Sentinel PoC: secret-scanner drift Round 15 — closes the **Slack
workflow-token family gap** explicitly named-but-deferred by Round 14's
closing checklist (AWS ABIA prefix, PR #1520).

Round 14 (PR #1520, 2026-05-16) closed the AWS 4-character credential-
prefix family at 4-of-4 (``AKIA``/``ASIA``/``ACCA``/``ABIA``) and
explicitly named the next-round adjacent-prefix candidates. From the
Round-14 closing checklist:

  **Slack workflow tokens** (``xoxe-`` refresh token, ``xoxc-`` browser
  token, ``xoxd-`` cookie/session token — adjacent-PREFIX candidates if
  the Slack family expands beyond the four already covered).

The pre-Round-15 Slack family covered four prefixes:

  * ``xoxb-`` (Bot User Token, strict shape) — covered
  * ``xoxp-`` (User Token, strict shape) — covered
  * ``xoxa-`` (OAuth App Configuration Token, permissive shape) — covered
  * ``xoxr-`` (Legacy Refresh Token, permissive shape) — covered

This round closes the THREE remaining canonical Slack token-family
prefixes, completing the Slack issuer landscape at 7-of-7 documented
prefixes:

  * ``xoxe-`` / ``xoxe.xoxb-`` / ``xoxe.xoxp-`` (Token Rotation
    Refresh Token — the modern V2 rotation flow introduced 2020).
  * ``xoxc-`` (Browser Session Token — extracted from Slack web
    client cookies, grants user-level session auth).
  * ``xoxd-`` (Cookie Session Token — the ``d`` cookie value paired
    with ``xoxc-`` for direct browser-style API access).

Threat model
------------

A leaked Slack workflow token slips past every detection branch in
``_scan_content``:

  1. **``_PEM_RE``** — no PEM markers; no match.

  2. **``_KNOWN_TOKENS``** — pre-fix no entry covers the ``xoxe-`` /
     ``xoxc-`` / ``xoxd-`` prefixes; no match.

  3. **``_AWS_ID_RE``** — wrong issuer family; no match.

  4. **``_BEARER_RE``** — requires the literal ``Bearer `` keyword
     before the body; bare Slack tokens without that prefix do NOT
     match.

  5. **``_SENSITIVE_ASSIGN_RE``** — only fires when the variable
     name carries a sensitive keyword (``key``/``secret``/``token``/
     etc.). For bare token leaks in log lines, JSON fixtures without
     sensitive keys, comments, documentation snippets, or arbitrary
     text — NO match.

  6. **``_HIGH_ENTROPY_RE``** — the permissive body alphabet of
     Slack tokens (``[0-9a-zA-Z\\-]``) does lie inside the entropy
     alphabet ``[A-Za-z0-9+/=_-]``, so the body span matches
     generically as a ``Hochentropischer Token-String`` finding —
     BUT this LOSES the Slack-specific issuer attribution that
     incident-response keys off (revocation flow at api.slack.com/
     apps/, distinct from every other vendor's). Furthermore, for
     uniform-character-class bodies (all-lowercase / all-uppercase /
     all-digit, common for hash-derived or poorly-seeded RNG tokens),
     the entropy fallback's ``_looks_like_secret`` heuristic requires
     ``min_categories=2`` and may return ``False`` for the body span —
     a fully silent-undetection branch for uniform bodies. The xoxe-
     prefix itself is NOT in the matched span (only the body is), so
     the issuer attribution is lost even when the entropy fallback
     does fire.

Per-prefix blast radius:

  * **xoxe- / xoxe.xoxb- / xoxe.xoxp-** (Token Rotation Refresh):
    The holder can mint fresh ``xoxb-``/``xoxp-`` access tokens with
    the rotation chain's identity and scopes until the refresh token
    is revoked. Slack's V2 rotation introduces 12-hour access-token
    TTLs but refresh tokens are long-lived (typically multi-month),
    so a leaked refresh token grants persistent access until manual
    revocation. Revocation flow: api.slack.com/apps/<app>/oauth >
    Reinstall App (rotates the rotation chain).

  * **xoxc-** (Browser Session Token): Extracted from ``slack.com``
    browser session cookies, granting full user-level authentication
    scope with no scope restrictions (acts as the logged-in user).
    Unofficial tools (slack_cleaner, slackdump, slack-export scripts)
    use xoxc- for unattended scripted access to the user's Slack
    workspace. A leak is the canonical "session hijack" credential.
    Revocation flow: user must log out of Slack and revoke active
    sessions at slack.com/account/sessions (no token-revocation UI;
    cookie-based auth).

  * **xoxd-** (Cookie Session Token): Companion to xoxc-, carrying
    the ``d`` cookie value from the browser session. Used in
    conjunction with xoxc- by unofficial scripted access tools.
    A leak grants the same SESSION-LEVEL access as xoxc-; both
    tokens typically leak together.

Severity
--------

**HIGH-MEDIUM** — attribution drift for the modern Slack rotation
family + silent-undetection for uniform-character-class bodies (same
shape as Bearer case-sensitivity drift, Round 2026-05-15 PR #c074c89).
The high-severity branch is the silent-undetection case for uniform
bodies; the medium-severity branch is the attribution-drift case
where the entropy fallback caught the body but lost the Slack-specific
issuer attribution that determines which revocation playbook applies
(api.slack.com vs. slack.com/account/sessions).

Fix
---

Three new ``_KNOWN_TOKENS`` entries appended in ``src/utils/
secret_scanner.py``, placed BEFORE the existing ``xoxb-``/``xoxp-``/
``xoxa-``/``xoxr-`` entries so ``is_covered`` correctly anchors the
chained ``xoxe.xoxb-``/``xoxe.xoxp-`` shape at the more-specific
rotation attribution (the inner ``xoxb-``/``xoxp-`` span is suppressed
by the larger ``xoxe.``-prefixed match).

Marker: SENTINEL_SLACK_WORKFLOW_TOKEN_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_SLACK_WORKFLOW_TOKEN_DRIFT = "slack workflow token family drift round 15"


# ---------------------------------------------------------------------------
# 1. xoxe- direct refresh token (silent-undetection / attribution-drift)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_slack_xoxe_direct_refresh_token(
    tmp_path: Path,
) -> None:
    """Bare ``xoxe-<body>`` token (direct shape from the V2 rotation
    flow) in plaintext context. Pre-fix the entropy fallback may catch
    the body span but loses the Slack-Token-Rotation-Refresh-Token-
    specific issuer attribution that anchors revocation at
    api.slack.com/apps/<app>/oauth (distinct from xoxr-/xoxa-/xoxb-/
    xoxp- revocation flows)."""
    file_path = tmp_path / "rotation.py"
    # Synthetic ``1A``-repeat body — clearly non-realistic, matches
    # the new detector's [0-9a-zA-Z\-]{20,} alphabet without resembling
    # any real-world Slack token (avoids GitHub push-protection false
    # positives on test fixtures).
    secret = "xoxe-1-" + "1A" * 15
    file_path.write_text(
        f'SLACK_REFRESH_TOKEN = "{secret}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect xoxe- direct refresh token"
    assert secret not in [f.match for f in findings], (
        "Raw secret must be masked in findings"
    )
    reasons = [f.reason for f in findings]
    assert "Slack Token Rotation Refresh Token gefunden" in reasons, (
        f"Expected Slack-rotation-specific attribution; got reasons: {reasons}"
    )


def test_secret_scanner_detects_slack_xoxe_chained_bot_refresh_token(
    tmp_path: Path,
) -> None:
    """Chained ``xoxe.xoxb-<body>`` shape from the V2 token rotation
    flow (https://api.slack.com/authentication/rotation). This is
    the canonical refresh-token format Slack issues for bot tokens —
    the embedded ``xoxb-`` prefix anchors the rotation chain for
    Slack's auth server lookup. Pre-fix the bare ``xoxb-`` regex
    requires a strict ``[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}`` body
    that the rotation shape (``1-<base64body>``) does not match, so
    the existing detector misses the chained form entirely."""
    file_path = tmp_path / "rotation_bot.py"
    # Synthetic body — clearly non-realistic test fixture mirroring
    # the canonical xoxe.xoxb- chained shape without triggering
    # GitHub push-protection false positives on real-token shapes.
    secret = "xoxe.xoxb-1-" + "1A" * 20
    file_path.write_text(
        f'BOT_REFRESH_TOKEN = "{secret}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "Slack Token Rotation Refresh Token gefunden" in reasons, (
        f"Chained xoxe.xoxb- shape MUST be attributed as rotation token "
        f"(not as bare Slack Bot Token via inner xoxb- match). "
        f"Got reasons: {reasons}"
    )


def test_secret_scanner_detects_slack_xoxe_chained_user_refresh_token(
    tmp_path: Path,
) -> None:
    """Chained ``xoxe.xoxp-<body>`` shape from the V2 token rotation
    flow — user-token rotation companion to ``xoxe.xoxb-``."""
    file_path = tmp_path / "rotation_user.py"
    secret = "xoxe.xoxp-1-" + "1B" * 20
    file_path.write_text(
        f'USER_REFRESH_TOKEN = "{secret}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "Slack Token Rotation Refresh Token gefunden" in reasons, (
        f"Chained xoxe.xoxp- shape MUST be attributed as rotation token. "
        f"Got reasons: {reasons}"
    )


# ---------------------------------------------------------------------------
# 2. xoxc- Slack Browser Session Token (session-hijack credential)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_slack_xoxc_browser_session_token(
    tmp_path: Path,
) -> None:
    """Bare ``xoxc-<body>`` browser session token (extracted from
    Slack web client cookies). Grants full user-level session auth
    with no scope restrictions — the canonical "session hijack"
    credential. A leak is equivalent to the user being logged in."""
    file_path = tmp_path / "session.py"
    # Mimics real-world xoxc- format: team-id-channel-id-body.
    secret = "xoxc-1234567890-1234567890-AAAAAAAAAAAAAAAAAAAA"
    file_path.write_text(
        f'SLACK_BROWSER_TOKEN = "{secret}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect xoxc- browser session token"
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "Slack Browser Session Token gefunden" in reasons, (
        f"Expected Slack browser-session-specific attribution; "
        f"got reasons: {reasons}"
    )


# ---------------------------------------------------------------------------
# 3. xoxd- Slack Cookie Session Token (companion to xoxc-)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_slack_xoxd_cookie_session_token(
    tmp_path: Path,
) -> None:
    """Bare ``xoxd-<body>`` cookie session token (the ``d`` cookie
    value from Slack web sessions, paired with xoxc-). A leak grants
    SESSION-LEVEL access equivalent to xoxc-; both tokens typically
    leak together via DevTools cookie extraction."""
    file_path = tmp_path / "cookie_session.py"
    secret = "xoxd-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    file_path.write_text(
        f'SLACK_D_COOKIE = "{secret}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect xoxd- cookie session token"
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "Slack Cookie Session Token gefunden" in reasons, (
        f"Expected Slack cookie-session-specific attribution; "
        f"got reasons: {reasons}"
    )


# ---------------------------------------------------------------------------
# 4. Plaintext-context detection (no sensitive variable name)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_xoxe_in_plain_log_line(tmp_path: Path) -> None:
    """xoxe- token in a plain log line — no sensitive variable name
    to anchor the generic assignment heuristic. The new detector
    MUST fire on the bare token regardless of surrounding context."""
    file_path = tmp_path / "debug.log"
    secret = "xoxe-1-" + "1A" * 15
    file_path.write_text(
        f"2026-05-16T10:00:00Z DEBUG oauth.refresh issued new token {secret}\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Token Rotation Refresh Token gefunden" in reasons, (
        f"xoxe- token in plain log line MUST be detected; got: {reasons}"
    )


def test_secret_scanner_detects_xoxc_in_json_without_sensitive_key(
    tmp_path: Path,
) -> None:
    """xoxc- token embedded in JSON fixture without sensitive key
    (the JSON key is generic — ``token`` is sensitive but ``value``
    is not). The new detector MUST fire on the bare token."""
    file_path = tmp_path / "session_fixture.json"
    secret = "xoxc-1234567890-1234567890-AAAAAAAAAAAAAAAAAAAA"
    file_path.write_text(
        f'{{"value": "{secret}", "issued_at": "2026-05-16T10:00:00Z"}}\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Browser Session Token gefunden" in reasons, (
        f"xoxc- in non-sensitive JSON key MUST be detected; got: {reasons}"
    )


# ---------------------------------------------------------------------------
# 5. Negative cases — no false positives
# ---------------------------------------------------------------------------


def test_xoxe_pattern_does_not_flag_short_prefix(tmp_path: Path) -> None:
    """Short ``xoxe-`` strings (< 20 char body) MUST NOT match.
    Anchors against operator-named placeholders / accidentally
    truncated tokens that share the prefix."""
    file_path = tmp_path / "config.py"
    not_a_token = "xoxe-1-short"  # body too short
    file_path.write_text(f'placeholder = "{not_a_token}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Token Rotation Refresh Token gefunden" not in reasons


def test_xoxc_pattern_does_not_flag_mid_word(tmp_path: Path) -> None:
    """``xoxc-`` appearing mid-word (preceded by an alphanumeric
    character) MUST NOT match. The lookbehind anchor enforces token
    boundary."""
    file_path = tmp_path / "config.py"
    not_a_token = "prefixxoxc-1234567890-1234567890-AAAAAAAAAAAAAAAAAAAA"
    file_path.write_text(f'value = "{not_a_token}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Browser Session Token gefunden" not in reasons


def test_xoxd_pattern_does_not_flag_short_body(tmp_path: Path) -> None:
    """``xoxd-`` with < 20 char body MUST NOT match."""
    file_path = tmp_path / "config.py"
    not_a_token = "xoxd-shortbody"  # < 20 chars after prefix
    file_path.write_text(f'value = "{not_a_token}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Cookie Session Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 6. Regression guards — existing Slack tokens still detected with
#    canonical attribution (no collision with new patterns).
# ---------------------------------------------------------------------------


def test_existing_xoxb_strict_still_detected(tmp_path: Path) -> None:
    """Regression guard: existing ``xoxb-`` strict-format bot tokens
    continue to receive the canonical ``Slack Bot Token gefunden``
    attribution. The new xoxe- patterns must not steal the xoxb-
    attribution for strict-format tokens that don't have the xoxe.
    prefix."""
    file_path = tmp_path / "bot.py"
    secret = "xoxb-1234567890-1234567890-" + "A" * 24
    file_path.write_text(f'BOT = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Bot Token gefunden" in reasons, (
        f"Bare xoxb- must keep its canonical attribution; got: {reasons}"
    )


def test_existing_xoxp_strict_still_detected(tmp_path: Path) -> None:
    """Regression guard: existing ``xoxp-`` user tokens keep canonical
    attribution."""
    file_path = tmp_path / "user.py"
    secret = "xoxp-1234567890-1234567890-1234567890-" + "A" * 32
    file_path.write_text(f'USER = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack User Token gefunden" in reasons


def test_existing_xoxa_still_detected(tmp_path: Path) -> None:
    """Regression guard: existing ``xoxa-`` OAuth tokens keep canonical
    attribution. The xoxe- entry placed BEFORE xoxa- must not absorb
    xoxa- via accidental overlap (xoxe- and xoxa- have different
    prefix prefixes; no overlap is possible)."""
    file_path = tmp_path / "oauth.py"
    secret = "xoxa-1234567890-abcdef-ABCDEFGH"
    file_path.write_text(f'OAUTH = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack OAuth Access Token gefunden" in reasons


def test_existing_xoxr_still_detected(tmp_path: Path) -> None:
    """Regression guard: existing ``xoxr-`` legacy refresh tokens keep
    canonical attribution."""
    file_path = tmp_path / "refresh_legacy.py"
    secret = "xoxr-1234567890-abcdef-ABCDEFGH"
    file_path.write_text(f'LEGACY_REFRESH = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Refresh Token gefunden" in reasons


# ---------------------------------------------------------------------------
# 7. Cross-prefix mutual exclusion — all seven Slack prefixes coexist.
# ---------------------------------------------------------------------------


def test_all_seven_slack_prefixes_distinct_findings(tmp_path: Path) -> None:
    """Every prefix in the now-complete Slack family produces a
    distinct finding with the correct issuer attribution. The seven
    canonical Slack prefixes:

      * xoxb- → Slack Bot Token (strict)
      * xoxp- → Slack User Token (strict)
      * xoxa- → Slack OAuth Access Token (permissive)
      * xoxr- → Slack Refresh Token (permissive, legacy)
      * xoxe- → Slack Token Rotation Refresh Token (THIS ROUND)
      * xoxc- → Slack Browser Session Token (THIS ROUND)
      * xoxd- → Slack Cookie Session Token (THIS ROUND)
    """
    file_path = tmp_path / "slack_all.py"
    bot = "xoxb-1234567890-1234567890-" + "A" * 24
    user = "xoxp-1234567890-1234567890-1234567890-" + "B" * 32
    oauth = "xoxa-1234567890-abcdef-ABCDEFGHIJKL"
    legacy_refresh = "xoxr-1234567890-abcdef-ABCDEFGHIJKL"
    rotation_refresh = "xoxe-1-" + "1A" * 15
    browser = "xoxc-1234567890-1234567890-AAAAAAAAAAAAAAAAAAAA"
    cookie = "xoxd-" + "B" * 30
    file_path.write_text(
        "\n".join(
            [
                f'BOT = "{bot}"',
                f'USER = "{user}"',
                f'OAUTH = "{oauth}"',
                f'LEGACY = "{legacy_refresh}"',
                f'ROTATION = "{rotation_refresh}"',
                f'BROWSER = "{browser}"',
                f'COOKIE = "{cookie}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = {f.reason for f in findings}

    assert "Slack Bot Token gefunden" in reasons
    assert "Slack User Token gefunden" in reasons
    assert "Slack OAuth Access Token gefunden" in reasons
    assert "Slack Refresh Token gefunden" in reasons
    assert "Slack Token Rotation Refresh Token gefunden" in reasons
    assert "Slack Browser Session Token gefunden" in reasons
    assert "Slack Cookie Session Token gefunden" in reasons


# ---------------------------------------------------------------------------
# 8. Cross-vendor boundary — no collision with other token families.
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
def test_cross_vendor_no_collision_with_slack_workflow_tokens(
    tmp_path: Path, vendor_token: str, vendor_reason: str
) -> None:
    """Cross-vendor regression: other vendors' tokens are not mis-
    attributed to the new Slack workflow patterns. The Slack workflow
    detectors anchor on the xox[ecd] prefix family which is unique
    to Slack — no collision possible at the prefix level."""
    file_path = tmp_path / "cross_vendor.py"
    file_path.write_text(
        f'OTHER_VENDOR = "{vendor_token}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert vendor_reason in reasons, (
        f"Cross-vendor token {vendor_token[:20]}... must keep its "
        f"canonical attribution; got: {reasons}"
    )
    # No false-positive Slack attribution.
    assert "Slack Token Rotation Refresh Token gefunden" not in reasons
    assert "Slack Browser Session Token gefunden" not in reasons
    assert "Slack Cookie Session Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 9. Inventory invariants — source-grep enforces presence of the three
#    new patterns. A future regression that drops a pattern fails this
#    test until the canonical detection is restored.
# ---------------------------------------------------------------------------


def test_secret_scanner_module_contains_xoxe_known_token_entry() -> None:
    """Inventory pin: ``src/utils/secret_scanner.py`` must contain a
    ``_KNOWN_TOKENS`` entry that anchors the ``xoxe-`` prefix family
    (direct AND chained shapes)."""
    from src.utils import secret_scanner

    source = Path(secret_scanner.__file__).read_text(encoding="utf-8")
    assert "xoxe" in source, (
        "secret_scanner.py must contain an xoxe- detection pattern "
        "(Slack V2 token rotation refresh token)"
    )
    assert "Slack Token Rotation Refresh Token" in source, (
        "secret_scanner.py must use canonical 'Slack Token Rotation "
        "Refresh Token gefunden' attribution"
    )


def test_secret_scanner_module_contains_xoxc_known_token_entry() -> None:
    """Inventory pin: ``xoxc-`` browser session token detector present."""
    from src.utils import secret_scanner

    source = Path(secret_scanner.__file__).read_text(encoding="utf-8")
    assert "xoxc-" in source, (
        "secret_scanner.py must contain an xoxc- detection pattern "
        "(Slack browser session token)"
    )
    assert "Slack Browser Session Token" in source


def test_secret_scanner_module_contains_xoxd_known_token_entry() -> None:
    """Inventory pin: ``xoxd-`` cookie session token detector present."""
    from src.utils import secret_scanner

    source = Path(secret_scanner.__file__).read_text(encoding="utf-8")
    assert "xoxd-" in source, (
        "secret_scanner.py must contain an xoxd- detection pattern "
        "(Slack cookie session token)"
    )
    assert "Slack Cookie Session Token" in source


# ---------------------------------------------------------------------------
# 10. Masking contract — raw token must never leak unredacted into the
#     findings list (the report is rendered into operator-facing logs
#     and the GitHub issue body, which can mirror the source unmasked
#     into another committed artefact).
# ---------------------------------------------------------------------------


def test_xoxe_token_masked_in_findings(tmp_path: Path) -> None:
    """The xoxe- raw token value MUST be masked (``xoxe***...``) in
    every finding emitted; the raw value must never appear unredacted
    in ``Finding.match`` so the report renderer can safely include
    findings in operator-facing artefacts."""
    file_path = tmp_path / "secret.py"
    secret = "xoxe-1-" + "1A" * 20
    file_path.write_text(f'TOKEN = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    for f in findings:
        assert secret not in f.match, (
            f"Raw token leaked into finding: {f.match!r}"
        )


def test_xoxc_token_masked_in_findings(tmp_path: Path) -> None:
    """xoxc- raw value masked in findings."""
    file_path = tmp_path / "secret.py"
    secret = "xoxc-1234567890-1234567890-AAAAAAAAAAAAAAAAAAAA"
    file_path.write_text(f'TOKEN = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    for f in findings:
        assert secret not in f.match


def test_xoxd_token_masked_in_findings(tmp_path: Path) -> None:
    """xoxd- raw value masked in findings."""
    file_path = tmp_path / "secret.py"
    secret = "xoxd-" + "C" * 32
    file_path.write_text(f'TOKEN = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    for f in findings:
        assert secret not in f.match


# ---------------------------------------------------------------------------
# 11. Silent-undetection branch: uniform-character-class body. Mirrors
#     the Bearer case-sensitivity drift (PR #c074c89, 2026-05-15) and
#     the WooCommerce ck_/cs_ silent-undetection branch (Round 13).
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_xoxe_uniform_body(tmp_path: Path) -> None:
    """Slack rotation refresh token with all-uppercase body
    (poorly-seeded RNG / hash-derived test fixture / hand-typed
    placeholder). Pre-fix the entropy fallback's
    ``min_categories=2`` requirement rejects all-uppercase bodies
    in non-assignment context — the credential is silently
    undetected. Post-fix the specific xoxe- detector fires on the
    prefix regardless of body character distribution."""
    file_path = tmp_path / "uniform_body.log"
    secret = "xoxe-" + "A" * 30  # all uppercase, 30-char body
    # No sensitive variable name — bare log line.
    file_path.write_text(
        f"audit-log: refresh issued {secret}\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Slack Token Rotation Refresh Token gefunden" in reasons, (
        f"xoxe- with uniform uppercase body MUST be detected (pre-fix "
        f"this is silently undetected via entropy fallback's "
        f"min_categories=2 rejection). Got reasons: {reasons}"
    )
