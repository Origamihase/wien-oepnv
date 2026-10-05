import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest
from hypothesis import settings as hypothesis_settings

# Hypothesis' per-example deadline (200 ms) measured the first example of a
# property test together with the lazy build of the station and brand
# patterns (about 0.6 s): two tests without ``deadline=None`` failed whenever
# they happened to run first (test-suite audit 2026-10-04). Hangs are
# caught by pytest-timeout.
hypothesis_settings.register_profile("wien-oepnv", deadline=None)
hypothesis_settings.load_profile("wien-oepnv")

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

_STRECKENDATEN_DIR = root / "data" / "streckendaten"
_STRECKENDATEN_ARCHIVE = _STRECKENDATEN_DIR / "streckendaten_notfallmanagement.zip"
_STRECKENDATEN_GEOJSON = _STRECKENDATEN_DIR / "streckendaten_notfallmanagement.geojson"



def _path_digest(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


_SAMPLE_STRECKENDATEN = {
    "type": "FeatureCollection",
    "name": "streckendaten_notfallmanagement",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 1,
                "LINIE": "S7",
                "linienname": "S7 Wien Mitte ↔ Flughafen",
                "LINIENNUMMER": "S7",
                "streckennummer": "20101",
                "streckenname": "Wien Mitte – Flughafen Wien",
                "BETRIEBSSTELLE_VON": "WIEN MITTE",
                "BETRIEBSSTELLE_BIS": "FLUGHAFEN WIEN",
                "Richtung": "Richtung Flughafen",
                "Category": "Personenverkehr",
                "KM_VON": 0.0,
                "KM_BIS": 18.3,
                "ANZAHL_GLEISE": 2,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [16.382, 48.206],
                    [16.413, 48.191],
                    [16.473, 48.157],
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 2,
                "LINIE": "S80",
                "linienname": "S80 Aspern Nord ↔ Hütteldorf",
                "LINIENNUMMER": "S80",
                "streckennummer": "20303",
                "streckenname": "Aspern Nord – Hütteldorf",
                "BETRIEBSSTELLE_VON": "ASPERN NORD",
                "BETRIEBSSTELLE_BIS": "WIEN HÜTTELDORF",
                "Richtung": "Richtung Aspern",
                "Category": "Personenverkehr",
                "KM_VON": 0.0,
                "KM_BIS": 26.4,
                "ANZAHL_GLEISE": 2,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [16.52, 48.234],
                    [16.444, 48.208],
                    [16.31, 48.196],
                ],
            },
        },
    ],
}


def _download_streckendaten(url: str, destination: Path) -> bool:
    tmp_path = destination.with_suffix(".tmp")
    try:
        with urllib_request.urlopen(url, timeout=30) as response:
            status = getattr(response, "status", None)
            if status is not None and status >= 400:
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tmp_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except (OSError, urllib_error.URLError, urllib_error.HTTPError, ValueError):
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        return False
    else:
        tmp_path.replace(destination)
        return True


def _write_sample_streckendaten(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            _STRECKENDATEN_GEOJSON.name,
            json.dumps(_SAMPLE_STRECKENDATEN, ensure_ascii=False),
        )


def _extract_streckendaten(archive: Path, target_dir: Path) -> list[Path]:
    created_paths: list[Path] = []
    with zipfile.ZipFile(archive, "r") as zipped:
        for info in zipped.infolist():
            destination = (target_dir / info.filename).resolve()
            if not destination.is_relative_to(target_dir.resolve()):
                continue
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                continue
            zipped.extract(info, path=target_dir)
            created_paths.append(destination)
    return created_paths


