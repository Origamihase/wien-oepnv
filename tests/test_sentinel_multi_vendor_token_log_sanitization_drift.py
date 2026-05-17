"""Sentinel drift coverage for the High-Severity Cloud / Payment / LLM /
Git-Host token family value-shape log-sanitisation across
``sanitize_log_message`` and the downstream ``_sanitize_exception_msg``
chain.

The 2026-05-17 Vault and GitHub Log-Sanitisation Drift Closure rounds
(see ``.jules/sentinel.md``) established the canonical "value-shape mask
for every scanner ``_KNOWN_TOKENS`` prefix" contract. The GitHub round
explicitly named "the FIRST of the ~70 next-round-candidate scanner
detectors with parallel log-sanitisation drift"; this round closes the
next cluster of high-severity vendor families that the scanner already
detects in committed source files but whose companion log-sanitisation
codepath (``src/utils/logging.py:sanitize_log_message``) was NOT
extended in any prior round.

Families covered (each is a sibling-drift closure for one or more
``_KNOWN_TOKENS`` entries):

* **AWS access keys** — ``AKIA`` / ``ASIA`` / ``ACCA`` / ``ABIA``
  prefixes plus a 16-char ``[A-Z0-9]`` body. Cloud-account
  credentials — leak grants full data-plane + control-plane access
  for the principal's IAM scope: read every accessible S3 bucket /
  RDS / DynamoDB, mint STS sessions, modify IAM (with ``iam:*``),
  exfiltrate KMS-encrypted data via ``kms:Decrypt``. Per-prefix
  attribution accelerates IR triage (Personnel ``AKIA`` vs. STS
  session ``ASIA`` vs. Federated ``ACCA`` vs. Service Bearer
  ``ABIA`` rotation flows differ).

* **Google API Key** — ``AIza`` prefix plus a 35-char
  ``[0-9A-Za-z\\-_]`` body. Per-key scope for the issuing project:
  Maps / Places / Geocoding / Translate / YouTube quota burn (USD
  100s/day at scale), plus the project's quota-tier billing fraud.

* **Stripe Secret / Restricted / Webhook keys** — ``sk_live_`` /
  ``sk_test_`` / ``rk_live_`` / ``rk_test_`` (24-char body) plus
  ``whsec_`` (32+ char body). Payment-processing fraud (full
  account API access for the live secret; webhook forgery for
  ``whsec_``).

* **Anthropic API Key** — ``sk-ant-(api|admin)NN-`` prefix plus a
  32+ char body. LLM billing fraud + prompt exfiltration. Admin-
  tier additionally grants console access to the org's billing /
  organisation members.

* **OpenAI keys** — ``sk-`` (legacy, exactly-48-char body) /
  ``sk-proj-`` / ``sk-svcacct-`` (each with 40+ char body). Same
  blast radius across tiers: completion API at the issuer's
  expense, custom-model exfiltration, fine-tune-job hijack.

* **GitLab Personal Access Token** — ``glpat-`` prefix plus a
  20-char body. Mirror of the GitHub PAT family scope on the
  GitLab side: full repo / project access per token scope.

* **NPM Access Token** — ``npm_`` prefix plus a 36-char alphanumeric
  body. Supply-chain risk: publish malicious packages under the
  issuer's organisation, modify package-tarball contents,
  deprecate legitimate versions.

* **SendGrid API Key** — ``SG.`` prefix plus a ``<22>.<43>`` body.
  Transactional email sent FROM the project's authenticated
  sending domain (phishing amplification leveraging SPF / DKIM /
  DMARC authentication), contact-list exfiltration.

* **Hugging Face Access Token** — ``hf_`` prefix plus a 32+ char
  body. Model hub access: read private models / datasets /
  Spaces, push backdoored model weights, exfiltrate fine-tuning
  data.

Pre-fix detection gaps (mirror the 2026-05-17 Vault / GitHub rounds'
structural analysis):

1. ``sanitize_log_message`` masks credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare
   token in plain log text bypasses every existing pattern. Four
   leak surfaces:

   * **Plain application f-string logs** — ``log.error(f"Auth failed
     using {token}: {exc}")``. The bare token shape lands in operator
     log streams verbatim.
   * **Upstream error responses** — ``log.warning(f"Provider error:
     {response.text}")`` where a misconfigured / compromised upstream
     echoes the supplied token back in its error payload (Stripe
     test mode, AWS STS error payloads, OpenAI rate-limit responses).
   * **JSON values without sensitive key names** — ``{"data":
     "AKIA..."}`` or ``{"payload": "sk-ant-..."}``. The JSON-key
     sensitive-name regex misses keys like ``data`` / ``payload`` /
     ``response_body`` / ``message`` so the token value leaks
     unredacted into the JSON value span.
   * **URL paths / query strings with non-sensitive parameter
     names** — ``GET /v1/some/path?ref=AKIA...`` or
     ``/api/internal/audit/sk-ant-api03-XXX/details``. The Basic-
     Auth-in-URL regex requires the credential to appear before
     ``@``; path-embedded and query-string tokens with NON-
     sensitive parameter names slip past entirely.

2. End-to-end via ``_sanitize_exception_msg``: this is the canonical
   exception-text sanitisation path in ``src/utils/http.py``. It
   extracts HTTP URLs via a pre-regex and falls back to
   ``sanitize_log_message`` for the non-HTTP-URL remainder. Fixing
   the latter closes the exception-text leak sink for every leaked
   token in the families covered here.

Threat model is identical to the scanner's ``_KNOWN_TOKENS`` round
for each family: the bare token shape in the operator log stream
(Slack escalation, GitHub Issue body submitted by
``submit_auto_issue``, ``docs/feed_health.json`` public artefact,
SIEM aggregator pipeline) grants the same per-vendor scope as the
committed-source leak that the scanner already protects against.

**Fix:** append eleven value-shape mask patterns to
``sanitize_log_message``'s pattern list mirroring the scanner regex
structural anchors exactly. Each pattern preserves the issuer-
specific prefix (``AKIA***``, ``sk_live_***``, ``sk-ant-api03-***``,
``glpat-***`` etc.) for incident-response triage because each tier
has a distinct revocation flow:

* AWS — Console > IAM > Users > Security credentials > Deactivate
  & Delete access key; STS sessions auto-expire but stolen
  permanent keys must be deactivated explicitly.
* Google API — console.cloud.google.com > APIs & Services >
  Credentials > Regenerate key (or restrict by referrer/IP/API).
* Stripe — dashboard.stripe.com/apikeys > Roll key for live;
  webhook endpoint settings for ``whsec_``.
* Anthropic — console.anthropic.com/settings/keys > Revoke.
* OpenAI — platform.openai.com/api-keys > Revoke; for legacy
  ``sk-`` reissue after revocation.
* GitLab — gitlab.com/-/user_settings/personal_access_tokens >
  Revoke; audit GitLab activity log for misuse window.
* NPM — npmjs.com/settings/<user>/tokens > Revoke; audit
  ``npm publish`` history for malicious version pushes.
* SendGrid — app.sendgrid.com/settings/api_keys > Delete; audit
  mail.send API logs for unauthorised sends.
* Hugging Face — huggingface.co/settings/tokens > Refresh /
  Revoke; audit private-model access logs.

Structural anchors mirror the scanner regexes exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myAKIA<16>``, ``foosk_live_<24>`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format
  reject accidental fragments while accepting every real-shape
  token.

Idempotence: masked forms (``AKIA***``, ``sk_live_***``,
``glpat-***`` etc.) do NOT match any of the new regexes because
``*`` is not in any body alphabet AND the masked body length
(3 chars) is below every per-family floor (16/22/24/32/35/36/40/48).

Marker: SENTINEL_MULTI_VENDOR_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_MULTI_VENDOR_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_MULTI_VENDOR_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for the AWS / Google "
    "API / Stripe / Anthropic / OpenAI / GitLab PAT / NPM / SendGrid / "
    "Hugging Face token families that the scanner's _KNOWN_TOKENS "
    "already detects in committed source files. Bare tokens in plain "
    "log text, JSON values with non-sensitive keys, URL paths / query "
    "strings, and exception messages slipped past all key/header/URL-"
    "credential masking patterns and leaked verbatim into operator log "
    "streams and the public docs/feed_health.json artefact."
)


# ---------------------------------------------------------------------------
# Canonical real-shape token fixtures, one per scanner-detected prefix.
# Each token uses a body shape that exercises the regex's full alphabet
# so partial-class bypasses (uniform-class bodies) cannot mask a regex
# bug as a passing test.
# ---------------------------------------------------------------------------

# AWS — 4 prefixes + 16 uppercase alphanumeric body.
_AWS_AKIA = "AKIA" + "Z" * 16
_AWS_ASIA = "ASIA" + "5" * 16
_AWS_ACCA = "ACCA" + "Y4" * 8
_AWS_ABIA = "ABIA" + "B3" * 8

# Google API Key — 35-char body from [0-9A-Za-z\-_].
_GOOGLE_API = "AIza" + "Sy" + "A-_" * 11  # 35-char body after AIza prefix

# Stripe — 24-char alphanumeric bodies.
_STRIPE_LIVE = "sk_live_" + "Aa1Bb2" * 4
_STRIPE_TEST = "sk_test_" + "Cc3Dd4" * 4
_STRIPE_RKEY_LIVE = "rk_live_" + "Ee5Ff6" * 4
_STRIPE_RKEY_TEST = "rk_test_" + "Gg7Hh8" * 4
# Stripe webhook signing secret — 32+ char alphanumeric body.
_STRIPE_WHSEC = "whsec_" + "Aa0Bb1Cc2Dd3Ee4Ff5Gg6Hh7Ii8Jj9Kk1"  # 33 chars body

# Anthropic — api/admin + NN version + 32+ char body.
_ANTHROPIC_API = (
    "sk-ant-api03-" + "Ant_API_" + "Aa1Bb2_-Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1Ll"
)  # 32+ char body
_ANTHROPIC_ADMIN = (
    "sk-ant-admin02-"
    + "Ant_AD_"
    + "Mm1Nn2_-Oo3Pp4Qq5Rr6Ss7Tt8Uu9Vv0Ww1Xx"
)

# OpenAI — 48-char alphanumeric legacy body, 40+ char project/svcacct body.
_OPENAI_LEGACY = "sk-" + "Aa1Bb2Cc3Dd4" * 4  # 48 chars body
_OPENAI_PROJ = "sk-proj-" + "Pp0_Qq1-Rr2_Ss3-Tt4_Uu5-Vv6_Ww7-Xx8_Yy9-"  # 40 chars body
_OPENAI_SVCACCT = (
    "sk-svcacct-" + "Sv_-Cc0Dd1Ee2Ff3Gg4Hh5Ii6Jj7Kk8Ll9Mm0Nn4Oo"
)  # 41 chars body

# GitLab PAT — strict 20-char body.
_GITLAB_PAT = "glpat-" + "Gl1-Gl2_3Gl4-Gl5-678"  # 20 chars body

# NPM — strict 36-char alphanumeric body.
_NPM_TOKEN = "npm_" + "Nn1Oo2Pp3" * 4  # 36 chars body

# SendGrid — distinct three-segment shape SG.<22>.<43>.
# First segment EXACTLY 22 chars, second segment EXACTLY 43 chars.
_SENDGRID = "SG." + ("Aa1Bb2" * 3 + "Cc3X") + "." + ("Aa1Bb2Cc3" * 4 + "Dd4Ee5F")

# Hugging Face — 32+ char alphanumeric body.
_HF_TOKEN = "hf_" + "Hf0Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk"  # 32+ chars


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected_prefix",
    [
        (_AWS_AKIA, "AKIA"),
        (_AWS_ASIA, "ASIA"),
        (_AWS_ACCA, "ACCA"),
        (_AWS_ABIA, "ABIA"),
        (_GOOGLE_API, "AIza"),
        (_STRIPE_LIVE, "sk_live_"),
        (_STRIPE_TEST, "sk_test_"),
        (_STRIPE_RKEY_LIVE, "rk_live_"),
        (_STRIPE_RKEY_TEST, "rk_test_"),
        (_STRIPE_WHSEC, "whsec_"),
        (_ANTHROPIC_API, "sk-ant-api03-"),
        (_ANTHROPIC_ADMIN, "sk-ant-admin02-"),
        (_OPENAI_LEGACY, "sk-"),
        (_OPENAI_PROJ, "sk-proj-"),
        (_OPENAI_SVCACCT, "sk-svcacct-"),
        (_GITLAB_PAT, "glpat-"),
        (_NPM_TOKEN, "npm_"),
        (_SENDGRID, "SG."),
        (_HF_TOKEN, "hf_"),
    ],
)
def test_vendor_token_in_plain_log_line_is_masked(
    token: str, expected_prefix: str
) -> None:
    """Bare vendor token in plain log text MUST be masked by
    ``sanitize_log_message`` — pre-fix this leaked verbatim through
    the operator-log sink and the public ``docs/feed_health.json``
    artefact."""
    log_line = f"Provider API returned 401: invalid credential {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token with prefix '{expected_prefix}' leaked through "
        f"sanitize_log_message: "
        f"{SENTINEL_MULTI_VENDOR_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert f"{expected_prefix}***" in result, (
        f"Mask MUST preserve issuer-attribution prefix "
        f"'{expected_prefix}***' for incident-response triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak vector via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected_prefix",
    [
        (_AWS_AKIA, "AKIA"),
        (_GOOGLE_API, "AIza"),
        (_STRIPE_LIVE, "sk_live_"),
        (_STRIPE_WHSEC, "whsec_"),
        (_ANTHROPIC_API, "sk-ant-api03-"),
        (_OPENAI_LEGACY, "sk-"),
        (_OPENAI_PROJ, "sk-proj-"),
        (_GITLAB_PAT, "glpat-"),
        (_NPM_TOKEN, "npm_"),
        (_SENDGRID, "SG."),
        (_HF_TOKEN, "hf_"),
    ],
)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_vendor_token_in_json_value_is_masked(
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


@pytest.mark.parametrize(
    "token,expected_prefix",
    [
        (_AWS_AKIA, "AKIA"),
        (_GOOGLE_API, "AIza"),
        (_STRIPE_LIVE, "sk_live_"),
        (_ANTHROPIC_API, "sk-ant-api03-"),
        (_OPENAI_LEGACY, "sk-"),
        (_GITLAB_PAT, "glpat-"),
        (_HF_TOKEN, "hf_"),
    ],
)
def test_vendor_token_in_url_query_with_non_sensitive_param_is_masked(
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


def test_vendor_token_in_url_path_segment_is_masked() -> None:
    """Token embedded in URL path segment (NOT ``user:pass@`` form)
    MUST be masked — covers the path-embedded leak surface."""
    log_line = f"GET /api/internal/audit/{_AWS_AKIA}/details 200"
    result = sanitize_log_message(log_line)
    assert _AWS_AKIA not in result
    assert "AKIA***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg — exception text from provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected_prefix",
    [
        (_AWS_AKIA, "AKIA"),
        (_GOOGLE_API, "AIza"),
        (_STRIPE_LIVE, "sk_live_"),
        (_ANTHROPIC_API, "sk-ant-api03-"),
        (_OPENAI_LEGACY, "sk-"),
        (_GITLAB_PAT, "glpat-"),
        (_NPM_TOKEN, "npm_"),
        (_SENDGRID, "SG."),
        (_HF_TOKEN, "hf_"),
    ],
)
def test_vendor_token_through_sanitize_exception_msg(
    token: str, expected_prefix: str
) -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask vendor tokens. The function
    extracts HTTP URLs via a pre-regex and falls back to
    ``sanitize_log_message`` for the remainder; fixing the latter
    closes the exception-text leak sink."""
    exc_msg = f"HTTPError: 401 Unauthorized — credential {token} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"{expected_prefix}***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_vendor_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args.
    Vendor tokens in string args MUST be masked."""
    arg = f"audit: {_AWS_AKIA}"
    result = sanitize_log_arg(arg)
    assert _AWS_AKIA not in result
    assert "AKIA***" in result


def test_sanitize_log_arg_masks_vendor_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before
    sanitisation. A custom object whose ``__str__`` contains a vendor
    token MUST have the token masked."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_STRIPE_LIVE})"

    result = sanitize_log_arg(_Wrapper())
    assert _STRIPE_LIVE not in result, (
        "Stripe live secret leaked through sanitize_log_arg"
    )
    assert "sk_live_***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Short fragments — body below each per-family floor
        "AKIAshort",
        "AIza_short",
        "sk_live_short",
        "whsec_short",
        "sk-ant-api03-short",
        "sk-proj-short",
        "sk-short",
        "glpat-short",
        "npm_short",
        "SG.short.short",
        "hf_short",
        # Mid-identifier collisions (lookbehind prevents these)
        "xAKIAZZZZZZZZZZZZZZZZ",  # 'x' before 'AKIA' breaks prefix
        "0AIza" + "A" * 35,
        "1sk_live_" + "A" * 24,
        "2sk-ant-api03-" + "A" * 32,
        "3glpat-" + "A" * 20,
        "4npm_" + "A" * 36,
    ],
)
def test_benign_vendor_prefix_is_not_masked(benign: str) -> None:
    """Negative case: short prefixes / mid-identifier collisions MUST
    NOT be masked. The ``(?<![A-Za-z0-9])`` lookbehind plus the body
    floor are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive vendor token mask on benign input: "
        f"{benign!r} → {result!r}"
    )


