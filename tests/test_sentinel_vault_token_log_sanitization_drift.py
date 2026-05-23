"""Sentinel drift coverage for HashiCorp Vault token-family value-shape
log-sanitisation across ``sanitize_log_message`` and the downstream
``_sanitize_exception_msg`` chain.

The 2026-05-17 ``hvb.`` / ``hvr.`` Vault Token Family Drift Closure round
 extended ``_KNOWN_TOKENS`` in
``src/utils/secret_scanner.py`` to detect HashiCorp Vault Service / Batch /
Recovery tokens (``hvs.`` / ``hvb.`` / ``hvr.`` prefixes with 30+ char
base64url bodies). The companion log-sanitisation codepath
(``src/utils/logging.py:sanitize_log_message``) was NOT extended in the
same round — preserving the canonical sibling-drift pattern where the
source-of-truth committed-credential leak gets caught by the scanner but
the operator-log / public ``docs/feed_health.json`` artefact sink leaks
the token verbatim.

Pre-fix detection gaps (mirror the 2026-05-16 Database / JDBC / LDAP /
SSH Credential Log-Sanitisation Drift round's structural analysis):

1. ``sanitize_log_message`` masks credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare Vault
   token in plain log text bypasses every existing pattern. Three leak
   surfaces:

   * **Plain application f-string logs** — ``log.error(f"Vault read
     failed with token {token}: {exc}")``. The bare token shape lands
     in operator log streams verbatim.
   * **Upstream error responses** — ``log.warning(f"Provider error:
     {response.text}")`` where a misconfigured / compromised upstream
     echoes the supplied token back in its error payload.
   * **JSON values without sensitive key names** — ``{"data":
     "hvs.AAAA..."}`` or ``{"payload": "hvr.CCC..."}``. The JSON-key
     sensitive-name regex (``[a-z0-9_.\\-]*token`` etc.) misses keys
     like ``data`` / ``payload`` / ``response_body`` so the token
     value leaks unredacted.
   * **URL paths embedding the token** — ``GET /v1/auth/hvs.AAA.../
     lookup``. The ``user:pass@`` URL regex requires the credential
     to appear before ``@``; path-embedded tokens slip past.

2. Threat model: mirror the secret-scanner round. A Vault token leaked
   into the operator log stream (Slack escalation, GitHub Issue body
   submitted by ``submit_auto_issue``, ``docs/feed_health.json`` public
   artefact, SIEM aggregator pipeline) grants the same Vault-cluster
   scope per privilege tier as the committed-source leak that the
   scanner already protects against:

   * ``hvs.`` — Service Token (persistent, full policy scope).
   * ``hvb.`` — Batch Token (ephemeral, full policy scope for TTL).
   * ``hvr.`` — Recovery Token (root-equivalent on sealed Vault;
     mint new root token via ``POST /v1/sys/generate-root`` once
     unsealed → persistent backdoor with full administrative scope).

The log-sanitisation drift closes the second leak sink with the same
family scope as the scanner round.

**Fix:** append a sibling pattern to ``sanitize_log_message``'s pattern
list mirroring the scanner regex structural anchors
(``(?<![A-Za-z0-9])(hvs|hvb|hvr)\\.[A-Za-z0-9_\\-]{30,}(?![A-Za-z0-9])``)
that masks the body span while preserving the issuer-specific prefix
for incident-response triage. The 30+ char body floor rejects
accidental fragments (attribute-access chains like ``obj.hvs.foo``,
filesystem paths, mid-identifier collisions) while accepting the
canonical 90-110 char Vault token shape.

Marker: SENTINEL_VAULT_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_VAULT_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_VAULT_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for the HashiCorp Vault "
    "token family (hvs./hvb./hvr.) that the 2026-05-17 secret-scanner "
    "round added to _KNOWN_TOKENS. Bare Vault tokens in plain log text, "
    "JSON values with non-sensitive keys, and URL paths slipped past all "
    "key/header/URL-credential masking patterns and leaked verbatim into "
    "operator log streams and the public docs/feed_health.json artefact."
)

# Real-shape Vault token bodies are 90-110 chars base64url. Use 90 char
# bodies as canonical realistic shape; each prefix gets a distinctive
# body so a per-case failure is unambiguous in the test report.
_HVS_BODY = "Aa1_B-c" + "x" * 83  # 90 chars total, mixed alphabet
_HVB_BODY = "Bb2_C-d" + "y" * 83  # 90 chars total
_HVR_BODY = "Cc3_D-e" + "z" * 83  # 90 chars total

_HVS_TOKEN = f"hvs.{_HVS_BODY}"
_HVB_TOKEN = f"hvb.{_HVB_BODY}"
_HVR_TOKEN = f"hvr.{_HVR_BODY}"


# ---------------------------------------------------------------------------
# (1) Plain log line — bare Vault token in application f-string logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_HVS_TOKEN, "hvs"),
        (_HVB_TOKEN, "hvb"),
        (_HVR_TOKEN, "hvr"),
    ],
)
def test_vault_token_in_plain_log_line_is_masked(token: str, prefix: str) -> None:
    """Bare Vault token in plain log text MUST be masked by
    ``sanitize_log_message`` — pre-fix this leaked verbatim through
    the operator-log sink."""
    log_line = f"Vault returned 401: invalid token {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Vault {prefix.upper()} token leaked through sanitize_log_message: "
        f"{SENTINEL_VAULT_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert f"{prefix}.***" in result, (
        f"Vault {prefix.upper()} token mask MUST preserve the "
        f"'{prefix}.' prefix for incident-response triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak vector via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_HVS_TOKEN, "hvs"),
        (_HVB_TOKEN, "hvb"),
        (_HVR_TOKEN, "hvr"),
    ],
)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_vault_token_in_json_value_is_masked(
    token: str, prefix: str, key_name: str
) -> None:
    """Vault token in JSON value with a NON-sensitive key name MUST
    be masked — pre-fix the JSON-key sensitive-name regex missed keys
    like ``data`` / ``payload`` / ``response_body`` and the token
    value leaked verbatim."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Vault {prefix.upper()} token in JSON value with non-sensitive "
        f"key '{key_name}' leaked through sanitize_log_message"
    )
    assert f"{prefix}.***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token — non-``user:pass@`` form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_HVS_TOKEN, "hvs"),
        (_HVB_TOKEN, "hvb"),
        (_HVR_TOKEN, "hvr"),
    ],
)
def test_vault_token_in_url_path_is_masked(token: str, prefix: str) -> None:
    """Vault token embedded in URL path (NOT ``user:pass@`` form) MUST
    be masked — pre-fix the URL credential regex required the credential
    to appear before ``@``; path-embedded tokens slipped past."""
    log_line = f"GET /v1/auth/{token}/lookup 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{prefix}.***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg — exception text from provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_HVS_TOKEN, "hvs"),
        (_HVB_TOKEN, "hvb"),
        (_HVR_TOKEN, "hvr"),
    ],
)
def test_vault_token_through_sanitize_exception_msg(token: str, prefix: str) -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask Vault tokens. ``_sanitize_exception_msg``
    extracts HTTP URLs via a pre-regex and falls back to
    ``sanitize_log_message`` for the remainder; fixing the latter
    closes the exception-text leak sink."""
    exc_msg = f"VaultError: 401 Unauthorized — token {token} is invalid"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"{prefix}.***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_vault_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args.
    Vault tokens in string args MUST be masked."""
    arg = f"audit: {_HVS_TOKEN}"
    result = sanitize_log_arg(arg)
    assert _HVS_TOKEN not in result
    assert "hvs.***" in result


