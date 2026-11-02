"""Tests for ``scripts/update_stammstrecke_hbf.py``.

The Hauptbahnhof-based ``/departureBoard`` monitor was introduced on
2026-05-15 to lift the ``numF=6`` ceiling that the ``/trip``-based
predecessor (``scripts/update_stammstrecke_status.py``) inherited from
the VAO API (see ``docs/reference/trip.md:34``). These tests pin the
direction-classification logic and the response parsing against
synthetic VAO payloads.

The HTTP layer is never exercised; ``_query_departure_board`` is
mocked per test. The quota counter file is redirected to ``tmp_path``
via the same convention as the legacy-script tests.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
