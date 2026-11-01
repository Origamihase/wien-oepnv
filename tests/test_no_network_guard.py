"""The suite-wide network guard ``_no_network`` (``tests/conftest.py``).

``_no_real_dns`` resolves every name to the address of a real server, so a
request without a mock used to go there, fail and be retried: two tests made
75 such connection attempts per run, about two and a half minutes of every CI
run (technical debt assessment and review of 2026-11-01). The guard refuses
every connection outside loopback and Unix sockets and fails the test that
opened it, also when the code under test swallows the error.
"""

from __future__ import annotations

import errno
import socket

import pytest

from tests.conftest import STUB_PUBLIC_IP, _is_local_address

pytest_plugins = ["pytester"]


def test_a_public_address_is_refused_at_once(_no_network: list[str]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(OSError) as excinfo:
            sock.connect((STUB_PUBLIC_IP, 443))
    assert excinfo.value.errno == errno.ENETUNREACH
    assert len(_no_network) == 1
    assert STUB_PUBLIC_IP in _no_network[0]
    assert "tests/test_no_network_guard.py" in _no_network[0]
    _no_network.clear()  # the refusal is what this test checks


def test_connect_ex_reports_the_refusal(_no_network: list[str]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        assert sock.connect_ex(("192.0.2.1", 80)) == errno.ENETUNREACH
    assert len(_no_network) == 1
    _no_network.clear()


def test_loopback_stays_reachable(_no_network: list[str]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass
    assert _no_network == []


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("127.0.0.1", True),
        ("127.5.5.5", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("localhost", True),
        (STUB_PUBLIC_IP, False),
        ("10.0.0.1", False),
        ("::ffff:140.82.121.6", False),
        ("2001:db8::1", False),
        ("data.wien.gv.at", False),
    ],
)
def test_only_loopback_counts_as_local(host: str, local: bool) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    address = (host, 443, 0, 0) if family == socket.AF_INET6 else (host, 443)
    assert _is_local_address(family, address) is local


def test_unix_sockets_count_as_local() -> None:
    assert _is_local_address(socket.AF_UNIX, "/tmp/any.sock") is True


def test_the_guard_covers_every_test(request: pytest.FixtureRequest) -> None:
    # This test does not ask for the fixture; autouse brings it anyway.
    assert "_no_network" in request.fixturenames


def test_a_swallowed_refusal_still_fails_the_test(pytester: pytest.Pytester) -> None:
    # Both tests the guard caught on 2026-11-01 reached the network through
    # code that swallows the error; the teardown check is what made them red.
    pytester.makeconftest("from tests.conftest import _no_network  # noqa: F401\n")
    pytester.makepyfile(
        test_inner=f"""
import contextlib
import socket


def test_swallows_the_error():
    with socket.socket() as sock, contextlib.suppress(OSError):
        sock.connect(("{STUB_PUBLIC_IP}", 443))
"""
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        ["*the test opened a network connection*", f"*('{STUB_PUBLIC_IP}', 443) from *"]
    )