def _ensure_streckendaten_dataset() -> tuple[Path, bool, bool, bool, list[Path]]:
    dataset_dir = _STRECKENDATEN_DIR
    dir_existed = dataset_dir.exists()
    dataset_dir.mkdir(parents=True, exist_ok=True)

    archive_path = _STRECKENDATEN_ARCHIVE
    archive_existed = archive_path.exists()
    geojson_existed = _STRECKENDATEN_GEOJSON.exists()

    if not archive_existed:
        download_url = os.getenv("STRECKENDATEN_DOWNLOAD_URL")
        created = False
        if download_url and download_url.startswith("https://"):
            created = _download_streckendaten(download_url, archive_path)
        if not created:
            _write_sample_streckendaten(archive_path)

    extracted_paths: list[Path] = []
    if not geojson_existed:
        extracted_paths = _extract_streckendaten(archive_path, dataset_dir)

    return (
        archive_path,
        archive_existed,
        geojson_existed,
        dir_existed,
        extracted_paths,
    )


def _cleanup_created_paths(paths: list[Path]) -> None:
    directories: set[Path] = set()
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        parent = path.parent
        while True:
            try:
                parent.relative_to(_STRECKENDATEN_DIR)
            except ValueError:
                break
            if parent == _STRECKENDATEN_DIR:
                break
            directories.add(parent)
            parent = parent.parent
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()


# The dataset sits at a fixed path the code reads, so pytest-xdist workers
# share it. Each worker runs this session fixture: unguarded, one worker read
# the sample archive while another was still writing it (``BadZipFile``) or
# deleted it at its teardown while the others were still testing; every test
# on those workers then failed in setup (4,190 errors in one
# ``-n 4 -p randomly`` run, 2026-10-04). A file lock serialises setup and
# teardown, and a count of the workers using the dataset leaves the cleanup
# to the last one. Serial runs (CI) take the same path with a count of one.
_STRECKENDATEN_LOCK = Path(tempfile.gettempdir()) / f"wien-oepnv-streckendaten-{_path_digest(_STRECKENDATEN_DIR)}"


@contextlib.contextmanager
def _streckendaten_lock() -> Iterator[dict[str, Any]]:
    """Hold the cross-process lock; yield the shared bookkeeping, saved on exit."""
    lock_path = _STRECKENDATEN_LOCK.with_suffix(".lock")
    state_path = _STRECKENDATEN_LOCK.with_suffix(".json")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            try:
                shared: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                shared = {"users": 0}
            yield shared
            if shared["users"] > 0:
                state_path.write_text(json.dumps(shared), encoding="utf-8")
            else:
                with contextlib.suppress(OSError):
                    state_path.unlink()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@pytest.fixture(scope="session")
def streckendaten_dataset() -> Iterator[Path]:
    with _streckendaten_lock() as shared:
        if shared["users"] == 0:
            (
                _archive_path,
                archive_existed,
                geojson_existed,
                dir_existed,
                extracted_paths,
            ) = _ensure_streckendaten_dataset()
            shared["created"] = {
                "archive_existed": archive_existed,
                "geojson_existed": geojson_existed,
                "dir_existed": dir_existed,
                "extracted": [str(path) for path in extracted_paths],
            }
        shared["users"] += 1

    try:
        yield _STRECKENDATEN_ARCHIVE
    finally:
        with _streckendaten_lock() as shared:
            shared["users"] -= 1
            created = shared.get("created", {})
            keep_flag = os.getenv("KEEP_STRECKENDATEN_DATASET", "").strip().lower()
            if shared["users"] <= 0 and keep_flag not in {"1", "true", "yes"}:
                _remove_created_streckendaten(created)


def _remove_created_streckendaten(created: dict[str, Any]) -> None:
    archive_path = _STRECKENDATEN_ARCHIVE
    if not created.get("archive_existed", True) and archive_path.exists():
        archive_path.unlink()
    if not created.get("geojson_existed", True):
        _cleanup_created_paths([Path(path) for path in created.get("extracted", [])])
    if not created.get("dir_existed", True) and _STRECKENDATEN_DIR.exists():
        shutil.rmtree(_STRECKENDATEN_DIR, ignore_errors=True)
    elif _STRECKENDATEN_DIR.exists():
        try:
            next(_STRECKENDATEN_DIR.iterdir())
        except StopIteration:
            with contextlib.suppress(OSError):
                _STRECKENDATEN_DIR.rmdir()


