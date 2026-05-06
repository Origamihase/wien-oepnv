from __future__ import annotations

import importlib.util
import pytest
from pathlib import Path


def _load_module() -> object:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "generate_sitemap.py"
    spec = importlib.util.spec_from_file_location("generate_sitemap", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_base_url_rejects_invalid_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import cast, Any
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", "javascript:alert(1)")
    assert cast(Any, module)._base_url() == cast(Any, module).DEFAULT_BASE_URL.rstrip("/")


def test_base_url_rejects_control_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import cast, Any
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", "https://example.com\n/inject")
    assert cast(Any, module)._base_url() == cast(Any, module).DEFAULT_BASE_URL.rstrip("/")


def test_base_url_accepts_valid_https(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import cast, Any
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", "https://example.com/base/")
    assert cast(Any, module)._base_url() == "https://example.com/base"


@pytest.mark.parametrize(
    "value",
    [
        # Loopback / IP-literal hosts must not land in the published sitemap.
        "http://localhost:8080",
        "http://127.0.0.1",
        "https://192.168.1.1",
        "https://[::1]",
        # Reserved / internal TLDs (RFC 6761, RFC 2606, container DNS) — would
        # leak internal hostnames into the sitemap if accepted.
        "https://app.internal",
        "https://my.local",
        "https://example.test",
        "https://example.invalid",
        "https://service.cluster",
        "https://api.svc",
        # Wildcard DNS rebinding services that resolve to loopback.
        "http://127.0.0.1.nip.io",
        "https://example.nip.io",
    ],
)
def test_base_url_rejects_internal_and_ip_hosts(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Stricter validation must reject hosts that would corrupt the public
    sitemap (and the matching robots.txt ``Sitemap:`` directive)."""
    from typing import cast, Any
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", value)
    assert cast(Any, module)._base_url() == cast(Any, module).DEFAULT_BASE_URL.rstrip(
        "/"
    )


def test_base_url_rejects_embedded_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing rejection — re-asserted after the validate_http_url swap so a
    future regression in the helper doesn't silently let credentials into a
    publicly-served URL."""
    from typing import cast, Any
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", "https://user:pass@example.com")
    assert cast(Any, module)._base_url() == cast(Any, module).DEFAULT_BASE_URL.rstrip(
        "/"
    )
