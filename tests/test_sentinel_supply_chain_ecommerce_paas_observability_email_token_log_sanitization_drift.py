"""Sentinel drift coverage for the Supply-Chain / E-Commerce / PaaS /
Observability / Email-Platform token tier value-shape log-sanitisation
across ``sanitize_log_message`` and the downstream
``_sanitize_exception_msg`` chain.

After the 2026-05-17 SaaS / Comms / Workspace / Observability / Secret-
Manager round, the secret-scanner ``_KNOWN_TOKENS`` table still detected
committed tokens for ten remaining high-blast-radius issuer families
that the log-sanitisation codepath
(``src/utils/logging.py:sanitize_log_message``) DID NOT mask. Bare token
shapes in plain log text (application f-string logs, upstream error
responses echoing the token back, JSON values without sensitive key
names, URL paths / query strings with NON-sensitive parameter names)
bypassed every existing key/header/URL-credential mask pattern and
leaked verbatim into operator log streams and the public
``docs/feed_health.json`` artefact.

Families covered (each is a sibling-drift closure for one or more
``_KNOWN_TOKENS`` entries; ordered by blast-radius tier):

Supply-Chain tier (1 prefix)
----------------------------

* **PyPI API Token** — ``pypi-<20+ chars from [A-Za-z0-9_-]>``. Issued
  at pypi.org/manage/account/token/. Leak grants publish access to
  every accessible PyPI project — canonical supply-chain compromise
  primitive: a hostile actor pushes a backdoored wheel of a popular
  package and every CI run of every downstream consumer pulls the
  malicious version.

E-Commerce tier (5 prefixes / 2 vendors)
----------------------------------------

* **Shopify Admin/Custom/Partner API + Shared Secret** — ``shpat_``,
  ``shpss_``, ``shppa_``, ``shpca_`` each followed by 32 mixed-case
  hex. Full storefront/admin scope: read every order / customer record
  (PII + payment metadata), modify product catalogue, drain Shopify
  Payments balance via refunds-to-attacker-IBAN flow. ``shpss_`` is
  the webhook HMAC signing key — forge any webhook payload from any
  private Shopify app.

* **WooCommerce Consumer Key/Secret** — ``ck_<32+ alnum>``,
  ``cs_<32+ alnum>``. Pairs for the WooCommerce REST API. Full
  storefront access: read every order / customer record, drain via
  refund flow analogous to Shopify Admin API.

* **Square Access Token** — ``EAAA<60+ chars from [A-Za-z0-9_-]>``.
  Full payment processing access: read transaction history (PII +
  card-fingerprint metadata), issue refunds, create new charges.

PaaS / Edge-Runtime tier (3 prefixes / 3 vendors)
-------------------------------------------------

* **Netlify Personal Access Token** — ``nfp_<40+ alphanumeric>``. Full
  account scope: redirect every site's deploys to an attacker-controlled
  build, exfiltrate every env-var that a build can read (the canonical
  landing zone for AWS / Stripe / database creds), modify DNS / SSL
  config to hijack the production domain.

* **Render API Key** — ``rnd_<40+ chars from [A-Za-z0-9_-]>``. Same
  deployment-hijack blast radius as Netlify for services hosted on
  Render's PaaS.

* **Fly.io API Token** — ``FlyV1 fm1_<50+>`` / ``FlyV1 fm2_<50+>`` /
  ``FlyV1 fo1_<50+>`` (canonical macaroon-based access tokens). Edge-
  runtime hijack: deploy malicious code to every Fly app the token's
  macaroon scope grants, modify routing / Wireguard peers / Anycast
  routes, rotate billing credentials. The ``FlyV1`` prefix uniquely
  contains a LITERAL SPACE between the issuer keyword and the
  macaroon-discriminator body — mirroring the scanner's exact pattern.

Observability tier (3 prefixes / 1 vendor)
------------------------------------------

* **New Relic User Key** — ``NRAK-<27 uppercase alnum>``. Full
  user-scope account access: read every APM stream's payload (which
  routinely contains debug-logged tokens from production traces —
  secondary credential leak amplifier).
* **New Relic REST API Key** — ``NRRA-<40 mixed-case hex>``. REST API
  access scoped per the key's privilege level.
* **New Relic Insights Insert Key** — ``NRII-<32 mixed-case hex>``.
  NRDB write access; can fabricate / overwrite APM events to mask
  intrusion artifacts.

Email-Platform tier (2 prefixes / 2 vendors — phishing amplification)
---------------------------------------------------------------------

* **Brevo (Sendinblue) API Key** — ``xkeysib-<64 hex>-<16 alnum>``.
  Two-part body shape. Leak grants the attacker the ability to send
  mail FROM the project's authenticated sending domain (phishing
  amplification leveraging existing SPF / DKIM / DMARC), export the
  full subscriber CSV (PII exfiltration), and access the
  transactional logs.

* **Mailchimp API Key** — ``<32 lowercase hex>-us<digits>``. The
  ``us<region>`` datacenter routing identifier is PRESERVED in the
  mask (``***-us20``) for IR-routing attribution since mailchimp.com's
  API endpoints are keyed off the region. NO issuer prefix — purely
  structural. Phishing amplification, subscriber CSV export.

Pre-fix detection gaps (mirror the 2026-05-17 SaaS / Multi-Vendor /
Vault / GitHub / Slack-AIML / CICD-DevOps rounds' structural analysis):

1. ``sanitize_log_message`` masked credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare
   token in plain log text bypassed every existing pattern.

2. End-to-end via ``_sanitize_exception_msg``: the canonical
   exception-text sanitisation path in ``src/utils/http.py``. It
   extracts HTTP URLs via a pre-regex and falls back to
   ``sanitize_log_message`` for the non-HTTP-URL remainder.

**Fix:** append eleven value-shape mask patterns to
``sanitize_log_message``'s pattern list mirroring the scanner regex
structural anchors exactly. Each mask preserves the issuer-specific
prefix (``pypi-***``, ``shpat_***``, ``EAAA***``, ``nfp_***``,
``FlyV1 fm1_***``, ``NRAK-***``, ``xkeysib-***`` etc.) for incident-
response triage. The Mailchimp pattern is unique — no prefix, so the
mask preserves the ``-us<region>`` datacenter suffix.

Structural anchors mirror the scanner regexes exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``xpypi-``, ``foonfp_``, ``Ashpat_`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format reject
  accidental fragments while accepting every real-shape token.

Idempotence: masked forms (``pypi-***``, ``shpat_***``, ``EAAA***``,
``nfp_***``, ``FlyV1 fm1_***``, ``NRAK-***``, ``xkeysib-***``,
``***-us20``) do NOT match any of the new regexes because ``*`` is not
in any body alphabet AND the masked body length (3 chars) is below
every per-family floor (20/27/32/40/50/60/64).

Marker: SENTINEL_SUPPLY_CHAIN_ECOMMERCE_PAAS_OBSERVABILITY_EMAIL_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_SUPPLY_CHAIN_ECOMMERCE_PAAS_OBSERVABILITY_EMAIL_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_SUPPLY_CHAIN_ECOMMERCE_PAAS_OBSERVABILITY_EMAIL_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for PyPI (pypi-), Shopify "
    "(shpat_/shpss_/shppa_/shpca_), WooCommerce (ck_/cs_), Square (EAAA), "
    "Netlify (nfp_), Render (rnd_), Fly.io (FlyV1 fm1_/fm2_/fo1_), New Relic "
    "(NRAK-/NRRA-/NRII-), Brevo (xkeysib-), and Mailchimp (<32hex>-us<region>) "
    "token families that the scanner's _KNOWN_TOKENS already detects in "
    "committed source files. Bare tokens in plain log text, JSON values with "
    "non-sensitive keys, URL paths / query strings, and exception messages "
    "slipped past all key/header/URL-credential masking patterns and leaked "
    "verbatim into operator log streams and the public docs/feed_health.json "
    "artefact."
)


# ---------------------------------------------------------------------------
# Canonical real-shape token fixtures, one per scanner-detected prefix.
# Each body uses a mixed alphabet that exercises the regex's full character
# class so partial-class bypasses cannot mask a regex bug as a passing test.
# ---------------------------------------------------------------------------


def _body_extended(length: int) -> str:
    """Deterministic body of exactly ``length`` chars across the extended
    ``[A-Za-z0-9_-]`` alphabet."""
    chunk = "Aa1B-c_D"  # 8-char cycle covering upper/lower/digit/dash/underscore
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_base64url_eq(length: int) -> str:
    """Deterministic body of exactly ``length`` chars across the base64url+=
    alphabet ``[A-Za-z0-9_=-]``."""
    chunk = "Aa1B-c_D=2"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_alnum(length: int) -> str:
    """Deterministic body of exactly ``length`` chars across pure
    alphanumeric ``[A-Za-z0-9]``."""
    chunk = "Aa1Bb2Cc3"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_hex_lower(length: int) -> str:
    """Deterministic body of ``length`` lowercase hex chars."""
    chunk = "0123456789abcdef"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_hex_mixed(length: int) -> str:
    """Deterministic body of ``length`` mixed-case hex chars ``[a-fA-F0-9]``."""
    chunk = "0123456789aAbBcCdDeEfF"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_upper_alnum(length: int) -> str:
    """Deterministic body of ``length`` uppercase alphanumeric chars ``[A-Z0-9]``."""
    chunk = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return (chunk * (length // len(chunk) + 1))[:length]


# PyPI API Token: ``pypi-<20+ from [A-Za-z0-9_-]>``.
_PYPI = "pypi-" + _body_extended(40)

# Brevo API Key: two-part ``xkeysib-<64 hex>-<16 alnum>``.
_BREVO = "xkeysib-" + _body_hex_lower(64) + "-" + _body_alnum(16)

# Netlify Personal Access Token: ``nfp_<40+ alnum>``.
_NETLIFY = "nfp_" + _body_alnum(45)

# Render API Key: ``rnd_<40+ from [A-Za-z0-9_-]>``.
_RENDER = "rnd_" + _body_extended(48)

# New Relic family.
_NEW_RELIC_NRAK = "NRAK-" + _body_upper_alnum(27)
_NEW_RELIC_NRRA = "NRRA-" + _body_hex_mixed(40)
_NEW_RELIC_NRII = "NRII-" + _body_hex_mixed(32)

# Fly.io API Token: literal space, three macaroon discriminators.
_FLYIO_FM1 = "FlyV1 fm1_" + _body_base64url_eq(56)
_FLYIO_FM2 = "FlyV1 fm2_" + _body_base64url_eq(56)
_FLYIO_FO1 = "FlyV1 fo1_" + _body_base64url_eq(56)

# Square Access Token: ``EAAA<60+ from [A-Za-z0-9_-]>``.
_SQUARE = "EAAA" + _body_extended(64)

# Shopify family — strict 32 mixed-case hex bodies.
_SHOPIFY_SHPAT = "shpat_" + _body_hex_mixed(32)
_SHOPIFY_SHPSS = "shpss_" + _body_hex_mixed(32)
_SHOPIFY_SHPPA = "shppa_" + _body_hex_mixed(32)
_SHOPIFY_SHPCA = "shpca_" + _body_hex_mixed(32)

# WooCommerce Consumer Key/Secret: ``ck_<32+ alnum>``, ``cs_<32+ alnum>``.
_WOOCOM_CK = "ck_" + _body_alnum(36)
_WOOCOM_CS = "cs_" + _body_alnum(36)

# Mailchimp API Key: ``<32 lowercase hex>-us<region>``.
_MAILCHIMP = _body_hex_lower(32) + "-us20"


# Sanity checks: fixtures match scanner regex floor exactly.
assert _PYPI.startswith("pypi-") and len(_PYPI) - len("pypi-") >= 20
assert _BREVO.startswith("xkeysib-")
brevo_parts = _BREVO[len("xkeysib-"):].split("-")
assert len(brevo_parts) == 2
assert len(brevo_parts[0]) == 64 and all(c in "0123456789abcdef" for c in brevo_parts[0])
assert len(brevo_parts[1]) == 16
assert _NETLIFY.startswith("nfp_") and len(_NETLIFY) - len("nfp_") >= 40
assert _RENDER.startswith("rnd_") and len(_RENDER) - len("rnd_") >= 40
assert _NEW_RELIC_NRAK.startswith("NRAK-") and len(_NEW_RELIC_NRAK) - len("NRAK-") == 27
assert _NEW_RELIC_NRRA.startswith("NRRA-") and len(_NEW_RELIC_NRRA) - len("NRRA-") == 40
assert _NEW_RELIC_NRII.startswith("NRII-") and len(_NEW_RELIC_NRII) - len("NRII-") == 32
for tok, disc in (
    (_FLYIO_FM1, "fm1"),
    (_FLYIO_FM2, "fm2"),
    (_FLYIO_FO1, "fo1"),
):
    assert tok.startswith(f"FlyV1 {disc}_")
    assert len(tok) - len(f"FlyV1 {disc}_") >= 50
assert _SQUARE.startswith("EAAA") and len(_SQUARE) - len("EAAA") >= 60
for tok, prefix in (
    (_SHOPIFY_SHPAT, "shpat_"),
    (_SHOPIFY_SHPSS, "shpss_"),
    (_SHOPIFY_SHPPA, "shppa_"),
    (_SHOPIFY_SHPCA, "shpca_"),
):
    assert tok.startswith(prefix) and len(tok) - len(prefix) == 32
assert _WOOCOM_CK.startswith("ck_") and len(_WOOCOM_CK) - len("ck_") >= 32
assert _WOOCOM_CS.startswith("cs_") and len(_WOOCOM_CS) - len("cs_") >= 32
mc_parts = _MAILCHIMP.split("-")
assert len(mc_parts) == 2
assert len(mc_parts[0]) == 32 and all(c in "0123456789abcdef" for c in mc_parts[0])
assert mc_parts[1].startswith("us") and mc_parts[1][2:].isdigit()


# Group fixtures by tier; mask format `expected_mask` is the exact
# expected substring in the masked output.
_SUPPLY_CHAIN_TOKENS = [
    (_PYPI, "pypi-***"),
]

_ECOMMERCE_TOKENS = [
    (_SHOPIFY_SHPAT, "shpat_***"),
    (_SHOPIFY_SHPSS, "shpss_***"),
    (_SHOPIFY_SHPPA, "shppa_***"),
    (_SHOPIFY_SHPCA, "shpca_***"),
    (_WOOCOM_CK, "ck_***"),
    (_WOOCOM_CS, "cs_***"),
    (_SQUARE, "EAAA***"),
]

_PAAS_TOKENS = [
    (_NETLIFY, "nfp_***"),
    (_RENDER, "rnd_***"),
    (_FLYIO_FM1, "FlyV1 fm1_***"),
    (_FLYIO_FM2, "FlyV1 fm2_***"),
    (_FLYIO_FO1, "FlyV1 fo1_***"),
]

_OBSERVABILITY_TOKENS = [
    (_NEW_RELIC_NRAK, "NRAK-***"),
    (_NEW_RELIC_NRRA, "NRRA-***"),
    (_NEW_RELIC_NRII, "NRII-***"),
]

_EMAIL_TOKENS = [
    (_BREVO, "xkeysib-***"),
    (_MAILCHIMP, "***-us20"),
]

_ALL_TOKENS = (
    _SUPPLY_CHAIN_TOKENS
    + _ECOMMERCE_TOKENS
    + _PAAS_TOKENS
    + _OBSERVABILITY_TOKENS
    + _EMAIL_TOKENS
)


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner DOES detect these tokens in committed
# source. Proves the divergence between scanner detection and log-
# sanitisation that this round closes. If the scanner ever drops one of
# these prefixes, this test FAILS first (loud) — preventing silent
# drift in the opposite direction.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,_expected_mask", _ALL_TOKENS)
def test_drift_premise_scanner_detects_token(
    token: str, _expected_mask: str
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


@pytest.mark.parametrize("token,expected_mask", _ALL_TOKENS)
def test_token_in_plain_log_line_is_masked(
    token: str, expected_mask: str
) -> None:
    """Bare token in plain log text MUST be masked by
    ``sanitize_log_message`` — pre-fix this leaked verbatim through the
    operator-log sink and the public ``docs/feed_health.json`` artefact."""
    log_line = f"Provider API returned 401: invalid credential {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token leaked through sanitize_log_message: "
        f"{SENTINEL_SUPPLY_CHAIN_ECOMMERCE_PAAS_OBSERVABILITY_EMAIL_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert expected_mask in result, (
        f"Mask MUST preserve issuer-attribution form '{expected_mask}' "
        f"for incident-response triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_mask", _ALL_TOKENS)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_token_in_json_value_is_masked(
    token: str, expected_mask: str, key_name: str
) -> None:
    """Token in JSON value with a NON-sensitive key name MUST be
    masked — pre-fix the JSON-key sensitive-name regex missed keys
    like ``data`` / ``payload`` / ``response_body`` / ``message`` and
    the token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token in JSON value with non-sensitive key '{key_name}' "
        f"leaked through sanitize_log_message"
    )
    assert expected_mask in result


