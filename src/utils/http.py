"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import time
import types
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, Container, MutableMapping
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_DEFAULT_RETRY_OPTIONS: dict[str, Any] = {
    "total": 4,
    "backoff_factor": 0.6,
    "status_forcelist": (429, 500, 502, 503, 504),
    "allowed_methods": ("GET",),
}

# Default timeout in seconds if none is provided
DEFAULT_TIMEOUT = 20

# DNS resolution timeout in seconds
DNS_TIMEOUT = 5.0

# Shared executor for DNS resolution to avoid thread exhaustion
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="DNS_Resolver")

log = logging.getLogger(__name__)

def _normalize_key(key: str) -> str:
    """Normalize key for loose matching (lowercase, no hyphens/underscores)."""
    # Security: Use a stricter normalization (strip everything except a-z0-9)
    # to catch variations like "api.key", "api key", "Client.ID", etc.
    return re.sub(r"[^a-z0-9]", "", key.lower())


# Regex to detect credentials in URLs that might be missed by urlparse (e.g. missing //)
# Matches "scheme:user:pass@host" or "scheme://user:pass@host"
_URL_AUTH_RE = re.compile(
    r"^(?P<scheme>https?|ftp):(?P<slash>//)?(?P<auth>[^/\s]+)@", re.IGNORECASE
)

# Keys in query parameters that should be redacted in logs
# We store them normalized (lowercase, no separators) to catch variations
# like x-api-key, x_api_key, X-Api-Key, etc.
_SENSITIVE_QUERY_KEYS = frozenset({
    "accessid",
    "token",
    "key",
    "apikey",
    "password",
    "secret",
    "passphrase",
    "authorization",
    "auth",
    "clientsecret",
    "clientid",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "code",
    "sig",
    "signature",
    "session",
    "sessionid",
    "cookie",
    "sid",
    "ticket",
    # Additional common sensitive keys
    "jwt",
    "bearertoken",
    "authtoken",
    "jsessionid",
    "phpsessid",
    "aspnetsessionid",
    "cfduid",
    "tenant",
    "tenantid",
    "subscription",
    "subscriptionid",
    "oid",
    "objectid",
    "dsn",
    "otp",
    "glpat",
    "ghp",
    "codechallenge",
    "codeverifier",
    "xapikey",
    "ocpapimsubscriptionkey",
    "subscriptionkey",
    # OAuth 2.0 / OIDC / SAML critical parameters
    "state",
    "nonce",
    "clientassertion",
    "clientassertiontype",
    "samlrequest",
    "samlresponse",
    # Additional sensitive tokens
    "bearer",
    # AWS and other cloud tokens
    "xauthtoken",
    "xamzsecuritytoken",
    "xamzsignature",
    "xamzcredential",
})

# High-risk substrings that trigger redaction even if the key isn't an exact match in _SENSITIVE_QUERY_KEYS.
# Normalized keys containing these substrings will be redacted in error messages.
_SENSITIVE_KEY_SUBSTRINGS = frozenset({
    "token",
    "secret",
    "password",
    "credential",
    "passphrase",
    "apikey",
    "accesskey",
    "privatekey",
    "signature",
    "email",
    "webhook",
    # Additional broad matching
    "session",
    "cookie",
    "clientid",
    "clientsecret",
    "authorization",
})

# Headers that must be stripped on cross-origin redirects or scheme downgrades
_SENSITIVE_HEADERS = frozenset({
    "Authorization",
    "Proxy-Authorization",
    "X-Goog-Api-Key",
    "X-Api-Key",
    "X-Auth-Token",
    "Private-Token",
    "Cookie",
    "Set-Cookie",
    "Ocp-Apim-Subscription-Key",
    "X-Amz-Security-Token",
    "X-Gitlab-Token",
    "X-GitHub-Token",
    "X-Vault-Token",
    "X-Sentry-Token",
    "DD-API-KEY",
    "X-Figma-Token",
    "X-Plex-Token",
    "X-Shopify-Access-Token",
    "X-Slack-Token",
    "X-HubSpot-API-Key",
    "X-Postmark-Server-Token",
    "X-Postmark-Account-Token",
    "X-RapidAPI-Key",
    "X-Service-Token",
    "X-Access-Token",
    "X-CSRF-Token",
    "X-CSRFToken",
    "X-XSRF-TOKEN",
})

