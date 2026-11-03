"""Part of ``_KNOWN_TOKENS`` of the secret scanner:
Tailscale, AWS STS ABIA, Dropbox, Pulumi, Slack xapp, Databricks, HubSpot,
PlanetScale, Heroku, Docker Hub.

Moved verbatim from ``src/utils/secret_scanner.py``; the facade joins the parts
in their original order (the first matching entry wins).
"""

from __future__ import annotations

import re

KNOWN_TOKENS_CLOUD_PLATFORM = [
    # Tailscale Key family (``tskey-(?:auth|api|client|webhook)-<keyID>-<keySecret>``).
    # Issued via login.tailscale.com/admin/settings/keys for tailnet
    # operations across four documented tiers:
    #   * ``tskey-auth-`` — Auth Key for registering new nodes into the
    #     tailnet's overlay network (pre-auth or reusable; with or
    #     without ephemeral lifetime).
    #   * ``tskey-api-`` — API Access Token for the management REST API
    #     (manage devices, ACLs, DNS, user provisioning).
    #   * ``tskey-client-`` — OAuth client secret for programmatic
    #     access via the OAuth2 client-credentials flow.
    #   * ``tskey-webhook-`` — Webhook signing secret for verifying
    #     payload authenticity from tailnet event subscriptions.
    #
    # Format: ``tskey-<tier>-<keyID>-<keySecret>`` where keyID is 8+
    # alphanumeric chars (real-world 9-14) and keySecret is 20+
    # alphanumeric chars (real-world 30-50+). The multiple dash-
    # separated segments bypass the entropy fallback's contiguous-match
    # span (dashes ARE in the alphabet, but Tailscale tokens are
    # typically broken at the dashes by the heuristic split inside
    # ``_scan_content``) — individual ``<keyID>``/``<keySecret>``
    # fragments frequently fall below the 24-char entropy floor when
    # examined in isolation, and the issuer attribution that anchors
    # the revocation flow is lost.
    #
    # Threat model per tier (each maps to a DISTINCT revocation
    # sub-page of login.tailscale.com/admin/settings):
    #   * ``auth`` — leak lets an attacker attach a rogue NODE to the
    #     victim's private overlay network. The rogue node sees every
    #     subnet-routed service the tailnet exposes (internal admin
    #     panels, dev databases, monitoring dashboards) and can pivot
    #     laterally as a trusted peer. Auth keys can have re-use limits
    #     (single-use / N-use / unlimited) — unlimited keys are the
    #     highest-blast-radius variant.
    #   * ``api`` — leak grants admin-API access: modify ACLs (open
    #     attacker access to every tailnet device), rotate DNS
    #     configuration (DNS-rebinding amplifier), add/remove users,
    #     mint fresh auth keys until revocation.
    #   * ``client`` — OAuth client secret. Leak mints OAuth access
    #     tokens for the configured scope — same blast radius as the
    #     ``api`` tier for any operation the OAuth scope grants.
    #   * ``webhook`` — webhook signing secret. Leak lets an attacker
    #     FORGE tailnet event payloads (device-joined, device-removed,
    #     ACL-updated) that downstream consumers will accept as
    #     authentic, enabling state-machine confusion attacks against
    #     IAM-integration glue code.
    #
    # The tier keyword is the structural disambiguator AND the
    # incident-response attribution — each tier has a distinct
    # revocation sub-page (the admin settings UI has separate "Keys",
    # "OAuth clients", and "Webhooks" tabs). Distinct reason per tier
    # routes IR triage to the correct sub-page in seconds.
    #
    # Real-world emission patterns: GitHub Actions secrets
    # (``TS_AUTH_KEY``, ``TAILSCALE_API_TOKEN``), Kubernetes
    # ConfigMaps / Secrets for the ``tailscale`` sidecar / operator,
    # Docker Compose env, ``terraform.tfvars`` for the Tailscale
    # Terraform provider, Ansible inventory.
    (
        re.compile(
            r"(?<![A-Za-z0-9])tskey-(?:auth|api|client|webhook)-"
            r"[A-Za-z0-9]{8,}-[A-Za-z0-9]{20,}(?![A-Za-z0-9])"
        ),
        "Tailscale Key gefunden",
    ),
    # AWS STS Service Bearer Token (``ABIA<16 chars from [A-Z0-9]>``).
    # Issued by ``sts:GetServiceBearerToken`` for service-to-service
    # authentication on behalf of an AWS user. Same 4+16=20 char format
    # as ``AKIA``/``ASIA``/``ACCA`` (already enumerated in ``_AWS_ID_RE``)
    # but the ``ABIA`` prefix was the **fourth credential prefix in the
    # AWS unique-identifier family** that ``_AWS_ID_RE`` explicitly
    # enumerated as named-but-uncovered (Round 13 closing checklist
    # listed only ``AKIA``/``ASIA``/``ACCA``). Per the AWS IAM "Unique
    # identifiers" reference, ``ABIA`` is the canonical prefix for STS
    # service bearer tokens; per gitleaks / trufflehog / detect-secrets
    # / aws-secret-detector default rules, it is detected alongside
    # the other credential prefixes.
    #
    # Pre-fix the 20-char ``ABIA<16>`` token format falls **below**
    # ``_HIGH_ENTROPY_RE``'s 24-char minimum. A bare leaked token in
    # plaintext context (log line, JSON fixture without sensitive key,
    # documentation snippet, hostile-PR fragment) was **silently
    # undetected** by every detection branch — the CI gate passed,
    # the credential sat in the public repository indefinitely, and
    # the issuing user's full AWS scope (the bearer token's authorized
    # API access window) was exposed to every consumer. In assignment
    # context the only finding was the generic ``Verdächtige
    # Zuweisung``, losing the AWS-specific issuer attribution that
    # incident-response keys off (revocation flow at IAM > STS service
    # bearer token management — distinct from the IAM > Access keys
    # flow used to revoke ``AKIA``).
    #
    # The strict ``[A-Z0-9]{16}`` body alphabet (uppercase + digits
    # only, mirroring the canonical ``_AWS_ID_RE`` body) anchors against
    # false positives on lowercase or mixed-case strings happening to
    # start with ``ABIA`` (English words / placeholder identifiers); the
    # ``(?<![A-Za-z0-9])`` lookbehind anchor prevents matching mid-word
    # occurrences. Distinct attribution from the generic AWS Access Key
    # ID reason so the report identifies WHICH AWS credential type
    # leaked and the operator can rotate via the correct STS revocation
    # flow rather than the IAM access-key rotation flow.
    (
        re.compile(r"(?<![A-Za-z0-9])ABIA[A-Z0-9]{16}(?![A-Za-z0-9])"),
        "AWS STS Service Bearer Token gefunden",
    ),
    # Dropbox Short-Lived Access Token (``sl.<base64url body>``). The
    # canonical Dropbox OAuth2 short-lived access-token format introduced
    # in the 2021 OAuth2 rotation rollout — replaces the legacy 64-char
    # alphanumeric long-lived tokens that fall into the bucket-(b) shape
    # (no canonical prefix). Issued by the ``oauth2/token`` endpoint with
    # ``grant_type=refresh_token`` and consumed by every Dropbox HTTP API
    # endpoint (``/2/files/*``, ``/2/sharing/*``, ``/2/team/*``,
    # ``/2/users/*``) for full file-storage / sharing / team-admin access.
    # The ``sl.`` prefix is unambiguous (no other major issuer uses this
    # prefix) and the literal ``.`` separator sits OUTSIDE the entropy
    # fallback's ``[A-Za-z0-9+/=_-]`` alphabet — so pre-fix the
    # ``_HIGH_ENTROPY_RE`` match started AT the base64url body after
    # the dot, stripping the ``sl.`` prefix from the matched span and
    # losing the Dropbox-specific issuer attribution. Body alphabet
    # ``[A-Za-z0-9_-]`` (base64url) lies entirely inside the entropy
    # alphabet so the body itself matched as one generic
    # ``Hochentropischer Token-String`` finding. Body lower bound 40
    # chars rejects accidental ``sl.``-prefixed fragments (operator
    # placeholder values, ISO 639 Slovenian language code URL path
    # segments like ``/sl/about``) while accepting every legitimate
    # token — real-world Dropbox short-lived tokens are 130-160 chars
    # in the base64url body span.
    #
    # Threat model: a leaked Dropbox short-lived access token grants
    # the issuing app's full file-storage / sharing / team-admin scope
    # for the token's TTL (typically 4 hours, but the same app's
    # refresh token can re-mint short-lived tokens indefinitely — a
    # leaked short-lived token strongly signals the refresh token is
    # also exposed somewhere in the same artefact). Full file read =
    # data exfiltration (customer documents, source-code backups,
    # plain-text credential notes, scanned ID cards stored as personal
    # backups). File write = ransomware-style overwrite, malicious-
    # document injection. Sharing scope = create unauthorised shared
    # links exfiltrating the team's stored content via the public
    # internet. Team-admin scope = exfiltrate the team's member
    # directory, revoke other admins' access, modify retention
    # policies for persistence. Real-world emission patterns:
    # ``.env`` files (``DROPBOX_TOKEN=sl....``), CI/CD pipeline debug
    # logs, GitHub Actions secrets dumped to logs by a misconfigured
    # action, notebook outputs hardcoding the token, Dropbox SDK error
    # responses echoing the token back in diagnostic messages.
    # Revocation flow lives at dropbox.com/developers/apps (App console
    # > app settings > "Revoke tokens") and is distinct from every
    # other vendor's. Closes the file-storage SaaS vendor family that
    # was named-but-deferred as a "bucket-(b)" candidate by prior
    # rounds — the modern short-lived format DOES carry a distinctive
    # prefix (``sl.``) that anchors per-issuer attribution.
    (
        re.compile(r"(?<![A-Za-z0-9])sl\.[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"),
        "Dropbox Short-Lived Access Token gefunden",
    ),
    # Pulumi Access Token (``pul-<40 lowercase hex>``). The canonical
    # Pulumi Personal Access Token format. Issued via
    # app.pulumi.com/account/tokens for full Pulumi Cloud API access:
    # read/write every accessible org / project / stack's state, trigger
    # arbitrary ``pulumi up`` operations modifying production
    # infrastructure, exfiltrate the org's complete deployment-history
    # audit log. Format matches the canonical trufflehog / gitleaks /
    # detect-secrets default rule (``pul-[a-f0-9]{40}``) — 4-char prefix
    # plus 40-char lowercase-hex body for a total of 44 chars. The
    # ``pul-`` prefix is unambiguous (no other major issuer uses this
    # prefix), and the strict 40-char lowercase-hex body (a SHA-1-shape
    # hash digest) anchors against false positives on placeholder values
    # like ``pull-request-1234`` or ``pul-foo`` (the ``-`` in placeholder
    # text is in the entropy alphabet but the body alphabet rejects
    # non-hex chars and the strict 40-char length rejects short fragments).
    # Pre-fix the entropy fallback matched the full ``pul-<body>`` span
    # as one generic ``Hochentropischer Token-String`` finding (both
    # ``-`` and the lowercase-hex body lie inside the entropy alphabet),
    # losing the Pulumi-specific issuer attribution that anchors the
    # app.pulumi.com/account/tokens revocation flow.
    #
    # Threat model (HIGHEST blast radius — IaC control plane): a leaked
    # Pulumi access token grants the issuing user's full Pulumi Cloud
    # API access for every accessible org / project / stack. Read
    # access = exfiltrate EVERY secret persisted in stack state (cloud
    # provider credentials are the canonical IaC-stored credential
    # class — AWS / Azure / GCP keys, database passwords, third-party
    # API keys, TLS private keys for issued certificates), reconstruct
    # the org's complete infrastructure topology for reconnaissance.
    # Write access = trigger arbitrary ``pulumi up`` operations
    # modifying production infrastructure (provision attacker-
    # controlled VMs in the victim's cloud account, modify IAM
    # bindings, add backdoored DNS records redirecting customer
    # traffic). The IaC control-plane breach is the canonical "pivot
    # to every downstream environment via a single credential"
    # amplifier — structurally analogous to a leaked Terraform Cloud
    # workspace token. Real-world emission patterns: ``.env`` files
    # (``PULUMI_ACCESS_TOKEN=pul-...``), GitHub Actions secrets,
    # CI/CD pipeline debug logs, notebook outputs, Pulumi SDK error
    # responses echoing the token back. The revocation flow lives at
    # app.pulumi.com/account/tokens > "Revoke" and is distinct from
    # every other vendor's. Closes the IaC SaaS vendor family that
    # was implicit in the previous rounds' coverage of cloud provider
    # secrets (AWS / Azure / GCP credentials are the canonical
    # IaC-stored secret family) but missing for the IaC control-plane
    # platform itself.
    (
        re.compile(r"(?<![A-Za-z0-9])pul-[a-f0-9]{40}(?![A-Za-z0-9])"),
        "Pulumi Access Token gefunden",
    ),
    # Slack App-Level Token (``xapp-<version>-<app_id>-<sequence>-<hex>``).
    # The canonical Slack App-Level Token format used for Socket Mode and
    # app-level Events API access. Issued via api.slack.com/apps/<app_id>/
    # general ("App-Level Tokens" section) with scopes from the
    # ``connections:write`` / ``authorizations:read`` family. Strict
    # sibling of the existing ``xoxb-`` / ``xoxp-`` / ``xoxa-`` / ``xoxc-``
    # / ``xoxd-`` / ``xoxe-`` / ``xoxr-`` Slack family entries — they each
    # have dedicated ``_KNOWN_TOKENS`` rows but the App-Level variant was
    # SILENTLY UNDETECTED entirely (NO finding at all). The multi-dash
    # multi-segment format splits the entropy match at every per-segment
    # boundary into fragments below the 24-char ``_HIGH_ENTROPY_RE``
    # floor (1-digit version, 11-char app id, 13-digit sequence are all
    # too short to trip the entropy detector independently), so the
    # FULL ``xapp-1-A...-...-...`` span escaped detection on every
    # branch including the generic entropy fallback. Real-world emission
    # patterns: ``.env`` files (``SLACK_APP_TOKEN=xapp-...``), GitHub
    # Actions secrets dumped to logs by a misconfigured action, notebook
    # outputs hardcoding the token in a Slack SDK ``socket_mode_client``
    # constructor, Slack SDK error responses echoing the token back.
    # Revocation flow lives at api.slack.com/apps/<app_id>/general
    # ("App-Level Tokens" section > "Regenerate") and is distinct from
    # every other Slack token family's revocation flow (xoxb/xoxp =
    # ``oauth.v2.revoke`` API; xoxc/xoxd = slack.com/account/sessions;
    # xapp = api.slack.com/apps/<app_id>/general).
    #
    # Threat model (HIGH blast radius — workspace event firehose plus
    # cross-tenant app management): a leaked ``xapp-`` grants the
    # holder the app's Socket Mode connection (full firehose of every
    # app-subscribed workspace event — DM contents, channel messages
    # the app can see, interactive component payloads, slash command
    # invocations, modal submissions). Combined with the
    # ``authorizations:read`` scope it enumerates every workspace
    # install of the app — one leaked App-Level Token compromises
    # every workspace the app is installed in (cross-tenant pivot).
    #
    # Structural anchors:
    #   * ``xapp-`` literal prefix (unambiguous; no other major issuer).
    #   * ``[0-9]+`` version segment (typically ``1``).
    #   * ``[A-Z][A-Z0-9]{8,}`` app_id segment — Slack App IDs always
    #     start with the literal ``A`` followed by 10+ uppercase alnum
    #     chars (Slack's documented App ID format).
    #   * ``[0-9]+`` sequence segment (typically 13 digits).
    #   * ``[a-zA-Z0-9]{32,}`` body floor — real Slack App-Level Token
    #     bodies are 64+ chars; the 32-char floor rejects accidental
    #     fragments while accepting future canonical-length variations.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xxapp-...`` / ``...0`` tails).
    (
        re.compile(
            r"(?<![A-Za-z0-9])xapp-[0-9]+-[A-Z][A-Z0-9]{8,}-[0-9]+-[a-zA-Z0-9]{32,}(?![A-Za-z0-9])"
        ),
        "Slack App-Level Token gefunden",
    ),
    # Databricks Personal Access Token (``dapi<32 hex>(?:-<digit>)?``).
    # The canonical Databricks PAT format. Issued via the Databricks
    # workspace UI (User Settings → Developer → Access tokens) for full
    # workspace-scoped API access (Databricks REST API ``/api/2.0/...``).
    # The body (32 lowercase hex chars after the ``dapi`` prefix) lies
    # ENTIRELY inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — pre-fix the entropy regex matched the full
    # ``dapi<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the Databricks-specific issuer
    # attribution that anchors the per-workspace revocation flow.
    #
    # Threat model (HIGH blast radius — full workspace data plane plus
    # job-execution plane): a leaked ``dapi`` grants the issuing user's
    # full Databricks workspace-scoped API access. Read access =
    # exfiltrate EVERY table the user can SELECT (Unity Catalog tables,
    # S3/ADLS/GCS-backed Delta tables, federated tables — the canonical
    # data-warehouse credential class), export entire datasets via
    # ``/api/2.0/jobs/runs/export``, exfiltrate notebook source code
    # (which routinely embeds further credentials — cloud provider keys,
    # database connection strings, third-party API keys). Write access
    # = submit arbitrary Spark jobs / SQL queries on the user's
    # attached clusters (compute-resource theft on GPU clusters at
    # USD 100s-1000s/hour), modify Unity Catalog permissions (with
    # appropriate privileges), upload backdoored notebooks to user
    # folders for persistence. The cluster-execution capability is the
    # canonical "arbitrary code execution within the cloud account"
    # amplifier — Databricks clusters run on the customer's AWS/Azure/
    # GCP account, giving the cluster the IAM role attached to the
    # cluster (often a broad ``DatabricksDataAccess`` role with S3 /
    # Glue / Athena read).
    #
    # Real-world emission patterns: ``.env`` files (``DATABRICKS_TOKEN=
    # dapi...``), CI/CD pipeline debug logs (``terraform-provider-
    # databricks`` echoing the token in plan output), notebook output
    # cells displaying ``os.environ`` for debugging, ``databricks-cli``
    # ``--profile`` config files committed by mistake. Revocation flow
    # lives at Databricks workspace UI > User Settings > Developer >
    # Access tokens > "Revoke" — distinct per workspace, distinct from
    # every other Databricks credential class (service principals, OAuth
    # apps, basic auth).
    #
    # Structural anchors:
    #   * ``dapi`` literal prefix (unambiguous; no other major issuer).
    #   * ``[a-f0-9]{32}`` strict lowercase-hex 32-char body matches
    #     Databricks' documented canonical format and rejects placeholder
    #     values (``dapibus malesuada`` from Lorem Ipsum, ``dapi-foo``).
    #   * ``(?:-[0-9]+)?`` optional version suffix supports the modern
    #     ``dapi<hex>-2`` / ``dapi<hex>-3`` rotation format.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xdapi...`` / ``...G`` tails).
    (
        re.compile(r"(?<![A-Za-z0-9])dapi[a-f0-9]{32}(?:-[0-9]+)?(?![A-Za-z0-9])"),
        "Databricks Personal Access Token gefunden",
    ),
    # HubSpot Private App Token (``pat-(?:na1|na2|na3|eu1)-<UUID>``).
    # The canonical HubSpot Private App access token format. Issued via
    # the HubSpot portal UI at Settings → Account Setup → Integrations →
    # Private Apps → <App> → Auth tab. Used for the HubSpot CRM /
    # Marketing / Automation REST APIs (``/crm/v3/...``, ``/marketing/v3/
    # ...``, ``/contacts/v1/...``). The body is a canonical RFC-4122
    # UUID (8-4-4-4-12 lowercase hex with internal ``-`` separators),
    # which DOES match the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet (``-`` IS in the alphabet) — pre-fix the entropy regex
    # matched the full ``pat-<region>-<UUID>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the HubSpot-
    # specific issuer attribution that anchors the per-portal revocation
    # flow.
    #
    # Threat model (HIGH blast radius — full CRM data plane with PII):
    # a leaked ``pat-`` grants the issuing private app's configured
    # OAuth-equivalent scopes against the HubSpot portal. The canonical
    # scope set (``crm.objects.contacts.read``/``write``,
    # ``crm.objects.companies.``, ``crm.objects.deals.``,
    # ``marketing.``, ``automation.``, ``forms.``, ``files.``)
    # provides FULL access to: the portal's complete contact database
    # (names + emails + phone + addresses + custom properties —
    # GDPR-protected PII at scale), every company and deal record
    # (B2B revenue data + pipeline forecasts), every marketing email
    # campaign (recipient lists + open / click tracking — competitive
    # intelligence goldmine), every automation workflow (modify or
    # disable triggers — sabotage primitive), every form submission
    # (incoming lead capture — exfiltrate or redirect to attacker
    # endpoint). Real-world emission patterns: ``.env`` files
    # (``HUBSPOT_PRIVATE_APP_TOKEN=pat-na1-...``), CI/CD pipeline debug
    # logs, GitHub Actions secrets dumped to logs by a misconfigured
    # action, notebook outputs hardcoding ``HubSpot(access_token=
    # "pat-na1-...")``, curl examples in README files. Revocation flow
    # lives at the HubSpot portal UI > Settings > Account Setup >
    # Integrations > Private Apps > <App> > Auth tab > "Rotate"
    # (immediate) or "Delete app" (permanent) — distinct per portal,
    # distinct from every other CRM-vendor rotation flow (Salesforce,
    # Microsoft Dynamics 365, Zoho CRM).
    #
    # Structural anchors:
    #   * ``pat-`` literal prefix (HubSpot's canonical Private App
    #     prefix; no other major issuer uses this prefix with the
    #     region + UUID body shape).
    #   * ``(?:na1|na2|na3|eu1)`` strict region alternation matches
    #     HubSpot's documented data-residency regions (US East = na1,
    #     US Central = na2, US West = na3, Germany = eu1). Future
    #     regions (e.g. ap1 for Asia-Pacific, au1 for Australia) would
    #     require an additive update — the strict alternation prevents
    #     false positives on placeholder values like ``pat-foo-...``
    #     while accepting every legitimate token currently issued.
    #   * ``[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}``
    #     strict UUID body (RFC 4122 canonical 8-4-4-4-12 hex form,
    #     case-insensitive per RFC 4122 §3 ABNF — HubSpot issues
    #     lowercase but accepts either case on input).
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xpat-na1-...`` / ``...0``
    #     tails).
    (
        re.compile(
            r"(?<![A-Za-z0-9])pat-(?:na1|na2|na3|eu1)-"
            r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-"
            r"[a-fA-F0-9]{4}-[a-fA-F0-9]{12}(?![A-Za-z0-9])"
        ),
        "HubSpot Private App Token gefunden",
    ),
    # PlanetScale Database Token (``pscale_(?:oauth|tkn|pw)_<43 chars>``).
    # The canonical PlanetScale credential format spanning three tiers
    # with DISTINCT revocation flows:
    #   * ``pscale_oauth_<body>`` — OAuth Client Secret. Issued via
    #     app.planetscale.com/<org>/settings/oauth-apps. Mint user-
    #     delegated OAuth access tokens against the PlanetScale API.
    #     Multi-user pivot (the OAuth flow can grant tokens for ANY
    #     PlanetScale user who has authorized the app — cross-account
    #     amplifier).
    #   * ``pscale_tkn_<body>`` — Service Token / Personal Access Token.
    #     Issued via app.planetscale.com/<org>/settings/service-tokens.
    #     Full PlanetScale API access scoped per the token's configured
    #     permissions (typically ``connect_production_branch``,
    #     ``manage_branches``, ``manage_deploy_requests``,
    #     ``read_organization``). Modify production schemas, exfiltrate
    #     every branch's DB password, trigger arbitrary deploy requests,
    #     delete branches.
    #   * ``pscale_pw_<body>`` — Database branch password. **HIGHEST
    #     data-plane blast radius:** direct MySQL-wire-protocol access
    #     to the database branch (read every table, write every table
    #     — full customer data exfiltration / ransomware-style overwrite
    #     primitive). Issued via app.planetscale.com/<org>/<db>/<branch>/
    #     passwords.
    # The body (43-char ``[A-Za-z0-9_-]`` base64url-ish alphabet) lies
    # ENTIRELY inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — pre-fix the entropy regex matched the full
    # ``pscale_<tier>_<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the PlanetScale-specific issuer
    # attribution PLUS the credential-tier disambiguation that anchors
    # the operator's revocation playbook (three distinct revocation
    # panels — OAuth Apps, Service Tokens, Branch Passwords — require
    # tier identification from the prefix).
    #
    # Real-world emission patterns: ``.env`` files (``PLANETSCALE_TOKEN=
    # pscale_tkn_...``, ``DATABASE_URL=mysql://<user>:pscale_pw_...@<host>/
    # <db>``), CI/CD pipeline debug logs (Terraform
    # ``planetscale_database`` resource echoing the token in plan
    # output), notebook outputs hardcoding the PlanetScale Python client
    # constructor, ``pscale`` CLI ``--service-token`` flag in CI YAML
    # files committed to source.
    #
    # Structural anchors:
    #   * ``pscale_`` literal prefix (unambiguous; no other major issuer
    #     uses this prefix).
    #   * ``(?:oauth|tkn|pw)`` strict tier alternation matches the
    #     three documented PlanetScale credential tiers. The strict
    #     alternation prevents false positives on placeholder values
    #     like ``pscale_foo_...`` while preserving per-tier IR triage.
    #   * ``[A-Za-z0-9_-]{43}`` strict 43-char base64url body matches
    #     PlanetScale's documented canonical format. The strict length
    #     rejects accidental fragments while the boundary anchors
    #     prevent extension into longer adjacent identifiers.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xpscale_tkn_...`` / ``...G``
    #     tails).
    (
        re.compile(
            r"(?<![A-Za-z0-9])pscale_(?:oauth|tkn|pw)_[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])"
        ),
        "PlanetScale Database Token gefunden",
    ),
    # Heroku Platform API Token (``HRKU-<base64url 36+ body>``).
    # The canonical Heroku Platform API authorization token format issued
    # post-March 2023 in response to the heroku.com OAuth incident
    # (https://status.heroku.com/incidents/2413). Issued via the Heroku
    # CLI (``heroku authorizations:create``) and the Heroku Dashboard at
    # dashboard.heroku.com/account/applications. Used for the Heroku
    # Platform API (``api.heroku.com/apps/...``,
    # ``api.heroku.com/account/...``, ``api.heroku.com/teams/...``)
    # for full app / dyno / config-var / Heroku Postgres / Heroku Redis
    # control-plane access. The body alphabet (``[A-Za-z0-9_-]``) lies
    # ENTIRELY inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — pre-fix the entropy regex matched the full
    # ``HRKU-<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the Heroku-specific issuer
    # attribution that anchors the per-account revocation flow.
    #
    # Threat model (HIGH blast radius — full PaaS control plane plus
    # adjacent data-plane access via add-ons): a leaked ``HRKU-`` grants
    # the issuing user / authorization's full Heroku Platform API
    # scope. Read access = enumerate every app the user has access to
    # (across personal account and every Heroku Team they collaborate
    # on), dump every app's config vars (which routinely embed further
    # credentials — ``DATABASE_URL`` for Heroku Postgres with the
    # PostgreSQL connection string including the password,
    # ``REDIS_URL`` for Heroku Redis with the auth token,
    # ``SENDGRID_API_KEY`` / ``STRIPE_SECRET_KEY`` etc. for every
    # third-party add-on the app uses — the canonical "one credential
    # leak cascades to many" amplifier). Write access = arbitrary code
    # execution via ``heroku run`` against any dyno (canonical "shell
    # on the production server" primitive), modify app config vars
    # (overwrite ``DATABASE_URL`` to redirect every app instance to an
    # attacker-controlled DB for credential interception), release
    # new app code via ``heroku releases:rollback`` or new slug uploads
    # (supply-chain compromise of the production app), scale up / down
    # dynos (DoS or cost-amplification attack).
    #
    # Real-world emission patterns: ``.env`` files
    # (``HEROKU_API_KEY=HRKU-...``), ``~/.netrc`` files committed by
    # accident, CI/CD pipeline debug logs (``heroku-cli`` debug output
    # echoing the token in plan output), GitHub Actions secrets dumped
    # to logs by a misconfigured action, ``Procfile`` / ``app.json``
    # examples in README files hardcoding the token. Revocation flow
    # lives at the Heroku Dashboard > Account Settings > Applications
    # > "Revoke" (per-authorization) or via the CLI ``heroku
    # authorizations:revoke <id>`` — distinct per account, distinct
    # from every other PaaS-vendor rotation flow (Render via the
    # dashboard ``Settings > API Keys``, Vercel via the dashboard
    # ``Settings > Tokens``, Fly.io via ``fly tokens revoke``).
    #
    # Structural anchors:
    #   * ``HRKU-`` literal prefix (Heroku's canonical post-2023
    #     prefix; no other major issuer uses this prefix). Case-
    #     sensitive matches the documented uppercase convention.
    #   * ``[A-Za-z0-9_\-]{36,}`` body alphabet covers BOTH the UUID-
    #     shape body (32 hex + 4 dashes = 36 chars) AND the base64url-
    #     shape body (40+ chars). The 36-char floor rejects accidental
    #     fragments while accepting every documented Heroku canonical
    #     token shape.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``XHRKU-...`` / ``...G`` tails).
    (
        re.compile(r"(?<![A-Za-z0-9])HRKU-[A-Za-z0-9_\-]{36,}(?![A-Za-z0-9])"),
        "Heroku Platform API Token gefunden",
    ),
    # Docker Hub Personal Access Token (``dckr_pat_<base64url 27+ body>``).
    # The canonical Docker Hub PAT format used for Docker registry
    # authentication (``docker login`` with the PAT as the password).
    # Issued via the Docker Hub UI at hub.docker.com/settings/security
    # for full user-scoped registry access (push / pull / delete
    # repositories the user owns, list every private repository under
    # the user's namespace). The body alphabet (``[A-Za-z0-9_-]``)
    # lies ENTIRELY inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — pre-fix the entropy regex
    # matched the full ``dckr_pat_<body>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the
    # Docker-Hub-specific issuer attribution that anchors the per-user
    # revocation flow.
    #
    # Threat model (HIGH blast radius — supply-chain compromise
    # primitive): a leaked ``dckr_pat_`` grants the issuing user's
    # full Docker Hub scope per the token's configured permissions.
    # Read access = pull every private image in the user's namespace
    # (potentially containing baked-in credentials, proprietary source
    # code in container layers, internal-only infrastructure topology
    # encoded in image labels). Write access = push backdoored images
    # to ANY repository under the user's namespace under any tag — the
    # canonical "supply-chain compromise" primitive. Every downstream
    # consumer pulling ``user/image:latest`` (CI/CD pipelines,
    # Kubernetes deployments using ``imagePullPolicy: Always``,
    # ``docker-compose`` setups with no pinned digest) pulls the
    # backdoored image. The blast-radius amplifier is the cascade:
    # Docker Hub is a top-3 public registry and base images frequently
    # get reused across many projects, so a compromised base image
    # with millions of weekly pulls cascades to every downstream
    # consumer.
    #
    # Real-world emission patterns: ``.env`` files
    # (``DOCKER_HUB_TOKEN=dckr_pat_...``), CI/CD pipeline YAML
    # (``docker login -u $USER -p $DOCKER_HUB_TOKEN`` echoed in debug
    # logs when the pipeline runs with ``set -x``),
    # ``~/.docker/config.json`` files committed by mistake (the
    # ``auths`` block embeds the base64-encoded ``user:dckr_pat_<body>``
    # string), GitHub Actions secrets dumped to logs by a misconfigured
    # action, ``docker buildx`` debug output echoing the token in the
    # registry-login phase, notebook outputs running ``docker push``
    # with the token in plain text. Revocation flow lives at the Docker
    # Hub UI > Account Settings > Security > Access Tokens > "Delete"
    # — distinct per user, distinct from every other container-registry
    # vendor's revocation flow (GitHub Container Registry uses GitHub
    # PATs with ``write:packages`` scope; AWS ECR uses IAM credentials;
    # GitLab Container Registry uses GitLab PATs).
    #
    # Structural anchors:
    #   * ``dckr_pat_`` literal prefix (Docker Hub's canonical PAT
    #     prefix; no other major issuer uses this prefix). The trailing
    #     ``_`` after ``pat`` is the documented separator before the
    #     body.
    #   * ``[A-Za-z0-9_\-]{27,}`` body alphabet covers Docker Hub's
    #     documented base64url-ish body shape. The 27-char floor matches
    #     the minimum documented PAT length while accepting future
    #     canonical-length variations.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xdckr_pat_...`` / ``...G``
    #     tails). The lookbehind specifically rejects accidentally
    #     prefixed shapes that would otherwise extend the literal
    #     ``dckr_pat_`` identifier into a longer mid-word match.
    (
        re.compile(r"(?<![A-Za-z0-9])dckr_pat_[A-Za-z0-9_\-]{27,}(?![A-Za-z0-9])"),
        "Docker Hub Personal Access Token gefunden",
    ),
]
