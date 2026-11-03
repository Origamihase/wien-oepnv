"""Value-shape masks for CI/CD-DevOps, SaaS-comms/secret-manager and further SaaS tokens.

Covers CI/CD-DevOps, SaaS-comms/secret-manager, Bitbucket/Mapbox, Discord and
Atlassian/Sentry/Linear/Notion/Mailgun tokens. Pure data moved out of
``src/utils/logging.py``, which imports it and runs these patterns right
after ``_CLOUD_AI_TOKEN_PATTERNS``.
"""

from __future__ import annotations

# The list keeps a private name; ``src/utils/logging.py`` imports it
# (code scanning counts an unlisted ``_name`` as unused).
__all__ = ["_DEVOPS_SAAS_TOKEN_PATTERNS"]

_DEVOPS_SAAS_TOKEN_PATTERNS: list[tuple[str, str]] = [
    # DevOps / CI/CD Pipeline + DigitalOcean Cloud token-family
    # value-shape masking. Sibling-drift closure for the secret-
    # scanner ``_KNOWN_TOKENS`` entries that detect committed CI/CD
    # tokens across 4 vendors / 13 issuer prefixes:
    #   * GitLab CI/CD pipeline tier (8): ``glrt-`` (Runner Auth),
    #     ``gldt-`` (Deploy), ``glagent-`` (KAS Agent), ``glft-``
    #     (Feature Flag client), ``glimt-`` (Incoming Mail / Service
    #     Desk), ``glcbt-`` (CI Build per-job), ``glsoat-`` (SCIM
    #     OAuth), ``glptt-`` (Pipeline Trigger).
    #   * CircleCI Personal API Token: ``CCIPAT_<32+>``.
    #   * Buildkite (2): ``bkat_`` (Agent), ``bkua_`` (User Access).
    #   * DigitalOcean cloud (2): ``dop_v1_<64 hex>`` (PAT) +
    #     ``doo_v1_<64 hex>`` (OAuth Refresh).
    # The scanner closed the *detection* codepath for committed
    # source files across the 2026-05-16 / 2026-05-17 rounds; the
    # log-sanitisation codepath was NOT extended in any prior
    # round — bare tokens in plain log text (application f-string
    # logs, upstream error responses echoing the token back, JSON
    # values with NON-sensitive key names, URL paths / query
    # strings) bypassed every existing key/header/URL-credential
    # mask pattern and leaked verbatim into operator log streams
    # plus the public ``docs/feed_health.json`` artefact.
    #
    # Threat model per family:
    #   * GitLab Runner / Deploy / Agent / Pipeline Trigger / CI
    #     Build / Feature Flag / Incoming Mail / SCIM OAuth — CI/CD
    #     code-execution on the project's runners, secrets
    #     exfiltration, cluster control plane (KAS), feature-flag
    #     manipulation, SCIM user-provisioning hijack.
    #   * CircleCI ``CCIPAT_`` — full user-scoped REST-API access:
    #     read every accessible pipeline's build logs (which often
    #     include masked-but-echoed env vars), trigger pipelines
    #     with arbitrary parameters, modify project settings.
    #   * Buildkite ``bkat_`` (Agent) — highest leak surface in the
    #     modern CI stack: rogue agents drain the job queue with
    #     whatever build-secret env vars the pipeline exposes.
    #   * Buildkite ``bkua_`` (User Access) — full user-scoped
    #     access across every accessible Buildkite organisation.
    #   * DigitalOcean ``dop_v1_`` — full account API access
    #     (Droplets, Spaces, Databases, Kubernetes clusters across
    #     every project in the account). ``doo_v1_`` mints fresh
    #     ``dop_v1_`` access tokens until revoked at the OAuth
    #     app authorisation page.
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``myglrt-``, ``fooCCIPAT_`` are preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict body lengths per vendor canonical format:
    #     glrt/gldt/glft = exactly 20 chars; glsoat = 20+; glimt =
    #     25+; glagent = 50+; glcbt = ``<alnum>_<20+>``; glptt =
    #     exactly 40; CCIPAT_ = 32+; bkat_/bkua_ = 40+ alnum;
    #     dop_v1_/doo_v1_ = exactly 64 lowercase hex.
    # The mask preserves the issuer-specific prefix
    # (``glrt-***``, ``CCIPAT_***``, ``bkat_***``, ``dop_v1_***``
    # etc.) for incident-response triage — each tier has a distinct
    # revocation flow:
    #   * GitLab CI/CD — project / group / instance access-tokens
    #     pages (each token type has its own admin sub-page).
    #   * CircleCI — app.circleci.com/settings/user/tokens > Revoke.
    #   * Buildkite Agent — buildkite.com/organizations/<org>/agents.
    #   * Buildkite User — buildkite.com/user/api-access-tokens.
    #   * DigitalOcean — cloud.digitalocean.com/account/api/tokens.
    #
    # Idempotence: masked forms (``glrt-***``, ``CCIPAT_***``,
    # ``bkat_***``, ``dop_v1_***``) do NOT match any of these
    # regexes because ``*`` is not in any body alphabet AND the
    # masked body length (3 chars) is below every per-family floor
    # (20/25/27/32/40/50/64).
    #
    # Marker: SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT.
    #
    # Ordering: ``glcbt-`` (special two-part body) listed BEFORE
    # the other GitLab CI/CD prefixes so the more-specific
    # ``glcbt-<alnum>_<20+>`` shape wins first — preserving the
    # full ``glcbt-***`` attribution rather than splitting the
    # body span. The other GitLab prefixes are mutually exclusive
    # at the prefix level (glrt vs gldt vs glft etc.) so order
    # among them is documentation aid only.
    (
        r"(?<![A-Za-z0-9])(glcbt)-[A-Za-z0-9]+_[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(glrt|gldt|glft)-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(glagent)-[A-Za-z0-9_\-]{50,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(glimt)-[A-Za-z0-9_\-]{25,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(glsoat)-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(glptt)-[0-9a-zA-Z_\-]{40}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(CCIPAT)_[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(bkat|bkua)_[A-Za-z0-9]{40,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(dop_v1|doo_v1)_[a-f0-9]{64}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    # SaaS / Communications / Workspace / Observability / Secret-Manager
    # token tier value-shape masking. Sibling-drift closure for the
    # secret-scanner ``_KNOWN_TOKENS`` entries detecting committed
    # tokens across these high-blast-radius issuer families that the
    # log-sanitisation codepath was NOT extended for in any prior
    # round — bare tokens in plain log text (application f-string
    # logs, upstream error responses echoing the token back, JSON
    # values with non-sensitive key names, URL paths / query strings
    # with non-sensitive parameter names) bypassed every existing
    # key/header/URL-credential mask pattern and leaked verbatim
    # into operator log streams plus the public
    # ``docs/feed_health.json`` artefact.
    #
    # Families covered (10 issuers / 12 patterns):
    #
    # Universal Auth tier — the single highest-blast-radius gap:
    #   * ``eyJ<10+>.<10+>.<20+>`` — JSON Web Token (JWT). The
    #     canonical bearer credential for Auth0, Okta, AWS Cognito,
    #     Google Identity, Azure AD, custom OAuth/OIDC providers and
    #     every modern SaaS identity provider. Dots are OUTSIDE the
    #     entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet so without
    #     this specific pattern only ONE segment is matched at a
    #     time and the full-token span (the bearer credential) is
    #     silently lost.
    #
    # Workspace SaaS tier:
    #   * ``ATATT3xFfGF0<100+>`` — Atlassian API Token (Jira /
    #     Confluence / Trello REST API). Full Cloud-API scope.
    #   * ``lin_api_<32+>`` — Linear API Key. Full issue tracker /
    #     project-management GraphQL scope.
    #   * ``secret_<43>`` — Notion Integration Token (legacy
    #     format). Full workspace read/write for shared content.
    #   * ``ntn_<43+>`` — Notion Modern Integration Token. Same
    #     blast radius as the legacy ``secret_`` form.
    #   * ``PMAK-<24 hex>-<34 hex>`` — Postman API Key. Full
    #     workspace collections / environments / mocks scope.
    #
    # Observability tier:
    #   * ``sntrys_<30+>`` — Sentry Auth Token. Org-level Sentry
    #     API access (issue/event data, releases, debug files,
    #     source maps, member list, webhook configuration).
    #
    # Secret-Manager tier — **HIGHEST blast-radius amplifier**:
    #   * ``dp.<pt|st|sa|ct|scim|audit>.<43>`` — Doppler Tokens.
    #     Six role variants. One leaked Doppler token grants read
    #     access to every secret stored in the accessible
    #     projects/configs (database credentials, third-party API
    #     keys, OAuth client secrets, signing keys are all
    #     routinely stored in Doppler environments). One leak
    #     compromises every downstream credential.
    #
    # Communications tier:
    #   * ``<3-14 digits>:<35>`` — Telegram Bot Token. Full bot
    #     impersonation in every chat the bot is added to.
    #   * ``AC<32 hex>`` — Twilio Account SID. Principal credential
    #     for the project; pairs with the Auth Token for full
    #     telephony API access. 2FA-bypass primitive via SMS.
    #   * ``SK<32 hex>`` — Twilio API Key SID. Fine-grained scoped
    #     credential; pairs with a separate secret.
    #   * ``key-<32 hex>`` — Mailgun Private API Key. Mail-send
    #     access from the project's authenticated domain
    #     (phishing amplification leveraging SPF / DKIM).
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``myATATT3xFfGF0``, ``foosntrys_`` are
    #     preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict body lengths / alphabets per vendor canonical
    #     format reject accidental fragments while accepting every
    #     real-shape token.
    # Each mask preserves the issuer-specific prefix (``eyJ***``,
    # ``ATATT3xFfGF0***``, ``sntrys_***``, ``dp.pt.***``,
    # ``PMAK-***``, ``key-***`` etc.) for incident-response triage
    # because each tier has a distinct revocation flow.
    #
    # Idempotence: masked forms (``eyJ***``, ``ATATT3xFfGF0***``,
    # ``sntrys_***``, ``dp.pt.***``, ``PMAK-***``, etc.) do NOT
    # match any of these regexes because ``*`` is not in any body
    # alphabet AND the masked body length (3 chars) is below every
    # per-family floor (20/30/32/35/43/100).
    #
    # Marker: SENTINEL_SAAS_COMMS_SECRET_MANAGER_TOKEN_LOG_SANITIZATION_DRIFT.
    #
    # Mapbox Access Token value-shape masking. Sibling-drift closure
    # for the secret-scanner ``_KNOWN_TOKENS`` entry that detects
    # committed Mapbox tokens
    # (``(?:pk|sk|tk)\.eyJ<3-segment-JWT-body>``). The scanner closes
    # the *detection* codepath via a dedicated entry placed BEFORE
    # the generic JWT entry so the more-specific Mapbox attribution
    # wins via the ``covered_ranges`` arbitration; this mask closes
    # the *sanitisation* codepath via a dedicated regex placed
    # BEFORE the generic JWT regex so the more-specific replacement
    # wins before the JWT mask strips the inner ``eyJ<body>`` span.
    #
    # ORDER REQUIREMENT: this entry MUST appear BEFORE the generic
    # JWT mask immediately below — otherwise the JWT regex matches
    # the inner ``eyJ<body>`` first and replaces it with ``eyJ***``,
    # leaving ``pk.eyJ***`` / ``sk.eyJ***`` / ``tk.eyJ***`` in the
    # output (the leading scope-tier prefix is preserved by
    # accident, but the IR triage path sees a JWT-attributed
    # finding rather than a Mapbox-attributed one — the operator
    # might not realise the leaked credential demands the
    # account.mapbox.com revocation flow rather than a generic
    # JWT-issuer revocation flow). Placing Mapbox first preserves
    # the full ``(?:pk|sk|tk)\.eyJ***`` attribution span so the
    # responder identifies the Mapbox scope tier at a glance.
    #
    # Threat model per scope tier (see the matching scanner entry
    # in ``src/utils/secret_scanner.py`` for full per-tier blast
    # radius analysis):
    #   * ``pk.`` — Public Access Token. Quota theft (overage
    #     billing fraud), DDoS-amplification against the issuing
    #     account's Mapbox quota.
    #   * ``sk.`` — **SECRET Access Token. HIGHEST blast radius in
    #     the Mapbox family.** Full account-write scopes:
    #     overwrite production tilesets (route-hijack / map-
    #     manipulation amplifier), mint new ``sk.`` tokens for
    #     persistence, exfiltrate billing / analytics data,
    #     manipulate the production maps consumed by downstream
    #     consumers and integrators.
    #   * ``tk.`` — Temporary Access Token (ephemeral scope,
    #     short TTL). Distinct attribution lets IR verify the
    #     leak window aligned with token TTL.
    #
    # Structural anchors mirror the scanner regex exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-identifier
    #     false positives (``foosk.eyJ...`` is NOT matched because
    #     ``s`` is preceded by ``o`` alphanumeric — the existing
    #     JWT mask still catches the inner JWT in that pathological
    #     case so security is preserved, just via the JWT
    #     attribution rather than Mapbox).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict ``[A-Za-z0-9_\-]`` per-segment base64url alphabet
    #     mirrors the JWT mask; ``{10,10,20}`` per-segment floors
    #     mirror the JWT mask AND match real-world Mapbox token
    #     shapes.
    # The mask preserves the issuer-specific scope-tier prefix
    # (``pk.eyJ***`` / ``sk.eyJ***`` / ``tk.eyJ***``) for IR
    # triage — the operator immediately identifies the Mapbox
    # scope tier and navigates to account.mapbox.com/access-tokens/
    # for the appropriate revocation flow.
    #
    # Idempotence: masked forms (``pk.eyJ***`` / ``sk.eyJ***`` /
    # ``tk.eyJ***``) do NOT re-match this regex because ``*`` is
    # OUTSIDE the per-segment alphabet ``[A-Za-z0-9_\-]`` AND the
    # masked body length (3 chars per segment) is below the
    # per-segment floor (10/10/20). The JWT mask immediately
    # below ALSO does not re-match (the ``eyJ`` anchor still
    # requires 10+ chars after, which the masked ``***`` form
    # does not satisfy).
    #
    # Marker: SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT.
    (
        r"(?<![A-Za-z0-9])((?:pk|sk|tk)\.eyJ)"
        r"[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}"
        r"(?![A-Za-z0-9])",
        r"\1***",
    ),
    (
        r"(?<![A-Za-z0-9])(eyJ)[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])",
        r"\1***",
    ),
    # Bitbucket App Password / Repository Access Token value-shape
    # masking. Sibling-drift closure for the secret-scanner
    # ``_KNOWN_TOKENS`` entry that detects committed Bitbucket
    # tokens (``ATBB<24+ alnum body>``). The scanner closes the
    # *detection* codepath for committed source files (placed near
    # the Atlassian Cloud API Token entry); this mask closes the
    # *sanitisation* codepath via the matching value-shape regex.
    # Without this mask, a bare ``ATBB<body>`` token shape in
    # plain log text (application f-string logs, upstream
    # Bitbucket API error responses echoing the token back, JSON
    # values with non-sensitive key names, URL paths embedding
    # the token, exception messages routed through
    # ``_sanitize_exception_msg``) bypasses every existing
    # key/header/URL-credential mask pattern and leaks verbatim
    # into operator log streams and the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model (mirror the scanner's blast-radius analysis):
    # leak grants the issuing principal's Bitbucket Cloud scope
    # per the token's configured permissions:
    #   * ``repository:read`` — code disclosure, IP exfiltration.
    #   * ``repository:write`` — backdoored commits to protected
    #     branches (supply-chain compromise primitive).
    #   * ``workspace:admin`` — modify workspace member roles,
    #     add attacker collaborators, exfiltrate every repo in
    #     the workspace.
    #   * App Password tier — additionally grants the issuing
    #     user's read access across EVERY accessible workspace
    #     (multi-workspace pivot amplifier).
    # Blast radius mirrors the GitHub PAT (``ghp_***``) and
    # GitLab PAT (``glpat-***``) families on their respective
    # platforms.
    #
    # The mask preserves the issuer-specific prefix (``ATBB***``)
    # for IR triage — the revocation flow lives at
    # bitbucket.org/account/settings/app-passwords/ (App
    # Passwords) or the per-project / per-workspace / per-repo
    # Access Tokens settings pages (resource-scoped variants).
    # All these revocation flows are DISTINCT from the Atlassian
    # Cloud API Token revocation flow
    # (id.atlassian.com/manage-profile/security/api-tokens) used
    # for the existing ``ATATT3xFfGF0***`` mask, so per-issuer
    # attribution is critical for IR triage to land on the
    # correct admin panel.
    #
    # Structural anchors mirror the scanner regex exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-identifier
    #     false positives (``XATBB<body>``, ``0ATBB<body>`` are
    #     preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict ``[A-Za-z0-9]`` body alphabet (no ``_-``)
    #     distinguishes Bitbucket from the GitHub / GitLab / NPM
    #     token families and rejects accidental fragments.
    #   * 24-char body floor matches the permissive minimum for
    #     legacy Bitbucket Server access tokens while accepting
    #     every real-world Bitbucket Cloud Repository / Workspace
    #     / Project Access Token (canonical 32-40 char body).
    #
    # Idempotence: masked form (``ATBB***``) does NOT re-match
    # because ``*`` is OUTSIDE the body alphabet ``[A-Za-z0-9]``
    # AND the masked body length (3 chars) is below the 24-char
    # floor.
    #
    # Cross-family mutex: ``ATBB`` vs. ``ATATT3xFfGF0`` prefixes
    # are disjoint at the 5th-character level (``ATBB`` vs.
    # ``ATATT3xFfGF0`` — the latter is 12 chars and starts with
    # ``ATATT`` after the 4th char), so no token can match both
    # patterns and the existing Atlassian Cloud API Token mask
    # remains correctly attributed.
    (
        r"(?<![A-Za-z0-9])(ATBB)[A-Za-z0-9]{24,}(?![A-Za-z0-9])",
        r"\1***",
    ),
    # Discord Bot Token: ``<base64url(snowflake-id)>.<base64url(
    # timestamp)>.<HMAC>``. Three dot-separated base64url segments —
    # structurally identical to JWT but with a snowflake-ID-based
    # first segment instead of the JOSE ``eyJ`` header. Discord
    # stringifies the user ID (decimal digits) before base64-
    # encoding, so the first segment ALWAYS starts with ``[MNO]``
    # (decimal ``1``-``3`` → ``M``, ``4``-``7`` → ``N``, ``8``-``9``
    # → ``O``); JWTs ALWAYS start with ``eyJ``. The two leading-
    # character classes are disjoint, so no token can match both
    # patterns and the JWT regex above is mutually exclusive at the
    # leading-char level. Sibling-drift closure for the 2026-05-08
    # Round-3 secret-scanner round that added the Discord pattern
    # to ``_KNOWN_TOKENS`` but left the log-sanitisation codepath
    # untouched — Discord was explicitly named in the deferred
    # backlog of every 2026-05-17 multi-vendor / SaaS / supply-
    # chain log-sanitisation round but never closed until now.
    #
    # Threat model: a leaked Discord bot token grants FULL bot
    # privileges in every guild the bot is invited to (read/write
    # all visible messages, kick/ban users, edit channels and
    # roles, run any registered slash commands, read voice/DM
    # history with appropriate scopes). The revocation flow lives
    # at https://discord.com/developers/applications/ Developer
    # Portal — distinct from any other vendor's, so issuer-
    # specific attribution accelerates IR triage. Pre-fix the dots
    # are OUTSIDE the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet, so without this specific pattern only ONE segment
    # at a time matches against the existing key/header/URL
    # masks, losing both the issuer attribution AND the full
    # credential span.
    #
    # The mask preserves the leading ``[MNO]`` Discord-shape
    # disambiguator AND the three-segment structure
    # (``M***.***.***``) so IR triage immediately recognises the
    # Discord shape (vs. JWT ``eyJ***`` shape). Idempotence:
    # ``M***.***.***`` does NOT re-match because ``*`` is not in
    # the body alphabet AND the masked segment length (3 chars)
    # is below every per-segment floor (22/6/27).
    #
    # Marker: SENTINEL_DISCORD_BOT_TOKEN_LOG_SANITIZATION_DRIFT.
    (
        r"(?<![A-Za-z0-9])([MNO])[A-Za-z0-9_\-]{22,27}\.[A-Za-z0-9_\-]{6,7}\.[A-Za-z0-9_\-]{27,}(?![A-Za-z0-9])",
        r"\1***.***.***",
    ),
    (
        r"(?<![A-Za-z0-9])(ATATT3xFfGF0)[A-Za-z0-9_=\-]{100,}(?![A-Za-z0-9])",
        r"\1***",
    ),
    (
        r"(?<![A-Za-z0-9])(sntrys)_[A-Za-z0-9_=\-]{30,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(lin_api)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    # Notion: ``secret_<43 alnum>`` legacy + ``ntn_<43+ extended>``
    # modern. Two patterns because the bodies have different
    # alphabets (legacy strict alnum, modern includes ``_``/``-``)
    # and different floor lengths, so a single combined regex would
    # either under-match the modern form or over-match the legacy
    # one.
    (
        r"(?<![A-Za-z0-9])(secret)_[A-Za-z0-9]{43}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(ntn)_[A-Za-z0-9_\-]{43,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(PMAK)-[a-fA-F0-9]{24}-[a-fA-F0-9]{34}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    # Doppler: multi-segment ``dp.<role>.<43 alnum>``. The two
    # literal ``.`` separators are outside the entropy fallback's
    # alphabet — the canonical "entropy-fallback-bypass" shape.
    # Role alternation matches the six documented Doppler token
    # roles (personal / service / service-account / CLI / SCIM
    # / audit) — any other role suffix is rejected.
    (
        r"(?<![A-Za-z0-9])(dp\.(?:pt|st|sa|ct|scim|audit))\.[A-Za-z0-9]{43}(?![A-Za-z0-9])",
        r"\1.***",
    ),
    # Telegram Bot Token: ``<3-14 digits>:<35 chars from
    # [A-Za-z0-9_-]>``. The bot-ID (digits) is not secret on its
    # own (Telegram exposes bot user IDs publicly) but masking
    # only the body after ``:`` preserves the bot-identification
    # span for incident-response triage while still suppressing
    # the credential. Marker pattern: capture the digit prefix
    # and replace only the colon-separated body.
    (
        r"(?<![A-Za-z0-9])([0-9]{3,14}):[a-zA-Z0-9_\-]{35}(?![A-Za-z0-9])",
        r"\1:***",
    ),
    # Twilio Account SID + API Key SID: uppercase ``AC``/``SK``
    # + 32 lowercase hex. Case-sensitive: ``sk-<48>`` (OpenAI
    # legacy) and ``sk_live_<24>`` (Stripe) are lowercase + have
    # a dash/underscore inside the prefix, so they are mutually
    # exclusive with the Twilio shape. The 32 lowercase-hex body
    # is the canonical Twilio SID format.
    (
        r"(?<![A-Za-z0-9])(AC|SK)[a-f0-9]{32}(?![A-Za-z0-9])",
        r"\1***",
    ),
    # Mailgun Private API Key: ``key-<32 lowercase hex>``. The
    # ``key-`` prefix is intentionally short — strict 32-char
    # lowercase-hex body is the disambiguator. The ``(?<![A-Za-z0-9])``
    # lookbehind prevents ``api-key-<hex>``, ``foo-key-<hex>`` and
    # similar legitimate placeholder shapes from being falsely
    # masked.
    (
        r"(?<![A-Za-z0-9])(key)-[a-f0-9]{32}(?![A-Za-z0-9])",
        r"\1-***",
    ),
]