@pytest.fixture(scope="session", autouse=True)
def _ensure_streckendaten(streckendaten_dataset: Path) -> Iterator[None]:  # noqa: PT005
    yield


@pytest.fixture(autouse=True)
def _restore_replaced_modules() -> Iterator[None]:
    """Undo what a test did to modules: re-imports and reloads.

    Thirty-one test files import ``src.build_feed`` afresh (some with
    ``src.feed.config``) by popping it from ``sys.modules``, and twelve call
    ``importlib.reload``. Both stayed for the rest of the session. After a
    re-import every test module collected before still held the original:
    the fixtures in this file (``reset_build_feed_state``,
    ``time_line_today``) reset and patched the copy, not the module those
    tests call. A reload replaces the module's classes, so a test holding
    ``GooglePlacesError`` from its import no longer caught the error the
    client raised. The outcome depended on the order and on today's date
    (test-suite audit 2026-10-04: 21 tests failed in shuffled runs, 14 more
    with the clock set to New Year). Modules a test imports for the first
    time stay.
    """
    import importlib

    before = dict(sys.modules)
    namespaces: dict[int, tuple[Any, dict[str, Any]]] = {}
    real_reload = importlib.reload

    def _reload(module: Any) -> Any:
        namespaces.setdefault(id(module), (module, dict(vars(module))))
        return real_reload(module)

    importlib.reload = _reload
    try:
        yield
    finally:
        importlib.reload = real_reload
        for module, namespace in namespaces.values():
            vars(module).clear()
            vars(module).update(namespace)
        for name, module in before.items():
            if sys.modules.get(name) is module:
                continue
            sys.modules[name] = module
            parent_name, _, child = name.rpartition(".")
            parent = sys.modules.get(parent_name) if parent_name else None
            if parent is not None:
                with contextlib.suppress(AttributeError, TypeError):
                    setattr(parent, child, module)


@pytest.fixture(autouse=True)
def _feed_config_stays() -> Iterator[None]:
    """Restore every value of ``src.feed.config`` a test changed.

    ``refresh_from_env()`` re-reads the whole configuration from the
    environment. A test that sets ``MAX_ITEMS=-5`` and refreshes left
    ``MAX_ITEMS`` at 0 after monkeypatch had restored the variable, and the
    next test that rendered a feed got no items (test-suite audit
    2026-10-04, shuffled run). Values set with ``monkeypatch.setattr`` were
    restored already; this covers the refresh.
    """
    from src.feed import config as feed_config

    before = dict(vars(feed_config))
    yield
    namespace = vars(feed_config)
    for name, value in before.items():
        if namespace.get(name) is not value:
            namespace[name] = value


@pytest.fixture(autouse=True)
def _root_logger_stays_clean() -> Iterator[None]:
    """Undo what a test did to the root logger: handlers, formatters, level.

    Script entry points install their own sanitising handler on the root
    logger (``_configure_safe_logging``, ``setup_script_logging``), once per
    process; when a test ran one, a later test of the installation found
    nothing to install and failed. ``configure_logging`` puts a
    ``SafeFormatter`` on every handler already there, pytest's capture
    handler included, which lives for the whole session: ``SafeFormatter``
    formats a copy of the record, so from then on no record in
    ``caplog.records`` had a ``message`` (test-suite audit 2026-10-04, both
    in shuffled runs only). pytest's own handlers stay attached.
    """
    import logging

    root = logging.getLogger()
    before = [(handler, handler.formatter, handler.level) for handler in root.handlers]
    level = root.level
    yield
    kept = {handler for handler, _, _ in before}
    for handler in list(root.handlers):
        if handler not in kept and not type(handler).__module__.startswith("_pytest"):
            root.removeHandler(handler)
    for handler, formatter, handler_level in before:
        handler.setFormatter(formatter)
        handler.setLevel(handler_level)
    root.setLevel(level)


