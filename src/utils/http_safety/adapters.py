"""Transport adapters and DNS-safe / pinned connections.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from requests.utils import select_proxy
from urllib3.connection import HTTPSConnection, HTTPConnection
from urllib3.connectionpool import HTTPSConnectionPool, HTTPConnectionPool
from urllib3.poolmanager import PoolManager

from .ip_guard import _resolve_hostname_safe, is_ip_safe
from .limits import DEFAULT_TIMEOUT
from .peer import _require_trusted_proxy_host
from .redaction import _sanitize_url_for_error

class TimeoutHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that enforces a default timeout."""

    def __init__(self, *args: Any, timeout: int | float | tuple[float, float] | None = None, **kwargs: Any) -> None:
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: Any = True,
        cert: Any = None,
        proxies: dict[str, str] | None = None,
    ) -> requests.Response:
        # Same parameters and defaults as ``HTTPAdapter.send`` (requests >= 2.34
        # ships its own types and checks the override); ``Session.send`` passes
        # them as keywords.
        if timeout is None:
            timeout = self.timeout if self.timeout is not None else DEFAULT_TIMEOUT
        # Security: the proxy ``requests`` is about to use, decided before a
        # byte leaves; an untrusted host fails closed (PROXY_TRUSTED_HOSTS).
        if request.url and select_proxy(request.url, proxies or {}) is not None:
            _require_trusted_proxy_host(request.url)
        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


class SafeDNSHTTPConnection(HTTPConnection):
    """
    HTTPConnection that resolves DNS once, validates the IP, and connects securely.
    """
    def _new_conn(self) -> Any:
        target_ip: str | None = None
        try:
            ip_obj = ipaddress.ip_address(self.host)
            target_ip_cand = str(ip_obj)
            if is_ip_safe(target_ip_cand):
                target_ip = str(target_ip_cand)
        except ValueError:
            pass  # host is a name, not an IP literal: resolved safely below

        if target_ip is None:
            ips = _resolve_hostname_safe(self.host)
            for _, _, _, _, sockaddr in ips:
                if is_ip_safe(str(sockaddr[0])):
                    target_ip = str(sockaddr[0])
                    break

        if not target_ip:
            sanitized = _sanitize_url_for_error(f"http://{self.host}")
            raise ValueError(f"No safe IP resolved for {sanitized} (DNS Rebinding protection)")

        conn = socket.create_connection(
            (target_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )

        if self.socket_options:
            for opt in self.socket_options:
                conn.setsockopt(*opt[:3])

        return conn


class SafeDNSHTTPSConnection(HTTPSConnection):
    """
    HTTPSConnection that resolves DNS once, validates the IP, and connects securely.
    """
    def _new_conn(self) -> Any:
        target_ip: str | None = None
        try:
            ip_obj = ipaddress.ip_address(self.host)
            target_ip_cand = str(ip_obj)
            if is_ip_safe(target_ip_cand):
                target_ip = str(target_ip_cand)
        except ValueError:
            pass  # host is a name, not an IP literal: resolved safely below

        if target_ip is None:
            ips = _resolve_hostname_safe(self.host)
            for _, _, _, _, sockaddr in ips:
                if is_ip_safe(str(sockaddr[0])):
                    target_ip = str(sockaddr[0])
                    break

        if not target_ip:
            sanitized = _sanitize_url_for_error(f"https://{self.host}")
            raise ValueError(f"No safe IP resolved for {sanitized} (DNS Rebinding protection)")

        conn = socket.create_connection(
            (target_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )

        if self.socket_options:
            for opt in self.socket_options:
                conn.setsockopt(*opt[:3])

        return conn


# TCP keepalive for a connection that waits minutes for its answer without a
# byte on the wire (a thinking model): the first probe after 60 s idle, then
# every 30 s, so no NAT or firewall on the way forgets the connection. On
# 2026-11-02 the KI-Wächter request from a GitHub-hosted runner died after
# 261 s with a ConnectionError while Gemini was still working.
KEEPALIVE_SOCKET_OPTIONS: tuple[tuple[int, int, int], ...] = tuple(
    opt for opt in (
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPIDLE", -1), 60),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPINTVL", -1), 30),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPCNT", -1), 5),
    ) if opt[1] != -1
)


