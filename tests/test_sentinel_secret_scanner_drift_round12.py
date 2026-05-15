"""Sentinel PoC: secret-scanner drift Round 12 — closes the
**adjacent-prefix candidates named-but-deferred by Round 11**: Mailgun
private API keys (``key-<32 hex>``) and Square Access Tokens
(``EAAA<base64 body>``); plus the **Shopify access-token family**
(``shpat_`` Admin API / ``shpss_`` shared secret / ``shppa_`` private
app / ``shpca_`` custom app) as the e-commerce-platform sub-landscape
opened in lockstep with the named candidates.

Round 11 (PR closing GitLab developer-tooling tier + CircleCI) closed
the **CI/CD execution-tier** sub-landscape and explicitly named four
named-but-deferred next-round targets:

  1. **AWS Secret Access Keys** (40-char base64, NO canonical prefix
     — bucket-(b) shape, covered structurally by the entropy fallback).
  2. **Cloudflare API Tokens** (40-char base64, NO canonical prefix
     — bucket-(b) shape).
  3. **Heroku API keys** (36-char UUID-shape, NO canonical prefix
     — bucket-(b) shape).
  4. **Mailgun private API keys** (``key-<32 hex>`` — adjacent
     **PREFIX** candidate; anchors against the prefix for specific
     attribution).
  5. **Square Access Tokens** (``EAAA<base64 body>`` — adjacent
     **PREFIX** candidate; anchors against the prefix for specific
     attribution).

Targets (1)-(3) are bucket-(b) (no prefix, structural-only detection
via entropy fallback) — they cannot anchor specific attribution and
are deferred indefinitely. Targets (4)-(5) are the adjacent-PREFIX
candidates this round closes via canonical issuer-specific patterns.

Round 12 closes the named-prefix candidates AND opens the e-commerce-
platform sub-landscape by adding the **Shopify access-token family**
(4 prefixes), which mirrors GitLab's multi-prefix taxonomy and is
structurally the e-commerce-tier counterpart to GitLab's developer-
tooling tier closed in Round 11.

  1. **Mailgun private API key** (``key-<32 hex>``): issued via
     app.mailgun.com/app/account/security/api_keys for full Mailgun
     transactional-mail API access. A leak grants the issuing
     account's full mail-sending capability — the attacker can send
     mail FROM the project's authenticated sending domain (phishing
     amplification leveraging the project's existing SPF/DKIM/DMARC
     authentication), exfiltrate the suppression / bounce / event
     logs (which may carry PII), modify webhook endpoints to
     redirect delivery events, and create new API keys for
     persistence. Pre-fix the entropy fallback's
     ``[A-Za-z0-9+/=_-]{24,}`` regex matches the 32-hex body span
     (the dash is OUTSIDE the entropy alphabet, so the prefix
     ``key-`` is NOT contiguous with the body — only the body span
     is flagged as one generic ``Hochentropischer Token-String``
     finding, losing both the ``key-`` prefix AND the Mailgun-
     specific issuer attribution that incident-response keys off
     (revocation flow at app.mailgun.com).

  2. **Square Access Token** (``EAAA<base64 body>``): issued via
     developer.squareup.com/apps for full Square REST-API access
     (read every customer's payment / catalog / inventory data,
     initiate transactions, refund payments, modify employee
     permissions). Total length 64+ chars (4-char ``EAAA`` prefix +
     60+ char base64url body). The ``EAAA`` prefix is the base64
     encoding of the first 3 bytes of the embedded JSON token
     payload's leading byte sequence (similar to JWT's ``eyJ``);
     it is unambiguous (no other major issuer uses this prefix in
     base64 form) and anchors against the entropy fallback's body
     span. Pre-fix the entropy regex matches the full ``EAAA<body>``
     span (every char in the alphabet) as one generic finding,
     losing the Square-specific attribution. A leak grants the
     issuing seller's full Square dashboard scope — the highest
     leak surface in the payment-processor tier.

  3. **Shopify access-token family** (4 prefixes):

     a. **Admin API access token** (``shpat_<32 hex>``): issued by
        Shopify when a custom app installs into a store via the
        Admin API authentication flow. A leak grants the app's full
        scope: read/write every product, customer, order, inventory
        item, fulfillment, refund, draft order, gift card, and
        webhook configuration in the store. Body alphabet is
        lowercase hex (32 chars).

     b. **Shared secret** (``shpss_<32 hex>``): issued alongside
        ``shpat_`` for HMAC-SHA256 signature verification of webhook
        payloads delivered by Shopify to the app's callback URL. A
        leak lets a network adversary forge webhook payloads that
        the app's signature verification will accept — every webhook-
        driven business logic (order fulfilment, refund processing,
        cart-abandonment automation) can be triggered by an
        attacker. Same 32-hex body shape.

     c. **Private app API access token** (``shppa_<32 hex>``):
        legacy format issued via "Private apps" (deprecated in
        2022-01-01 for new stores but still issued for existing
        installations). A leak grants the private app's scope —
        same blast radius as ``shpat_`` for the apps that haven't
        migrated to the modern custom-app flow.

     d. **Custom app access token** (``shpca_<32 hex>``): issued via
        the "Custom apps" admin flow (post-2022 replacement of the
        ``shppa_`` private apps). Same blast radius as ``shpat_``
        from the per-store custom-app installation flow.

Pre-fix every Shopify token in the family matches the generic high-
entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` as one finding (the
underscore is INSIDE the entropy alphabet, so the full ``shp*_<body>``
span is flagged as ``Hochentropischer Token-String``), losing the
Shopify-specific attribution that determines which admin dashboard
the operator must visit to revoke the token (the four prefixes have
DISTINCT revocation flows — Admin API tokens revoke per-app at
admin.shopify.com/apps/<app>/edit; shared secrets rotate via the
app's webhook-settings page; private-app tokens revoke at
admin.shopify.com/admin/apps/private; custom-app tokens revoke at
admin.shopify.com/admin/settings/apps/development).

Each test below pre-fix would have flagged only the generic high-
entropy fallback for the body span after the prefix; post-fix every
token gets the issuer-specific reason that incident-response playbooks
key off (rotation flow, revocation URL, blast-radius estimate).

Closing-checklist sweep status post-Round-12:

  * **Adjacent-prefix candidates from Round 11:** Mailgun (named,
    closed); Square (named, closed). The remaining three Round-11
    named candidates (AWS Secret Access Keys, Cloudflare API Tokens,
    Heroku API keys) are bucket-(b) (no canonical prefix) and are
    deferred indefinitely — they cannot anchor issuer attribution
    and are caught structurally by the entropy fallback.
  * **E-commerce-platform tier opened:** Shopify family (4 prefixes
    — Admin API ``shpat_``, shared secret ``shpss_``, private app
    ``shppa_``, custom app ``shpca_``). This is the e-commerce-
    tier counterpart to the GitLab developer-tooling tier closed in
    Round 11 — both are multi-prefix issuer taxonomies covered by
    iterative closing-checklist sweeps. Total Shopify prefixes
    enumerated: 4 of 4 documented.
  * **Named-but-deferred next-round candidates:** Magento 2 admin
    integration token (``Bearer <40 hex>`` — no canonical prefix,
    bucket-(b)); BigCommerce API token (``<32-64 base64url>`` — no
    canonical prefix, bucket-(b)); WooCommerce REST API key
    (``ck_<32 hex>`` + ``cs_<32 hex>`` pair — adjacent prefix
    candidate); Mailchimp API key (``<32 hex>-us<region>`` — adjacent
    prefix candidate, with the region suffix as the structural
    disambiguator); SES SMTP credentials (no canonical prefix,
    bucket-(b)); Postmark API key (``<UUID-shape>`` — no canonical
    prefix, bucket-(b)).
"""