@pytest.fixture(autouse=True)
def reset_vor_request_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import src.providers.vor as vor

    path = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", path)

    # Also reset the memory cache
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)

    yield
    if path.exists():
        path.unlink()


@pytest.fixture(autouse=True)
def reset_build_feed_state() -> None:
    import src.build_feed as build_feed
    from src.feed.providers import reset_registry

    build_feed.reset_module_state()
    reset_registry(with_defaults=True)


@pytest.fixture(autouse=True)
def forget_resolved_tickers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start and end every test without remembered WL tickers, away from the real file.

    ``src.providers.wl_resolved`` keeps the display tickers of closed
    incidents across fetches; within one test process a test's tickers
    would otherwise reach the next test's fetch. ``scripts/update_wl_cache.py``
    keeps them in ``data/wl_resolved_tickers.json``, which the update cycle
    commits: a test running its ``main()`` emptied that file (check of
    2026-10-05), so the file moves to *tmp_path*.
    """
    from scripts import update_wl_cache
    from src.providers import wl_resolved

    monkeypatch.setattr(update_wl_cache, "RESOLVED_TICKERS", tmp_path / "wl_resolved_tickers.json")
    wl_resolved.forget()
    yield
    wl_resolved.forget()


@pytest.fixture
def time_line_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render time lines as on 2026-10-02, 12:00 Vienna, whatever today is.

    The time line names no year inside the current one and says "Heute" for
    today (``format_local_times``). Tests that compare a whole rendered item
    with fixed dates would otherwise change their expected text at midnight
    or on New Year.
    """
    from datetime import datetime
    from functools import partial
    from zoneinfo import ZoneInfo

    import src.build_feed as build_feed

    today = datetime(2026, 10, 2, 12, 0, tzinfo=ZoneInfo("Europe/Vienna"))
    monkeypatch.setattr(
        build_feed, "format_local_times", partial(build_feed.format_local_times, now=today)
    )


@pytest.fixture(autouse=True)
def isolate_stats_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect ``src.utils.stats`` CSV appends to a per-test tmp directory.

    The production writers default to ``data/stats/`` under the repo
    root. Without this guard, any test that exercises a hot-path which
    transitively records stats (build_feed.update_item_state's
    strict-new branch, scripts/update_stammstrecke_status._process_direction)
    would write into the real on-disk ledger and contaminate the
    committed history with synthetic test rows.

    The override is monkeypatched on the module attribute, so call
    sites that did not pass an explicit ``stats_dir`` keyword (which
    is the common case in production) pick up the test path. Tests
    that *do* explicitly target a different ``stats_dir`` (e.g.
    ``test_utils_stats.py``) are unaffected because the explicit
    keyword wins inside ``stats_path``.
    """
    from src.utils import stats as stats_utils

    monkeypatch.setattr(stats_utils, "DEFAULT_STATS_DIR", tmp_path / "stats")
    yield


@pytest.fixture(autouse=True)
def isolate_episode_starts_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Redirect the Stammstrecke episode-start ledger to a per-test tmp file.

    ``src.feed.stammstrecke`` persists the first-observed episode start to
    ``cache/stammstrecke/episode_starts.json`` under the repo root
    (:data:`~src.feed.stammstrecke.EPISODE_STARTS_PATH`). That file is
    intentionally committed and refreshed by the Stammstrecke workflow, so a
    stray test write is easy to wave through in review as "the cache updated".
    Any test that drives the compute path without passing an explicit
    ``episode_starts_path`` falls back to ``EPISODE_STARTS_PATH`` and mutates
    the tracked seed, dirtying the working tree and risking an accidental
    commit of synthetic test rows.

    The canonical statemachine tests already inject a tmp path; integration
    tests that exercise the ledger only as a side effect do not — so isolate
    it globally, mirroring :func:`isolate_stats_writes`. The override is
    monkeypatched on the module attribute, which the producer reads at call
    time (``episode_starts_path or EPISODE_STARTS_PATH``), so an explicit
    keyword still wins for tests that target their own path.
    """
    from src.feed import stammstrecke

    monkeypatch.setattr(
        stammstrecke, "EPISODE_STARTS_PATH", tmp_path / "episode_starts.json"
    )
    yield


