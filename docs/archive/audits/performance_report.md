# Performance Report

## Test Suite Duration Overview
- Gesamtzeit für `pytest`: 9.93 Sekunden für 367 Tests.
- Langsamste Tests (Top 5):
  1. `tests/test_vor_request_limit.py::test_fetch_stationboard_counts_unsuccessful_requests[503-headers1]` – 5.00 s
  2. `tests/test_collect_items_timeout.py::test_provider_specific_timeout_override` – 1.00 s
  3. `tests/test_collect_items_timeout.py::test_slow_provider_does_not_block` – 1.00 s
  4. `tests/test_collect_items_timeout.py::test_provider_worker_limit` – 0.40 s
  5. `tests/test_vor_request_limit.py::test_save_request_count_is_safe_across_processes` – 0.40 s

## Beobachtungen
- Die VOR-Request-Limit-Tests dominieren die Laufzeit. Sie verwenden Mehrprozess-Sperren und simulieren Netzwerkeffekte, wodurch sie deutlich länger als andere Tests benötigen.
- Die Timeout-bezogenen Tests kosten ebenfalls 2.40 Sekunden und prüfen komplexe Thread-/Prozess-Logik.
- Alle übrigen Tests laufen unter 0.05 Sekunden und deuten auf eine insgesamt schnelle Test-Suite hin.

## Empfehlungen
- Prüfe, ob die VOR-Limitierungstests durch geringere Iterationszahlen oder parametrisiertes Caching beschleunigt werden können.
- Analysiere, ob wiederholte Sleep-Aufrufe in den Timeout-Tests reduziert werden können, ohne die Testaussagekraft zu verlieren.