class SafeDNSAdapter(TimeoutHTTPAdapter):
    """
    HTTPAdapter that forces safe DNS resolution for all connections.

    ``keepalive=True`` turns on TCP keepalive (:data:`KEEPALIVE_SOCKET_OPTIONS`)
    for requests that wait minutes for an answer.
    """
    def __init__(self, *args: Any, keepalive: bool = False, **kwargs: Any) -> None:
        # Set before ``super().__init__``, which builds the pool manager.
        self.keepalive = keepalive
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        if getattr(self, "keepalive", False):
            pool_kwargs["socket_options"] = list(KEEPALIVE_SOCKET_OPTIONS)

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )

        class SafeDNSHTTPConnectionPool(HTTPConnectionPool):
            ConnectionCls = SafeDNSHTTPConnection

        class SafeDNSHTTPSConnectionPool(HTTPSConnectionPool):
            ConnectionCls = SafeDNSHTTPSConnection

        self.poolmanager.pool_classes_by_scheme = self.poolmanager.pool_classes_by_scheme.copy()
        self.poolmanager.pool_classes_by_scheme["http"] = SafeDNSHTTPConnectionPool
        self.poolmanager.pool_classes_by_scheme["https"] = SafeDNSHTTPSConnectionPool


class PinnedHTTPSConnection(HTTPSConnection):
    """
    HTTPSConnection that forces connection to a specific IP while keeping the original hostname for SNI.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._pinned_ip = kwargs.pop("pinned_ip", None)
        super().__init__(*args, **kwargs)

    def _new_conn(self) -> Any:
        # Create socket connected to pinned IP

        # We ignore self.host for connection, use pinned_ip
        # explicit source_address passing to satisfy MyPy
        conn = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )

        # Apply socket options if present (simulating urllib3 behavior)
        if self.socket_options:
            for opt in self.socket_options:
                # opt is a tuple (level, optname, value)
                # some platforms might have 4 items? setsockopt takes 3.
                conn.setsockopt(*opt[:3])

        return conn


class PinnedHTTPSAdapter(TimeoutHTTPAdapter):
    """
    HTTPAdapter that forces all connections to a specific IP address
    while preserving the original hostname for SNI and Host header.
    """

    def __init__(self, pinned_ip: str, *args: Any, keepalive: bool = False, **kwargs: Any) -> None:
        self.pinned_ip = pinned_ip
        # Set before ``super().__init__``, which builds the pool manager.
        self.keepalive = keepalive
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        if getattr(self, "keepalive", False):
            pool_kwargs["socket_options"] = list(KEEPALIVE_SOCKET_OPTIONS)

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )

        # Create a closure class for PinnedHTTPSConnection that has pinned_ip baked in.
        pinned_ip = self.pinned_ip

        class LocalPinnedHTTPSConnection(PinnedHTTPSConnection):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["pinned_ip"] = pinned_ip
                super().__init__(*args, **kwargs)

        # Get the default HTTPS pool class
        # We use explicit HTTPSConnectionPool as base to satisfy MyPy,
        # assuming urllib3 uses that (which it does).
        # Dynamic base class causes MyPy errors.

        class PinnedHTTPSConnectionPool(HTTPSConnectionPool):
            ConnectionCls = LocalPinnedHTTPSConnection

        # Register it
        self.poolmanager.pool_classes_by_scheme = self.poolmanager.pool_classes_by_scheme.copy()
        self.poolmanager.pool_classes_by_scheme["https"] = PinnedHTTPSConnectionPool
