"""URL validation: unsafe characters, TLD/domain blocklists, public feed URL pin.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections.abc import Container
from urllib.parse import ParseResult, urlparse

from .ip_guard import _resolve_hostname_safe, is_ip_safe
from .limits import MAX_URL_LENGTH

# Block control characters, ASCII whitespace, and structural URL-injection
# characters in URLs.
#
# The character class union covers four orthogonal threat classes — each
# previously closed by an independent round, now consolidated here so the
# URL validation boundary mirrors the canonical log-sanitiser
# ``_INVISIBLE_DANGEROUS_RE`` set:
#
# 1. ASCII whitespace (``\s`` — TAB / LF / CR / SPACE plus the Unicode
#    LINE SEPARATOR U+2028 and PARAGRAPH SEPARATOR U+2029), ASCII
#    C0 controls + DEL (``\x00-\x1f\x7f``), and the **8-bit C1
#    controls** (``\x80-\x9f``). Original payload-shape motivation:
#    log injection via embedded newlines plus the ECMA-48 8-bit
#    terminal-escape primitives (``\x9b`` CSI, ``\x9d`` OSC, ``\x90``
#    DCS …) that survive the 7-bit ``_ANSI_ESCAPE_RE`` defence.
# 2. Structural URL-injection characters (``< > " \ ^ ` { | }``).
#    Original motivation: XSS / template-injection / shell-injection
#    when a URL is interpolated into a downstream sink without escaping.
# 3. **BiDi format controls** (U+061C ALM, U+202A-U+202E
#    LRE/RLE/PDF/LRO/**RLO**, U+2066-U+2069 LRI/RLI/FSI/PDI). These
#    are the canonical CVE-2021-42574 "Trojan Source" primitives — the
#    most impactful is RLO (U+202E), which inverts the visual rendering
#    of subsequent text. A planted feed-item ``link`` like
#    ``https://safe.example.com<RLO>/path/evil.exe`` is rendered with
#    the post-RLO segment reversed in any Unicode-aware feed reader,
#    so the URL the user *sees* differs from the URL the browser
#    actually fetches when clicked. Phishing primitive in any public
#    artefact (RSS feed, sitemap, GitHub Issue body, error logs) that
#    interpolates the URL.
# 4. **Zero-width characters** (U+200B-U+200F ZWSP/ZWNJ/ZWJ +
#    LRM/RLM, U+FEFF BOM). These are visually invisible but cause
#    cache-key collisions, equality-check disagreements, and the same
#    Trojan-Source class display-confusion as the BiDi format controls
#    (LRM/RLM are full BiDi primitives despite being zero-width).
#
# The 2026-05-09 "BiDi-Mark Drift Round 3" audit entry explicitly
# named this regex as a sibling drift candidate
# that the Round 3 PR did not close — it widened
# ``stations_validation._UNSAFE_CHARS_RE`` to the canonical
# ``_INVISIBLE_DANGEROUS_RE`` set but deferred the URL boundary. This
# entry closes that drift; the inventory test
# ``test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set``
# in ``tests/test_sentinel_http_url_chars_bidi_gap.py`` pins the
# invariant programmatically so a future widening of
# ``_INVISIBLE_DANGEROUS_RE`` (e.g. a Unicode 16 BiDi format control)
# fails the test until the URL validator is widened too.
#
# 2026-05-10 "8-bit C1 / DEL Drift": ``_INVISIBLE_DANGEROUS_RE`` was
# widened to ``\x7f-\x9f`` (DEL + 32 C1 controls) so the 8-bit
# terminal-escape primitives (``\x9b`` CSI, ``\x9d`` OSC, ``\x90``
# DCS, ``\x9e`` PM, ``\x9f`` APC) cannot bypass the
# ``strip_control_chars=False`` sibling sinks. The URL validator is
# widened in the same PR to mirror the new canonical floor — a
# planted feed/redirect URL carrying ``\x9b...m`` would otherwise
# slip past the validator and trigger SGR colour command interpretation
# in any 8-bit-C1-honouring terminal that renders the URL.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` to
# include the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). A planted feed-item ``link`` with
# tag-character path segments (visually identical to the legitimate
# URL but different bytes for cache-key / equality / GUID-collision
# shapes, and a prompt-injection smuggling primitive against any
# LLM-driven downstream service) would otherwise pass the validator
# unmodified and reach <link> of docs/feed.xml.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Pre-fix a planted feed-item ``link`` carrying any of these zero-
# width Format primitives in a path segment would pass the validator
# unmodified - the bytes look identical to the legitimate URL but
# carry distinct cache-key / GUID-collision shapes (and a prompt-
# injection smuggling primitive against any LLM-driven downstream
# service that ingests the published RSS feed). The U+2060..U+2069
# range folds in the existing BiDi-isolate band; reserved U+2065 has
# no defined meaning so the additive strip is safe.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# _INVISIBLE_DANGEROUS_RE union to cover the remaining 13 Unicode
# Cf-class bands (44 code points): U+00AD SOFT HYPHEN, U+0600..U+0605
# Arabic prefix marks, U+06DD, U+070F, U+0890..U+0891, U+08E2,
# U+206A..U+206F deprecated BiDi controls (folds the existing
# U+2060..U+2069 band into U+2060..U+206F), U+FFF9..U+FFFB INTERLINEAR
# ANNOTATION, U+110BD/U+110CD KAITHI, U+13430..U+13438 EGYPTIAN
# HIEROGLYPH, U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and U+1D173..U+1D17A
# MUSICAL SYMBOL formatting. Pre-fix a planted feed-item ``link``
# carrying SOFT HYPHEN renders zero-width unconditionally and lands
# inside <link> of docs/feed.xml - the most impactful of the 13 bands
# because SOFT HYPHEN has been used in real-world IDN homograph
# (CVE-2018-19165) and npm package-name spoofing (CVE-2021-43616)
# attacks since 2018.
_UNSAFE_URL_CHARS = re.compile(
    r"[\s\x00-\x1f\x7f-\x9f<>\"\\^`{|}"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u202a-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)


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


def _rebuild_netloc(normalized_hostname: str, port: int | None) -> str:
    """Reassemble a netloc from an NFKC-normalized hostname.

    Restores IPv6 literal brackets (``urlparse(...).hostname`` strips them) and a
    non-default explicit port. Extracted from :func:`validate_http_url` so that
    function stays at/below its C901 complexity baseline.
    """
    final_hostname = f"[{normalized_hostname}]" if ":" in normalized_hostname else normalized_hostname
    if port is not None:
        return f"{final_hostname}:{port}"
    return final_hostname


def _has_embedded_credentials(parsed: ParseResult) -> bool:
    """True if the URL carries userinfo (``user[:pass]@host``).

    Used both before AND after NFKC hostname normalization. NFKC can map
    fullwidth / compatibility characters to an ASCII ``@`` (e.g. U+FF20
    FULLWIDTH COMMERCIAL AT -> ``@``); because ``urlparse`` only splits
    userinfo at an ASCII ``@``, such a character survives the pre-normalization
    check as part of the hostname and only becomes a real ``@`` after
    normalization, re-partitioning the authority and re-introducing userinfo.
    Extracted so :func:`validate_http_url` stays at/below its C901 baseline.
    """
    return bool(parsed.username or parsed.password)


def validate_http_url(
    url: str | None, check_dns: bool = True, allowed_ports: Container[int] = (80, 443)
) -> str | None:
    """Ensure the given URL is valid and uses http or https.

    Returns the URL (stripped) if valid, or ``None`` if invalid/empty/wrong scheme.
    Also rejects URLs that point to localhost or private IP addresses (SSRF protection),
    or contain unsafe control characters/whitespace.

    Applies NFKC normalization to prevent IDNA homograph attacks or blocklist bypasses.
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
        # Check this BEFORE normalization to ensure we don't accidentally strip them when reconstructing netloc.
        if _has_embedded_credentials(parsed):
            return None

        # Security: Normalize only hostname to NFKC to prevent IDNA bypasses and homograph confusion
        # We do NOT normalize the full URL to preserve Base64/Query parameters (Task 2).
        if parsed.hostname:
             # The requirement says: "normalize exclusively the hostname".
             # urlparse.hostname returns lowercased hostname.
             normalized_hostname = unicodedata.normalize("NFKC", parsed.hostname)

             # Reconstruct netloc (IPv6-bracket + explicit-port restoration) via a
             # helper so this function stays at/below its C901 complexity baseline.
             # Avoid replace() which might clobber ports that match the hostname.
             new_netloc = _rebuild_netloc(normalized_hostname, parsed.port)

             # Update parsed object
             parsed = parsed._replace(netloc=new_netloc)

             # Reconstruct candidate with normalized hostname
             candidate = parsed.geturl()

             # Re-run the unsafe-char gate on the reconstructed URL: NFKC can map
             # fullwidth / compatibility forms (e.g. U+FF1C '＜' -> '<',
             # U+FF1E '＞' -> '>', U+FF5C '｜' -> '|', U+FF02 '＂' -> '"') to ASCII
             # structural characters that the pre-normalization check at the top
             # could not see. Without this, a hostname like ``exam＜ple.com``
             # would survive into the returned URL as ``exam<ple.com`` — defeating
             # the invariant ``_UNSAFE_URL_CHARS`` is meant to guarantee for URLs
             # that land in the published feed ``<link>``.
             if _UNSAFE_URL_CHARS.search(candidate):
                 return None

             # NFKC can also map fullwidth / compatibility characters to an
             # ASCII '@' (e.g. U+FF20 FULLWIDTH COMMERCIAL AT -> '@'). Such a
             # character is part of the hostname before normalization (urlparse
             # only splits userinfo at an ASCII '@'), so the pre-normalization
             # credentials check above cannot see it; after normalization it
             # re-partitions the authority and re-introduces userinfo. '@' is
             # intentionally NOT in _UNSAFE_URL_CHARS, so re-validate embedded
             # credentials on the reconstructed parse result to preserve the
             # no-credentials invariant (and avoid host-spoofing display
             # confusion in the published feed <link>).
             if _has_embedded_credentials(parsed):
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

        # hostname is now potentially updated in 'parsed', but 'hostname' var was from old 'parsed'.
        # Update local hostname var
        hostname = parsed.hostname
        if not hostname:
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
            # However, for pure IPv6 literals like "[::1]", hostname is "::1" (brackets stripped by urlparse).
            # But earlier in this function (line ~760), we reconstructed candidate with hostname.
            # If input was "http://[::1]", parsed.hostname is "::1".
            # ipaddress.ip_address("::1") works.
            # ipaddress.ip_address("[::1]") fails.
            # We strip just in case.
            ip_candidate = hostname.strip("[]").split("%")[0]
            ip = ipaddress.ip_address(ip_candidate)
            if not is_ip_safe(ip):
                return None

            # If it is a safe IP, we return the candidate (which preserves brackets for IPv6 if they were there)
            # But wait, earlier we did: candidate = parsed.geturl() after normalization.
            # If it was an IP, normalization (NFKC) usually leaves it alone or normalizes characters.
            # If we return 'candidate' here, it's fine.
            return candidate
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
            #
            # Security (trailing-dot bypass): match against ``check_host``
            # (the dot-stripped form built above for the TLD check), NOT
            # ``lower_host``. Pre-fix the loop used ``lower_host`` with
            # the trailing dot intact, so a host like
            # ``127.0.0.1.nip.io.`` (or ``..`` / ``...``) fell through —
            # ``"...nip.io.".endswith(".nip.io")`` is False — and the URL
            # was returned to a caller that explicitly bypassed live DNS
            # (``check_dns=False``). Reachable via every embedding path
            # that publishes a URL into a committed artefact.
            for unsafe_domain in _UNSAFE_DOMAINS:
                if check_host == unsafe_domain or check_host.endswith("." + unsafe_domain):
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


