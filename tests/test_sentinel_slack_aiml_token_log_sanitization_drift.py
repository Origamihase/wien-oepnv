"""Sentinel drift coverage for the Slack token family + AI/ML inference
platform tier value-shape log-sanitisation across
``sanitize_log_message`` and the downstream ``_sanitize_exception_msg``
chain.

The 2026-05-17 Multi-Vendor (AWS / Google / Stripe / Anthropic / OpenAI /
GitLab PAT / NPM / SendGrid / Hugging Face), GitHub, and HashiCorp Vault
Log-Sanitisation Drift Closure rounds
established the canonical "value-shape mask for every scanner
``_KNOWN_TOKENS`` prefix" contract. The Multi-Vendor round explicitly
named "~70 next-round-candidate scanner detectors with parallel log-
sanitisation drift"; this round closes two of the highest-impact next
clusters: the **Slack token family** (7 distinct issuer prefixes spanning
bot / user / OAuth / refresh / browser-session / cookie-session /
rotation-refresh credential tiers) and the **AI/ML Inference Platform
Tier** (Groq / Replicate / Perplexity / xAI / OpenRouter — 5 indie-
developer-facing inference vendors whose tokens the scanner already
detects in committed source files but whose companion log-sanitisation
codepath (``src/utils/logging.py:sanitize_log_message``) was NOT
extended in any prior round.

Families covered (each is a sibling-drift closure for one or more
``_KNOWN_TOKENS`` entries):

Slack family
------------

* **Slack Bot Token** — ``xoxb-<digits>-<digits>-<24 alnum>``. The
  workhorse credential for Slack automation: posted to channels, DM
  users, read messages, upload files, manage workspace members per the
  app's installed scopes. Highest routine-leak severity in the Slack
  family due to ubiquity in CI/CD secrets and `.env` files.

* **Slack User Token** — ``xoxp-<digits>-<digits>-<digits>-<32 alnum>``.
  Acts as the user — full impersonation including DMs, search, file
  access, channel history. The legacy V1 user-token shape (V2 rotation
  flow uses the ``xoxe.xoxp-`` chained refresh form covered below).

* **Slack OAuth Access Token** — ``xoxa-<body>``. Configuration tokens
  issued via the OAuth flow. Distinct from bot/user tokens in scope
  attribution but shares the same workspace blast radius.

* **Slack Refresh Token** — ``xoxr-<body>``. Issued alongside rotating
  bot/user tokens. Mints fresh ``xoxb-``/``xoxp-`` access tokens until
  the refresh token itself is revoked at slack.com/app-settings.

* **Slack Browser Session Token** — ``xoxc-<body>``. The session
  cookie extracted from Slack web sessions via DevTools. The canonical
  "session hijack" credential: an attacker holding ``xoxc-`` can
  impersonate the user's browser session for unattended scripted
  access including reading DMs, posting messages as the user, and
  exfiltrating workspace files.

* **Slack Cookie Session Token** — ``xoxd-<body>``. Companion to
  ``xoxc-`` (the ``d`` cookie value from Slack web sessions). The two
  typically leak together; same blast radius as ``xoxc-`` plus the
  ability to bypass workspace 2FA challenges if the session was
  established post-2FA.

* **Slack Token Rotation Refresh** — ``xoxe-`` (direct) /
  ``xoxe.xoxb-`` / ``xoxe.xoxp-`` (chained). The modern V2 rotation
  refresh credential. The chained shape embeds the rotation chain's
  identity (``xoxb-`` for bot rotation, ``xoxp-`` for user rotation).
  Distinct from the legacy ``xoxr-`` refresh-token flow; the two are
  NOT interchangeable.

AI/ML Inference Platform Tier
-----------------------------

* **Groq API Key** — ``gsk_<32+ alphanumeric>``. Issued via
  console.groq.com/keys for the Groq LPU-accelerated inference API
  (LLaMA / Mixtral / Gemma deployments). Leaking grants completion-
  API access at the issuer's per-key quota tier — common attack: free-
  tier abuse for unauthorised LLaMA inference.

* **Replicate API Token** — ``r8_<40 alphanumeric>``. Issued via
  replicate.com/account/api-tokens for the Replicate model-hosting
  platform (Stable Diffusion, CUDA-accelerated custom Cog models).
  Leaking grants compute-billing fraud — GPU inference at the
  issuer's expense; can also push backdoored Cog models if the token
  has push scope.

* **Perplexity API Key** — ``pplx-<32+ alphanumeric>``. Issued via
  perplexity.ai/settings/api for the Perplexity AI search-grounded
  inference API. Leaking grants billing fraud and search-result
  exfiltration via the answer-grounding chain.

* **xAI API Key** — ``xai-<32+ alphanumeric>``. Issued via
  console.x.ai/team/<team>/api-keys for xAI's Grok API. Newest
  high-volume issuer (grew rapidly in 2024-2025). Leaking grants
  Grok-2 / Grok-2 Vision inference at the issuer's expense.

* **OpenRouter API Key** — ``sk-or-v1-<32+ alphanumeric>``. The
  unified OpenAI-compatible API aggregator. UNIQUE CROSS-PLATFORM
  PIVOT AMPLIFIER: a leaked OpenRouter token grants access to all
  the user's attached provider keys (Anthropic, OpenAI, Mistral, etc)
  through the aggregator proxy — effectively a multi-vendor
  credential-chain pivot.

Pre-fix detection gaps (mirror the 2026-05-17 Multi-Vendor / Vault /
GitHub rounds' structural analysis):

1. ``sanitize_log_message`` masks credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare
   token in plain log text bypasses every existing pattern. Four
   leak surfaces:

   * **Plain application f-string logs** — ``log.error(f"Slack post
     failed using {token}: {exc}")``. The bare token shape lands in
     operator log streams verbatim.
   * **Upstream error responses** — ``log.warning(f"Provider error:
     {response.text}")`` where a misconfigured / compromised upstream
     echoes the supplied token back in its error payload (Slack
     ``invalid_auth`` responses sometimes include the token suffix
     in their ``warning`` field; AI/ML platforms commonly echo the
     auth header in rate-limit responses).
   * **JSON values without sensitive key names** — ``{"data":
     "xoxb-..."}`` or ``{"payload": "gsk_..."}``. The JSON-key
     sensitive-name regex misses keys like ``data`` / ``payload`` /
     ``response_body`` / ``message`` so the token value leaks
     unredacted into the JSON value span.
   * **URL paths / query strings with non-sensitive parameter
     names** — ``GET /v1/some/path?ref=xoxb-...`` or
     ``/api/internal/audit/gsk_XXX/details``. The Basic-Auth-in-URL
     regex requires the credential to appear before ``@``; path-
     embedded and query-string tokens with NON-sensitive parameter
     names slip past entirely.

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

**Fix:** append thirteen value-shape mask patterns to
``sanitize_log_message``'s pattern list mirroring the scanner regex
structural anchors exactly. Each pattern preserves the issuer-
specific prefix (``xoxb-***``, ``xoxe.xoxb-***``, ``gsk_***``,
``sk-or-v1-***`` etc.) for incident-response triage because each
tier has a distinct revocation flow:

* Slack — slack.com/app-settings > Workspace tokens > Revoke for
  xoxb/xoxp/xoxa/xoxr; password reset + active session termination
  for xoxc/xoxd; xoxe is revoked when the parent bot/user token's
  rotation chain is rotated.
* Groq — console.groq.com/keys > Revoke; audit usage dashboard
  for unauthorised inference.
* Replicate — replicate.com/account/api-tokens > Delete; audit
  predictions tab for unauthorised inference + Cog push history.
* Perplexity — perplexity.ai/settings/api > Revoke; audit billing
  dashboard.
* xAI — console.x.ai/team/<team>/api-keys > Revoke; audit team
  usage.
* OpenRouter — openrouter.ai/keys > Revoke; CRITICAL — audit the
  attached provider keys (Anthropic / OpenAI / Mistral / etc.)
  for downstream compromise via the aggregator proxy.

Structural anchors mirror the scanner regexes exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myxoxb-``, ``foogsk_`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format
  reject accidental fragments while accepting every real-shape
  token.

Idempotence: masked forms (``xoxb-***``, ``xoxe.xoxb-***``,
``gsk_***`` etc.) do NOT match any of the new regexes because
``*`` is not in any body alphabet AND the masked body length
(3 chars) is below every per-family floor (20/24/32/35/36/40).

Marker: SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for the Slack "
    "(xoxb-/xoxp-/xoxa-/xoxr-/xoxc-/xoxd-/xoxe-) and AI/ML inference "
    "platform tier (Groq gsk_, Replicate r8_, Perplexity pplx-, xAI "
    "xai-, OpenRouter sk-or-v1-) token families that the scanner's "
    "_KNOWN_TOKENS already detects in committed source files. Bare "
    "tokens in plain log text, JSON values with non-sensitive keys, "
    "URL paths / query strings, and exception messages slipped past "
    "all key/header/URL-credential masking patterns and leaked "
    "verbatim into operator log streams and the public "
    "docs/feed_health.json artefact."
)


# ---------------------------------------------------------------------------
# Canonical real-shape token fixtures, one per scanner-detected prefix.
# Each token uses a body shape that exercises the regex's full alphabet
# so partial-class bypasses (uniform-class bodies) cannot mask a regex
# bug as a passing test.
# ---------------------------------------------------------------------------

# Slack canonical structured tokens — strict per-segment shape.
# Scanner regex requires the bot/user numeric segments to be 10+ digits
# and the trailing alphanumeric segment exactly 24/32 chars (no _ or -).
_SLACK_BOT = (
    "xoxb-" + "1" * 11 + "-" + "9" * 11 + "-" + "Aa1Bb2Cc3Dd4" * 2  # 24-char tail
)
_SLACK_USER = (
    "xoxp-"
    + "1" * 11
    + "-"
    + "2" * 11
    + "-"
    + "3" * 11
    + "-"
    + "Ee5Ff6Gg7Hh8Ii9Jj0Kk1Ll2Mm3Nn4Oo"  # exactly 32 alphanumeric chars
)
# Slack OAuth / Refresh / Browser / Cookie / Rotation: body alphabet is
# ``[0-9a-zA-Z-]`` (dash allowed, NO underscore). 20+ char floor.
_SLACK_OAUTH = "xoxa-" + "Aa1-Bb2-Cc3-Dd4-Ee5-Ff6"  # 23 chars body
_SLACK_REFRESH = "xoxr-" + "Ff6-Gg7-Hh8-Ii9-Jj0-Kk1"
_SLACK_BROWSER = "xoxc-" + "Kk1-Ll2-Mm3-Nn4-Oo5-Pp6"
_SLACK_COOKIE = "xoxd-" + "Pp6-Qq7-Rr8-Ss9-Tt0-Uu1"
# Slack V2 rotation refresh — direct form (xoxe-<body>)
_SLACK_ROT_DIRECT = "xoxe-" + "Vv1-Ww2-Xx3-Yy4-Zz5-Aa6"
# Slack V2 rotation refresh — chained forms (xoxe.xox[bp]-<body>)
_SLACK_ROT_CHAINED_BOT = "xoxe.xoxb-" + "Cc3-Dd4-Ee5-Ff6-Gg7-Hh8"
_SLACK_ROT_CHAINED_USER = "xoxe.xoxp-" + "Hh8-Ii9-Jj0-Kk1-Ll2-Mm3"

# AI/ML platform tier tokens — pure alphanumeric body per scanner regex.
_GROQ = "gsk_" + "Gg0Aa1Bb2Cc3Dd4Ee5Ff6Hh7Ii8Jj9Kk0Ll1Mm2N"  # 40 chars
# Replicate body MUST be exactly 40 chars per scanner regex.
_REPLICATE = "r8_" + ("Rr0Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1Ll2" + "Z")  # exactly 40
_PERPLEXITY = "pplx-" + "Pp0Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0K"  # 32 chars
_XAI = "xai-" + "Xx0Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1"  # 32+ chars
_OPENROUTER = (
    "sk-or-v1-" + "Oo0Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1L"  # 32+ chars
)

# Sanity check: ensure Replicate body length is exactly 40 chars
assert len(_REPLICATE) - len("r8_") == 40, (
    "Replicate fixture body must be exactly 40 chars per scanner regex"
)


# Group fixtures for parametrisation
_SLACK_TOKENS = [
    (_SLACK_BOT, "xoxb-"),
    (_SLACK_USER, "xoxp-"),
    (_SLACK_OAUTH, "xoxa-"),
    (_SLACK_REFRESH, "xoxr-"),
    (_SLACK_BROWSER, "xoxc-"),
    (_SLACK_COOKIE, "xoxd-"),
    (_SLACK_ROT_DIRECT, "xoxe-"),
    (_SLACK_ROT_CHAINED_BOT, "xoxe.xoxb-"),
    (_SLACK_ROT_CHAINED_USER, "xoxe.xoxp-"),
]

_AIML_TOKENS = [
    (_GROQ, "gsk_"),
    (_REPLICATE, "r8_"),
    (_PERPLEXITY, "pplx-"),
    (_XAI, "xai-"),
    (_OPENROUTER, "sk-or-v1-"),
]

_ALL_TOKENS = _SLACK_TOKENS + _AIML_TOKENS


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_slack_aiml_token_in_plain_log_line_is_masked(
    token: str, expected_prefix: str
) -> None:
    """Bare Slack / AI/ML token in plain log text MUST be masked by
    ``sanitize_log_message`` — pre-fix this leaked verbatim through
    the operator-log sink and the public ``docs/feed_health.json``
    artefact."""
    log_line = f"Provider API returned 401: invalid credential {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token with prefix '{expected_prefix}' leaked through "
        f"sanitize_log_message: "
        f"{SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert f"{expected_prefix}***" in result, (
        f"Mask MUST preserve issuer-attribution prefix "
        f"'{expected_prefix}***' for incident-response triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak vector via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_slack_aiml_token_in_json_value_is_masked(
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
def test_slack_aiml_token_in_url_query_with_non_sensitive_param_is_masked(
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


def test_slack_token_in_url_path_segment_is_masked() -> None:
    """Token embedded in URL path segment (NOT ``user:pass@`` form)
    MUST be masked — covers the path-embedded leak surface."""
    log_line = f"GET /api/internal/audit/{_SLACK_BOT}/details 200"
    result = sanitize_log_message(log_line)
    assert _SLACK_BOT not in result
    assert "xoxb-***" in result


def test_aiml_token_in_url_path_segment_is_masked() -> None:
    """AI/ML token in URL path segment MUST be masked."""
    log_line = f"GET /api/internal/audit/{_GROQ}/details 200"
    result = sanitize_log_message(log_line)
    assert _GROQ not in result
    assert "gsk_***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg — exception text from provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_slack_aiml_token_through_sanitize_exception_msg(
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


def test_sanitize_log_arg_masks_slack_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_SLACK_BOT}"
    result = sanitize_log_arg(arg)
    assert _SLACK_BOT not in result
    assert "xoxb-***" in result


def test_sanitize_log_arg_masks_aiml_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation.
    Uses a NON-sensitive attribute name (``audit``) so the value-shape
    mask is the primary defence (the ``key=value`` regex would catch
    sensitive names like ``token`` first)."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_OPENROUTER})"

    result = sanitize_log_arg(_Wrapper())
    assert _OPENROUTER not in result, (
        "OpenRouter token leaked through sanitize_log_arg"
    )
    assert "sk-or-v1-***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Short fragments — body below each per-family floor
        "xoxb-short",
        "xoxp-short",
        "xoxa-short",
        "xoxr-short",
        "xoxc-short",
        "xoxd-short",
        "xoxe-short",
        "xoxe.xoxb-short",
        "gsk_short",
        "r8_short",
        "pplx-short",
        "xai-short",
        "sk-or-v1-short",
        # Mid-identifier collisions (lookbehind prevents these)
        "0xoxb-" + "1" * 11 + "-" + "9" * 11 + "-" + "A" * 24,
        "1xoxe-" + "A" * 25,
        "agsk_" + "A" * 40,
        "fr8_" + "A" * 40,
        "0pplx-" + "A" * 32,
        "1xai-" + "A" * 32,
        "2sk-or-v1-" + "A" * 32,
    ],
)
def test_benign_slack_aiml_prefix_is_not_masked(benign: str) -> None:
    """Negative case: short prefixes / mid-identifier collisions MUST
    NOT be masked. The ``(?<![A-Za-z0-9])`` lookbehind plus the body
    floor are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive vendor token mask on benign input: "
        f"{benign!r} → {result!r}"
    )


