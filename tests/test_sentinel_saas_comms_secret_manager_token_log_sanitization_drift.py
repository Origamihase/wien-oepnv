"""Sentinel drift coverage for the SaaS / Communications / Workspace /
Observability / Secret-Manager token tier value-shape log-sanitisation
across ``sanitize_log_message`` and the downstream
``_sanitize_exception_msg`` chain.

After the 2026-05-17 DevOps & CI/CD Pipeline + DigitalOcean round, the
secret-scanner ``_KNOWN_TOKENS`` table still detects committed tokens
across the following high-blast-radius issuer families that the log-
sanitisation codepath (``src/utils/logging.py:sanitize_log_message``)
DOES NOT mask — bare token shapes in plain log text (application
f-string logs, upstream error responses echoing the token back, JSON
values without sensitive key names, URL paths / query strings with
NON-sensitive parameter names) bypass every existing key/header/URL-
credential mask pattern and leak verbatim into operator log streams
and the public ``docs/feed_health.json`` artefact.

Families covered (each is a sibling-drift closure for one or more
``_KNOWN_TOKENS`` entries; ordered by blast radius):

Universal Auth tier (1 prefix)
------------------------------

* **JSON Web Token (JWT)** — ``eyJ<10+ base64url>.<10+ base64url>.<20+
  base64url>``. The canonical bearer credential for Auth0, Okta, AWS
  Cognito, Google Identity, Azure AD, custom OAuth/OIDC providers and
  every modern SaaS identity provider. A leaked JWT grants bearer
  access for the token's TTL (minutes to hours, but routinely
  re-issued via refresh tokens so any single leak compromises the
  authentication-flow chain). Dots are OUTSIDE the entropy fallback's
  ``[A-Za-z0-9+/=_-]`` alphabet so without this specific pattern only
  ONE segment is matched at a time, and the full-token span (the
  bearer credential) is silently lost.

Workspace SaaS tier (5 prefixes)
--------------------------------

* **Atlassian API Token** — ``ATATT3xFfGF0<100+ base64url body>``.
  Issued at id.atlassian.com/manage-profile/security/api-tokens for
  Jira / Confluence / Trello REST API access. Leak grants the issuing
  user's full Cloud-API scope across every accessible workspace.

* **Linear API Key** — ``lin_api_<32+ alphanumeric>``. Issued via
  linear.app/settings/api for personal API access against the issue
  tracker / project-management GraphQL API. Leak grants the issuing
  user's full Linear scope.

* **Notion Integration Token** — ``secret_<43 alphanumeric>`` (legacy)
  AND ``ntn_<43+ chars from [A-Za-z0-9_-]>`` (modern). Issued via
  notion.so/my-integrations. Leak grants read/write access to whatever
  workspace content the integration is shared with — full
  database/page contents including private collaborator notes.

* **Postman API Key** — ``PMAK-<24 hex>-<34 hex>``. Issued at
  postman.com/settings/me/api-keys for full Postman REST-API access:
  read/write every accessible workspace's collections, environments,
  mocks, monitors, and team membership.

Observability tier (1 prefix)
-----------------------------

* **Sentry Auth Token** — ``sntrys_<30+ base64url body>``. Issued at
  sentry.io/settings/auth-tokens/. Leak grants org-level Sentry API
  access — every project's issue / event data, releases, debug files,
  source maps, member list and webhook configuration.

Secret-Manager tier (1 prefix family)
-------------------------------------

* **Doppler Tokens** — ``dp.<pt|st|sa|ct|scim|audit>.<43 alphanumeric>``.
  Six role variants (personal-token / service-token / service-account
  token / CLI token / SCIM provisioning token / audit-log token).
  Issued at dashboard.doppler.com. **HIGHEST blast-radius amplifier in
  the modern stack**: a single leaked Doppler token grants read access
  to every secret stored in the accessible projects/configs — database
  credentials, third-party API keys, OAuth client secrets, signing
  keys are all routinely stored in Doppler environments. One leak
  compromises every downstream credential.

Communications tier (4 prefixes / 3 vendors)
--------------------------------------------

* **Telegram Bot Token** — ``<3-14 decimal digits>:<35 chars from
  [a-zA-Z0-9_-]>``. Issued by Telegram's BotFather. Leak grants full
  bot impersonation: post in every chat the bot is added to, exfiltrate
  message history, manipulate inline-query callbacks.

* **Twilio Account SID** — ``AC<32 lowercase hex>``. The principal
  credential for the project; pairs with the Auth Token to make API
  calls (call/SMS history, billing, phone-number provisioning).

* **Twilio API Key SID** — ``SK<32 lowercase hex>``. Fine-grained
  scoped credential; pairs with a separate secret for API access.
  Both Twilio SID forms enable 2FA-bypass attacks when paired with a
  leaked Auth Token / API-Key Secret.

* **Mailgun Private API Key** — ``key-<32 lowercase hex>``. Issued at
  app.mailgun.com/app/account/security/api_keys. Leak grants the
  attacker the ability to send mail FROM the project's authenticated
  domain (phishing amplification leveraging existing SPF / DKIM).

Pre-fix detection gaps (mirror the 2026-05-17 Multi-Vendor / Vault /
GitHub / Slack-AIML / CICD-DevOps rounds' structural analysis):

1. ``sanitize_log_message`` masks credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare
   token in plain log text bypasses every existing pattern.

2. End-to-end via ``_sanitize_exception_msg``: this is the canonical
   exception-text sanitisation path in ``src/utils/http.py``. It
   extracts HTTP URLs via a pre-regex and falls back to
   ``sanitize_log_message`` for the non-HTTP-URL remainder. Fixing
   the latter closes the exception-text leak sink for every leaked
   token in the families covered here.

**Fix:** append twelve value-shape mask patterns to
``sanitize_log_message``'s pattern list mirroring the scanner regex
structural anchors exactly. Each pattern preserves the issuer-
specific prefix (``eyJ***``, ``ATATT3xFfGF0***``, ``sntrys_***``,
``dp.pt.***``, ``PMAK-***``, ``key-***``, etc.) for incident-response
triage because each tier has a distinct revocation flow.

Structural anchors mirror the scanner regexes exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myATATT3xFfGF0``, ``foosntrys_`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format reject
  accidental fragments while accepting every real-shape token.

Idempotence: masked forms (``eyJ***``, ``ATATT3xFfGF0***``,
``sntrys_***``, ``dp.pt.***``, ``PMAK-***``, etc.) do NOT match any
of the new regexes because ``*`` is not in any body alphabet AND the
masked body length (3 chars) is below every per-family floor
(20/30/32/35/43/100).

Marker: SENTINEL_SAAS_COMMS_SECRET_MANAGER_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_SAAS_COMMS_SECRET_MANAGER_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_SAAS_COMMS_SECRET_MANAGER_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for JWT (eyJ.../.../.../), "
    "Atlassian (ATATT3xFfGF0...), Linear (lin_api_), Notion (secret_/ntn_), "
    "Postman (PMAK-), Sentry (sntrys_), Doppler (dp.pt./.../audit.), "
    "Telegram (<digits>:<35>), Twilio (AC<32hex>/SK<32hex>), and Mailgun "
    "(key-<32hex>) token families that the scanner's _KNOWN_TOKENS already "
    "detects in committed source files. Bare tokens in plain log text, "
    "JSON values with non-sensitive keys, URL paths / query strings, and "
    "exception messages slipped past all key/header/URL-credential masking "
    "patterns and leaked verbatim into operator log streams and the public "
    "docs/feed_health.json artefact."
)


# ---------------------------------------------------------------------------
# Canonical real-shape token fixtures, one per scanner-detected prefix.
# Each body uses a mixed alphabet that exercises the regex's full character
# class so partial-class bypasses (uniform-class bodies) cannot mask a
# regex bug as a passing test.
# ---------------------------------------------------------------------------


def _body_extended(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    extended ``[A-Za-z0-9_-]`` alphabet — exercises the full character
    class so a partial-class regex bug cannot pass."""
    chunk = "Aa1B-c_D"  # 8-char cycle covering upper/lower/digit/dash/underscore
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_base64url_eq(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    base64url + ``=`` padding alphabet ``[A-Za-z0-9_=-]``."""
    chunk = "Aa1B-c_D=2"  # 10-char cycle covering full alphabet
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_alnum(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    pure alphanumeric alphabet (no `_` or `-`)."""
    chunk = "Aa1Bb2Cc3"  # 9-char cycle, no underscores or dashes
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_hex_lower(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    lowercase hex alphabet."""
    chunk = "0123456789abcdef"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_hex_mixed(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    mixed-case hex alphabet ``[a-fA-F0-9]`` — exercises the full Postman
    alphabet so a lowercase-only regex bug cannot pass."""
    chunk = "0123456789aAbBcCdDeEfF"
    return (chunk * (length // len(chunk) + 1))[:length]


# JWT: three base64url segments separated by dots, ``eyJ`` header prefix.
# 10+ first segment, 10+ second, 20+ third.
_JWT = "eyJ" + _body_extended(28) + "." + _body_extended(40) + "." + _body_extended(43)

# Atlassian API Token: ``ATATT3xFfGF0<100+ base64url-eq body>``.
_ATLASSIAN = "ATATT3xFfGF0" + _body_base64url_eq(104)

# Telegram Bot Token: ``<3-14 digits>:<exactly 35 chars from [A-Za-z0-9_-]>``.
_TELEGRAM = "1234567890:" + _body_extended(35)

# Twilio Account SID: ``AC<32 lowercase hex>``.
_TWILIO_AC = "AC" + _body_hex_lower(32)

# Twilio API Key SID: ``SK<32 lowercase hex>``.
_TWILIO_SK = "SK" + _body_hex_lower(32)

# Sentry Auth Token: ``sntrys_<30+ base64url-eq body>``.
_SENTRY = "sntrys_" + _body_base64url_eq(40)

# Doppler tokens: ``dp.<role>.<43 alphanumeric>``.
_DOPPLER_PT = "dp.pt." + _body_alnum(43)
_DOPPLER_ST = "dp.st." + _body_alnum(43)
_DOPPLER_SA = "dp.sa." + _body_alnum(43)
_DOPPLER_CT = "dp.ct." + _body_alnum(43)
_DOPPLER_SCIM = "dp.scim." + _body_alnum(43)
_DOPPLER_AUDIT = "dp.audit." + _body_alnum(43)

# Linear API Key: ``lin_api_<32+ alphanumeric>``.
_LINEAR = "lin_api_" + _body_alnum(40)

# Notion Integration Token (legacy): ``secret_<43 alphanumeric>``.
_NOTION_LEGACY = "secret_" + _body_alnum(43)

# Notion Modern Integration Token: ``ntn_<43+ from [A-Za-z0-9_-]>``.
_NOTION_MODERN = "ntn_" + _body_extended(48)

# Postman API Key: ``PMAK-<24 mixed-case hex>-<34 mixed-case hex>``.
_POSTMAN = "PMAK-" + _body_hex_mixed(24) + "-" + _body_hex_mixed(34)

# Mailgun Private API Key: ``key-<32 lowercase hex>``.
_MAILGUN = "key-" + _body_hex_lower(32)


# Sanity checks: ensure fixture body lengths satisfy the scanner regex
# anchor floors exactly. Failure here means the test fixture itself is
# malformed and would mask a real regex bug.
assert _JWT.startswith("eyJ")
assert _JWT.count(".") == 2
assert _ATLASSIAN.startswith("ATATT3xFfGF0")
assert len(_ATLASSIAN) - len("ATATT3xFfGF0") >= 100
assert _TELEGRAM.split(":")[0].isdigit() and 3 <= len(_TELEGRAM.split(":")[0]) <= 14
assert len(_TELEGRAM.split(":")[1]) == 35
assert _TWILIO_AC.startswith("AC") and len(_TWILIO_AC) == 34
assert _TWILIO_SK.startswith("SK") and len(_TWILIO_SK) == 34
assert _SENTRY.startswith("sntrys_") and len(_SENTRY) - len("sntrys_") >= 30
for tok in (_DOPPLER_PT, _DOPPLER_ST, _DOPPLER_SA, _DOPPLER_CT, _DOPPLER_SCIM, _DOPPLER_AUDIT):
    assert tok.startswith("dp.")
    role_and_body = tok[len("dp."):]
    role, body = role_and_body.split(".", 1)
    assert role in {"pt", "st", "sa", "ct", "scim", "audit"}
    assert len(body) == 43
assert _LINEAR.startswith("lin_api_") and len(_LINEAR) - len("lin_api_") >= 32
assert _NOTION_LEGACY.startswith("secret_") and len(_NOTION_LEGACY) - len("secret_") == 43
assert _NOTION_MODERN.startswith("ntn_") and len(_NOTION_MODERN) - len("ntn_") >= 43
assert _POSTMAN.startswith("PMAK-")
postman_parts = _POSTMAN[len("PMAK-"):].split("-")
assert len(postman_parts) == 2
assert len(postman_parts[0]) == 24
assert len(postman_parts[1]) == 34
assert _MAILGUN.startswith("key-") and len(_MAILGUN) - len("key-") == 32


# Group fixtures for parametrisation, ordered by tier
_UNIVERSAL_AUTH_TOKENS = [
    (_JWT, "eyJ"),
]

_WORKSPACE_SAAS_TOKENS = [
    (_ATLASSIAN, "ATATT3xFfGF0"),
    (_LINEAR, "lin_api_"),
    (_NOTION_LEGACY, "secret_"),
    (_NOTION_MODERN, "ntn_"),
    (_POSTMAN, "PMAK-"),
]

_OBSERVABILITY_TOKENS = [
    (_SENTRY, "sntrys_"),
]

_SECRET_MANAGER_TOKENS = [
    (_DOPPLER_PT, "dp.pt."),
    (_DOPPLER_ST, "dp.st."),
    (_DOPPLER_SA, "dp.sa."),
    (_DOPPLER_CT, "dp.ct."),
    (_DOPPLER_SCIM, "dp.scim."),
    (_DOPPLER_AUDIT, "dp.audit."),
]

_COMMUNICATIONS_TOKENS = [
    (_TELEGRAM, "1234567890:"),
    (_TWILIO_AC, "AC"),
    (_TWILIO_SK, "SK"),
    (_MAILGUN, "key-"),
]

_ALL_TOKENS = (
    _UNIVERSAL_AUTH_TOKENS
    + _WORKSPACE_SAAS_TOKENS
    + _OBSERVABILITY_TOKENS
    + _SECRET_MANAGER_TOKENS
    + _COMMUNICATIONS_TOKENS
)


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner DOES detect these tokens in committed
# source. Proves the divergence between scanner detection and log-
# sanitisation that this round closes. If the scanner ever drops one of
# these prefixes, this test FAILS first (loud) — preventing silent
# drift in the opposite direction.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,_expected_prefix", _ALL_TOKENS)
def test_drift_premise_scanner_detects_token(
    token: str, _expected_prefix: str
) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST detect each token shape
    this round masks — that asymmetry IS the drift this round closes."""
    matched = False
    for regex, _reason in _KNOWN_TOKENS:
        if regex.search(token):
            matched = True
            break
    assert matched, (
        f"Drift premise FAILED: token {token[:20]!r}... is no longer "
        f"detected by _KNOWN_TOKENS — this test must be updated if the "
        f"scanner drops the corresponding pattern."
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_saas_token_in_plain_log_line_is_masked(
    token: str, expected_prefix: str
) -> None:
    """Bare SaaS/comms/secret-manager token in plain log text MUST be
    masked by ``sanitize_log_message`` — pre-fix this leaked verbatim
    through the operator-log sink and the public
    ``docs/feed_health.json`` artefact."""
    log_line = f"Provider API returned 401: invalid credential {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token with prefix '{expected_prefix}' leaked through "
        f"sanitize_log_message: "
        f"{SENTINEL_SAAS_COMMS_SECRET_MANAGER_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert f"{expected_prefix}***" in result, (
        f"Mask MUST preserve issuer-attribution prefix "
        f"'{expected_prefix}***' for incident-response triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_saas_token_in_json_value_is_masked(
    token: str, expected_prefix: str, key_name: str
) -> None:
    """Token in JSON value with a NON-sensitive key name MUST be
    masked — pre-fix the JSON-key sensitive-name regex missed keys
    like ``data`` / ``payload`` / ``response_body`` / ``message`` and
    the token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token with prefix '{expected_prefix}' in JSON value with "
        f"non-sensitive key '{key_name}' leaked through "
        f"sanitize_log_message"
    )
    assert f"{expected_prefix}***" in result


# ---------------------------------------------------------------------------
# (3) URL path / query string with non-sensitive parameter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_saas_token_in_url_query_with_non_sensitive_param_is_masked(
    token: str, expected_prefix: str
) -> None:
    """Token in URL query string with a NON-sensitive parameter name
    (``ref`` / ``commit_sha`` / ``q``) MUST be masked — pre-fix the
    URL credential regex required the credential to appear before
    ``@``; query-string and path-embedded tokens slipped past."""
    log_line = f"GET /api/foo/bar?ref={token} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{expected_prefix}***" in result


def test_atlassian_token_in_url_path_segment_is_masked() -> None:
    """Atlassian API Token embedded in URL path segment (NOT
    ``user:pass@`` form) MUST be masked — covers the path-embedded
    leak surface."""
    log_line = f"GET /api/internal/audit/{_ATLASSIAN}/details 200"
    result = sanitize_log_message(log_line)
    assert _ATLASSIAN not in result
    assert "ATATT3xFfGF0***" in result


def test_doppler_token_in_url_path_segment_is_masked() -> None:
    """Doppler token embedded in a URL path segment MUST be masked.
    Multi-segment shape with two dot separators is the canonical
    'entropy-fallback-bypass' shape — without the explicit pattern
    only the 43-alnum body span would match, losing ``dp.pt.``
    attribution."""
    log_line = f"GET /api/internal/audit/{_DOPPLER_PT}/details 200"
    result = sanitize_log_message(log_line)
    assert _DOPPLER_PT not in result
    assert "dp.pt.***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_saas_token_through_sanitize_exception_msg(
    token: str, expected_prefix: str
) -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask vendor tokens."""
    exc_msg = f"HTTPError: 401 Unauthorized — credential {token} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"{expected_prefix}***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_jwt_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_JWT}"
    result = sanitize_log_arg(arg)
    assert _JWT not in result
    assert "eyJ***" in result


def test_sanitize_log_arg_masks_doppler_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation.
    Uses a NON-sensitive attribute name (``audit``) so the value-shape
    mask is the primary defence (the ``key=value`` regex would catch
    sensitive names like ``token`` first)."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_DOPPLER_PT})"

    result = sanitize_log_arg(_Wrapper())
    assert _DOPPLER_PT not in result, (
        "Doppler personal token leaked through sanitize_log_arg"
    )
    assert "dp.pt.***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Short fragments — body below each per-family floor
        "eyJ-too.short.body",  # third segment < 20
        "ATATT3xFfGF0-short",
        "1234567890:short",  # second segment < 35
        "AC" + "a" * 31,  # body < 32 hex
        "SK" + "a" * 31,  # body < 32 hex
        "sntrys_short",
        "dp.pt.short",
        "dp.invalidrole.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # bad role
        "lin_api_short",
        "secret_short",  # body < 43
        "ntn_short",
        "PMAK-short-short",
        "key-shortish",
        # Mid-identifier collisions (lookbehind prevents these)
        "XeyJ" + "A" * 11 + ".AAAAAAAAAAAA.AAAAAAAAAAAAAAAAAAAAA",
        "ZATATT3xFfGF0" + "A" * 100,
        "0sntrys_" + "A" * 30,
        "Adp.pt." + "A" * 43,
        "Mlin_api_" + "A" * 32,
        "Xsecret_" + "A" * 43,
        "5ntn_" + "A" * 43,
        "ZPMAK-" + "a" * 24 + "-" + "a" * 34,
        "0key-" + "a" * 32,
    ],
)
def test_benign_saas_prefix_is_not_masked(benign: str) -> None:
    """Negative case: short prefixes / mid-identifier collisions MUST
    NOT be masked. The ``(?<![A-Za-z0-9])`` lookbehind plus the body
    floor are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive vendor token mask on benign input: "
        f"{benign!r} → {result!r}"
    )