def test_sanitize_log_arg_masks_vault_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation.
    A custom object whose ``__str__`` contains a Vault token MUST have
    the token masked (either by the existing ``token=`` query-param mask
    or by the new value-shape mask — both produce a leak-free output)."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_HVR_TOKEN})"

    result = sanitize_log_arg(_Wrapper())
    assert _HVR_TOKEN not in result, "Vault recovery token leaked through sanitize_log_arg"
    assert "hvr.***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Attribute-access chains — common Python idiom
        "obj.hvs.foo",
        "self.hvb.bar = 1",
        "config.hvr.timeout",
        # Short fragments (3-char body, well below 30-char floor)
        "hvs.abc and hvb.xyz",
        "hvr.x",
        # Mid-identifier collisions (lookbehind prevents these)
        "xhvs.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "Yhvb.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ],
)
def test_benign_vault_prefix_is_not_masked(benign: str) -> None:
    """Negative case: short prefixes / attribute-access chains /
    mid-identifier collisions MUST NOT be masked. The ``(?<![A-Za-z0-9])``
    lookbehind plus the 30+ char body floor are the structural
    disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive Vault token mask on benign input: {benign!r} → {result!r}"
    )


def test_short_vault_token_is_not_masked() -> None:
    """Negative case: body shorter than 30 chars is below the structural
    floor and MUST NOT be masked (rejects ``s.foo``-style false positives
    that the 30+ char floor disambiguates against)."""
    short = "hvs." + "a" * 29  # 29 chars body, below floor
    result = sanitize_log_message(short)
    assert result == short


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


def test_vault_token_mask_is_idempotent() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output. The mask token ``hvs.***`` MUST NOT be
    re-matched as a Vault token (``***`` is not in the body alphabet
    AND is below the 30-char floor)."""
    log_line = f"Failed: {_HVS_TOKEN}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _HVS_TOKEN not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — log mask covers every scanner prefix
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_vault_token_family() -> None:
    """**Sibling-alignment invariant.** Every Vault token prefix that
    appears in ``_KNOWN_TOKENS`` MUST have a matching mask in
    ``sanitize_log_message``. Any future Vault-family prefix addition
    to the scanner without a companion log-mask addition fails this
    test on the first pytest run after the new scanner entry is
    committed — surfacing the next drift family programmatically."""
    expected_vault_prefixes = {"hvs", "hvb", "hvr"}

    # Confirm the scanner enumerates exactly the expected Vault prefixes.
    scanner_vault_prefixes: set[str] = set()
    for regex, _attribution in _KNOWN_TOKENS:
        pattern_text = regex.pattern
        for prefix in expected_vault_prefixes:
            if f"{prefix}\\." in pattern_text:
                scanner_vault_prefixes.add(prefix)
    assert scanner_vault_prefixes == expected_vault_prefixes, (
        f"Scanner Vault prefix family drift: expected {expected_vault_prefixes}, "
        f"found {scanner_vault_prefixes}. If a new Vault token prefix was added "
        f"to _KNOWN_TOKENS, add it to sanitize_log_message AND update this test."
    )

    # Confirm sanitize_log_message masks every prefix.
    body = "Z" * 90
    for prefix in expected_vault_prefixes:
        log_line = f"diagnostic: {prefix}.{body}"
        result = sanitize_log_message(log_line)
        assert f"{prefix}.{body}" not in result, (
            f"Vault token prefix '{prefix}.' missing from sanitize_log_message "
            f"mask family — log-sanitisation drift vs. scanner _KNOWN_TOKENS"
        )
        assert f"{prefix}.***" in result, (
            f"Vault token prefix '{prefix}.' mask MUST preserve issuer "
            f"attribution as '{prefix}.***' for incident-response triage"
        )