# ---------------------------------------------------------------------------
# (3) URL path / query string with non-sensitive parameter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_mask", _ALL_TOKENS)
def test_token_in_url_query_with_non_sensitive_param_is_masked(
    token: str, expected_mask: str
) -> None:
    """Token in URL query string with a NON-sensitive parameter name
    (``ref`` / ``commit_sha`` / ``q``) MUST be masked — pre-fix the
    URL credential regex required the credential to appear before
    ``@``; query-string and path-embedded tokens slipped past."""
    log_line = f"GET /api/foo/bar?ref={token} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert expected_mask in result


def test_square_token_in_url_path_segment_is_masked() -> None:
    """Square access token embedded in URL path segment (NOT
    ``user:pass@`` form) MUST be masked — covers the path-embedded
    leak surface for the highest payment-fraud-impact token in this
    round."""
    log_line = f"GET /api/internal/audit/{_SQUARE}/details 200"
    result = sanitize_log_message(log_line)
    assert _SQUARE not in result
    assert "EAAA***" in result


def test_flyio_token_in_url_path_segment_is_masked() -> None:
    """Fly.io macaroon token embedded in a URL path segment MUST be
    masked. The literal-space shape (``FlyV1 fm1_``) is the canonical
    "non-prefix-anchored" issuer form requiring special masker
    structure — without the literal space the body regex would NOT
    match."""
    log_line = f"GET /api/internal/audit/{_FLYIO_FM1}/details 200"
    result = sanitize_log_message(log_line)
    assert _FLYIO_FM1 not in result
    assert "FlyV1 fm1_***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_mask", _ALL_TOKENS)
