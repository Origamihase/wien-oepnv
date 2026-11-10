"""Part of ``_KNOWN_TOKENS`` of the secret scanner:
GitHub, Google, Telegram, Stripe, Slack, package registries, AI platforms,
connection strings, DigitalOcean, Mapbox, JWT, Twilio, Notion.

Moved verbatim from ``src/utils/secret_scanner.py``; the facade joins the parts
in their original order (the first matching entry wins).
"""

from __future__ import annotations

import re

KNOWN_TOKENS_VCS_PAYMENT_AI = [
    (re.compile(r"(?<![A-Za-z0-9])glpat-[0-9a-zA-Z_\-]{20}(?![A-Za-z0-9])"), "GitLab Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])ghp_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])github_pat_[0-9a-zA-Z_]{22,}(?![A-Za-z0-9])"), "GitHub Fine-Grained Token gefunden"),
    # GitHub OAuth-App access token (issued via the OAuth web flow).
    (re.compile(r"(?<![A-Za-z0-9])gho_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub OAuth Access Token gefunden"),
    # GitHub App user-to-server token (App acting on behalf of an authenticated user).
    (re.compile(r"(?<![A-Za-z0-9])ghu_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub App User-to-Server Token gefunden"),
    # GitHub App server-to-server token. This is the format of `GITHUB_TOKEN`
    # auto-injected by GitHub Actions, so leakage is high-impact.
    (re.compile(r"(?<![A-Za-z0-9])ghs_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub App Server-to-Server Token gefunden"),
    # GitHub refresh token (issued alongside gho_/ghu_ during token rotation).
    (re.compile(r"(?<![A-Za-z0-9])ghr_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub Refresh Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z\-_]{35}(?![A-Za-z0-9])"), "Google API Key gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])[0-9]{3,14}:[a-zA-Z0-9_-]{35}(?![A-Za-z0-9])"), "Telegram Bot Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])sk_live_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Live Secret Key gefunden"),
    # Stripe test secret key. Less catastrophic than the live counterpart but still
    # grants access to the project's test-mode dashboard, customer/PaymentIntent
    # objects and webhooks — and a leaked test key strongly signals that a live
    # key exists somewhere in the same repo. Treated as a distinct finding so the
    # report calls out *which* environment leaked.
    (re.compile(r"(?<![A-Za-z0-9])sk_test_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Test Secret Key gefunden"),
    # Stripe restricted API keys (``rk_live_`` / ``rk_test_``). Restricted keys
    # carry a scoped subset of permissions, but a leak still grants the API
    # access defined by that scope (charges, customers, payouts, …) and is
    # high-impact for the affected resource. Format mirrors ``sk_*``: prefix
    # plus a 24-char alphanumeric body. Distinct reasons per environment so
    # the report identifies which key tier leaked.
    (re.compile(r"(?<![A-Za-z0-9])rk_live_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Restricted Live Key gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])rk_test_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Restricted Test Key gefunden"),
    # Stripe webhook signing secret (``whsec_``). Leakage is not an API
    # credential but lets an attacker forge webhook payloads that the
    # application's signature verification will accept — so any
    # webhook-driven business logic (refunds, account upgrades, fulfilment)
    # can be triggered by a network adversary. Body is base64-ish, ``32+``
    # chars in practice; pattern stays alphanumeric to match Stripe's
    # current format and avoid colliding with the ``[A-Za-z0-9+/=_-]``
    # entropy fallback's character class.
    (re.compile(r"(?<![A-Za-z0-9])whsec_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Stripe Webhook Signing Secret gefunden"),
    # Slack Token Rotation Refresh Token — the modern V2 rotation
    # credential issued via ``oauth.v2.access`` with
    # ``grant_type=refresh_token``. Two on-the-wire shapes per
    # https://api.slack.com/authentication/rotation:
    #   * **Direct shape** — ``xoxe-<numeric>-<base64-like body>`` for
    #     tokens issued without a parent rotation chain.
    #   * **Chained shape** — ``xoxe.xoxb-<body>`` (bot rotation) or
    #     ``xoxe.xoxp-<body>`` (user rotation), where the embedded
    #     ``xox[bp]-`` prefix anchors the rotation chain for Slack's
    #     auth server lookup.
    # A leak grants the holder the ability to mint fresh
    # ``xoxb-``/``xoxp-`` access tokens with the chain's identity and
    # scopes — multi-month blast radius (refresh tokens are long-
    # lived; the rotated access tokens have 12-hour TTL but can be
    # re-minted indefinitely until the refresh token itself is
    # revoked at https://api.slack.com/apps/<app>/oauth via the
    # "Reinstall App" / "Rotate Tokens" flow). The revocation flow
    # is distinct from the legacy ``xoxr-`` refresh-token flow (the
    # ``xoxr-`` prefix predates V2 rotation; the two are NOT
    # interchangeable). Placed BEFORE the existing ``xoxb-`` /
    # ``xoxp-`` / ``xoxa-`` / ``xoxr-`` entries so ``is_covered``
    # correctly anchors the chained shape at the larger ``xoxe.``-
    # prefixed span — the inner ``xoxb-``/``xoxp-`` span (which the
    # strict bare ``xoxb-``/``xoxp-`` regexes would NOT match anyway
    # because the rotation body shape ``1-<base64body>`` does not
    # fit ``[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24,32}``) is
    # suppressed. Pre-fix the chained shape was silently undetected
    # entirely (entropy fallback misses ``xoxe.`` due to the dot
    # falling outside ``[A-Za-z0-9+/=_-]``, only the post-prefix
    # body span matches generically — losing the Slack-rotation-
    # specific issuer attribution).
    (
        re.compile(r"(?<![A-Za-z0-9])xoxe(?:-|\.xox[bp]-)[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])"),
        "Slack Token Rotation Refresh Token gefunden",
    ),
    # Slack Browser Session Token (``xoxc-<body>``). Extracted from
    # ``slack.com`` browser session cookies, granting full user-level
    # session auth with **no scope restrictions** — equivalent to the
    # user being logged in via the web client. Unofficial tools
    # (slack_cleaner, slackdump, slack-export scripts) extract
    # ``xoxc-`` via DevTools and use it for unattended scripted
    # access to the user's Slack workspace. The canonical "session
    # hijack" credential for Slack: an attacker holding ``xoxc-``
    # can browse every conversation the user can see (DMs, private
    # channels, sensitive files), post messages as the user, and
    # exfiltrate the workspace's full message history via the
    # cookie-authenticated ``/api/conversations.list`` /
    # ``conversations.history`` endpoints. The revocation flow lives
    # at https://slack.com/account/sessions ("Sign out all other
    # sessions") and is distinct from the OAuth-app revocation flow
    # at https://api.slack.com/apps/ — session-cookie auth, not
    # app-token auth.
    (
        re.compile(r"(?<![A-Za-z0-9])xoxc-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])"),
        "Slack Browser Session Token gefunden",
    ),
    # Slack Cookie Session Token (``xoxd-<body>``). Companion to
    # ``xoxc-`` — the ``d`` cookie value from Slack web sessions,
    # used in conjunction with ``xoxc-`` for direct browser-style
    # API calls (e.g. unofficial endpoints that require
    # cookie-based auth). A leak grants the same SESSION-LEVEL
    # access as ``xoxc-`` (the two tokens typically leak together
    # via DevTools cookie extraction). Pre-fix the entropy fallback
    # caught the body span (the ``xoxd-`` prefix's dash is inside
    # the entropy alphabet and the body alphabet matches), but the
    # Slack-cookie-session-specific issuer attribution that anchors
    # the canonical revocation flow (https://slack.com/account/
    # sessions — distinct from every other Slack revocation flow)
    # was lost. Furthermore, for uniform-character-class bodies the
    # entropy fallback's ``min_categories=2`` requirement returned
    # ``False`` and the credential was silently undetected entirely.
    (
        re.compile(r"(?<![A-Za-z0-9])xoxd-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])"),
        "Slack Cookie Session Token gefunden",
    ),
    (re.compile(r"(?<![A-Za-z0-9])xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}(?![A-Za-z0-9])"), "Slack Bot Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])xoxp-[0-9]{10,}-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{32}(?![A-Za-z0-9])"), "Slack User Token gefunden"),
    # Slack OAuth-app access token (configuration token issued via the OAuth flow,
    # ``xoxa-`` prefix). Format mirrors the bot/user variants but is sometimes
    # shorter, so the body length is permissive while the unique prefix keeps
    # false positives essentially impossible.
    (re.compile(r"(?<![A-Za-z0-9])xoxa-[0-9a-zA-Z-]{20,}(?![A-Za-z0-9])"), "Slack OAuth Access Token gefunden"),
    # Slack refresh token (``xoxr-`` prefix), issued alongside rotating bot/user
    # tokens. Leakage grants the ability to mint fresh xoxb-/xoxp- tokens until
    # the refresh token itself is revoked.
    (re.compile(r"(?<![A-Za-z0-9])xoxr-[0-9a-zA-Z-]{20,}(?![A-Za-z0-9])"), "Slack Refresh Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])npm_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "NPM Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])pypi-[0-9a-zA-Z_\-]{20,}(?![A-Za-z0-9])"), "PyPI API Token gefunden"),
    # SendGrid API keys: SG.<22 chars>.<43 chars>. The two dots split the token into segments
    # that the generic [A-Za-z0-9+/=_-] entropy regex cannot match across, so without this
    # specific pattern only the trailing 43-char segment is flagged (as a generic high-entropy
    # string) and the SG. prefix plus the 22-char identifier are silently dropped.
    (re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])"), "SendGrid API Key gefunden"),
    # Anthropic issues sk-ant-<kind><NN>-<body>, where <kind> is 2 to 8 lowercase
    # letters (api, admin, oat, ort, sid, …) and <NN> a two-digit version.
    # A standard key reads sk-ant-api03-<93 chars>AA. The kind is matched as a
    # class so that the OAuth forms (oat01, ort01) and later kinds and version
    # suffixes carry the Anthropic label as well. The same expression is used in
    # src/utils/log_patterns_cloud_ai.py.
    (re.compile(r"(?<![A-Za-z0-9])sk-ant-[a-z]{2,8}[0-9]{2}-[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])"), "Anthropic API Key gefunden"),
    # OpenAI Project API keys: sk-proj-...
    (re.compile(r"(?<![A-Za-z0-9])sk-proj-[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"), "OpenAI Project API Key gefunden"),
    # OpenAI Service Account keys: sk-svcacct-...
    (re.compile(r"(?<![A-Za-z0-9])sk-svcacct-[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"), "OpenAI Service Account Key gefunden"),
    # OpenAI legacy/user API keys: sk- followed by exactly 48 alphanumeric chars.
    # The strict 48-char alphanumeric body avoids overlap with sk-ant-/sk-proj-/sk-svcacct- (all contain '-').
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{48}(?![A-Za-z0-9])"), "OpenAI API Key gefunden"),
    # Hugging Face access tokens: ``hf_<32+ alphanumeric chars>``. Issued via
    # https://huggingface.co/settings/tokens for read/write access to private
    # models, datasets and Spaces. A leak grants the token's permission scope
    # for the entire validity window (no automatic expiry on legacy tokens),
    # so credentials in committed config / notebook outputs / log artefacts
    # need precise attribution rather than a generic high-entropy hit.
    (re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Hugging Face Access Token gefunden"),
    # Groq API keys (``gsk_<48+ alphanumeric>``). Issued via
    # console.groq.com/keys for Groq's LLM inference API (extremely
    # fast token generation via custom LPU silicon — the platform's
    # competitive edge is sub-100ms first-token latency on Llama / Mixtral
    # / Gemma deployments). The ``gsk_`` prefix is unambiguous (no
    # other major issuer uses this prefix), and the strict alphanumeric
    # body lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex matches the
    # full ``gsk_<body>`` span as one generic "Hochentropischer
    # Token-String" finding, losing the Groq-specific issuer attribution
    # that incident-response keys off (revocation flow at
    # console.groq.com/keys; audit completion-API usage logs for
    # chargeback fraud / model-prompt exfiltration). Body lower bound
    # 32 chars allows future canonical length variations while
    # rejecting short ``gsk_``-prefixed fragments; real Groq keys are
    # 48-56 chars body. A leak grants the issuing account's full
    # Groq API scope: trigger inference jobs at the victim's expense
    # (USD 0.10-1.00 per completion at scale), exfiltrate proprietary
    # prompts via deployed-model queries, and potentially modify webhook
    # / billing configuration depending on the platform's account-
    # management API surface. Real-world emission patterns: Python
    # notebook outputs hardcoding ``client = Groq(api_key="gsk_...")``;
    # committed ``.env`` files; README curl examples; CI debug logs.
    (re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Groq API Key gefunden"),
    # Replicate API tokens (``r8_<40 alphanumeric>``). Issued via
    # replicate.com/account/api-tokens for Replicate's hosted-model
    # inference platform (Stable Diffusion, Llama, FLUX, Whisper,
    # custom Cog-packaged models). The ``r8_`` prefix is unambiguous
    # (no other major issuer uses this prefix). The 40-char alphanumeric
    # body matches Replicate's documented canonical format strictly —
    # rejecting short ``r8_``-prefixed fragments AND rejecting body
    # variations that don't fit the exact length. A leak's PRIMARY
    # attack vector is BILLING-CREDIT DRAIN — Replicate charges
    # per GPU-second for hosted inference (Stable Diffusion XL on
    # A100 = ~USD 0.01/sec; Llama-70B = ~USD 0.05/sec; long-running
    # video generation = $$$$). An attacker with the leaked token
    # can trigger arbitrary inference jobs, draining $1000s of
    # credits in hours before the victim notices. Distinct revocation
    # flow at replicate.com/account/api-tokens — must rotate the
    # specific token AND audit the recent inference job history.
    # Real-world emission: Python notebook outputs; ``REPLICATE_API_TOKEN=``
    # in committed ``.env``; HuggingFace Spaces secrets leaked to repo
    # config.
    (re.compile(r"(?<![A-Za-z0-9])r8_[A-Za-z0-9]{40}(?![A-Za-z0-9])"), "Replicate API Token gefunden"),
    # Perplexity API keys (``pplx-<32+ alphanumeric>``). Issued via
    # perplexity.ai/settings/api for Perplexity's Sonar / Sonar-Pro API
    # (AI-powered search and chat completions with real-time web
    # grounding). The ``pplx-`` prefix is unambiguous, and the strict
    # alphanumeric body lies entirely inside the entropy alphabet — so
    # the entropy regex matches the full ``pplx-<body>`` span as one
    # generic "Hochentropischer Token-String" finding, losing the
    # Perplexity-specific issuer attribution that incident-response
    # keys off (revocation flow at perplexity.ai/settings/api; audit
    # Sonar-API completion logs for chargeback fraud / prompt
    # exfiltration). Body lower bound 32 chars allows future canonical
    # length variations while rejecting short ``pplx-``-prefixed
    # fragments; real Perplexity keys are 48-56 chars body. A leak
    # grants the issuing account's full Perplexity API scope:
    # trigger Sonar / Sonar-Pro completions at the victim's expense
    # (USD 0.20-1.00 per completion for Sonar-Pro with web grounding),
    # exfiltrate proprietary prompts. Real-world emission: ``.env``
    # commits, curl examples in tutorials, CI/CD pipeline debug.
    (re.compile(r"(?<![A-Za-z0-9])pplx-[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Perplexity API Key gefunden"),
    # xAI Grok API keys (``xai-<32+ alphanumeric>``). Issued via
    # console.x.ai/team/<id>/api-keys for xAI's Grok platform
    # (Grok-2, Grok-3, Grok-4 — Elon Musk's LLM family released
    # in 2024-2025). The ``xai-`` prefix is unambiguous (no other
    # major issuer uses it), and the strict alphanumeric body lies
    # entirely inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — so the entropy regex matches the full ``xai-<body>``
    # span as one generic "Hochentropischer Token-String" finding,
    # losing the xAI-specific issuer attribution that incident-
    # response keys off (revocation flow at console.x.ai/team/<id>/
    # api-keys; audit Grok completion-API usage logs for chargeback
    # fraud / model-prompt exfiltration; check the org's billing
    # dashboard for unauthorized large-context completions —
    # Grok-4's 200K-token context window makes a single hostile
    # completion expensive). Body lower bound 32 chars allows future
    # canonical length variations while rejecting short prefix-only
    # fragments; real xAI keys are 64-128 chars body. A leak grants
    # the issuing account's full xAI API scope: trigger expensive
    # Grok completions at the victim's expense (USD 5-15 per 1M
    # tokens), exfiltrate proprietary prompts. xAI Grok was the
    # named-but-deferred next-round candidate from the 2026-05-16
    # Round-1 AI/ML platform tier closure (Groq / Replicate /
    # Perplexity); this round closes it.
    (re.compile(r"(?<![A-Za-z0-9])xai-[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "xAI API Key gefunden"),
    # OpenRouter API keys (``sk-or-v1-<32+ alphanumeric>``). Issued
    # via openrouter.ai/keys for OpenRouter's unified OpenAI-
    # compatible API aggregator that proxies requests to 200+
    # different LLMs (Claude, GPT, Llama, Mixtral, Gemini, Grok,
    # DeepSeek, etc.). The ``sk-or-v1-`` prefix is structurally
    # DISTINCT from OpenAI's strict ``sk-<48 alphanumeric>`` form
    # (the embedded hyphens in ``sk-or-v1-`` prevent matching
    # OpenAI's regex because the OpenAI body alphabet excludes ``-``),
    # so the two detectors are mutually exclusive at the prefix
    # level. Pre-fix OpenRouter tokens fell to the entropy fallback
    # (the full ``sk-or-v1-<body>`` span matches the high-entropy
    # alphabet because ``-`` is in ``[A-Za-z0-9+/=_-]``) — but as
    # generic "Hochentropischer Token-String", losing the
    # OpenRouter-specific issuer attribution. UNIQUE THREAT
    # AMPLIFIER for OpenRouter: BYOK (Bring Your Own Key) — the
    # platform allows users to attach their own provider keys for
    # fallback / cost optimization. A leaked OpenRouter token grants
    # access to ALL the user's attached provider keys (visible /
    # reusable via the OpenRouter dashboard). This is a CROSS-
    # PLATFORM PIVOT amplifier unique to aggregator platforms —
    # a single OpenRouter leak can compound to leak Anthropic /
    # OpenAI / Groq / etc. keys without those being separately
    # exposed in source.
    (re.compile(r"(?<![A-Za-z0-9])sk-or-v1-[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "OpenRouter API Key gefunden"),
    # Database connection strings with embedded credentials
    # (``<scheme>://<user>:<pass>@<host>``). Pre-fix EVERY database
    # URI with credentials was SILENTLY UNDETECTED across BOTH
    # detection branches:
    #   1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): the body
    #      alphabet ``[A-Za-z0-9+/=_-]`` excludes ``:``, ``/``, ``@``,
    #      so the entropy matcher splits at every URI delimiter. The
    #      fragments (``postgres``, ``admin``, ``secret123``,
    #      ``db.example.com``, ``5432``, ``prod``) are each below the
    #      24-char floor — NO finding fires.
    #   2. **Assignment heuristic** (``_SENSITIVE_ASSIGN_RE``): even
    #      with a sensitive variable name (``DATABASE_URL``,
    #      ``MONGO_URI``, ``REDIS_URL``), the unquoted-value branch
    #      SKIPS values containing ``()[]:`` characters at
    #      ``_scan_content``. Every database URI contains ``://`` AND
    #      a port-separator ``:`` — the check rejects the URI verbatim.
    # Combined effect: a committed ``.env`` file with
    # ``DATABASE_URL=postgres://admin:supersecret@prod-db.example.com:5432/prod``
    # ships to production with NO scanner alert.
    #
    # Threat model: database credentials are HIGHEST-VALUE secrets —
    # the URI password is base64-decodable in plain text (no offline
    # cracking needed), the URI typically targets production, and the
    # leak enables: (a) full read/write access to all customer data /
    # billing records / session stores; (b) lateral movement via
    # shared password reuse on related infrastructure; (c) schema
    # reconnaissance for social-engineering; (d) persistence via
    # INSERT of backdoor records.
    #
    # The regex matches the canonical RFC-3986-style URI shape for
    # the top database / broker / mail schemes. The structural
    # requirement ``user:pass@`` prevents matching URIs without
    # credentials (``postgres://localhost/db`` is benign — no match).
    # The ``(?i)`` inline flag handles case-insensitive scheme literals
    # (per RFC 3986 §3.1, URI schemes are case-insensitive). The
    # optional ``jdbc:`` prefix covers Java JDBC URL conventions.
    # The user part admits ``@``: mail logins are often written
    # unencoded (``smtps://noreply@example.com:pw@smtp.example.com``),
    # and urllib / requests split userinfo at the LAST ``@``, so such a
    # URI carries a live password just like the canonical shape.
    # Real-world emission: ``.env`` files, ``docker-compose.yml``,
    # Heroku ``app.json``, settings.py / application.yml /
    # database.yml, Python notebook output, README example URIs
    # (often live!), migration scripts, K8s ConfigMaps, Terraform
    # outputs.
    (
        re.compile(
            r"(?i)\b(?:jdbc:)?"
            r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|"
            r"amqp|amqps|kafka|clickhouse|cassandra|elasticsearch|smtp|smtps)"
            r"://[^\s/:]+:[^@\s/]+@[^\s/]+"
        ),
        "Database Connection String gefunden",
    ),
    # Directory / Shell / File-Share Connection Strings with embedded
    # credentials (``<scheme>://<user>:<pass>@<host>``) for the
    # ``ldap`` / ``ldaps`` / ``ssh`` / ``sftp`` / ``smb`` / ``cifs``
    # adjacent families. Strict-sibling drift closure for the
    # 2026-05-16 Database Connection String round above: both the
    # log-sanitisation codepath at ``src/utils/http.py:_URL_AUTH_RE``
    # AND the malformed-URI pattern at ``src/utils/logging.py``
    # already enumerate this EXACT 6-scheme adjacent family — the
    # scanner detection codepath was the third sibling left out of
    # step, so a committed ``LDAP_BIND_URL=ldap://admin:pw@dc01/``
    # shipped to production with the log path masking the credential
    # at operator-log emission time but the git-history source-of-
    # truth leaking the credential verbatim into every clone /
    # archive / search-engine cache.
    #
    # Pre-fix detection gaps (mirror the Database round's analysis):
    #   1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): body alphabet
    #      ``[A-Za-z0-9+/=_-]`` excludes ``:``, ``/``, ``@`` — the
    #      matcher splits at every URI delimiter. The fragments
    #      (``ldap``, ``admin``, ``CompanyAdmin``, ``dc01``,
    #      ``389``, ``dc=example``) are each below the 24-char
    #      floor; no entropy finding fires.
    #   2. **Assignment heuristic** (``_SENSITIVE_ASSIGN_RE``):
    #      even with a sensitive variable name (``LDAP_BIND_URL``,
    #      ``SSH_DEPLOY_URL``, ``SMB_SHARE_URL``), the unquoted-
    #      value branch SKIPS values containing ``:`` (port /
    #      user-pass separator). Every URI contains ``://`` AND
    #      ``:`` — the check rejects the URI verbatim.
    #
    # Threat model (higher-severity than Database — these grant
    # infrastructure-control-plane access, not just data-plane):
    #   * **LDAP / LDAPS**: Active-Directory service-account
    #     bind credentials. Leak grants forest enumeration, user-
    #     object read, attacker-machine domain-join, privileged-
    #     account reset and — with Replicate-Directory-Changes —
    #     DCSync to extract every krbtgt / computer-account hash.
    #   * **SSH / SFTP**: interactive shell / chrooted file-system
    #     access on the target. Universal post-exploitation
    #     primitive (backdoors, exfiltration, lateral pivot).
    #     Common in Ansible inventory, Capistrano ``deploy.rb``,
    #     Fabric ``fabfile.py``, GitLab CI ``deploy_keys``,
    #     GitHub Actions secrets, Dockerfile ``RUN ssh`` patterns.
    #   * **SMB / CIFS**: Windows file-share access. Routine
    #     leak surface for HR documents, executive correspondence,
    #     source-code backups, developer roaming profiles
    #     (containing cached AD credentials).
    #
    # The regex mirrors the Database round's structural anchors:
    # optional ``jdbc:`` prefix (Java JNDI), case-insensitive scheme
    # literal, ``://`` separator, structural ``user:pass@`` shape
    # requirement (so credential-less URIs ``ssh://host`` are NOT
    # flagged). Distinct reason ``Directory/Shell/Share Connection
    # String gefunden`` routes incident-response triage to the
    # correct revocation flow (AD service-account password rotation
    # vs. SSH key rotation vs. file-server password reset are three
    # distinct playbooks). Schemes match exactly the 6-scheme set in
    # ``src/utils/http.py:_URL_AUTH_RE`` and ``src/utils/logging.py``
    # — sibling-floor alignment guarded by
    # ``test_sentinel_directory_shell_share_uri_credential_drift.
    # test_sibling_floor_alignment_with_log_sanitization``.
    (
        re.compile(
            r"(?i)\b(?:jdbc:)?"
            r"(?:ldap|ldaps|ssh|sftp|smb|cifs)"
            r"://[^\s/:]+:[^@\s/]+@[^\s/]+"
        ),
        "Directory/Shell/Share Connection String gefunden",
    ),
    # DigitalOcean Personal Access Tokens (``dop_v1_<64 hex>``) and OAuth
    # refresh tokens (``doo_v1_<64 hex>``). The ``v1`` prefix anchors against
    # the official format; the strict 64-char lowercase-hex body avoids
    # overlap with the generic high-entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet (which would otherwise flag the body without preserving the
    # ``dop_v1_``/``doo_v1_`` issuer attribution). A leaked dop_v1_ grants
    # full account API access; a leaked doo_v1_ mints fresh dop_v1_'s until
    # revocation, so refresh-token leaks have multi-day blast radius.
    (re.compile(r"(?<![A-Za-z0-9])dop_v1_[a-f0-9]{64}(?![A-Za-z0-9])"), "DigitalOcean Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])doo_v1_[a-f0-9]{64}(?![A-Za-z0-9])"), "DigitalOcean OAuth Refresh Token gefunden"),
    # GitLab Pipeline Trigger Tokens: ``glptt-<40 chars>``. Distinct from
    # GitLab PATs (``glpat-``) — these tokens are scoped to triggering CI
    # pipelines via the API. A leaked trigger token lets a network adversary
    # kick off arbitrary pipeline runs (including any ``protected_branches``
    # secrets exposed to those pipelines), so the leak surface is the
    # repository's CI permissions rather than the user's PAT scope.
    (re.compile(r"(?<![A-Za-z0-9])glptt-[0-9a-zA-Z_\-]{40}(?![A-Za-z0-9])"), "GitLab Pipeline Trigger Token gefunden"),
    # Mapbox Access Token (``(?:pk|sk|tk)\.eyJ<3-segment-JWT-body>``).
    # Issued via account.mapbox.com/access-tokens/ for the Mapbox Maps /
    # Geocoding / Directions / Static Images / Tilesets / Vision SDK
    # APIs (the geospatial-vendor counterpart to the existing Google
    # API Key family already in this table). The on-the-wire format
    # is a 3-char prefix (``pk.`` public / ``sk.`` secret / ``tk.``
    # temporary) followed by a canonical 3-segment base64url JOSE JWT
    # body — structurally a JWT, but with a Mapbox-specific scope-
    # tier prefix that sits OUTSIDE the existing JWT detector's
    # ``eyJ`` anchor.
    #
    # ORDER REQUIREMENT: this entry MUST appear BEFORE the JWT entry
    # below so the more-specific Mapbox attribution wins via the
    # ``covered_ranges`` arbitration in ``_scan_content``. Pre-fix the
    # JWT entry matched the inner ``eyJ<body>.<body>.<body>`` span and
    # the leading ``pk.``/``sk.``/``tk.`` scope tier was LOST from
    # attribution AND from the covered span. The lookbehind
    # ``(?<![A-Za-z0-9])`` succeeds because the ``.`` before ``eyJ``
    # is non-alphanumeric, so the JWT regex match span ended one
    # character before the Mapbox span — the operator saw a generic
    # JWT finding without knowing it was a Mapbox secret token
    # demanding account.mapbox.com revocation.
    #
    # Threat model per scope tier (each maps to a DISTINCT operational
    # consequence — IR triage MUST identify the scope from the prefix
    # because Mapbox does NOT allow rotating just the scope without
    # rotating the entire token):
    #   * ``pk.`` — Public Access Token. Client-side scopes
    #     (``styles:read`` / ``fonts:read`` / ``datasets:read`` /
    #     ``vision:read``). LEAK: quota theft (third party uses your
    #     Mapbox account's monthly map-load / geocoding quota until
    #     it overflows; per-load overage charges from Mapbox start at
    #     USD 0.50 per 1k loads above the free tier). Routine in
    #     client-side JavaScript bundles (the token is published in
    #     the page's JS payload by design) but committed in source
    #     control it can still be exfiltrated by a network adversary
    #     for sustained quota abuse.
    #   * ``sk.`` — **SECRET Access Token (HIGHEST blast radius in the
    #     Mapbox family).** Full account-write scopes
    #     (``tilesets:write`` / ``uploads:write`` / ``datasets:write``
    #     / ``tokens:write`` / ``styles:write`` / ``analytics:read`` /
    #     potentially ``credentials:write`` for billing). LEAK:
    #     overwrite production tilesets with attacker-controlled
    #     content (route-hijack / map-manipulation amplifier for
    #     embedded navigation widgets), mint new ``sk.`` tokens to
    #     maintain persistence, exfiltrate the account's billing /
    #     analytics data, and modify the production maps used by
    #     downstream consumers (the Wien-OePNV project explicitly
    #     avoids Mapbox in favour of OpenStreetMap — see README §
    #     Datenquellen — but downstream forks and integrators may
    #     hold ``sk.`` tokens that this detector now protects).
    #   * ``tk.`` — Temporary Access Token (ephemeral scope, short
    #     TTL). Lower blast radius than ``pk.``/``sk.`` because the
    #     credential expires; still warrants distinct attribution
    #     so IR can verify the leak window aligned with token TTL.
    #
    # Real-world emission patterns: ``.env`` files
    # (``MAPBOX_ACCESS_TOKEN=sk.eyJ...``), client-side JavaScript
    # bundle build outputs (the public ``pk.`` is canonical, but the
    # ``sk.`` SHOULD NEVER ship to client — a leak in the bundle is
    # a high-severity finding), CI/CD pipeline debug logs, GitHub
    # Actions secrets dumped to logs by a misconfigured action,
    # Mapbox SDK error responses echoing the token back in
    # diagnostic messages, OpenStreetMap-to-Mapbox-bridge tools
    # that hard-code the secret for tileset-upload automation.
    (
        re.compile(
            r"(?<![A-Za-z0-9])(?:pk|sk|tk)\.eyJ"
            r"[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}"
            r"(?![A-Za-z0-9])"
        ),
        "Mapbox Access Token gefunden",
    ),
    # JSON Web Tokens (JWTs): ``eyJ<header>.<payload>.<signature>`` where
    # each segment is base64url-encoded ``[A-Za-z0-9_-]+``. The ``eyJ``
    # prefix is the base64url encoding of ``{"`` (the start of every JOSE
    # JSON header). Multi-segment dot-separated tokens bypass the generic
    # high-entropy fallback (which uses ``[A-Za-z0-9+/=_-]`` and stops at
    # the first dot), so without this specific pattern only one segment
    # at a time would be flagged — losing the full token attribution and
    # making revocation harder. Min lengths chosen to cover realistic
    # HS256/RS256 tokens (~30-char header, ~30-char payload, ~43-char
    # signature) without flagging short base64url strings that happen to
    # have the ``eyJ`` prefix purely by collision. Order: place AFTER more
    # specific issuer-prefixed tokens so ``is_covered`` correctly anchors.
    # The Mapbox detector immediately above MUST stay before this entry
    # because Mapbox tokens have a ``(?:pk|sk|tk)\.`` scope-tier prefix
    # that this JWT pattern's ``eyJ`` anchor would otherwise strip from
    # the matched span — losing the Mapbox-specific issuer attribution
    # and the scope-tier disambiguator that anchors IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])"),
        "JSON Web Token (JWT) gefunden",
    ),
    # Twilio Account SID (``AC<32 hex>``) and API Key SID (``SK<32 hex>``).
    # Twilio uses 34-char SIDs prefixed with a 2-letter resource-type code
    # followed by 32 lowercase hex chars; the Account SID is the principal
    # credential for the project and pairs with the Auth Token to make API
    # calls (call/SMS history, billing, phone-number provisioning), while
    # the API Key SID pairs with a separate secret for fine-grained scoped
    # access. Without a specific pattern these tokens fall back to the
    # generic high-entropy detector which would flag the 32-hex body as a
    # bare hash-like string, losing the issuer attribution that incident
    # response keys off (Twilio's revocation flow lives on twilio.com and
    # is distinct from any other vendor's). NOTE: lowercase ``sk_*``
    # (Stripe) does NOT collide — Stripe's prefix is ``sk_live_`` /
    # ``sk_test_`` (lowercase + underscore), Twilio's is uppercase ``SK``
    # immediately followed by hex, so the patterns are mutually exclusive.
    (re.compile(r"(?<![A-Za-z0-9])AC[a-f0-9]{32}(?![A-Za-z0-9])"), "Twilio Account SID gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])SK[a-f0-9]{32}(?![A-Za-z0-9])"), "Twilio API Key SID gefunden"),
    # Notion Internal Integration Token (``secret_<43 alphanumeric>``).
    # Notion API tokens are issued via developer integrations at
    # https://www.notion.so/my-integrations and grant read/write access to
    # whatever workspace content the integration is shared with — full
    # database/page contents, including any private collaborator notes.
    # The ``secret_`` prefix is Notion's canonical issuer tag, but the
    # underscore separates the prefix from the 43-char alphanumeric body,
    # so the entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet WOULD match
    # the full token as a single span — but only flag it as a generic
    # high-entropy hit, losing the Notion-specific issuer attribution that
    # downstream revocation playbooks need. The body length is exactly
    # 43 alphanumeric chars (no underscores or hyphens) — strict body
    # match avoids colliding with operator-set ``SECRET_KEY = "..."``
    # variable assignments captured by the broader ``_SENSITIVE_ASSIGN_RE``.
    (
        re.compile(r"(?<![A-Za-z0-9])secret_[A-Za-z0-9]{43}(?![A-Za-z0-9])"),
        "Notion Integration Token gefunden",
    ),
    # Notion Modern Integration Token (``ntn_<43+ chars>``). The newer
    # token format Notion introduced for the v2024-09-API rollout. Same
    # blast radius as the legacy ``secret_`` form (workspace read/write
    # against the integration's shared content), so distinct attribution
    # matters for revocation. ``ntn_`` is unambiguous (no other major
    # issuer uses this prefix), and the 43+ alphanumeric/underscore/hyphen
    # body distinguishes the modern format from the strict-43-alphanumeric
    # legacy ``secret_`` body above.
    (
        re.compile(r"(?<![A-Za-z0-9])ntn_[A-Za-z0-9_\-]{43,}(?![A-Za-z0-9])"),
        "Notion Modern Integration Token gefunden",
    ),
]
