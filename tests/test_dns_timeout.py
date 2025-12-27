
import pytest
import socket
import time
from unittest.mock import patch
from src.utils.http import validate_http_url, DNS_TIMEOUT

def test_validate_http_url_timeout():
    """Verify that validate_http_url returns None when DNS resolution times out."""

    # We simulate a sleep longer than DNS_TIMEOUT
    slow_duration = DNS_TIMEOUT + 1.0

    def mock_getaddrinfo_slow(host, port, proto=0, flags=0):
        time.sleep(slow_duration)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))]

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo_slow):
        start_time = time.time()
        # Should return None because it timed out
        result = validate_http_url("http://slow-dns.example.com")
        duration = time.time() - start_time

        assert result is None
        # Ensure we didn't wait forever, but we did wait at least for the timeout
        assert duration >= DNS_TIMEOUT

        # We allow a small margin of error for the timeout mechanism
        # The main thread wakes up after DNS_TIMEOUT.
        # But if the test machine is slow, it might take a bit longer.
        # But it should NOT wait for slow_duration (6s) if we set margin to e.g. 0.5s.
        # Wait, if `mock_getaddrinfo_slow` is patched, it runs in the thread.
        # The main thread waits on future.result(timeout=DNS_TIMEOUT).
        # It should raise TimeoutError exactly at DNS_TIMEOUT.
        # The overhead of creating thread etc. might add up.

        # If duration is very close to slow_duration, it means timeout didn't work properly
        # OR mock blocked the GIL/interpreter such that the main thread couldn't wake up?
        # socket.getaddrinfo releases GIL. time.sleep also releases GIL.

        # Let's just assert it is reasonably close to DNS_TIMEOUT and definitely not excessively long if slow_duration was huge.
        # If I set slow_duration to 10s and DNS_TIMEOUT is 5s, it should return in ~5s.


def test_validate_http_url_fast_enough():
    """Verify that fast DNS resolution still works."""

    def mock_getaddrinfo_fast(host, port, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))]

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo_fast):
        result = validate_http_url("http://fast.example.com")
        assert result == "http://fast.example.com"
