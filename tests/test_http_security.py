
import pytest
import responses
from unittest.mock import patch
from src.utils.http import session_with_retries, validate_http_url

def test_redirect_limit_enforcement():
    """Verify that the session redirect limit is securely configured."""
    session = session_with_retries("test-agent")
    # Should be limited to 10 (down from default 30) to mitigate ReDoS/resource exhaustion
    assert session.max_redirects == 10

def test_unsafe_tlds_blocked():
    """Verify that infrastructure TLDs are blocked."""
    # .arpa (Infrastructure TLD)
    url_arpa = "http://infra.arpa"
    assert validate_http_url(url_arpa, check_dns=False) is None

    # .kubernetes (Internal Cluster DNS)
    url_k8s = "http://service.kubernetes"
    assert validate_http_url(url_k8s, check_dns=False) is None

    # .cluster.local (Kubernetes default domain - blocked via .local)
    url_local = "http://foo.cluster.local"
    assert validate_http_url(url_local, check_dns=False) is None

    # .localdomain (Common internal)
    url_localdomain = "http://server.localdomain"
    assert validate_http_url(url_localdomain, check_dns=False) is None

    # .workgroup (Windows)
    url_workgroup = "http://pc.workgroup"
    assert validate_http_url(url_workgroup, check_dns=False) is None

    # .svc (Kubernetes)
    url_svc = "http://my-service.svc"
    assert validate_http_url(url_svc, check_dns=False) is None

    # .cluster (Kubernetes)
    url_cluster = "http://my-pod.cluster"
    assert validate_http_url(url_cluster, check_dns=False) is None

    # .consul (HashiCorp Consul)
    url_consul = "http://db.consul"
    assert validate_http_url(url_consul, check_dns=False) is None

def test_extended_unsafe_tlds_blocked():
    """Verify that newly added infrastructure TLDs are blocked."""
    # .prod
    assert validate_http_url("http://api.prod", check_dns=False) is None
    # .stage
    assert validate_http_url("http://db.stage", check_dns=False) is None
    # .staging
    assert validate_http_url("http://backend.staging", check_dns=False) is None
    # .sys
    assert validate_http_url("http://monitor.sys", check_dns=False) is None
    # .backup
    assert validate_http_url("http://store.backup", check_dns=False) is None
    # .printer
    assert validate_http_url("http://office.printer", check_dns=False) is None
    # .kube
    assert validate_http_url("http://svc.kube", check_dns=False) is None
    # .openshift
    assert validate_http_url("http://console.openshift", check_dns=False) is None
    # .istio
    assert validate_http_url("http://mesh.istio", check_dns=False) is None
    # .mesh
    assert validate_http_url("http://internal.mesh", check_dns=False) is None
    # .intra
    assert validate_http_url("http://portal.intra", check_dns=False) is None

def test_unsafe_tlds_blocked_with_dns_check():
    """Verify that infrastructure TLDs are blocked even if DNS check is enabled and resolves."""

    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        # Simulate resolving to a safe public IP to mimic an attacker
        # tricking DNS or an internal environment resolving .local
        mock_resolve.return_value = [(2, 1, 6, '', ('8.8.8.8', 80))]

        # .kubernetes should be blocked even if DNS resolves it
        url_k8s = "http://service.kubernetes"
        assert validate_http_url(url_k8s, check_dns=True) is None

        # .local should be blocked
        url_local = "http://internal.local"
        assert validate_http_url(url_local, check_dns=True) is None

@patch("src.utils.http.verify_response_ip")
@patch("src.utils.http.validate_http_url")
def test_strip_headers_on_scheme_downgrade(mock_validate_url, mock_verify_ip):
    """Verify that sensitive headers are stripped when redirecting from HTTPS to HTTP (Downgrade Attack)."""
    # Allow any URL for this test
    mock_validate_url.side_effect = lambda url, **kwargs: url
    mock_verify_ip.return_value = None  # No-op

    session = session_with_retries("test-agent")

    @responses.activate
    def run():
        # Setup redirect: HTTPS -> HTTP (same domain)
        responses.add(responses.GET, "https://secure.example.com/", status=302, headers={"Location": "http://secure.example.com/login"})
        responses.add(responses.GET, "http://secure.example.com/login", status=200)

        headers = {
            "X-Api-Key": "super-secret-key",
            "Authorization": "Bearer mytoken",
            "Cookie": "session=secret",
            "X-Sentry-Token": "sentry-token-value"
        }

        session.get("https://secure.example.com/", headers=headers)

        assert len(responses.calls) == 2
        # First request (HTTPS) should have headers
        req1 = responses.calls[0].request
        assert req1.headers["X-Api-Key"] == "super-secret-key"
        assert req1.headers["X-Sentry-Token"] == "sentry-token-value"

        # Second request (HTTP) should NOT have sensitive headers
        req2 = responses.calls[1].request
        assert "X-Api-Key" not in req2.headers, "X-Api-Key leaked to insecure HTTP endpoint"
        assert "Authorization" not in req2.headers, "Authorization leaked to insecure HTTP endpoint"
        # Note: 'Cookie' might be missing anyway due to requests default behavior, but we check to be sure
        assert "Cookie" not in req2.headers, "Cookie leaked to insecure HTTP endpoint"
        assert "X-Sentry-Token" not in req2.headers, "X-Sentry-Token leaked to insecure HTTP endpoint"

    run()


@patch("src.utils.http.verify_response_ip")
@patch("src.utils.http.validate_http_url")
def test_strip_headers_on_port_change(mock_validate_url, mock_verify_ip):
    """Verify that sensitive headers are stripped when redirecting to a different port on the same host."""
    # Allow any URL for this test
    mock_validate_url.side_effect = lambda url, **kwargs: url
    mock_verify_ip.return_value = None  # No-op

    session = session_with_retries("test-agent")

    @responses.activate
    def run():
        # Setup redirect: 8443 -> 9443 (same domain, different port)
        responses.add(responses.GET, "https://example.com:8443/", status=302, headers={"Location": "https://example.com:9443/resource"})
        responses.add(responses.GET, "https://example.com:9443/resource", status=200)

        headers = {
            "X-Api-Key": "super-secret-key",
            "Authorization": "Bearer mytoken",
            "X-Sentry-Token": "sentry-token-value"
        }

        session.get("https://example.com:8443/", headers=headers)

        assert len(responses.calls) == 2
        # First request (8443) should have headers
        req1 = responses.calls[0].request
        assert req1.headers["X-Api-Key"] == "super-secret-key"

        # Second request (9443) should NOT have sensitive headers
        req2 = responses.calls[1].request
        assert "X-Api-Key" not in req2.headers, "X-Api-Key leaked to different port"
        assert "Authorization" not in req2.headers, "Authorization leaked to different port"
        assert "X-Sentry-Token" not in req2.headers, "X-Sentry-Token leaked to different port"

    run()
