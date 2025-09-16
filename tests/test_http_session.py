from requests.adapters import HTTPAdapter

from src.utils.http import session_with_retries


def test_session_mounts_same_retry_adapter_for_http_and_https() -> None:
    session = session_with_retries("pytest-agent")

    http_adapter = session.adapters["http://"]
    https_adapter = session.adapters["https://"]

    assert isinstance(http_adapter, HTTPAdapter)
    assert http_adapter is https_adapter

    retry = http_adapter.max_retries
    assert retry.total == 4
    assert retry.backoff_factor == 0.6
    assert retry.status_forcelist == (429, 500, 502, 503, 504)
    assert retry.allowed_methods == ("GET",)