# Partial matches for dynamic sensitive header detection (normalized to lowercase)
_SENSITIVE_HEADER_PARTIALS = frozenset({
    "token",
    "key",
    "secret",
    "password",
    "passphrase",
    "credential",
    "signature",
    "session",
    "cookie",
    "auth",
    "access",
    "client",
})


def _sanitize_url_for_error(url: str) -> str:
    """Strip credentials and sensitive query params from URL for safe error logging."""
    try:
        # 0. Pre-sanitize malformed auth (e.g. "https:user:pass@...") that urlparse misses
        # This handles cases where user forgot // or scheme is non-standard
        match = _URL_AUTH_RE.match(url)
        if match:
            # Replace the auth part with ***
            # We reconstruct it carefully to avoid messing up the rest
            url = _URL_AUTH_RE.sub(r"\g<scheme>:\g<slash>***@", url, count=1)

        parsed = urlparse(url)

        # 1. Strip basic auth credentials (if urlparse found them)
        if parsed.username or parsed.password:
            # Reconstruct netloc without auth
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)

        # 2. Redact sensitive query parameters
        if parsed.query:
            query_params = parse_qsl(parsed.query, keep_blank_values=True)
            new_params = []
            for key, value in query_params:
                normalized = _normalize_key(key)
                if normalized in _SENSITIVE_QUERY_KEYS or any(s in normalized for s in _SENSITIVE_KEY_SUBSTRINGS):
                    new_params.append((key, "***"))
                else:
                    new_params.append((key, value))

            new_query = urlencode(new_params)
            parsed = parsed._replace(query=new_query)

        # 3. Redact sensitive fragment parameters (e.g. OIDC implicit flow)
        if parsed.fragment:
            fragment_params = parse_qsl(parsed.fragment, keep_blank_values=True)
            new_fragment_params = []
            any_sensitive_fragment = False

            for key, value in fragment_params:
                normalized = _normalize_key(key)
                if normalized in _SENSITIVE_QUERY_KEYS or any(s in normalized for s in _SENSITIVE_KEY_SUBSTRINGS):
                    new_fragment_params.append((key, "***"))
                    any_sensitive_fragment = True
                else:
                    new_fragment_params.append((key, value))

            if any_sensitive_fragment:
                new_fragment = urlencode(new_fragment_params)
                parsed = parsed._replace(fragment=new_fragment)

        return parsed.geturl()
    except Exception:
        return "invalid_url"


class TimeoutHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that enforces a default timeout."""

    def __init__(self, *args: Any, timeout: int | None = None, **kwargs: Any) -> None:
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


def _check_response_security(response: requests.Response, *args: Any, **kwargs: Any) -> None:
    # Always verify that the response came from a safe IP
    # This protects against DNS Rebinding attacks for both direct requests and redirects
    # Enforces "Verify-Then-Process" order.
    verify_response_ip(response)

    if response.is_redirect:
        next_url = response.headers.get("Location")
        if next_url:
            # Join relative URLs
            full_url = requests.compat.urljoin(response.url, next_url)
            if not validate_http_url(full_url):
                safe_url = _sanitize_url_for_error(full_url)
                raise ValueError(f"Unsafe redirect to: {safe_url}")


def _pin_url_to_ip(url: str) -> tuple[str, str]:
    """
    Resolve hostname to a safe IP and rewrite URL to use it (DNS Pinning).
    Returns (pinned_url, original_hostname).
    """
    # 1. Basic validation (without DNS check to allow us to handle it)
    safe_url = validate_http_url(url, check_dns=False)
    if not safe_url:
        sanitized = _sanitize_url_for_error(url)
        raise ValueError(f"Invalid URL: {sanitized}")

    parsed = urlparse(safe_url)
    hostname = parsed.hostname
    if not hostname:
        sanitized = _sanitize_url_for_error(url)
        raise ValueError(f"No hostname in URL: {sanitized}")

    # 2. Resolve to Safe IP
    ips = _resolve_hostname_safe(hostname)
    target_ip = None
    if ips:
        for _, _, _, _, sockaddr in ips:
            if is_ip_safe(sockaddr[0]):
                target_ip = sockaddr[0]
                break

    if not target_ip:
        sanitized = _sanitize_url_for_error(url)
        raise ValueError(f"No safe IP resolved for {sanitized}")

    # 3. Rewrite URL
    if ":" in target_ip:
        netloc = f"[{target_ip}]"
    else:
        netloc = target_ip

    port = _get_port(parsed)
    if port:
        # Check against scheme default ports
        default_port = 443 if parsed.scheme == "https" else 80
        if port != default_port:
            netloc = f"{netloc}:{port}"

    pinned_url = parsed._replace(netloc=netloc).geturl()
    return pinned_url, hostname


def _strip_sensitive_headers(
    headers: MutableMapping[str, str], original_url: str, new_url: str
) -> None:
    """Remove sensitive headers if the redirect crosses security boundaries."""
    original_parsed = urlparse(original_url)
    redirect_parsed = urlparse(new_url)

    host_changed = original_parsed.hostname != redirect_parsed.hostname
    scheme_downgraded = (
        original_parsed.scheme == "https" and redirect_parsed.scheme != "https"
    )
    port_changed = _get_port(original_parsed) != _get_port(redirect_parsed)

    if host_changed or scheme_downgraded or port_changed:
        for header_name in list(headers.keys()):
            if header_name in _SENSITIVE_HEADERS:
                del headers[header_name]
                continue

            normalized = header_name.lower()
            if any(partial in normalized for partial in _SENSITIVE_HEADER_PARTIALS):
                del headers[header_name]


def _get_port(parsed: Any) -> int | None:
    """Get the port from a parsed URL, handling default ports."""
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def _safe_rebuild_auth(self: requests.Session, prepared_request: requests.PreparedRequest, response: requests.Response) -> None:
    """Override for requests.Session.rebuild_auth to strip sensitive headers on cross-origin redirects."""
    # Call original implementation to handle Authorization header standard logic
    requests.Session.rebuild_auth(self, prepared_request, response)

    headers = prepared_request.headers
    url = prepared_request.url

    if "Location" not in response.headers:
        # Should not happen if rebuild_auth is called correctly by requests on redirect
        return

    original_parsed = urlparse(response.request.url)
    redirect_parsed = urlparse(url)

    # Detect security risks: Hostname change, Scheme Downgrade (HTTPS -> HTTP), or Port change
    host_changed = original_parsed.hostname != redirect_parsed.hostname
    scheme_downgraded = original_parsed.scheme == "https" and redirect_parsed.scheme != "https"
    port_changed = _get_port(original_parsed) != _get_port(redirect_parsed)

    if host_changed or scheme_downgraded or port_changed:
        # Dynamic check for sensitive headers based on name patterns
        # We iterate over a copy of keys to allow modification of the dict during iteration
        for header_name in list(headers.keys()):
            if header_name in _SENSITIVE_HEADERS:
                del headers[header_name]
                continue

            normalized = header_name.lower()
            if any(partial in normalized for partial in _SENSITIVE_HEADER_PARTIALS):
                del headers[header_name]


def session_with_retries(
    user_agent: str, timeout: int = DEFAULT_TIMEOUT, **retry_opts: Any
) -> requests.Session:
    """Return a :class:`requests.Session` pre-configured with retries and a default timeout.

    Args:
        user_agent: User-Agent header that should be sent with every request.
        timeout: Default timeout in seconds for requests (default: 20).
        **retry_opts: Additional keyword arguments forwarded to
            :class:`urllib3.util.retry.Retry`.
    """

    options = {**_DEFAULT_RETRY_OPTIONS, **retry_opts}
    session = requests.Session()

    # Security: Strip sensitive headers on cross-origin redirects
    session.rebuild_auth = types.MethodType(_safe_rebuild_auth, session)  # type: ignore

    # Security: Limit redirects to prevent infinite loops and resource exhaustion (DoS)
    session.max_redirects = 10
    session.hooks["response"].append(_check_response_security)
    retry = Retry(**options)
    adapter = TimeoutHTTPAdapter(max_retries=retry, timeout=timeout)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": user_agent,
    })

    proxies_configured = any(
        k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") for k in os.environ
    )
    if session.trust_env and proxies_configured:
        log.warning(
            "Security: Proxy configuration detected in environment. "
            "DNS Rebinding protection (verify_response_ip) may be bypassed."
        )

    return session


# Block control characters and whitespace in URLs to prevent log injection
# Also block unsafe characters (<, >, ", \, ^, `, {, |, }) to prevent XSS/Injection
_UNSAFE_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"\\^`{|}]")

# Limit URL length to reduce DoS risk from extremely long inputs.
MAX_URL_LENGTH = 2048

# TLDs that are reserved or commonly used for internal networks.
# We block these when DNS checks are skipped to prevent leaking internal names
# or generating invalid links in feeds.
_UNSAFE_TLDS = {
    "local",
    "localhost",
    "test",
    "example",
    "invalid",
    "lan",
    "home",
    "corp",
    "internal",
    "intranet",
    "private",
    "onion",  # Tor Hidden Services
    "i2p",    # Invisible Internet Project
    "arpa",   # Infrastructure TLD
    "kubernetes", # Kubernetes internal DNS
    "localdomain", # Linux/Unix default
    "domain", # Generic internal
    "workgroup", # Windows workgroup
    # Common internal network device names / TLDs
    "router",
    "modem",
    "gateway",
    "wpad",
    "server",
    "priv",
    "mshome",
    # Container / Orchestration internal TLDs
    "svc",
    "cluster",
    "consul",
    # Additional internal/infrastructure TLDs (SSRF protection)
    "backup",
    "prod",
    "stage",
    "staging",
    "sys",
    "printer",
    "kube",
    "openshift",
    "istio",
    "mesh",
    "intra",
}

# Known DNS Rebinding / Wildcard DNS services that map to local IPs.
# We block these domains (and their subdomains) regardless of DNS resolution settings.
_UNSAFE_DOMAINS = frozenset({
    "nip.io",
    "sslip.io",
    "xip.io",
    "xip.name",
    "localtest.me",
    "lvh.me",
    "vcap.me",
    "127.0.0.1.nip.io",
})

# Explicitly block Shared Address Space (RFC 6598) 100.64.0.0/10 which is often used for CGNAT/internal carrier networks.
_SHARED_ADDRESS_SPACE = ipaddress.IPv4Network("100.64.0.0/10")

# Explicitly block NAT64 Well-Known Prefix (RFC 6052) 64:ff9b::/96
# These addresses translate to IPv4 and can bypass IPv4 filters if the environment supports NAT64.
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def is_ip_safe(ip_addr: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is globally reachable and safe."""
    try:
        if isinstance(ip_addr, str):
            # Handle IPv6 scope ids if present
            ip = ipaddress.ip_address(ip_addr.split("%")[0])
        else:
            ip = ip_addr

        # Block unspecified addresses (0.0.0.0, ::)
        if ip.is_unspecified:
            return False

        # Ensure the IP is globally reachable (excludes private, loopback, link-local, reserved)
        # We also explicitly block multicast, as is_global can be True for multicast in some versions/contexts
        if not ip.is_global or ip.is_multicast:
            return False

        # We explicitly block is_site_local (deprecated fec0::/10) because is_global returns True for them in some python versions.
        # This attribute only exists on IPv6Address.
        if getattr(ip, "is_site_local", False):
            return False

        # Explicitly block Shared Address Space (CGNAT) 100.64.0.0/10
        # is_global behavior varies by python version for this range
        if ip.version == 4 and ip in _SHARED_ADDRESS_SPACE:
            return False

        # Explicitly block NAT64 WKP (64:ff9b::/96)
        if ip.version == 6 and ip in _NAT64_PREFIX:
            return False

        return True
    except ValueError:
        return False


