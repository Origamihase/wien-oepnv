"""Sentinel drift coverage for the DevOps / CI/CD Pipeline + DigitalOcean
Cloud token tier value-shape log-sanitisation across
``sanitize_log_message`` and the downstream ``_sanitize_exception_msg``
chain.

The 2026-05-17 Slack + AI/ML Inference Platform Log-Sanitisation Drift
Closure round named the **DevOps & CI/CD
Pipeline credential tier** as the next high-impact backlog cluster. The
secret-scanner ``_KNOWN_TOKENS`` already detects committed tokens for the
following 13 issuer-prefixes across 4 vendors, but the companion log-
sanitisation codepath (``src/utils/logging.py:sanitize_log_message``) was
NOT extended in any prior round — bare tokens in plain log text leaked
verbatim into operator log streams plus the public
``docs/feed_health.json`` artefact.

Families covered (each is a sibling-drift closure for one or more
``_KNOWN_TOKENS`` entries):

GitLab CI/CD Pipeline tier (8 prefixes)
---------------------------------------

* **GitLab Runner Authentication Token** — ``glrt-<20 chars from
  [A-Za-z0-9_-]>``. Issued via project / group / instance Runner
  registration in GitLab 15.6+ (post-16.0 default replacing the legacy
  unprefixed registration-token shape). A leak grants the holder the
  ability to register a rogue GitLab Runner against the issuing scope:
  the rogue runner drains the CI job queue, and every CI job (with
  whatever build-secret env vars the pipeline exposes) is delivered to
  attacker-controlled hardware.

* **GitLab Deploy Token** — ``gldt-<20 chars from [A-Za-z0-9_-]>``.
  Issued via project / group Settings > Repository > Deploy tokens for
  registry-pull / repository-read / package-read access. A leak grants
  Deploy-Token-scope pull access (CI image / package registry contents,
  source-code read) — sufficient to exfiltrate every committed secret
  and every package the project produces.

* **GitLab CI Agent Token** — ``glagent-<50+ chars from [A-Za-z0-9_-]>``.
  Issued for the GitLab Agent for Kubernetes (KAS). A leak grants the
  agent's full cluster scope: apply arbitrary manifests to the connected
  Kubernetes cluster, exfiltrate cluster secrets, run privileged pods.

* **GitLab Feature Flag Token** — ``glft-<20 chars from [A-Za-z0-9_-]>``.
  Issued for Unleash-protocol feature-flag clients. Leak grants the
  ability to flip feature flags arbitrarily (enable hidden / abandoned
  features, disable security gates).

* **GitLab Incoming Mail Token** — ``glimt-<25+ chars from
  [A-Za-z0-9_-]>``. Issued for the Service-Desk and incoming-mail
  integration. Leak grants impersonation of arbitrary users when
  injecting issues / merge-request comments via the mail gateway.

* **GitLab CI Build Token** — ``glcbt-<alphanumeric_segment>_<20+ chars
  from [A-Za-z0-9_-]>``. Issued per CI job by the GitLab Runner. Leak
  inside the job duration grants pull/push access to the project's
  Container Registry, Package Registry, and protected CI/CD variables.

* **GitLab SCIM OAuth Access Token** — ``glsoat-<20+ chars from
  [A-Za-z0-9_-]>``. Issued for SCIM-based user provisioning at the
  group / instance level. Leak grants the ability to create / modify /
  delete user accounts across the SCIM-managed scope.

* **GitLab Pipeline Trigger Token** — ``glptt-<40 chars from
  [A-Za-z0-9_-]>``. Issued per project for the Pipeline-Trigger API.
  Leak lets a network adversary trigger arbitrary pipelines with
  attacker-controlled variables — code-execution primitive on the
  project's runners.

CircleCI tier (1 prefix)
------------------------

* **CircleCI Personal API Token** — ``CCIPAT_<32+ chars from
  [A-Za-z0-9_-]>``. Issued via app.circleci.com/settings/user/tokens
  for REST API v2 access. Leak grants the issuing user's full
  CircleCI scope: read every accessible pipeline's build logs (which
  often include masked-but-echoed env vars), trigger pipelines with
  arbitrary parameters, modify project settings.

Buildkite tier (2 prefixes)
---------------------------

* **Buildkite Agent Token** — ``bkat_<40+ alphanumeric body>``. Issued
  via buildkite.com/organizations/<org>/agents for Buildkite agent
  registration. Highest leak surface in the modern CI stack: rogue
  agents can drain the job queue with whatever build-secret env vars
  the pipeline exposes.

* **Buildkite User Access Token** — ``bkua_<40+ alphanumeric body>``.
  Issued via buildkite.com/user/api-access-tokens for user-scoped REST
  API access. Leak grants the issuing user's full Buildkite scope
  across every accessible organisation: read pipeline definitions,
  retry historical builds with attacker-controlled env overrides,
  manage agents.

DigitalOcean cloud tier (2 prefixes)
------------------------------------

* **DigitalOcean Personal Access Token** — ``dop_v1_<64 lowercase hex>``.
  Issued via cloud.digitalocean.com/account/api/tokens for full account
  API access. Leak grants the issuer's full Droplet/Spaces/Database/
  Kubernetes cluster scope across every project in the account.

* **DigitalOcean OAuth Refresh Token** — ``doo_v1_<64 lowercase hex>``.
  Issued during the OAuth-app authorisation flow. Mints fresh
  ``dop_v1_`` access tokens until the refresh token is revoked.

Pre-fix detection gaps (mirror the 2026-05-17 Multi-Vendor / Vault /
GitHub / Slack-AIML rounds' structural analysis):

1. ``sanitize_log_message`` masks credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare
   token in plain log text bypasses every existing pattern. Four
   leak surfaces:

   * **Plain application f-string logs** — ``log.error(f"GitLab API
     401 using {token}: {exc}")``. The bare token shape lands in
     operator log streams verbatim.
   * **Upstream error responses** — ``log.warning(f"Provider error:
     {response.text}")`` where a misconfigured / compromised upstream
     echoes the supplied token back in its error payload.
   * **JSON values without sensitive key names** — ``{"data":
     "glrt-..."}`` or ``{"payload": "bkat_..."}``. The JSON-key
     sensitive-name regex misses keys like ``data`` / ``payload`` /
     ``response_body`` / ``message`` so the token value leaks
     unredacted into the JSON value span.
   * **URL paths / query strings with non-sensitive parameter
     names** — ``GET /api/v1/audit?ref=glrt-...`` or
     ``/internal/audit/CCIPAT_XXX/details``.

2. End-to-end via ``_sanitize_exception_msg``: this is the canonical
   exception-text sanitisation path in ``src/utils/http.py``. It
   extracts HTTP URLs via a pre-regex and falls back to
   ``sanitize_log_message`` for the non-HTTP-URL remainder. Fixing
   the latter closes the exception-text leak sink for every leaked
   token in the families covered here.

**Fix:** append thirteen value-shape mask patterns to
``sanitize_log_message``'s pattern list mirroring the scanner regex
structural anchors exactly. Each pattern preserves the issuer-
specific prefix (``glrt-***``, ``CCIPAT_***``, ``bkat_***``,
``dop_v1_***`` etc.) for incident-response triage because each tier
has a distinct revocation flow:

* GitLab CI/CD — gitlab.com/-/user_settings/applications &
  project / group settings > Access Tokens > Revoke (different sub-
  page per token type).
* CircleCI — app.circleci.com/settings/user/tokens > Revoke; audit
  build logs for unauthorised pipeline triggers.
* Buildkite Agent — buildkite.com/organizations/<org>/agents >
  Revoke; audit agent-connection history for rogue registrations.
* Buildkite User — buildkite.com/user/api-access-tokens > Delete.
* DigitalOcean PAT — cloud.digitalocean.com/account/api/tokens >
  Regenerate; audit billing for unauthorised resource creation.
* DigitalOcean OAuth Refresh — revoke at the OAuth app's authorisation
  list on the user's account page.

Structural anchors mirror the scanner regexes exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myglrt-``, ``fooCCIPAT_`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format
  reject accidental fragments while accepting every real-shape
  token.

Idempotence: masked forms (``glrt-***``, ``CCIPAT_***``,
``bkat_***``, ``dop_v1_***`` etc.) do NOT match any of the new
regexes because ``*`` is not in any body alphabet AND the masked
body length (3 chars) is below every per-family floor
(20/25/27/32/40/50/64).

Marker: SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
