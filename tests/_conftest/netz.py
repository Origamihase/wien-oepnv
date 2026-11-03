"""Autouse-Fixtures für Netz und Umgebung: kein echter DNS, kein Netzzugriff,
keine Proxy-Variablen des Rechners, Umgebung, Cache-Ordner, VAO-Version und
Health-Bericht bleiben unberührt.

Früher in ``tests/conftest.py``.
"""


import errno
import ipaddress
import os
import socket
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

root = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Deterministic DNS for mocked-HTTP tests
#
# ``request_safe`` and ``validate_http_url`` resolve every hostname through
# :func:`src.utils.http._resolve_hostname_safe` (real DNS via dnspython) and
# pin the connection to the vetted IP. The ``responses`` library only mocks
# the HTTP layer, so a test that POSTs to ``https://api.github.com/...``
# still needs a working resolver: when DNS times out, the SSRF guard
# rejects the URL and the test fails with "No safe IP resolved" instead of
# exercising the code under test.
# ---------------------------------------------------------------------------

#: Public (``is_ip_safe``-accepted) address returned by :func:`stub_public_dns`
#: and :func:`_no_real_dns`. The ``responses`` mock intercepts the request
#: before any socket is opened, so no traffic ever reaches it; a test without
#: such a mock is stopped by :func:`_no_network`. Documentation ranges
#: (TEST-NET-1/2/3) cannot be used here because the production guard rejects
#: them as non-global.
STUB_PUBLIC_IP = "140.82.121.6"


_PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@pytest.fixture(autouse=True)
def _without_host_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test without the host's proxy variables.

    Behind a proxy the HTTP layer admits trusted hosts only
    (``PROXY_TRUSTED_HOSTS``, audit 2026-09-17, B.3). Tests that need a proxy
    set it themselves, so the suite gives the same result on the CI runners
    (no proxy) and in a sandbox with one.
    """
    for name in _PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _environment_stays() -> Iterator[None]:
    """Give every test back the environment it started with.

    ``load_env_file`` and friends write into ``os.environ`` themselves, and
    ``monkeypatch.delenv(name, raising=False)`` on a variable that is not set
    remembers nothing, so a variable the code sets afterwards stays. Nine
    tests left 14 variables behind, among them ``VOR_ACCESS_ID=token`` and
    ``VOR_BASE_URL=https://example/`` for the rest of the run; one health
    check test passed only because of them and was red on its own, in random
    order, with ``-n 4`` and without network (test-suite audit 2026-10-28).
    """
    before = dict(os.environ)
    yield
    for name in [name for name in os.environ if name not in before]:
        del os.environ[name]
    for name, value in before.items():
        if os.environ.get(name) != value:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _cache_dir_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``utils.cache`` at a per-test ``cache/`` directory.

    ``write_cache`` prunes its provider's directory after writing, by file
    age. Two Baustellen tests redirected only ``_cache_file``, so the prune
    ran on the real ``cache/`` and deleted ``cache/baustellen_*/events.json``
    whenever the checkout's copy was older than 48 hours (an older clone or
    sandbox; CI checks out fresh). Scripts and feed share one module object,
    ``src.utils.cache`` (code audit 2026-10-31; no workflow puts ``src/`` on
    the path since 2026-11-01). A test that needs its own directory still
    patches ``_CACHE_DIR`` itself.
    """
    from src.utils import cache as cache_module

    monkeypatch.setattr(cache_module, "_CACHE_DIR", tmp_path / "cache")


@pytest.fixture(autouse=True)
def _vao_version_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the VAO version choice off the network and out of the real ``cache/``.

    Since 2026-10-29 every Stammstrecke poll reads the VAO version list and
    writes its choice to ``cache/stammstrecke/vao_version.json``
    (``src/utils/vao_version.py``). A test sees no list unless it patches
    ``fetch_version_list`` itself, and the choice lands in ``tmp_path``.
    """
    from src.providers import vor as vor_module
    from src.utils import vao_version as version_module

    monkeypatch.setattr(version_module, "VERSION_STATE_PATH", tmp_path / "vao_version.json")
    monkeypatch.setattr(vor_module, "fetch_version_list", lambda *_a, **_kw: None)


_HEALTH_REPORTS =(root / "docs" / "feed-health.md", root / "docs" / "feed-health.json")


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


@pytest.fixture(autouse=True)
def _health_report_stays_untouched() -> Iterator[None]:
    """Fail a test that writes the feed health report into the real ``docs/``.

    Three tests that ran ``main()`` redirected ``OUT_PATH`` and ``STATE_FILE``
    but not ``FEED_HEALTH_PATH`` / ``FEED_HEALTH_JSON_PATH``, and every suite
    run rewrote ``docs/feed-health.*`` with a ``/tmp/pytest-…`` feed path
    (audit 2026-09-24, update 19:16; A.7 of 2026-09-25). Git ignores the
    files, so nothing showed up in a diff.
    """
    before = [_stamp(path) for path in _HEALTH_REPORTS]
    yield
    assert [_stamp(path) for path in _HEALTH_REPORTS] == before, (
        "the test wrote docs/feed-health.*; point feed_config.FEED_HEALTH_PATH "
        "and FEED_HEALTH_JSON_PATH at tmp_path"
    )


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer every DNS query of a test with :data:`STUB_PUBLIC_IP`.

    The HTTP layer resolves every hostname itself (``_resolve_hostname_safe``,
    dnspython) before it accepts a URL, and ``src.feed.config`` does so for
    the public feed URL at import. Unstubbed, 23 tests asked the real DNS for
    third-party names (``example.com``, ``safe.com``, ``data.wien.gv.at``):
    they failed without a network, and their outcome depended on zones the
    project does not control (test-suite audit 2026-10-04). Every name now
    resolves to one public IPv4 address, the answer the CI runners get for
    the hosts in question. The address belongs to a real server: a request
    that no mock intercepts would connect to it, which :func:`_no_network`
    refuses. A test that needs another answer patches
    ``dns.resolver.Resolver.resolve`` or ``_resolve_hostname_safe`` itself,
    which takes precedence.
    """
    import dns.resolver
    from types import SimpleNamespace

    def _resolve(self: Any, qname: Any, rdtype: Any = "A", *args: Any, **kwargs: Any) -> Any:
        if str(rdtype).upper().endswith("AAAA"):
            raise dns.resolver.NoAnswer
        return [SimpleNamespace(address=STUB_PUBLIC_IP)]

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", _resolve)


