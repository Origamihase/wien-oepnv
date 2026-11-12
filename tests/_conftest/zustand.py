"""Autouse-Fixtures, die den Zustand zwischen Tests zurücksetzen: ersetzte
Module, Feed-Konfiguration, Root-Logger, Zähler, Caches, Statistik-Dateien,
Circuit Breaker.

Früher in ``tests/conftest.py``.
"""


import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests._conftest.build_feed_bindings import part

root = Path(__file__).resolve().parents[2]


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
    an exception class from its import no longer caught the error the
    reloaded module raised. The outcome depended on the order and on today's date
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
    import src.providers.vor_quota as vor

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


@pytest.fixture(autouse=True)
def _baustellen_fallback_layers_reset() -> None:
    """Start every test without the Baustellen fallback layers of an earlier one.

    ``scripts/update_baustellen_cache.py`` lists in the module-level
    ``_fallback_layers`` the layers that came from the last good answer, and
    ``main()`` exits 3 while the list is not empty. Only ``_fetch_layers``
    clears it, so a partial-outage test left it filled, and a later test
    that fakes ``_fetch_layers`` got 3 instead of 0 unless a clearing test
    ran in between (review of 2026-11-01: three such tests; the collection
    order hid it, chunk borders under ``-n auto`` can move as the suite
    grows). A test that never loads the script does not import it here.
    """
    module = sys.modules.get("scripts.update_baustellen_cache")
    if module is not None:
        module._fallback_layers.clear()


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
        part(build_feed, "item_format"), "format_local_times", partial(part(build_feed, "time_line").format_local_times, now=today)
    )


@pytest.fixture(autouse=True)
def isolate_ki_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep ``data/ki_entscheidungen.json``, the Gemini key and the step output out of test runs.

    A build reads Gemini's stored answers for unclear disruptions
    (``src.ki_entscheidung``) and, with a key, asks and writes them. Tests
    drive builds; without this guard a build under test would read the
    committed answers, a key in the caller's shell would send a request,
    and in CI every such build would append ``ki_outcome=…`` to the test
    job's ``$GITHUB_OUTPUT``. The variable (not a module attribute) is set
    for the same reason as in ``isolate_translation_status``; outside the
    allowed roots the file is neither read nor written, and tests of the
    store ``chdir`` into *tmp_path*.
    """
    monkeypatch.setenv("KI_ENTSCHEIDUNGEN_PATH", str(tmp_path / "data" / "ki_entscheidungen.json"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    yield


@pytest.fixture(autouse=True)
def isolate_translation_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep ``data/translation_status.json`` out of test runs.

    Every build that touches the translator records whether it loaded
    (``feed_build.translator._record_translation_status``, read by the Health check).
    Tests drive builds with a stub translator; without this guard they would
    rewrite the committed record. The variable (not a module attribute) is
    set because some tests ``importlib.reload`` the build module. Outside
    the allowed roots the write is refused and logged; tests of the record
    itself ``chdir`` into *tmp_path* so ``validate_path`` accepts the path.
    """
    monkeypatch.setenv("TRANSLATION_STATUS_PATH", str(tmp_path / "data" / "translation_status.json"))
    yield


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
    ``test_utils_stats/``) are unaffected because the explicit
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
