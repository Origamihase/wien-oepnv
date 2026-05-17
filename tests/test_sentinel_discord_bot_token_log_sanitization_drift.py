"""Sentinel drift coverage for Discord Bot Token value-shape
log-sanitisation across ``sanitize_log_message`` and the downstream
``_sanitize_exception_msg`` chain.

The 2026-05-08 Round-3 ``Discord Bot Token`` secret-scanner round (see
``.jules/sentinel.md``) extended ``_KNOWN_TOKENS`` in
``src/utils/secret_scanner.py`` to detect Discord bot tokens via the
canonical structural shape ``<base64url(snowflake-id)>.<base64url(timestamp)>
.<HMAC>`` (regex
``[MNO][A-Za-z0-9_\\-]{22,27}\\.[A-Za-z0-9_\\-]{6,7}\\.[A-Za-z0-9_\\-]{27,}``).
Successive multi-vendor log-sanitisation rounds (2026-05-17 GitHub /
Multi-Vendor / Slack-AIML / CICD-DevOps / SaaS-Comms-Secret-Manager /
Supply-Chain-Ecommerce-PaaS rounds) explicitly NAMED Discord as a
deferred candidate but never closed the sibling-drift.

This round closes the operator-log leak sink for the Discord bot token
family. Pre-fix EVERY bare Discord bot token shape in
``sanitize_log_message`` and the downstream ``_sanitize_exception_msg``
chain bypassed every existing key/header/URL-credential mask pattern
and leaked verbatim across four leak surfaces:

1. **Plain application f-string logs** — ``log.error(f"Discord error:
   {token}")``. The bare token shape lands in operator log streams
   verbatim.
2. **Upstream error responses** — ``log.warning(f"Provider error:
   {response.text}")`` where a misconfigured / compromised upstream
   echoes the supplied token back in its error payload.
3. **JSON values without sensitive key names** — ``{"data":
   "M<24>.<6>.<27>"}`` / ``{"payload": "N<24>.<6>.<27>"}``. The
   JSON-key sensitive-name regex (``[a-z0-9_.\\-]*token`` etc.) misses
   keys like ``data`` / ``payload`` / ``response_body`` / ``message``
   so the token value leaks unredacted.
4. **URL paths embedding the token** — ``GET /api/v9/users/{token}/me
   HTTP/1.1``. The Basic-Auth-in-URL regex requires the credential to
   appear before ``@``; path-embedded tokens slip past entirely.

Threat model (mirror the 2026-05-08 secret-scanner round's analysis):

* A leaked Discord bot token grants FULL bot privileges in every
  guild the bot is invited to — read/write all visible messages,
  kick/ban users, edit channels and roles, run any registered slash
  commands, and (with appropriate scopes) read voice/DM history.
* The structural shape is three dot-separated base64url segments
  (identical to JWT but with a snowflake-ID-based first segment
  instead of the JOSE ``eyJ`` header). The dots are OUTSIDE the
  entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet, so without a
  specific pattern only ONE segment is matched at a time, losing
  both the issuer attribution AND the full credential span.
* The Discord disambiguator from JWT is at the leading-character
  level: Discord stringifies the user ID (decimal digits) before
  base64-encoding, so the first segment ALWAYS starts with
  ``[MNO]`` (decimal ``1``-``3`` → ``M``, ``4``-``7`` → ``N``,
  ``8``-``9`` → ``O``); JWTs ALWAYS start with ``eyJ`` (base64
  encoding of ``{"``). The two leading-character classes are disjoint,
  so no token can match both patterns.
* Revocation flow lives at https://discord.com/developers/applications/
  Developer Portal — distinct from any other vendor's, so
  issuer-specific attribution accelerates IR triage.

**Fix:** append a sibling pattern to ``sanitize_log_message``'s
pattern list mirroring the scanner regex structural anchors exactly:
``(?<![A-Za-z0-9])([MNO])[A-Za-z0-9_\\-]{22,27}\\.[A-Za-z0-9_\\-]{6,7}
\\.[A-Za-z0-9_\\-]{27,}(?![A-Za-z0-9])``. The mask preserves the
leading ``[MNO]`` Discord-shape disambiguator and the three-segment
structure ``M***.***.***`` for incident-response triage while
suppressing every credential body span. Placed AFTER the JWT pattern
to mirror the scanner's ordering (Discord follows JWT in
``_KNOWN_TOKENS``).

Structural anchors mirror the scanner regex exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``obj.M<body>``, ``XM<body>`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict ``[MNO]`` leading-character class enforces the snowflake-ID
  leading-digit constraint AND the disjoint-from-JWT mutual exclusion.
* Strict per-segment body length floors (22-27 / 6-7 / 27+ chars)
  reject accidental fragments while accepting every real-shape token.

Idempotence: the masked form ``M***.***.***`` does NOT match the
regex because ``*`` is not in the body alphabet ``[A-Za-z0-9_\\-]``
AND the masked segment length (3 chars) is below every per-segment
floor (22/6/27).

Marker: SENTINEL_DISCORD_BOT_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_DISCORD_BOT_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_DISCORD_BOT_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for the Discord bot token "
    "shape ([MNO]<24-28>.<6-7>.<27+>) that the 2026-05-08 secret-scanner "
    "Round-3 added to _KNOWN_TOKENS. Bare Discord tokens in plain log text, "
    "JSON values with non-sensitive keys, URL paths, and exception messages "
    "slipped past all key/header/URL-credential masking patterns and leaked "
    "verbatim into operator log streams and the public docs/feed_health.json "
    "artefact."
)


def _body_extended(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    base64url ``[A-Za-z0-9_-]`` alphabet — exercises the full character
    class so a partial-class regex bug cannot pass."""
    chunk = "Aa1B-c_D"  # 8-char cycle covering upper/lower/digit/dash/underscore
    return (chunk * (length // len(chunk) + 1))[:length]


# Discord bot token fixture, one per snowflake-ID leading character.
# First segment 24 chars (real-world: 24-28), middle 6 chars (4-byte
# timestamp), third 30 chars (HMAC-SHA256 truncation, real-world 27+).
_DISCORD_M = "M" + _body_extended(24) + "." + _body_extended(6) + "." + _body_extended(30)
_DISCORD_N = "N" + _body_extended(24) + "." + _body_extended(6) + "." + _body_extended(30)
_DISCORD_O = "O" + _body_extended(24) + "." + _body_extended(6) + "." + _body_extended(30)


# Sanity check the fixtures.
for tok, lead in ((_DISCORD_M, "M"), (_DISCORD_N, "N"), (_DISCORD_O, "O")):
    assert tok.startswith(lead)
    seg1, seg2, seg3 = tok.split(".")
    assert 22 <= len(seg1) - 1 <= 27, f"first segment body len out of range: {len(seg1) - 1}"
    assert 6 <= len(seg2) <= 7, f"second segment len out of range: {len(seg2)}"
    assert len(seg3) >= 27, f"third segment too short: {len(seg3)}"


_ALL_DISCORD_TOKENS = [
    (_DISCORD_M, "M"),
    (_DISCORD_N, "N"),
    (_DISCORD_O, "O"),
]


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner DOES detect Discord bot tokens.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,_lead", _ALL_DISCORD_TOKENS)
def test_drift_premise_scanner_detects_discord_token(token: str, _lead: str) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST detect each Discord bot
    token shape this round masks — that asymmetry IS the drift this
    round closes. If the scanner ever drops the Discord prefix this test
    FAILS first (loud) — preventing silent drift in the opposite
    direction."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(token)
    ]
    assert any("Discord" in r for r in matched_reasons), (
        f"Drift premise FAILED: Discord bot token {token[:20]!r}... is no "
        f"longer detected by _KNOWN_TOKENS — matched reasons: "
        f"{matched_reasons}. This test must be updated if the scanner drops "
        f"the Discord pattern."
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare Discord bot token in application f-string logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,lead", _ALL_DISCORD_TOKENS)
def test_discord_token_in_plain_log_line_is_masked(token: str, lead: str) -> None:
    """Bare Discord bot token in plain log text MUST be masked by
    ``sanitize_log_message`` — pre-fix this leaked verbatim through the
    operator-log sink and the public ``docs/feed_health.json`` artefact."""
    log_line = f"Discord API returned 401: invalid bot token {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Discord bot token (lead '{lead}') leaked through "
        f"sanitize_log_message: "
        f"{SENTINEL_DISCORD_BOT_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert f"{lead}***.***.***" in result, (
        f"Discord bot token mask MUST preserve the leading-char "
        f"disambiguator '{lead}***' and the 3-segment shape "
        f"'***.***.***' for incident-response triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak vector via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,lead", _ALL_DISCORD_TOKENS)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_discord_token_in_json_value_is_masked(
    token: str, lead: str, key_name: str
) -> None:
    """Discord bot token in JSON value with a NON-sensitive key name
    MUST be masked — pre-fix the JSON-key sensitive-name regex missed
    keys like ``data`` / ``payload`` / ``response_body`` / ``message``
    and the token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Discord bot token (lead '{lead}') in JSON value with "
        f"non-sensitive key '{key_name}' leaked through "
        f"sanitize_log_message"
    )
    assert f"{lead}***.***.***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token — non-``user:pass@`` form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,lead", _ALL_DISCORD_TOKENS)
def test_discord_token_in_url_path_is_masked(token: str, lead: str) -> None:
    """Discord bot token embedded in URL path (NOT ``user:pass@`` form)
    MUST be masked — pre-fix the URL credential regex required the
    credential to appear before ``@``; path-embedded tokens slipped
    past."""
    log_line = f"GET /api/v9/users/{token}/me HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{lead}***.***.***" in result


@pytest.mark.parametrize("token,lead", _ALL_DISCORD_TOKENS)
def test_discord_token_in_url_query_with_non_sensitive_param_is_masked(
    token: str, lead: str
) -> None:
    """Discord bot token in URL query string with a NON-sensitive
    parameter name (``ref`` / ``commit_sha`` / ``q``) MUST be masked —
    pre-fix the URL credential regex required the credential to appear
    before ``@``; query-string tokens with non-sensitive parameter names
    slipped past."""
    log_line = f"GET /api/foo/bar?ref={token} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{lead}***.***.***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,lead", _ALL_DISCORD_TOKENS)
def test_discord_token_through_sanitize_exception_msg(
    token: str, lead: str
) -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask Discord bot tokens.
    ``_sanitize_exception_msg`` extracts HTTP URLs via a pre-regex and
    falls back to ``sanitize_log_message`` for the non-HTTP-URL
    remainder."""
    exc_msg = f"HTTPError: 401 Unauthorized — bot token {token} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"{lead}***.***.***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_discord_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args.
    Discord bot tokens in string args MUST be masked."""
    arg = f"audit: {_DISCORD_M}"
    result = sanitize_log_arg(arg)
    assert _DISCORD_M not in result
    assert "M***.***.***" in result


def test_sanitize_log_arg_masks_discord_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation.
    A custom object whose ``__str__`` contains a Discord bot token MUST
    have the token masked. Uses a NON-sensitive attribute name
    (``audit``) so the value-shape mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_DISCORD_O})"

    result = sanitize_log_arg(_Wrapper())
    assert _DISCORD_O not in result, (
        "Discord bot token leaked through sanitize_log_arg"
    )
    assert "O***.***.***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Short first segment (< 22 chars body) — below floor
        "MAa1B-c_DAa1B-c_D.AAAAAA.AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        # Short middle segment (< 6 chars)
        "MAa1B-c_DAa1B-c_DAa1B-c_D.AAAAA.AAAAAAAAAAAAAAAAAAAAAAAAAAA",
        # Short third segment (< 27 chars)
        "MAa1B-c_DAa1B-c_DAa1B-c_D.AAAAAA.AAAAAAAAAAAAAAAAAAAAAAAAAA",
        # Wrong leading char (not [MNO])
        "PAa1B-c_DAa1B-c_DAa1B-c_D.AAAAAA.AAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "XAa1B-c_DAa1B-c_DAa1B-c_D.AAAAAA.AAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "AAa1B-c_DAa1B-c_DAa1B-c_D.AAAAAA.AAAAAAAAAAAAAAAAAAAAAAAAAAA",
        # Mid-identifier collision — lookbehind prevents this
        "XM" + "A" * 24 + "." + "A" * 6 + "." + "A" * 30,
        "0M" + "A" * 24 + "." + "A" * 6 + "." + "A" * 30,
    ],
)
def test_benign_discord_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong leading char / mid-identifier
    collisions MUST NOT be masked. The ``(?<![A-Za-z0-9])`` lookbehind
    plus the ``[MNO]`` leading-char constraint plus the per-segment
    length floors are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive Discord token mask on benign input: "
        f"{benign!r} → {result!r}"
    )


# ---------------------------------------------------------------------------
# (7) JWT cross-mutex — JWT tokens MUST NOT be misattributed as Discord
# ---------------------------------------------------------------------------


def test_jwt_is_masked_with_jwt_prefix_not_discord_prefix() -> None:
    """**Cross-mutex invariant.** JWT tokens start with ``eyJ`` and MUST
    be masked as ``eyJ***`` — NOT as a Discord-shape mask. The Discord
    leading-char class ``[MNO]`` is disjoint from ``e`` so no
    misattribution is structurally possible. Pin the property
    programmatically."""
    jwt = "eyJ" + _body_extended(28) + "." + _body_extended(40) + "." + _body_extended(43)
    log_line = f"auth header: Bearer {jwt}"
    result = sanitize_log_message(log_line)
    assert jwt not in result, "JWT must be masked"
    assert "eyJ***" in result, (
        "JWT must mask as 'eyJ***' (JWT-specific attribution), not as a "
        "Discord-shape mask"
    )
    # Specifically: result must NOT contain a Discord-shape mask pattern
    for lead in ("M***.***.***", "N***.***.***", "O***.***.***"):
        assert lead not in result, (
            f"JWT misattributed as Discord-shape mask '{lead}' — "
            f"cross-mutex broken"
        )


def test_discord_is_masked_with_discord_prefix_not_jwt_prefix() -> None:
    """**Cross-mutex invariant.** Discord tokens start with ``[MNO]``
    and MUST be masked with the Discord-shape mask — NOT as ``eyJ***``.
    The JWT leading-char string ``eyJ`` is disjoint from ``[MNO]`` so
    no misattribution is structurally possible."""
    log_line = f"bot login: {_DISCORD_M}"
    result = sanitize_log_message(log_line)
    assert _DISCORD_M not in result, "Discord token must be masked"
    assert "M***.***.***" in result, (
        "Discord token must mask with the Discord-shape mask 'M***.***.***' "
        "(Discord-specific attribution), not as 'eyJ***'"
    )
    assert "eyJ***" not in result, (
        "Discord misattributed as JWT mask 'eyJ***' — cross-mutex broken"
    )


# ---------------------------------------------------------------------------
# (8) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "M***.***.***",
        "N***.***.***",
        "O***.***.***",
    ],
)
def test_discord_mask_is_idempotent(masked: str) -> None:
    """Running ``sanitize_log_message`` twice MUST be idempotent — the
    masked form (``[MNO]***.***.***``) MUST NOT itself match the
    Discord regex. The ``*`` char is outside the body alphabet
    ``[A-Za-z0-9_\\-]`` AND the masked segment length (3 chars) is
    below the per-segment floors (22/6/27)."""
    log_line = f"prior IR note: token redacted as {masked}"
    result = sanitize_log_message(log_line)
    assert masked in result, (
        f"Idempotence broken: masked form {masked!r} was further "
        f"modified by sanitize_log_message: {result!r}"
    )


def test_discord_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_DISCORD_N}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _DISCORD_N not in first


# ---------------------------------------------------------------------------
# (9) Sibling-alignment invariant — log mask covers the scanner family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_discord_token_family() -> None:
    """**Sibling-alignment invariant.** The Discord bot token shape that
    appears in ``_KNOWN_TOKENS`` MUST have a matching mask in
    ``sanitize_log_message``. Any future Discord-family pattern
    adjustment to the scanner without a companion log-mask adjustment
    fails this test on the first pytest run after the new scanner
    entry is committed — surfacing the next drift family
    programmatically."""
    # Confirm the scanner has exactly one Discord entry.
    discord_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Discord" in reason
    ]
    assert len(discord_entries) == 1, (
        f"Scanner Discord family drift: expected exactly 1 Discord regex in "
        f"_KNOWN_TOKENS, found {len(discord_entries)}. If a new Discord "
        f"variant was added/removed, update sanitize_log_message AND this "
        f"test."
    )
    # Confirm the scanner regex shape matches the expected leading-char
    # constraint.
    pattern = discord_entries[0]
    assert "[MNO]" in pattern, (
        f"Scanner Discord regex no longer uses the '[MNO]' leading-char "
        f"disambiguator: {pattern!r}. Update sanitize_log_message + this "
        f"test in lockstep."
    )

    # Confirm sanitize_log_message masks each leading character.
    for lead in ("M", "N", "O"):
        token = lead + _body_extended(24) + "." + _body_extended(6) + "." + _body_extended(30)
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"Discord bot token (lead '{lead}') missing from "
            f"sanitize_log_message mask family — log-sanitisation drift "
            f"vs. scanner _KNOWN_TOKENS"
        )
        assert f"{lead}***.***.***" in result, (
            f"Discord bot token (lead '{lead}') mask MUST preserve Discord "
            f"attribution as '{lead}***.***.***' for incident-response triage"
        )