def _resolve_hostname_safe(hostname: str) -> list[tuple[Any, ...]]:
    """Resolve hostname with a timeout to prevent DoS."""
    try:
        # Reuse the shared executor instead of creating one per call
        future = _DNS_EXECUTOR.submit(socket.getaddrinfo, hostname, None, proto=socket.IPPROTO_TCP)
        return future.result(timeout=DNS_TIMEOUT)
    except TimeoutError:
        log.warning("DNS resolution timed out for %s (DoS protection)", hostname)
        return []
    except (socket.gaierror, ValueError) as exc:
        log.debug("DNS resolution failed for %s: %s", hostname, exc)
        return []
    except Exception as exc:
        log.warning("Unexpected error during DNS resolution for %s: %s", hostname, exc)
        return []
    # We do not shutdown the shared executor


def validate_http_url(
    url: str | None, check_dns: bool = True, allowed_ports: Container[int] = (80, 443)
) -> str | None:
    """Ensure the given URL is valid and uses http or https.

    Returns the URL (stripped) if valid, or ``None`` if invalid/empty/wrong scheme.
    Also rejects URLs that point to localhost or private IP addresses (SSRF protection),
    or contain unsafe control characters/whitespace.

    Now enforces a port whitelist to prevent scanning of non-standard ports.

    Args:
        url: The URL to validate.
        check_dns: If True (default), resolves the hostname to ensure it exists
                   and doesn't point to a private IP (SSRF protection).
                   If False, only syntax and scheme checks are performed.
        allowed_ports: Container of allowed ports (default: 80, 443).
    """
    if not url:
        return None

    candidate = url.strip()
    if not candidate:
        return None

    # Guard against excessively long URLs (DoS protection).
    if len(candidate) > MAX_URL_LENGTH:
        return None

    # Reject internal whitespace or control characters
    if _UNSAFE_URL_CHARS.search(candidate):
        return None

    try:
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in ("http", "https"):
            return None

        # Disallow embedded credentials to avoid leaking secrets via logs or proxies.
        if parsed.username or parsed.password:
            return None

        hostname = parsed.hostname
        if not hostname:
            return None

        # Validate port
        try:
            port = parsed.port
        except ValueError:
            # Invalid port number (e.g. out of range or non-numeric)
            return None

        if port is None:
            # Default ports are implicit
            if parsed.scheme.lower() == "http":
                port = 80
            elif parsed.scheme.lower() == "https":
                port = 443

        if port not in allowed_ports:
            return None

        # Block localhost (handle trailing dot bypass)
        if hostname.lower().rstrip(".") == "localhost":
            return None

        # Block private IP literals even if DNS check is disabled.
        # This prevents leaking private network structure in generated feeds
        # or bypassing SSRF checks by avoiding DNS resolution steps.
        try:
            # Handle IPv6 brackets and scope IDs if present
            # urlparse.hostname strips brackets for IPv6, so we just handle scope/formatting
            ip_candidate = hostname.strip("[]").split("%")[0]
            ip = ipaddress.ip_address(ip_candidate)
            if not is_ip_safe(ip):
                return None
        except ValueError:
            # Not a standard literal IP address
            lower_host = hostname.lower()

            # Security: Handle trailing dots for FQDNs to prevent TLD check bypass
            # e.g., "foo.local." -> "foo.local"
            check_host = lower_host.rstrip(".")
            labels = check_host.split(".")

            if labels:
                tld = labels[-1]
                # Security Enhancement: Block reserved/internal TLDs unconditionally (SSRF protection)
                if not tld or tld in _UNSAFE_TLDS:
                    return None

            # Security Enhancement: Block known DNS rebinding/wildcard DNS services (e.g. nip.io)
            # This is critical when check_dns=False to prevent bypassing IP checks via public domains
            # that resolve to localhost (e.g. 127.0.0.1.nip.io).
            for unsafe_domain in _UNSAFE_DOMAINS:
                if lower_host == unsafe_domain or lower_host.endswith("." + unsafe_domain):
                    return None

            # Security Enhancement: If DNS resolution is skipped, we must be stricter.
            # We reject hostnames that look like obfuscated IPs (integer/hex) or invalid TLDs.
            if not check_dns:
                # 1. Check if it looks like a Hex IP (e.g., 0x7f000001)
                if lower_host.startswith("0x") and re.fullmatch(r"0x[0-9a-f]+", lower_host):
                    return None

                # 2. Check TLD validity
                # Valid public TLDs must start with a letter (RFC 1123).
                # This catches:
                # - Integer IPs (2130706433 -> TLD "2130706433" starts with digit)
                # - Dotted Quad IPs (127.0.0.1 -> TLD "1" starts with digit)
                # - Short numeric (127.1 -> TLD "1" starts with digit)
                # - Dotted Hex (0x7f.0x1 -> TLD "0x1" starts with digit)
                if labels:
                    tld = labels[-1]
                    if not tld or not tld[0].isalpha():
                        return None

                    # Security Enhancement: Require FQDN (at least one dot) for non-DNS validated hosts
                    # This filters out local hostnames (e.g. "http://myserver", "http://router")
                    # unless they are standard IPs (caught above) or localhost (caught earlier).
                    if len(labels) < 2:
                        return None

        # Resolve hostname to IPs to prevent DNS rebinding/aliasing to private IPs
        # This now includes a timeout mechanism
        if check_dns:
            addr_info = _resolve_hostname_safe(hostname)

            # If resolution yielded no results (timeout or failure), reject the URL
            if not addr_info:
                return None

            for _, _, _, _, sockaddr in addr_info:
                if not is_ip_safe(sockaddr[0]):
                    return None

        return candidate
    except Exception:
        return None


