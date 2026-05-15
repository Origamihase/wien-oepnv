"""Utility helpers to detect accidentally committed secrets."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Sequence
import re
import subprocess  # nosec B404

from .files import read_capped_text

__all__ = [
    "Finding",
    "scan_repository",
    "load_ignore_file",
    "MAX_IGNORE_FILE_BYTES",
    "MAX_SCAN_FILE_BYTES",
]

# Security: per-loader byte caps for the two on-disk parsers in this
# module. Pre-fix both sites used ``Path.read_text(encoding="utf-8",
# errors="ignore")`` with NO size cap whatsoever — a planted huge file
# at the ignore-file path or any tracked file in the repo raised
# ``MemoryError`` past the surrounding handler and crashed the secret
# scanner CI gate, bypassing detection on subsequent commits.
#   - ``.secret-scan-ignore`` is a small list of glob patterns,
#     typically a few KiB; 1 MiB is ~1000x legit.
#   - Per-file scan content must accommodate large checked-in data
#     files (HTML test fixtures, mapping JSONs); 50 MiB matches the
#     ``DEFAULT_MAX_TEXT_FILE_BYTES`` ceiling for non-JSON disk reads
#     while still rejecting GiB-sized planted attacks.
MAX_IGNORE_FILE_BYTES = 1 * 1024 * 1024
MAX_SCAN_FILE_BYTES = 50 * 1024 * 1024

log = logging.getLogger(__name__)

_HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9])")

# Detect sensitive variable assignments (e.g. key = "value")
# We use a broad list of keywords and allow common separators (hyphens, dots) in prefixes/suffixes
# to catch variations like my-api-key, config.client_secret, etc.
_SENSITIVE_ASSIGN_RE = re.compile(
    r"""(?xis)
    (
        # Group 1: The key
        (?:
            [a-z0-9_.-]*  # Prefix allowing letters, numbers, underscores, dots, hyphens
            (?:
                token|secret|password|passphrase|credential|
                accessid|accesskey|access-key|access.key|
                apikey|api-key|api.key|
                privatekey|private-key|private.key|
                secret-key|secret.key|client-secret|client.secret|
                authorization|auth-token|auth.token|auth|
                _key|ssh-key|ssh.key|id_rsa|
                clientid|client-id|client.id|client_id|
                session_id|session-id|session.id|sessionid|
                cookie|signature|bearer|jwt|
                webhook_url|webhook-url|webhook.url|webhook|
                dsn|subscriptionkey
            )
            [a-z0-9_.-]*  # Suffix allowing letters, numbers, underscores, dots, hyphens
        )
        |
        (?:
            # Strict matching for short/risky keywords to avoid false positives (e.g. throughput)
            [a-z0-9_.-]*  # Prefix
            (?:
                glpat|ghp|otp
            )
            (?:[-_][a-z0-9_.-]*)?  # Strict suffix (underscore/hyphen required or end)
        )
    )
    \s*[:=]\s*  # Assignment operator (= or :) surrounded by flexible whitespace (including newlines)
    (
        (?:\"{3}.*?\"{3})|         # Triple-double-quoted value (non-greedy)
        (?:'{3}.*?'{3})|           # Triple-single-quoted value (non-greedy)
        (?:\"(?:\\.|[^\"\\])*\")|  # Double-quoted value
        (?:'(?:\\.|[^'\\])*')|     # Single-quoted value
        [^;#'\"\n]+                # Unquoted value (until comment or newline)
    )
    """
)

_AWS_ID_RE = re.compile(r"(?<![A-Za-z0-9])(AKIA|ASIA|ACCA)[A-Z0-9]{16}(?![A-Za-z0-9])")
_BEARER_RE = re.compile(r"Bearer\s+([A-Za-z0-9\-_.]{16,})")
_PEM_RE = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(?:.|\n)*?(-----END [A-Z ]*PRIVATE KEY-----)")

# Known high-value token patterns to detect specifically
# These bypass the generic entropy checks and provide specific descriptions
_KNOWN_TOKENS = [
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
    # Anthropic API keys: sk-ant-api{NN}-... and sk-ant-admin{NN}-...
    # Standard format: sk-ant-api03-<93 chars>AA. Pattern stays loose to also catch
    # forthcoming version suffixes (api04, admin02, …) without missing real leaks.
    (re.compile(r"(?<![A-Za-z0-9])sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])"), "Anthropic API Key gefunden"),
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
    # secret stored in the namespace's apps and integrations). Total
    # length typically 95-110 chars (4-char prefix incl. dot + 90+ char
    # base64url body). The ``hvs.`` prefix is unique to HashiCorp's
    # modern HCP token format (introduced 2023; replaces the legacy
    # ``hvb.`` admin tokens) and the literal ``.`` separator
    # disambiguates from any alphanumeric-prefixed token already in the
    # table. A leak grants whoever holds the token full read-access to
    # every secret the issuing service principal / human user can see —
    # the highest blast-radius credential class in the modern infra
    # stack. The revocation flow lives at portal.cloud.hashicorp.com
    # and is distinct from any other vendor's, so issuer-specific
    # attribution is critical for IR triage. Pre-fix the entropy
    # fallback flagged the body as a generic high-entropy span (the
    # ``.`` is OUTSIDE the entropy alphabet ``[A-Za-z0-9+/=_-]``, so
    # only the body span after ``hvs.`` matched), losing the
    # HCP-specific issuer attribution.
    (
        re.compile(r"(?<![A-Za-z0-9])hvs\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])"),
        "HCP Vault Secrets Token gefunden",
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
]


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    match: str
    reason: str


def load_ignore_file(base_dir: Path, filename: str = ".secret-scan-ignore") -> list[str]:
    path = base_dir / filename
    if not path.exists():
        return []
    # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap so
    # a planted huge ``.secret-scan-ignore`` cannot exhaust memory and
    # crash the CI gate before secrets are detected on the rest of the
    # repo. ``errors="ignore"`` preserves the legacy lossy-decode
    # contract for non-UTF-8 fragments.
    content = read_capped_text(
        path,
        MAX_IGNORE_FILE_BYTES,
        errors="ignore",
        label="secret-scan-ignore",
        logger=log,
    )
    if content is None:
        return []
    patterns: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _tracked_files(base_dir: Path) -> list[Path]:
    try:
        # Bandit B603/B607: ``git ls-files`` runs on a trusted local path,
        # command list is fully static (no user input).
        completed = subprocess.run(  # nosec B603, B607
            ["git", "ls-files", "-z"],
            cwd=base_dir,
            check=True,
            shell=False,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return [path for path in base_dir.rglob("*") if path.is_file()]
    stdout = completed.stdout.decode("utf-8", errors="ignore")
    files: list[Path] = []
    for entry in stdout.split("\0"):
        if not entry:
            continue
        files.append((base_dir / entry).resolve())
    return files


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return True
    return b"\0" in chunk


def _looks_like_secret(candidate: str, is_assignment: bool = False) -> bool:
    # Allow shorter secrets for explicit assignments (e.g. password="...")
    min_len = 8 if is_assignment else 24
    if len(candidate) < min_len:
        return False
    categories = 0
    categories += any(c.islower() for c in candidate)
    categories += any(c.isupper() for c in candidate)
    categories += any(c.isdigit() for c in candidate)

    # In strict contexts (assignment to sensitive var), allow symbols/spaces as entropy
    if is_assignment:
        categories += any(not c.isalnum() for c in candidate)

    # In strict contexts (assignments), we allow single-category secrets (e.g. all-lowercase)
    # provided they meet the length and entropy requirements.
    min_categories = 1 if is_assignment else 2
    if categories < min_categories:
        return False
    if len(set(candidate)) < max(6, len(candidate) // 4):
        return False
    return True


def _mask_secret(value: str) -> str:
    """Mask a secret value for display (e.g. 'AKIA***1234')."""
    length = len(value)
    if length <= 8:
        return "***"
    if length <= 20:
        return f"{value[:2]}***{value[-2:]}"
    return f"{value[:4]}***{value[-4:]}"


def _scan_content(content: str) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    covered_ranges: list[tuple[int, int]] = []

    # Pre-calculate line offsets for fast lookup
    # Using simple list of newline positions
    newlines = [i for i, char in enumerate(content) if char == "\n"]

    def get_line_number(index: int) -> int:
        from bisect import bisect_left
        # newlines contains indices of newlines.
        # If index is before first newline, it's line 1 (bisect returns 0)
        # If index is after first newline, it's line 2 (bisect returns 1)
        return bisect_left(newlines, index) + 1

    def is_covered(start: int, end: int) -> bool:
        for c_start, c_end in covered_ranges:
            if start < c_end and end > c_start:
                return True
        return False

    for match in _PEM_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if not is_covered(span_start, span_end):
            findings.append((get_line_number(match.start()), candidate, "Private Key (PEM) gefunden"))
            covered_ranges.append((span_start, span_end))

    for regex, reason in _KNOWN_TOKENS:
        for match in regex.finditer(content):
            candidate = match.group(0)
            span_start, span_end = match.span(0)

            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, reason))
                covered_ranges.append((span_start, span_end))

    for match in _AWS_ID_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if not is_covered(span_start, span_end):
            findings.append((get_line_number(match.start()), candidate, "AWS Access Key ID gefunden"))
            covered_ranges.append((span_start, span_end))

    for match in _BEARER_RE.finditer(content):
        candidate = match.group(1)
        span_start, span_end = match.span(1)

        if _looks_like_secret(candidate, is_assignment=True):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Bearer-Token wirkt echt"))
                covered_ranges.append((span_start, span_end))

    for match in _SENSITIVE_ASSIGN_RE.finditer(content):
        candidate = match.group(2).strip()
        # Strip outer quotes if present
        quoted = False
        # Handle triple quotes first (check length >= 6 to avoid index errors)
        if candidate.startswith('"""') and candidate.endswith('"""') and len(candidate) >= 6:
            candidate = candidate[3:-3]
            quoted = True
        elif candidate.startswith("'''") and candidate.endswith("'''") and len(candidate) >= 6:
            candidate = candidate[3:-3]
            quoted = True
        elif (candidate.startswith('"') and candidate.endswith('"')) or (
            candidate.startswith("'") and candidate.endswith("'")
        ):
            candidate = candidate[1:-1]
            quoted = True

        if not quoted:
            # Ignore code-like constructs in unquoted values
            if any(c in candidate for c in "().[]:"):
                continue
            # Ignore common Python keywords to avoid flagging code as secrets
            if candidate.startswith(
                (
                    "return ",
                    "import ",
                    "from ",
                    "class ",
                    "def ",
                    "if ",
                    "else",
                    "elif",
                    "for ",
                    "while ",
                    "try",
                    "except",
                    "with ",
                    "async ",
                    "await ",
                    "raise ",
                )
            ):
                continue
            if candidate in ("None", "True", "False"):
                continue

        # Use the span of the value group (including quotes) for coverage
        span_start, span_end = match.span(2)

        if _looks_like_secret(candidate, is_assignment=True):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Verdächtige Zuweisung eines potentiellen Secrets"))
                covered_ranges.append((span_start, span_end))

    for match in _HIGH_ENTROPY_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if candidate.isalpha():
            # Reduce false positives for LongCamelCaseClassNames
            continue

        if _looks_like_secret(candidate):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Hochentropischer Token-String"))

    return findings


def _should_ignore(path: Path, patterns: Sequence[str], base_dir: Path) -> bool:
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        return False
    return any(relative.match(pattern) for pattern in patterns)


def scan_repository(
    base_dir: Path,
    *,
    paths: Iterable[Path] | None = None,
    ignore_patterns: Sequence[str] | None = None,
) -> list[Finding]:
    ignore_patterns = tuple(ignore_patterns or ())
    if paths is not None:
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(p for p in path.rglob("*") if p.is_file())
            else:
                files.append(path)
    else:
        files = _tracked_files(base_dir)
    findings: list[Finding] = []
    for file_path in files:
        if not file_path.exists() or not file_path.is_file():
            continue
        if _should_ignore(file_path, ignore_patterns, base_dir):
            continue
        if _is_binary(file_path):
            continue
        # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap
        # so a planted huge tracked file (e.g. an intentionally-corrupt
        # data dump) cannot exhaust memory and crash the scanner before
        # planted secrets in sibling files are flagged.
        # ``errors="ignore"`` preserves the legacy lossy-decode contract
        # for non-UTF-8 fragments that aren't filtered by ``_is_binary``.
        content = read_capped_text(
            file_path,
            MAX_SCAN_FILE_BYTES,
            errors="ignore",
            label="scan target",
            logger=log,
        )
        if content is None:
            continue

        for lineno, snippet, reason in _scan_content(content):
            # Mask the secret value to prevent leakage in logs/CI
            masked = _mask_secret(snippet)
            findings.append(
                Finding(
                    path=file_path,
                    line_number=lineno,
                    match=masked,
                    reason=reason,
                )
            )
    return findings
