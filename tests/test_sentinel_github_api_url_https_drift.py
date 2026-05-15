"""Sentinel PoC: ``_is_trusted_github_api`` URL allow-list drift — HTTPS not
enforced on the credential-bearing GitHub API submission endpoint.

The 2026-05-10 *HTTPS-only Provider URL Drift* round closed the analogous
shape for the three provider-side validators (``_validated_oebb_url`` /
``_validated_wl_base`` / ``_validated_vor_base_url``) — pinning the scheme
to ``https`` so an ``http://`` env override falls back to the safe default.
The auto-issue submission endpoint
(``src/feed/reporting.py:_is_trusted_github_api``) was left at the looser
``parsed.scheme.lower() not in ("http", "https")`` shape, which accepts
both ``http`` and ``https``.

Threat model
------------

The endpoint validated by ``_is_trusted_github_api`` carries a
``Authorization: Bearer <FEED_GITHUB_TOKEN>`` header on every request
(``submit()`` in the same module attaches the header unconditionally
once the URL is approved). The token is sourced from
``read_secret("FEED_GITHUB_TOKEN")`` / ``read_secret("GITHUB_TOKEN")``
and grants whatever scope the issuing PAT / fine-grained token / App
installation token carries — typically ``issues:write`` plus
``contents:read``, but in CI environments the token is often the
``GITHUB_TOKEN`` injected by GitHub Actions which can be configured
with even broader permissions.

An attacker who controls ``FEED_GITHUB_API_URL`` (or its legacy
``GITHUB_API_URL`` alias) — via intentional CI misconfiguration, a
leaked secrets-store entry, an attacker-crafted ``.env`` file, or a
hostile-PR introduced env block in the Actions workflow — sets the
value to ``http://api.github.com``. Pre-fix behaviour:

1. ``_GithubIssueConfig.from_env`` reads the env var verbatim, strips
   the trailing slash, and stores ``http://api.github.com``.
2. ``_is_trusted_github_api("http://api.github.com")`` accepts the URL
   (the scheme check rejects only non-``http``/non-``https`` schemes).
3. ``submit()`` constructs ``http://api.github.com/repos/owner/name/issues``
   and POSTs the bearer token over plaintext HTTP.
4. Any on-path attacker (compromised network hop, BGP hijack, hostile
   public WiFi, malicious transparent proxy, MITM TLS-strip on the
   client's egress) captures the token verbatim from the cleartext
   ``Authorization`` header.

The same applies to the GitHub Enterprise (GHE) branch: an env override
``FEED_GITHUB_API_URL=http://ghe.corp.local/api/v3`` combined with the
operator's existing ``FEED_GITHUB_ENTERPRISE_HOSTS=ghe.corp.local``
allowlist passes the trust check and leaks the token over plaintext.

Even though GitHub's real API rejects HTTP and immediately closes the
connection or redirects to HTTPS, the bearer token is already on the
wire BEFORE the redirect — the cross-scheme redirect handler in
``request_safe`` strips the header on the redirected request, but the
initial POST has already exposed the credential.

The fix
-------

Mirror the canonical ``validate_public_feed_url`` /
``_validated_*_base_url`` shape: enforce ``parsed.scheme.lower() ==
"https"`` so an ``http://`` env override is rejected at the trust
gate and ``submit()`` aborts without sending the token. The existing
``submit()`` rejection branch already emits a WARNING via the
``sanitize_log_arg``-routed log line; the new HTTPS-only rejection
shares that branch byte-for-byte.

Why HTTPS-only is safe (no false positives)
-------------------------------------------

The GitHub REST API has been HTTPS-only since 2014; ``api.github.com``
has never accepted ``http://`` in production. GitHub Enterprise Server
canonical installations also require HTTPS for any production
deployment (TLS termination at the GHE-managed load balancer). No
legitimate operator configuration uses HTTP for this endpoint, so the
tightening has zero impact on real deployments and only closes the
TLS-strip credential-leak vector.

Inventory invariant
-------------------

The post-fix shape pins ``_is_trusted_github_api`` into the same
``HTTPS-only validator`` family as ``_validated_vor_base_url``,
``_validated_oebb_url``, ``_validated_wl_base``, and
``validate_public_feed_url``. The audit-walker test below names
every member of the family + the new entry and asserts the
HTTPS-only shape; a future fifth validator that accepts ``http://``
fails the test until the canonical shape is restored.
"""

from __future__ import annotations

import logging

import pytest
import responses

