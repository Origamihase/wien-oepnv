
import pytest
from src.utils.http import validate_http_url

def test_validate_http_url_blocks_multicast():
    # 224.0.0.0/4 is multicast
    assert validate_http_url("http://224.0.0.1") is None

def test_validate_http_url_blocks_reserved():
    # 240.0.0.0/4 is reserved
    assert validate_http_url("http://240.0.0.1") is None

def test_validate_http_url_blocks_ipv6_unique_local():
    # fc00::/7 is unique local (private)
    assert validate_http_url("http://[fc00::1]") is None

def test_validate_http_url_blocks_ipv4_mapped_loopback():
    # ::ffff:127.0.0.1 is mapped loopback
    assert validate_http_url("http://[::ffff:127.0.0.1]") is None