# ---------------------------------------------------------------------------
# CircuitBreaker test-isolation fixture
#
# Module-level :class:`src.utils.circuit_breaker.CircuitBreaker` instances
# remember failure streaks across calls *and* across tests in the same
# process. A test that intentionally trips a breaker (the OSM Overpass
# breaker is the canonical example — see
# ``tests/places/test_osm_client.py::test_fetch_stations_breaker_opens_after_repeated_failures``)
# leaves the global in OPEN state for the rest of the suite, which then
# fails any later test that exercises the same call site with a
# ``CircuitBreakerOpen`` masquerading as the upstream's real failure.
#
# The autouse fixture below resets every CircuitBreaker registered in
# :attr:`CircuitBreaker._instances` back to CLOSED + zero failures
# before each test runs. Adding a new breaker is now zero boilerplate:
# the :class:`CircuitBreaker` ``__init__`` auto-registers every
# instance into the process-wide :class:`weakref.WeakSet` so newly
# instantiated breakers (HAFAS, Stammstrecke, future providers) are
# picked up automatically without touching this fixture.
# ---------------------------------------------------------------------------


def _eagerly_import_breaker_modules() -> None:
    """Import every module known to own a :class:`CircuitBreaker` singleton.

    The registry is populated lazily by ``CircuitBreaker.__init__``, so a
    breaker only appears in :meth:`iter_instances` after its owning
    module is imported. Tests that exercise (e.g.) HAFAS but happen to
    run before the first test that imports the OSM client would
    otherwise see a partial registry. Eager-importing the known
    breaker-owning modules here ties registration to session start, not
    to first use, so the autouse reset fixture covers every project
    breaker on the very first test.

    Imports are wrapped individually so a partial test environment
    (e.g. a places-free worktree) does not abort collection.
    """
    import importlib

    for module_name in (
        "src.places.osm_client",
        "src.places.hafas_client",
        "src.places.client",
    ):
        try:
            importlib.import_module(module_name)
        except ImportError:  # pragma: no cover - tolerated by design
            pass


_eagerly_import_breaker_modules()


@pytest.fixture(autouse=True)
def reset_circuit_breakers() -> Iterator[None]:
    """Force every known CircuitBreaker back to CLOSED before each test.

    Iterates :meth:`CircuitBreaker.iter_instances` (the process-wide
    weak registry populated by ``CircuitBreaker.__init__``) so newly
    added breakers are picked up automatically — no per-module
    bookkeeping required. Without this guard, a test that opens the
    breaker (intentionally or by side-effect of a chaos run) would
    silently fail every later test that touches the same call site,
    masking real upstream regressions and creating order-dependent
    test failures.
    """
    from src.utils.circuit_breaker import CircuitBreaker

    for breaker in CircuitBreaker.iter_instances():
        breaker.reset()
    yield
    for breaker in CircuitBreaker.iter_instances():
        breaker.reset()


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

#: Public (``is_ip_safe``-accepted) address returned by :func:`stub_public_dns`.
#: The ``responses`` mock intercepts the request before any socket is opened,
#: so no traffic ever reaches it. Documentation ranges (TEST-NET-1/2/3) cannot
#: be used here because the production guard rejects them as non-global.
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


