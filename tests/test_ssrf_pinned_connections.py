"""The connection classes of :mod:`src.utils.http` open sockets only to vetted IPs.

``SafeDNSHTTPConnection`` and ``SafeDNSHTTPSConnection`` resolve the host
once, skip every address :func:`src.utils.http.is_ip_safe` rejects and open
the socket to the first safe one (DNS rebinding protection).
``PinnedHTTPSConnection`` opens it to the pinned IP and keeps the host name
for SNI and the certificate check.

Until 2026-11-01 the two HTTPS classes ran in the suite only through two
tests that reached the real network by accident, and ``SafeDNSHTTPConnection``
did not run at all. ``_no_network`` in ``tests/conftest.py`` refuses such
connections now, so these tests run the code on purpose and offline:
``socket.create_connection`` is replaced by a recorder that hands back a
stand-in socket.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from src.utils import http as http_utils
from src.utils.http import (
    PinnedHTTPSAdapter,
    PinnedHTTPSConnection,
    SafeDNSAdapter,
    SafeDNSHTTPConnection,
    SafeDNSHTTPSConnection,
)
from tests.conftest import STUB_PUBLIC_IP

OTHER_PUBLIC_IP = "140.82.121.7"


class _StandInSocket:
    """Records the socket options the connection applies."""

    def __init__(self) -> None:
        self.options: list[tuple[Any, ...]] = []

    def setsockopt(self, *args: Any) -> None:
        self.options.append(args)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Every ``socket.create_connection`` call as ``(address, timeout, source_address)``."""

    calls: list[tuple[Any, ...]] = []

    def _create_connection(
        address: tuple[str, int],
        timeout: Any = None,
        source_address: Any = None,
    ) -> _StandInSocket:
        calls.append((address, timeout, source_address))
        return _StandInSocket()

    # ``src.utils.http`` calls ``socket.create_connection`` through the module.
    monkeypatch.setattr(socket, "create_connection", _create_connection)
    return calls


def _answers(*ips: str) -> list[tuple[Any, ...]]:
    """``getaddrinfo``-shaped answers, the form ``_resolve_hostname_safe`` returns."""

    answers: list[tuple[Any, ...]] = []
    for ip in ips:
        if ":" in ip:
            answers.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0)))
        else:
            answers.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)))
    return answers


def _resolve_to(monkeypatch: pytest.MonkeyPatch, *ips: str) -> list[str]:
    """Let ``_resolve_hostname_safe`` answer ``ips``; returns the looked-up names."""

    looked_up: list[str] = []

    def _resolve(hostname: str) -> list[tuple[Any, ...]]:
        looked_up.append(hostname)
        return _answers(*ips)

    monkeypatch.setattr(http_utils, "_resolve_hostname_safe", _resolve)
    return looked_up


SAFE_DNS_CONNECTIONS = pytest.mark.parametrize(
    ("connection_cls", "port"),
    [(SafeDNSHTTPConnection, 80), (SafeDNSHTTPSConnection, 443)],
    ids=["http", "https"],
)


@SAFE_DNS_CONNECTIONS
def test_connects_to_the_first_safe_address(
    connection_cls: type[SafeDNSHTTPConnection | SafeDNSHTTPSConnection],
    port: int,
    monkeypatch: pytest.MonkeyPatch,
    opened: list[tuple[Any, ...]],
) -> None:
    # Private, loopback and shared (CGNAT) answers come first and are skipped.
    looked_up = _resolve_to(
        monkeypatch, "10.0.0.5", "127.0.0.1", "100.64.0.1", STUB_PUBLIC_IP, OTHER_PUBLIC_IP
    )
    conn = connection_cls(
        "data.wien.gv.at",
        port,
        timeout=7,
        socket_options=[(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)],
    )

    sock = conn._new_conn()

    assert looked_up == ["data.wien.gv.at"]
    assert opened == [((STUB_PUBLIC_IP, port), 7, None)]
    assert isinstance(sock, _StandInSocket)
    assert sock.options == [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]


