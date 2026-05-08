"""Sentinel PoC: JSON size-bomb defence — Round 4 ``scripts/`` network sweep.

Threat model
------------
Round 1 / Round 2 / Round 3 of the JSON size-bomb family closed the
**on-disk** parsers across ``src/`` (eleven loaders) and ``scripts/``
(sixteen loaders). The canonical defence — the shared
``src.utils.files.read_capped_json`` helper — combines the depth-bomb catch
tuple ``(OSError, json.JSONDecodeError, RecursionError)`` with the
byte-size cap fired BEFORE ``open()`` so the file content is never
buffered into memory when oversized.

Round 4 closes the structurally-orthogonal **network-response** axis the
prior rounds explicitly scoped out. Three direct ``session.get(...)`` /
``session.post(...)`` call sites in ``scripts/`` bypass the project's
canonical safe HTTP layer (``request_safe`` / ``fetch_content_safe``) and
read ``response.json()`` / ``response.text`` without any byte-size cap on
the response body:

  scripts/fetch_vor_haltestellen.py
    * ``fetch_access_id`` (line 161) — ``session.get(config_url, timeout=30)``
      then ``resp.text`` to extract the VAO ``accessId``.
    * ``fetch_candidates`` (line 411) — ``session.post(mgate_url, ...)`` to
      the VAO mgate endpoint, then ``resp.json()``.

  scripts/update_vor_stations.py
    * ``fetch_vor_stops_from_api`` (line 589) — ``session.get(...)`` to
      ``location.name``, then ``response.json()``.

Why the depth-bomb catch tuple is structurally insufficient (Round 1
verdict applies verbatim to network-sourced parsers): a wide-but-flat
JSON document such as ``[1,1,1,…(1 GiB)…]`` is BOTH a valid JSON
document AND wide enough to exhaust the runner's cgroup. ``response.json``
internally calls ``json.loads`` on the buffered ``response.content``
bytes; the resulting ``MemoryError`` is a ``BaseException`` subclass —
NOT caught by the existing ``except (ValueError, RecursionError)``
handlers — so the exception propagates past the per-station loop, past
the script's top-level try-block, and crashes the cron pipeline via
``CalledProcessError`` (orchestrator runs every script via
``subprocess.run(check=True)``).

Threat actor model: compromised upstream / DNS-hijack / MITM /
content-cache-poisoning attack on the VAO endpoints. Severity
MEDIUM-HIGH because the cron pipeline fan-out (`update_all_stations.py`)
turns a single poisoned response into a whole-pipeline abort, masking
any prior partial state and leaving no heartbeat record of the cause.

The fix shape mirrors the on-disk rounds: each call site adds
``stream=True`` and routes the body through
``src.utils.http.read_response_safe`` (the canonical helper that already
enforces ``MAX_PAYLOAD_SIZE = 10 MiB`` via the ``Content-Length``
pre-check AND a streaming-byte-budget tally on ``iter_content``). Each
script exposes its own ``MAX_VOR_API_RESPONSE_BYTES`` module-level
constant so the auto-discoverable inventory test catches any future
loader added without the cap.
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
import requests


# ============================================================================
# Test scaffolding
# ============================================================================


_BASE_DIR = Path(__file__).resolve().parents[1]


class _SizeBombResponse:
    """Mock response that simulates an upstream serving a wide-but-flat
    payload above the size cap. ``iter_content_called`` flips True only if
    a caller actually streams the body; the post-fix ``read_response_safe``
    short-circuits via ``Content-Length`` BEFORE any streaming, so we can
    pin the cap fires *before* the body is buffered.
    """

    def __init__(
        self,
        *,
        content_length: int | None = None,
        body: bytes = b"[]",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        # Use the supplied custom headers; if the caller did not pass any
        # we synthesise a Content-Type to keep ``response.json()`` parity.
        merged_headers: dict[str, str] = {"Content-Type": "application/json"}
        if headers is not None:
            merged_headers.update(headers)
        if content_length is not None:
            merged_headers.setdefault("Content-Length", str(content_length))
        self.headers = merged_headers
        self._body = body
        self.iter_content_called = False
        self.closed = False

    # The following surface mirrors the subset of ``requests.Response``
    # that ``read_response_safe`` and the call-site code use post-fix.

    def iter_content(self, chunk_size: int = 8192) -> Any:
        self.iter_content_called = True
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        # Pre-fix paths call ``.json()`` directly. We make this work by
        # decoding the recorded body. After the fix, callers read via
        # ``read_response_safe`` first and then ``json.loads`` on bytes —
        # but for any test that exercises the normal-sized branch we
        # still need a working ``.json()``.
        return json.loads(self._body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body

    def __enter__(self) -> _SizeBombResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class _RecordingSession:
    """Minimal session double that records the kwargs of every
    ``get``/``post``/``request`` call. The post-fix code MUST pass
    ``stream=True`` so the response body is not buffered eagerly — we
    pin that contract here.
    """

    def __init__(self, response: _SizeBombResponse) -> None:
        self._response = response
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _SizeBombResponse:
        self.calls.append(("GET", url, kwargs))
        return self._response

    def post(self, url: str, **kwargs: Any) -> _SizeBombResponse:
        self.calls.append(("POST", url, kwargs))
        return self._response

    def request(self, method: str, url: str, **kwargs: Any) -> _SizeBombResponse:
        self.calls.append((method, url, kwargs))
        return self._response

    def __enter__(self) -> _RecordingSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def _module_source(module_name: str) -> ast.Module:
    """Parse a module by dotted name into an AST tree (file-based)."""
    module = importlib.import_module(module_name)
    file_path = Path(module.__file__ or "")
    return ast.parse(file_path.read_text(encoding="utf-8"))


def _is_session_value(value: ast.expr) -> bool:
    """Match expressions the static check should treat as a Session.

    Accepts ``session`` (bare Name), ``self._session`` /
    ``self.session`` / any ``Attribute`` whose attribute name contains
    "session". Rejects unrelated objects whose type happens to expose a
    ``.get`` method (``dict``, ``Mapping``, etc.) so the static check
    targets the canonical HTTP-call shape and nothing else.
    """
    if isinstance(value, ast.Name):
        return value.id == "session"
    if isinstance(value, ast.Attribute):
        return "session" in (value.attr or "").lower()
    return False


def _find_session_call_kwargs(
    tree: ast.Module, function_name: str, session_method: str
) -> list[set[str]]:
    """Return the set of kwarg names for every
    ``session.<session_method>(...)`` call inside ``function_name``.

    Used by the static checks that pin ``stream=True`` is present at every
    fixed call site. The inventory matters: a future PR that drops
    ``stream=True`` on any of these sites silently re-introduces the
    pre-fix vulnerability, so the test fails immediately at PR-review
    time instead of waiting for a planted oversized response.
    """
    kwarg_sets: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != function_name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != session_method:
                continue
            if not _is_session_value(func.value):
                continue
            kwarg_sets.append({kw.arg for kw in inner.keywords if kw.arg is not None})
    return kwarg_sets


# ============================================================================
# Precondition: every covered script exposes the canonical cap constant.
# ============================================================================


_NETWORK_INVENTORY = (
    "scripts.fetch_vor_haltestellen",
    "scripts.update_vor_stations",
)


def test_precondition_max_payload_size_helper_exists() -> None:
    """The shared ``read_response_safe`` helper must remain importable
    from ``src.utils.http``. The post-fix call sites depend on it for the
    streaming cap; if the helper moves, every fix site must move with it.
    """
    from src.utils.http import MAX_PAYLOAD_SIZE, read_response_safe

    assert callable(read_response_safe)
    assert isinstance(MAX_PAYLOAD_SIZE, int) and MAX_PAYLOAD_SIZE > 0


@pytest.mark.parametrize("module_name", _NETWORK_INVENTORY)
def test_canonical_size_cap_constants_inventory_round4_network(module_name: str) -> None:
    """Inventory of every covered ``scripts/`` network-response cap
    constant. If a future refactor adds another script that calls
    ``session.get/post`` followed by ``.json()``/``.text``, the
    inventory below MUST be extended — otherwise a future loader is
    silently exposed to the wide-but-flat memory bomb the on-disk
    rounds closed for files.
    """
    module = importlib.import_module(module_name)
    cap = getattr(module, "MAX_VOR_API_RESPONSE_BYTES", None)
    assert isinstance(cap, int), (
        f"{module_name} must expose MAX_VOR_API_RESPONSE_BYTES as an int"
    )
    # Must accommodate legitimate VOR responses (largest observed is the
    # mgate location-match payload at ~50 KiB) with comfortable headroom
    # for fleet growth, and must stay well below the runner's cgroup
    # limit so a cap-fire never converts to OOM.
    assert 1_000_000 <= cap <= 50 * 1024 * 1024, (
        f"{module_name}.MAX_VOR_API_RESPONSE_BYTES out of bounds: {cap}"
    )


# ============================================================================
# AST static check: every fixed call site uses ``stream=True``.
# ============================================================================


_AST_SITES: tuple[tuple[str, str, str], ...] = (
    ("scripts.fetch_vor_haltestellen", "fetch_access_id", "get"),
    ("scripts.fetch_vor_haltestellen", "fetch_candidates", "post"),
    ("scripts.update_vor_stations", "fetch_vor_stops_from_api", "get"),
)


@pytest.mark.parametrize(("module_name", "function", "method"), _AST_SITES)
def test_session_call_uses_stream_true(
    module_name: str, function: str, method: str
) -> None:
    """Every fixed call site MUST set ``stream=True`` so the body is not
    buffered into ``response.content`` eagerly. Without ``stream=True``
    the requests library reads the whole body before returning the
    response object — defeating the purpose of ``read_response_safe``.
    """
    tree = _module_source(module_name)
    kwarg_sets = _find_session_call_kwargs(tree, function, method)
    assert kwarg_sets, (
        f"{module_name}.{function} must contain at least one session.{method}(...) call"
    )
    for kwargs in kwarg_sets:
        assert "stream" in kwargs, (
            f"{module_name}.{function}: session.{method}(...) MUST pass stream=True"
        )


# ============================================================================
# PoC: oversized response is rejected via ``read_response_safe`` before any
# downstream parser sees the body.
# ============================================================================


def _patch_session_factory(
    monkeypatch: pytest.MonkeyPatch, module: Any, session: _RecordingSession
) -> None:
    """Wire the script's session factory to return our recording double."""
    monkeypatch.setattr(
        module, "session_with_retries", lambda *args, **kwargs: session
    )


