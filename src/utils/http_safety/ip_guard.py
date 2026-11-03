"""IP safety checks and the hardened DNS resolver.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
import dns.resolver
import dns.exception
from typing import Any, TypeGuard

from .limits import DNS_TIMEOUT

# The logger keeps the name it had in ``src/utils/http.py``.
log = logging.getLogger("src.utils.http")

# Explicitly block Shared Address Space (RFC 6598) 100.64.0.0/10 which is often used for CGNAT/internal carrier networks.
_SHARED_ADDRESS_SPACE = ipaddress.IPv4Network("100.64.0.0/10")

# Explicitly block NAT64 Well-Known Prefix (RFC 6052) 64:ff9b::/96
# These addresses translate to IPv4 and can bypass IPv4 filters if the environment supports NAT64.
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def is_ip_safe(
    ip_addr: Any
) -> TypeGuard[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Check if an IP address is globally reachable and safe."""
    try:
        if isinstance(ip_addr, str):
            # Handle IPv6 scope ids if present
            ip = ipaddress.ip_address(ip_addr.split("%")[0])
        elif isinstance(ip_addr, ipaddress.IPv4Address | ipaddress.IPv6Address):
            ip = ip_addr
        else:
            return False

        # Normalize IPv4-mapped IPv6 addresses (``::ffff:a.b.c.d``) to their
        # embedded IPv4 form so every property and range check below — most
        # importantly the explicit RFC 6598 Shared-Address-Space (CGNAT) block
        # — runs against the real IPv4. On Python 3.11 the mapped form reports
        # ``is_global=True``/``is_private=False`` for the 100.64.0.0/10 range,
        # so without this unwrap ``http://[::ffff:100.64.0.1]/`` or an
        # attacker-controlled AAAA record pointing there would slip past the
        # IPv4-only CGNAT guard. (3.13 already delegates these properties, but
        # the project targets 3.11.)
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped

        # Block unspecified addresses (0.0.0.0, ::)
        if ip.is_unspecified:
            return False

        # Ensure the IP is globally reachable (excludes private, loopback, link-local, reserved)
        # We also explicitly block multicast, as is_global can be True for multicast in some versions/contexts
        if not ip.is_global or ip.is_multicast:
            return False

        # Explicitly reject addresses where is_private, is_loopback, is_link_local evaluates to True
        if ip.is_private or ip.is_loopback or ip.is_link_local:
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
    """Resolve hostname using dnspython with a timeout to prevent thread exhaustion/DoS."""
    results = []

    # Hash hostname for safe logging. CodeQL's clear-text-logging dataflow
    # tracker conservatively treats any string derived from a URL parameter
    # as potentially carrying credentials (the ``user:pass@host`` form).
    # ``hashlib.sha256`` is a recognised barrier where regex whitelists are
    # not (review feedback on PR #1334). Diagnostic value is preserved:
    # the same hostname always produces the same 12-char prefix, so log
    # lines can still be correlated when investigating DNS issues.
    host_log = hashlib.sha256(
        str(hostname).encode("utf-8", "replace")
    ).hexdigest()[:12]

    resolver = dns.resolver.Resolver()
    resolver.timeout = DNS_TIMEOUT
    resolver.lifetime = DNS_TIMEOUT

    try:
        # Resolve A records (IPv4)
        try:
            answers_v4 = resolver.resolve(hostname, "A")
            for rdata in answers_v4:
                # socket.getaddrinfo format: (family, type, proto, canonname, sockaddr)
                # We return enough structure to satisfy the rest of the code: sockaddr is (ip, port)
                results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (rdata.address, 0)))
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            pass  # no A record; the AAAA lookup below may still answer

        # Resolve AAAA records (IPv6)
        try:
            answers_v6 = resolver.resolve(hostname, "AAAA")
            for rdata in answers_v6:
                results.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (rdata.address, 0, 0, 0)))  # type: ignore[arg-type]
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            pass  # no AAAA record; an empty result is logged right below

        if not results:
            log.debug("DNS resolution yielded no A/AAAA records for host:%s", host_log)

    except dns.exception.Timeout:
        log.warning("DNS resolution timed out for host:%s (DoS protection)", host_log)
    except Exception as exc:
        # Log the exception class only — ``str(exc)`` from a DNS resolver
        # error typically embeds the hostname, which would re-introduce
        # the clear-text-logging dataflow that ``host_log`` was meant to
        # break.
        log.warning(
            "Unexpected error during DNS resolution for host:%s: %s",
            host_log,
            type(exc).__name__,
        )

    return results