@SAFE_DNS_CONNECTIONS
def test_safe_ip_literal_needs_no_lookup(
    connection_cls: type[SafeDNSHTTPConnection | SafeDNSHTTPSConnection],
    port: int,
    monkeypatch: pytest.MonkeyPatch,
    opened: list[tuple[Any, ...]],
) -> None:
    looked_up = _resolve_to(monkeypatch, OTHER_PUBLIC_IP)

    sock = connection_cls(STUB_PUBLIC_IP, port, socket_options=[])._new_conn()

    assert looked_up == []
    assert [address for address, _timeout, _source in opened] == [(STUB_PUBLIC_IP, port)]
    assert sock.options == []


@SAFE_DNS_CONNECTIONS
@pytest.mark.parametrize(
    ("host", "answers"),
    [
        pytest.param("127.0.0.1", (), id="loopback-literal"),
        pytest.param("10.1.2.3", ("10.1.2.3",), id="private-literal"),
        pytest.param("intern.example", ("192.168.1.10", "::1", "fe80::1"), id="private-answers"),
        pytest.param(
            "rebind.example",
            ("0.0.0.0", "100.64.0.1", "::ffff:127.0.0.1"),  # noqa: S104 - answers to refuse, no bind
            id="rebinding-answers",
        ),
        pytest.param("leer.example", (), id="no-answer"),
    ],
)
def test_no_safe_address_opens_nothing(
    connection_cls: type[SafeDNSHTTPConnection | SafeDNSHTTPSConnection],
    port: int,
    host: str,
    answers: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    opened: list[tuple[Any, ...]],
) -> None:
    _resolve_to(monkeypatch, *answers)

    with pytest.raises(ValueError, match=r"No safe IP resolved .*\(DNS Rebinding protection\)"):
        connection_cls(host, port)._new_conn()

    assert opened == []


@pytest.mark.parametrize(
    "socket_options",
    [pytest.param([(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)], id="options"), pytest.param([], id="none")],
)
def test_pinned_connection_opens_the_pinned_ip_and_keeps_the_name(
    socket_options: list[tuple[int, int, int]],
    monkeypatch: pytest.MonkeyPatch,
    opened: list[tuple[Any, ...]],
) -> None:
    looked_up = _resolve_to(monkeypatch, OTHER_PUBLIC_IP)
    conn = PinnedHTTPSConnection(
        "data.wien.gv.at",
        443,
        pinned_ip=STUB_PUBLIC_IP,
        timeout=5,
        socket_options=socket_options,
    )

    sock = conn._new_conn()

    assert looked_up == []
    assert opened == [((STUB_PUBLIC_IP, 443), 5, None)]
    # SNI and the certificate check go by the name, not by the IP.
    assert conn.host == "data.wien.gv.at"
    assert sock.options == socket_options


@pytest.mark.parametrize(
    ("url", "expected_cls", "port"),
    [
        pytest.param("http://data.wien.gv.at/x", SafeDNSHTTPConnection, 80, id="http"),
        pytest.param("https://data.wien.gv.at/x", SafeDNSHTTPSConnection, 443, id="https"),
    ],
)
def test_safe_dns_adapter_pools_use_the_safe_connections(
    url: str,
    expected_cls: type[Any],
    port: int,
    monkeypatch: pytest.MonkeyPatch,
    opened: list[tuple[Any, ...]],
) -> None:
    _resolve_to(monkeypatch, "10.0.0.5", STUB_PUBLIC_IP)
    adapter = SafeDNSAdapter(timeout=3)
    try:
        conn = adapter.poolmanager.connection_from_url(url)._new_conn()
        assert isinstance(conn, expected_cls)
        conn._new_conn()
    finally:
        adapter.close()

    assert [address for address, _timeout, _source in opened] == [(STUB_PUBLIC_IP, port)]


def test_pinned_adapter_pool_uses_the_pinned_connection(
    monkeypatch: pytest.MonkeyPatch,
    opened: list[tuple[Any, ...]],
) -> None:
    looked_up = _resolve_to(monkeypatch, OTHER_PUBLIC_IP)
    adapter = PinnedHTTPSAdapter(STUB_PUBLIC_IP, timeout=3)
    try:
        conn = adapter.poolmanager.connection_from_url("https://data.wien.gv.at/x")._new_conn()
        assert isinstance(conn, PinnedHTTPSConnection)
        assert conn.host == "data.wien.gv.at"
        conn._new_conn()
    finally:
        adapter.close()

    assert looked_up == []
    assert [address for address, _timeout, _source in opened] == [(STUB_PUBLIC_IP, 443)]
