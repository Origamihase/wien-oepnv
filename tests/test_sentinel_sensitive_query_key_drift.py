"""Sentinel PoC: ``_SENSITIVE_QUERY_KEYS`` misses five high-impact
query parameter names that leak through both ``_sanitize_url_for_error``
(URLs in operator error-log streams) AND ``_strip_sensitive_params``
(URLs carried through cross-origin redirect chains to potentially-
malicious targets):

1. **SAMLArt** (SAML 2.0 Artifact, RFC 6595 / OASIS SAML 2.0 §3.6.4) —
   a 5-minute-validity bearer credential pointing at a stored SAML
   assertion on the IdP's Artifact Resolution Service (ARS). A leaked
   ``SAMLArt`` within its validity window can be resolved to the full
   ``SAMLResponse`` (the user's authenticated identity assertion) via
   a single back-channel call from the attacker to the IdP. Blast
   radius: full user authentication for any IdP-trusted SP.

2. **RelayState** (SAML 2.0 §3.4.4) — SP-supplied state preservation
   string carried through the SSO flow. Often contains the
   post-authentication landing URL (``?dest=/admin/dashboard``) or
   serialised session context. Not a credential per se, but a context
   leak that enables targeted phishing (the attacker learns the user's
   pre-auth destination).

3. **`csrf` / `_csrf`** — bare CSRF token names that don't contain the
   ``token`` substring already in ``_SENSITIVE_KEY_SUBSTRINGS``. The
   canonical leak shape is Spring Security's ``?_csrf=<token>`` GET-
   based protection. After ``_normalize_key`` strips the leading
   underscore the parameter becomes bare ``csrf`` — and matches neither
   the exact set nor the ``token``-prefix-only substring set. Replayable
   within session lifetime (minutes to hours).

4. **`xsrf` / `XSRF-TOKEN`** — Angular's default XSRF cookie / header
   name. Bare ``xsrf`` (without the ``-token`` suffix) bypasses the
   ``token`` substring match. The cookie / param form
   ``?xsrf=<value>`` is rare but appears in some legacy AJAX bootstrap
   flows.

5. **`_wpnonce`** (WordPress) — WP's per-action nonce protection,
   carried in GET parameters for state-changing actions (delete-post,
   approve-comment, install-plugin). The normalised form is
   ``wpnonce``, which doesn't contain any current substring match.
   Validity: typically 24 hours (WP's default ``DAY_IN_SECONDS / 2``
   lifetime). A leaked ``_wpnonce`` within the window enables arbitrary
   WP action replay if the attacker also has session cookie access.

The gap exists across BOTH consuming functions:

* ``_sanitize_url_for_error(url)`` — called from every operator
  error-log emission to redact sensitive query params before display.
  Pre-fix the credentials surface verbatim in CI logs, GitHub PR
  comments, Datadog / Splunk / ELK ingest, and pre-commit hook
  output.

* ``_strip_sensitive_params(url)`` — called BEFORE following a
  cross-origin redirect (see ``_process_redirect`` at
  ``src/utils/http.py:1817``). Pre-fix the credentials are CARRIED
  THROUGH to a potentially-malicious redirect target, where the
  target host gains access to the user's SAML / CSRF / WP context
  for the credential's validity window. This is the highest-severity
  leak path — the malicious target is by definition adversarial.

Threat model
------------

The five-credential class shares the same root cause: their normalised
key names (``samlart``, ``relaystate``, ``csrf``, ``xsrf``, ``wpnonce``)
are NOT in the canonical ``_SENSITIVE_QUERY_KEYS`` exact-match set, and
they don't contain any substring from ``_SENSITIVE_KEY_SUBSTRINGS``
(the ``token`` substring catches ``csrf_token`` and ``csrfmiddlewaretoken``
but not bare ``csrf`` / ``xsrf``). The ``_normalize_key`` helper strips
non-alphanumeric characters before matching, so ``SAMLArt``, ``_csrf``,
``XSRF-TOKEN`` and ``_wpnonce`` all reduce to their bare normalised
form — meaning a single exact-match entry per key covers every
real-world variant.

Severity escalation per credential class:

* **SAMLArt: HIGH** — full SAML assertion retrieval via the Artifact
  Resolution Service within the 5-minute validity window. The
  resolved assertion is the user's identity, signed by the IdP and
  trusted by every SP in the SSO federation. An attacker who can
  trigger a redirect via ``_strip_sensitive_params`` AND has a
  malicious-target listener gains full user authentication on every
  IdP-trusted SP for the assertion's lifetime (typically 1 hour).

* **csrf / xsrf: MEDIUM-HIGH** — replayable within session lifetime
  (typically 30 minutes to 8 hours, framework- and config-dependent).
  Enables state-changing-action replay if the attacker also has
  session cookie access (e.g., via a separate XSS vector or
  cookie-jar-share misconfiguration).

* **RelayState: MEDIUM** — context leak (post-auth destination URL,
  often containing tenant identifiers, user IDs, or feature flags).
  Not a credential but high-value reconnaissance.

* **_wpnonce: MEDIUM** — typically 24-hour validity. Enables WP
  state-change replay (delete-post, install-plugin, modify-user) if
  paired with session cookie access. Mitigated by WP's nonce
  rotation policy on plugin / theme / core updates.

Real-world emission patterns
----------------------------

- **SP-initiated SSO logs**: ``SAMLArt`` and ``RelayState`` land in
  the SP's request logs when the user hits the ACS endpoint
  (``POST /saml/acs?SAMLArt=...&RelayState=...``).
- **CSRF protection logs**: ``_csrf`` lands in nginx / Apache access
  logs and application error logs when validation fails.
- **WordPress error logs**: ``_wpnonce`` lands in WP-CLI logs,
  WP-DEBUG logs, and operator audit trails.
- **Cross-origin redirect chains**: any redirect from an SP / WP
  site to a third-party domain carries the original query string
  (including sensitive params) to the redirect target unless
  explicitly stripped. The redirect target's access logs then
  contain the credentials.
- **Pre-commit hook fixture failures**: when a test fixture contains
  a realistic SAML / CSRF URL, the pre-commit secret scanner emits
  the URL verbatim in the failure output for operator triage.

Fix
---

Add the five missing normalised key names as exact matches in
``_SENSITIVE_QUERY_KEYS``::

    _SENSITIVE_QUERY_KEYS = frozenset({
        # ... existing entries ...
        "samlart",        # SAML 2.0 Artifact (5-min ARS-resolvable)
        "relaystate",     # SAML 2.0 SP state preservation
        "csrf",           # Bare CSRF (Spring _csrf normalised)
        "xsrf",           # Bare XSRF (Angular XSRF-TOKEN cookie)
        "wpnonce",        # WordPress _wpnonce (per-action protection)
    })

Both consuming functions (``_sanitize_url_for_error`` and
``_strip_sensitive_params``) iterate ``_SENSITIVE_QUERY_KEYS`` directly,
so the single set update closes BOTH the error-log redaction path
AND the cross-origin redirect-strip path with zero additional code
changes.

Marker: SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_url_for_error, _strip_sensitive_params


SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT = (
    "_SENSITIVE_QUERY_KEYS missing SAML 2.0 Artifact / RelayState + "
    "bare csrf/xsrf + WordPress _wpnonce — leaks across "
    "_sanitize_url_for_error AND _strip_sensitive_params"
)


# Realistic credential bodies for PoC tests. Each is a plausible
# real-world leak shape per the threat model:
#
#  * ``_SAMLART_BODY`` — 58-char base64-encoded SAML 2.0 Artifact per
#    OASIS SAML 2.0 §3.6.4 (the canonical Type 4 artifact shape: 2 bytes
#    TypeCode + 2 bytes EndpointIndex + 20 bytes SourceID + 20 bytes
#    MessageHandle = 44 bytes raw = 60 base64 chars including padding).
#  * ``_CSRF_BODY`` — 36-char Spring Security CSRF token (UUID v4
#    canonical shape with dashes stripped).
#  * ``_XSRF_BODY`` — 24-char Angular XSRF-TOKEN cookie value.
#  * ``_WPNONCE_BODY`` — 10-char WordPress nonce (the canonical WP
#    nonce length: ``substr(wp_hash($action), -10, 10)``).

_SAMLART_BODY = "AAQAACK4Gj1uFBjQqwbeQk5jeSrXgQAOEYRwsZA1J3GibE5oWyA89uVbiNI"
assert len(_SAMLART_BODY) >= 40
_CSRF_BODY = "abc123def456ghi789jkl012mno345pqr678"
assert len(_CSRF_BODY) >= 24
_XSRF_BODY = "AbCdEfGhIjKlMnOpQrStUvWx"
assert len(_XSRF_BODY) >= 16
_WPNONCE_BODY = "a1b2c3d4e5"
assert len(_WPNONCE_BODY) >= 8
_RELAYSTATE_BODY = "https%3A%2F%2Fapp.example.com%2Fadmin%2Fdashboard"


# ---------------------------------------------------------------------------
# (1) Error-log redaction PoCs (``_sanitize_url_for_error``): the
#     credentials must NOT appear verbatim in the sanitised URL —
#     each sensitive query param's value must be replaced with ``***``
#     (URL-encoded as ``%2A%2A%2A``).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value,key_label",
    [
        ("SAMLArt", _SAMLART_BODY, "SAML 2.0 Artifact"),
        ("RelayState", _RELAYSTATE_BODY, "SAML 2.0 RelayState"),
        ("_csrf", _CSRF_BODY, "Spring Security _csrf"),
        ("csrf", _CSRF_BODY, "bare csrf"),
        ("CSRF", _CSRF_BODY, "uppercase CSRF"),
        ("xsrf", _XSRF_BODY, "bare xsrf"),
        ("XSRF-TOKEN", _XSRF_BODY, "Angular XSRF-TOKEN"),
        ("_wpnonce", _WPNONCE_BODY, "WordPress _wpnonce"),
        ("wpnonce", _WPNONCE_BODY, "bare wpnonce"),
    ],
)
def test_sanitize_url_redacts_saml_csrf_wp_params(
    key: str, value: str, key_label: str
) -> None:
    """Every variant of SAMLArt / RelayState / csrf / xsrf / wpnonce
    must be redacted by ``_sanitize_url_for_error``. Pre-fix the raw
    credential body surfaces verbatim in operator error-log streams
    (CI logs, GitHub PR comments, Datadog / Splunk / ELK ingest,
    pre-commit hook output).

    ``_normalize_key`` strips non-alphanumeric characters before
    matching, so ``SAMLArt``, ``_csrf``, ``XSRF-TOKEN``, ``_wpnonce``
    all reduce to their bare normalised form. A single exact-match
    entry per key covers every real-world casing / separator variant.
    """
    url = f"https://example.com/path?{key}={value}&other=visible"
    sanitized = _sanitize_url_for_error(url)

    assert value not in sanitized, (
        f"{key_label} leaked verbatim into error log: param={key!r} "
        f"value={value!r} sanitised_url={sanitized!r}. "
        f"_SENSITIVE_QUERY_KEYS needs the normalised key name as an "
        f"exact match. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    # The benign ``other=visible`` param must survive — only sensitive
    # params get redacted (regression guard against over-redaction).
    assert "other=visible" in sanitized, (
        f"Over-redaction: benign param ``other=visible`` was stripped "
        f"alongside the sensitive {key_label}. sanitised_url="
        f"{sanitized!r}. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) Cross-origin redirect-strip PoCs (``_strip_sensitive_params``):
#     the credentials must be COMPLETELY REMOVED from the redirect
#     target URL (not just redacted) so they are not carried through
#     to a potentially-malicious target host.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value,key_label",
    [
        ("SAMLArt", _SAMLART_BODY, "SAML 2.0 Artifact"),
        ("RelayState", _RELAYSTATE_BODY, "SAML 2.0 RelayState"),
        ("_csrf", _CSRF_BODY, "Spring Security _csrf"),
        ("csrf", _CSRF_BODY, "bare csrf"),
        ("xsrf", _XSRF_BODY, "bare xsrf"),
        ("XSRF-TOKEN", _XSRF_BODY, "Angular XSRF-TOKEN"),
        ("_wpnonce", _WPNONCE_BODY, "WordPress _wpnonce"),
    ],
)
def test_strip_sensitive_params_drops_saml_csrf_wp(
    key: str, value: str, key_label: str
) -> None:
    """The redirect-strip path must REMOVE sensitive params entirely
    from the URL carried to the redirect target. This is the highest-
    severity leak path: an attacker controlling the redirect target
    (DNS rebinding, malicious 3xx, compromised CDN, hostile open
    redirect) would otherwise see the SAML / CSRF / WP credential
    in the next-hop request log.

    Pre-fix every credential class leaked through the redirect chain.
    Post-fix only the benign ``other`` param survives.
    """
    url = f"https://target.example.com/?{key}={value}&other=visible"
    stripped = _strip_sensitive_params(url)

    assert value not in stripped, (
        f"{key_label} CARRIED THROUGH cross-origin redirect: param="
        f"{key!r} value={value!r} stripped_url={stripped!r}. The "
        f"sensitive credential lands in the redirect target's access "
        f"logs. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    # In ``_strip_sensitive_params`` mode, sensitive keys are REMOVED
    # entirely (not redacted with ``***``). The benign ``other`` param
    # must survive.
    assert "other=visible" in stripped, (
        f"Over-stripping: benign param ``other=visible`` was removed "
        f"alongside the sensitive {key_label}. stripped_url="
        f"{stripped!r}. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    # The sensitive key itself must NOT appear (different from the
    # error-log redaction which preserves the key with a redacted
    # value).
    assert f"{key}=" not in stripped, (
        f"Sensitive key name {key!r} still present in stripped URL: "
        f"stripped_url={stripped!r}. ``_strip_sensitive_params`` "
        f"should REMOVE the key entirely (not redact). "
        f"({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Combined PoC: a realistic SP-initiated SSO error-log line
#     carries SAMLArt + RelayState side by side. Both must be redacted
#     in error logs AND stripped in cross-origin redirects.
# ---------------------------------------------------------------------------


def test_combined_saml_artifact_relaystate_redacted_in_error_log() -> None:
    """A real-world SP-initiated SSO error log carries the canonical
    Authorization header form ``?SAMLArt=...&RelayState=...``. Both
    params must be redacted simultaneously.
    """
    url = (
        f"https://sp.example.com/saml/acs"
        f"?SAMLArt={_SAMLART_BODY}"
        f"&RelayState={_RELAYSTATE_BODY}"
    )
    sanitized = _sanitize_url_for_error(url)

    assert _SAMLART_BODY not in sanitized, (
        f"SAMLArt leaked verbatim in combined SSO log: "
        f"sanitised_url={sanitized!r}. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    assert _RELAYSTATE_BODY not in sanitized, (
        f"RelayState leaked verbatim in combined SSO log: "
        f"sanitised_url={sanitized!r}. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )


def test_combined_saml_artifact_relaystate_stripped_on_redirect() -> None:
    """Cross-origin redirect of an SP-ACS URL must remove BOTH SAMLArt
    AND RelayState before following the redirect to prevent the
    target from receiving the SSO context.
    """
    url = (
        f"https://attacker.example.com/"
        f"?SAMLArt={_SAMLART_BODY}"
        f"&RelayState={_RELAYSTATE_BODY}"
    )
    stripped = _strip_sensitive_params(url)

    assert _SAMLART_BODY not in stripped, (
        f"SAMLArt carried through to attacker via redirect: "
        f"stripped_url={stripped!r}. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    assert _RELAYSTATE_BODY not in stripped, (
        f"RelayState carried through to attacker via redirect: "
        f"stripped_url={stripped!r}. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Regression guards: existing redaction behaviour for the original
#     sensitive query keys (token / secret / password / session / etc.)
#     must continue to work after adding the new entries. The fix is
#     additive — no existing entry is changed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        ("token", "AbCdEfGh1234567890XYZ"),
        ("access_token", "OAuth2BearerToken1234567890"),
        ("password", "MyP@ssw0rd!"),
        ("client_secret", "ClientSecretValue1234567890"),
        ("api_key", "ApiKeyValue1234567890"),
        ("session_id", "SessionIdValue1234"),
        ("authorization", "Bearer AbCdEfGh1234567890"),
        ("signature", "Signature1234567890hex"),
    ],
)
def test_existing_sensitive_query_keys_still_redacted(
    key: str, value: str
) -> None:
    """Adding new entries to ``_SENSITIVE_QUERY_KEYS`` must NOT break
    existing redaction. Every entry in the prior canonical set must
    continue to produce a redacted output. Regression guard against
    accidental removal during the set-extension diff.
    """
    url = f"https://example.com/?{key}={value}&other=visible"
    sanitized = _sanitize_url_for_error(url)

    assert value not in sanitized, (
        f"Regression: existing sensitive key {key!r} no longer "
        f"redacted after adding SAML/CSRF/WP entries. "
        f"sanitised_url={sanitized!r}. "
        f"({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    assert "other=visible" in sanitized


# ---------------------------------------------------------------------------
# (5) Negative cases: ensure the new entries do NOT over-redact benign
#     URLs that happen to contain similar but distinct query parameter
#     names. The exact-match-on-normalised-key contract narrows the
#     false-positive surface to keys whose normalised form is exactly
#     one of the new entries.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # ``state`` is already an exact match in _SENSITIVE_QUERY_KEYS, but
        # ``postal_state`` should NOT match (normalised: postalstate — no
        # substring match either).
        "https://example.com/?postal_state=NY",
        # ``sample`` does not match ``samlart`` (different normalised form).
        "https://example.com/?sample=test",
        # ``related`` is benign — normalised ``related`` is not in the
        # exact set nor in any substring.
        "https://example.com/?related=item123",
        # ``crsf`` (typo) — normalised same as ``crsf``, not ``csrf``.
        # If this matched it'd be over-redaction; the exact-match contract
        # prevents that.
        "https://example.com/?crsf=value123456",
        # ``serif`` doesn't contain xsrf as substring (xsrf is not in the
        # substrings list — only added as exact). So serif must not match.
        "https://example.com/?serif=true",
    ],
)
def test_new_entries_do_not_over_redact_benign_urls(url: str) -> None:
    """The exact-match-on-normalised-key contract narrows the false-
    positive surface. Benign URLs with similar-but-distinct query
    parameter names (``postal_state``, ``sample``, ``related``,
    typo'd ``crsf``, ``serif``) must NOT have their query values
    redacted.
    """
    sanitized = _sanitize_url_for_error(url)
    # Extract the original value
    original_value = url.rsplit("=", 1)[-1]
    assert original_value in sanitized, (
        f"Over-redaction: benign query value {original_value!r} was "
        f"stripped from URL {url!r}. sanitised_url={sanitized!r}. "
        f"({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Membership invariant: the new entries must be in the canonical
#     ``_SENSITIVE_QUERY_KEYS`` set. A future regression that removes
#     any of the entries fails this test immediately.
# ---------------------------------------------------------------------------


def test_new_sensitive_query_keys_present_in_set() -> None:
    """The five new entries (``samlart``, ``relaystate``, ``csrf``,
    ``xsrf``, ``wpnonce``) must be in the canonical
    ``_SENSITIVE_QUERY_KEYS`` set. This invariant pins the
    membership against future regressions.
    """
    from src.utils.http import _SENSITIVE_QUERY_KEYS

    required_entries = {"samlart", "relaystate", "csrf", "xsrf", "wpnonce"}
    missing = required_entries - _SENSITIVE_QUERY_KEYS
    assert not missing, (
        f"Required entries missing from _SENSITIVE_QUERY_KEYS: "
        f"{missing!r}. The five-entry batch closes the SAML 2.0 "
        f"Artifact / RelayState + bare csrf/xsrf + WordPress "
        f"_wpnonce drift. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Header equivalents (X-CSRF-Token, X-XSRF-TOKEN) already covered
#     via _is_sensitive_header — regression guard that the header path
#     still works after this round's query-param-only update.
# ---------------------------------------------------------------------------


def test_existing_csrf_header_redaction_still_works() -> None:
    """The X-CSRF-Token / X-XSRF-TOKEN HTTP header redaction path
    (``_is_sensitive_header``) is independent of this round's
    ``_SENSITIVE_QUERY_KEYS`` update — but the regression guard
    confirms the header path still detects CSRF headers correctly.
    The two paths are complementary: query-param leaks live in URLs,
    header leaks live in HTTP headers; both must be redacted on
    cross-origin redirects."""
    from src.utils.http import _is_sensitive_header

    assert _is_sensitive_header("X-CSRF-Token"), (
        f"Regression: X-CSRF-Token header no longer flagged as "
        f"sensitive. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    assert _is_sensitive_header("X-XSRF-TOKEN"), (
        f"Regression: X-XSRF-TOKEN header no longer flagged as "
        f"sensitive. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
    assert _is_sensitive_header("X-CSRFToken"), (
        f"Regression: X-CSRFToken header no longer flagged as "
        f"sensitive. ({SENTINEL_QUERY_KEY_SAML_CSRF_DRIFT})"
    )