def test_token_through_sanitize_exception_msg(
    token: str, expected_mask: str
) -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask vendor tokens."""
    exc_msg = f"HTTPError: 401 Unauthorized — credential {token} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert expected_mask in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_pypi_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_PYPI}"
    result = sanitize_log_arg(arg)
    assert _PYPI not in result
    assert "pypi-***" in result


def test_sanitize_log_arg_masks_shopify_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation.
    Uses a NON-sensitive attribute name (``audit``) so the value-shape
    mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_SHOPIFY_SHPAT})"

    result = sanitize_log_arg(_Wrapper())
    assert _SHOPIFY_SHPAT not in result, (
        "Shopify Admin API token leaked through sanitize_log_arg"
    )
    assert "shpat_***" in result


def test_sanitize_log_arg_masks_mailchimp_in_object_repr() -> None:
    """Mailchimp's NO-prefix structural pattern is the canonical
    test of the value-shape mask defence — the entire body is a
    32-char hex span with no issuer keyword, so this is the most-
    structural-only mask in the family."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_MAILCHIMP})"

    result = sanitize_log_arg(_Wrapper())
    assert _MAILCHIMP not in result, (
        "Mailchimp API key leaked through sanitize_log_arg"
    )
    assert "***-us20" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Short fragments — body below each per-family floor
        "pypi-short",  # body < 20
        "xkeysib-tooshort",
        "nfp_only10chrs",  # body < 40
        "rnd_short",
        "NRAK-short",  # body < 27
        "NRRA-short",  # body < 40
        "NRII-tooshort",  # body < 32
        "FlyV1 fm1_short",  # body < 50
        "EAAA-tooshort",  # body < 60
        "shpat_short",  # body < 32
        "shpss_short",
        "shppa_short",
        "shpca_short",
        "ck_short",  # body < 32
        "cs_short",
        "FlyV1 invaliddisc_" + "A" * 50,  # invalid macaroon discriminator
        # Mid-identifier collisions (lookbehind prevents these)
        "Xpypi-" + "A" * 22,
        "0xkeysib-" + "a" * 64 + "-" + "A" * 16,
        "Anfp_" + "A" * 40,
        "Frnd_" + "A" * 40,
        "9NRAK-" + "A" * 27,
        "WNRRA-" + "a" * 40,
        "VNRII-" + "a" * 32,
        # FlyV1 has a literal space — embedding inside an identifier
        # MUST be rejected by the lookbehind. The "X" prefix in
        # "XFlyV1 fm1_..." is rejected by the lookbehind on the
        # ``FlyV1`` keyword.
        "XFlyV1 fm1_" + "A" * 50,
        "1EAAA" + "A" * 60,
        "Ashpat_" + "a" * 32,
        "Mck_" + "A" * 35,
        # Mailchimp: lookbehind rejects ``[A-Za-z0-9]`` before the hex span.
        "0" + "a" * 32 + "-us20",
        "X" + "a" * 32 + "-us20",
        # Body-alphabet mismatches reject false positives at the regex level.
        "NRAK-aaaaaaaaaaaaaaaaaaaaaaaaaaa",  # lowercase — NRAK requires [A-Z0-9]
        "shpat_GhIjKlMnOpQrStUvWxYzAbCdEf012345",  # non-hex chars in body — rejected
    ],
)
def test_benign_input_is_not_masked(benign: str) -> None:
    """Negative case: short prefixes, mid-identifier collisions, and
    body-alphabet mismatches MUST NOT be masked. The
    ``(?<![A-Za-z0-9])`` lookbehind plus the body floor and strict
    body alphabet are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive vendor token mask on benign input: "
        f"{benign!r} → {result!r}"
    )


def test_short_pypi_body_is_not_masked() -> None:
    """PyPI body shorter than 20 chars is below the structural floor."""
    short = "pypi-" + "A" * 19
    result = sanitize_log_message(short)
    assert result == short


def test_invalid_flyio_discriminator_is_not_masked() -> None:
    """Fly.io macaroon discriminator MUST be one of ``fm1|fm2|fo1``;
    any other value is below the structural floor."""
    invalid = "FlyV1 zz9_" + _body_base64url_eq(56)
    result = sanitize_log_message(invalid)
    assert result == invalid


def test_mailchimp_invalid_region_suffix_is_not_masked() -> None:
    """Mailchimp datacenter suffix MUST be ``us`` + 1-3 digits. Any
    other suffix is below the structural floor."""
    invalid = _body_hex_lower(32) + "-eu1"  # eu not us
    result = sanitize_log_message(invalid)
    assert result == invalid


# ---------------------------------------------------------------------------
# (7) Idempotence — masked outputs MUST NOT match the new patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "pypi-***",
        "xkeysib-***",
        "nfp_***",
        "rnd_***",
        "NRAK-***",
        "NRRA-***",
        "NRII-***",
        "FlyV1 fm1_***",
        "FlyV1 fm2_***",
        "FlyV1 fo1_***",
        "EAAA***",
        "shpat_***",
        "shpss_***",
        "shppa_***",
        "shpca_***",
        "ck_***",
        "cs_***",
        "***-us20",
    ],
)
def test_masked_form_is_idempotent(masked: str) -> None:
    """Running sanitize_log_message twice MUST be idempotent — the
    masked form (``<prefix>***`` or ``***-us<region>``) MUST NOT
    itself match any of the new regexes. The ``*`` char is outside
    every body alphabet AND the ``***`` length (3 chars) is below
    every per-family body floor."""
    log_line = f"prior IR note: token redacted as {masked}"
    result = sanitize_log_message(log_line)
    assert masked in result, (
        f"Idempotence broken: masked form {masked!r} was further "
        f"modified by sanitize_log_message: {result!r}"
    )


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner and masker must align
# ---------------------------------------------------------------------------


def test_scanner_and_masker_share_supply_chain_ecommerce_paas_family() -> None:
    """**Sibling-alignment invariant.** Every token prefix this round
    masks MUST also appear in the scanner ``_KNOWN_TOKENS``. Any
    future drift (scanner adds a new prefix without a matching mask,
    or vice versa) is surfaced programmatically on the first pytest
    run."""
    scanner_patterns = "\n".join(rx.pattern for rx, _ in _KNOWN_TOKENS)
    # These substrings appear in the canonical scanner regex form for each
    # family — alignment failure indicates one side has dropped detection.
    required_scanner_substrings = [
        "pypi-",
        "xkeysib-",
        "nfp_",
        "rnd_",
        "NRAK-",
        "NRRA-",
        "NRII-",
        "FlyV1 ",
        "EAAA",
        "shpat_",
        "shpss_",
        "shppa_",
        "shpca_",
        "ck_",
        "cs_",
        "us[0-9]",  # Mailchimp's structural suffix
    ]
    for needle in required_scanner_substrings:
        assert needle in scanner_patterns, (
            f"Scanner missing pattern fragment '{needle}' — "
            f"sanitize_log_message has a mask but the scanner does not "
            f"detect it (reverse drift). Add the corresponding regex to "
            f"src/utils/secret_scanner.py:_KNOWN_TOKENS or update this test."
        )

    # Confirm sanitize_log_message masks each token end-to-end.
    for token, expected_mask in _ALL_TOKENS:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"Token {token[:30]!r}... missing from sanitize_log_message "
            f"mask family — log-sanitisation drift vs. scanner _KNOWN_TOKENS"
        )
        assert expected_mask in result, (
            f"Mask MUST preserve issuer attribution as '{expected_mask}' "
            f"for incident-response triage"
        )