from src.feed.reporting import RunReport, _is_trusted_github_api


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_GITHUB_API_HTTPS_DRIFT`` finds the full call-graph at once.
SENTINEL_GITHUB_API_HTTPS_DRIFT = "https-only github api validator drift"


# ---------------------------------------------------------------------------
# (1) ``_is_trusted_github_api`` — HTTPS scheme pin on the public
#     ``api.github.com`` endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com",
        "http://api.github.com/",
        # Uppercase scheme — ``urlparse`` lowercases via NFKC, but the
        # check uses ``.lower()`` defensively.
        "HTTP://api.github.com",
        "Http://api.github.com/",
    ],
)
def test_is_trusted_github_api_rejects_http_public(url: str) -> None:
    """Pre-fix: every ``http://`` variant of api.github.com is accepted
    because the scheme check uses ``not in ("http", "https")``. Post-fix:
    rejected because the validator requires ``https``.

    Closes the credential-leak vector documented at the module docstring
    — the auto-issue submission would otherwise POST the bearer
    ``FEED_GITHUB_TOKEN`` / ``GITHUB_TOKEN`` over plaintext HTTP.
    """
    assert _is_trusted_github_api(url) is False, (
        f"_is_trusted_github_api accepted http URL {url!r} — "
        f"this is a TLS-strip credential leak. "
        f"({SENTINEL_GITHUB_API_HTTPS_DRIFT})"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com",
        "https://api.github.com/",
    ],
)
def test_is_trusted_github_api_accepts_https_public(url: str) -> None:
    """Regression: HTTPS canonical URL must continue to be trusted —
    this is the production happy path that every default
    ``FEED_GITHUB_API_URL`` resolves to."""
    assert _is_trusted_github_api(url) is True


# ---------------------------------------------------------------------------
# (2) ``_is_trusted_github_api`` — HTTPS scheme pin on the operator-
#     declared GHE allowlist branch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,allowlist",
    [
        ("http://github.example.com/api/v3", "github.example.com"),
        ("http://github.example.com/api/v3/", "github.example.com"),
        ("http://ghe.corp.local/api/graphql", "ghe.corp.local"),
        # Case-insensitive scheme — uppercase form also rejected.
        ("HTTP://ghe.corp.local/api/v3", "ghe.corp.local"),
    ],
)
def test_is_trusted_github_api_rejects_http_ghe(
    monkeypatch: pytest.MonkeyPatch, url: str, allowlist: str
) -> None:
    """Pre-fix: ``http://`` GHE URLs whose hostname is in the operator
    allowlist pass the trust check. Post-fix: rejected because the
    validator requires ``https`` regardless of the GHE allowlist.

    Same TLS-strip credential-leak shape as the api.github.com branch:
    the bearer token never reaches the wire over plaintext HTTP
    regardless of operator opt-in for non-public-GitHub hosts.
    """
    monkeypatch.setenv("FEED_GITHUB_ENTERPRISE_HOSTS", allowlist)
    assert _is_trusted_github_api(url) is False, (
        f"_is_trusted_github_api accepted http GHE URL {url!r} "
        f"with allowlist {allowlist!r} — TLS-strip credential leak. "
        f"({SENTINEL_GITHUB_API_HTTPS_DRIFT})"
    )


def test_is_trusted_github_api_accepts_https_ghe_with_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: HTTPS GHE URLs with operator opt-in remain trusted —
    this is the supported Enterprise Server deployment path."""
    monkeypatch.setenv("FEED_GITHUB_ENTERPRISE_HOSTS", "github.example.com")
    assert _is_trusted_github_api("https://github.example.com/api/v3") is True
    assert _is_trusted_github_api("https://github.example.com/api/graphql") is True


# ---------------------------------------------------------------------------
# (3) End-to-end via ``submit()`` — the auto-issue submitter must NOT
#     send the bearer token to an HTTP endpoint regardless of which
#     branch (public api.github.com or GHE allowlist) would otherwise
#     have validated.
# ---------------------------------------------------------------------------


@responses.activate
def test_submit_refuses_when_api_url_is_http_public(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setting ``FEED_GITHUB_API_URL=http://api.github.com`` must NOT
    leak the token. Pre-fix the trust gate accepted the URL and the
    submitter POSTed the bearer token over plaintext HTTP; post-fix the
    gate rejects and ``submit()`` aborts before any network call.
    """
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "ghs_topsecrettoken_DO_NOT_LEAK")
    monkeypatch.setenv("FEED_GITHUB_API_URL", "http://api.github.com")

    # If the guard fails open, the token would be POSTed to the HTTP
    # endpoint. Register a passthrough so any leak is observable as a
    # recorded call.
    responses.post(
        "http://api.github.com/repos/demo/repo/issues",
        json={"html_url": "http://api.github.com/issues/1"},
        status=201,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")
    report.log_results()

    # Critical assertion: NO outbound HTTP call was made — token is
    # contained.
    assert not responses.calls, (
        "Bearer token was leaked over plaintext HTTP to api.github.com "
        f"({SENTINEL_GITHUB_API_HTTPS_DRIFT})"
    )
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "build_feed" and record.levelno == logging.WARNING
    ]
    assert any(
        "kein bekannter GitHub-Endpunkt" in message
        for message in warning_messages
    ), f"Expected guard warning not found in: {warning_messages}"


