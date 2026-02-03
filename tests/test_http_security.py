
import pytest
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
