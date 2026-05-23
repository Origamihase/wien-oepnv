"""Sentinel PoC: secret-scanner drift Round 9 — three additional New Relic
issuer prefixes whose canonical format silently bypasses specific
attribution in the post-Round-8 ``_KNOWN_TOKENS`` table.

The 2026-05-10 Round 8 closed Render /
Buildkite User Access Token / Fly.io and re-stated the prevention rule:

> "Every audit round that adds a new issuer MUST also enumerate THREE
> adjacent sub-landscapes the round did NOT cover."

Round 8 enumerated **CI/CD hosting tier continued** (closed Render),
**CI/CD execution tier continued** (closed Buildkite User Access
Token), and **PaaS / edge runtime** (closed Fly.io), and explicitly
named the **observability** sub-landscape as the next-round target:

* **Observability tier** — New Relic (``NRAK-<27 alphanumeric>``
  for User API Keys, ``NRRA-<40 hex>`` for legacy REST API Keys,
  ``NRII-<32 hex>`` for Insights Insert Keys) — UNAMBIGUOUS prefix,
  multi-issuer family covering both modern (NRAK) and legacy (NRRA,
  NRII) credential shapes.
* **Observability tier continued** — Datadog (``<32 hex>``-shape,
  no prefix — bucket-(b)) — deferred for a later round (no canonical
  prefix; permanent bucket-(b)).
* **Observability tier continued** — PagerDuty (``<20 alphanumeric>``,
  no prefix — bucket-(b)) — deferred for a later round.

Closing the three named-and-canonical-prefixed New Relic entries
re-establishes the issuer-attribution coverage the Round 8 closing
checklist guaranteed. Each token's canonical format silently
bypasses specific attribution in ``_KNOWN_TOKENS``:

  1. **New Relic User API Key** (``NRAK-<27 uppercase alphanumeric body>``)
     — issued via one.newrelic.com > API Keys > Create key (User key
     type) for full New Relic platform API access (NerdGraph
     queries, account configuration, alert policy / notification
     channel management, dashboard create/update/delete, user
     management). Total length 32 chars (5-char ``NRAK-`` prefix +
     27-char alphanumeric body). The ``NRAK-`` prefix is unambiguous
     (no other major issuer uses it), and the strict alphanumeric
     body lies entirely inside the entropy fallback's
     ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex would
     match the full ``NRAK-<body>`` span as one generic finding,
     losing the New-Relic-specific attribution that incident-
     response keys off. A leak grants the issuing user's full New
     Relic API scope across every accessible account: query every
     ingested metric / log / trace, modify alert routing
     (suppressing real incidents), exfiltrate dashboard contents
     (which often embed business metric names that reveal product
     telemetry), and create new API keys to maintain persistence.
     The revocation flow lives at one.newrelic.com/api-keys and
     is distinct from any other vendor's, so issuer-specific
     attribution accelerates IR triage.

  2. **New Relic REST API Key** (``NRRA-<40 hex body>``) — the
     legacy REST API key format (deprecated in favour of NRAK
     since 2021 but still issued and accepted for backward
     compatibility). Total length 45 chars (5-char ``NRRA-``
     prefix + 40-char lowercase hex body). The ``NRRA-`` prefix
     is unambiguous, and the strict hex body lies entirely inside
     the entropy fallback's alphabet. A leak grants the issuing
     account's REST API v2 scope: read application performance
     data, browser monitoring data, mobile monitoring data, and
     synthetic monitoring data. The legacy key format has fewer
     scoping controls than NRAK, so leak surfaces are typically
     wider. Distinct revocation flow at one.newrelic.com/api-keys
     under the "REST API Keys" tab.

  3. **New Relic Insights Insert Key** (``NRII-<32 hex body>``) —
     issued via one.newrelic.com > API Keys > Create key (Insights
     Insert key type) for ingestion-only access to the New Relic
     Events / Insights API. Total length 37 chars (5-char ``NRII-``
     prefix + 32-char lowercase hex body). The ``NRII-`` prefix
     is unambiguous, and the strict hex body lies entirely inside
     the entropy fallback's alphabet. A leak grants the issuing
     account's event-ingestion scope: an attacker can spam the
     account's event stream with fabricated metrics, polluting
     dashboards, triggering false-positive alerts, and consuming
     the account's data ingestion quota. Distinct revocation flow
     at one.newrelic.com/api-keys under the "Insights Insert Keys"
     tab.

Each test below pre-fix would have flagged only the generic
high-entropy fallback for the body span after the prefix; post-fix
every token gets the issuer-specific reason that incident-response
playbooks key off.

Closing checklist: Round 9 closes the three named-and-canonical-
prefixed New Relic entries (NRAK, NRRA, NRII). The next round can
pick up:

* **Observability tier continued** — Datadog (``<32 hex>``-shape,
  no prefix — bucket-(b)).
* **Observability tier continued** — PagerDuty (``<20 alphanumeric>``,
  no prefix — bucket-(b)).
* **Observability tier continued** — Honeycomb (``<32 hex>``-shape,
  no prefix — bucket-(b)).
* **Customer engagement** — Twilio Sub-Account Auth Token
  (``<32 hex>``-shape, no prefix — bucket-(b), already partially
  covered by Twilio Account SID ``AC<32 hex>`` but the auth-token
  half is bucket-(b)).
"""

