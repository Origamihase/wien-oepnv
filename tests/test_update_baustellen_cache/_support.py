"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_update_baustellen_cache.py.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache
from tests._conftest.http_bindings import setattr_all


def _patch_http_layer_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch the SSRF / DNS / IP-verification layer so a
    ``responses``-mocked URL can be consumed without hitting the real
    network.

    Defense-in-depth: TWO independent safeguards prevent a real
    network leak if a future test author calls this helper without
    activating ``responses``:

    1. **TEST-NET-1 fake IP** (``192.0.2.1``, RFC 5737 §3) — IANA-
       reserved for documentation-only use. Routers MUST NOT forward
       packets to this range, so a leaked HTTP request times out
       at the OS networking layer instead of reaching a real host
       (the previous fake ``8.8.8.8`` was Google Public DNS, which
       responds to TCP connects on port 443 — polite but still a
       real network round-trip).

    2. **Runtime guard via ``responses.is_enabled()``** — the
       patched ``_resolve_hostname_safe`` lambda first checks whether
       the ``responses`` mocker is currently active. If not, it
       raises ``RuntimeError`` with a clear message instructing the
       test author to wrap the HTTP-using code in
       ``@responses.activate``. This converts the silent-timeout
       failure mode into a fail-fast diagnostic.

    Combined with the existing test-isolation property of pytest's
    ``monkeypatch`` (per-test scope, auto-reverted), the helper is
    safe to use in any test that legitimately mocks HTTP via
    ``responses``.

    Patches the four layers in concert; missing any one leaks
    through to a real network call:

    * ``validate_http_url`` — patched on the **caller's namespace**
      (``update_baustellen_cache``) because the script does
      ``from src.utils.http import validate_http_url`` at import time,
      so patching only ``sys.modules['src.utils.http'].validate_http_url``
      would leave the script's local binding pointing at the
      original function.
    * ``verify_response_ip`` — patched on the **provider module**.
    * ``_resolve_hostname_safe`` — patched on the **provider
      module** with a fake resolver returning the TEST-NET-1 IP
      (and the runtime guard described above).
    * ``is_ip_safe`` — patched on the **provider module** to
      always return ``True`` so the safe-IP allowlist does not
      reject the TEST-NET-1 fallback (which is correctly classified
      as a documentation-range IP and would otherwise be rejected).
    """
    import responses as _responses_lib

    # RFC 5737 §3 TEST-NET-1 — guaranteed unrouteable so a leaked
    # HTTP request times out instead of reaching a real host.
    fake_addrinfo: list[tuple[Any, ...]] = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0)),
    ]

    def _is_responses_active() -> bool:
        """Detect whether the ``responses`` mocker has been started.

        ``responses.mock._patcher`` is ``None`` when the mocker is
        idle and a ``unittest.mock._patch`` instance after
        ``start()`` (set by both the ``@responses.activate``
        decorator and the ``responses.RequestsMock`` context
        manager). ``responses.is_enabled()`` is the public API in
        newer ``responses`` versions but is not present in the
        version pinned by this project (see requirements-dev.txt);
        the internal-attribute check is the documented fallback
        per the ``responses`` issue tracker.
        """
        return getattr(_responses_lib.mock, "_patcher", None) is not None

    def _guarded_resolve(_hostname: str) -> list[tuple[Any, ...]]:
        """Fail-fast guard: refuse to resolve when ``responses`` is not
        active. This converts the silent-timeout failure mode (the
        TEST-NET-1 IP would otherwise produce a 5-second timeout) into
        an immediate, actionable error pointing at the missing
        ``@responses.activate`` decorator.
        """
        if not _is_responses_active():
            raise RuntimeError(
                "_patch_http_layer_bypass was called but the "
                "``responses`` HTTP mocker is NOT currently active. "
                "Wrap the HTTP-using code in @responses.activate "
                "(or use the responses context manager). Refusing to "
                "let a real HTTP request reach the network — even via "
                "the TEST-NET-1 fallback IP — because doing so would "
                "produce a misleading 5-second timeout instead of a "
                "clear test-setup error."
            )
        return fake_addrinfo

    # 1) The caller's local binding (the script imports the name
    # directly, so the module-attribute patch below cannot reach it).
    monkeypatch.setattr(
        update_baustellen_cache, "validate_http_url", lambda url, **_kw: url
    )
    # 2) The provider-module attributes — these are looked up at call
    # time by the HTTP machinery itself, so patching the module
    # attribute is sufficient.
    module = sys.modules["src.utils.http"]
    monkeypatch.setattr(module, "validate_http_url", lambda url, **kw: url)
    monkeypatch.setattr(module, "verify_response_ip", lambda _: None)
    # Since the split of src/utils/http.py (2026-11-02) both names are also
    # read from src/utils/http_safety/*.py: replace every binding.
    setattr_all(monkeypatch, "_resolve_hostname_safe", _guarded_resolve)
    setattr_all(monkeypatch, "is_ip_safe", lambda _ip, **_kw: True)


SAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "samples" / "baustellen_sample.geojson"


def _bau_feature(*, name: str = "Brünner Straße 59", objectid: int = 1) -> dict[str, Any]:
    return {
        "properties": {
            "BEZEICHNUNG": name,
            "OBJEKT_BEGINN": "2026-05-17Z",
            "OBJEKT_ENDE": "2026-08-14Z",
            "OBJECTID": objectid,
        },
        "geometry": {},
    }


def _real_cache_file(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Point the updater's own ``write_cache`` (``src.utils.cache``) at *target*."""
    module = sys.modules[update_baustellen_cache.__dict__["write_cache"].__module__]
    monkeypatch.setattr(module, "_cache_file", lambda provider: target)


def _site(name: str, text: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [16.37, 48.2]},
        "properties": {
            "BEZEICHNUNG": name,
            "OBJEKT_BEGINN": "2026-09-30Z",
            "OBJEKT_ENDE": "2026-11-15Z",
            "PRESSETEXT": text,
        },
    }


__all__ = [
    "SAMPLE_PATH",
    "_bau_feature",
    "_patch_http_layer_bypass",
    "_real_cache_file",
    "_site",
]
