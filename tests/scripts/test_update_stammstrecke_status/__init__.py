"""Tests for ``scripts/update_stammstrecke_status.py`` (VOR/VAO migration).

The 2026-05-09 architecture pivot replaced the pyhafas client with the
VOR/VAO ReST ``/trip`` endpoint. These tests exercise the same decision
tree as before — import-time failure, transport error, circuit-breaker
open, mean-below-threshold, mean-above-threshold, no S-Bahn legs
found, ``first_seen`` persistence and recovery — but the upstream is
mocked at the ``_query_trips`` boundary instead of the pyhafas client.

The HTTP layer is never actually exercised in tests: ``_query_trips`` is
patched per-test to return synthetic VAO ``Trip`` payloads (a single
ride leg with controllable name/category/delay/cancelled fields). The
quota-counter file (``REQUEST_COUNT_FILE``) is redirected to ``tmp_path``
via the existing module-level convention so a test run never touches the
real ``data/vor_request_count.json``.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
