"""VOR/VAO credential helpers: the base-URL host pin, the access-token
normalisation and the ``requests`` auth hook.

Split off ``src/providers/vor.py`` (700-line rule, 2026-11-02); that module
re-exports every name here and stays the public entry point. Nothing in this
module holds state: the credentials and the base URL live in
``src.providers.vor``.
"""

from __future__ import annotations

import base64
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests.auth import AuthBase

from ..utils.http import validate_http_url

# Security: ``VOR_BASE_URL`` (and the legacy ``VOR_BASE`` alias) is the prefix
# that ``VorAuth`` matches with ``r.url.startswith(self.base_url)`` to decide
# whether to attach the VAO ``accessId`` query parameter and the
# ``Authorization: Bearer/Basic <VOR_ACCESS_ID>`` header. ``validate_http_url``
# only checks SSRF/DNS-rebinding properties, not host identity — so an env
# override such as ``VOR_BASE_URL=https://evil.com/api/`` would (a) point all
# VOR fetches at the attacker, and (b) make ``VorAuth`` happily inject the
# access-ID into every one of those requests (URLs starting with the override
# match by definition). Pin the host to the official VAO endpoint, identical
# in shape to ``_validated_oebb_url`` / ``_validated_wl_base`` for the
# corresponding provider env vars. Forks that need a different upstream must
# update this allowlist deliberately rather than via an env override.
#
# 2026-05-10 (HTTPS-only Provider URL Drift): the validator additionally
# pins the scheme to ``https``. ``validate_http_url`` accepts both ``http``
# and ``https``; without this pin, an env override such as
# ``VOR_BASE_URL=http://routenplaner.verkehrsauskunft.at/api/`` would be
# accepted, ``apply_authentication`` would still attach the VAO access ID
# (today only emits a WARNING but proceeds anyway), and every VAO request
# would carry the long-lived credential over plaintext HTTP — captured by
# any on-path attacker (compromised network, BGP hijack, MITM proxy).
# Mirrors the canonical ``validate_public_feed_url`` HTTPS-only pin
# (``src/utils/http.py``) and the OEBB / WL sibling validators.
_VOR_TRUSTED_HOSTS = frozenset({"routenplaner.verkehrsauskunft.at"})


def _validated_vor_base_url(raw: str) -> str | None:
    # ``check_dns=False``: the host is pinned to a single trusted allow-list
    # value below, so the DNS-rebinding/SSRF resolution adds nothing here. It
    # would, however, run a blocking DNS lookup at module-import time
    # (``refresh_base_configuration`` runs at import) and silently DROP a
    # legitimate override on a transient resolver hiccup. The https-scheme +
    # exact-host pins remain the real checks.
    safe = validate_http_url(raw, check_dns=False)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Security: refuse plaintext HTTP — the VAO base URL carries the
    # access ID on every request; an HTTP scheme is a TLS-strip credential
    # leak. See the regex header above for the full threat model.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in _VOR_TRUSTED_HOSTS:
        return None
    return safe


def _normalise_access_token(raw: str) -> tuple[str, str]:
    token = raw.strip()
    if not token:
        return "", ""

    lower_token = token.lower()

    auth_type_override = os.environ.get('VOR_AUTH_TYPE')
    if auth_type_override:
        auth_type_override = auth_type_override.strip().lower()
        if auth_type_override == "bearer":
            if lower_token.startswith("bearer "):
                normalized = token[7:].strip()
            elif lower_token.startswith("basic "):
                normalized = token[6:].strip()
            else:
                normalized = token
            return normalized, f"Bearer {normalized}"
        elif auth_type_override == "basic":
            if lower_token.startswith("basic "):
                normalized = token[6:].strip()
            elif lower_token.startswith("bearer "):
                normalized = token[7:].strip()
            else:
                normalized = token
            if ":" in normalized:
                encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
                return normalized, f"Basic {encoded}"

            # Check if it's already properly Base64 encoded
            try:
                decoded = base64.b64decode(normalized).decode("utf-8")
                # If it decodes and we can re-encode it to the exact same string, it's valid Base64
                if base64.b64encode(decoded.encode("utf-8")).decode("ascii") == normalized:
                    return normalized, f"Basic {normalized}"
            except Exception: # noqa: S110
                pass  # nosec B110

            # Fallback: forcefully encode it
            encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
            return normalized, f"Basic {encoded}"

    if lower_token.startswith("basic "):
        normalized = token[6:].strip()
        # Heuristic: If it contains a colon, it's likely unencoded user:pass
        if ":" in normalized:
            encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
            return normalized, f"Basic {encoded}"
        return normalized, f"Basic {normalized}"

    if lower_token.startswith("bearer "):
        normalized = token[7:].strip()
        return normalized, f"Bearer {normalized}"

    normalized = token
    if ":" in normalized:
        encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
        header = f"Basic {encoded}"
    else:
        header = f"Bearer {normalized}"
    return normalized, header


class VorAuth(AuthBase):
    """
    Injects VOR access credentials into the request via query parameter,
    only if not already authenticated via header.
    """
    def __init__(self, access_id: str, auth_header: str, base_url: str):
        self.access_id = access_id
        self.auth_header = auth_header
        self.base_url = base_url

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        # Security: Only inject credentials if target is VOR API
        if not r.url or not r.url.startswith(self.base_url):
            return r

        # Inject Authorization header if configured and missing
        if self.auth_header and "Authorization" not in r.headers:
            r.headers["Authorization"] = self.auth_header

        # Inject accessId query param if configured
        if self.access_id:
            # Check if accessId is already present in query params
            try:
                parsed = urlparse(r.url)
                query_params = parse_qsl(parsed.query, keep_blank_values=True)

                if not any(k == "accessId" for k, v in query_params):
                    query_params.append(("accessId", self.access_id))
                    new_query = urlencode(query_params)
                    new_parts = parsed._replace(query=new_query)
                    r.url = urlunparse(new_parts)
            except ValueError:
                pass  # malformed URL: leave it untouched, the request then fails visibly

        return r


__all__ = [
    "VorAuth",
    "_VOR_TRUSTED_HOSTS",
    "_normalise_access_token",
    "_validated_vor_base_url",
]