def test_short_groq_body_is_not_masked() -> None:
    """Groq body shorter than 32 chars is below the structural floor."""
    short = "gsk_" + "a" * 31  # 31 chars body, below 32-char floor
    result = sanitize_log_message(short)
    assert result == short


def test_short_replicate_body_is_not_masked() -> None:
    """Replicate body shorter than 40 chars is below the floor."""
    short = "r8_" + "a" * 39  # 39 chars body, below exact-40 floor
    result = sanitize_log_message(short)
    assert result == short


def test_long_replicate_body_is_not_masked() -> None:
    """Replicate body longer than exactly 40 chars is also rejected
    by the strict-length anchor (mirrors scanner regex)."""
    long = "r8_" + "a" * 41  # 41 chars body, exceeds exact-40 anchor
    result = sanitize_log_message(long)
    # Boundary char makes the lookbehind fail; the strict 40-char
    # regex requires exactly 40 chars then a non-alphanumeric. With
    # 41 alphanumeric chars in a row, the regex either won't match
    # OR matches only 40 — but the lookahead requires non-alphanumeric
    # immediately after, so 41-char body fails the lookahead.
    assert long in result


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,_prefix", _ALL_TOKENS)
def test_slack_aiml_token_mask_is_idempotent(token: str, _prefix: str) -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output. The mask token (e.g. ``xoxb-***``) MUST
    NOT be re-matched as a credential."""
    log_line = f"Failed: {token}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert token not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — log mask covers every scanner prefix
# ---------------------------------------------------------------------------


def test_slack_family_log_mask_covers_every_scanner_prefix() -> None:
    """**Sibling-alignment invariant.** Every Slack issuer prefix
    that appears in ``_KNOWN_TOKENS`` MUST have a matching mask in
    ``sanitize_log_message``. A future Slack prefix addition to the
    scanner without a companion log-mask addition fails this test
    programmatically."""
    # We verify the canonical bare-prefix forms (the structured xoxb-/
    # xoxp- get tested separately above with their digit segments).
    pairs = [
        ("xoxa-", _SLACK_OAUTH),
        ("xoxr-", _SLACK_REFRESH),
        ("xoxc-", _SLACK_BROWSER),
        ("xoxd-", _SLACK_COOKIE),
        ("xoxe-", _SLACK_ROT_DIRECT),
    ]
    for prefix, token in pairs:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"Slack prefix '{prefix}' missing from sanitize_log_message "
            f"mask family — log-sanitisation drift vs. scanner _KNOWN_TOKENS"
        )
        assert f"{prefix}***" in result, (
            f"Slack prefix '{prefix}' mask MUST preserve issuer "
            f"attribution as '{prefix}***' for incident-response triage"
        )


def test_aiml_family_log_mask_covers_every_scanner_prefix() -> None:
    """**Sibling-alignment invariant** for the AI/ML inference platform
    tier (Groq / Replicate / Perplexity / xAI / OpenRouter)."""
    pairs = [
        ("gsk_", _GROQ),
        ("r8_", _REPLICATE),
        ("pplx-", _PERPLEXITY),
        ("xai-", _XAI),
        ("sk-or-v1-", _OPENROUTER),
    ]
    for prefix, token in pairs:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"AI/ML prefix '{prefix}' missing from sanitize_log_message "
            f"mask family"
        )
        assert f"{prefix}***" in result, (
            f"AI/ML prefix '{prefix}' mask MUST preserve issuer "
            f"attribution as '{prefix}***' for incident-response triage"
        )


def test_scanner_known_tokens_contain_every_log_masked_prefix() -> None:
    """**Inverse sibling-alignment invariant.** Every prefix we log-
    mask in this round MUST also exist in the scanner's
    ``_KNOWN_TOKENS``. This ensures the log mask doesn't drift ahead
    of the scanner — if the scanner ever drops a prefix, this test
    fires."""
    scanner_pattern_text = " ".join(
        regex.pattern for regex, _attr in _KNOWN_TOKENS
    )

    expected_substrings = [
        ("xoxb-", "Slack Bot Token"),
        ("xoxp-", "Slack User Token"),
        ("xoxa-", "Slack OAuth Access Token"),
        ("xoxr-", "Slack Refresh Token"),
        ("xoxc-", "Slack Browser Session Token"),
        ("xoxd-", "Slack Cookie Session Token"),
        ("xoxe", "Slack Token Rotation Refresh"),
        ("gsk_", "Groq API Key"),
        ("r8_", "Replicate API Token"),
        ("pplx-", "Perplexity API Key"),
        ("xai-", "xAI API Key"),
        ("sk-or-v1-", "OpenRouter API Key"),
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


def test_openrouter_does_not_get_eaten_by_openai_legacy() -> None:
    """The OpenAI legacy ``sk-[A-Za-z0-9]{48}`` pattern must NOT match
    OpenRouter ``sk-or-v1-...`` tokens — the OpenRouter regex
    preserves the ``sk-or-v1-`` prefix attribution."""
    log_line = f"key: {_OPENROUTER}"
    result = sanitize_log_message(log_line)
    assert _OPENROUTER not in result
    assert "sk-or-v1-***" in result
    # The mask MUST NOT be the generic OpenAI ``sk-***`` form.
    # If the OpenAI legacy regex matched, the result would contain a
    # plain ``sk-***`` span at the openrouter prefix position.
    # We allow ``sk-or-v1-***`` substring matches but no isolated ``sk-***``
    # because the OpenRouter mask preserves the full prefix.


def test_xoxe_chained_does_not_get_eaten_by_xoxb_pattern() -> None:
    """The ``xoxe.xoxb-`` chained form must be masked by the xoxe
    regex (preserves ``xoxe.xoxb-`` prefix), NOT split at the
    embedded ``xoxb-`` boundary."""
    log_line = f"refresh: {_SLACK_ROT_CHAINED_BOT}"
    result = sanitize_log_message(log_line)
    assert _SLACK_ROT_CHAINED_BOT not in result
    # The mask MUST be the full chained form
    assert "xoxe.xoxb-***" in result


# ---------------------------------------------------------------------------
# (10) Real-world emission pattern — upstream error echoing token back
# ---------------------------------------------------------------------------


def test_upstream_error_echoing_slack_token_back_is_masked() -> None:
    """Real-world leak vector: Slack ``invalid_auth`` error response
    sometimes includes the supplied token suffix in its ``warning``
    field. The application log line ``log.warning(f"Slack error:
    {response.text}")`` must NOT leak the token."""
    fake_response_text = (
        '{"ok": false, "error": "invalid_auth", '
        f'"warning": "supplied token {_SLACK_BOT}"}}'
    )
    log_line = f"Slack API error: {fake_response_text}"
    result = sanitize_log_message(log_line)
    assert _SLACK_BOT not in result
    assert "xoxb-***" in result


def test_upstream_error_echoing_groq_token_back_is_masked() -> None:
    """Mirror test for Groq API: rate-limit response echoing the
    Authorization header value verbatim (a common AI-platform debug-
    response pattern) must be masked."""
    fake_error = (
        '{"error": {"type": "authentication_error", '
        f'"detail": "Provided key {_GROQ} is invalid"}}}}'
    )
    log_line = f"Provider error: {fake_error}"
    result = sanitize_log_message(log_line)
    assert _GROQ not in result
    assert "gsk_***" in result


def test_upstream_error_echoing_openrouter_pivot_token_is_masked() -> None:
    """OpenRouter is a CROSS-PLATFORM PIVOT AMPLIFIER: a leaked
    OpenRouter token grants access to the user's attached provider
    keys (Anthropic / OpenAI / Mistral / etc.). The bare token in
    log streams MUST be masked to prevent multi-vendor compromise."""
    log_line = (
        f"OpenRouter 401: invalid key {_OPENROUTER} — check that the "
        f"key has not been rotated"
    )
    result = sanitize_log_message(log_line)
    assert _OPENROUTER not in result
    assert "sk-or-v1-***" in result
