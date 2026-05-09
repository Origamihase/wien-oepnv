"""Sentinel PoC: ``OSMOverpassConfig`` host-only endpoint validation.

The 2026-05-08 OSM-First migration shipped ``OSMOverpassConfig`` with a
``__post_init__`` that validates ``endpoint`` only by **hostname**: any URL
whose ``urlparse(url).hostname`` lands in
:data:`_TRUSTED_OVERPASS_HOSTS` is accepted, regardless of scheme,
port, or path. Three orthogonal defence-in-depth gaps fall out of that
shape that the strict ``get_overpass_endpoint()`` allow-list already
closes for the env-driven path:

  (a) **TLS-strip / HTTP downgrade** — ``http://overpass-api.de/api/interpreter``
      passes the host check. ``validate_http_url`` (called inside
      ``request_safe``) accepts ``http://`` by default, so the cron
      pipeline's outbound request would actually go over plaintext —
      a MITM (corporate gateway, public WiFi, hostile ISP) can
      intercept the response and inject a malicious station payload
      that flows verbatim into ``stations.json`` and the published
      feed.
  (b) **Path / endpoint hijack** — ``https://overpass-api.de/api/admin``
      or ``https://overpass-api.de/anything-else`` passes the host
      check. The Overpass operator runs other paths on the same host
      (e.g. status endpoints) and an attacker who can flip a future
      env or config-file consumer of ``OSMOverpassConfig`` could
      redirect the cron pipeline to a different endpoint on the same
      host that the project does not expect.
  (c) **Non-standard port** — ``https://overpass-api.de:8443/api/interpreter``
      passes the host check. ``validate_http_url`` rejects non-default
      ports (allowed_ports defaults to ``(80, 443)``), so the request
      would actually fail at request time. But the *config* is
      accepted; in a future code path that bypasses ``validate_http_url``
      (e.g. a websocket upgrade, raw socket fallback, debug client)
      the port is unconstrained.

Threat model (what this defence-in-depth gap closes):
  Today the only callers of ``OSMOverpassConfig`` resolve the
  endpoint through ``get_overpass_endpoint()`` which IS strict
  (exact-match against ``DEFAULT_OVERPASS_ENDPOINTS``). The host-only
  check therefore appears redundant. But the journal pattern across
  every prior round of the env-cap / allow-list family is exactly:
  *the boundary that was strict on day one drifted into a host-only
  check after some refactor, and a future caller landed on the
  loose internal validator instead of the strict resolver*. Pin
  the strict shape at the structural boundary (``__post_init__``)
  so a future contributor who instantiates ``OSMOverpassConfig``
  from a CLI flag, a config file, a leaked env var, or a unit test
  fixture cannot accidentally re-enable any of (a)-(c) above.

The fix mirrors ``get_overpass_endpoint()``: the endpoint MUST appear
verbatim in :data:`DEFAULT_OVERPASS_ENDPOINTS`. ``urlparse`` is no
longer trusted to do the discrimination — string-equality is the
strongest possible allow-list contract.
"""

from __future__ import annotations

import pytest

from src.places.osm_client import (
    DEFAULT_OVERPASS_ENDPOINTS,
    OSMOverpassConfig,
)


_VALID_USER_AGENT = "wien-oepnv-test/1.0 (contact: test@example.com)"


def test_config_accepts_canonical_https_endpoint() -> None:
    """Regression: every URL listed in ``DEFAULT_OVERPASS_ENDPOINTS`` is
    still accepted verbatim. The strict validator MUST NOT break the
    happy path."""
    for endpoint in DEFAULT_OVERPASS_ENDPOINTS:
        config = OSMOverpassConfig(endpoint=endpoint, user_agent=_VALID_USER_AGENT)
        assert config.endpoint == endpoint


def test_config_rejects_http_downgrade_on_trusted_host() -> None:
    """Pre-fix: ``http://overpass-api.de/api/interpreter`` passes the
    host-only check (urlparse hostname is ``overpass-api.de``, which IS
    on the trusted-host allow-list). Post-fix: rejected because the
    URL is not an exact match against ``DEFAULT_OVERPASS_ENDPOINTS``.

    Closes the TLS-strip / HTTP-downgrade vector documented at the top
    of this module.
    """
    plaintext = DEFAULT_OVERPASS_ENDPOINTS[0].replace("https://", "http://", 1)
    assert plaintext.startswith("http://"), "Sanity check on test fixture"

    with pytest.raises(ValueError):
        OSMOverpassConfig(endpoint=plaintext, user_agent=_VALID_USER_AGENT)