from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# 1. Mailgun private API key (``key-<32 hex>``)
# ---------------------------------------------------------------------------
#
# Format: ``key-<32 lowercase hex chars>``. Issued via
# app.mailgun.com/app/account/security/api_keys for full Mailgun
# transactional-mail API access. A leak grants mail-sending FROM the
# project's authenticated domain (phishing amplification), suppression-
# list / bounce / event log exfiltration, and webhook redirection.


def test_secret_scanner_detects_mailgun_private_api_key(tmp_path: Path) -> None:
    """Mailgun private API key: ``key-<32 hex>``.

    Pre-fix the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches
    only the 32-hex body span (the leading dash in ``key-`` is INSIDE
    the entropy alphabet — but ``key-`` by itself is only 4 chars,
    too short for the entropy fallback's 24-char minimum). The body
    span gets flagged as a generic ``Hochentropischer Token-String``
    finding, losing the Mailgun-specific attribution that incident-
    response keys off (revocation at app.mailgun.com).
    """
    file_path = tmp_path / "mailgun_config.py"
    body = "0" * 32
    secret = f"key-{body}"
    assert len(body) == 32
    file_path.write_text(
        f'MAILGUN_API_KEY = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Mailgun private API key"
    reasons = [f.reason for f in findings]
    assert "Mailgun Private API Key gefunden" in reasons, (
        f"Expected Mailgun-specific attribution, got reasons: "
        f"{reasons}. Mailgun private API keys grant the issuing "
        "account's full transactional-mail API scope (mail-send, "
        "suppression-list exfiltration, webhook redirection); precise "
        "attribution accelerates revocation at app.mailgun.com."
    )
    # Mask check: raw value must not leak.
    assert secret not in [f.match for f in findings]


def test_mailgun_api_key_detected_in_env_config(tmp_path: Path) -> None:
    """Mailgun keys commonly appear in ``.env`` / shell-rc files when
    the operator wires the API key into transactional-mail automation.
    The detector must work in unquoted ``KEY=VALUE`` shapes."""
    file_path = tmp_path / "mailgun.env"
    body = "0123456789abcdef0123456789abcdef"  # 32 hex chars
    secret = f"key-{body}"
    assert len(body) == 32
    file_path.write_text(f"MAILGUN_API_KEY={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Mailgun Private API Key gefunden" in reasons, (
        "Mailgun detector must flag keys in unquoted KEY=VALUE shapes"
    )


def test_mailgun_api_key_does_not_flag_short_key_prefix(tmp_path: Path) -> None:
    """Negative case: short ``key-`` strings MUST NOT match the
    Mailgun pattern. The strict 32-hex body length guard prevents
    collision with operator-named placeholders (e.g. ``key-test``,
    ``key-value`` config patterns) and accidentally-truncated tokens.
    """
    file_path = tmp_path / "config.py"
    not_mailgun = "key-test"
    file_path.write_text(f'placeholder = "{not_mailgun}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Mailgun Private API Key gefunden" not in reasons


def test_mailgun_api_key_rejects_non_hex_body(tmp_path: Path) -> None:
    """Negative case: ``key-<32 chars>`` where the body has non-hex
    chars (uppercase letters, ``g-z``) must NOT match the Mailgun
    pattern. Mailgun's canonical format is strictly 32 lowercase hex
    chars; matching a broader alphabet would collide with arbitrary
    operator-supplied ``key-...`` placeholder values."""
    file_path = tmp_path / "config.py"
    # 32-char body with non-hex characters (uppercase + g-z)
    not_mailgun = "key-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    file_path.write_text(
        f'placeholder = "{not_mailgun}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Mailgun Private API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# 2. Square Access Token (``EAAA<base64 body>``)
# ---------------------------------------------------------------------------
#
# Format: ``EAAA<60+ chars from [A-Za-z0-9_-]>``. Issued via
# developer.squareup.com/apps for full Square REST-API access. A leak
# grants the issuing seller's full Square dashboard scope (payment
# initiation, customer / catalog data exfiltration, refund authority).


def test_secret_scanner_detects_square_access_token(tmp_path: Path) -> None:
    """Square Access Token: ``EAAA<60+ chars>``.

    Pre-fix the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches the
    full ``EAAA<body>`` span (every char in the alphabet) as a single
    generic ``Hochentropischer Token-String`` finding, losing the
    Square-specific attribution that incident-response keys off
    (revocation at developer.squareup.com — distinct from every other
    payment-processor's flow, in particular Stripe's
    ``dashboard.stripe.com/apikeys``).
    """
    file_path = tmp_path / "square_config.py"
    body = "A" * 60
    secret = f"EAAA{body}"
    assert len(body) == 60
    file_path.write_text(
        f'SQUARE_ACCESS_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Square Access Token"
    reasons = [f.reason for f in findings]
    assert "Square Access Token gefunden" in reasons, (
        f"Expected Square-specific attribution, got reasons: "
        f"{reasons}. Square Access Tokens grant the issuing seller's "
        "full Square dashboard scope (payment initiation, refund "
        "authority, customer / catalog data exfiltration); precise "
        "attribution accelerates revocation at developer.squareup.com."
    )
    assert secret not in [f.match for f in findings]


def test_square_access_token_with_base64url_body(tmp_path: Path) -> None:
    """Real Square tokens use base64url alphabet (``[A-Za-z0-9_-]``).
    Verify the detector accepts the canonical alphabet including the
    underscore and hyphen body characters that are valid in base64url
    but distinct from the strict-alphanumeric tier."""
    file_path = tmp_path / "square_oauth.py"
    body = "EPpazVhh3-WupQfdmUMtO_qY9X2k" + "Z" * 32
    assert len(body) >= 60
    secret = f"EAAA{body}"
    file_path.write_text(
        f'access_token = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Square Access Token gefunden" in reasons


def test_square_access_token_does_not_flag_short_eaaa_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``EAAA`` strings (e.g. operator
    placeholder values, accidentally-truncated tokens) MUST NOT match
    the Square pattern. The 60-char body lower bound prevents false
    positives against arbitrary ``EAAA``-prefixed strings."""
    file_path = tmp_path / "config.py"
    not_square = "EAAAtest"
    file_path.write_text(f'placeholder = "{not_square}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Square Access Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 3. Shopify Admin API Access Token (``shpat_<32 hex>``)
# ---------------------------------------------------------------------------
#
# Format: ``shpat_<32 lowercase hex chars>``. Issued via custom-app
# OAuth flow. A leak grants the app's full installed scope on the
# store: read/write every product, customer, order, inventory item,
# fulfilment, refund, draft order, gift card, and webhook config.


def test_secret_scanner_detects_shopify_admin_api_token(tmp_path: Path) -> None:
    """Shopify Admin API Access Token: ``shpat_<32 hex>``.

    Pre-fix the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches
    the full ``shpat_<body>`` span (the underscore is INSIDE the
    entropy alphabet) as a single generic ``Hochentropischer Token-
    String`` finding, losing the Shopify-Admin-API-specific
    attribution that determines which admin dashboard the operator
    must visit to revoke the token (admin.shopify.com/apps/<app>/edit
    — distinct from every other vendor's flow).
    """
    file_path = tmp_path / "shopify_admin.py"
    body = "0" * 32
    secret = f"shpat_{body}"
    assert len(body) == 32
    file_path.write_text(
        f'SHOPIFY_ADMIN_API_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Shopify Admin API Access Token"
    reasons = [f.reason for f in findings]
    assert "Shopify Admin API Access Token gefunden" in reasons, (
        f"Expected Shopify-Admin-API-specific attribution, got "
        f"reasons: {reasons}. Shopify Admin API tokens grant full "
        "store admin scope (product / customer / order / inventory "
        "read+write); precise attribution accelerates revocation at "
        "admin.shopify.com/apps/<app>/edit."
    )
    assert secret not in [f.match for f in findings]


def test_shopify_admin_token_detected_in_env(tmp_path: Path) -> None:
    """Shopify Admin tokens commonly appear in ``.env`` files when an
    operator wires the custom-app credentials into a background
    worker / cron job."""
    file_path = tmp_path / "shopify.env"
    body = "abcdef0123456789abcdef0123456789"
    secret = f"shpat_{body}"
    file_path.write_text(f"SHOPIFY_ACCESS_TOKEN={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Shopify Admin API Access Token gefunden" in reasons


def test_shopify_admin_token_does_not_flag_short_shpat_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``shpat_`` strings MUST NOT match the
    Shopify pattern. The strict 32-hex body length guard prevents
    collision with operator-named placeholders."""
    file_path = tmp_path / "config.py"
    not_shpat = "shpat_test"
    file_path.write_text(f'placeholder = "{not_shpat}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Shopify Admin API Access Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 4. Shopify Shared Secret (``shpss_<32 hex>``)
# ---------------------------------------------------------------------------
#
# Format: ``shpss_<32 lowercase hex chars>``. Issued alongside
# ``shpat_`` for HMAC-SHA256 signature verification of webhook
# payloads. A leak lets an attacker forge webhook payloads that the
# app's signature verification will accept.


def test_secret_scanner_detects_shopify_shared_secret(tmp_path: Path) -> None:
    """Shopify Shared Secret: ``shpss_<32 hex>``.

    Pre-fix the entropy fallback matched the full ``shpss_<body>``
    span as a generic ``Hochentropischer Token-String``, losing the
    webhook-forgery-specific attribution. The shared secret class is
    distinct from the admin-API token class: the webhook subsystem
    consumes only the shared secret, so a leaked ``shpss_`` does NOT
    immediately grant admin API access but DOES let an attacker
    trigger every webhook-driven workflow.
    """
    file_path = tmp_path / "shopify_webhook.py"
    body = "0" * 32
    secret = f"shpss_{body}"
    file_path.write_text(
        f'SHOPIFY_WEBHOOK_SECRET = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Shopify Shared Secret"
    reasons = [f.reason for f in findings]
    assert "Shopify Shared Secret gefunden" in reasons, (
        f"Expected Shopify-Shared-Secret-specific attribution, got "
        f"reasons: {reasons}. Shared secrets enable webhook-payload "
        "forgery; precise attribution accelerates rotation via the "
        "app's webhook-settings page."
    )
    assert secret not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# 5. Shopify Private App API Access Token (``shppa_<32 hex>``)
# ---------------------------------------------------------------------------
#
# Format: ``shppa_<32 lowercase hex chars>``. Legacy private-app
# token shape (deprecated for new stores 2022-01-01, still issued
# for existing installations). Same blast radius as ``shpat_``.


def test_secret_scanner_detects_shopify_private_app_token(tmp_path: Path) -> None:
    """Shopify Private App API Access Token: ``shppa_<32 hex>``.

    Legacy format that still grants full private-app scope for
    pre-2022 store installations. Pre-fix the entropy fallback
    matched the full span as a generic finding, losing the
    private-app-specific attribution (revocation flow at
    admin.shopify.com/admin/apps/private — distinct from the
    custom-app revocation flow).
    """
    file_path = tmp_path / "shopify_private_app.py"
    body = "0" * 32
    secret = f"shppa_{body}"
    file_path.write_text(
        f'SHOPIFY_PRIVATE_APP_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Shopify Private App Token"
    reasons = [f.reason for f in findings]
    assert "Shopify Private App Access Token gefunden" in reasons, (
        f"Expected Shopify-Private-App-specific attribution, got "
        f"reasons: {reasons}. Private-app tokens grant the legacy "
        "app's full store scope; precise attribution accelerates "
        "revocation at admin.shopify.com/admin/apps/private."
    )
    assert secret not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# 6. Shopify Custom App Access Token (``shpca_<32 hex>``)
# ---------------------------------------------------------------------------
#
# Format: ``shpca_<32 lowercase hex chars>``. Modern custom-app
# replacement for the deprecated ``shppa_`` private-app flow
# (post-2022). Same blast radius as ``shpat_``.


def test_secret_scanner_detects_shopify_custom_app_token(tmp_path: Path) -> None:
    """Shopify Custom App Access Token: ``shpca_<32 hex>``.

    Modern custom-app format (post-2022 replacement for ``shppa_``).
    Pre-fix the entropy fallback matched the full span as a generic
    finding, losing the custom-app-specific attribution (revocation
    flow at admin.shopify.com/admin/settings/apps/development).
    """
    file_path = tmp_path / "shopify_custom_app.py"
    body = "0" * 32
    secret = f"shpca_{body}"
    file_path.write_text(
        f'SHOPIFY_CUSTOM_APP_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Shopify Custom App Token"
    reasons = [f.reason for f in findings]
    assert "Shopify Custom App Access Token gefunden" in reasons, (
        f"Expected Shopify-Custom-App-specific attribution, got "
        f"reasons: {reasons}. Custom-app tokens grant the modern "
        "app's full store scope; precise attribution accelerates "
        "revocation at admin.shopify.com/admin/settings/apps/development."
    )
    assert secret not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# 7. Cross-vendor boundary regression
# ---------------------------------------------------------------------------


def test_shopify_full_family_patterns_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    """The four Shopify token prefixes (``shpat_``, ``shpss_``,
    ``shppa_``, ``shpca_``) share the ``shp`` ascender but differ
    at the fourth/fifth character. Verify every token is attributed
    to its own issuer-specific reason with no cross-family false
    positives in the post-Round-12 ``_KNOWN_TOKENS`` table.
    """
    file_path = tmp_path / "all_shopify_tokens.py"
    shpat = "shpat_" + ("0" * 32)
    shpss = "shpss_" + ("1" * 32)
    shppa = "shppa_" + ("2" * 32)
    shpca = "shpca_" + ("3" * 32)
    file_path.write_text(
        f'AT = "{shpat}"\n'
        f'SS = "{shpss}"\n'
        f'PA = "{shppa}"\n'
        f'CA = "{shpca}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]

    # Each Shopify token gets its own distinct attribution.
    assert "Shopify Admin API Access Token gefunden" in reasons
    assert "Shopify Shared Secret gefunden" in reasons
    assert "Shopify Private App Access Token gefunden" in reasons
    assert "Shopify Custom App Access Token gefunden" in reasons


def test_round12_patterns_do_not_collide_with_existing_vendors(
    tmp_path: Path,
) -> None:
    """The three Round-12 issuers (Mailgun, Square, Shopify) MUST NOT
    cross-trigger any existing vendor's pattern:

      * Mailgun ``key-<hex>`` vs. Stripe ``whsec_`` / ``sk_`` /
        ``rk_``: different prefix shape.
      * Square ``EAAA<base64>`` vs. JWT ``eyJ<base64>``: both start
        with a base64 prefix but ``EAAA`` decodes to different bytes
        than ``eyJ``, and the patterns are mutually exclusive at
        the prefix level.
      * Shopify ``shp*_<hex>`` vs. GitLab ``gl*-<body>``: different
        separator (``_`` vs. ``-``) and different prefix shape.
    """
    file_path = tmp_path / "mixed_round12.py"
    mailgun = f"key-{'0' * 32}"
    square = f"EAAA{'A' * 60}"
    shpat = f"shpat_{'1' * 32}"
    file_path.write_text(
        f'MAILGUN = "{mailgun}"\n'
        f'SQUARE = "{square}"\n'
        f'SHOPIFY = "{shpat}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]

    assert "Mailgun Private API Key gefunden" in reasons
    assert "Square Access Token gefunden" in reasons
    assert "Shopify Admin API Access Token gefunden" in reasons

    # No cross-vendor false positives.
    assert "JSON Web Token (JWT) gefunden" not in reasons
    assert "Stripe Live Secret Key gefunden" not in reasons
    assert "Stripe Webhook Signing Secret gefunden" not in reasons
    assert "GitLab Personal Access Token gefunden" not in reasons


def test_round12_secrets_are_masked_in_findings(tmp_path: Path) -> None:
    """All Round-12 secret matches must be masked via ``_mask_secret``
    before reporting, so an accidental commit of the scan output does
    NOT leak the underlying token bytes verbatim to subsequent
    consumers (CI logs, alert pages, PR comments). Mirrors the
    masking contract from every prior round's tests."""
    file_path = tmp_path / "round12_mix.py"
    mailgun = f"key-{'a' * 32}"
    square = f"EAAA{'B' * 60}"
    shpat = f"shpat_{'c' * 32}"
    file_path.write_text(
        f'MAILGUN = "{mailgun}"\n'
        f'SQUARE = "{square}"\n'
        f'SHOPIFY = "{shpat}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    matches = [f.match for f in findings]

    # Verbatim tokens must NOT appear in any finding.match span.
    for secret in (mailgun, square, shpat):
        assert secret not in matches, (
            f"Round-12 secret {secret!r} leaked verbatim into findings; "
            f"_mask_secret must redact every secret match."
        )