def verify_response_ip(response: requests.Response) -> None:
    """Verify that the response connection was made to a safe IP (DNS Rebinding protection)."""
    try:
        # r.raw.connection is usually a urllib3.connection.HTTPConnection
        # .sock is the underlying socket
        conn = getattr(response.raw, "connection", None)
        sock = getattr(conn, "sock", None)
        if sock:
            peer_info = sock.getpeername()
            peer_ip = peer_info[0]
            if not is_ip_safe(peer_ip):
                raise ValueError(
                    f"Security: Connected to unsafe IP {peer_ip} (DNS Rebinding protection)"
                )
        else:
            # If we cannot find the socket, we cannot verify the IP.
            # Fail securely.
            raise ValueError(
                f"Security: Could not retrieve socket for {response.url} (DNS Rebinding protection)"
            )

    except (AttributeError, OSError, ValueError) as exc:
        # If we cannot verify the IP (e.g. mocks, strange adapters),
        # we fail securely instead of failing open.
        # If is_ip_safe returned False (ValueError raised above), we propagate it.
        if "DNS Rebinding protection" in str(exc):
            raise

        # Robustly get URL or use fallback
        raw_url = getattr(response, "url", "unknown_url")
        url = _sanitize_url_for_error(raw_url)

        log.warning(
            "Security: Could not verify peer IP for %s (Fail Closed): %s", url, exc
        )
        raise ValueError(
            f"Security: Could not verify peer IP for {url} (DNS Rebinding protection)"
        ) from exc


