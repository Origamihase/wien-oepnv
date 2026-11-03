"""Part of ``_KNOWN_TOKENS`` of the secret scanner:
Discord, Atlassian, Bitbucket, Sentry, Linear, Brevo, Postman, HashiCorp
Vault, Doppler, Buildkite, Netlify, Render, New Relic, Fly.io.

Moved verbatim from ``src/utils/secret_scanner.py``; the facade joins the parts
in their original order (the first matching entry wins).
"""

from __future__ import annotations

import re

KNOWN_TOKENS_DEVTOOLS_SAAS = [
    # Discord Bot Token: ``<base64url(user-id)>.<base64url(timestamp)>.<HMAC>``.
    # Three dot-separated base64url segments — structurally identical to
    # JWTs but with the snowflake-ID-based first segment instead of the
    # JOSE ``eyJ`` header. Discord stringifies the user ID (decimal
    # digits) before base64-encoding it, so the first segment ALWAYS
    # starts with the base64 encoding of the leading decimal digit:
    # ``1``-``3``→``M``, ``4``-``7``→``N``, ``8``-``9``→``O``. Every
    # snowflake user ID starts with a single decimal digit (1-9), so
    # ``[MNO]`` is a complete leading-character constraint and is the
    # disambiguator from JWTs (which always start with ``eyJ``). The
    # mutual exclusion is enforced at the leading-character level: no
    # token can match both the JWT and Discord patterns.
    #
    # A leaked bot token grants the attacker FULL bot privileges in
    # every guild the bot is invited to (read/write all visible
    # messages, kick/ban users, edit channels and roles, run any
    # registered slash commands, with appropriate scopes read voice/DM
    # history). The dots are outside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet, so without this specific pattern
    # only ONE segment is matched at a time — the full-token span (and
    # the Discord-specific reason needed for revocation at the
    # https://discord.com/developers/applications/ Developer Portal)
    # would be lost. Body-length quantifiers: first segment 24+ chars
    # (real-world snowflake-IDs base64-encode to 24-28 chars), second
    # segment exactly 6 chars (4-byte timestamp), third segment 27+
    # chars (HMAC-SHA256 truncation). Order: place AFTER JWT so a
    # JWT-shape token whose first segment happens to start with [MNO]
    # (impossible in practice but guarded structurally) would still
    # match the JWT pattern first via ``is_covered``.
    (
        re.compile(
            r"(?<![A-Za-z0-9])[MNO][A-Za-z0-9_\-]{22,27}\.[A-Za-z0-9_\-]{6,7}\.[A-Za-z0-9_\-]{27,}(?![A-Za-z0-9])"
        ),
        "Discord Bot Token gefunden",
    ),
    # Atlassian Cloud API Token (``ATATT3xFfGF0<base64 body><CRC32 hex>``).
    # Issued via id.atlassian.com/manage-profile/security/api-tokens for
    # Jira / Confluence / Trello Cloud REST-API access. The canonical
    # format is a 12-char unique prefix (``ATATT3xFfGF0``) followed by
    # ~184 base64url-alphabet body chars and an 8-char CRC32 hex suffix
    # — total ~204 chars in observed real tokens. The body alphabet is
    # ``[A-Za-z0-9_=\-]`` (base64url + ``=`` padding); the prefix is
    # unambiguous (no other major issuer uses ``ATATT3xFfGF0``) so a
    # 100+ body length (well below the canonical ~192 chars) provides a
    # safe lower bound that rejects accidental ``ATATT3``-prefixed
    # fragments while accepting every legitimate token. A leak grants
    # the issuing user's full Cloud-API scope across every accessible
    # workspace (read every Jira issue/page, post comments, transition
    # tickets, browse Confluence pages, manipulate Trello boards) — the
    # revocation flow lives at id.atlassian.com and is distinct from
    # any other vendor's, so issuer-specific attribution accelerates
    # IR triage. Pre-fix the body matched the entropy fallback as a
    # generic high-entropy span; the prefix and the CRC32 suffix were
    # silently lost.
    (
        re.compile(r"(?<![A-Za-z0-9])ATATT3xFfGF0[A-Za-z0-9_=\-]{100,}(?![A-Za-z0-9])"),
        "Atlassian API Token gefunden",
    ),
    # Bitbucket App Password / Repository Access Token (``ATBB<24+ alnum
    # body>``). Issued via bitbucket.org/account/settings/app-passwords/
    # (App Password — user-scoped, the common shape for CLI / git-over-
    # HTTPS) or via per-repository / workspace / project settings >
    # Access Tokens (resource-scoped, used by deploy scripts and CI/CD
    # pipelines). Sibling-drift closure for the 2026-05-16 Atlassian
    # Cloud API Token (``ATATT3xFfGF0``) round above: same issuer
    # (Atlassian Corporation), distinct product (Bitbucket vs. Jira /
    # Confluence / Trello), distinct prefix (``ATBB`` vs.
    # ``ATATT3xFfGF0``), DISTINCT REVOCATION FLOW
    # (bitbucket.org/account/settings/app-passwords/ for App Passwords
    # plus the project/repo/workspace Access Tokens settings pages for
    # the resource-scoped variants; ALL distinct from
    # id.atlassian.com/manage-profile/security/api-tokens for the
    # Atlassian Cloud API Tokens). Per-issuer attribution is critical
    # for IR triage because the operator must navigate to the correct
    # admin panel — confusing Bitbucket with Atlassian Cloud sends the
    # responder to the WRONG settings page.
    #
    # Format: canonical trufflehog / gitleaks default rule for
    # Bitbucket Access Tokens is ``ATBB[a-zA-Z0-9]{32}([a-fA-F0-9]{8})?``
    # — strict 32-char alphanumeric body plus optional 8-char CRC32
    # suffix (total 36 or 44 chars after the ``ATBB`` prefix). Real-
    # world tokens land in the 36-44 char body range. The ``{24,}``
    # lower bound here is intentionally permissive to cover historical
    # variants (the legacy short Bitbucket Server access-token format
    # had a 24-char body) while still rejecting accidental fragments
    # via the strict ``[A-Za-z0-9]`` body alphabet (no underscores or
    # hyphens — distinguishes from the GitHub / GitLab / NPM token
    # families which all use ``[A-Za-z0-9_-]``).
    #
    # Pre-fix detection gaps (mirror the Figma + Tailscale round):
    #   1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): the body
    #      alphabet ``[A-Za-z0-9]`` lies INSIDE the entropy alphabet
    #      ``[A-Za-z0-9+/=_-]`` so the full ``ATBB<body>`` span DID
    #      match generically as ``Hochentropischer Token-String`` —
    #      BUT the Bitbucket-specific issuer attribution that anchors
    #      the revocation flow at bitbucket.org/account/settings/
    #      app-passwords/ was LOST. The generic high-entropy reason
    #      forces the operator to manually identify the issuer from
    #      surrounding context, slowing IR triage.
    #   2. **Log sanitisation codepath**: the bare ``ATBB<body>`` token
    #      shape in plain log text (application f-string logs,
    #      Bitbucket API error responses echoing the token back, JSON
    #      values with non-sensitive keys, URL paths embedding the
    #      token) leaked verbatim — no value-shape mask existed for
    #      this token family.
    #
    # Threat model: a leaked Bitbucket Repository Access Token grants
    # the issuing principal's full Bitbucket Cloud scope per the
    # token's configured permissions (read / write / admin on the
    # specific repo / workspace / project). Common scope combinations:
    #   * ``repository:read + pullrequest:read`` — code-disclosure leak,
    #     IP exfiltration, source-control reconnaissance.
    #   * ``repository:write`` — push backdoored commits to protected
    #     branches (canonical supply-chain compromise primitive on
    #     Bitbucket-hosted projects).
    #   * ``workspace:admin`` — modify workspace member roles, add
    #     attacker accounts as collaborators, exfiltrate every repo
    #     in the workspace, rotate every other workspace token to
    #     maintain persistence.
    #   * App Password tier — additionally grants the user's read
    #     access across EVERY accessible workspace (multi-workspace
    #     pivot amplifier).
    # Blast radius mirrors the GitHub PAT (``ghp_``) and GitLab PAT
    # (``glpat-``) families on their respective platforms — the
    # repository-host control plane is the highest leak surface for
    # source-control-driven supply-chain compromise.
    (
        re.compile(r"(?<![A-Za-z0-9])ATBB[A-Za-z0-9]{24,}(?![A-Za-z0-9])"),
        "Bitbucket Access Token gefunden",
    ),
    # Sentry Auth Token (``sntrys_<base64-with-embedded-JSON>``).
    # Sentry's modern rotation-aware auth-token format (introduced
    # 2023; replaces the legacy 32/64-hex internal tokens). The body
    # encodes an embedded JSON payload describing the organisation /
    # scope plus a trailing checksum guarding against typo-induced
    # cross-token confusion. Body alphabet is ``[A-Za-z0-9_=\-]``
    # (base64url + ``=`` padding + the underscore separator between
    # body and checksum). Total length 60-100+ chars in practice; the
    # 30+ body lower bound rejects short ``sntrys_``-prefixed
    # fragments while accepting every legitimate token. A leak grants
    # access to the Sentry org-level API
    # (``/api/0/organizations/<slug>/...``) — every project's issue /
    # event data, releases, debug files, source maps, member list and
    # webhook configuration — full IR-relevant blast radius. The
    # revocation flow lives at sentry.io/settings/auth-tokens/ and is
    # distinct from any other vendor's. Pre-fix the body matched the
    # entropy fallback as a generic high-entropy span; the
    # ``sntrys_`` prefix that anchors revocation was silently lost.
    (
        re.compile(r"(?<![A-Za-z0-9])sntrys_[A-Za-z0-9_=\-]{30,}(?![A-Za-z0-9])"),
        "Sentry Auth Token gefunden",
    ),
    # Linear API Key (``lin_api_<32+ alphanumeric chars>``). Issued via
    # linear.app/settings/api for personal API access against the
    # Linear (issue tracker / project management) GraphQL API. A leak
    # grants the issuing user's full Linear scope: read/write every
    # visible issue, comment, attachment, project, team metadata and
    # webhook configuration. The ``lin_api_`` prefix is unambiguous
    # (no other major issuer uses it), and the strict alphanumeric
    # body (no ``_``/``-`` after the prefix in canonical Linear
    # format) avoids overlap with the hyphenated bodies of other
    # tokens (``glpat-``, ``ghp_`` family). Body lower bound 32 chars
    # rejects short ``lin_api_`` fragments while accepting the
    # canonical 40-char-body shape; the 32-char floor matches the
    # historic minimum observed in older Linear tokens. The
    # revocation flow lives at linear.app/settings/api and is distinct
    # from any other vendor's, so issuer-specific attribution
    # accelerates IR triage. Pre-fix the entropy fallback flagged the
    # ``lin_api_<body>`` span generically (the underscore is in the
    # alphabet) without preserving the Linear-specific issuer name
    # that incident response keys off.
    (
        re.compile(r"(?<![A-Za-z0-9])lin_api_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"),
        "Linear API Key gefunden",
    ),
    # Brevo (formerly Sendinblue) v3 API Key
    # (``xkeysib-<64 lowercase hex>-<16 alphanumeric>``). Issued via
    # app.brevo.com/settings/keys/api for transactional email,
    # marketing-automation, contacts, SMS-API and webhook configuration
    # access. Total length 89 chars (8-char prefix + 64-char hex secret
    # + 1 dash + 16-char alphanumeric request-id-like suffix). The
    # ``xkeysib-`` prefix is unambiguous (no other major issuer uses
    # it), and the strict 64-hex secret + 16-alphanumeric suffix matches
    # Brevo's documented canonical format. A leak grants the issuing
    # account's full transactional-mail / contacts API scope: the
    # attacker can send mail FROM the project's domain (phishing
    # amplification leveraging existing SPF / DKIM authentication),
    # exfiltrate the contact list, register webhooks redirecting
    # delivery events to attacker-controlled endpoints, or modify
    # campaign templates. The revocation flow lives at
    # https://app.brevo.com/settings/keys/api and is distinct from
    # any other vendor's, so issuer-specific attribution accelerates
    # IR triage. Pre-fix the entropy fallback's
    # ``[A-Za-z0-9+/=_-]{24,}`` regex matches the full token span as a
    # single "Hochentropischer Token-String" finding (hyphen is in the
    # alphabet) WITHOUT preserving the Brevo-specific issuer name.
    (
        re.compile(r"(?<![A-Za-z0-9])xkeysib-[a-f0-9]{64}-[A-Za-z0-9]{16}(?![A-Za-z0-9])"),
        "Brevo (Sendinblue) API Key gefunden",
    ),
    # Postman API Key (``PMAK-<24 hex>-<34 hex>``). Issued via
    # postman.com/settings/me/api-keys for full Postman REST-API access:
    # read/write every accessible workspace's collections, environments,
    # mocks, monitors, and team membership. Total length 64 chars
    # (5-char prefix + 24-char hex + 1 dash + 34-char hex). The ``PMAK-``
    # prefix is unambiguous (no other major issuer uses uppercase
    # ``PMAK-``), and the strict hex body avoids overlap with the
    # entropy fallback's broader alphabet. A leak grants the issuing
    # user's full Postman API scope across every workspace they belong
    # to, including private API definitions and mock-server URLs that
    # may carry embedded credentials. The revocation flow lives at
    # postman.com/settings/me/api-keys and is distinct from any other
    # vendor's. Pre-fix the entropy fallback flagged the body+suffix
    # as a generic high-entropy span, losing the Postman attribution.
    (
        re.compile(r"(?<![A-Za-z0-9])PMAK-[a-fA-F0-9]{24}-[a-fA-F0-9]{34}(?![A-Za-z0-9])"),
        "Postman API Key gefunden",
    ),
    # HashiCorp Cloud Platform (HCP) Vault Secrets token (``hvs.<base64
    # body>``). Issued via portal.cloud.hashicorp.com for HCP Vault
    # Secrets API access (the managed-Vault offering — read every
    # secret stored in the namespace's apps and integrations). The
    # same ``hvs.`` prefix is also issued by self-hosted HashiCorp
    # Vault (Enterprise + Community) since the 1.10 release
    # (2022-03) for persistent ``service``-type tokens (default token
    # shape, written to Vault's storage backend; companion to the
    # ``hvb.`` batch token + ``hvr.`` recovery token siblings closed
    # in the entries immediately below this one). Total length
    # typically 95-110 chars (4-char prefix incl. dot + 90+ char
    # base64url body). The literal ``.`` separator disambiguates
    # from any alphanumeric-prefixed token already in the table. A
    # leak grants whoever holds the token full read-access to every
    # secret the issuing service principal / human user can see —
    # the highest blast-radius credential class in the modern infra
    # stack. The revocation flow lives at portal.cloud.hashicorp.com
    # (HCP) or ``vault token revoke <token>`` (self-hosted) and is
    # distinct from any other vendor's, so issuer-specific
    # attribution is critical for IR triage. Pre-fix the entropy
    # fallback flagged the body as a generic high-entropy span (the
    # ``.`` is OUTSIDE the entropy alphabet ``[A-Za-z0-9+/=_-]``, so
    # only the body span after ``hvs.`` matched), losing the
    # HCP-specific issuer attribution.
    (
        re.compile(r"(?<![A-Za-z0-9])hvs\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])"),
        "HCP Vault Secrets Token gefunden",
    ),
    # HashiCorp Vault Batch Token (``hvb.<base64url body>``). The
    # canonical batch-token prefix for self-hosted HashiCorp Vault
    # (Vault Enterprise + Vault Community) since the 1.10 release
    # (2022-03; replaces the legacy ``b.`` prefix). Sibling-drift
    # closure for the ``hvs.`` Vault Service Token detector above:
    # the two prefixes are issued by the SAME Vault auth-method API
    # call (``auth/token/create``) with different ``type`` parameters
    # — ``service`` produces the persistent ``hvs.`` token (default,
    # written to Vault's storage backend), ``batch`` produces the
    # ephemeral, lightweight ``hvb.`` token (NOT written to storage
    # — Vault encrypts the token's auth data into the token itself,
    # which means batch tokens scale to high-throughput workloads
    # without storage backend pressure). Pre-fix the ``hvb.`` shape
    # had NO ``_KNOWN_TOKENS`` entry and the entropy fallback
    # flagged only the body span after ``hvb.`` (the ``.`` is
    # OUTSIDE the entropy alphabet ``[A-Za-z0-9+/=_-]``) as a
    # generic ``Hochentropischer Token-String`` finding, losing the
    # Vault-specific issuer attribution that incident-response
    # triage keys off.
    #
    # Threat model: batch tokens are typically issued for CI/CD
    # pipelines, ephemeral container workloads, and serverless
    # function invocations — the exact contexts where token leaks
    # in committed source are MOST likely. A leaked batch token
    # grants the issuing policy's full Vault scope for the token's
    # TTL: read every KV secret the policy permits (database creds,
    # cloud provider keys, internal API tokens, OAuth client
    # secrets routinely stored in Vault), generate dynamic
    # secrets (database credentials, AWS STS tokens via the
    # ``aws/sts`` mount, GCP service-account tokens via the
    # ``gcp/`` mount, PKI certificates via the ``pki/`` mount),
    # and — if the policy includes ``encrypt`` capability on a
    # ``transit/`` mount — encrypt/decrypt arbitrary application
    # data. Blast radius is the same per-policy scope as
    # ``hvs.`` service tokens, scoped only by the batch token's
    # TTL (typically minutes to hours, but configurable to days).
    # The revocation flow lives at ``vault token revoke <token>``
    # (or the API equivalent ``POST /v1/auth/token/revoke``) and
    # is the same revocation flow as ``hvs.`` service tokens
    # (Vault treats both as first-class auth tokens for
    # revocation), so issuer-specific attribution accelerates IR
    # triage to the correct Vault cluster's audit log + revoke
    # endpoint.
    (
        re.compile(r"(?<![A-Za-z0-9])hvb\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])"),
        "HashiCorp Vault Batch Token gefunden",
    ),
    # HashiCorp Vault Recovery Token (``hvr.<base64url body>``). The
    # canonical recovery-token prefix for self-hosted HashiCorp Vault
    # since 1.10 release (replaces the legacy ``r.`` prefix). Recovery
    # tokens are issued ONLY in HSM-backed or auto-unseal Vault
    # deployments (the Enterprise tier or the Cloud KMS / AWS KMS /
    # Azure Key Vault / GCP CKMS auto-unseal flows) via the
    # ``generate-recovery-token`` API (``POST /v1/sys/generate-
    # recovery-token``). They authorise the highest-privilege
    # recovery operations on a sealed or partially-sealed Vault
    # cluster — operations a regular root token CANNOT perform when
    # Vault is in a degraded state (sealed / cluster-leader-failover
    # / lost-quorum scenarios). Sibling-drift closure for the
    # ``hvs.`` Vault Service Token detector above: same Vault
    # cluster, same on-the-wire encoding (base64url body after the
    # dotted prefix), distinct privilege tier.
    #
    # Threat model (HIGHEST severity in the HashiCorp Vault token
    # family): a leaked recovery token grants the holder root-
    # equivalent operations on the sealed Vault cluster — including
    # the ability to GENERATE A NEW ROOT TOKEN
    # (``POST /v1/sys/generate-root``) once Vault is unsealed, which
    # is then a persistent backdoor with FULL Vault administrative
    # capability. The compromised root token can subsequently:
    #   * Read every secret in every namespace / KV mount.
    #   * Modify every policy attached to every token.
    #   * Disable audit logging to cover tracks.
    #   * Add new auth methods (LDAP, Kerberos, OIDC, AppRole) for
    #     persistent attacker access independent of the root
    #     token's eventual revocation.
    #   * Mint new dynamic secrets (cloud provider credentials,
    #     database admin accounts) that outlive the Vault breach.
    # Pre-fix the ``hvr.`` shape had NO ``_KNOWN_TOKENS`` entry
    # and the entropy fallback only flagged the body span after
    # ``hvr.`` as a generic ``Hochentropischer Token-String``
    # finding, losing both the Vault-recovery-specific issuer
    # attribution AND the cluster-recovery-flow-specific incident
    # response surface (operator must immediately re-seal the
    # cluster, regenerate the recovery-key shares via Shamir
    # threshold reconstruction, and audit every operation since
    # the recovery token was issued for evidence of a generated
    # root token). The revocation flow lives at
    # ``POST /v1/sys/generate-recovery-token/attempt`` (cancel +
    # restart the recovery flow) and is distinct from regular
    # token revocation, so issuer-specific attribution is
    # critical for IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])hvr\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])"),
        "HashiCorp Vault Recovery Token gefunden",
    ),
    # Doppler tokens (``dp.<role>.<43 alphanumeric body>`` where
    # ``<role>`` is one of ``pt`` / ``st`` / ``sa`` / ``ct`` / ``scim``
    # / ``audit``). Issued via dashboard.doppler.com for Doppler's
    # secrets-management API. The six roles correspond to:
    # personal-token (``pt``), service-token (``st``), service-account
    # token (``sa``), CLI token (``ct``), SCIM provisioning token
    # (``scim``) and audit-log token (``audit``). Total length 49-52
    # chars (3-char ``dp.`` prefix + 2-5 char role + 1 dot + 43-char
    # alphanumeric body). The literal ``.`` separators are OUTSIDE the
    # entropy fallback's alphabet ``[A-Za-z0-9+/=_-]``, so the entropy
    # regex matches only the 43-char body span — losing both the
    # ``dp.<role>.`` prefix AND the Doppler issuer attribution that
    # incident-response triage keys off. A leak grants the issuing
    # principal's full Doppler scope across every project / config
    # they can see — read every secret (database creds, third-party
    # API keys, OAuth client secrets, signing keys are all routinely
    # stored in Doppler environments), modify config branches, and
    # exfiltrate the audit log. The revocation flow lives at
    # dashboard.doppler.com and is distinct from any other vendor's.
    # Doppler is the canonical secrets-management sibling of HCP
    # Vault Secrets (Round 6) and rounds out the secrets-management
    # sub-landscape Round 6 named but did not enumerate.
    (
        re.compile(r"(?<![A-Za-z0-9])dp\.(?:pt|st|sa|ct|scim|audit)\.[A-Za-z0-9]{43}(?![A-Za-z0-9])"),
        "Doppler Token gefunden",
    ),
    # Buildkite Agent Token (``bkat_<40+ alphanumeric body>``). Issued
    # via buildkite.com/organizations/<org>/agents for Buildkite agent
    # registration. The ``bkat_`` prefix is unambiguous (no other
    # major issuer uses it), and the strict alphanumeric body lies
    # entirely inside the entropy fallback's alphabet — so the
    # entropy regex matches the full ``bkat_<body>`` span as one
    # generic finding, losing the Buildkite-specific attribution. A
    # leak lets a network adversary register a rogue agent that
    # drains the Buildkite job queue: every CI job (with whatever
    # build-secret env vars the pipeline exposes) is delivered to
    # attacker-controlled hardware. Blast radius = the entire CI
    # estate's job-execution surface — the highest leak surface in
    # the modern CI stack. Body lower bound 40 chars matches
    # Buildkite's documented agent-token format and rejects short
    # ``bkat_``-prefixed fragments while accepting every legitimate
    # token. The revocation flow lives at
    # buildkite.com/organizations/<org>/agents and is distinct from
    # any other vendor's.
    (
        re.compile(r"(?<![A-Za-z0-9])bkat_[A-Za-z0-9]{40,}(?![A-Za-z0-9])"),
        "Buildkite Agent Token gefunden",
    ),
    # Netlify Personal Access Token (``nfp_<40+ alphanumeric body>``).
    # Issued via app.netlify.com/user/applications for full Netlify
    # REST-API access (the modern post-2023 ``nfp_``-prefixed format;
    # the legacy 40-char-hex pre-prefix tokens fall into the
    # bucket-(b) no-prefix landscape). Total length 44+ chars (4-char
    # prefix + 40+ char body). The ``nfp_`` prefix is unambiguous,
    # and the body lies entirely inside the entropy alphabet —
    # same generic-only attribution gap as Buildkite. A leak grants
    # the issuing user's full Netlify API scope: read/write every
    # site's deploys, redirect rules, environment variables, build-
    # hook URLs, edge-function code, and DNS records. The site-deploy
    # primitive in particular means an attacker can replace the live
    # site with arbitrary HTML / JS, bypassing every downstream
    # content gate. The revocation flow lives at
    # app.netlify.com/user/applications and is distinct from any
    # other vendor's. Netlify rounds out the CI/CD sub-landscape's
    # hosting-platform tier alongside Buildkite (CI execution).
    (
        re.compile(r"(?<![A-Za-z0-9])nfp_[A-Za-z0-9]{40,}(?![A-Za-z0-9])"),
        "Netlify Personal Access Token gefunden",
    ),
    # Render Personal Access Token (``rnd_<40+ alphanumeric body>``).
    # Issued via dashboard.render.com/u/settings#api-keys for full
    # Render REST-API access (the modern Render-platform token
    # format). Total length 44+ chars (4-char prefix + 40+ char
    # body). The ``rnd_`` prefix is unambiguous (no other major
    # issuer uses it), and the body lies entirely inside the
    # entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet — so the
    # entropy regex matches the full ``rnd_<body>`` span as one
    # generic finding, losing the Render-specific attribution that
    # incident-response keys off. A leak grants the issuing user's
    # full Render API scope: read/write every owned service's
    # deploys, environment variables, persistent disks, custom
    # domains, build hooks and webhook configuration; a malicious
    # deploy can replace the live application (web service, static
    # site, cron job, background worker) with arbitrary code,
    # bypassing every downstream gate. The revocation flow lives at
    # dashboard.render.com/u/settings#api-keys and is distinct from
    # any other vendor's. Render closes the named-but-deferred
    # Round-6/Round-7 hosting-platform sibling alongside Netlify
    # (Round 7) on the CI/CD hosting tier.
    (
        re.compile(r"(?<![A-Za-z0-9])rnd_[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"),
        "Render API Key gefunden",
    ),
    # Buildkite User Access Token (``bkua_<40+ alphanumeric body>``).
    # Issued via buildkite.com/user/api-access-tokens for user-scoped
    # REST-API access (issue queries, build retries, pipeline
    # manipulation, agent management). Distinct from the Round-7
    # Buildkite Agent Token (``bkat_``): agent tokens register CI
    # workers, user tokens act on behalf of a human user. The two
    # patterns are mutually exclusive at the prefix level (``bkat_``
    # vs ``bkua_`` differ at the fourth character). The ``bkua_``
    # prefix is unambiguous, and the body lies entirely inside the
    # entropy alphabet — same generic-only attribution gap as
    # ``bkat_``. A leak grants the issuing user's full Buildkite
    # API scope across every accessible organisation: read pipeline
    # definitions (which often embed secrets in env references),
    # retry historical builds with attacker-controlled env
    # overrides, manage agents, and exfiltrate access logs. The
    # revocation flow lives at buildkite.com/user/api-access-tokens
    # (distinct from agent-token revocation), so issuer-specific
    # attribution accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])bkua_[A-Za-z0-9]{40,}(?![A-Za-z0-9])"),
        "Buildkite User Access Token gefunden",
    ),
    # New Relic User API Key (``NRAK-<27 uppercase alphanumeric body>``).
    # Issued via one.newrelic.com > API Keys > Create key (User key
    # type) for full New Relic platform API access (NerdGraph
    # queries, account configuration, alert policy / notification
    # channel management, dashboard create/update/delete, user
    # management). Total length 32 chars (5-char ``NRAK-`` prefix +
    # 27-char alphanumeric body). The ``NRAK-`` prefix is unambiguous
    # (no other major issuer uses it), and the strict alphanumeric
    # body lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex matches
    # the full ``NRAK-<body>`` span as one generic finding, losing
    # the New-Relic-specific issuer attribution that incident-
    # response keys off. A leak grants the issuing user's full New
    # Relic API scope across every accessible account: query every
    # ingested metric / log / trace, modify alert routing
    # (suppressing real incidents), exfiltrate dashboard contents
    # (which often embed business metric names that reveal product
    # telemetry), and create new API keys to maintain persistence.
    # The revocation flow lives at one.newrelic.com/api-keys and is
    # distinct from any other vendor's. New Relic closes the
    # named-but-deferred Round-8 observability sub-landscape.
    (
        re.compile(r"(?<![A-Za-z0-9])NRAK-[A-Z0-9]{27}(?![A-Za-z0-9])"),
        "New Relic User API Key gefunden",
    ),
    # New Relic REST API Key (``NRRA-<40 lowercase hex body>``). The
    # legacy REST API v2 credential format (deprecated in favour of
    # NRAK since 2021 but still issued and accepted for backward
    # compatibility). Total length 45 chars (5-char ``NRRA-`` prefix
    # + 40-char lowercase hex body). The ``NRRA-`` prefix is
    # unambiguous, and the strict hex body lies entirely inside the
    # entropy fallback's alphabet. A leak grants the issuing
    # account's REST API v2 scope: read application performance
    # data, browser monitoring data, mobile monitoring data, and
    # synthetic monitoring data. The legacy key format has fewer
    # scoping controls than NRAK, so leak surfaces are typically
    # wider. Distinct revocation flow at one.newrelic.com/api-keys
    # under the "REST API Keys" tab.
    (
        re.compile(r"(?<![A-Za-z0-9])NRRA-[a-fA-F0-9]{40}(?![A-Za-z0-9])"),
        "New Relic REST API Key gefunden",
    ),
    # New Relic Insights Insert Key (``NRII-<32 lowercase hex body>``).
    # Issued via one.newrelic.com > API Keys > Create key (Insights
    # Insert key type) for ingestion-only access to the New Relic
    # Events / Insights API. Total length 37 chars (5-char ``NRII-``
    # prefix + 32-char lowercase hex body). The ``NRII-`` prefix is
    # unambiguous, and the strict hex body lies entirely inside the
    # entropy fallback's alphabet. A leak grants the issuing
    # account's event-ingestion scope: an attacker can spam the
    # account's event stream with fabricated metrics, polluting
    # dashboards, triggering false-positive alerts, and consuming
    # the account's data ingestion quota. Distinct revocation flow
    # at one.newrelic.com/api-keys under the "Insights Insert Keys"
    # tab.
    (
        re.compile(r"(?<![A-Za-z0-9])NRII-[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "New Relic Insights Insert Key gefunden",
    ),
    # Fly.io API Token (``FlyV1 fm[12]_<base64 body>`` or
    # ``FlyV1 fo1_<base64 body>``). Issued via the ``fly auth token``
    # CLI or fly.io/dashboard/<org>/tokens for full Fly.io platform
    # API access (deploy apps, read secrets, manipulate Wireguard
    # peers, manage organisations). The canonical leak surface is the
    # Authorization-header form ``FlyV1 <token>``: the ``FlyV1 ``
    # scheme prefix (with literal space) anchors against fly.io
    # specifically. Modern macaroon tokens use ``fm2_`` (current
    # default) or ``fm1_`` (legacy macaroon), and the oldest opaque
    # tokens use ``fo1_``. Total length 200+ chars in practice
    # (the macaroon body encodes embedded JSON capability
    # descriptions plus organisation / app scope). The literal
    # space in ``FlyV1 `` and the body alphabet
    # ``[A-Za-z0-9_=\-]`` (base64url + ``=`` padding) place the
    # prefix outside the entropy fallback's contiguous-match span —
    # pre-fix the entropy regex matches only the body span after
    # the underscore, losing both the ``FlyV1 fm2_`` prefix AND the
    # Fly.io-specific issuer attribution. The 50+ body lower bound
    # rejects short ``FlyV1 fm2_``-prefixed fragments while
    # accepting every legitimate token (real Fly.io macaroons are
    # always >150 chars). A leak grants the issuing principal's
    # full Fly.io organisation scope: deploy arbitrary container
    # images (which can exfiltrate every secret in the org's apps),
    # modify networking (Wireguard peers, IP allocations, Anycast
    # routes), and rotate billing credentials. The revocation flow
    # lives at fly.io/dashboard/<org>/tokens and is distinct from
    # any other vendor's. Fly.io is the canonical PaaS / edge-
    # runtime sibling not previously covered.
    (
        re.compile(r"(?<![A-Za-z0-9])FlyV1 (?:fm[12]|fo1)_[A-Za-z0-9_=\-]{50,}(?![A-Za-z0-9])"),
        "Fly.io API Token gefunden",
    ),
]
