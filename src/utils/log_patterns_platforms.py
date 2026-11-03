"""Value-shape masks for platform tokens (supply chain, e-commerce, PaaS, observability, email).

Also Figma/Tailscale, Dropbox/Pulumi, Slack xapp/Databricks,
HubSpot/PlanetScale and Heroku/Docker. Pure data moved out of
``src/utils/logging.py``, which imports it and runs these patterns last,
after ``_DEVOPS_SAAS_TOKEN_PATTERNS``.
"""

from __future__ import annotations

# The list keeps a private name; ``src/utils/logging.py`` imports it
# (code scanning counts an unlisted ``_name`` as unused).
__all__ = ["_PLATFORM_TOKEN_PATTERNS"]

_PLATFORM_TOKEN_PATTERNS: list[tuple[str, str]] = [
    # Supply-Chain / E-Commerce / PaaS / Observability / Email-Platform
    # token tier value-shape masking. Sibling-drift closure for the
    # secret-scanner ``_KNOWN_TOKENS`` entries detecting committed
    # tokens across the remaining named-but-deferred backlog from the
    # 2026-05-17 SaaS / Comms / Workspace / Observability / Secret-
    # Manager round — every bare token shape below leaked verbatim
    # through ``sanitize_log_message`` pre-fix (plain log lines, JSON
    # values with non-sensitive keys, URL paths / query strings with
    # non-sensitive parameter names, exception messages routed through
    # ``_sanitize_exception_msg``).
    #
    # Families covered (10 issuers / 11 patterns):
    #
    # Supply-Chain tier:
    #   * ``pypi-<20+ from [A-Za-z0-9_-]>`` — PyPI API Token. Issued
    #     at pypi.org/manage/account/token/. Leak grants publish
    #     access to every accessible PyPI project — canonical
    #     supply-chain compromise primitive: a hostile actor can push
    #     a backdoored wheel of a popular package and every CI run
    #     of every downstream consumer pulls the malicious version.
    #
    # E-Commerce tier (HIGH payment-fraud blast radius):
    #   * ``shpat_<32 mixed-case hex>`` — Shopify Admin API Access
    #     Token. Full storefront/admin scope: read every order /
    #     customer record (PII + payment metadata), modify product
    #     catalogue (BEC-style price manipulation), drain Shopify
    #     Payments balance via refunds-to-attacker-IBAN flow.
    #   * ``shpss_<32 hex>`` — Shopify Shared Secret. Webhook HMAC
    #     signing key — forge any webhook payload from any private
    #     Shopify app.
    #   * ``shppa_<32 hex>`` — Shopify Partner API Access Token.
    #     Cross-store: every store the Partner is installed on.
    #   * ``shpca_<32 hex>`` — Shopify Custom App Access Token.
    #     Per-store but the same Admin API scope as ``shpat_``.
    #   * ``ck_<32+ alnum>`` — WooCommerce Consumer Key. Pairs with
    #     ``cs_`` for full WooCommerce REST API access: read every
    #     order, customer, product. Drain via refund flow analogous
    #     to Shopify Admin API.
    #   * ``cs_<32+ alnum>`` — WooCommerce Consumer Secret. The
    #     paired secret for ``ck_``.
    #   * ``EAAA<60+ from [A-Za-z0-9_-]>`` — Square Access Token.
    #     Full payment processing access: read transaction history
    #     (PII + card-fingerprint metadata), issue refunds, create
    #     new charges. Direct payment fraud primitive.
    #
    # PaaS / Edge-Runtime tier (deployment hijack):
    #   * ``nfp_<40+ alnum>`` — Netlify Personal Access Token. Full
    #     account scope: redirect every site's deploys to an
    #     attacker-controlled build, exfiltrate every env-var that
    #     a build can read (the canonical landing zone for AWS /
    #     Stripe / database creds), modify DNS / SSL config to
    #     hijack the production domain (then capture every
    #     authenticated session via a forged TLS chain).
    #   * ``rnd_<40+ from [A-Za-z0-9_-]>`` — Render API Key. Same
    #     deployment-hijack blast radius as Netlify for services
    #     hosted on Render's PaaS.
    #   * ``FlyV1 (?:fm1|fm2|fo1)_<50+>`` — Fly.io Macaroon Token.
    #     Edge-runtime hijack: deploy malicious code to every Fly
    #     app the token's macaroon scope grants, modify routing /
    #     Wireguard peers / Anycast routes, rotate billing
    #     credentials. The ``FlyV1`` prefix uniquely contains a
    #     LITERAL SPACE between the issuer keyword and the
    #     macaroon-discriminator body — mirroring the scanner's
    #     exact pattern.
    #
    # Observability tier (data-exfiltration + APM leak amplifier):
    #   * ``NRAK-<27 uppercase alnum>`` — New Relic User Key.
    #     Full user-scope account access: read every APM stream's
    #     payload (which routinely contains debug-logged tokens
    #     from production traces — secondary credential leak
    #     amplifier).
    #   * ``NRRA-<40 mixed-case hex>`` — New Relic REST API Key.
    #     REST API access scoped per the key's privilege level.
    #   * ``NRII-<32 mixed-case hex>`` — New Relic Insights Insert
    #     Key. NRDB write access; can fabricate / overwrite APM
    #     events to mask intrusion artifacts.
    #
    # Email-Platform tier (phishing amplification):
    #   * ``xkeysib-<64 hex>-<16 alnum>`` — Brevo (Sendinblue) API
    #     Key. Marketing + transactional email vendor. Two-part
    #     body shape: a leak grants the attacker the ability to
    #     send mail FROM the project's authenticated sending
    #     domain (phishing amplification leveraging the account's
    #     existing SPF / DKIM / DMARC reputation), export the
    #     full subscriber CSV (PII exfiltration), and access the
    #     transactional logs.
    #   * ``<32 lowercase hex>-us<digits>`` — Mailchimp API Key.
    #     The ``us<region>`` datacenter routing identifier is
    #     PRESERVED in the mask (``***-us20``) for IR-routing
    #     attribution since mailchimp.com's API endpoints are
    #     keyed off the region. Mask preserves the suffix so the
    #     responder can locate the correct admin subdomain
    #     (``admin.mailchimp.com`` for direct-routed access).
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``xpypi-``, ``foonfp_``, ``Ashpat_`` are
    #     preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict body lengths / alphabets per vendor canonical
    #     format reject accidental fragments while accepting every
    #     real-shape token.
    #
    # Each mask preserves the issuer-specific prefix (``pypi-***``,
    # ``shpat_***``, ``EAAA***``, ``nfp_***``, ``FlyV1 fm1_***``,
    # ``NRAK-***``, ``xkeysib-***`` etc.) for incident-response
    # triage because each tier has a distinct revocation flow:
    #   * PyPI — pypi.org/manage/account/token/ > Revoke.
    #   * Shopify Admin/Custom/Partner/Shared-Secret — shopify.com
    #     admin panel > Apps > Custom apps > Revoke.
    #   * WooCommerce — site admin > WooCommerce > Settings >
    #     Advanced > REST API > Revoke.
    #   * Square — squareup.com/dashboard > Apps > Revoke.
    #   * Netlify — app.netlify.com/user/applications > Revoke.
    #   * Render — render.com/u/settings > API Keys > Revoke.
    #   * Fly.io — fly.io/dashboard/<org>/tokens > Revoke.
    #   * New Relic — one.newrelic.com/api-keys > Revoke.
    #   * Brevo — app.brevo.com/settings/keys/api > Delete.
    #   * Mailchimp — mailchimp.com/account/api/ > Revoke.
    #
    # Idempotence: masked forms (``pypi-***``, ``shpat_***``,
    # ``EAAA***``, ``nfp_***``, ``FlyV1 fm1_***``, ``NRAK-***``,
    # ``xkeysib-***``, ``***-us20``) do NOT match any of these
    # regexes because ``*`` is not in any body alphabet AND the
    # masked body length (3 chars) is below every per-family floor
    # (20/27/32/40/50/60/64).
    #
    # Marker: SENTINEL_SUPPLY_CHAIN_ECOMMERCE_PAAS_OBSERVABILITY_EMAIL_TOKEN_LOG_SANITIZATION_DRIFT.
    #
    # Ordering: ``shpat|shpss|shppa|shpca`` are grouped (mutually
    # exclusive at the prefix level, same body alphabet/length).
    # ``ck|cs`` are grouped (mutually exclusive at the prefix level,
    # same body alphabet/length floor). ``NRAK|NRRA|NRII`` are kept
    # separate because each has a distinct body alphabet (NRAK
    # uppercase alnum, NRRA/NRII mixed-case hex).
    (
        r"(?<![A-Za-z0-9])(pypi)-[0-9a-zA-Z_\-]{20,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(xkeysib)-[a-f0-9]{64}-[A-Za-z0-9]{16}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(nfp)_[A-Za-z0-9]{40,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(rnd)_[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(NRAK)-[A-Z0-9]{27}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(NRRA)-[a-fA-F0-9]{40}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(NRII)-[a-fA-F0-9]{32}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(FlyV1 (?:fm[12]|fo1))_[A-Za-z0-9_=\-]{50,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(EAAA)[A-Za-z0-9_\-]{60,}(?![A-Za-z0-9])",
        r"\1***",
    ),
    (
        r"(?<![A-Za-z0-9])(shpat|shpss|shppa|shpca)_[a-fA-F0-9]{32}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(ck|cs)_[a-zA-Z0-9]{32,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    # Mailchimp API Key: ``<32 lowercase hex>-us<digits>``. NO issuer
    # prefix — purely structural. The mask preserves the ``-us<region>``
    # datacenter routing suffix so the incident responder can locate
    # the correct admin subdomain (the ``us<region>`` selector is also
    # the per-tenant admin host like ``us20.admin.mailchimp.com``);
    # the 32-hex body span is the credential and is the part masked
    # to ``***``.
    (
        r"(?<![A-Za-z0-9])[a-f0-9]{32}-(us[0-9]{1,3})(?![A-Za-z0-9])",
        r"***-\1",
    ),
    # Figma + Tailscale token-family value-shape masking. Sibling-
    # drift closure for the secret-scanner ``_KNOWN_TOKENS`` entries
    # added in the same round — Figma Personal Access Token
    # (``figd_<43>``) and Tailscale auth/api/client/webhook keys
    # (``tskey-(?:auth|api|client|webhook)-<id>-<secret>``). The
    # log-sanitisation codepath MUST mask these alongside the
    # scanner detection codepath; without value-shape masking, a
    # bare leaked token in plain log text (application f-string
    # logs, upstream error responses echoing the token back, JSON
    # values without sensitive key names, URL paths / query strings
    # with NON-sensitive parameter names, exception messages routed
    # through ``_sanitize_exception_msg``) bypasses every existing
    # key/header/URL-credential mask pattern and leaks verbatim
    # into operator log streams and the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model:
    #   * Figma PAT ``figd_<43>`` — Full design-collaboration scope
    #     for the issuing user across every accessible team /
    #     project / file. The Figma ``X-Figma-Token`` header is
    #     already in ``_SENSITIVE_HEADERS``; this entry closes the
    #     companion VALUE-shape gap (header name was redacted, but
    #     the VALUE embedded in JSON / URL paths / exception text
    #     leaked verbatim).
    #   * Tailscale ``tskey-auth-`` — Auth Key: attach a rogue node
    #     to the victim's private overlay network. The rogue node
    #     sees every subnet-routed service AND pivots laterally as
    #     a trusted peer.
    #   * Tailscale ``tskey-api-`` — Admin REST API access: modify
    #     ACLs (open every tailnet device to the attacker), rotate
    #     DNS configuration (DNS-rebinding amplifier), add/remove
    #     users, mint fresh auth keys.
    #   * Tailscale ``tskey-client-`` — OAuth client secret: mints
    #     fresh OAuth access tokens until revocation.
    #   * Tailscale ``tskey-webhook-`` — Webhook signing secret:
    #     forge tailnet event payloads accepted as authentic by
    #     downstream consumers (state-machine confusion attacks).
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``myfigd_<body>``, ``Xtskey-auth-...`` are
    #     preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict body lengths per vendor canonical format: Figma
    #     EXACTLY 43 chars from ``[A-Za-z0-9_-]``; Tailscale
    #     ``<tier>`` from a strict 4-keyword alternation,
    #     ``<keyID>`` 8+ alnum, ``<keySecret>`` 20+ alnum.
    # The masks preserve issuer-specific prefixes:
    #   * Figma → ``figd_***`` (revocation flow at figma.com/
    #     settings/personal-access-tokens).
    #   * Tailscale → ``tskey-auth-***`` / ``tskey-api-***`` /
    #     ``tskey-client-***`` / ``tskey-webhook-***`` (per-tier
    #     attribution; each tier has a distinct revocation sub-page
    #     under login.tailscale.com/admin/settings).
    #
    # Idempotence: masked forms (``figd_***``, ``tskey-auth-***``,
    # etc.) do NOT re-match because ``*`` is OUTSIDE every body
    # alphabet AND the masked body length (3 chars) is below every
    # per-family floor (43 / 8 / 20).
    #
    # Cross-family mutex: ``figd_`` vs. ``tskey-`` prefixes are
    # disjoint at the leading-character level, so no token can
    # match both patterns.
    #
    # Marker: SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT.
    (
        r"(?<![A-Za-z0-9])(figd)_[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(tskey-(?:auth|api|client|webhook))-"
        r"[A-Za-z0-9]{8,}-[A-Za-z0-9]{20,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    # Dropbox + Pulumi token-family value-shape masking. Sibling-
    # drift closure for the secret-scanner ``_KNOWN_TOKENS`` entries
    # added in the same round — Dropbox Short-Lived Access Token
    # (``sl.<base64url body 40+>``) and Pulumi Access Token
    # (``pul-<40 lowercase hex>``). The log-sanitisation codepath
    # MUST mask these alongside the scanner detection codepath;
    # without value-shape masking, a bare leaked token in plain
    # log text (application f-string logs, upstream error responses
    # echoing the token back, JSON values without sensitive key
    # names, URL paths / query strings with NON-sensitive parameter
    # names, exception messages routed through
    # ``_sanitize_exception_msg``) bypasses every existing
    # key/header/URL-credential mask pattern and leaks verbatim
    # into operator log streams and the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model:
    #   * Dropbox ``sl.<body>`` — file-storage / sharing /
    #     team-admin scope per the issuing app's permissions.
    #     File read = customer data exfiltration; file write =
    #     ransomware-style overwrite; sharing scope = create
    #     unauthorised public shared links; team-admin scope =
    #     exfiltrate team directory, revoke other admins, modify
    #     retention policies. The short-lived 4h TTL bounds the
    #     blast window but the issuing app's refresh token can
    #     re-mint short-lived tokens indefinitely — a leaked
    #     short-lived token implies the refresh token is also
    #     exposed in the same artefact.
    #   * Pulumi ``pul-<body>`` (HIGHEST blast radius — IaC
    #     control plane) — full Pulumi Cloud API access for the
    #     issuing user across every accessible org / project /
    #     stack. Read access = exfiltrate every secret persisted
    #     in stack state (cloud provider creds, database
    #     passwords, third-party API keys, TLS private keys).
    #     Write access = trigger arbitrary ``pulumi up`` modifying
    #     production infrastructure (provision attacker VMs,
    #     modify IAM, redirect DNS). The "pivot to every
    #     downstream environment via a single credential"
    #     amplifier.
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``mysl.<body>``, ``Xpul-<body>`` are
    #     preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Dropbox body: 40+ chars from ``[A-Za-z0-9_-]`` (base64url
    #     alphabet); real-world bodies 130-160 chars.
    #   * Pulumi body: strict 40-char lowercase hex (SHA-1-shape
    #     digest) rejects placeholder values like
    #     ``pull-request-1234`` that contain hyphens.
    # The masks preserve issuer-specific prefixes:
    #   * Dropbox → ``sl.***`` (revocation flow at
    #     dropbox.com/developers/apps > App > "Revoke tokens").
    #   * Pulumi → ``pul-***`` (revocation flow at
    #     app.pulumi.com/account/tokens > "Revoke").
    #
    # Idempotence: masked forms (``sl.***``, ``pul-***``) do NOT
    # re-match because ``*`` is OUTSIDE every body alphabet AND
    # the masked body length (3 chars) is below every per-family
    # floor (40 / 40).
    #
    # Cross-family mutex: ``sl.`` vs. ``pul-`` prefixes are
    # disjoint at the leading-character level (``s`` vs. ``p``),
    # so no token can match both patterns.
    #
    # Marker: SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT.
    (
        r"(?<![A-Za-z0-9])sl\.[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])",
        r"sl.***",
    ),
    (
        r"(?<![A-Za-z0-9])pul-[a-f0-9]{40}(?![A-Za-z0-9])",
        r"pul-***",
    ),
    # Slack App-Level Token (``xapp-<v>-<app_id>-<seq>-<hex>``) + Databricks
    # Personal Access Token (``dapi<32 hex>(?:-<digit>)?``) value-shape
    # masking. Sibling-drift closure for the 2026-05-18 round that
    # extended ``_KNOWN_TOKENS`` in ``src/utils/secret_scanner.py`` to
    # detect each canonical token shape with vendor-specific
    # attribution. Pre-fix the scanner-side coverage was missing for
    # BOTH families AND the log-sanitisation codepath was NOT
    # extended in any prior round — bare token shapes in plain log
    # text (application f-string logs, upstream error responses
    # echoing the token back, JSON values without sensitive key
    # names, URL paths embedding the token, URL query strings with
    # NON-sensitive parameter names) bypassed every existing
    # key/header/URL-credential mask and leaked verbatim to operator
    # log streams plus the public ``docs/feed_health.json`` artefact.
    #
    # Threat model (mirror the secret-scanner round's blast radius):
    #   * Slack ``xapp-`` — Socket Mode + app-level Events API
    #     connection. Leaking grants the app's full event firehose
    #     (DMs / channel messages / slash commands / modal
    #     submissions for every app-subscribed event) AND combined
    #     with ``authorizations:read`` enumerates every workspace
    #     install of the app (cross-tenant pivot — one App-Level
    #     Token compromises every workspace the app is installed in).
    #   * Databricks ``dapi`` — full workspace-scoped data plane +
    #     job-execution plane. Leaking grants ``SELECT`` on every
    #     table the user can read (Unity Catalog data exfil),
    #     arbitrary Spark/SQL job submission on the user's clusters
    #     (compute theft on USD 100s-1000s/hour GPU clusters), AND
    #     arbitrary code execution within the cloud account via the
    #     cluster's attached IAM role (the canonical "data plane to
    #     control plane pivot" amplifier).
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``Xxapp-...``, ``mydapi...`` are preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Slack: ``xapp-<digits>-[A-Z][A-Z0-9]{8,}-<digits>-<32+
    #     alnum body>`` matches the canonical Slack App-Level Token
    #     format (App ID always starts with ``A`` followed by 10+
    #     uppercase alnum chars per Slack docs; 13-digit sequence;
    #     64+ char body — 32-char floor accepts future variations).
    #   * Databricks: ``dapi[a-f0-9]{32}(?:-[0-9]+)?`` strict
    #     lowercase-hex body anchors against placeholder false
    #     positives like the Latin Lorem-Ipsum word "dapibus" and
    #     the literal ``dapi-foo`` placeholder while accepting the
    #     modern ``dapi<hex>-2`` rotation format.
    #
    # The masks preserve issuer-specific prefixes:
    #   * Slack App-Level → ``xapp-***`` (revocation flow at
    #     api.slack.com/apps/<app_id>/general > "App-Level Tokens"
    #     > "Regenerate").
    #   * Databricks → ``dapi***`` (revocation flow at Databricks
    #     workspace UI > User Settings > Developer > Access tokens
    #     > "Revoke" — distinct per workspace).
    #
    # Idempotence: masked forms (``xapp-***``, ``dapi***``) do NOT
    # re-match because ``*`` is OUTSIDE every body alphabet AND
    # the masked body length (3 chars) is below every per-family
    # floor (32 / 32).
    #
    # Cross-family mutex: ``xapp-`` vs. ``dapi`` prefixes are
    # disjoint at the leading-character level (``x`` vs. ``d``),
    # so no token can match both patterns. They are ALSO disjoint
    # from the existing Slack family entries (``xoxb-``/``xoxp-``/
    # etc.) at the second-character level (``a`` vs. ``o``).
    #
    # Marker: SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT.
    (
        r"(?<![A-Za-z0-9])(xapp)-[0-9]+-[A-Z][A-Z0-9]{8,}-[0-9]+-[a-zA-Z0-9]{32,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(dapi)[a-f0-9]{32}(?:-[0-9]+)?(?![A-Za-z0-9])",
        r"\1***",
    ),
    # HubSpot Private App Token (``pat-(?:na1|na2|na3|eu1)-<UUID>``) +
    # PlanetScale Database Token (``pscale_(?:oauth|tkn|pw)_<43>``)
    # value-shape masking. Sibling-drift closure for the 2026-05-18
    # round that extended ``_KNOWN_TOKENS`` in
    # ``src/utils/secret_scanner.py`` to detect each canonical token
    # shape with vendor-specific attribution. Pre-fix the scanner-
    # side coverage was missing for BOTH families AND the log-
    # sanitisation codepath was NOT extended in any prior round —
    # bare token shapes in plain log text (application f-string logs,
    # upstream error responses echoing the token back, JSON values
    # without sensitive key names, URL paths embedding the token,
    # URL query strings with NON-sensitive parameter names) bypassed
    # every existing key/header/URL-credential mask and leaked
    # verbatim to operator log streams plus the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model (mirror the secret-scanner round's blast radius):
    #   * HubSpot ``pat-`` — Full CRM data plane with PII. Leaking
    #     grants the issuing private app's configured scopes against
    #     the HubSpot portal: complete contact database (GDPR-
    #     protected PII at scale), company / deal records, marketing
    #     campaigns + recipient lists, automation workflows, form
    #     submissions. Distinct from every other CRM-vendor rotation
    #     flow.
    #   * PlanetScale ``pscale_<tier>_`` — Per-tier DB control plane
    #     plus data plane: ``oauth_`` mints user OAuth tokens
    #     (multi-user pivot); ``tkn_`` grants Service Token /
    #     PAT-scoped API access (modify schemas, exfiltrate
    #     branch passwords, trigger deploys); ``pw_`` provides
    #     direct MySQL-wire-protocol access (full data
    #     exfiltration / ransomware overwrite primitive).
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``Xpat-na1-...``, ``mypscale_tkn_...`` are
    #     preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * HubSpot: ``pat-(?:na1|na2|na3|eu1)-<8>-<4>-<4>-<4>-<12>``
    #     UUID-shape body anchored against the four documented
    #     HubSpot data-residency regions (na1=US East, na2=US
    #     Central, na3=US West, eu1=Germany).
    #   * PlanetScale: ``pscale_(?:oauth|tkn|pw)_[A-Za-z0-9_-]{43}``
    #     strict 43-char body across three documented tiers.
    #
    # The masks preserve issuer-specific prefixes for IR triage:
    #   * HubSpot → ``pat-***`` (revocation flow at HubSpot portal >
    #     Settings > Account Setup > Integrations > Private Apps >
    #     <App> > Auth tab > "Rotate"). Per-region disambiguation
    #     is intentionally collapsed in the mask — the operator
    #     reads the region from the SAME log line context (the
    #     leaked token's adjacent JSON / env-var name typically
    #     identifies the portal) without needing the region
    #     embedded in the mask.
    #   * PlanetScale → ``pscale_oauth_***`` / ``pscale_tkn_***`` /
    #     ``pscale_pw_***`` (per-tier mask preserves tier
    #     attribution; three distinct revocation panels at
    #     app.planetscale.com require tier identification from the
    #     prefix).
    #
    # Idempotence: masked forms (``pat-***``, ``pscale_tkn_***``)
    # do NOT re-match because ``*`` is OUTSIDE every body alphabet
    # AND the masked body length (3 chars) is below every per-
    # family floor (UUID = 36 / PlanetScale = 43).
    #
    # Cross-family mutex: ``pat-`` vs. ``pscale_`` prefixes are
    # disjoint at the second-character level (``pa`` vs. ``ps``),
    # so no token can match both patterns.
    #
    # Marker: SENTINEL_HUBSPOT_PLANETSCALE_TOKEN_DRIFT.
    (
        r"(?<![A-Za-z0-9])(pat)-(?:na1|na2|na3|eu1)-"
        r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-"
        r"[a-fA-F0-9]{4}-[a-fA-F0-9]{12}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(pscale_(?:oauth|tkn|pw))_[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    # Heroku Platform API Token (``HRKU-<base64url 36+ body>``) +
    # Docker Hub Personal Access Token (``dckr_pat_<base64url 27+
    # body>``) value-shape masking. Sibling-drift closure for the
    # 2026-05-18 round that extended ``_KNOWN_TOKENS`` in
    # ``src/utils/secret_scanner.py`` to detect each canonical token
    # shape with vendor-specific attribution. Pre-fix the scanner-
    # side coverage was missing for BOTH families AND the log-
    # sanitisation codepath was NOT extended in any prior round —
    # bare token shapes in plain log text (application f-string
    # logs, upstream error responses echoing the token back, JSON
    # values without sensitive key names, URL paths embedding the
    # token, URL query strings with NON-sensitive parameter names)
    # bypassed every existing key/header/URL-credential mask and
    # leaked verbatim to operator log streams plus the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model (mirror the secret-scanner round's blast radius):
    #   * Heroku ``HRKU-`` — Full PaaS control plane plus adjacent
    #     data-plane access via add-ons. Leaking grants the issuing
    #     authorization's full Heroku Platform API scope: enumerate
    #     every app, dump every app's config vars (which routinely
    #     embed ``DATABASE_URL`` for Heroku Postgres,
    #     ``REDIS_URL`` for Heroku Redis, ``SENDGRID_API_KEY``,
    #     ``STRIPE_SECRET_KEY``, etc. — the canonical "one
    #     credential leak cascades to many" amplifier), shell into
    #     production via ``heroku run`` (arbitrary code execution
    #     primitive), release backdoored slugs (supply-chain
    #     compromise of the production app), scale dynos (DoS /
    #     cost-amplification attack).
    #   * Docker Hub ``dckr_pat_`` — Supply-chain compromise
    #     primitive. Push backdoored images to ANY repository under
    #     the user's namespace; every downstream consumer pulling
    #     ``user/image:latest`` (CI/CD pipelines, Kubernetes with
    #     ``imagePullPolicy: Always``, ``docker-compose`` without
    #     pinned digest) pulls the backdoor. Docker Hub is a top-3
    #     public registry — compromised base images cascade to
    #     every downstream consumer.
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``XHRKU-...``, ``mydckr_pat_...`` are
    #     preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Heroku: ``HRKU-[A-Za-z0-9_\-]{36,}`` covers both the
    #     UUID-shape (32 hex + 4 dashes = 36 chars) and the
    #     base64url-shape (40+ chars) bodies.
    #   * Docker Hub: ``dckr_pat_[A-Za-z0-9_\-]{27,}`` covers the
    #     documented base64url-ish PAT body shape.
    #
    # The masks preserve issuer-specific prefixes for IR triage:
    #   * Heroku → ``HRKU-***`` (revocation flow at
    #     dashboard.heroku.com/account/applications or
    #     ``heroku authorizations:revoke <id>``). Distinct from
    #     every other PaaS-vendor rotation flow (Render, Vercel,
    #     Fly.io).
    #   * Docker Hub → ``dckr_pat_***`` (revocation flow at
    #     hub.docker.com/settings/security > "Delete"). Distinct
    #     from every other container-registry vendor's revocation
    #     flow (GHCR uses GitHub PATs, AWS ECR uses IAM, GitLab
    #     Container Registry uses GitLab PATs).
    #
    # Idempotence: masked forms (``HRKU-***``, ``dckr_pat_***``)
    # do NOT re-match because ``*`` is OUTSIDE every body alphabet
    # AND the masked body length (3 chars) is below every per-
    # family floor (36 / 27).
    #
    # Cross-family mutex: ``HRKU-`` vs. ``dckr_pat_`` prefixes are
    # disjoint at the leading-character level (``H`` vs. ``d``),
    # so no token can match both patterns.
    #
    # Marker: SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT.
    (
        r"(?<![A-Za-z0-9])(HRKU)-[A-Za-z0-9_\-]{36,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(dckr_pat)_[A-Za-z0-9_\-]{27,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
]