def test_fetch_access_id_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PoC: an oversized ``Content-Length`` is rejected before the body
    is buffered. Pre-fix, ``resp.text`` would read the whole response
    into memory and OOM the script when the upstream serves a 1 GiB JS
    blob; post-fix, the size cap fires immediately.
    """
    from scripts import fetch_vor_haltestellen as module

    response = _SizeBombResponse(
        content_length=20 * 1024 * 1024,  # 20 MiB > 10 MiB cap
        body=b'aid:"deadbeef"',
        headers={"Content-Type": "application/javascript"},
    )
    session = _RecordingSession(response)

    with pytest.raises(ValueError, match=r"(?i)content[ -]?length|response too large"):
        module.fetch_access_id(session)

    # Cap fires BEFORE the body is buffered (Content-Length pre-check).
    assert response.iter_content_called is False, (
        "size cap must fire BEFORE iter_content is called"
    )


def test_fetch_access_id_rejects_oversized_streamed_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PoC variant: when the upstream omits ``Content-Length`` (chunked
    transfer-encoding), the streaming cap on ``iter_content`` still
    fires once the running tally exceeds ``MAX_VOR_API_RESPONSE_BYTES``.
    """
    from scripts import fetch_vor_haltestellen as module

    cap = module.MAX_VOR_API_RESPONSE_BYTES
    oversized_body = b"a" * (cap + 8192)
    response = _SizeBombResponse(
        content_length=None,  # no Content-Length header
        body=oversized_body,
        headers={"Content-Type": "application/javascript"},
    )
    session = _RecordingSession(response)

    with pytest.raises(ValueError, match=r"(?i)response too large|content[ -]?length"):
        module.fetch_access_id(session)

    # The streaming cap kicks in mid-iteration once the running tally
    # exceeds the budget; iter_content WAS called, but read_response_safe
    # aborted the loop before the full payload was buffered.
    assert response.iter_content_called is True