@pytest.fixture
def stub_public_dns(monkeypatch: pytest.MonkeyPatch) -> str:
    """Resolve every hostname to :data:`STUB_PUBLIC_IP` without real DNS.

    Replaces only the resolver (``_resolve_hostname_safe``); the production
    SSRF checks (``is_ip_safe``, IP pinning, redirect re-validation) still
    run against the returned address.

    Returns:
        The stubbed IP, so tests can assert on the pinned address.
    """
    import socket
    from typing import Any

    import src.utils.http as http_utils
    from tests._conftest.http_bindings import setattr_all

    # Guard: the stub must survive the real safety check, otherwise every
    # test using it would fail for the wrong reason. (Bound to a name first
    # so the ``TypeGuard`` does not narrow the ``str`` constant to an IP type.)
    stub_is_safe = http_utils.is_ip_safe(STUB_PUBLIC_IP)
    assert stub_is_safe, f"{STUB_PUBLIC_IP} must pass is_ip_safe()"

    def _resolve(hostname: str) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (STUB_PUBLIC_IP, 0))]

    # Every binding: since 2026-11-02 the resolver is also read from
    # src/utils/http_safety/*.py (validate_http_url, the SafeDNS connections).
    setattr_all(monkeypatch, "_resolve_hostname_safe", _resolve)
    return STUB_PUBLIC_IP


# ---------------------------------------------------------------------------
# No network
#
# ``_no_real_dns`` answers every name with :data:`STUB_PUBLIC_IP`, the address
# of a real GitHub server. A request that no mock intercepted therefore went
# to that server, failed, and the HTTP layer retried it four times with
# backoff: two tests (14 cases) made 15 such requests, 75 connection attempts
# per run, about two and a half minutes of every CI run (technical debt
# assessment and review of 2026-11-01). ``_no_network`` refuses such a
# connection at once and fails the test that opened it.
# ---------------------------------------------------------------------------

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


def _is_local_address(family: int, address: Any) -> bool:
    """Loopback, ``localhost`` and Unix sockets stay allowed."""
    if family == getattr(socket, "AF_UNIX", None):
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    if not isinstance(host, str):
        return False
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


def _connection_origin() -> str:
    """Innermost frame of each project file on the way to the connection.

    One frame per file, so the HTTP layer does not hide the caller:
    ``src/utils/http.py … <- scripts/<updater>.py … <- tests/test_….py …``.
    """
    origin: dict[Path, str] = {}
    for frame in reversed(traceback.extract_stack()):
        try:
            relative = Path(frame.filename).resolve().relative_to(root)
        except ValueError:
            continue
        if relative.parts[0] not in {"src", "scripts", "tests"} or relative.name == "conftest.py":
            continue
        origin.setdefault(relative, f"{relative}:{frame.lineno} ({frame.name})")
        if len(origin) == 4:
            break
    return " <- ".join(origin.values()) or "outside the project code"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail every test that opens a network connection.

    Each ``connect`` to anything but loopback or a Unix socket fails at once
    with ``ENETUNREACH``, as on a machine without a network, so the code
    under test takes its error path without waiting for a timeout. Because
    that path may well swallow the error, the test fails afterwards in any
    case, naming the address and the code that asked for it. The retry
    backoff of the HTTP layer still runs first (about 8 s per request), so
    mock the HTTP layer instead (``responses``, ``patch.object``).
    Subprocesses are not covered. The fixture yields the refused attempts, so the guard's own
    tests (``tests/test_no_network_guard.py``) can inspect and clear them.
    """
    attempts: list[str] = []

    def _refuse(address: Any) -> None:
        attempts.append(f"{address!r} from {_connection_origin()}")

    def _connect(self: socket.socket, address: Any) -> None:
        if not _is_local_address(self.family, address):
            _refuse(address)
            raise OSError(errno.ENETUNREACH, "network access is off in tests (tests/conftest.py)")
        _REAL_CONNECT(self, address)

    def _connect_ex(self: socket.socket, address: Any) -> int:
        if not _is_local_address(self.family, address):
            _refuse(address)
            return errno.ENETUNREACH
        return _REAL_CONNECT_EX(self, address)

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    yield attempts
    if attempts:
        pytest.fail(
            "the test opened a network connection; mock the HTTP layer instead:\n  "
            + "\n  ".join(attempts),
            pytrace=False,
        )
