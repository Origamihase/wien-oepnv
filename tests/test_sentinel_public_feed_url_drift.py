"""Sentinel PoC: ``validate_public_feed_url`` allow-list drift sub-vectors.

The 2026-05-09 Two-Site Drift Closure entry (sentinel.md, top of file)
explicitly flagged ``validate_public_feed_url`` (``src/utils/http.py``)
as the next allow-list-drift candidate after closing
``OSMOverpassConfig`` host-only validation.  The validator delegates to
``validate_http_url`` for SSRF / control-character / port checks and
then layers a host allow-list on top:

  ``_PUBLIC_FEED_URL_TRUSTED_HOSTS = frozenset({"github.com"})``
  ``_PUBLIC_FEED_URL_TRUSTED_SUFFIXES = (".github.io",)``

Three orthogonal sub-vectors slip past that pre-fix shape because
``validate_http_url`` is intentionally lenient on scheme and the suffix
check is byte-exact against ``.github.io`` only — not against the
*shape* of the prefix label:

  (a) **TLS-strip / HTTP downgrade** — ``http://github.com/...`` and
      ``http://example.github.io/...`` both pass.
      ``validate_http_url`` accepts both ``http`` and ``https`` by
      default, so an env override (``FEED_LINK=http://...`` /
      ``PAGES_BASE_URL=http://...`` / ``SITE_BASE_URL=http://...``)
      lands a plaintext URL inside the published RSS feed
      ``<link>``, atom ``self``/``alternate`` hrefs, and
      ``sitemap.xml`` ``<loc>`` elements.  Every subscriber's RSS
      reader fetches the link as-written; a MITM (corporate gateway,
      hostile ISP, public WiFi) downgrades the request and replaces
      the published artefact contents.  Many RSS readers do not
      consult HSTS preload lists, so the upgrade-to-HTTPS that
      browsers get for ``*.github.io`` does not save subscribers.

  (b) **Sub-subdomain wildcard** — ``https://a.b.github.io/...``
      passes because the suffix check is ``host.endswith(".github.io")``
      with no constraint on the prefix.  A real GitHub Pages target
      is always ``<single-owner>.github.io`` (or
      ``<single-owner>.github.io/<repo>``); sub-subdomains are not
      Pages targets.  An attacker who can flip an env override can
      route the published feed link to ``attacker.victim.github.io``
      to lend visual credibility to a phishing destination.

  (c) **Empty / dash-prefixed subdomain** — ``https://.github.io/...``
      and ``https://-bad.github.io/...`` both pass.  The empty-prefix
      shape is RFC-invalid as a hostname but ``urlparse`` accepts it
      and ``"".endswith(".github.io")`` is False but
      ``".github.io".endswith(".github.io")`` is True, so the
      validator accepts a literal ``.github.io`` hostname.  GitHub
      usernames cannot start with a dash (RFC-1123 label rules plus
      GitHub's stricter handle rules), so a leading-dash subdomain is
      not a real Pages target either.

Threat model (what this defence-in-depth gap closes):
  Today the only consumers of ``validate_public_feed_url``
  (``src/feed/config.py``, ``scripts/generate_sitemap.py``) call the
  validator with operator-supplied env overrides
  (``FEED_LINK``, ``PAGES_BASE_URL``, ``SITE_BASE_URL``).  An
  attacker who lands an ``http://``-scheme override or a
  sub-subdomain owner-impersonation override poisons every published
  artefact for every subscriber and search-engine crawler.  The host
  pin is the LAST gate before the URL flows into RSS / sitemap /
  atom output; tightening the pin to **byte-strict scheme + label
  shape** matches the journal-pinned ``OSMOverpassConfig`` strict-
  equality pattern and collapses the cartesian product of
  sub-components into a single decision.

The fix:
  1. Force ``https`` scheme — the validator is for URLs that land in
     publicly-served artefacts, where HTTP is never legitimate.
  2. Require the suffix prefix to be a single non-empty label that
     starts with an alphanumeric character — rejects empty
     subdomains (``.github.io``), leading-dash labels
     (``-bad.github.io``), and sub-subdomain shapes
     (``a.b.github.io``).
"""

from __future__ import annotations

import pytest

from src.utils.http import validate_public_feed_url


