import os
import sys
from pathlib import Path

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

# The English feed build asks ``transformers`` for the translation model
# (``src.feed_build.translator._load_translator``), and transformers fetches the model's ``config.json`` from
# huggingface.co unless the pinned revision is in the local cache. A fresh CI
# runner has no such cache, so every test that built the EN feed without a
# stub went to the network; ``_no_network`` (tests/_conftest/netz.py) refuses that, after about
# 46 s of retries in ``huggingface_hub``. Offline, the lookup fails at once
# and the build takes the fallback it took in CI before (the test jobs
# install no torch, so the pipeline never loaded there). huggingface_hub
# reads the variable when it is first imported, which no import above does
# (review of 2026-11-01).
os.environ["HF_HUB_OFFLINE"] = "1"

# The fixtures and helpers live in tests/_conftest/ (700-line limit since
# 2026-11-02). Importing them here keeps them fixtures of the whole test
# folder, as before; pytest sorts autouse fixtures by name, so their order
# does not change. ``from tests.conftest import ...`` keeps working too.
from tests._conftest.koordinaten import (  # noqa: E402, F401
    assert_coords_close,
)
from tests._conftest.netz import (  # noqa: E402, F401
    STUB_PUBLIC_IP,
    _cache_dir_stays,
    _environment_stays,
    _health_report_stays_untouched,
    _is_local_address,
    _no_network,
    _no_real_dns,
    _vao_version_stays,
    _without_host_proxy,
    stub_public_dns,
)
from tests._conftest.streckendaten import (  # noqa: E402, F401
    _ensure_streckendaten,
    streckendaten_dataset,
)
from tests._conftest.zustand import (  # noqa: E402, F401
    _baustellen_fallback_layers_reset,
    _feed_config_stays,
    _restore_replaced_modules,
    _root_logger_stays_clean,
    forget_resolved_tickers,
    isolate_episode_starts_writes,
    isolate_ki_decisions,
    isolate_stats_writes,
    isolate_translation_status,
    reset_build_feed_state,
    reset_circuit_breakers,
    reset_vor_request_count,
    time_line_today,
)

__all__ = [
    "STUB_PUBLIC_IP",
    "_baustellen_fallback_layers_reset",
    "_cache_dir_stays",
    "_ensure_streckendaten",
    "_environment_stays",
    "_feed_config_stays",
    "_health_report_stays_untouched",
    "_is_local_address",
    "_no_network",
    "_no_real_dns",
    "_restore_replaced_modules",
    "_root_logger_stays_clean",
    "_vao_version_stays",
    "_without_host_proxy",
    "assert_coords_close",
    "forget_resolved_tickers",
    "isolate_episode_starts_writes",
    "isolate_ki_decisions",
    "isolate_stats_writes",
    "isolate_translation_status",
    "reset_build_feed_state",
    "reset_circuit_breakers",
    "reset_vor_request_count",
    "streckendaten_dataset",
    "stub_public_dns",
    "time_line_today",
]
