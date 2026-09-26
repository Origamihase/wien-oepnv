"""Requests through a proxy reach trusted hosts only (audit 2026-09-17, B.3).

Behind a proxy the proxy resolves the hostname itself: neither the pinned IP
nor the peer-IP check can hold, and the peer is the proxy. The HTTP layer used
to skip the check silently whenever a proxy variable was set (fail open). Now a
request through a proxy must go to a host of ``PROXY_TRUSTED_HOSTS`` or to a
literal, safe IP; anything else fails closed before a byte is sent.

The conftest removes the host's proxy variables; each test sets its own.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import pytest
import requests
import responses

from scripts import sync_hafas_profile, update_baustellen_cache, update_station_directory, update_wl_stations
from src.places import client as places_client
from src.places import hafas_client, osm_client
from src.providers import oebb, vor, wl_fetch
from src.utils.http import (
    PROXY_TRUSTED_HOSTS,
    TimeoutHTTPAdapter,
    is_ip_safe,
    request_safe,
    session_with_retries,
    verify_response_ip,
)

PROXY = "http://proxy.invalid:3128"


def _response(url: str, peer: str) -> Mock:
    """A response whose socket reports *peer* (behind a proxy: the proxy)."""
    response = Mock()
    response.request.url = url
    response.url = url
    response.raw._connection.sock.getpeername.return_value = (peer, 443)
    return response


def test_without_a_proxy_the_peer_ip_is_checked() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="Connected to unsafe IP"):
            verify_response_ip(_response("https://www.wienerlinien.at/ogd_realtime/x", "192.168.1.1"))


@pytest.mark.parametrize(
    ("variables", "url"),
    [
        ({"HTTPS_PROXY": PROXY}, "https://www.wienerlinien.at/ogd_realtime/x"),
        ({"https_proxy": PROXY}, "https://fahrplan.oebb.at/bin/mgate.exe"),
        ({"ALL_PROXY": "socks5://proxy.invalid:1080"}, "https://places.googleapis.com/v1/places:searchText"),
        ({"HTTP_PROXY": PROXY}, "http://93.184.216.34/pinned"),  # the pinned plain-HTTP path
        ({"HTTPS_PROXY": PROXY}, "https://WWW.Wienerlinien.at./ogd_realtime/x"),  # case, trailing dot
    ],
)
def test_behind_a_proxy_a_trusted_host_passes(variables: dict[str, str], url: str) -> None:
    # The peer is the proxy, often in a private network: no longer a reason to refuse.
    with patch.dict(os.environ, variables, clear=True):
        verify_response_ip(_response(url, "10.0.0.5"))


@pytest.mark.parametrize(
    ("variables", "url"),
    [
        ({"HTTPS_PROXY": PROXY}, "https://evil.example/data"),
        ({"https_proxy": PROXY}, "https://api.github.com.evil.example/"),
        ({"ALL_PROXY": "socks5://proxy.invalid:1080"}, "https://internal.corp/"),
        ({"HTTP_PROXY": PROXY}, "http://10.0.0.1/admin"),
        ({"HTTP_PROXY": PROXY}, "http://169.254.169.254/latest/meta-data/"),
    ],
)
def test_behind_a_proxy_any_other_host_fails_closed(variables: dict[str, str], url: str) -> None:
    with patch.dict(os.environ, variables, clear=True):
        with pytest.raises(ValueError, match="not a trusted host for requests through a proxy"):
            verify_response_ip(_response(url, "10.0.0.5"))


def test_a_host_excluded_by_no_proxy_gets_the_peer_check() -> None:
    # NO_PROXY: requests connects directly, so the peer is the upstream again.
    with patch.dict(os.environ, {"HTTPS_PROXY": PROXY, "NO_PROXY": "evil.example"}, clear=True):
        with pytest.raises(ValueError, match="Connected to unsafe IP"):
            verify_response_ip(_response("https://evil.example/data", "192.168.1.1"))


def test_the_proxy_of_the_other_scheme_does_not_count() -> None:
    with patch.dict(os.environ, {"HTTP_PROXY": PROXY}, clear=True):
        with pytest.raises(ValueError, match="Connected to unsafe IP"):
            verify_response_ip(_response("https://evil.example/data", "192.168.1.1"))


def _prepared(url: str) -> requests.PreparedRequest:
    return requests.Request("GET", url).prepare()


def test_the_adapter_refuses_an_untrusted_host_before_sending() -> None:
    with patch("requests.adapters.HTTPAdapter.send") as send:
        with pytest.raises(ValueError, match="not a trusted host"):
            TimeoutHTTPAdapter().send(_prepared("https://evil.example/data"), proxies={"https": PROXY})
    send.assert_not_called()


@pytest.mark.parametrize(
    ("url", "proxies"),
    [
        ("https://www.wienerlinien.at/ogd_realtime/x", {"https": PROXY}),
        ("https://evil.example/data", {}),  # no proxy: the pinned IP and peer check apply
        ("https://evil.example/data", {"http": PROXY}),  # a proxy for the other scheme
    ],
)
def test_the_adapter_sends_otherwise(url: str, proxies: dict[str, str]) -> None:
    with patch("requests.adapters.HTTPAdapter.send") as send:
        TimeoutHTTPAdapter().send(_prepared(url), proxies=proxies)
    send.assert_called_once()


@responses.activate
@pytest.mark.usefixtures("stub_public_dns")
def test_request_safe_behind_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    trusted = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-linien.csv"
    responses.add(responses.GET, trusted, body="LineID;LineText\n", content_type="text/csv")
    responses.add(responses.GET, "https://rebind-attacker.net/data", body="secret", content_type="text/plain")
    session = session_with_retries("test-agent")

    assert request_safe(session, trusted).text == "LineID;LineText\n"
    with pytest.raises(ValueError, match="not a trusted host"):
        request_safe(session, "https://rebind-attacker.net/data")
    assert [call.request.url for call in responses.calls] == [trusted]  # nothing reached the untrusted host


def test_session_warns_about_the_proxy(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    with caplog.at_level("WARNING"):
        session_with_retries("test-agent")
    assert "limited to PROXY_TRUSTED_HOSTS" in caplog.text


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


def _upstream_hosts() -> dict[str, set[str]]:
    """Every host the project fetches from, by where it is configured."""
    sources: dict[str, Any] = {
        "wl_fetch": wl_fetch._WL_TRUSTED_HOSTS,
        "oebb": oebb._OEBB_TRUSTED_HOSTS,
        "vor": vor._VOR_TRUSTED_HOSTS,
        "baustellen": update_baustellen_cache._BAUSTELLEN_TRUSTED_HOSTS,
        "hafas": {_host(hafas_client._HAFAS_ENDPOINT)},
        "overpass": {_host(url) for url in osm_client.DEFAULT_OVERPASS_ENDPOINTS},
        "places": {_host(places_client._API_BASE)},
        "hafas_profile": {_host(sync_hafas_profile._PROFILE_SOURCE_URL)},
        "station_directory": {_host(update_station_directory.DEFAULT_SOURCE_URL)},
        "wl_stations": {_host(update_wl_stations.OGD_LINIEN_URL), _host(update_wl_stations.OGD_HALTESTELLEN_URL)},
        "reporting": {"api.github.com"},
    }
    return {name: set(hosts) for name, hosts in sources.items()}


def test_every_upstream_is_trusted_behind_a_proxy() -> None:
    # A new or moved upstream must be added to PROXY_TRUSTED_HOSTS, or it
    # fails closed on every machine with a proxy.
    missing = {name: hosts - PROXY_TRUSTED_HOSTS for name, hosts in _upstream_hosts().items() if hosts - PROXY_TRUSTED_HOSTS}
    assert missing == {}


def test_every_trusted_host_is_an_upstream() -> None:
    # The list names upstreams only; a stale entry widens what a proxy may reach.
    used = set().union(*_upstream_hosts().values())
    assert PROXY_TRUSTED_HOSTS - used == set()


def test_the_trusted_hosts_are_plain_hostnames() -> None:
    for host in PROXY_TRUSTED_HOSTS:
        assert host == host.lower().strip(".") and "/" not in host and ":" not in host
        assert not is_ip_safe(host)