def read_response_safe(
    response: requests.Response,
    max_bytes: int = 10 * 1024 * 1024,
    timeout: float | None = None,
) -> bytes:
    """Read response content safely, enforcing size limits and timeouts.

    Args:
        response: The requests Response object (must be opened with stream=True).
        max_bytes: Maximum allowed size in bytes.
        timeout: Maximum time in seconds allowed for reading the body.

    Raises:
        ValueError: If Content-Length or actual size exceeds max_bytes.
        requests.Timeout: If the read operation exceeds the timeout.
    """
    # Check Content-Length header if present
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError:
            # Ignore malformed Content-Length header; strict check happens in loop
            pass
        else:
            if length > max_bytes:
                raise ValueError(f"Content-Length exceeds {max_bytes} bytes")

    chunks = []
    received = 0
    start_time = time.monotonic()

    for chunk in response.iter_content(chunk_size=8192):
        if timeout is not None and (time.monotonic() - start_time) > timeout:
            raise requests.Timeout(f"Read timed out after {timeout} seconds")

        chunks.append(chunk)
        received += len(chunk)
        if received > max_bytes:
            raise ValueError(f"Response too large (> {max_bytes} bytes)")
    return b"".join(chunks)


def request_safe(
    session: requests.Session,
    url: str,
    method: str = "GET",
    max_bytes: int = 10 * 1024 * 1024,
    timeout: int | None = None,
    allowed_content_types: Container[str] | None = None,
    raise_for_status: bool = True,
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request with DNS pinning and size limits to prevent DoS/SSRF.

    Args:
        session: The requests session to use.
        url: The URL to fetch.
        method: HTTP method (default: "GET").
        max_bytes: Maximum allowed response body size in bytes (default: 10MB).
        timeout: Request timeout in seconds.
        allowed_content_types: Optional list of allowed MIME types.
        raise_for_status: If True, call raise_for_status() on the response (default: True).
        **kwargs: Additional arguments passed to session.request().

    Returns:
        The requests.Response object with content consumed and attached to ._content.

    Raises:
        ValueError: If URL is unsafe, Content-Type is invalid, or body size exceeds max_bytes.
        requests.RequestException: For network errors.
    """
    # Security: Enforce default timeout to prevent Slowloris attacks if caller forgets it
    if timeout is None:
        timeout = DEFAULT_TIMEOUT

    # Security: Disable automatic redirects to prevent DNS Rebinding TOCTOU.
    # We handle redirects manually to pin the DNS for each step.
    # We remove it from kwargs to prevent conflict with explicit argument in session.request.
    kwargs.pop("allow_redirects", None)

    # Ensure Host header is set to original hostname for Virtual Hosting
    if "headers" not in kwargs:
        kwargs["headers"] = {}

    # Security: Ensure redirects are validated by merging our security hook
    # with existing session hooks and any hooks passed by the caller.
    has_hooks = hasattr(session, "hooks")
    if has_hooks:
        request_hooks = session.hooks.copy()
    else:
        request_hooks = {}

    caller_hooks = kwargs.pop("hooks", None)
    if caller_hooks:
        for event, hook in caller_hooks.items():
            existing = request_hooks.get(event, [])
            if not isinstance(existing, list):
                existing = [existing]
            else:
                existing = list(existing)  # Copy

            if not isinstance(hook, list):
                hook = [hook]

            request_hooks[event] = existing + hook

    # Ensure response hook list is prepared and includes our security check
    resp_hooks = request_hooks.get("response", [])
    if not isinstance(resp_hooks, list):
        resp_hooks = [resp_hooks]
    else:
        resp_hooks = list(resp_hooks)

    if _check_response_security not in resp_hooks:
        resp_hooks.append(_check_response_security)
    request_hooks["response"] = resp_hooks

    # Determine max redirects
    max_redirects = 10
    if hasattr(session, "max_redirects"):
        max_redirects = session.max_redirects

    current_url = url
    start_time = time.monotonic()

    # Determine methods that typically support redirects (GET, HEAD)
    # RFC 7231 says 3xx should be followed for safe methods, or 303 See Other.
    # requests follows redirects for all methods if allow_redirects=True, but we handle manually.
    # If method is POST/PUT/DELETE, standard redirects (301, 302) might change method to GET.
    # requests handles this logic inside SessionRedirectMixin.
    # For simplicity and security, we only follow redirects if appropriate,
    # but here we replicate requests' behavior of following redirects.

    # We loop to handle redirects
    for attempt in range(max_redirects + 1):
        # Calculate remaining timeout
        elapsed = time.monotonic() - start_time
        # Security: Enforce a total timeout across redirects, but allow a minimal window (0.1s)
        # to ensure we don't fail strictly on 0s timeouts used in tests with mocks.
        current_timeout = max(0.1, float(timeout) - elapsed)

        # 1. Validate and Pin
        safe_url = validate_http_url(current_url, check_dns=False)
        if not safe_url:
            # Security: avoid echoing potentially sensitive URLs in errors.
            sanitized_url = _sanitize_url_for_error(current_url)
            raise ValueError(f"Unsafe or invalid URL: {sanitized_url}")

        parsed = urlparse(safe_url)
        target_url = safe_url

        if parsed.scheme == "http":
            pinned_url, hostname = _pin_url_to_ip(safe_url)
            kwargs["headers"]["Host"] = hostname
            target_url = pinned_url
        else:
            # HTTPS: Resolve to check safety (fail fast) but don't pin (rely on certs + verify_response_ip)
            ips = _resolve_hostname_safe(parsed.hostname or "")
            if not ips or not any(
                is_ip_safe(sockaddr[0]) for _, _, _, _, sockaddr in ips
            ):
                sanitized_url = _sanitize_url_for_error(current_url)
                raise ValueError(f"No safe IP resolved for {sanitized_url}")

            # If we switched from HTTP (pinned) to HTTPS, or just between domains, clean up Host header
            if "Host" in kwargs["headers"]:
                del kwargs["headers"]["Host"]

        # 2. Make Request
        # We use session.request inside the loop.
        ctx = session.request(
            method,
            target_url,
            stream=True,
            timeout=current_timeout,
            hooks=request_hooks,
            allow_redirects=False,
            **kwargs,
        )

        with ctx as r:
            # Prevent DNS Rebinding: Check the actual connected IP
            verify_response_ip(r)

            # Duck-typing check for mocks that might lack is_redirect
            is_redirect = getattr(r, "is_redirect", False)

            if is_redirect:
                # Handle Redirect
                location = r.headers.get("Location")
                if location:
                    if attempt == max_redirects:
                        raise requests.TooManyRedirects(
                            f"Exceeded {max_redirects} redirects"
                        )

                    # Resolve relative URLs
                    next_url = requests.compat.urljoin(current_url, location)

                    # Strip sensitive headers if needed
                    _strip_sensitive_headers(kwargs["headers"], current_url, next_url)

                    # Update URL and continue loop
                    current_url = next_url

                    # If redirects are followed, standard behavior (like requests) is to switch to GET
                    # for 301/302/303 if original was not HEAD.
                    # 307/308 preserve method.
                    # For simplicity, if we are redirecting, we generally respect the status code implications.
                    # requests implementation details are complex.
                    # However, since we are doing manual redirects for security, we should mimic requests behavior roughly
                    # or just keep using the same method if it's 307/308, and switch to GET for others?
                    # For this implementation, we simply persist the method unless it's a 303 (See Other)
                    # which MUST be GET.
                    if r.status_code == 303 and method != "HEAD":
                        method = "GET"
                        # Drop data/json/files for GET redirect
                        kwargs.pop("data", None)
                        kwargs.pop("json", None)
                        kwargs.pop("files", None)

                    # For 301/302, requests switches to GET if not 307/308
                    if r.status_code in (301, 302) and method == "POST":
                        method = "GET"
                        kwargs.pop("data", None)
                        kwargs.pop("json", None)
                        kwargs.pop("files", None)

                    continue

            # Final Response
            if raise_for_status:
                r.raise_for_status()

            if allowed_content_types is not None:
                content_type_header = r.headers.get("Content-Type", "")
                if not content_type_header:
                    raise ValueError(
                        "Content-Type header missing, but validation required"
                    )
                # Robust parsing
                mime_type = content_type_header.split(";")[0].strip().lower()
                if mime_type not in allowed_content_types:
                    raise ValueError(
                        f"Invalid Content-Type: {mime_type} (expected {allowed_content_types})"
                    )

            # Calculate remaining time for reading body
            read_timeout = max(0.1, float(timeout) - (time.monotonic() - start_time))
            content = read_response_safe(r, max_bytes, timeout=read_timeout)

            # Manually attach content to response object so it's usable after close
            r._content = content
            r._content_consumed = True

            return r

    # Should not be reached due to TooManyRedirects check inside loop,
    # but defensive return.
    raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects")


def fetch_content_safe(
    session: requests.Session,
    url: str,
    max_bytes: int = 10 * 1024 * 1024,
    timeout: int | None = None,
    allowed_content_types: Container[str] | None = None,
    **kwargs: Any,
) -> bytes:
    """Fetch URL content with a size limit to prevent DoS (legacy wrapper)."""
    response = request_safe(
        session,
        url,
        method="GET",
        max_bytes=max_bytes,
        timeout=timeout,
        allowed_content_types=allowed_content_types,
        raise_for_status=True,
        **kwargs,
    )
    return response.content
