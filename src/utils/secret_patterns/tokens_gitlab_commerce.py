"""Part of ``_KNOWN_TOKENS`` of the secret scanner:
GitLab runner/deploy/agent/feed/mail/CI/OAuth tokens, CircleCI, Mailgun,
Square, Shopify, WooCommerce, Mailchimp, Figma.

Moved verbatim from ``src/utils/secret_scanner.py``; the facade joins the parts
in their original order (the first matching entry wins).
"""

from __future__ import annotations

import re

KNOWN_TOKENS_GITLAB_COMMERCE = [
    # GitLab Runner Authentication Token (``glrt-<20 chars from
    # [A-Za-z0-9_-]>``). Issued via project / group / instance Runner
    # registration in GitLab 15.6+ (the post-16.0 default replacing the
    # legacy unprefixed registration-token shape). Format mirrors
    # ``glpat-``: 5-char prefix + 20-char ``[A-Za-z0-9_-]`` body. The
    # ``glrt-`` prefix is unambiguous (no other major issuer uses it),
    # and the body lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex would match
    # the full ``glrt-<body>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the
    # GitLab-Runner-specific issuer attribution that incident-response
    # keys off. A leak grants whoever holds the token the ability to
    # **register a rogue GitLab Runner** against the issuing project /
    # group / instance scope: the rogue runner subsequently drains the
    # CI job queue, and every CI job (with whatever build secrets the
    # pipeline exposes — DEPLOYMENT_KEY, CONTAINER_REGISTRY_PASSWORD,
    # every protected-branch-scoped CI variable) is delivered to
    # attacker-controlled hardware. Blast radius = the entire CI
    # estate's job-execution surface — structurally identical to the
    # Buildkite Agent Token (``bkat_``, Round 7) covered earlier. The
    # revocation flow lives at gitlab.com/<scope>/-/runners and is
    # distinct from any other vendor's, so issuer-specific attribution
    # accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])glrt-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])"),
        "GitLab Runner Authentication Token gefunden",
    ),
    # GitLab Deploy Token (``gldt-<20 chars from [A-Za-z0-9_-]>``).
    # Issued via project / group settings > Repository > Deploy Tokens
    # in GitLab 16.0+ (the post-16.0 default with prefix; pre-16.0
    # deploy tokens were unprefixed and fall into the permanent
    # bucket-(b) shape). Format mirrors ``glpat-``: 5-char prefix +
    # 20-char ``[A-Za-z0-9_-]`` body. The ``gldt-`` prefix is
    # unambiguous, and the body lies entirely inside the entropy
    # fallback's alphabet — same generic-only attribution gap as the
    # ``glrt-`` case. A leak grants the issuing scope's **Deploy Token
    # capabilities**: read/write Container Registry images, read/write
    # Package Registry artefacts, and (for the ``write_repository``
    # scope) push to protected branches. The Container Registry
    # surface is especially dangerous: an attacker who can push a
    # tampered image to the project's registry persists their
    # compromise across every downstream deployment that pulls the
    # image, bypassing the source-repository security gate entirely.
    # The revocation flow lives at gitlab.com/<project>/-/settings/
    # repository#js-deploy-tokens and is distinct from any other
    # vendor's.
    (
        re.compile(r"(?<![A-Za-z0-9])gldt-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])"),
        "GitLab Deploy Token gefunden",
    ),
    # GitLab Cluster Agent for Kubernetes Token
    # (``glagent-<50+ chars from [A-Za-z0-9_-]>``). Issued via project
    # / group settings > Operate > Kubernetes clusters > GitLab Agent
    # in GitLab 14.0+ for registering a GitLab Agent for Kubernetes
    # inside a target cluster. Format diverges from the ``glpat-``
    # family: 8-char prefix + 50+ char body (the body is longer because
    # the registered Agent uses the token for GraphQL-level mTLS
    # handshake metadata and the extra entropy is needed for the
    # agent's identity fingerprint). The ``glagent-`` prefix is
    # unambiguous, and the body lies entirely inside the entropy
    # fallback's alphabet — same generic-only attribution gap as the
    # ``glrt-`` / ``gldt-`` cases. A leak grants whoever holds the
    # token the ability to **register a rogue GitLab Agent for
    # Kubernetes** against the issuing scope: the rogue agent
    # subsequently runs ``kubectl`` commands inside the target
    # cluster (via the configured impersonation account) and
    # reads / mutates every Kubernetes resource the agent's RBAC
    # binding permits. Blast radius = the entire connected cluster's
    # resource surface — the highest leak surface in the GitLab
    # GitOps stack, structurally analogous to the Buildkite / GitLab
    # Runner registration tokens but acting at the in-cluster
    # orchestrator boundary rather than the CI runner boundary. The
    # revocation flow lives at gitlab.com/<project>/-/settings/
    # cluster_agents and is distinct from every other vendor's.
    (
        re.compile(r"(?<![A-Za-z0-9])glagent-[A-Za-z0-9_\-]{50,}(?![A-Za-z0-9])"),
        "GitLab Cluster Agent Token gefunden",
    ),
    # GitLab Feed Token (``glft-<20 chars from [A-Za-z0-9_-]>``).
    # Issued automatically for every user via ``Settings > Access
    # Tokens > Feed token`` for personal RSS/Atom-feed authentication
    # against the GitLab REST API. Format mirrors ``glpat-``: 5-char
    # prefix + 20-char ``[A-Za-z0-9_-]`` body. The ``glft-`` prefix
    # is unambiguous, and the body lies entirely inside the entropy
    # fallback's ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex
    # matches the full ``glft-<body>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the GitLab-
    # Feed-Token-specific issuer attribution. A leak grants the
    # issuing user's read scope to the activity stream — visible
    # issues, merge requests, comments, project metadata; for an
    # admin user the feed exposes the entire instance's project
    # taxonomy. Blast radius lower than the CI/CD-infrastructure-
    # tier siblings (``glrt-``/``gldt-``/``glagent-``) but the leak-
    # surface is broad. The revocation flow lives at
    # gitlab.com/-/user_settings/personal_access_tokens (alongside
    # the canonical PAT revocation flow) and is distinct from any
    # other vendor's. Closes one of the four developer-tooling-tier
    # GitLab prefixes named-but-deferred by Round 10 (PR #1493).
    (
        re.compile(r"(?<![A-Za-z0-9])glft-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])"),
        "GitLab Feed Token gefunden",
    ),
    # GitLab Incoming Mail Token (``glimt-<25+ chars from
    # [A-Za-z0-9_-]>``). Embedded in the reply-by-email
    # ``Reply-To: noreply+<token>@<instance>.gitlab.com`` header,
    # used by the GitLab incoming-mail subsystem to verify that an
    # inbound reply genuinely belongs to the issuing user. Format
    # diverges slightly from ``glpat-``: 6-char prefix + 25-char
    # body (the longer body matches the upstream
    # ``Devise.friendly_token(25)`` shape used by Rails ActionMailer
    # reply-by-email scoping). A leak lets a network adversary post
    # comments / merge request replies / issue updates **as the
    # issuing user** by sending crafted email to the GitLab inbound-
    # mail relay — full impersonation within the user's commenting
    # scope. The revocation flow lives at
    # gitlab.com/-/user_settings/personal_access_tokens (alongside
    # the Feed Token) and is distinct from any other vendor's, so
    # issuer-specific attribution accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])glimt-[A-Za-z0-9_\-]{25,}(?![A-Za-z0-9])"),
        "GitLab Incoming Mail Token gefunden",
    ),
    # GitLab CI Build Token (``glcbt-<partition_prefix>_<body>``).
    # Per-build CI token issued by the GitLab Rails server when a CI
    # job starts; exposed to the job as the ``CI_JOB_TOKEN`` env var
    # (GitLab 16.0+ post-DB-partitioning rollout — pre-16.0 build
    # tokens were unprefixed and fall into the bucket-(b) shape).
    # Format diverges from every other GitLab prefix: 5-char
    # ``glcbt-`` prefix + variable-length partition prefix (1-3
    # alphanumeric chars anchoring the token to its DB partition for
    # fast lookup) + literal ``_`` + 20+ char body from
    # ``[A-Za-z0-9_-]``. The structured ``<partition>_<body>`` shape
    # is unique among GitLab prefixes and is the structural
    # disambiguator from ``glpat-`` / ``glrt-`` / ``gldt-`` (which
    # all use a flat 20-char body). A leak during the job's lifetime
    # (token is invalidated when the job completes, but the window
    # can be hours for long-running jobs) grants the attacker the
    # ability to **call the GitLab REST API as the job**: download
    # package-registry / container-registry artefacts the job had
    # access to, trigger downstream pipelines via the canonical
    # ``CI_JOB_TOKEN`` auth flow, impersonate the job to other
    # pipelines that allow inbound job-token access (the
    # ``allow_job_token_access`` setting on protected branches).
    (
        re.compile(r"(?<![A-Za-z0-9])glcbt-[A-Za-z0-9]+_[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])"),
        "GitLab CI Build Token gefunden",
    ),
    # GitLab Scoped OAuth Access Token (``glsoat-<20+ chars from
    # [A-Za-z0-9_-]>``). Issued by SCIM-integrated SSO providers
    # (Okta / OneLogin / AzureAD / Google Workspace) when an OAuth
    # application provisions a scoped access token for a GitLab
    # user. Format mirrors ``glpat-`` with a longer prefix: 7-char
    # ``glsoat-`` prefix + 20+ char ``[A-Za-z0-9_-]`` body. The
    # ``glsoat-`` prefix anchors against the OAuth-application-
    # scoped subset of token scopes (as opposed to the broader
    # ``glpat-`` user-PAT scope, which would grant the full set of
    # the user's PAT scopes). A leak grants the OAuth application's
    # scoped capabilities for the issuing user — typically
    # ``read_user`` / ``read_repository`` / ``api`` for SCIM-
    # provisioned OAuth apps in enterprise GitLab Self-Managed
    # installations. The revocation flow lives at
    # gitlab.com/-/profile/applications (distinct from the
    # gitlab.com/-/user_settings/personal_access_tokens PAT flow),
    # so issuer-specific attribution accelerates IR triage to the
    # correct dashboard / API endpoint.
    (
        re.compile(r"(?<![A-Za-z0-9])glsoat-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])"),
        "GitLab Scoped OAuth Access Token gefunden",
    ),
    # CircleCI Personal API Token (``CCIPAT_<32+ chars from
    # [A-Za-z0-9_-]>``). Issued via
    # app.circleci.com/settings/user/tokens for full CircleCI
    # REST-API v2 access. The ``CCIPAT_`` prefix was added in
    # 2023 to replace the legacy unprefixed 40-char-alphanumeric
    # CircleCI tokens (legacy tokens fall into the bucket-(b)
    # shape; the modern ``CCIPAT_`` format anchors against the
    # entropy fallback's body span). Format: 7-char prefix + 32+
    # char ``[A-Za-z0-9_-]`` body. The body lies entirely inside
    # the entropy fallback's alphabet (the underscore is in the
    # alphabet), so pre-fix the entropy regex matches the full
    # ``CCIPAT_<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the CircleCI-specific
    # attribution. A leak grants the issuing user's full CircleCI
    # organisation scope: read every project's pipeline
    # configuration (which embeds inline env-var references to
    # other vendors' tokens — AWS keys, Docker registry creds,
    # third-party API tokens), trigger arbitrary pipelines on
    # attacker-controlled branches, exfiltrate build artifacts,
    # and manage SSH keys for project deployments. Blast radius
    # is structurally identical to the Buildkite User Access
    # Token (``bkua_``, Round 8) — the personal-token tier of the
    # CI execution sub-landscape. The revocation flow lives at
    # app.circleci.com/settings/user/tokens and is distinct from
    # every other vendor's, so issuer-specific attribution
    # accelerates IR triage. Closes the CircleCI prefix named-but-
    # deferred by Round 7/8 (CI execution-tier sibling).
    (
        re.compile(r"(?<![A-Za-z0-9])CCIPAT_[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])"),
        "CircleCI Personal API Token gefunden",
    ),
    # Mailgun Private API Key (``key-<32 lowercase hex>``). Issued via
    # app.mailgun.com/app/account/security/api_keys for full Mailgun
    # transactional-mail API access. Total length 36 chars (4-char
    # ``key-`` prefix + 32-char lowercase hex body). The ``key-``
    # prefix is unambiguous in the Mailgun-issuer context: while the
    # literal substring ``key-`` is common in placeholder values
    # (``api-key``, ``foo-key-1``), the strict 32-char-lowercase-hex
    # body length guard rejects every operator-supplied placeholder
    # that doesn't accidentally land in the hex alphabet AND clock
    # in at exactly 32 chars. A leak grants the issuing account's
    # full transactional-mail / contacts API scope: the attacker can
    # send mail FROM the project's authenticated sending domain
    # (phishing amplification leveraging the project's existing SPF
    # / DKIM / DMARC authentication), exfiltrate the suppression /
    # bounce / event logs (which may carry PII), modify webhook
    # endpoints to redirect delivery events to attacker-controlled
    # URLs, and create new API keys to maintain persistence. The
    # revocation flow lives at app.mailgun.com/app/account/security/
    # api_keys and is distinct from every other vendor's (in
    # particular Brevo (Sendinblue) ``xkeysib-`` from Round 5 —
    # both are transactional-mail vendors but their revocation
    # flows live on different control planes), so issuer-specific
    # attribution accelerates IR triage. Pre-fix the entropy
    # fallback's ``[A-Za-z0-9+/=_-]{24,}`` regex matches only the
    # 32-hex body span as one generic ``Hochentropischer Token-
    # String`` finding (the trailing alphabet excludes ``key-``
    # because ``key-`` is only 4 chars — too short for the 24-char
    # entropy minimum on its own), losing the Mailgun-specific
    # attribution. Closes the named-but-deferred adjacent-prefix
    # candidate from Round 11 (PR closing GitLab developer-tooling
    # + CircleCI).
    (
        re.compile(r"(?<![A-Za-z0-9])key-[a-f0-9]{32}(?![A-Za-z0-9])"),
        "Mailgun Private API Key gefunden",
    ),
    # Square Access Token (``EAAA<60+ chars from [A-Za-z0-9_-]>``).
    # Issued via developer.squareup.com/apps for full Square REST-API
    # access. The ``EAAA`` prefix is the base64url encoding of the
    # first 3 bytes of the embedded JSON token payload's leading
    # byte sequence (structurally analogous to JWT's ``eyJ`` —
    # ``eyJ`` decodes to ``{"`` and ``EAAA`` decodes to a different
    # 3-byte sequence). Both ``EAAA`` and ``eyJ`` are 4-char base64
    # prefixes but are mutually exclusive at the leading-character
    # level — no token can match both patterns simultaneously. Total
    # length 64+ chars in modern Square tokens (4-char prefix +
    # 60+ char body). The 60-char body lower bound rejects short
    # ``EAAA``-prefixed fragments (operator placeholder values,
    # accidentally-truncated tokens) while accepting every
    # legitimate Square access token. A leak grants the issuing
    # seller's full Square dashboard scope: read every customer's
    # payment data / catalog / inventory state, initiate transactions
    # under the seller's account, refund payments, modify employee
    # permissions, exfiltrate the merchant's tax-filing data. Blast
    # radius is structurally identical to the Stripe Live Secret
    # Key (``sk_live_``, Round 1) — the payment-processor-tier
    # leak surface. The revocation flow lives at
    # developer.squareup.com/apps and is distinct from every other
    # payment-processor's (in particular Stripe's
    # dashboard.stripe.com/apikeys), so issuer-specific attribution
    # accelerates IR triage. Pre-fix the entropy fallback matched
    # the full ``EAAA<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the Square-specific
    # attribution. Closes the named-but-deferred adjacent-prefix
    # candidate from Round 11.
    (
        re.compile(r"(?<![A-Za-z0-9])EAAA[A-Za-z0-9_\-]{60,}(?![A-Za-z0-9])"),
        "Square Access Token gefunden",
    ),
    # Shopify Admin API Access Token (``shpat_<32 lowercase hex>``).
    # Issued by Shopify when a custom app installs into a store via
    # the Admin API OAuth flow (post-2022 modern custom-app flow,
    # ``shpat_`` prefix). Total length 38 chars (6-char prefix +
    # 32-char hex body). The ``shpat_`` prefix is unambiguous, and
    # the strict 32-hex body length avoids overlap with the entropy
    # fallback's broader alphabet. A leak grants the app's full
    # installed scope on the store: read/write every product,
    # customer, order, inventory item, fulfilment, refund, draft
    # order, gift card, abandoned checkout, customer-segment, and
    # webhook configuration in the store; modify shop settings;
    # initiate refunds and create new staff accounts. Blast radius
    # is the highest leak surface in the e-commerce-platform tier
    # — structurally identical to the GitLab Personal Access Token
    # (``glpat-``, Round 1) for the e-commerce dashboard. The
    # revocation flow lives at admin.shopify.com/apps/<app>/edit
    # and is distinct from every other vendor's. Pre-fix the
    # entropy fallback's ``[A-Za-z0-9+/=_-]{24,}`` regex matches
    # the full ``shpat_<body>`` span (the underscore IS in the
    # entropy alphabet) as one generic ``Hochentropischer Token-
    # String`` finding, losing the Shopify-Admin-API-specific
    # attribution that determines which admin dashboard the operator
    # must visit to revoke the token. Opens the e-commerce-platform
    # sub-landscape in lockstep with the named adjacent-prefix
    # candidates (Mailgun / Square) above.
    (
        re.compile(r"(?<![A-Za-z0-9])shpat_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Admin API Access Token gefunden",
    ),
    # Shopify Shared Secret (``shpss_<32 lowercase hex>``). Issued
    # alongside ``shpat_`` for HMAC-SHA256 signature verification
    # of webhook payloads delivered by Shopify to the app's callback
    # URL. Format identical to ``shpat_`` (6-char prefix + 32-char
    # hex body); the ``shpss_`` prefix is unambiguous and mutually
    # exclusive with ``shpat_``/``shppa_``/``shpca_`` at the
    # fifth-character level. A leak lets a network adversary forge
    # webhook payloads that the app's signature verification will
    # accept — every webhook-driven business logic (order fulfilment,
    # refund processing, cart-abandonment automation, customer-data
    # synchronisation, gift-card issuance) can be triggered by an
    # attacker. Blast radius is structurally identical to the
    # Stripe Webhook Signing Secret (``whsec_``, Round 1) for the
    # e-commerce dashboard — webhook-payload forgery, not direct
    # API access. The rotation flow lives via the app's webhook-
    # settings page in the admin dashboard and is distinct from
    # the access-token revocation flow.
    (
        re.compile(r"(?<![A-Za-z0-9])shpss_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Shared Secret gefunden",
    ),
    # Shopify Private App API Access Token (``shppa_<32 lowercase
    # hex>``). Legacy private-app token shape (deprecated for new
    # stores 2022-01-01 but still issued for existing installations
    # that haven't migrated to the modern custom-app flow). Format
    # identical to ``shpat_``/``shpss_``/``shpca_`` (6-char prefix
    # + 32-char hex body); the ``shppa_`` prefix is unambiguous.
    # A leak grants the private app's full store scope — same
    # blast radius as ``shpat_`` for the apps that haven't migrated
    # to the modern custom-app flow. The revocation flow lives at
    # admin.shopify.com/admin/apps/private and is distinct from
    # the custom-app revocation flow at
    # admin.shopify.com/admin/settings/apps/development. The
    # per-prefix attribution lets the operator land on the correct
    # admin page in seconds during incident response.
    (
        re.compile(r"(?<![A-Za-z0-9])shppa_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Private App Access Token gefunden",
    ),
    # Shopify Custom App Access Token (``shpca_<32 lowercase hex>``).
    # Modern custom-app token format (post-2022 replacement of the
    # deprecated ``shppa_`` private-app shape). Format identical to
    # the rest of the Shopify family (6-char prefix + 32-char hex
    # body); the ``shpca_`` prefix is unambiguous. A leak grants
    # the modern custom app's full store scope — same blast radius
    # as ``shpat_``. The revocation flow lives at
    # admin.shopify.com/admin/settings/apps/development and is
    # distinct from every other Shopify revocation flow (per-prefix
    # attribution is critical for IR triage so the operator lands
    # on the correct admin page instantly).
    (
        re.compile(r"(?<![A-Za-z0-9])shpca_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Custom App Access Token gefunden",
    ),
    # WooCommerce REST API Consumer Key (``ck_<32+ alphanumeric>``).
    # Issued via wp-admin > WooCommerce > Settings > Advanced > REST API
    # > Add Key. The key is generated by ``wp_generate_password(32, false)``
    # (modern WooCommerce) which returns 32 chars from ``[a-zA-Z0-9]``;
    # legacy installations using ``wc_rand_hash()`` produced 64-char
    # lowercase-hex bodies, and intermediate versions used 40-char
    # alphanumeric bodies. The ``{32,}`` lower bound covers the modern
    # canonical form AND every documented legacy variant in one regex;
    # the greedy quantifier + lookahead anchor end at the natural
    # alphanumeric boundary so the longest legitimate body wins. The
    # ``ck_`` prefix is unambiguous in the WooCommerce-issuer context
    # — while the literal substring is common in placeholder values
    # (``track_id``, ``check_button``), the strict 32-char-alphanumeric
    # body length guard rejects every operator-supplied placeholder
    # that doesn't accidentally land in the alphanumeric alphabet AND
    # clock in at exactly 32+ chars after the ``ck_`` prefix. A leak
    # paired with the matching ``cs_`` consumer secret (next entry)
    # grants the issuing API user's full WooCommerce REST scope:
    # read/write every product, order, customer, coupon, refund,
    # shipping zone, tax rate, and webhook configuration on the store;
    # the role attached to the API user (read / write / read_write)
    # determines the effective blast radius, but write-tier keys are
    # the default WooCommerce documentation recommends. Pre-fix every
    # WooCommerce consumer key with a uniform character class body
    # (all-lowercase, all-uppercase, all-digits) was SILENTLY
    # UNDETECTED — the entropy fallback's ``_looks_like_secret``
    # heuristic requires multiple character categories, and a body
    # like ``ck_aaaaaa...`` (all lowercase) failed that check, so the
    # key slipped past the scanner entirely; mixed-case bodies were
    # caught only by the generic "Verdächtige Zuweisung" assignment
    # heuristic (which depends on the variable name containing a
    # sensitive keyword like ``key``/``secret`` and produces no
    # issuer-specific attribution). Post-fix every WooCommerce
    # consumer key receives the specific German attribution
    # (``WooCommerce Consumer Key gefunden``) that incident-response
    # playbooks key off (revocation flow at wp-admin > WooCommerce >
    # Settings > Advanced > REST API). The revocation flow is
    # distinct from every other e-commerce-platform vendor's (in
    # particular Shopify's admin.shopify.com/* — both WooCommerce
    # and Shopify carry the same e-commerce-tier blast radius but
    # their revocation flows live on entirely different control
    # planes), so issuer-specific attribution accelerates IR triage.
    # Closes the named-but-deferred adjacent-prefix candidate from
    # Round 12 (Shopify family + Mailgun + Square).
    (
        re.compile(r"(?<![A-Za-z0-9])ck_[a-zA-Z0-9]{32,}(?![A-Za-z0-9])"),
        "WooCommerce Consumer Key gefunden",
    ),
    # WooCommerce REST API Consumer Secret (``cs_<32+ alphanumeric>``).
    # Sibling to ``ck_`` — issued together by the same admin flow,
    # used together as Basic Auth ``ck_<key>:cs_<secret>`` for every
    # WooCommerce REST API request. Format identical to ``ck_``
    # (3-char prefix + 32+ char alphanumeric body). A leak of the
    # consumer secret alone (without the matching ``ck_``) is less
    # immediately exploitable (the key is the identifier, the secret
    # is the signing material), but the standard WooCommerce
    # documentation recommends storing both in the same config block
    # — a leak of either typically implies a leak of both, and a
    # leaked ``cs_`` strongly signals that a ``ck_`` exists somewhere
    # in the same repo (just as a leaked ``sk_test_`` Stripe key
    # signals an ``sk_live_`` exists elsewhere). Distinct reason from
    # the consumer key so the report calls out *which* half of the
    # credential pair leaked and the operator can rotate the correct
    # credential with confidence. Pre-fix detection gap identical to
    # ``ck_``: silently undetected for uniform-character-class bodies,
    # generic "Verdächtige Zuweisung" only for mixed-case bodies.
    (
        re.compile(r"(?<![A-Za-z0-9])cs_[a-zA-Z0-9]{32,}(?![A-Za-z0-9])"),
        "WooCommerce Consumer Secret gefunden",
    ),
    # Mailchimp API Key (``<32 hex>-us<datacenter>``). Issued via
    # mailchimp.com/account/api-keys for full Mailchimp REST API
    # access. Format: 32 lowercase hex chars + literal ``-us`` +
    # 1-3 digit datacenter number (``-us1``, ``-us14``, ``-us20``
    # are all valid; the datacenter shard is assigned at account
    # creation based on the account's geographic region and grows
    # over time as Mailchimp adds capacity). The ``-us<N>`` suffix
    # is the structural disambiguator: the entropy fallback's
    # ``[A-Za-z0-9+/=_-]{24,}`` regex matches the entire string
    # (every char in the alphabet, including the dash), but the
    # ``-us<digit>`` suffix is unique to Mailchimp's datacenter
    # routing convention and unambiguously identifies the issuer.
    # No other major API vendor uses the ``-us<digit>`` suffix
    # pattern in their token format. A leak grants the issuing
    # account's full Mailchimp scope: read/write every audience
    # / list / contact / campaign / automation / signup form /
    # template / report; export the full subscriber CSV (PII
    # exfiltration); send mass mailings AS the account (phishing
    # amplification leveraging the account's authenticated sender
    # domain and existing SPF/DKIM/DMARC reputation); modify
    # webhook URLs to redirect subscription/unsubscription events
    # to attacker-controlled URLs; create new transactional API
    # keys via Mandrill (Mailchimp's transactional sub-product)
    # for persistence. Blast radius is structurally identical to
    # Mailgun (``key-<32 hex>``, Round 12) for the
    # marketing-email-platform tier — both are email-vendor leak
    # surfaces but their control planes are distinct
    # (mailchimp.com vs. app.mailgun.com), so per-issuer attribution
    # is critical for IR triage. The revocation flow lives at
    # mailchimp.com/account/api/ (and the datacenter-prefixed
    # admin.mailchimp.com URLs for direct-routed UI access). Closes
    # the named-but-deferred adjacent-prefix candidate from Round 12.
    (
        re.compile(r"(?<![A-Za-z0-9])[a-f0-9]{32}-us[0-9]{1,3}(?![A-Za-z0-9])"),
        "Mailchimp API Key gefunden",
    ),
    # Figma Personal Access Token (``figd_<43 chars from [A-Za-z0-9_-]>``).
    # Issued via figma.com/settings/personal-access-tokens for full Figma
    # REST API access scoped to the issuing user's accessible teams /
    # projects / files. The Figma issuer header (``X-Figma-Token``) is
    # ALREADY enumerated in ``src/utils/http.py:_SENSITIVE_HEADERS`` (so
    # the cross-origin redirect strip path covers it), but the TOKEN
    # VALUE shape was not in this scanner table — a header/value drift
    # mirroring the pattern this round closes (header name reaches the
    # operator log redacted, value-shape leaks verbatim).
    #
    # Total length 48 chars (5-char prefix + 43-char body). The
    # ``figd_`` prefix is unambiguous (no other major issuer uses it),
    # and the body alphabet ``[A-Za-z0-9_-]`` (base64url + underscore +
    # dash) lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex matches the
    # full ``figd_<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding (the dash and underscore ARE in the
    # alphabet), losing the Figma-specific issuer attribution that
    # incident-response triage keys off. The strict 43-char body length
    # matches the documented canonical PAT shape (per the trufflehog /
    # gitleaks / detect-secrets default rules) and rejects accidental
    # ``figd_``-prefixed fragments.
    #
    # Threat model: a leaked Figma PAT grants the issuing user's full
    # design-collaboration scope across every accessible team / project
    # / file — read every design (including unpublished prototypes and
    # internal pitch decks that routinely contain customer-facing
    # branding before public reveal), copy proprietary design tokens
    # (which often encode business-strategic colour / typography
    # decisions), POST comments AS the user (impersonation risk for
    # social-engineering reconnaissance), and exfiltrate the team's
    # entire design-system version history. Blast radius is structurally
    # similar to Notion (``secret_<43>`` / ``ntn_<43+>``, Round 8) for
    # the workspace-collaboration tier — both are content-management
    # SaaS tokens with full-workspace read/write scope.
    #
    # Real-world emission patterns: ``.env`` files
    # (``FIGMA_TOKEN=figd_...``), CI/CD pipeline debug logs, README
    # curl examples, notebook outputs hardcoding the PAT, and the
    # canonical sibling-drift leak surface — JSON values in error
    # responses echoing the token back via a hostile / misconfigured
    # upstream. The revocation flow lives at figma.com/settings/
    # personal-access-tokens > Revoke and is distinct from every other
    # vendor's, so issuer-specific attribution accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])figd_[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])"),
        "Figma Personal Access Token gefunden",
    ),
]
