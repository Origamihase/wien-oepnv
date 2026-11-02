"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_network_tainted_non_finite_drift.py.
"""

from __future__ import annotations

import inspect
from typing import Any

import requests


SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT = (
    "network/env/sidecar non-finite literal drift"
)


# ---------------------------------------------------------------------------
# Inventory pins (source-grep): every enumerated site must carry the hooks.
# ---------------------------------------------------------------------------


def _assert_non_finite_pin(func: Any, *, where: str) -> None:
    """Assert ``func`` source pins the non-finite defence in some form."""
    source = inspect.getsource(func)
    if "loads_finite(" in source:
        return  # via wrapper
    # Otherwise both hooks must be present as kwargs.
    assert "parse_constant=_reject_non_finite_constant" in source, (
        f"{where}: missing ``parse_constant=_reject_non_finite_constant`` "
        f"pin. A planted NaN / Infinity literal from the taint source "
        f"propagates as ``float('nan')`` / ``float('inf')`` past the "
        f"parse boundary into Python computation.\n\nMarker: "
        f"{SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT}"
    )
    assert "parse_float=_reject_non_finite_float" in source, (
        f"{where}: missing ``parse_float=_reject_non_finite_float`` "
        f"pin. A planted ``1e1000`` scientific-notation overflow bypasses "
        f"``parse_constant`` and lands ``float('inf')`` in the parsed "
        f"structure.\n\nMarker: "
        f"{SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT}"
    )


# ---------------------------------------------------------------------------
# Behavioural PoC: response.json() shape (OSM / HAFAS / GitHub).
# ---------------------------------------------------------------------------


def _make_response(body: bytes, status_code: int = 200) -> requests.Response:
    """Construct a real ``requests.Response`` with the given body bytes.

    We populate the internal ``_content`` and ``_content_consumed`` flags
    so ``response.json()`` runs the actual stdlib parse path (with our
    pinned hooks), not a mock that bypasses the security boundary.
    """
    resp = requests.Response()
    resp._content = body
    resp._content_consumed = True
    resp.status_code = status_code
    resp.encoding = "utf-8"
    resp.url = "https://example.com/test"
    return resp


__all__ = [
    "SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT",
    "_assert_non_finite_pin",
    "_make_response",
]