from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# 1. New Relic User API Key (NRAK-)
# ---------------------------------------------------------------------------
#
# Format: ``NRAK-<27 uppercase alphanumeric body>``. Issued via
# one.newrelic.com > API Keys > Create key. Grants full NerdGraph
# platform API access (account configuration, alert routing,
# dashboards, user management). Highest blast radius among New Relic
# credentials.


def test_secret_scanner_detects_new_relic_user_api_key(tmp_path: Path) -> None:
    """New Relic User API Key: ``NRAK-<27 uppercase alphanumeric>``.

    Pre-fix the entropy fallback flagged ``NRAK-<body>`` as a
    generic Hochentropischer Token-String finding without preserving
    the New-Relic-specific issuer attribution that incident-response
    keys off. Post-fix the specific pattern attributes the leak to
    New Relic.
    """
    file_path = tmp_path / "newrelic_config.py"
    # Synthetic NRAK shape: 5-char prefix + 27-char body. Use an
    # all-zeros body so the value is structurally invalid as a real
    # New Relic key (real keys carry a non-trivial entropy / checksum
    # signature) while still matching the regex under test. This
    # keeps the test fixture safely committable past push-time
    # secret-scanning gates that look for realistic key shapes.
    body = "0" * 27
    secret = f"NRAK-{body}"
    assert len(body) == 27
    file_path.write_text(f'NEW_RELIC_USER_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect New Relic User API Key"
    reasons = [f.reason for f in findings]
    assert "New Relic User API Key gefunden" in reasons, (
        f"Expected 'New Relic User API Key gefunden' in reasons; got {reasons}"
    )


# ---------------------------------------------------------------------------
# 2. New Relic REST API Key (NRRA-)
# ---------------------------------------------------------------------------
#
# Format: ``NRRA-<40 lowercase hex body>``. Legacy REST API v2
# credential format. Wider scope than NRAK (fewer scoping controls),
# still issued and accepted for backward compatibility.


def test_secret_scanner_detects_new_relic_rest_api_key(tmp_path: Path) -> None:
    """New Relic REST API Key: ``NRRA-<40 lowercase hex>``."""
    file_path = tmp_path / "newrelic_legacy.py"
    # Synthetic NRRA shape: 5-char prefix + 40-char hex body. All-zeros
    # body keeps the fixture structurally invalid as a real key while
    # matching the regex under test (see ``test_secret_scanner_detects_
    # new_relic_user_api_key`` for the rationale).
    body = "0" * 40
    secret = f"NRRA-{body}"
    assert len(body) == 40
    file_path.write_text(f'NEW_RELIC_REST_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect New Relic REST API Key"
    reasons = [f.reason for f in findings]
    assert "New Relic REST API Key gefunden" in reasons, (
        f"Expected 'New Relic REST API Key gefunden' in reasons; got {reasons}"
    )


# ---------------------------------------------------------------------------
# 3. New Relic Insights Insert Key (NRII-)
# ---------------------------------------------------------------------------
#
# Format: ``NRII-<32 lowercase hex body>``. Ingestion-only access to
# the New Relic Events / Insights API. A leak lets an attacker
# pollute the account's event stream with fabricated metrics,
# triggering false-positive alerts and consuming ingestion quota.


def test_secret_scanner_detects_new_relic_insights_insert_key(
    tmp_path: Path,
) -> None:
    """New Relic Insights Insert Key: ``NRII-<32 lowercase hex>``."""
    file_path = tmp_path / "newrelic_insights.py"
    # Synthetic NRII shape: 5-char prefix + 32-char hex body. All-zeros
    # body keeps the fixture structurally invalid as a real key while
    # matching the regex under test (see ``test_secret_scanner_detects_
    # new_relic_user_api_key`` for the rationale).
    body = "0" * 32
    secret = f"NRII-{body}"
    assert len(body) == 32
    file_path.write_text(f'NEW_RELIC_INSIGHTS_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect New Relic Insights Insert Key"
    reasons = [f.reason for f in findings]
    assert "New Relic Insights Insert Key gefunden" in reasons, (
        f"Expected 'New Relic Insights Insert Key gefunden' in reasons; got {reasons}"
    )


# ---------------------------------------------------------------------------
# 4. Boundary regression: the new patterns do not collide with neighbouring
#    pattern shapes that already live in ``_KNOWN_TOKENS``.
# ---------------------------------------------------------------------------


def test_new_relic_patterns_do_not_collide_with_existing_tokens(
    tmp_path: Path,
) -> None:
    """The NRAK / NRRA / NRII prefixes are uppercase 4-letter codes
    that never appear in any other ``_KNOWN_TOKENS`` entry. Verify
    that real legitimate text containing the substring ``NR`` (e.g.
    German ``Nr.`` for "number") does not trigger a false positive.
    """
    file_path = tmp_path / "boundary.py"
    file_path.write_text(
        "ITEM_NR = 'Nr. 12345'\nNRAK_NOT_KEY = 'NRAK-too short'\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    nr_findings = [
        f
        for f in findings
        if "New Relic" in f.reason
    ]
    assert not nr_findings, (
        f"Boundary regression: New Relic detector should not match short / "
        f"non-key text; got {nr_findings}"
    )
