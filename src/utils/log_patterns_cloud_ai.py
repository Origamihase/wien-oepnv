"""Value-shape masks for Vault, GitHub, cloud/payment/AI vendors and Slack/AI-ML tokens.

Pure data moved out of ``src/utils/logging.py``, which imports it and runs
these patterns right after ``_GENERIC_PATTERNS`` from
``src/utils/log_patterns_generic.py``.
"""

from __future__ import annotations

# The list keeps a private name; ``src/utils/logging.py`` imports it
# (code scanning counts an unlisted ``_name`` as unused).
__all__ = ["_CLOUD_AI_TOKEN_PATTERNS"]

_CLOUD_AI_TOKEN_PATTERNS: list[tuple[str, str]] = [
    # HashiCorp Vault token family value-shape masking.
    # Sibling-drift closure for the 2026-05-17 secret-scanner round
    # that extended ``_KNOWN_TOKENS`` in
    # ``src/utils/secret_scanner.py`` to detect HashiCorp Vault
    # Service / Batch / Recovery tokens (``hvs.`` / ``hvb.`` /
    # ``hvr.`` prefixes with 30+ char base64url bodies). The
    # scanner closed the *detection* codepath for committed source
    # files but the log-sanitisation codepath was NOT extended in
    # the same round — bare Vault token shapes in plain log text
    # (application f-string logs, upstream error responses echoing
    # the token back, JSON values without sensitive key names, URL
    # paths embedding the token) bypass every existing
    # key/header/URL-credential mask pattern and leak verbatim to
    # operator log streams and the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model (mirror the secret-scanner Round 6 / 2026-05-17
    # rounds' blast-radius analysis):
    #   * ``hvs.`` — Vault Service Token (persistent, full policy
    #     scope; HCP Vault Secrets managed + self-hosted Vault 1.10+).
    #   * ``hvb.`` — Vault Batch Token (ephemeral, full policy
    #     scope for TTL; common in CI/CD and serverless workloads).
    #   * ``hvr.`` — Vault Recovery Token (root-equivalent on
    #     sealed/HSM-backed Vault; mint new root token via
    #     ``POST /v1/sys/generate-root`` once unsealed → persistent
    #     backdoor with full administrative scope).
    #
    # Structural anchors mirror the scanner regex:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``obj.hvs.foo`` is preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * 30+ char body floor rejects accidental fragments
    #     (attribute-access chains, filesystem paths, mid-
    #     identifier collisions) while accepting the canonical
    #     90-110 char Vault token shape.
    # The mask preserves the issuer-specific prefix (``hvs.***`` /
    # ``hvb.***`` / ``hvr.***``) for incident-response triage:
    # each tier has a distinct revocation flow (``vault token
    # revoke`` for service/batch via ``POST /v1/auth/token/revoke``
    # vs. ``POST /v1/sys/generate-recovery-token/attempt`` + Shamir
    # recovery-key re-keying for recovery).
    #
    # Marker: SENTINEL_VAULT_TOKEN_LOG_SANITIZATION_DRIFT.
    (
        r"(?<![A-Za-z0-9])(hvs|hvb|hvr)\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])",
        r"\1.***",
    ),
    # GitHub token family value-shape masking. Sibling-drift closure
    # for the secret-scanner ``_KNOWN_TOKENS`` entries that detect
    # committed GitHub tokens (``ghp_`` Personal Access Token,
    # ``gho_`` OAuth Access Token, ``ghu_`` App User-to-Server,
    # ``ghs_`` App Server-to-Server / ``GITHUB_TOKEN``, ``ghr_``
    # Refresh Token, ``github_pat_`` Fine-Grained PAT). The scanner
    # closed the *detection* codepath for committed source files; the
    # log-sanitisation codepath was NOT extended in the same rounds —
    # bare GitHub token shapes in plain log text (application f-string
    # logs, upstream error responses echoing the token back, JSON
    # values without sensitive key names, URL query strings / path
    # segments with NON-sensitive parameter names like ``ref`` /
    # ``commit_sha``) bypass every existing key/header/URL-credential
    # mask pattern and leak verbatim to operator log streams and the
    # public ``docs/feed_health.json`` artefact.
    #
    # Threat model (per-tier blast radius):
    #   * ``ghp_<36 alphanumeric>`` — Personal Access Token (Classic).
    #     Full scope per token configuration. Leaking grants ability
    #     to read every repo the user can read, push to every repo
    #     the user can write, exfiltrate secrets via repo files /
    #     Actions logs, create/delete repos, and (with admin:org)
    #     administer the user's organisations.
    #   * ``gho_<36 alphanumeric>`` — OAuth-App Access Token (issued
    #     via OAuth web flow). Per-OAuth-app scope.
    #   * ``ghu_<36 alphanumeric>`` — GitHub App User-to-Server Token.
    #     Per-installation scope intersected with user's repo access.
    #   * ``ghs_<36 alphanumeric>`` — **HIGHEST routine-leak severity.**
    #     Format of ``GITHUB_TOKEN`` auto-injected by GitHub Actions
    #     into every workflow run. Leaking grants full ``contents:
    #     write`` / ``packages: write`` / ``actions: write`` scope
    #     for the workflow's TTL (typically 1-6 hours, actively
    #     renewable).
    #   * ``ghr_<36 alphanumeric>`` — Refresh Token (issued alongside
    #     ``gho_``/``ghu_`` during token rotation). Mints fresh access
    #     tokens until refresh token is revoked.
    #   * ``github_pat_<22+ alphanumeric_>`` — Fine-Grained PAT.
    #     Per-repo or per-org scoped with resource-level permissions
    #     (Contents, Metadata, Actions, Pull Requests, Issues,
    #     Workflows, Webhooks). Modern replacement for ``ghp_``
    #     classic tokens. Body permits internal underscores per
    #     GitHub's canonical ``github_pat_<22>_<59>`` format.
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``myghp_xxx``, ``xghs_yyy`` are preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict 36-char alphanumeric body for ``ghp_``/``gho_``/
    #     ``ghu_``/``ghs_``/``ghr_`` matches GitHub's canonical
    #     token shape and rejects accidental fragments.
    #   * 22+ char body with underscores allowed for ``github_pat_``
    #     matches the fine-grained format.
    # The mask preserves the issuer-specific prefix (``ghp_***`` /
    # ``ghs_***`` etc.) for incident-response triage — each tier has
    # a distinct revocation flow (Settings → Developer settings →
    # Personal access tokens for ghp_/github_pat_; Settings →
    # Applications → Authorized OAuth Apps for gho_/ghr_; per-App
    # installation rotation for ghu_/ghs_).
    #
    # Idempotent: masked forms (``ghp_***`` / ``github_pat_***``)
    # do NOT match the regex because ``*`` is not in the body
    # alphabet AND the masked body length (3 chars) is below the
    # 36/22-char floors.
    #
    # Marker: SENTINEL_GITHUB_TOKEN_LOG_SANITIZATION_DRIFT.
    (
        r"(?<![A-Za-z0-9])(ghp|gho|ghu|ghs|ghr)_[0-9a-zA-Z]{36}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(github_pat)_[0-9a-zA-Z_]{22,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    # High-Severity Cloud / Payment / LLM / Git-Host Token Family
    # value-shape masking. Sibling-drift closure for the secret-
    # scanner ``_KNOWN_TOKENS`` entries enumerated in
    # ``src/utils/secret_scanner.py`` (AWS / Google API / Stripe /
    # Anthropic / OpenAI / GitLab PAT / NPM / SendGrid / Hugging
    # Face). The scanner closed the *detection* codepath for
    # committed source files in rounds 1-N; the log-sanitisation
    # codepath was NOT extended for these families — bare token
    # shapes in plain log text (application f-string logs, upstream
    # error responses echoing the token back, JSON values with
    # NON-sensitive key names, URL paths embedding the token, and
    # exception text routed through ``_sanitize_exception_msg``)
    # bypassed every existing key/header/URL-credential mask and
    # leaked verbatim into operator log streams plus the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model per family:
    #   * AWS ``AKIA``/``ASIA``/``ACCA``/``ABIA<16 uppercase>`` —
    #     Cloud account access (full data plane + control plane
    #     for the issuing principal): read every S3 bucket /
    #     RDS DB / DynamoDB table the principal can see, mint
    #     STS credentials, modify IAM (with ``iam:*``), exfiltrate
    #     KMS-encrypted data via ``kms:Decrypt``. Per-prefix
    #     attribution (Personnel vs. STS vs. Federated vs. Bearer)
    #     accelerates IR triage to the right rotation flow.
    #   * Google API Key ``AIza<35 base64url-ish>`` — Per-key
    #     scope for the issuing project: Maps / Places / Geocoding
    #     / Translate / YouTube quota burn (USD 100s/day at scale),
    #     plus the project's quota-tier billing fraud.
    #   * Stripe ``sk_live_<24>`` / ``sk_test_<24>`` /
    #     ``rk_live_<24>`` / ``rk_test_<24>`` / ``whsec_<32+>`` —
    #     Payment-processing fraud (full account API access for
    #     the live secret; webhook forgery for whsec_). Live
    #     secret is the highest-severity; restricted variants
    #     carry scoped subsets.
    #   * Anthropic ``sk-ant-(api|admin)NN-<32+>`` — LLM billing
    #     fraud + prompt exfiltration. Admin-tier additionally
    #     grants console access to the org's billing /
    #     organisation members.
    #   * OpenAI ``sk-<48 alphanumeric>`` (legacy) /
    #     ``sk-proj-<40+>`` / ``sk-svcacct-<40+>`` — Same blast
    #     radius across tiers: completion API at the issuer's
    #     expense, custom-model exfiltration, fine-tune-job
    #     hijack.
    #   * GitLab PAT ``glpat-<20>`` — Mirror of the GitHub PAT
    #     family scope on the GitLab side: full repo / project
    #     access per the token's scope configuration.
    #   * NPM ``npm_<36 alphanumeric>`` — Supply-chain risk:
    #     publish malicious packages under the issuer's
    #     organisation, modify package-tarball contents, deprecate
    #     legitimate versions.
    #   * SendGrid ``SG.<22>.<43>`` — Transactional email sent
    #     FROM the project's authenticated sending domain
    #     (phishing amplification leveraging SPF / DKIM / DMARC
    #     authentication), contact-list exfiltration, webhook-
    #     redirect to attacker-controlled URLs.
    #   * Hugging Face ``hf_<32+>`` — Model hub access: read
    #     private models / datasets / Spaces, push backdoored
    #     model weights, exfiltrate fine-tuning data.
    #
    # Structural anchors mirror the scanner regexes:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``myAKIA<16>`` is preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict body lengths / alphabets per vendor canonical
    #     format reject accidental fragments while accepting
    #     every real-shape token.
    # The mask preserves issuer-specific prefixes (``AKIA***``,
    # ``sk_live_***``, ``sk-ant-api03-***`` etc.) for incident-
    # response triage — each vendor has a distinct revocation
    # flow.
    #
    # Ordering note: more-specific OpenAI prefixes
    # (``sk-ant-``/``sk-proj-``/``sk-svcacct-``) are listed BEFORE
    # the generic OpenAI legacy ``sk-<48 alphanumeric>`` so they
    # win first. The patterns are also mutually exclusive at the
    # body-alphabet level (legacy ``sk-`` requires 48 chars from
    # ``[A-Za-z0-9]`` with NO ``-``, while the prefixed siblings
    # have ``-`` after the issuer keyword), so ordering is a
    # documentation aid rather than a correctness requirement.
    #
    # Idempotence: masked forms (``AKIA***``, ``sk_live_***``,
    # ``glpat-***`` etc.) do NOT match any of these regexes
    # because ``*`` is not in any body alphabet AND the masked
    # body length (3 chars) is below every per-family floor.
    #
    # Marker: SENTINEL_MULTI_VENDOR_TOKEN_LOG_SANITIZATION_DRIFT.
    (
        r"(?<![A-Za-z0-9])(AKIA|ASIA|ACCA|ABIA)[A-Z0-9]{16}(?![A-Za-z0-9])",
        r"\1***",
    ),
    (
        r"(?<![A-Za-z0-9])(AIza)[0-9A-Za-z\-_]{35}(?![A-Za-z0-9])",
        r"\1***",
    ),
    (
        r"(?<![A-Za-z0-9])(sk_live|sk_test|rk_live|rk_test)_[0-9a-zA-Z]{24}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(whsec)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(sk-ant-(?:api|admin)[0-9]{2})-[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(sk-(?:proj|svcacct))-[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    # OpenRouter API Key (``sk-or-v1-<32+ alphanumeric>``). Listed
    # BEFORE the OpenAI legacy ``sk-<48 alnum>`` pattern so the
    # more-specific OpenRouter prefix wins first. The two are
    # structurally distinct (OpenRouter contains a dash inside
    # the prefix span which breaks the OpenAI legacy regex's
    # strict alphanumeric body, so collision is impossible at the
    # regex level) but explicit ordering documents the intent.
    # Part of SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT.
    (
        r"(?<![A-Za-z0-9])(sk-or-v1)-[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(sk)-[A-Za-z0-9]{48}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(glpat)-[0-9a-zA-Z_\-]{20}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(npm)_[0-9a-zA-Z]{36}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(SG)\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])",
        r"\1.***",
    ),
    (
        r"(?<![A-Za-z0-9])(hf)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    # Slack token family value-shape masking. Sibling-drift closure
    # for the secret-scanner ``_KNOWN_TOKENS`` entries that detect
    # committed Slack tokens across SEVEN issuer prefixes:
    # ``xoxb-`` (Bot Token), ``xoxp-`` (User Token), ``xoxa-``
    # (OAuth Access), ``xoxr-`` (Refresh), ``xoxc-`` (Browser
    # Session), ``xoxd-`` (Cookie Session), ``xoxe-`` /
    # ``xoxe.xoxb-`` / ``xoxe.xoxp-`` (V2 Token Rotation
    # Refresh — direct + chained forms). The scanner closed the
    # *detection* codepath for committed source files in
    # successive rounds; the log-sanitisation codepath was NOT
    # extended in any prior round — bare Slack token shapes in
    # plain log text (Slack provider error responses, application
    # f-string logs, JSON values with NON-sensitive keys, URL
    # paths / query strings) bypassed every existing key/header/
    # URL-credential mask pattern and leaked verbatim into
    # operator log streams plus the public
    # ``docs/feed_health.json`` artefact.
    #
    # Threat model per Slack issuer tier:
    #   * ``xoxb-<digits>-<digits>-<24 alnum>`` — Bot Token. The
    #     workhorse credential for Slack automation: posts to
    #     channels, DMs, reads messages, uploads files, manages
    #     workspace members per the app's installed scopes.
    #     Highest routine-leak severity in the Slack family due
    #     to ubiquity in CI/CD secrets / ``.env`` files.
    #   * ``xoxp-<digits>-<digits>-<digits>-<32 alnum>`` — User
    #     Token. Acts AS the user — full impersonation including
    #     DMs, search, file access, channel history.
    #   * ``xoxa-<body>`` — OAuth Access Token. Configuration
    #     tokens issued via the OAuth flow.
    #   * ``xoxr-<body>`` — Refresh Token. Mints fresh ``xoxb-`` /
    #     ``xoxp-`` access tokens until the refresh token itself
    #     is revoked at slack.com/app-settings.
    #   * ``xoxc-<body>`` — Browser Session Token. Session cookie
    #     extracted via DevTools; canonical "session hijack"
    #     credential. Unattended scripted access to the user's
    #     Slack workspace.
    #   * ``xoxd-<body>`` — Cookie Session Token. Companion to
    #     ``xoxc-``; same blast radius plus 2FA-bypass if the
    #     session was established post-2FA.
    #   * ``xoxe-`` / ``xoxe.xoxb-`` / ``xoxe.xoxp-`` — V2 Token
    #     Rotation Refresh (direct + chained). The chained shape
    #     embeds the rotation chain's identity (``xoxb-`` for
    #     bot rotation, ``xoxp-`` for user rotation).
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
    #     positives (``myxoxb-``, ``0xoxe-`` are preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict per-segment digit lengths for the canonical
    #     ``xoxb-``/``xoxp-`` shapes (10+ digit segments) reject
    #     accidental fragments while accepting every real-shape
    #     token.
    #   * 20+ char body floor for the dash-suffixed variants.
    # The mask preserves the issuer-specific prefix
    # (``xoxb-***``, ``xoxe.xoxb-***`` etc.) for incident-response
    # triage — each tier has a distinct revocation flow
    # (slack.com/app-settings > Workspace tokens for
    # xoxb/xoxp/xoxa/xoxr; password reset + active session
    # termination for xoxc/xoxd; xoxe rotates via the parent
    # token chain).
    #
    # Ordering: ``xoxe.xox[bp]-`` chained patterns are listed
    # BEFORE the bare ``xoxb-``/``xoxp-`` patterns so the more-
    # specific chained shape wins first — preserving the full
    # ``xoxe.xoxb-***`` attribution rather than splitting at the
    # inner ``xoxb-`` boundary. Re-encoding the rotation-chain
    # identity is critical for IR triage because the chained
    # form's revocation flow (rotate the parent ``xoxb-``/
    # ``xoxp-``) differs from the direct ``xoxe-`` form
    # (revoke at refresh-token settings).
    #
    # Idempotence: masked forms (``xoxb-***``, ``xoxe.xoxb-***``)
    # do NOT match any of these regexes because ``*`` is not in
    # any body alphabet AND the masked body length (3 chars) is
    # below every per-family floor (20/24/32).
    #
    # Marker: SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT.
    (
        r"(?<![A-Za-z0-9])(xoxe\.xox[bp])-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(xoxe)-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(xoxb)-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(xoxp)-[0-9]{10,}-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{32}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(xox[acdr])-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    # AI/ML Inference Platform tier value-shape masking. Sibling-
    # drift closure for the secret-scanner ``_KNOWN_TOKENS``
    # entries that detect committed AI/ML inference platform
    # tokens (Groq ``gsk_`` / Replicate ``r8_`` / Perplexity
    # ``pplx-`` / xAI ``xai-``; OpenRouter ``sk-or-v1-`` is
    # listed earlier alongside the OpenAI legacy regex to
    # guarantee precedence). The scanner closed the *detection*
    # codepath for committed source files across the 2026-05-16
    # AI/ML Inference Platform tier rounds; the log-sanitisation
    # codepath was NOT extended in any prior round — bare tokens
    # in plain log text leaked verbatim.
    #
    # Threat model per vendor:
    #   * Groq ``gsk_<32+ alnum>`` — LPU-accelerated inference
    #     (LLaMA / Mixtral / Gemma). Billing fraud + free-tier
    #     abuse for unauthorised inference.
    #   * Replicate ``r8_<40 alnum>`` — Model hosting (Stable
    #     Diffusion, custom Cog models). Compute-billing fraud
    #     (GPU inference at issuer's expense) + backdoored Cog
    #     model push if token has push scope.
    #   * Perplexity ``pplx-<32+ alnum>`` — Search-grounded
    #     inference. Billing fraud + search-result exfiltration.
    #   * xAI ``xai-<32+ alnum>`` — Grok inference API. Billing
    #     fraud (Grok-2 / Grok-2 Vision at issuer's expense).
    #   * OpenRouter ``sk-or-v1-<32+ alnum>`` — UNIQUE CROSS-
    #     PLATFORM PIVOT AMPLIFIER (handled by the earlier
    #     OpenRouter regex). A leaked OpenRouter token grants
    #     access to ALL the user's attached provider keys
    #     (Anthropic / OpenAI / Mistral / etc.) through the
    #     aggregator proxy — effectively a multi-vendor
    #     credential-chain pivot.
    #
    # Structural anchors mirror the scanner regexes exactly:
    #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word
    #     false positives (``agsk_``, ``frnd_`` are preserved).
    #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
    #   * Strict per-vendor body lengths (32+ for Groq /
    #     Perplexity / xAI; EXACTLY 40 for Replicate per the
    #     scanner regex's exact-length anchor).
    #
    # Idempotence: masked forms (``gsk_***``, ``pplx-***``) do
    # NOT match any of these regexes (mask alphabet excludes
    # ``*``; mask body length 3 is below every floor).
    (
        r"(?<![A-Za-z0-9])(gsk)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(r8)_[A-Za-z0-9]{40}(?![A-Za-z0-9])",
        r"\1_***",
    ),
    (
        r"(?<![A-Za-z0-9])(pplx)-[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
    (
        r"(?<![A-Za-z0-9])(xai)-[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
        r"\1-***",
    ),
]