# Security: pin env-controlled URLs that land in PUBLISHED artefacts (RSS feed
# ``<link>`` / atom hrefs / sitemap ``<loc>``) to GitHub-hosted hosts. Without
# this pin, an env override (intentional misconfig, leaked CI env, compromised
# secret store) would let an attacker substitute the canonical project URL in
# every published item — turning the feed and sitemap into a phishing/SEO
# redirect amplifier for every consumer (subscriber, search-engine crawler).
# ``validate_http_url`` only checks SSRF/DNS-rebinding properties, not host
# identity OR scheme strictness — it accepts both ``http`` and ``https``,
# any path, and any subdomain shape that survives ``urlparse``. The
# allow-list pin therefore must additionally constrain:
#   * the URL scheme to ``https`` (an ``http://`` published feed link is
#     a TLS-strip primitive against subscribers — many RSS readers do not
#     consult HSTS preload lists, so the publisher MUST emit HTTPS-only);
#   * the ``.github.io`` prefix to a single non-empty alphanumeric label
#     (real GitHub Pages targets are ``<owner>.github.io`` —
#     sub-subdomains, empty prefixes, and dash-prefixed labels are not
#     legitimate Pages targets and are rejected at this boundary).
# Allowed: ``github.com`` (canonical repo URL, byte-exact match) and any
# single-label ``<owner>.github.io`` Pages target (the natural GitHub Pages
# target for forks).
_PUBLIC_FEED_URL_TRUSTED_HOSTS = frozenset({"github.com"})
_PUBLIC_FEED_URL_TRUSTED_SUFFIXES = (".github.io",)
# Single-label allow-list contract for the ``.github.io`` suffix prefix:
# starts with [a-z0-9], optional trailing [a-z0-9-] body, max 63 chars
# (RFC-1123 label limit). Pinned tighter than RFC because GitHub usernames
# cannot start with a dash. The hostname is already lowercased by
# ``urlparse``/NFKC normalisation in ``validate_http_url`` before this
# regex is consulted, so ``re.IGNORECASE`` is intentionally NOT used.
_PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def validate_public_feed_url(
    url: str | None, *, check_dns: bool = True
) -> str | None:
    """Validate a URL that will be embedded in a publicly-served artefact.

    Mirrors :func:`validate_http_url` but additionally pins the hostname to
    the GitHub-hosted allowlist so an env override cannot weaponise the feed
    or sitemap as a redirect/phishing primitive. Use ``check_dns=False`` for
    URLs that are only embedded (never fetched), since DNS state at build
    time is irrelevant to whether the URL is a safe target for embedding.

    Pins three sub-vectors that ``validate_http_url`` does not constrain:

    1. **Scheme** — ``https`` only. ``http://`` published links downgrade
       to plaintext on every subscriber's RSS reader (many do not honour
       HSTS preload), exposing the artefact contents to MITM substitution.
    2. **Host identity** — ``github.com`` (byte-exact) or
       ``<owner>.github.io`` (single non-empty alphanumeric label).
       Sub-subdomain shapes (``a.b.github.io``), empty prefixes
       (``.github.io``), and dash-prefixed labels (``-bad.github.io``)
       are not legitimate GitHub Pages targets and are rejected.
    3. **Reuses every check** in ``validate_http_url`` (control chars,
       userinfo, port whitelist, IDNA NFKC normalisation, SSRF / DNS
       rebinding when ``check_dns=True``).
    """

    safe = validate_http_url(url, check_dns=check_dns)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Force HTTPS — ``http://`` is a TLS-strip primitive on subscribers.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host in _PUBLIC_FEED_URL_TRUSTED_HOSTS:
        return safe
    for suffix in _PUBLIC_FEED_URL_TRUSTED_SUFFIXES:
        if host.endswith(suffix):
            prefix = host[: -len(suffix)]
            if _PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE.fullmatch(prefix):
                return safe
    return None