# ---------------------------------------------------------------------------
# Regression: every canonical accepted URL still passes the validator.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Origamihase/wien-oepnv",
        "https://github.com/forker/wien-oepnv",
        "https://origamihase.github.io/wien-oepnv",
        "https://forker.github.io/wien-oepnv",
        "https://example.github.io/repo",
        "https://example.github.io/",
        "https://example.github.io",
    ],
)
def test_canonical_https_urls_still_accepted(url: str) -> None:
    """Happy path: HTTPS canonical URLs land verbatim in the validator's
    output.  Pre- and post-fix behaviour MUST match for the legitimate
    deployment surfaces (default ``FEED_LINK`` / ``PAGES_BASE_URL`` /
    ``SITE_BASE_URL`` and every fork variant)."""
    assert validate_public_feed_url(url, check_dns=False) is not None


# ---------------------------------------------------------------------------
# (a) TLS-strip / HTTP downgrade — http:// must be rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/Origamihase/wien-oepnv",
        "http://github.com/forker/wien-oepnv",
        "http://origamihase.github.io/wien-oepnv",
        "http://forker.github.io/wien-oepnv",
        "http://example.github.io/repo",
    ],
)
def test_http_scheme_rejected(url: str) -> None:
    """Pre-fix: every ``http://`` variant of the trusted hosts is
    accepted because ``validate_http_url`` allows both ``http`` and
    ``https`` schemes by default.  Post-fix: rejected because the
    public-feed validator pins the scheme to ``https``.

    Closes the TLS-strip / HTTP-downgrade vector documented at the top
    of this module — every subscriber's RSS reader fetches the link
    as-written, and many do not consult HSTS preload lists.
    """
    assert validate_public_feed_url(url, check_dns=False) is None


# ---------------------------------------------------------------------------
# (b) Sub-subdomain wildcard — multi-label prefixes must be rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://a.b.github.io/wien-oepnv",
        "https://a.b.c.github.io/wien-oepnv",
        "https://attacker.victim.github.io/wien-oepnv",
        "https://nested.deep.example.github.io/repo",
    ],
)
def test_sub_subdomain_rejected(url: str) -> None:
    """Pre-fix: every multi-label prefix on ``.github.io`` is accepted
    because the suffix check is ``host.endswith(".github.io")`` with
    no constraint on the prefix shape.  Post-fix: rejected because the
    public-feed validator requires the prefix to be a single
    non-empty label.

    Closes the sub-subdomain wildcard vector — real GitHub Pages
    targets are always ``<single-owner>.github.io``.
    """
    assert validate_public_feed_url(url, check_dns=False) is None


# ---------------------------------------------------------------------------
# (c) Empty / dash-prefixed subdomain — invalid label shapes must be
#     rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://.github.io/wien-oepnv",  # empty subdomain
        "https://-bad.github.io/wien-oepnv",  # leading dash
        "https://-.github.io/wien-oepnv",  # just a dash
    ],
)
def test_invalid_label_shape_rejected(url: str) -> None:
    """Pre-fix: ``.github.io`` (empty subdomain) and ``-bad.github.io``
    (leading-dash label) both pass because ``urlparse`` accepts the
    RFC-invalid hostnames and ``host.endswith(".github.io")`` is True
    for the empty-prefix case.  Post-fix: rejected because the
    public-feed validator requires the prefix to start with an
    alphanumeric character.

    Closes the malformed-label vector — GitHub usernames cannot start
    with a dash, and an empty subdomain is not a real hostname.
    """
    assert validate_public_feed_url(url, check_dns=False) is None


# ---------------------------------------------------------------------------
# Existing rejection contract — preserved post-fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/feed",
        "https://gihub.com/foo/bar",  # typosquat
        "https://github.com.evil.com/foo",  # suffix attack on github.com
        "https://example.github.io.evil.com/foo",  # suffix attack on github.io
        "https://evil-github.io/foo",  # missing leading dot
        "https://github.io/foo",  # bare apex without subdomain
    ],
)
def test_pre_existing_rejection_contract_preserved(url: str) -> None:
    """Regression: every URL in the pre-existing rejection contract
    (``test_feed_public_url_host_pinning.py``) continues to be
    rejected post-fix.  The new tightening MUST NOT relax any
    pre-existing security check."""
    assert validate_public_feed_url(url, check_dns=False) is None