_HEALTH_REPORTS = (root / "docs" / "feed-health.md", root / "docs" / "feed-health.json")


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
    the hosts in question; nothing leaves the machine. A test that needs
    another answer patches ``dns.resolver.Resolver.resolve`` or
    ``_resolve_hostname_safe`` itself, which takes precedence.
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
    run against the returned address. Patched on both import aliases
    (``src.utils.http`` and ``utils.http``) because some provider modules
    import the package without the ``src.`` prefix.

    Returns:
        The stubbed IP, so tests can assert on the pinned address.
    """
    import socket
    from typing import Any

    import src.utils.http as http_utils

    # Guard: the stub must survive the real safety check, otherwise every
    # test using it would fail for the wrong reason. (Bound to a name first
    # so the ``TypeGuard`` does not narrow the ``str`` constant to an IP type.)
    stub_is_safe = http_utils.is_ip_safe(STUB_PUBLIC_IP)
    assert stub_is_safe, f"{STUB_PUBLIC_IP} must pass is_ip_safe()"

    def _resolve(hostname: str) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (STUB_PUBLIC_IP, 0))]

    for module_name in ("src.utils.http", "utils.http"):
        module = sys.modules.get(module_name)
        if module is not None:
            monkeypatch.setattr(module, "_resolve_hostname_safe", _resolve)
    return STUB_PUBLIC_IP


# ---------------------------------------------------------------------------
# Coordinate-proximity test helper
#
# Replacement for the brittle ``pytest.approx(<lat>) ; pytest.approx(<lon>)``
# pattern in tests that pin station coordinates. ``pytest.approx`` uses a
# default relative tolerance of ~1e-6 which at lat=48° is roughly 5 cm
# east-west — far tighter than any upstream API's coordinate stability,
# so every refresh of ``data/stations.json`` would otherwise break tests
# even when the new coords are operationally equivalent (same building,
# different platform reference).
#
# ``assert_coords_close`` instead measures the great-circle distance
# between the obtained and expected coords and asserts it is within
# ``max_meters``. Same semantics as ``apply_coordinate_inertia`` so
# tests and production agree on what "the same point" means.
# ---------------------------------------------------------------------------

def assert_coords_close(
    lat1: float | None,
    lon1: float | None,
    lat2: float,
    lon2: float,
    max_meters: float = 150.0,
) -> None:
    """Assert two coordinate pairs are within ``max_meters`` of each other.

    The first pair (``lat1``/``lon1``) is the value under test and is
    typed ``float | None`` to match the project's ``StationInfo``
    shape — many station-lookup return types expose coordinates as
    optional. ``None`` is rejected with an explicit assertion failure
    so the caller doesn't have to thread ``assert info.latitude is
    not None`` boilerplate through every test.

    Args:
        lat1: First-pair latitude (typically the value under test).
            ``None`` is treated as a test failure.
        lon1: First-pair longitude. ``None`` → test failure.
        lat2: Second-pair latitude (the expected value).
        lon2: Second-pair longitude.
        max_meters: Maximum allowed great-circle distance, defaults to
            150 m to match :data:`STATION_DRIFT_TOLERANCE_METERS`.

    Raises:
        AssertionError: If either of ``lat1`` / ``lon1`` is ``None``,
            or the two points are further apart than ``max_meters``.
            The error message names both pairs and the measured
            distance for easy diagnosis.
    """
    from src.utils.geo import calculate_distance_meters

    assert lat1 is not None and lon1 is not None, (
        f"obtained coordinates were None (lat={lat1!r}, lon={lon1!r}); "
        f"expected ({lat2}, {lon2})"
    )
    distance = calculate_distance_meters(lat1, lon1, lat2, lon2)
    assert distance <= max_meters, (
        f"coordinates ({lat1}, {lon1}) and ({lat2}, {lon2}) are "
        f"{distance:.1f} m apart (max allowed: {max_meters} m)"
    )
