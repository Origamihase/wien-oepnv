from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module() -> object:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "generate_sitemap.py"
    spec = importlib.util.spec_from_file_location("generate_sitemap", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[assignment]
    return module


def test_base_url_rejects_invalid_scheme(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", "javascript:alert(1)")
    assert module._base_url() == module.DEFAULT_BASE_URL.rstrip("/")


def test_base_url_rejects_control_characters(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", "https://example.com\n/inject")
    assert module._base_url() == module.DEFAULT_BASE_URL.rstrip("/")


def test_base_url_accepts_valid_https(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("SITE_BASE_URL", "https://example.com/base/")
    assert module._base_url() == "https://example.com/base"