def test_config_rejects_path_hijack_on_trusted_host() -> None:
    """Pre-fix: ``https://overpass-api.de/api/admin`` passes the host
    check because the hostname lands in the allow-list. Post-fix:
    rejected because the URL is not an exact match against
    ``DEFAULT_OVERPASS_ENDPOINTS``.

    Closes the path-hijack vector — an attacker who can flip the
    endpoint URL through a future config-file consumer could
    redirect the cron pipeline to an unrelated path on the same
    host (e.g. an admin endpoint, a debug-status route, a 404
    fingerprint surface).
    """
    canonical = DEFAULT_OVERPASS_ENDPOINTS[0]
    # Use the same host but a different path
    hijacked = canonical.rsplit("/", 1)[0] + "/admin"

    with pytest.raises(ValueError):
        OSMOverpassConfig(endpoint=hijacked, user_agent=_VALID_USER_AGENT)


def test_config_rejects_non_standard_port_on_trusted_host() -> None:
    """Pre-fix: ``https://overpass-api.de:8443/api/interpreter`` passes
    the host check because the hostname lands in the allow-list.
    Post-fix: rejected because the URL is not an exact match against
    ``DEFAULT_OVERPASS_ENDPOINTS``.

    Closes the port-hijack vector — even though
    :func:`validate_http_url` rejects non-standard ports at request
    time, a future code path that bypasses that helper (debug
    client, websocket upgrade, raw urllib3 access) would happily
    use the configured non-standard port.
    """
    canonical = DEFAULT_OVERPASS_ENDPOINTS[0]
    # Inject a non-standard port between the host and the path
    scheme, rest = canonical.split("://", 1)
    host, _, path = rest.partition("/")
    weird = f"{scheme}://{host}:8443/{path}"

    with pytest.raises(ValueError):
        OSMOverpassConfig(endpoint=weird, user_agent=_VALID_USER_AGENT)


def test_config_rejects_trailing_slash_variant() -> None:
    """Defence-in-depth: even a subtle visual variant of a canonical
    URL (extra trailing slash) is rejected. The strict allow-list
    requires byte-exact equality so a future contributor cannot drift
    by adding a trailing slash at one call site and not the others.
    """
    canonical = DEFAULT_OVERPASS_ENDPOINTS[0]
    if canonical.endswith("/"):
        variant = canonical.rstrip("/")
    else:
        variant = canonical + "/"

    with pytest.raises(ValueError):
        OSMOverpassConfig(endpoint=variant, user_agent=_VALID_USER_AGENT)


def test_config_rejects_canonical_url_with_userinfo() -> None:
    """Pre-fix: ``https://user:pass@overpass-api.de/api/interpreter``
    passes the host check (urlparse strips userinfo). Post-fix:
    rejected because the URL is not an exact match against
    ``DEFAULT_OVERPASS_ENDPOINTS``.

    Closes the userinfo-injection vector: even though the actual HTTP
    layer ignores userinfo on most modern hosts, embedding ``user:pass``
    in the URL leaks the credentials into log lines that print the
    full ``self.endpoint`` string and risks downstream consumers
    (e.g. SIEM ingestion) treating the userinfo as authentication
    metadata.
    """
    canonical = DEFAULT_OVERPASS_ENDPOINTS[0]
    scheme, rest = canonical.split("://", 1)
    poisoned = f"{scheme}://attacker:secret@{rest}"

    with pytest.raises(ValueError):
        OSMOverpassConfig(endpoint=poisoned, user_agent=_VALID_USER_AGENT)


def test_config_still_rejects_unrelated_host() -> None:
    """Regression: the host-only baseline is preserved. An entirely
    untrusted host is still rejected with the same error class."""
    with pytest.raises(ValueError):
        OSMOverpassConfig(
            endpoint="https://evil.example/api/interpreter",
            user_agent=_VALID_USER_AGENT,
        )