def test_fetch_access_id_parses_normal_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative-case regression: a normal-sized response still parses
    correctly. The cap constant must NOT be tightened past legitimate
    response shapes, otherwise the cron breaks for non-attack reasons.
    """
    from scripts import fetch_vor_haltestellen as module

    body = b'var config = { aid:"abcd1234abcd1234", base:"x" };'
    response = _SizeBombResponse(
        content_length=len(body),
        body=body,
        headers={"Content-Type": "application/javascript"},
    )
    session = _RecordingSession(response)

    aid = module.fetch_access_id(session)
    assert aid == "abcd1234abcd1234"


def test_fetch_candidates_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PoC: an oversized mgate response is rejected via ``ValueError``
    which the existing ``except (ValueError, RecursionError)`` handler
    catches; the function returns ``[]`` and the per-name loop in
    ``main`` continues with the next station.
    """
    from scripts import fetch_vor_haltestellen as module

    response = _SizeBombResponse(
        content_length=20 * 1024 * 1024,
        body=b"{}",
        headers={"Content-Type": "application/json"},
    )
    session = _RecordingSession(response)

    candidates = module.fetch_candidates(
        session, mgate_url=module.DEFAULT_MGATE_URL,
        access_id="deadbeef", name="Wien Aspern Nord",
    )
    assert candidates == []
    assert response.iter_content_called is False