def test_short_atlassian_body_is_not_masked() -> None:
    """Atlassian body shorter than 100 chars is below the structural floor."""
    short = "ATATT3xFfGF0" + "A" * 99  # 99 chars body, below 100-char floor
    result = sanitize_log_message(short)
    assert result == short


def test_short_jwt_body_is_not_masked() -> None:
    """JWT body shorter than the 20-char third-segment floor is below
    the structural floor."""
    short = "eyJ" + "A" * 10 + "." + "A" * 10 + "." + "A" * 19  # third seg 19
    result = sanitize_log_message(short)
    assert result == short


def test_invalid_doppler_role_is_not_masked() -> None:
    """Doppler role MUST be one of ``pt|st|sa|ct|scim|audit``; any
    other value is below the structural floor."""
    invalid = "dp.invalid." + _body_alnum(43)
    result = sanitize_log_message(invalid)
    assert result == invalid


# ---------------------------------------------------------------------------
# (7) Idempotence — masked outputs MUST NOT match the new patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "eyJ***",
        "ATATT3xFfGF0***",
        "1234567890:***",
        "AC***",
        "SK***",
        "sntrys_***",
        "dp.pt.***",
        "dp.st.***",
        "dp.sa.***",
        "dp.ct.***",
        "dp.scim.***",
        "dp.audit.***",
        "lin_api_***",
        "secret_***",
        "ntn_***",
        "PMAK-***",
        "key-***",
    ],
)
def test_masked_form_is_idempotent(masked: str) -> None:
    """Running sanitize_log_message twice MUST be idempotent — the
    masked form (``<prefix>***``) MUST NOT itself match any of the
    new regexes. The ``*`` char is outside every body alphabet AND
    the ``***`` length (3 chars) is below every per-family body floor."""
    log_line = f"prior IR note: token redacted as {masked}"
    result = sanitize_log_message(log_line)
    assert masked in result, (
        f"Idempotence broken: masked form {masked!r} was further "
        f"modified by sanitize_log_message: {result!r}"
    )
