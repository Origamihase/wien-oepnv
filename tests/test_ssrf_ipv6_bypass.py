import ipaddress
import pytest
from src.utils.http import is_ip_safe, validate_http_url

def test_nat64_bypass_detected():
    # NAT64 Well-Known Prefix (WKP) 64:ff9b::/96
    # This prefix is used to translate IPv4 addresses to IPv6.
    # If an attacker uses this prefix with a private IPv4 address (e.g. 127.0.0.1),
    # they might bypass IPv4 filters if the system only checks is_global.

    # 127.0.0.1 -> 64:ff9b::7f00:0001
    # 64:ff9b::127.0.0.1 is also valid syntax in some contexts

    unsafe_ips = [
        "64:ff9b::127.0.0.1",
        "64:ff9b::7f00:0001",
        "64:ff9b::c0a8:0001", # 192.168.0.1
        "64:ff9b::0a00:0001", # 10.0.0.1
    ]

    for ip_str in unsafe_ips:
        ip = ipaddress.ip_address(ip_str)
        # Verify it is currently considered global (unsafe behavior)
        # We expect this to be True currently, which confirms the vulnerability
        assert ip.is_global is True, f"{ip_str} should be global by default"

        # We want is_ip_safe to return False (safe behavior)
        # But initially it will return True (vulnerable)
        # So we assert is_ip_safe returns False, expecting it to fail until fixed.
        assert is_ip_safe(ip) is False, f"Vulnerability: {ip_str} was accepted as safe!"

def test_other_translation_prefixes():
    # 6to4 (2002::/16) - Should be blocked by is_global=False
    assert is_ip_safe(ipaddress.ip_address("2002:7f00:0001::")) is False

    # Teredo (2001::/32) - Should be blocked by is_global=False
    assert is_ip_safe(ipaddress.ip_address("2001:0000:7f00:0001::")) is False

def test_ipv4_mapped():
    # IPv4-mapped (::ffff:0:0/96) - Should be blocked
    assert is_ip_safe(ipaddress.ip_address("::ffff:127.0.0.1")) is False