def test_fetch_candidates_parses_normal_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative-case regression for the mgate POST path."""
    from scripts import fetch_vor_haltestellen as module

    body = json.dumps(
        {
            "svcResL": [
                {"res": {"match": {"locL": [{"name": "Wien Aspern Nord", "extId": "490091000"}]}}}
            ]
        }
    ).encode("utf-8")
    response = _SizeBombResponse(
        content_length=len(body),
        body=body,
        headers={"Content-Type": "application/json"},
    )
    session = _RecordingSession(response)

    candidates = module.fetch_candidates(
        session, mgate_url=module.DEFAULT_MGATE_URL,
        access_id="deadbeef", name="Wien Aspern Nord",
    )
    assert len(candidates) == 1
    assert candidates[0]["name"] == "Wien Aspern Nord"


def test_fetch_vor_stops_from_api_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PoC: an oversized location.name response is rejected without
    aborting the per-station loop. The fallback (if any) is used; the
    next station's resolution still runs.
    """
    from scripts import update_vor_stations as module

    response = _SizeBombResponse(
        content_length=20 * 1024 * 1024,
        body=b"{}",
        headers={"Content-Type": "application/json"},
    )
    session = _RecordingSession(response)

    monkeypatch.setattr(
        module, "session_with_retries", lambda *args, **kwargs: session
    )
    monkeypatch.setattr(
        module.vor_provider,
        "apply_authentication",
        lambda s: None,
    )
    monkeypatch.setattr(module.vor_provider, "VOR_RETRY_OPTIONS", {}, raising=False)

    fallback = {
        "490091000": module.VORStop(
            vor_id="490091000",
            name="Fallback Aspern Nord",
            latitude=None,
            longitude=None,
        )
    }

    stops = module.fetch_vor_stops_from_api(["490091000"], fallback=fallback)

    # The fallback is used because the size cap ValueError was caught by
    # the ``except (ValueError, RecursionError)`` handler. The per-station
    # loop did NOT abort — the contract that protects the rest of the
    # batch is preserved.
    assert [stop.vor_id for stop in stops] == ["490091000"]
    assert stops[0].name == "Fallback Aspern Nord"
    assert response.iter_content_called is False