def test_short_aws_body_is_not_masked() -> None:
    """AWS body shorter than 16 chars is below the structural floor."""
    short = "AKIA" + "A" * 15  # 15 chars body, below 16-char floor
    result = sanitize_log_message(short)
    assert result == short


def test_short_openai_legacy_body_is_not_masked() -> None:
    """OpenAI legacy body shorter than 48 chars is below the floor."""
    short = "sk-" + "a" * 47  # 47 chars body, below 48-char floor
    result = sanitize_log_message(short)
    assert result == short


def test_short_glpat_body_is_not_masked() -> None:
    """GitLab PAT body shorter than 20 chars is below the floor."""
    short = "glpat-" + "a" * 19  # 19 chars body, below 20-char floor
    result = sanitize_log_message(short)
    assert result == short


def test_short_sendgrid_body_is_not_masked() -> None:
    """SendGrid requires <22>.<43> body — shorter shapes are not masked."""
    short = "SG." + "A" * 21 + "." + "A" * 42  # below floor
    result = sanitize_log_message(short)
    assert result == short


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        _AWS_AKIA,
        _GOOGLE_API,
        _STRIPE_LIVE,
        _STRIPE_WHSEC,
        _ANTHROPIC_API,
        _OPENAI_LEGACY,
        _OPENAI_PROJ,
        _GITLAB_PAT,
        _NPM_TOKEN,
        _SENDGRID,
        _HF_TOKEN,
    ],
)
def test_vendor_token_mask_is_idempotent(token: str) -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output. The mask token (e.g. ``AKIA***``) MUST
    NOT be re-matched as a credential (``*`` is not in any body
    alphabet AND the masked body length is below every per-family
    floor)."""
    log_line = f"Failed: {token}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert token not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — log mask covers every scanner prefix
# ---------------------------------------------------------------------------


def test_aws_family_log_mask_covers_every_scanner_prefix() -> None:
    """**Sibling-alignment invariant.** Every AWS access-key prefix
    that appears in ``_KNOWN_TOKENS`` / ``_AWS_ID_RE`` MUST have a
    matching mask in ``sanitize_log_message``. A future AWS prefix
    addition to the scanner without a companion log-mask addition
    fails this test programmatically."""
    expected_prefixes = {"AKIA", "ASIA", "ACCA", "ABIA"}
    body = "Z" * 16
    for prefix in expected_prefixes:
        log_line = f"diagnostic: {prefix}{body}"
        result = sanitize_log_message(log_line)
        assert f"{prefix}{body}" not in result, (
            f"AWS prefix '{prefix}' missing from sanitize_log_message "
            f"mask family — log-sanitisation drift vs. _AWS_ID_RE"
        )
        assert f"{prefix}***" in result, (
            f"AWS prefix '{prefix}' mask MUST preserve issuer "
            f"attribution as '{prefix}***' for incident-response triage"
        )


def test_stripe_family_log_mask_covers_every_scanner_prefix() -> None:
    """**Sibling-alignment invariant** for the Stripe family
    (``sk_live_`` / ``sk_test_`` / ``rk_live_`` / ``rk_test_``)."""
    expected_prefixes = {"sk_live", "sk_test", "rk_live", "rk_test"}
    body = "Z" * 24
    for prefix in expected_prefixes:
        log_line = f"diagnostic: {prefix}_{body}"
        result = sanitize_log_message(log_line)
        assert f"{prefix}_{body}" not in result, (
            f"Stripe prefix '{prefix}_' missing from "
            f"sanitize_log_message mask family"
        )
        assert f"{prefix}_***" in result, (
            f"Stripe prefix '{prefix}_' mask MUST preserve issuer "
            f"attribution as '{prefix}_***' for incident-response triage"
        )


def test_scanner_known_tokens_contain_every_log_masked_prefix() -> None:
    """**Inverse sibling-alignment invariant.** Every prefix we log-
    mask in this round MUST also exist in the scanner's
    ``_KNOWN_TOKENS`` (or the AWS-specific ``_AWS_ID_RE``). This
    ensures the log mask doesn't drift ahead of the scanner — if the
    scanner ever drops a prefix, this test fires."""
    scanner_pattern_text = " ".join(
        regex.pattern for regex, _attr in _KNOWN_TOKENS
    )

    # Each entry: (substring-to-find, label-for-error-message)
    # Use distinctive substrings that survive copy-paste between
    # scanner and log-sanitiser without false positives.
    expected_substrings = [
        ("sk_live_", "Stripe Live Secret Key"),
        ("sk_test_", "Stripe Test Secret Key"),
        ("rk_live_", "Stripe Restricted Live Key"),
        ("rk_test_", "Stripe Restricted Test Key"),
        ("whsec_", "Stripe Webhook Signing Secret"),
        ("AIza", "Google API Key"),
        ("sk-ant-", "Anthropic API Key"),
        ("sk-proj-", "OpenAI Project API Key"),
        ("sk-svcacct-", "OpenAI Service Account Key"),
        ("glpat-", "GitLab Personal Access Token"),
        ("npm_", "NPM Access Token"),
        ("SG\\.", "SendGrid API Key"),
        ("hf_", "Hugging Face Access Token"),
    ]
    for substring, label in expected_substrings:
        assert substring in scanner_pattern_text, (
            f"Scanner _KNOWN_TOKENS is missing the '{substring}' "
            f"({label}) entry that the log-sanitiser mask family "
            f"depends on. If this test fires, either restore the "
            f"scanner entry or remove the corresponding mask from "
            f"sanitize_log_message."
        )


# ---------------------------------------------------------------------------
# (9) Cross-family no-collision — each pattern matches ONLY its own family
# ---------------------------------------------------------------------------


def test_openai_legacy_does_not_eat_sk_ant_token() -> None:
    """The OpenAI legacy ``sk-[A-Za-z0-9]{48}`` pattern must NOT match
    Anthropic ``sk-ant-...`` tokens (Anthropic body alphabet permits
    ``-`` so the legacy regex's strict alphanumeric body is the
    structural disambiguator)."""
    log_line = f"key: {_ANTHROPIC_API}"
    result = sanitize_log_message(log_line)
    assert _ANTHROPIC_API not in result
    # Must be masked by the Anthropic regex (preserves ``sk-ant-api03-``
    # prefix), NOT by the OpenAI legacy regex (would produce only ``sk-***``).
    assert "sk-ant-api03-***" in result
    assert "sk-***" not in result.replace("sk-ant-api03-***", "")


def test_openai_legacy_does_not_eat_sk_proj_token() -> None:
    """The OpenAI legacy ``sk-[A-Za-z0-9]{48}`` pattern must NOT match
    OpenAI Project ``sk-proj-...`` tokens — the Project regex
    preserves the ``sk-proj-`` prefix attribution."""
    log_line = f"key: {_OPENAI_PROJ}"
    result = sanitize_log_message(log_line)
    assert _OPENAI_PROJ not in result
    assert "sk-proj-***" in result


def test_openai_legacy_does_not_eat_sk_svcacct_token() -> None:
    """The OpenAI legacy pattern must NOT match Service Account
    tokens — the SA regex preserves the ``sk-svcacct-`` prefix."""
    log_line = f"key: {_OPENAI_SVCACCT}"
    result = sanitize_log_message(log_line)
    assert _OPENAI_SVCACCT not in result
    assert "sk-svcacct-***" in result


# ---------------------------------------------------------------------------
# (10) Real-world emission pattern — upstream error echoing token back
# ---------------------------------------------------------------------------


def test_upstream_error_echoing_aws_token_back_is_masked() -> None:
    """Real-world leak vector: a compromised / misconfigured upstream
    echoes the supplied access-key in its error payload. The
    application log line ``log.warning(f"Provider error: {response.text}")``
    must NOT leak the token."""
    fake_response_text = (
        '{"error": "Access Denied", "request_id": "abc", '
        f'"credentials_used": "{_AWS_AKIA}"}}'
    )
    log_line = f"Provider error: {fake_response_text}"
    result = sanitize_log_message(log_line)
    assert _AWS_AKIA not in result
    assert "AKIA***" in result


def test_upstream_error_echoing_anthropic_token_back_is_masked() -> None:
    """Mirror test for Anthropic API: upstream error echoing the API
    credential (e.g. via a debug-mode error response with the
    credential embedded in a non-sensitive-named JSON field) must
    be masked AND preserve the ``sk-ant-api03-`` issuer attribution
    for incident-response triage."""
    fake_error = (
        '{"type": "authentication_error", '
        f'"detail": "Credential {_ANTHROPIC_API} is revoked"}}'
    )
    log_line = f"Provider error: {fake_error}"
    result = sanitize_log_message(log_line)
    assert _ANTHROPIC_API not in result
    assert "sk-ant-api03-***" in result