@responses.activate
def test_submit_refuses_when_api_url_is_http_ghe(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setting ``FEED_GITHUB_API_URL=http://<allowlisted-ghe-host>`` must
    NOT leak the token even when the host is in
    ``FEED_GITHUB_ENTERPRISE_HOSTS``. The HTTPS-only pin applies to
    every branch of the validator.
    """
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "ghs_ghe_topsecret_DO_NOT_LEAK")
    monkeypatch.setenv(
        "FEED_GITHUB_API_URL", "http://github.example.com/api/v3"
    )
    monkeypatch.setenv(
        "FEED_GITHUB_ENTERPRISE_HOSTS", "github.example.com"
    )

    responses.post(
        "http://github.example.com/api/v3/repos/demo/repo/issues",
        json={"html_url": "http://github.example.com/issues/1"},
        status=201,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")
    report.log_results()

    assert not responses.calls, (
        "Bearer token was leaked over plaintext HTTP to a GHE host "
        f"({SENTINEL_GITHUB_API_HTTPS_DRIFT})"
    )
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "build_feed" and record.levelno == logging.WARNING
    ]
    assert any(
        "kein bekannter GitHub-Endpunkt" in message
        for message in warning_messages
    ), f"Expected guard warning not found in: {warning_messages}"


# ---------------------------------------------------------------------------
# (4) Inventory invariant — every URL validator that gates a
#     credential-bearing request carries the HTTPS-only contract.
# ---------------------------------------------------------------------------


def test_github_api_validator_in_https_only_family() -> None:
    """Audit walker: enumerate every URL validator across the project that
    gates a credential-bearing or content-publishing request, and assert
    that the canonical ``http://`` form of its trusted host is rejected.
    Mirrors :func:`tests.test_sentinel_provider_url_https_drift.\
test_provider_url_validators_all_reject_http`
    but adds the GitHub API submitter into the inventory.

    Closes the cross-validator drift: a future fifth validator that
    accepts ``http://`` would fail this test until the canonical shape
    is restored.
    """
    from src.providers.oebb import _validated_oebb_url
    from src.providers.vor import _validated_vor_base_url
    from src.providers.wl_fetch import _validated_wl_base
    from src.utils.http import validate_public_feed_url

    # Each tuple is (validator-callable, canonical http URL it must reject,
    # human-readable label for the assertion message).
    inventory: tuple[tuple[object, str, str], ...] = (
        (
            _validated_vor_base_url,
            "http://routenplaner.verkehrsauskunft.at/",
            "_validated_vor_base_url (VOR access ID)",
        ),
        (
            _validated_oebb_url,
            "http://fahrplan.oebb.at/",
            "_validated_oebb_url (ÖBB feed content)",
        ),
        (
            _validated_wl_base,
            "http://www.wienerlinien.at/ogd_realtime",
            "_validated_wl_base (WL feed content)",
        ),
        (
            validate_public_feed_url,
            "http://github.com",
            "validate_public_feed_url (RSS feed link)",
        ),
        (
            _is_trusted_github_api,
            "http://api.github.com",
            "_is_trusted_github_api (GitHub bearer token)",
        ),
    )

    for validator, http_url, label in inventory:
        result = validator(http_url)  # type: ignore[operator]
        # Trust validators return False for rejection; URL-sanitisers
        # return None. Both compare falsey in this audit-walker contract.
        assert not result, (
            f"{label}: accepted http URL {http_url!r} — "
            f"this is a TLS-strip / credential-leak primitive. "
            f"({SENTINEL_GITHUB_API_HTTPS_DRIFT})"
        )
